"""Apify-shaped remote lifecycle tests; every provider call is a local fake.

Tests exercise paid-unit reservation and vendor raw-data deletion outbox without
calling Apify or representing synthetic review records as live observations.
"""
import copy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
import threading
import time

import pytest

from tests.test_foundation_api import service,_job
from tests.test_review_integration import reviews,submit,rows
from src.providers.fake_reviews import FakeReviewCollectionProvider
from src.providers.reviews import ReviewProviderError
from src.reliability.budget import BudgetPolicy


class RemoteActorFake(FakeReviewCollectionProvider):
    name='apify'
    configured=True
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.start_requests=[]
        self.before_start=None
        self.fail_delete=False
    def start(self,request):
        self.start_requests.append(request)
        if self.before_start:self.before_start(request)
        return super().start(request)
    def delete_dataset(self,remote,request):
        if self.fail_delete:
            self.calls.append(('delete_dataset_failed',remote['dataset_id']))
            raise ReviewProviderError('provider_outcome_unknown')
        return super().delete_dataset(remote,request)


@pytest.fixture
def remote_reviews(reviews):
    provider=RemoteActorFake(records=rows())
    reviews.app.state.reviews.provider=provider
    config=copy.deepcopy(reviews.app.state.budget.policy.config)
    config['prices']['apify/review_start']={'currency':'USD','rates_per_million':{'usd_micros':'1'},'max_units':{'usd_micros':3000000}}
    for operation in ('review_poll','review_page','review_abort','review_delete_dataset','review_delete_run'):
        config['prices']['apify/'+operation]={'currency':'USD','rates_per_million':{'requests':'1'},'max_units':{'requests':1}}
    config['limits']['USD']={key:3000000 for key in config['limits']['USD']}
    reviews.app.state.budget.policy=BudgetPolicy(config)
    policy=copy.deepcopy(reviews.policy_body)
    policy.update(provider='apify',version='synthetic-apify-remote-v1',remote_raw_ttl_seconds=3600)
    policy['rights']['raw_store']=True
    response=reviews.client.post('/api/v2/admin/review-policies',json=policy)
    assert response.status_code==201,response.text
    reviews.policy=response.json()
    response=reviews.client.post('/api/v2/admin/review-places',json={'provider':'apify',
        'external_place_id':'ChIJ'+'a'*23,'city':'tokyo','name':'Synthetic remote actor restaurant',
        'address':'Synthetic remote Tokyo 1-2-3','source_url':'https://www.google.com/maps/place/synthetic-remote',
        'rating':4.5,'total_rating_count':1200})
    assert response.status_code==201,response.text
    response=reviews.client.patch('/api/v2/admin/review-places/'+response.json()['id'],json={
        'expected_version':1,'status':'verified','evidence':'Synthetic verified branch and address only'})
    assert response.status_code==200,response.text
    reviews.place=response.json()
    return reviews


def remote_submit(reviews,**changes):
    return submit(reviews,max_total_charge_usd='0.01',**changes)


def terminal_from_sql(reviews,job_id):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with reviews.app.state.db.connect() as con:
            state=con.execute('SELECT state FROM jobs WHERE id=?',(job_id,)).fetchone()[0]
        if state in ('failed','cancelled','succeeded','partial'):return state
        time.sleep(.01)
    raise AssertionError('Timed out waiting for synthetic remote lifecycle')


def test_remote_run_reserves_abort_deletions_and_capped_requests_before_start(remote_reviews):
    reviews=remote_reviews; snapshots=[]
    def snapshot(request):
        with reviews.app.state.db.connect() as con:
            snapshots.append({r['operation']:dict(r) for r in con.execute('SELECT * FROM usage_reservations WHERE job_id=?',(request.job_id,))})
    reviews.app.state.reviews.provider.before_start=snapshot
    response=remote_submit(reviews)
    assert response.status_code==202,response.text
    receipt=response.json()
    assert _job(reviews.client,receipt)['state']=='succeeded'
    reserved=snapshots[0]
    assert {'review_abort','review_delete_dataset','review_delete_run','review_start'}<=reserved.keys()
    assert all(reserved[name]['state']=='reserved' for name in ('review_abort','review_delete_dataset','review_delete_run'))
    assert reserved['review_start']['state']=='sent'
    sent_cap=Decimal(reviews.app.state.reviews.provider.start_requests[0].max_total_charge_usd)
    # 150 polls +20pages*2 attempts + abort + two deletions, all priced one micro.
    assert sent_cap==Decimal('0.01')-Decimal(196)/1000000
    assert reserved['review_start']['estimated_cost_micros']==int(sent_cap*1000000)
    with reviews.app.state.db.connect() as con:
        cleanup=con.execute('SELECT * FROM review_remote_cleanup WHERE run_id=?',(receipt['run_id'],)).fetchone()
        assert cleanup['state']=='succeeded' and cleanup['dataset_deleted']==cleanup['run_deleted']==1
        assert cleanup['remote_json']=='{}'
        dump='\n'.join(con.iterdump())
        reservations=list(con.execute('SELECT * FROM usage_reservations WHERE job_id=?',(receipt['job_id'],)))
    assert 'PRIVATE_REVIEW_BODY' not in dump and 'AUTHOR_PRIVATE' not in dump
    assert sum(r['estimated_cost_micros'] if r['state'] not in ('settled','released') else r['actual_cost_micros'] or 0 for r in reservations)<=10000
    assert next(r for r in reservations if r['operation']=='review_start')['state']=='pending_reconciliation'
    assert [c[0] for c in reviews.app.state.reviews.provider.calls][-2:]==['delete_dataset','delete_run']
    after=len(reviews.app.state.reviews.provider.calls)
    reviews.app.state.reviews.cleanup_remote(receipt['run_id'])
    assert len(reviews.app.state.reviews.provider.calls)==after


@pytest.mark.parametrize('withdraw',['revoke','expire'])
def test_late_remote_start_preserves_cleanup_outbox_after_request_purge(remote_reviews,withdraw):
    reviews=remote_reviews; started,release=threading.Event(),threading.Event()
    def wait_start(request):
        started.set();assert release.wait(5)
    reviews.app.state.reviews.provider.before_start=wait_start
    response=remote_submit(reviews)
    assert response.status_code==202,response.text
    receipt=response.json()
    assert started.wait(3)
    try:
        if withdraw=='revoke':
            assert reviews.client.delete('/api/v2/admin/review-policies/'+reviews.policy['id']).status_code==200
        else:
            with reviews.app.state.db.connect() as con:
                con.execute("UPDATE review_collection_runs SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(receipt['run_id'],))
            reviews.app.state.reviews.purge()
        with reviews.app.state.db.connect() as con:
            assert con.execute('SELECT request_json FROM review_collection_runs WHERE id=?',(receipt['run_id'],)).fetchone()[0]=='{}'
    finally:release.set()
    assert terminal_from_sql(reviews,receipt['job_id']) in ('cancelled','failed')
    # The response may arrive after permission withdrawal, but its deletion-only
    # ID is retained without activating bookings, language evidence, or raw text.
    with reviews.app.state.db.connect() as con:
        pending=con.execute('SELECT * FROM review_remote_cleanup WHERE run_id=?',(receipt['run_id'],)).fetchone()
        assert pending is not None
    reviews.app.state.reviews.cleanup_remote(receipt['run_id'])
    with reviews.app.state.db.connect() as con:
        cleanup=con.execute('SELECT * FROM review_remote_cleanup WHERE run_id=?',(receipt['run_id'],)).fetchone()
        assert cleanup['state']=='succeeded',dict(cleanup)
        assert cleanup['remote_json']=='{}'
        assert con.execute('SELECT COUNT(*) FROM review_aggregates').fetchone()[0]==0
        assert 'PRIVATE_REVIEW_BODY' not in '\n'.join(con.iterdump())
    assert [c[0] for c in reviews.app.state.reviews.provider.calls][-2:]==['delete_dataset','delete_run']


def test_remote_delete_failure_stays_visible_and_never_claims_erasure(remote_reviews):
    reviews=remote_reviews
    reviews.app.state.reviews.provider.fail_delete=True
    response=remote_submit(reviews)
    assert response.status_code==202,response.text
    receipt=response.json()
    assert _job(reviews.client,receipt)['state']=='succeeded'
    result=reviews.client.get(receipt['status_url']).json()
    assert result['remote_cleanup']['state']=='failed'
    assert not result['remote_cleanup']['dataset_deleted'] and not result['remote_cleanup']['run_deleted']
    with reviews.app.state.db.connect() as con:
        cleanup=con.execute('SELECT * FROM review_remote_cleanup').fetchone()
        assert json.loads(cleanup['remote_json'])['dataset_id']
        state=con.execute("SELECT state FROM usage_reservations WHERE operation='review_delete_dataset'").fetchone()[0]
        assert state=='unknown'
    assert not any(c[0]=='delete_run' for c in reviews.app.state.reviews.provider.calls)


def test_remote_storage_rights_and_ttl_required_before_any_call(remote_reviews):
    reviews=remote_reviews
    with reviews.app.state.db.connect() as con:
        con.execute('UPDATE provider_policies SET remote_raw_ttl_seconds=0 WHERE id=?',(reviews.policy['id'],))
    response=remote_submit(reviews)
    assert response.status_code==409 and response.json()['error']['code']=='REMOTE_RETENTION_UNREVIEWED'
    assert reviews.app.state.reviews.provider.calls==[]
    with reviews.app.state.db.connect() as con:
        con.execute('UPDATE provider_policies SET remote_raw_ttl_seconds=3600,rights_json=? WHERE id=?',
            (json.dumps({**reviews.policy['rights'],'raw_store':False}),reviews.policy['id']))
    assert remote_submit(reviews).status_code==409
    assert reviews.app.state.reviews.provider.calls==[]


def test_call_fee_ceiling_rejects_actor_start_when_run_cap_too_small(remote_reviews):
    reviews=remote_reviews
    response=submit(reviews,max_total_charge_usd='0.0001')
    assert response.status_code==202,response.text
    assert _job(reviews.client,response.json())['state']=='failed'
    assert reviews.app.state.reviews.provider.start_requests==[]
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM review_remote_cleanup').fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM usage_reservations WHERE state IN ('sent','unknown','pending_reconciliation')").fetchone()[0]==0
