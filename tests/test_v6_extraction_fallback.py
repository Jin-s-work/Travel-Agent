"""v6 stage 2: AI extraction failure -> local fallback / review, through real jobs.

Documents run on the app's dispatcher threads, so the replay adapter is chosen
by environment (MAIL_EXTRACTION_CHAIN=replay, LLM_REPLAY_DIR) and priced as
replay/<EXTRACTION_MODEL> at 0. Model answers are handmade synthetic records.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.foundation import local_mail
from src.loader import read_email_bytes
from tests.test_foundation_api import service, _job, _reprocess, _trip, _upload  # noqa: F401 - fixture
from tests.test_local_mail import PACK
from tests.test_v6_llm_gateway import (booking, document, extraction_kwargs, policy_with, put_record, repair_kwargs,
                                       replay_runtime_prices)

HOTEL = '02-tokyo-hotel.eml'
HOTEL_BYTES = (PACK / HOTEL).read_bytes()
HOTEL_INVALID = document(booking('A hotel sentence that the mail never contained.', kind='숙소',
                                 provider='デモ東京ホテル 本館 (架空施設)', confirmation_number='TEST-HOTEL-002',
                                 date='2026-11-06', time='15:00'))
DINNER_BYTES = (b'SYNTHETIC TEST DATA. Demo Bistro dinner reservation confirmed for 2026-11-07 at 19:00. '
                b'Reference SYN-DIN-7.')
DINNER_SNIPPET = 'Demo Bistro dinner reservation confirmed for 2026-11-07 at 19:00.'
DINNER_GOOD = document(booking(DINNER_SNIPPET, provider='Demo Bistro', confirmation_number='SYN-DIN-7'))
DINNER_INVALID = document(booking('Something the mail never said.', provider='Demo Bistro', confirmation_number='SYN-DIN-7'))
DINNER_INVALID_REPAIR = document(booking(DINNER_SNIPPET, provider='Demo Bistro', confirmation_number='SYN-DIN-7', time='7pm'))


def ai_replay(service, monkeypatch, tmp_path, *, requested='auto', name='fallback'):
    """Real parser path (no injected parser) with a replay chain set by environment."""
    replay_dir = tmp_path / 'replay-records'
    for variable in ('EMBEDDING_PROVIDER', 'ANSWER_PROVIDER', 'AGENT_PROVIDER', 'LLM_PAID_FALLBACK'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'replay')
    monkeypatch.setenv('LLM_REPLAY_DIR', str(replay_dir))
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    service.app.state.documents.parser = None  # embedder stays the injected fake
    service.app.state.budget.policy = policy_with(replay_runtime_prices())
    service.settings.mail_analysis_mode = requested
    local_calls = []
    original = local_mail.parse_local_document

    def counted(raw):
        local_calls.append(1)
        return original(raw)
    monkeypatch.setattr(local_mail, 'parse_local_document', counted)
    user = service.login(name)
    trip = _trip(user.client)
    capabilities = user.client.get('/api/v2/mail-capabilities').json()
    assert capabilities['analysis_mode'] == 'ai', capabilities
    return SimpleNamespace(user=user, client=user.client, trip=trip, base=f"/api/v2/trips/{trip['id']}",
                           replay_dir=replay_dir, local_calls=local_calls)


def ledger(service):
    with service.app.state.db.connect() as con:
        return [dict(row) for row in con.execute('SELECT provider,call_key,state FROM usage_reservations ORDER BY created_at')]


def upload(env, name, content, key):
    receipt = _upload(env.client, env.trip['id'], name=name, content=content, key=key)
    return _job(env.client, receipt)


def test_auto_mode_falls_back_to_local_parser_after_failed_repair(service, monkeypatch, tmp_path):
    env = ai_replay(service, monkeypatch, tmp_path)
    raw = read_email_bytes(HOTEL_BYTES, HOTEL)
    put_record(env.replay_dir, extraction_kwargs(raw), HOTEL_INVALID)
    put_record(env.replay_dir, repair_kwargs(raw, HOTEL_INVALID), HOTEL_INVALID)
    result = upload(env, HOTEL, HOTEL_BYTES, 'hotel')
    item = result['files'][0]
    assert item['analysis_mode'] == 'ai' and item['state'] == 'needs_review', item
    assert 'AI_EXTRACTION_FALLBACK' in item['review_reasons'] and 'BASIC_EXTRACTION_REVIEW' in item['review_reasons']
    assert item['extraction']['fallback_path'] == ['ai', 'repair', 'local']
    assert item['extraction']['repair_attempts'] == 1 and item['extraction']['error_kind'] == 'invalid'
    assert item['extraction']['prompt']['id'] == 'mail_document_extraction'
    assert result['review_required'] is True
    assert result['state'] == 'partial'  # AI-mode needs_review is not counted as a clean success.
    rows = env.client.get(env.base + '/bookings').json()['items']
    assert [(r['provider'], r['confirmation_number']) for r in rows] == [('デモ東京ホテル 本館 (架空施設)', 'TEST-HOTEL-002')]
    documents = env.client.get(env.base + '/documents').json()['items']
    assert documents[0]['status'] == 'needs_review' and documents[0]['active_generation_id']
    assert env.local_calls == [1]
    replay = [row for row in ledger(service) if row['provider'] == 'replay']
    assert [row['call_key'].rsplit(':extract', 1)[1] for row in replay] == [':chat:0', ':repair:chat:0']
    assert all(row['state'] == 'settled' for row in replay)


def test_local_failure_keeps_active_bookings_and_user_corrections(service, monkeypatch, tmp_path):
    env = ai_replay(service, monkeypatch, tmp_path, name='keep')
    raw = read_email_bytes(DINNER_BYTES, 'dinner.txt')
    put_record(env.replay_dir, extraction_kwargs(raw), DINNER_GOOD, source='handmade')
    first = upload(env, 'dinner.txt', DINNER_BYTES, 'dinner')
    assert first['state'] == 'succeeded' and first['files'][0]['extraction']['fallback_path'] == ['ai'], first
    row = env.client.get(env.base + '/bookings').json()['items'][0]
    patched = env.client.patch(env.base + '/bookings/' + row['id'], json={'expected_version': row['version'],
                               'changes': [{'field_path': 'time', 'value': '20:30'}]})
    assert patched.status_code == 200, patched.text
    before = env.client.get(env.base + '/bookings').json()['items']
    assert before[0]['time'] == '20:30'
    document_before = env.client.get(env.base + '/documents').json()['items'][0]
    # The same mail now gets an unusable answer and an unusable repair; the
    # basic parser cannot read it either (no supported booking kind).
    put_record(env.replay_dir, extraction_kwargs(raw), DINNER_INVALID)
    put_record(env.replay_dir, repair_kwargs(raw, DINNER_INVALID), DINNER_INVALID_REPAIR)
    again = _job(env.client, _reprocess(env.client, env.base + f"/documents/{document_before['id']}/reprocess").json())
    item = again['files'][0]
    assert item['state'] == 'needs_review' and item['error_code'] == 'EXTRACTION_NEEDS_REVIEW', item
    assert item['activated'] is False and item['bookings_count'] == 0
    assert item['extraction']['fallback_path'] == ['ai', 'repair', 'needs_review']
    assert env.client.get(env.base + '/bookings').json()['items'] == before
    document_after = env.client.get(env.base + '/documents').json()['items'][0]
    assert document_after['active_generation_id'] == document_before['active_generation_id']
    assert document_after['status'] == 'needs_review'
    assert document_after['latest_generation']['status'] == 'needs_review'
    assert document_after['latest_generation']['error_code'] == 'EXTRACTION_NEEDS_REVIEW'
    assert env.local_calls == [1]


def test_ai_mode_reports_invalid_extraction_without_local_fallback(service, monkeypatch, tmp_path):
    env = ai_replay(service, monkeypatch, tmp_path, requested='ai', name='strict')
    raw = read_email_bytes(HOTEL_BYTES, HOTEL)
    put_record(env.replay_dir, extraction_kwargs(raw), HOTEL_INVALID)
    put_record(env.replay_dir, repair_kwargs(raw, HOTEL_INVALID), HOTEL_INVALID)
    result = upload(env, HOTEL, HOTEL_BYTES, 'strict')
    item = result['files'][0]
    assert result['state'] == 'failed'
    assert item['state'] == 'failed' and item['error_code'] == 'EXTRACTION_INVALID', item
    assert env.local_calls == []
    assert env.client.get(env.base + '/bookings').json()['items'] == []
    assert len([row for row in ledger(service) if row['provider'] == 'replay']) == 2  # one repair was tried
    from src.prompts import load_prompt
    assert item['extraction'] == {'repair_attempts': 1, 'error_kind': 'invalid', 'fallback_path': ['ai', 'repair'],
                                  'prompt': load_prompt('mail_document_extraction').meta()}


@pytest.mark.parametrize('failure', ['replay_miss', 'transport_unknown'])
def test_billing_or_transport_errors_are_never_converted_to_fallback(service, monkeypatch, tmp_path, failure):
    env = ai_replay(service, monkeypatch, tmp_path, name='no-fallback-' + failure)
    raw = read_email_bytes(HOTEL_BYTES, HOTEL)
    if failure == 'transport_unknown':
        put_record(env.replay_dir, extraction_kwargs(raw), HOTEL_INVALID)

        def lost(self, client, kind, kwargs):
            raise RuntimeError('synthetic response lost after send')
        monkeypatch.setattr('src.llm.replay.ReplayAdapter.send', lost)
    result = upload(env, HOTEL, HOTEL_BYTES, failure)
    item = result['files'][0]
    expected = {'replay_miss': 'REPLAY_MISS', 'transport_unknown': 'PROVIDER_OUTCOME_UNKNOWN'}[failure]
    assert item['state'] == 'failed' and item['error_code'] == expected, item
    assert env.local_calls == []
    assert env.client.get(env.base + '/bookings').json()['items'] == []
    replay = [row for row in ledger(service) if row['provider'] == 'replay']
    assert [row['state'] for row in replay] == ([] if failure == 'replay_miss' else ['unknown'])


@pytest.mark.parametrize('failure', ['repair_miss', 'repair_unknown'])
def test_repair_call_billing_or_transport_errors_are_never_converted_to_fallback(service, monkeypatch, tmp_path, failure):
    env = ai_replay(service, monkeypatch, tmp_path, name='repair-' + failure)
    raw = read_email_bytes(HOTEL_BYTES, HOTEL)
    put_record(env.replay_dir, extraction_kwargs(raw), HOTEL_INVALID)
    if failure == 'repair_unknown':
        put_record(env.replay_dir, repair_kwargs(raw, HOTEL_INVALID), HOTEL_INVALID)
        from src.llm.replay import ReplayAdapter
        original = ReplayAdapter.send

        def lost_on_repair(self, client, kind, kwargs):
            if len(kwargs.get('messages') or ()) > 2:  # the repair request appends two messages
                raise RuntimeError('synthetic repair response lost after send')
            return original(self, client, kind, kwargs)
        monkeypatch.setattr(ReplayAdapter, 'send', lost_on_repair)
    result = upload(env, HOTEL, HOTEL_BYTES, failure)
    item = result['files'][0]
    expected = {'repair_miss': 'REPLAY_MISS', 'repair_unknown': 'PROVIDER_OUTCOME_UNKNOWN'}[failure]
    assert item['state'] == 'failed' and item['error_code'] == expected, item
    from src.prompts import load_prompt
    assert item['extraction'] == {'repair_attempts': 1, 'error_kind': 'provider', 'fallback_path': ['ai', 'repair'],
                                  'prompt': load_prompt('mail_document_extraction').meta()}
    assert env.local_calls == []
    assert env.client.get(env.base + '/bookings').json()['items'] == []
    replay = [row for row in ledger(service) if row['provider'] == 'replay']
    assert [row['state'] for row in replay] == (['settled'] if failure == 'repair_miss' else ['settled', 'unknown'])


AI_ONLY_REFUND = 'AI-only refund note (synthetic)'
HOTEL_SNIPPET = '施設: デモ東京ホテル 本館 (架空施設). 予約番号 TEST-HOTEL-002.'
HOTEL_GOOD = document(booking(HOTEL_SNIPPET, kind='숙소', provider='デモ東京ホテル 本館 (架空施設)',
                              confirmation_number='TEST-HOTEL-002', date='2026-11-06', date_end='2026-11-09',
                              time='15:00', time_end='11:00', refund_policy=AI_ONLY_REFUND))


def test_failed_ai_reprocess_never_replaces_active_bookings_with_the_local_result(service, monkeypatch, tmp_path):
    """AI extraction -> user correction -> AI fails and the basic parser succeeds:
    the active AI bookings (and the correction) stay; the basic result is staged only."""
    env = ai_replay(service, monkeypatch, tmp_path, name='keep-ai')
    raw = read_email_bytes(HOTEL_BYTES, HOTEL)
    put_record(env.replay_dir, extraction_kwargs(raw), HOTEL_GOOD, source='handmade')
    first = upload(env, HOTEL, HOTEL_BYTES, 'keep-ai')
    assert first['state'] == 'succeeded' and first['files'][0]['extraction']['fallback_path'] == ['ai'], first
    row = env.client.get(env.base + '/bookings').json()['items'][0]
    assert row['refund_policy'] == AI_ONLY_REFUND
    patched = env.client.patch(env.base + '/bookings/' + row['id'], json={'expected_version': row['version'],
                               'changes': [{'field_path': 'time', 'value': '16:30'}]})
    assert patched.status_code == 200, patched.text
    before = env.client.get(env.base + '/bookings').json()['items']
    document_before = env.client.get(env.base + '/documents').json()['items'][0]
    put_record(env.replay_dir, extraction_kwargs(raw), HOTEL_INVALID)
    put_record(env.replay_dir, repair_kwargs(raw, HOTEL_INVALID), HOTEL_INVALID)
    again = _job(env.client, _reprocess(env.client, env.base + f"/documents/{document_before['id']}/reprocess").json())
    item = again['files'][0]
    assert item['state'] == 'needs_review' and item['error_code'] == 'EXTRACTION_NEEDS_REVIEW', item
    assert item['activated'] is False and item['bookings_count'] == 0
    assert item['extraction']['fallback_path'] == ['ai', 'repair', 'local', 'needs_review']
    assert 'AI_EXTRACTION_FALLBACK' in item['review_reasons']
    assert again['state'] == 'failed'  # nothing was activated (same rule as the other needs_review path)
    after = env.client.get(env.base + '/bookings').json()['items']
    assert after == before and [(b['time'], b['refund_policy']) for b in after] == [('16:30', AI_ONLY_REFUND)]
    document_after = env.client.get(env.base + '/documents').json()['items'][0]
    assert document_after['active_generation_id'] == document_before['active_generation_id']
    assert document_after['status'] == 'needs_review'
    assert document_after['latest_generation']['status'] == 'needs_review'
    assert env.local_calls == [1]
    with service.app.state.db.connect() as con:
        # The newest generation by the id the API reports (rowid is SQLite-only).
        staged = con.execute('SELECT status,extracted_json FROM document_generations WHERE id=? AND document_id=?',
                             (document_after['latest_generation']['id'], document_before['id'])).fetchone()
    facts = json.loads(staged['extracted_json'])
    assert staged['status'] == 'needs_review' and [f['confirmation_number'] for f in facts] == ['TEST-HOTEL-002']
    assert facts[0]['refund_policy'] is None  # the basic parser's result, kept for review only


def hotel_runtime(tmp_path, monkeypatch):
    """jobs.claim -> JobContext -> Operations (no dispatcher) for the hotel mail."""
    from src.foundation.repository import utcnow
    from tests.test_reliability_runtime import construct
    replay_dir = tmp_path / 'replay-records'
    for variable in ('EMBEDDING_PROVIDER', 'ANSWER_PROVIDER', 'AGENT_PROVIDER', 'LLM_PAID_FALLBACK'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'replay')
    monkeypatch.setenv('LLM_REPLAY_DIR', str(replay_dir))
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    env = construct(tmp_path, policy=policy_with(replay_runtime_prices()))
    env.documents.parser = None
    env.documents.settings.mail_analysis_mode = 'auto'
    now = utcnow()
    with env.db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    ('A', 'fake@example.invalid', 'fake', 'A', now, now))
        con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES(?,?,?,?,?,?,?)',
                    ('session-A', 'A', 'hash', 'csrf', '2099-01-01T00:00:00+00:00', 0, now))
    trip = env.repo.create_trip('A', {'title': 'Synthetic Tokyo', 'start_date': '2026-11-05', 'end_date': '2026-11-10'})
    doc = env.documents.save('A', trip['id'], HOTEL, HOTEL_BYTES)
    trip = env.repo.get_trip('A', trip['id'])
    payload = {'accepted': [{'document_id': doc['id'], 'filename': HOTEL}], 'duplicates': [], 'rejected': []}
    job = env.jobs.enqueue('A', 'session-A', 'personal_trip', trip['id'], 'documents', payload, trip['version'], 'synthetic-hotel-key')
    return env, trip['id'], job['id'], doc['id'], replay_dir


def test_crash_between_fallback_activation_and_review_mark_still_needs_review(tmp_path, monkeypatch):
    from src.foundation.repository import DomainError
    from tests.test_reliability_runtime import expire_leases, run_one
    env, trip, job_id, did, replay_dir = hotel_runtime(tmp_path, monkeypatch)
    raw = read_email_bytes(HOTEL_BYTES, HOTEL)
    put_record(replay_dir, extraction_kwargs(raw), HOTEL_INVALID)
    put_record(replay_dir, repair_kwargs(raw, HOTEL_INVALID), HOTEL_INVALID)
    local_calls = []
    original = local_mail.parse_local_document
    monkeypatch.setattr(local_mail, 'parse_local_document', lambda raw: (local_calls.append(1), original(raw))[1])
    crashed = []

    def lose_lease(point, job):
        if point == 'before_mark_review' and not crashed:
            crashed.append(point)
            raise DomainError('LEASE_LOST', 'synthetic lease loss', 409)
    env.operations.fault_hook = lose_lease
    with pytest.raises(DomainError):
        run_one(env)
    stored_doc = env.repo.get_document('A', trip, did)
    assert crashed and stored_doc['active_generation_id'] and stored_doc['status'] != 'needs_review'  # the window
    expire_leases(env)
    result = run_one(env)
    item = result['result']['files'][0]
    assert item['state'] == 'needs_review' and 'AI_EXTRACTION_FALLBACK' in item['review_reasons'], item
    assert item['extraction']['fallback_path'] == ['ai', 'repair', 'local']
    assert result['state'] == 'partial'
    assert env.repo.get_document('A', trip, did)['status'] == 'needs_review'
    assert local_calls == [1]  # resumed from the checkpoint, not re-parsed


def test_injected_parser_path_is_unchanged(service, monkeypatch):
    local_calls = []
    monkeypatch.setattr(local_mail, 'parse_local_document', lambda raw: local_calls.append(1))
    user = service.login('injected')
    trip = _trip(user.client)
    result = _job(user.client, _upload(user.client, trip['id'], key='injected-ok'))
    assert result['state'] == 'succeeded'
    assert result['files'][0]['extraction'] == {'repair_attempts': 0, 'fallback_path': ['injected'], 'prompt': None}
    assert [call[0] for call in service.calls].count('parse') == 1
    service.state.parse_error = True
    failed = _job(user.client, _upload(user.client, trip['id'], name='other.txt', content=b'Synthetic other', key='injected-bad'))
    assert failed['files'][0]['error_code'] == 'PROVIDER_OUTCOME_UNKNOWN'
    assert [call[0] for call in service.calls].count('parse') == 2
    assert local_calls == []
