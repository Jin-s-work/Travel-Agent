"""Lifespan dispatcher + real temporary Chroma + fake, fully tracked providers."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from tests.test_foundation_api import service, _trip, _upload, _job, _fact


def post_upload(client, trip_id, content=b'Synthetic reservation', key='reliable-intent-1', name='same.txt'):
    return client.post(f'/api/v2/trips/{trip_id}/documents',
        files=[('files', (name, content, 'text/plain'))], headers={'Idempotency-Key': key})


def eventually(function, timeout=10):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = function()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError('Timed out waiting for persisted state')


def test_sse_replay_uses_last_event_id_without_new_job_or_provider_call(service):
    a, b = service.login('A'), service.login('B')
    trip = _trip(a.client)
    submitted = _upload(a.client, trip['id'])
    snapshot = _job(a.client, submitted)
    calls = len(service.calls)
    url = f"/api/v2/jobs/{submitted['job_id']}/events"
    assert b.client.get(url).status_code == 404
    first = a.client.get(url)
    assert first.status_code == 200 and 'text/event-stream' in first.headers['content-type']
    ids = [int(line[4:]) for line in first.text.splitlines() if line.startswith('id: ')]
    assert ids == list(range(1, snapshot['last_event_id']+1))
    assert 'event: completed' in first.text
    after = ids[len(ids)//2]
    resumed = a.client.get(url, headers={'Last-Event-ID': str(after)})
    assert [int(line[4:]) for line in resumed.text.splitlines() if line.startswith('id: ')] == list(range(after+1, snapshot['last_event_id']+1))
    assert len(service.calls) == calls
    assert 'Synthetic reservation' not in resumed.text and 'private-job-artifacts' not in resumed.text
    invalid = a.client.get(url, headers={'Last-Event-ID': '999999'})
    assert invalid.status_code == 409 and invalid.json()['error']['code'] == 'EVENT_CURSOR_INVALID'


def test_live_sse_stops_after_session_revocation(service):
    a = service.login('A')
    trip = _trip(a.client)
    started, release = threading.Event(), threading.Event()
    def parser(raw):
        started.set()
        assert release.wait(6)
        return [_fact()]
    service.app.state.documents.parser = parser
    response = post_upload(a.client, trip['id'])
    assert response.status_code == 202
    job_id = response.json()['job_id']
    assert started.wait(3)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            live = pool.submit(a.client.get, f'/api/v2/jobs/{job_id}/events')
            # The stream has time to open while the provider remains blocked.
            time.sleep(.15)
            service.app.state.auth.disable_user(a.user['id'])
            stream = live.result(timeout=4)
            assert stream.status_code == 200 and 'event: access_revoked' in stream.text
            assert a.client.get(f'/api/v2/jobs/{job_id}').status_code == 401
    finally:
        release.set()


def test_concurrent_post_idempotency_same_intent_and_different_bytes(service):
    a = service.login('A')
    trip = _trip(a.client)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: post_upload(a.client, trip['id']), range(6)))
    assert all(response.status_code == 202 for response in responses), [response.text for response in responses]
    ids = {response.json()['job_id'] for response in responses}
    assert len(ids) == 1
    assert _job(a.client, responses[0].json())['state'] == 'succeeded'
    assert len([call for call in service.calls if call[0] == 'parse']) == 1
    with service.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM source_documents WHERE trip_id=?', (trip['id'],)).fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM jobs WHERE trip_id=?', (trip['id'],)).fetchone()[0] == 1
    mismatch = post_upload(a.client, trip['id'], content=b'Different input under same key')
    assert mismatch.status_code == 409 and mismatch.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    replay = post_upload(a.client, trip['id'])
    assert replay.status_code == 202 and replay.json()['job_id'] in ids


def test_different_upload_keys_same_inflight_content_make_one_paid_extraction(service):
    a = service.login('A')
    trip = _trip(a.client)
    started, release = threading.Event(), threading.Event()
    parse_calls = []
    def parser(raw):
        parse_calls.append(raw)
        started.set()
        assert release.wait(6)
        return [_fact()]
    service.app.state.documents.parser = parser
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(post_upload, a.client, trip['id'], b'Same in-flight bytes', key)
                       for key in ('independent-intent-one', 'independent-intent-two')]
            responses = [future.result(timeout=4) for future in pending]
        assert all(response.status_code == 202 for response in responses), [response.text for response in responses]
        assert len({response.json()['job_id'] for response in responses}) == 1
        assert started.wait(3)
        with service.app.state.db.connect() as con:
            assert con.execute('SELECT COUNT(*) FROM jobs WHERE trip_id=?', (trip['id'],)).fetchone()[0] == 1
    finally:
        release.set()
    assert _job(a.client, responses[0].json())['state'] == 'succeeded'
    assert parse_calls == ['Same in-flight bytes']


def test_manual_retry_only_failed_units_and_never_retries_unknown_charge(service):
    a = service.login('A')
    trip = _trip(a.client)
    parsed = []
    def parser(raw):
        parsed.append(raw)
        return [_fact(provider=raw, stable_item_key=raw)]
    def embed(texts):
        return [] if any('Bad fixture' in text for text in texts) else [[.1]*8 for _ in texts]
    service.app.state.documents.parser = parser
    service.app.state.documents.embedder = embed
    response = a.client.post(f"/api/v2/trips/{trip['id']}/documents", files=[
        ('files', ('good.txt', b'Good fixture', 'text/plain')),
        ('files', ('bad.txt', b'Bad fixture', 'text/plain'))], headers={'Idempotency-Key': 'partial-upload-intent'})
    assert response.status_code == 202, response.text
    snapshot = _job(a.client, response.json())
    assert snapshot['state'] == 'partial', snapshot
    failed = [item for item in snapshot['files'] if item['state'] == 'failed']
    assert len(failed) == 1 and failed[0]['error_code'] == 'EMBEDDING_INVALID'
    service.app.state.documents.embedder = lambda texts: [[.1]*8 for _ in texts]
    retry = a.client.post(f"/api/v2/jobs/{snapshot['job_id']}/retry", headers={'Idempotency-Key': 'explicit-retry-intent'})
    assert retry.status_code == 202, retry.text
    done = _job(a.client, retry.json())
    assert done['state'] == 'succeeded' and len(done['files']) == 1
    assert done['files'][0]['document_id'] == failed[0]['document_id']
    # Completed extraction within the failed document is reusable too: only
    # that document's missing embedding/activation stages run in the new job.
    assert parsed.count('Good fixture') == parsed.count('Bad fixture') == 1
    assert len(a.client.get(f"/api/v2/trips/{trip['id']}/bookings").json()['items']) == 2
    # An old browser still showing the original partial job cannot charge its
    # completed retry again by generating a fresh client key.
    again = a.client.post(f"/api/v2/jobs/{snapshot['job_id']}/retry", headers={'Idempotency-Key': 'different-button-click'})
    assert again.status_code == 202 and again.json()['job_id'] == done['job_id']
    assert parsed.count('Good fixture') == parsed.count('Bad fixture') == 1
    def uncertain(raw):
        raise TimeoutError('Synthetic provider request may have reached remote')
    service.app.state.documents.parser = uncertain
    uncertain_job = post_upload(a.client, trip['id'], b'Unknown external outcome', 'unknown-charge-intent')
    failed = _job(a.client, uncertain_job.json())
    assert failed['state'] == 'failed'
    blocked = a.client.post(f"/api/v2/jobs/{failed['job_id']}/retry", headers={'Idempotency-Key': 'must-not-repeat'})
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'JOB_NOT_RETRYABLE'


def test_corrupt_active_index_enqueues_one_repair_while_sql_daily_reads_work(service):
    a = service.login('A')
    trip = _trip(a.client)
    _upload(a.client, trip['id'])
    with service.app.state.db.connect() as con:
        active = con.execute('SELECT g.* FROM trips t JOIN trip_index_generations g ON g.id=t.active_index_id WHERE t.id=?', (trip['id'],)).fetchone()
    service.app.state.generations.client.delete_collection(name=active['collection_name'])
    started, release = threading.Event(), threading.Event()
    def blocked_embed(texts):
        started.set()
        assert release.wait(6)
        return [[.1]*8 for _ in texts]
    service.app.state.documents.embedder = blocked_embed
    path = f"/api/v2/trips/{trip['id']}/ask"
    try:
        first = a.client.post(path, json={'question': '취소 조건을 알려줘'})
        assert first.status_code == 503 and first.json()['error']['code'] == 'SEARCH_REBUILDING', first.text
        assert started.wait(3)
        second = a.client.post(path, json={'question': '예약 정책을 알려줘'})
        assert second.status_code == 503, second.text
        jobs = a.client.get(f"/api/v2/trips/{trip['id']}/jobs").json()['items']
        repairs = [item for item in jobs if item['operation'] == 'reindex']
        assert len(repairs) == 1
        daily = a.client.post(path, json={'question': '2026-11-07 전체 예약 알려줘'})
        assert daily.status_code == 200 and '09:00' in daily.json()['answer']
        assert 'bookings_on_date' in daily.json()['tools_used']
    finally:
        release.set()
    assert _job(a.client, repairs[0])['state'] == 'succeeded'
    healthy = a.client.post(path, json={'question': '취소 조건을 알려줘'})
    assert healthy.status_code == 200, healthy.text


def test_delete_during_provider_blocks_old_jobs_and_purges_after_late_result(service):
    a, b = service.login('A'), service.login('B')
    trip = _trip(a.client)
    started, release = threading.Event(), threading.Event()
    def parser(raw):
        started.set()
        assert release.wait(6)
        return [_fact()]
    service.app.state.documents.parser = parser
    response = post_upload(a.client, trip['id'])
    assert response.status_code == 202
    job_id = response.json()['job_id']
    assert started.wait(3)
    try:
        deletion = a.client.delete(f"/api/v2/trips/{trip['id']}")
        assert deletion.status_code == 202, deletion.text
        receipt = deletion.json()
        for suffix in ('', '/events'):
            assert a.client.get(f'/api/v2/jobs/{job_id}'+suffix).status_code == 404
        status = a.client.get(receipt['receipt_url'])
        assert set(status.json()) == {'receipt_id', 'state', 'error_code', 'updated_at'}
        assert b.client.get(receipt['receipt_url']).status_code == 404
        assert a.client.get(f"/api/v2/trips/{trip['id']}/bookings").status_code == 404
        with service.app.state.db.connect() as con:
            assert con.execute("SELECT 1 FROM deletion_tombstones WHERE target_type='trip' AND target_id=?", (trip['id'],)).fetchone()
    finally:
        release.set()
    final = eventually(lambda: (lambda value: value if value['state'] in {'succeeded','failed'} else None)(a.client.get(receipt['receipt_url']).json()))
    assert final['state'] == 'succeeded', final
    assert not (service.settings.documents_dir/trip['id']).exists()
    assert not (service.settings.artifacts_dir/trip['id']).exists()
    with service.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM bookings WHERE trip_id=? AND deleted_at IS NULL', (trip['id'],)).fetchone()[0] == 0
        rows = con.execute('SELECT state FROM usage_reservations WHERE trip_id=?', (trip['id'],)).fetchall()
        assert rows and all(row['state'] == 'settled' for row in rows)
