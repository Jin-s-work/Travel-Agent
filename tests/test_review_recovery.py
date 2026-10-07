"""SIGKILL/restart checks against the actual review service and SQLite receipts.

All remote calls are synthetic and leave a separately fsynced call counter. No
HTTP client, API credential, Chroma, or language model is involved in this suite.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.foundation.db import Database
from src.foundation.repository import DomainError, Repository
from src.providers.fake_reviews import FakeReviewCollectionProvider
from src.reliability.budget import Budget, BudgetPolicy
from src.reliability.dispatcher import JobContext
from src.reliability.jobs import Jobs
from src.reliability.providers import ProviderGateway
from src.research.service import ReviewService, RIGHTS
from tests.job_diagnostics import claim_required


class SyntheticDetector:
    version = 'synthetic-recovery-detector-v1'

    def detect(self, text):
        return {'language': 'ja', 'detector_version': self.version,
                'model_confidence': 1.0}


class CountedProvider(FakeReviewCollectionProvider):
    def __init__(self, path):
        super().__init__(page_size=50)
        self.path = Path(path)

    def count(self, label):
        with (self.path / 'synthetic-calls.txt').open('a') as output:
            output.write(label + '\n')
            output.flush()
            os.fsync(output.fileno())

    def start(self, request):
        self.count('start')
        return super().start(request)

    def fetch_page(self, remote, cursor, request):
        self.count('page:' + str(cursor))
        page = super().fetch_page(remote, cursor, request)
        # These values must never reach receipts, checkpoints, errors or backups.
        for row in page.records:
            row['original_text'] = 'PRIVATE_RAW_REVIEW_秘密_복구 ' + row['original_text']
            row['translated_text'] = 'PRIVATE_TRANSLATION_RECOVERY'
            row['reviewerName'] = 'PRIVATE_AUTHOR_RECOVERY'
        return page


def construct(path, fault=None):
    path = Path(path)
    db = Database(path / 'review-recovery.sqlite3')
    repo = Repository(db)
    jobs = Jobs(db)
    config = BudgetPolicy.for_tests(rate='1', limit=1000).config
    for operation in ('review_start', 'review_poll', 'review_page', 'review_abort',
                      'review_delete_dataset', 'review_delete_run'):
        config['prices']['fake/' + operation] = {
            'currency': 'USD', 'rates_per_million': {'calls': '1'},
            'max_units': {'calls': 1}}
    budget = Budget(db, BudgetPolicy(config))
    gateway = ProviderGateway(budget, path / 'private-artifacts')
    responses = []

    def inject(point, run_id):
        if point == 'review_response_received':
            responses.append(run_id)
            if fault == 'page_response_before_receipt' and len(responses) == 2:
                os.kill(os.getpid(), signal.SIGKILL)

    review = ReviewService(db, repo, jobs, gateway, provider=CountedProvider(path),
                           detector=SyntheticDetector(), fault_hook=inject)
    return SimpleNamespace(db=db, repo=repo, jobs=jobs, budget=budget,
                           review=review, path=path, fault=fault)


def seed(env, *, max_total_charge_usd='0.001'):
    now = datetime.now(timezone.utc)
    actor = SimpleNamespace(id='admin', session_id='admin-session')
    with env.db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (actor.id, 'admin@example.invalid', 'fake', actor.id, 'admin', now.isoformat(), now.isoformat()))
        con.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                    (actor.session_id, actor.id, 'synthetic-hash', 'synthetic-csrf',
                     (now + timedelta(days=1)).isoformat(), 0, now.isoformat()))
    env.review.set_controls(actor, {'expected_version': 1, 'research_enabled': True,
                                   'production_enabled': False})
    policy = env.review.add_policy(actor, {
        'provider': 'fake', 'version': 'synthetic-recovery-policy-v1',
        'purpose': 'Synthetic recovery test only', 'evidence': ['Synthetic fixtures'],
        'rights': {key: key not in ('raw_store', 'llm') for key in RIGHTS},
        'reviewed_at': now.isoformat(), 'expires_at': (now + timedelta(days=1)).isoformat(),
        'aggregate_ttl_seconds': 3600, 'id_ttl_seconds': 3600})
    place = env.review.add_place(actor, {
        'provider': 'fake', 'external_place_id': 'synthetic-place', 'city': 'tokyo',
        'name': 'Synthetic place', 'address': 'Synthetic Tokyo address',
        'source_url': 'https://www.google.com/maps/place/synthetic',
        'rating': 4.5, 'total_rating_count': 2000})
    place = env.review.verify_place(actor, place['id'], {
        'expected_version': 1, 'status': 'verified', 'evidence': 'Synthetic identity'})
    submitted = env.review.submit(actor, {
        'place_id': place['id'], 'policy_id': policy['id'], 'max_review_records': 100,
        'max_pages': 20, 'max_elapsed_seconds': 300, 'max_total_charge_usd': max_total_charge_usd},
        'synthetic-recovery-intent')
    return actor, submitted


class KillingContext(JobContext):
    def __init__(self, env, job, owner):
        super().__init__(env.jobs, job, owner)
        self.fault = env.fault

    def checkpoint(self, data, **kwargs):
        first_page = data.get('collection', {}).get('pages') == 1
        if ((self.fault == 'start_receipt_before_checkpoint' and 'remote' in data)
                or (self.fault == 'page_receipt_before_checkpoint' and first_page)):
            os.kill(os.getpid(), signal.SIGKILL)
        result = super().checkpoint(data, **kwargs)
        if self.fault == 'page_checkpoint' and first_page:
            os.kill(os.getpid(), signal.SIGKILL)
        return result


def run_one(env, owner='recovery-worker'):
    job = claim_required(env.jobs,owner)
    context = KillingContext(env, job, owner)
    result = env.review.execute(job, context)
    if env.fault == 'aggregate_activation':
        os.kill(os.getpid(), signal.SIGKILL)
    env.jobs.finish(job['id'], job['fencing_token'], state=result['state'], result=result['result'])
    return result


def kill_child(path, point):
    script = ('import sys\n'
              'from tests.test_review_recovery import construct,run_one\n'
              'run_one(construct(sys.argv[1],sys.argv[2]), "killed-review-worker")\n')
    child_env = {**os.environ, 'OPENAI_API_KEY': 'test', 'APIFY_TOKEN': '', 'APIFY_API_TOKEN': ''}
    result = subprocess.run([sys.executable, '-c', script, str(path), point],
                            capture_output=True, text=True, timeout=30, env=child_env)
    assert result.returncode == -signal.SIGKILL, result.stderr


def expire(env):
    with env.db.connect() as con:
        con.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE state='running'")
        con.execute("UPDATE dispatcher_leases SET lease_expires_at='2000-01-01T00:00:00+00:00'")


def assert_minimized(env):
    with env.db.connect() as con:
        dump = '\n'.join(con.iterdump())
    for forbidden in ('PRIVATE_RAW_REVIEW', 'PRIVATE_TRANSLATION_RECOVERY', 'PRIVATE_AUTHOR_RECOVERY'):
        assert forbidden not in dump
        assert not any(forbidden in path.read_text() for path in env.path.rglob('*.json'))


@pytest.mark.parametrize('point', [
    'start_receipt_before_checkpoint', 'page_receipt_before_checkpoint',
    'page_checkpoint', 'aggregate_activation',
])
def test_sigkill_receipt_and_checkpoint_replay_never_repeats_remote_call(tmp_path, point):
    initial = construct(tmp_path)
    _, submitted = seed(initial)
    kill_child(tmp_path, point)
    with initial.db.connect() as con:
        assert con.execute('SELECT state FROM jobs WHERE id=?', (submitted['job_id'],)).fetchone()[0] == 'running'
        old_aggregate = con.execute('SELECT * FROM review_aggregates').fetchone()
    assert_minimized(initial)
    restarted = construct(tmp_path)
    expire(restarted)
    result = run_one(restarted)
    assert result['state'] == 'succeeded'
    assert (tmp_path / 'synthetic-calls.txt').read_text().splitlines() == ['start', 'page:None', 'page:page:1']
    with restarted.db.connect() as con:
        assert con.execute('SELECT attempt FROM jobs WHERE id=?', (submitted['job_id'],)).fetchone()[0] == 2
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM usage_reservations WHERE state='settled'").fetchone()[0] == 3
        assert con.execute('SELECT COUNT(*) FROM review_aggregates').fetchone()[0] == 1
        aggregate = con.execute('SELECT * FROM review_aggregates').fetchone()
        if old_aggregate:
            assert aggregate['id'] == old_aggregate['id']
            assert aggregate['computed_at'] == old_aggregate['computed_at']
            assert aggregate['expires_at'] == old_aggregate['expires_at']
    assert_minimized(restarted)


def test_sigkill_after_remote_response_without_receipt_stays_unknown(tmp_path):
    initial = construct(tmp_path)
    _, submitted = seed(initial)
    kill_child(tmp_path, 'page_response_before_receipt')
    restarted = construct(tmp_path)
    expire(restarted)
    with pytest.raises(DomainError) as error:
        run_one(restarted)
    assert error.value.code == 'CALL_OUTCOME_UNKNOWN'
    assert (tmp_path / 'synthetic-calls.txt').read_text().splitlines() == ['start', 'page:None']
    with restarted.db.connect() as con:
        calls = [dict(row) for row in con.execute('SELECT operation,state,estimated_cost_micros FROM usage_reservations ORDER BY created_at')]
        assert [(row['operation'], row['state']) for row in calls] == [('review_start', 'settled'), ('review_page', 'unknown')]
        assert calls[-1]['estimated_cost_micros'] == 1
        assert con.execute('SELECT COUNT(*) FROM review_aggregates').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0] == 1
        assert con.execute('SELECT active_aggregate_id FROM place_identities').fetchone()[0] is None
    assert_minimized(restarted)


def test_restart_with_changed_detector_does_not_mix_classification_versions(tmp_path):
    initial = construct(tmp_path)
    seed(initial)
    kill_child(tmp_path, 'page_checkpoint')
    restarted = construct(tmp_path)
    restarted.review.detector.version = 'synthetic-recovery-detector-v2'
    expire(restarted)
    with pytest.raises(DomainError) as error:
        run_one(restarted)
    assert error.value.code == 'DETECTOR_VERSION_CHANGED'
    assert (tmp_path / 'synthetic-calls.txt').read_text().splitlines() == ['start', 'page:None']
    with restarted.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM review_aggregates').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0] == 2
    assert_minimized(restarted)


def test_old_worker_is_fenced_before_call_and_before_result_persistence(tmp_path):
    env = construct(tmp_path)
    seed(env)
    job = env.jobs.claim('old-worker')
    context = JobContext(env.jobs, job, 'old-worker')
    expire(env)
    replacement = env.jobs.claim('new-worker')
    assert replacement['fencing_token'] > job['fencing_token']
    with pytest.raises(DomainError) as error:
        env.review.execute(job, context)
    assert error.value.code == 'LEASE_LOST'
    assert not (tmp_path / 'synthetic-calls.txt').exists()
    # Expire while a paid page is returning: cost remains settled, data cannot
    # become an observation receipt or active aggregate under the old fence.
    new_context = JobContext(env.jobs, replacement, 'new-worker')
    original = env.review.provider.fetch_page

    def late_page(*args):
        value = original(*args)
        expire(env)
        return value

    env.review.provider.fetch_page = late_page
    with pytest.raises(DomainError) as error:
        env.review.execute(replacement, new_context)
    assert error.value.code == 'LEASE_LOST'
    with env.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM review_aggregates').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0] == 1
        assert con.execute("SELECT state FROM usage_reservations WHERE operation='review_page'").fetchone()[0] == 'settled'
    assert_minimized(env)


def test_guard_rejected_after_reservation_releases_provably_unsent_cost(tmp_path):
    from src.reliability.budget import CallContext
    from src.research.calls import ResearchCalls
    from src.research.service import SCOPE
    env = construct(tmp_path)
    seed(env)
    job = env.jobs.claim('guard-worker')
    context = JobContext(env.jobs, job, 'guard-worker')
    with env.db.connect() as con:
        run = dict(con.execute('SELECT * FROM review_collection_runs').fetchone())
    guard_calls = []

    def rejected_guard(con=None):
        guard_calls.append(True)
        if len(guard_calls) == 2:
            raise DomainError('JOB_CANCELLED', 'Synthetic cancellation', 409)
        return context.guard(con=con)

    calls = ResearchCalls(env.review, run, CallContext(
        owner_id='admin', actor_id='admin', scope_kind='admin_research',
        scope_id=SCOPE, job_id=job['id']), rejected_guard)
    with pytest.raises(DomainError) as error:
        calls.run_call('review_start', 'start', 'synthetic-request-hash',
                       lambda: pytest.fail('Cancelled request must not be sent'))
    assert error.value.code == 'JOB_CANCELLED'
    with env.db.connect() as con:
        assert con.execute('SELECT state FROM usage_reservations').fetchone()[0] == 'released'
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0] == 0


def test_run_charge_cap_stops_next_page_before_invocation(tmp_path):
    env = construct(tmp_path)
    # Two synthetic micro-USD cover start and page one, but never page two.
    seed(env, max_total_charge_usd='0.000002')
    result = run_one(env)
    assert result['state'] == 'partial'
    assert result['result']['stop_reason'] == 'budget_cap'
    assert (tmp_path / 'synthetic-calls.txt').read_text().splitlines() == ['start', 'page:None']
    with env.db.connect() as con:
        assert con.execute("SELECT SUM(actual_cost_micros) FROM usage_reservations WHERE state='settled'").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM usage_reservations WHERE state='released'").fetchone()[0] == 1
        aggregate = json.loads(con.execute('SELECT coverage_json FROM review_aggregates').fetchone()[0])
        assert aggregate['unique_count'] == 50
        assert aggregate['partial'] is True
        assert con.execute('SELECT active_aggregate_id FROM place_identities').fetchone()[0] is None
    assert_minimized(env)
