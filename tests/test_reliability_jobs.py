"""Real temporary SQLite, concurrent callers and killed-process recovery tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from src.foundation.db import Database, SCHEMA
from src.foundation.repository import DomainError, Repository
from src.reliability.dispatcher import Dispatcher, RetryableJobError
from src.reliability.jobs import Jobs
from tests.job_diagnostics import claim_required, snapshot as job_snapshot


@pytest.fixture
def work(tmp_path):
    db = Database(tmp_path / 'private.sqlite')
    now = datetime.now(timezone.utc)
    with db.connect() as con:
        for user in ('A', 'B', 'admin'):
            con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                        (user, user+'@example.invalid', 'fake', user, 'admin' if user == 'admin' else 'member', now.isoformat(), now.isoformat()))
            con.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?)', (user+'s', user, user+'hash', user+'csrf', (now+timedelta(hours=2)).isoformat(), 0, now.isoformat()))
    repo = Repository(db)
    trips = {user: repo.create_trip(user, {'title': user+' synthetic', 'start_date': '2026-11-01', 'end_date': '2026-11-03'})['id'] for user in ('A', 'B')}
    return db, repo, trips, Jobs(db, lease_seconds=.3)


def enqueue(work, key='intent-001', *, actor='A', **kwargs):
    _, _, trips, jobs = work
    return jobs.enqueue(actor, actor+'s', 'personal_trip', trips[actor], 'documents',
                        kwargs.pop('payload', {'document_ids': ['doc_fake']}), kwargs.pop('input_version', 1), key, **kwargs)


def expect_code(code, function):
    with pytest.raises(DomainError) as error:
        function()
    assert error.value.code == code


def test_version_two_migrates_existing_db_without_losing_receipts(tmp_path):
    path = tmp_path/'old.sqlite'
    con = sqlite3.connect(path)
    con.executescript(SCHEMA+'\nPRAGMA user_version=1;')
    con.execute("INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES ('legacy','fake@example.invalid','fake','legacy','2026','2026')")
    con.execute("INSERT INTO trips(id,owner_id,title,start_date,end_date,created_at,updated_at) VALUES ('oldtrip','legacy','synthetic','2026-11-01','2026-11-02','2026','2026')")
    for state in ('running', 'queued', 'succeeded'):
        con.execute('INSERT INTO processing_receipts(id,user_id,trip_id,payload_hash,status,result_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                    (state, 'legacy', 'oldtrip', 'hash', state, '{"files":[{"state":"succeeded"}]}', '2026', '2026'))
    con.commit()
    con.close()
    db = Database(path)
    with db.connect() as con:
        from src.foundation.db import SCHEMA_VERSION
        assert con.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        tables = {row['name'] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'processing_receipts', 'jobs', 'job_events', 'trip_index_generations', 'usage_reservations', 'usage_ledger'} <= tables
        receipts = {row['id']: dict(row) for row in con.execute('SELECT * FROM processing_receipts')}
        assert receipts['succeeded']['status'] == 'succeeded'
        assert receipts['running']['status'] == receipts['queued']['status'] == 'failed'
        assert json.loads(receipts['running']['result_json']) == {'files': [{'state': 'succeeded'}], 'error_code': 'RECOVERY_REQUIRED', 'recovery_required': True}
    Database(path)


def test_idempotency_concurrent_same_key_creates_exactly_one_job(work):
    with ThreadPoolExecutor(max_workers=12) as pool:
        values = list(pool.map(lambda _: enqueue(work), range(24)))
    assert len({item['id'] for item in values}) == 1
    db, _, _, _ = work
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM job_events').fetchone()[0] == 1
    expect_code('IDEMPOTENCY_CONFLICT', lambda: enqueue(work, payload={'document_ids': ['different']}))
    expect_code('IDEMPOTENCY_CONFLICT', lambda: enqueue(work, input_version=2))


def test_admin_scope_with_null_trip_remains_idempotent_and_private(work):
    db, _, _, jobs = work
    jobs.admin_scopes = frozenset({'tokyo-approved'})
    args = ('admin', 'admins', 'admin_research', 'tokyo-approved', 'research', {'place_id': 'synthetic'}, 0, 'same-key-admin')
    first = jobs.enqueue(*args)
    assert jobs.enqueue(*args)['id'] == first['id']
    with db.connect() as con:
        row = con.execute('SELECT * FROM jobs WHERE id=?', (first['id'],)).fetchone()
        assert row['trip_id'] is None and row['owner_id'] is None
    expect_code('NOT_FOUND', lambda: jobs.get(first['id'], 'A', 'As'))
    expect_code('NOT_FOUND', lambda: jobs.enqueue('A', 'As', *args[2:]))
    expect_code('INVALID_JOB_SCOPE', lambda: jobs.enqueue('admin', 'admins', 'admin_research', '', *args[4:]))
    jobs.admin_scopes = frozenset()
    expect_code('NOT_FOUND', lambda: jobs.get(first['id'], 'admin', 'admins'))


def test_scope_ownership_session_and_safe_reference_validation(work):
    _, _, trips, jobs = work
    first = enqueue(work)
    expect_code('NOT_FOUND', lambda: jobs.get(first['id'], 'B', 'Bs'))
    expect_code('NOT_FOUND', lambda: jobs.events(first['id'], 'B', 'Bs'))
    expect_code('NOT_FOUND', lambda: jobs.enqueue('B', 'Bs', 'personal_trip', trips['A'], 'documents', {}, 1, 'another-key'))
    expect_code('AUTH_REQUIRED', lambda: jobs.get(first['id'], 'A', 'Bs'))
    expect_code('INVALID_JOB_REFERENCE', lambda: enqueue(work, 'key-secrets', payload={'nested': [{'api_key': 'must-not-store'}]}))
    expect_code('INVALID_JOB_OPERATION', lambda: jobs.enqueue('A', 'As', 'personal_trip', trips['A'], 'cleanup_trip', {}, 1, 'cleanup-spoof'))


def test_claim_global_dispatcher_and_single_concurrency(work):
    _, _, _, jobs = work
    enqueue(work)
    enqueue(work, 'intent-002')
    claimed = jobs.claim('worker-1')
    assert claimed['attempt'] == claimed['fencing_token'] == 1
    assert jobs.claim('worker-1') is None
    assert jobs.claim('worker-2') is None
    jobs.finish(claimed['id'], claimed['fencing_token'])
    assert jobs.claim('worker-1') is not None


def test_expired_worker_is_fenced_before_and_after_reclaim(work):
    db, _, _, jobs = work
    current = [datetime.now(timezone.utc)]
    jobs.clock = lambda: current[0]
    enqueue(work)
    old = jobs.claim('old-worker')
    jobs.checkpoint(old['id'], old['fencing_token'], {'extraction_ref': 'artifact_abc'}, stage='extracted', done=1, total=2)
    current[0] += timedelta(seconds=.4)
    expect_code('LEASE_LOST', lambda: jobs.finish(old['id'], old['fencing_token']))
    resumed = jobs.claim('new-worker')
    assert resumed['attempt'] == 2 and resumed['fencing_token'] == 2
    assert resumed['checkpoint']['extraction_ref'] == 'artifact_abc'
    expect_code('LEASE_LOST', lambda: jobs.checkpoint(old['id'], old['fencing_token'], {'bad_ref': 'old'}))
    expect_code('LEASE_LOST', lambda: jobs.heartbeat(old['id'], old['fencing_token'], 'old-worker'))
    jobs.finish(resumed['id'], resumed['fencing_token'])
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM trip_index_writers').fetchone()[0] == 0


def test_checkpoint_and_completion_can_share_activation_transaction(work):
    db, _, _, jobs = work
    job = enqueue(work)
    claimed = jobs.claim('one')
    with pytest.raises(RuntimeError):
        with db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            jobs.guard(job['id'], claimed['fencing_token'], con)
            jobs.checkpoint(job['id'], claimed['fencing_token'], {'activated_ref': 'index_fake'}, con=con)
            jobs.finish(job['id'], claimed['fencing_token'], con=con)
            raise RuntimeError('force rollback')
    assert jobs.get(job['id'], 'A', 'As')['state'] == 'running'
    assert jobs.guard(job['id'], claimed['fencing_token'])['checkpoint'] == {}


def test_queued_cancel_has_no_claim_running_cancel_is_only_request(work):
    _, _, _, jobs = work
    queued = enqueue(work)
    assert jobs.cancel(queued['id'], 'A', 'As')['state'] == 'cancelled'
    assert jobs.claim('worker') is None
    running = enqueue(work, 'next-intent')
    claimed = jobs.claim('worker')
    snapshot = jobs.cancel(running['id'], 'A', 'As')
    assert snapshot['state'] == 'running' and snapshot['cancel_requested_at']
    expect_code('JOB_CANCELLED', lambda: jobs.guard(running['id'], claimed['fencing_token']))
    jobs.finish(running['id'], claimed['fencing_token'], state='cancelled')
    assert jobs.cancel(running['id'], 'A', 'As')['state'] == 'cancelled'


def test_session_revocation_disallows_activation_and_queued_execution(work):
    db, _, _, jobs = work
    first = enqueue(work)
    claimed = jobs.claim('one')
    second = enqueue(work, 'second-intent')
    with db.connect() as con:
        con.execute("UPDATE users SET session_epoch=1 WHERE id='A'")
    expect_code('AUTH_REQUIRED', lambda: jobs.finish(first['id'], claimed['fencing_token']))
    jobs.finish(first['id'], claimed['fencing_token'], state='failed', error_code='AUTH_REQUIRED')
    assert jobs.claim('one') is None
    with db.connect() as con:
        assert con.execute('SELECT error_code FROM jobs WHERE id=?', (second['id'],)).fetchone()[0] == 'AUTH_REQUIRED'


def test_deleted_trip_jobs_are_hidden_and_cleanup_receipt_is_minimal(work):
    db, repo, trips, jobs = work
    # This assertion exercises deletion, not lease expiry. Network-backed SQL
    # can take longer than the fixture's 300 ms lease during the preceding reads.
    jobs.lease_seconds = 10
    regular = enqueue(work)
    old = jobs.claim('worker')
    repo.delete_trip('A', trips['A'])
    receipt = jobs.enqueue_cleanup('A', 'As', trips['A'])
    assert set(receipt) == {'receipt_id', 'state', 'error_code', 'updated_at'}
    expect_code('NOT_FOUND', lambda: jobs.get(regular['id'], 'A', 'As'))
    expect_code('NOT_FOUND', lambda: jobs.events(regular['id'], 'A', 'As'))
    expect_code('NOT_FOUND', lambda: jobs.guard(regular['id'], old['fencing_token']))
    jobs.finish(regular['id'], old['fencing_token'], state='cancelled')
    # Cleanup is a narrowly authorized system continuation, not personal result production.
    with db.connect() as con:
        con.execute("UPDATE users SET status='disabled' WHERE id='A'")
    cleanup = jobs.claim('worker')
    assert cleanup['operation'] == 'cleanup_trip'
    jobs.guard(cleanup['id'], cleanup['fencing_token'])
    jobs.finish(cleanup['id'], cleanup['fencing_token'])
    expect_code('AUTH_REQUIRED', lambda: jobs.deletion_receipt(receipt['receipt_id'], 'A', 'As'))
    expect_code('NOT_FOUND', lambda: jobs.deletion_receipt(receipt['receipt_id'], 'B', 'Bs'))


def test_crash_between_tombstone_and_cleanup_enqueue_recovers_without_user_session(work):
    db, repo, trips, jobs = work
    original = enqueue(work)
    repo.delete_trip('A', trips['A'])
    with db.connect() as con:
        con.execute("DELETE FROM sessions WHERE user_id='A'")
    recovered = jobs.recover_cleanup()
    assert len(recovered) == 1
    assert jobs.recover_cleanup() == []
    cleanup = jobs.claim('recovered-worker')
    assert cleanup['operation'] == 'cleanup_trip' and cleanup['actor_id'] == 'A'
    assert cleanup['session_id'] == 'system_cleanup' and cleanup['deletion_epoch'] >= 1
    jobs.finish(cleanup['id'], cleanup['fencing_token'])
    with db.connect() as con:
        assert con.execute('SELECT state FROM jobs WHERE id=?', (original['id'],)).fetchone()[0] == 'cancelled'


def test_job_list_pagination_is_not_limited_to_two_hundred_records(work):
    _, _, trips, jobs = work
    for number in range(203):
        enqueue(work, f'event-intent-{number:03}')
    first = jobs.list_for_trip(trips['A'], 'A', 'As', limit=200)
    second = jobs.list_for_trip(trips['A'], 'A', 'As', limit=200, offset=200)
    assert jobs.count_for_trip(trips['A'], 'A', 'As') == 203
    assert len(first) == 200 and len(second) == 3
    assert not {row['id'] for row in first} & {row['id'] for row in second}


def test_client_fingerprint_is_stable_after_completion_and_derived_payload_changes(work):
    _, _, trips, jobs = work
    args = ('A', 'As', 'personal_trip', trips['A'], 'documents')
    first = jobs.enqueue(*args, {'accepted': [{'document_id': 'original', 'filename': 'same.eml'}]}, 1,
                         'network-intent', request_fingerprint='original-client-bytes-hash')
    worker = jobs.claim('worker')
    jobs.finish(worker['id'], worker['fencing_token'])
    repeated = jobs.enqueue(*args, {'duplicates': [{'document_id': 'original', 'filename': 'same.eml'}]}, 2,
                            'network-intent', request_fingerprint='original-client-bytes-hash')
    assert repeated['id'] == first['id']
    assert repeated['submission'] == first['submission']
    assert jobs.lookup(*args, 'network-intent', 'original-client-bytes-hash')['id'] == first['id']
    expect_code('IDEMPOTENCY_CONFLICT', lambda: jobs.lookup(*args, 'network-intent', 'changed-client-bytes-hash'))


def test_one_retry_child_per_parent_even_for_concurrent_different_keys(work):
    db, _, trips, jobs = work
    original = enqueue(work)
    claimed = jobs.claim('worker')
    jobs.finish(claimed['id'], claimed['fencing_token'], state='partial', result={'files': [{'document_id': 'doc_fake', 'state': 'failed'}]})
    def retry(number):
        return jobs.enqueue('A', 'As', 'personal_trip', trips['A'], 'documents',
            {'resume_from': original['id'], 'accepted': [{'document_id': 'doc_fake'}]}, 1,
            f'new-retry-click-{number}', request_fingerprint='same-failed-unit')
    with ThreadPoolExecutor(max_workers=6) as pool:
        children = list(pool.map(retry, range(12)))
    assert len({child['id'] for child in children}) == 1
    child = claim_required(jobs,'worker')
    jobs.finish(child['id'], child['fencing_token'])
    assert retry(20)['id'] == child['id']
    assert retry(20)['submission']['resume_from'] == original['id']
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 2
        assert con.execute('SELECT COUNT(*) FROM idempotency_keys').fetchone()[0] == 14


def test_pending_documents_reuse_work_across_keys_and_mixed_batches_only_add_new_units(work):
    db, _, trips, jobs = work
    args = ('A', 'As', 'personal_trip', trips['A'], 'documents')
    original = jobs.enqueue(*args, {'accepted': [{'document_id': 'same-doc', 'filename': 'first.txt'}]}, 1,
                            'first-key', request_fingerprint='original-client-hash')
    reused = jobs.enqueue(*args, {'accepted': [{'document_id': 'same-doc', 'filename': 'another.txt'}]}, 1,
                          'different-key', request_fingerprint='second-client-hash')
    assert reused['id'] == original['id']
    mixed = jobs.enqueue(*args, {'accepted': [
        {'document_id': 'same-doc', 'filename': 'another.txt'},
        {'document_id': 'fresh-doc', 'filename': 'fresh.txt'}]}, 1,
        'mixed-client-key', request_fingerprint='mixed-original-hash')
    assert mixed['id'] != original['id']
    assert mixed['submission']['accepted'] == [{'document_id': 'fresh-doc', 'filename': 'fresh.txt'}]
    assert mixed['submission']['duplicates'] == [{'document_id': 'same-doc', 'filename': 'another.txt',
                                                'job_id': original['id'], 'state': 'processing'}]
    assert jobs.lookup(*args, 'mixed-client-key', 'mixed-original-hash')['id'] == mixed['id']
    expect_code('IDEMPOTENCY_CONFLICT', lambda: jobs.lookup(*args, 'different-key', 'changed-hash'))
    worker = jobs.claim('worker')
    assert worker['id'] == original['id']
    jobs.finish(worker['id'], worker['fencing_token'])
    # An explicit later re-extraction has its own intent and may legitimately cost money.
    explicit = jobs.enqueue(*args, {'accepted': [{'document_id': 'same-doc', 'filename': 'first.txt'}]}, 1,
                            'explicit-reprocess', request_fingerprint='explicit-hash')
    assert explicit['id'] != original['id']
    with db.connect() as con:
        assert con.execute('SELECT payload_hash FROM jobs WHERE id=?', (mixed['id'],)).fetchone()[0] == 'mixed-original-hash'


def test_replay_is_monotonic_bounded_and_does_not_execute_jobs(work):
    _, _, _, jobs = work
    first = enqueue(work)
    claimed = jobs.claim('one')
    jobs.progress(first['id'], claimed['fencing_token'], 'extracting', done=1, total=8)
    jobs.progress(first['id'], claimed['fencing_token'], 'extracting', done=2, total=8)
    jobs.finish(first['id'], claimed['fencing_token'], state='partial', result={'files': [{'state': 'succeeded'}, {'state': 'failed'}]})
    events = jobs.events(first['id'], 'A', 'As', after=2, limit=2)
    assert [item['id'] for item in events] == [3, 4]
    assert jobs.events(first['id'], 'A', 'As', after=events[-1]['id'])[0]['event'] == 'partial_result'
    assert jobs.get(first['id'], 'A', 'As')['attempt'] == 1
    expect_code('INVALID_EVENT_ID', lambda: jobs.events(first['id'], 'A', 'As', after=-1))


def test_running_public_snapshot_exposes_completed_file_counts_without_private_refs(work):
    _, _, _, jobs = work
    job = enqueue(work)
    worker = jobs.claim('worker')
    jobs.checkpoint(job['id'], worker['fencing_token'], {'files': [
        {'document_id': 'doc_fake', 'filename': 'same.txt', 'state': 'succeeded', 'bookings_count': 8,
         'extracted_ref': 'private-artifact', 'provider_response': 'private-facts'}]},
        stage='document_complete', done=1, total=2)
    snapshot = jobs.get(job['id'], 'A', 'As')
    assert snapshot['state'] == 'running'
    assert snapshot['result']['files'] == [
        {'document_id': 'doc_fake', 'filename': 'same.txt', 'state': 'succeeded', 'bookings_count': 8}]
    assert 'private-artifact' not in json.dumps(snapshot) and 'private-facts' not in json.dumps(snapshot)


def test_version_check_is_explicit_so_handler_can_rebase_current_corrections(work):
    db, _, trips, jobs = work
    job = enqueue(work)
    with db.connect() as con:
        con.execute('UPDATE trips SET version=version+1 WHERE id=?', (trips['A'],))
    claimed = jobs.claim('one')
    jobs.guard(job['id'], claimed['fencing_token'])
    expect_code('VERSION_CONFLICT', lambda: jobs.guard(job['id'], claimed['fencing_token'], require_version=True))


def test_deadline_attempt_limits_and_checkpoint_survive_retry(work):
    _, _, _, jobs = work
    clock = [datetime.now(timezone.utc)]
    jobs.clock = lambda: clock[0]
    job = enqueue(work, max_attempts=2)
    first = jobs.claim('worker')
    jobs.checkpoint(job['id'], first['fencing_token'], {'extract_ref': 'safe_reference'})
    jobs.retry(job['id'], first['fencing_token'], error_code='PROVIDER_429', delay_seconds=.1)
    clock[0] += timedelta(seconds=.15)
    second = jobs.claim('worker')
    assert second['checkpoint']['extract_ref'] == 'safe_reference'
    jobs.retry(job['id'], second['fencing_token'], error_code='PROVIDER_429')
    assert jobs.get(job['id'], 'A', 'As')['state'] == 'failed'
    timed = enqueue(work, 'timed-intent', deadline_seconds=1)
    clock[0] += timedelta(seconds=2)
    assert jobs.claim('worker') is None
    assert jobs.get(timed['id'], 'A', 'As')['error_code'] == 'JOB_DEADLINE'


def test_dispatcher_keeps_event_loop_responsive_and_two_dispatchers_do_not_overlap(work):
    db, _, _, jobs = work
    enqueue(work)
    enqueue(work, 'second-intent')
    activity = {'active': 0, 'max': 0, 'calls': 0}
    lock = threading.Lock()
    def handler(job, context):
        with lock:
            activity['active'] += 1
            activity['max'] = max(activity['max'], activity['active'])
        try:
            context.checkpoint({'artifact_ref': 'fake'}, stage='provider', done=0, total=1)
            time.sleep(.12)
            context.guard()
            with lock:activity['calls'] += 1
            return {'done': True}
        finally:
            # A fenced attempt has exited too. Counting it forever creates a
            # false overlap when a later valid attempt starts after it returns.
            with lock:activity['active'] -= 1
    async def scenario():
        first = Dispatcher(jobs, handler, poll_seconds=.01, heartbeat_seconds=.05)
        second = Dispatcher(Jobs(db, lease_seconds=.3), handler, poll_seconds=.01, heartbeat_seconds=.05)
        await first.start()
        await second.start()
        ticks = 0
        end = asyncio.get_running_loop().time()+3
        while activity['calls'] < 2 and asyncio.get_running_loop().time() < end:
            ticks += 1
            await asyncio.sleep(.01)
        await first.stop()
        await second.stop()
        return ticks
    ticks = asyncio.run(scenario())
    assert ticks >= 10 and activity == {'active': 0, 'max': 1, 'calls': 2}, {'activity':activity,**job_snapshot(jobs)}


def test_wall_clock_rollback_keeps_future_job_queued_without_weakening_lease(work):
    db,_,_,jobs=work
    clock=[datetime.now(timezone.utc)]
    jobs.clock=lambda:clock[0]
    submitted=enqueue(work)
    created=clock[0]
    clock[0]-=timedelta(seconds=2)
    assert jobs.claim('clock-regression-worker') is None
    status=job_snapshot(jobs)
    assert status['controls_mode']=='normal'
    assert status['jobs'][0]['available_delta_seconds']==2
    assert status['jobs'][0]['state']=='queued' and status['jobs'][0]['attempt']==0
    assert status['jobs'][0]['scope_error'] is None
    assert status['dispatcher']['lease_expires_at']>jobs.now()
    clock[0]=created
    claimed=jobs.claim('clock-regression-worker')
    assert claimed['id']==submitted['id'] and claimed['attempt']==1


def test_real_sigkill_resumes_checkpoint_without_repeating_completed_provider(work, tmp_path):
    db, _, _, jobs = work
    queued = enqueue(work)
    marker = tmp_path/'checkpoint-ready'
    script = r'''
import asyncio, sys, time
from pathlib import Path
from src.foundation.db import Database
from src.reliability.jobs import Jobs
from src.reliability.dispatcher import Dispatcher
db=Database(sys.argv[1])
def handler(job, context):
    with db.connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS synthetic_provider_calls(id INTEGER PRIMARY KEY)')
        con.execute('INSERT INTO synthetic_provider_calls DEFAULT VALUES')
    context.checkpoint({'extracted_ref':'existing-artifact'},stage='extracted',done=1,total=2)
    Path(sys.argv[2]).write_text('checkpoint-ready')
    time.sleep(30)
async def main():
    dispatcher=Dispatcher(Jobs(db,lease_seconds=.3),handler,poll_seconds=.01,heartbeat_seconds=.05)
    await dispatcher.start()
    await asyncio.sleep(60)
asyncio.run(main())
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(db.path), str(marker)],
                             cwd=Path(__file__).parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        end = time.monotonic()+10
        while not marker.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(.02)
        assert marker.exists(), child.stderr.read().decode() if child.poll() is not None else 'checkpoint timeout'
        child.kill()
        child.wait(timeout=5)
        assert child.returncode < 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stderr.close()
    time.sleep(.4)
    recovered = jobs.claim('replacement-process')
    assert recovered['id'] == queued['id'] and recovered['attempt'] == 2
    assert recovered['checkpoint']['extracted_ref'] == 'existing-artifact'
    # Resume the remaining activation stage; no second paid extraction occurs.
    jobs.checkpoint(recovered['id'], recovered['fencing_token'], {'activated_ref': 'result'}, stage='activated', done=2, total=2)
    jobs.finish(recovered['id'], recovered['fencing_token'])
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM synthetic_provider_calls').fetchone()[0] == 1
    assert jobs.get(queued['id'], 'A', 'As')['state'] == 'succeeded'
