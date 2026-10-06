"""Private-beta HTTP/SQL review workflow with synthetic providers only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import copy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.test_foundation_api import service,_trip,_job
from src.providers.fake_reviews import FakeReviewCollectionProvider
from src.providers.reviews import ProviderPage,ReviewProviderError
from src.reliability.budget import BudgetPolicy
from src.research.service import RIGHTS


class FixtureDetector:
    version='synthetic-detector-v1'
    def detect(self,text):
        return {'language':text.split(' ')[0],'detector_version':self.version,'confidence':1.0}


def rows(count=200):
    end=datetime.now(timezone.utc)-timedelta(minutes=1)
    output=[]
    for index in range(count):
        language='ja' if index<150 else 'ko' if index<154 else 'en'
        output.append({'provider_review_id':f'synthetic-r{index}',
            'original_text':f'{language} PRIVATE_REVIEW_BODY synthetic restaurant text {index}' if index<190 else None,
            'translated_text':'TRANSLATION_PRIVATE synthetic review translation' if index>=190 else None,
            'original_separation_verified':True,'text_presence':'present',
            'published_at':(end-timedelta(hours=index)).isoformat(),'date_precision':'exact',
            'rating':5,'rating_scale':5,'name':'AUTHOR_PRIVATE','reviewerPhotoUrl':'PHOTO_PRIVATE'})
    return output


@pytest.fixture
def reviews(service):
    service.app.state.reviews.provider=FakeReviewCollectionProvider(records=rows())
    service.app.state.reviews.detector=FixtureDetector()
    config=copy.deepcopy(service.app.state.budget.policy.config)
    for operation in ('review_start','review_poll','review_page','review_abort','review_delete_dataset','review_delete_run'):
        config['prices']['fake/'+operation]={'currency':'USD','rates_per_million':{'calls':'0'},'max_units':{'calls':1}}
    service.app.state.budget.policy=BudgetPolicy(config)
    admin=service.login('review-admin')
    with service.app.state.db.connect() as con:
        con.execute("UPDATE users SET role='admin' WHERE id=?",(admin.user['id'],))
    client=admin.client
    control=client.patch('/api/v2/admin/review-controls',json={'expected_version':1,'research_enabled':True,'production_enabled':False})
    assert control.status_code==200,control.text
    now=datetime.now(timezone.utc)
    policy_body={'provider':'fake','version':'synthetic-v1','purpose':'Synthetic integration fixture only',
        'evidence':['Synthetic test-generated reviews; no live rights assertion'],
        'rights':{k:k not in ('raw_store','llm') for k in RIGHTS},'reviewed_at':now.isoformat(),
        'expires_at':(now+timedelta(days=1)).isoformat(),'aggregate_ttl_seconds':3600,'id_ttl_seconds':3600}
    policy=client.post('/api/v2/admin/review-policies',json=policy_body)
    assert policy.status_code==201,policy.text
    place=client.post('/api/v2/admin/review-places',json={'provider':'fake','external_place_id':'synthetic-place-1',
        'city':'tokyo','name':'Synthetic Tokyo restaurant','address':'Synthetic Tokyo 1-2-3',
        'source_url':'https://www.google.com/maps/place/synthetic','rating':4.5,'total_rating_count':1200})
    assert place.status_code==201,place.text
    place=client.patch('/api/v2/admin/review-places/'+place.json()['id'],json={'expected_version':1,
        'status':'verified','evidence':'Synthetic name, branch and address fixture verified'})
    assert place.status_code==200,place.text
    return SimpleNamespace(**vars(service),admin=admin,client=client,place=place.json(),policy=policy.json(),policy_body=policy_body)


def submit(reviews,key='review-test-key',**changes):
    body={'place_id':reviews.place['id'],'policy_id':reviews.policy['id'],'max_review_records':200,
          'max_pages':20,'max_elapsed_seconds':300,'max_total_charge_usd':'0'}
    body.update(changes)
    return reviews.client.post('/api/v2/admin/review-collection-runs',json=body,headers={'Idempotency-Key':key})


def completed(reviews,key='review-test-key'):
    response=submit(reviews,key)
    assert response.status_code==202,response.text
    job=_job(reviews.client,response.json())
    assert job['state']=='succeeded',job
    result=reviews.client.get(response.json()['status_url'])
    assert result.status_code==200,result.text
    return response.json(),result.json()


def test_synthetic_a_counts_scope_idempotency_and_no_raw_persistence(reviews,caplog):
    submitted,run=completed(reviews)
    assert run['synthetic'] is True
    assert run['counts']['text_count']==200 and run['counts']['classified_count']==190
    assert run['counts']['unknown_count']==10 and run['counts']['local_count']==150
    assert run['counts']['korean_count']==4
    metrics=run['evaluation']['metrics']
    assert metrics['classified_local_share']==pytest.approx(150/190)
    assert metrics['classified_korean_share']==pytest.approx(4/190)
    assert metrics['local_share_lower_bound']==.75 and metrics['korean_share_upper_bound']==.07
    assert run['evaluation']['decision']=='unsupported'
    assert 'CLASSIFICATION_QUALITY_UNVERIFIED' in run['evaluation']['reason_codes']
    calls=len(reviews.app.state.reviews.provider.calls)
    replay=submit(reviews)
    assert replay.status_code==202 and replay.json()['job_id']==submitted['job_id']
    assert len(reviews.app.state.reviews.provider.calls)==calls
    mismatch=submit(reviews,max_review_records=100)
    assert mismatch.status_code==409 and mismatch.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
    with reviews.app.state.db.connect() as con:
        job=con.execute('SELECT * FROM jobs WHERE id=?',(submitted['job_id'],)).fetchone()
        assert job['trip_id'] is None and job['scope_kind']=='admin_research' and job['scope_id']=='review_catalog_v1'
        dump='\n'.join(con.iterdump())
        for receipt in con.execute('SELECT safe_value_json FROM review_call_receipts'):
            for record in json.loads(receipt[0]).get('records',[]):
                assert not {'original_text','translated_text','original_text_ref','dedupe_key'} & record.keys()
    for forbidden in ('PRIVATE_REVIEW_BODY','TRANSLATION_PRIVATE','AUTHOR_PRIVATE','PHOTO_PRIVATE'):
        assert forbidden not in dump
        assert forbidden not in caplog.text
        for path in reviews.settings.database_path.parent.rglob('*.json'):
            assert forbidden not in path.read_text()


def test_concurrent_same_intent_only_one_remote_start(reviews):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:submit(reviews),range(4)))
    assert all(r.status_code==202 for r in responses),[r.text for r in responses]
    assert len({r.json()['job_id'] for r in responses})==1
    assert _job(reviews.client,responses[0].json())['state']=='succeeded'
    assert len([c for c in reviews.app.state.reviews.provider.calls if c[0]=='start'])==1


def test_admin_original_actor_scope_sse_and_consumer_trip_ownership(reviews):
    submitted,run=completed(reviews)
    member=reviews.login('member')
    otheradmin=reviews.login('other-admin')
    with reviews.app.state.db.connect() as con:
        con.execute("UPDATE users SET role='admin' WHERE id=?",(otheradmin.user['id'],))
    for client in (member.client,otheradmin.client):
        assert client.get(submitted['status_url']).status_code==404
        assert client.get('/api/v2/jobs/'+submitted['job_id']).status_code==404
        assert client.get('/api/v2/jobs/'+submitted['job_id']+'/events').status_code==404
    assert member.client.get('/api/v2/admin/review-controls').status_code==404
    t1,t2=_trip(member.client),_trip(reviews.client)
    assert member.client.post(f"/api/v2/trips/{t1['id']}/places",json={'place_id':reviews.place['id']}).status_code==201
    path=f"/api/v2/trips/{t1['id']}/places/{reviews.place['id']}/review-evidence"
    assert reviews.client.get(path).status_code==404
    assert member.client.get(f"/api/v2/trips/{t2['id']}/places/{reviews.place['id']}/review-evidence").status_code==404
    evidence=member.client.get(path).json()
    assert evidence['state']=='unavailable' and evidence['metrics'] is None
    assert 'REVIEW_PRODUCTION_DISABLED' in evidence['evaluation']['reason_codes']
    before=len(reviews.app.state.reviews.provider.calls)
    for _ in range(3):member.client.get(path)
    assert len(reviews.app.state.reviews.provider.calls)==before
    stream=reviews.client.get(submitted['events_url'])
    assert stream.status_code==200 and 'event: completed' in stream.text
    ids=[int(line[4:]) for line in stream.text.splitlines() if line.startswith('id: ')]
    resumed=reviews.client.get(submitted['events_url'],headers={'Last-Event-ID':str(ids[-2])})
    assert [int(line[4:]) for line in resumed.text.splitlines() if line.startswith('id: ')]==[ids[-1]]
    assert 'PRIVATE_REVIEW_BODY' not in stream.text


def test_partial_new_run_keeps_previous_aggregate_and_timestamp(reviews):
    original,first=completed(reviews)
    with reviews.app.state.db.connect() as con:
        active=con.execute('SELECT active_aggregate_id FROM place_identities WHERE id=?',(reviews.place['id'],)).fetchone()[0]
    provider=FakeReviewCollectionProvider(records=rows(),failures={('page:1',1):ReviewProviderError('provider_blocked')})
    reviews.app.state.reviews.provider=provider
    submitted=submit(reviews,'second-partial').json()
    assert _job(reviews.client,submitted)['state']=='partial'
    run=reviews.client.get(submitted['status_url']).json()
    assert run['coverage']['stop_reason']=='provider_blocked' and run['counts']['text_count']==50
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT active_aggregate_id FROM place_identities WHERE id=?',(reviews.place['id'],)).fetchone()[0]==active
        assert con.execute('SELECT computed_at FROM review_aggregates WHERE id=?',(active,)).fetchone()[0]==first['checked_at']


def test_policy_revocation_purges_ids_and_marks_derived_results(reviews):
    submitted,_=completed(reviews)
    response=reviews.client.delete('/api/v2/admin/review-policies/'+reviews.policy['id'])
    assert response.status_code==200,response.text
    reviews.app.state.reviews.purge();reviews.app.state.reviews.purge()
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0]==0
        assert con.execute('SELECT checkpoint_json FROM jobs WHERE id=?',(submitted['job_id'],)).fetchone()[0]=='{}'
        aggregate=con.execute('SELECT * FROM review_aggregates').fetchone()
        assert aggregate['invalidated_at'] and aggregate['counts_json']=='{}'
        assert con.execute("SELECT COUNT(*) FROM research_tombstones WHERE target_type='run'").fetchone()[0]==1
    view=reviews.client.get(submitted['status_url'])
    assert view.status_code==200 and view.json()['evaluation']['strict_pass'] is False
    assert reviews.client.get('/api/v2/jobs/'+submitted['job_id']).status_code==404
    assert reviews.client.get(submitted['events_url']).status_code==404
    assert submit(reviews,'new-after-revoke').status_code==409


def test_retention_expiry_purges_and_cannot_reactivate_on_restore(reviews):
    submitted,_=completed(reviews)
    with reviews.app.state.db.connect() as con:
        con.execute("UPDATE review_collection_runs SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(submitted['run_id'],))
    reviews.app.state.reviews.purge()
    with reviews.app.state.db.connect() as con:
        # Simulates stale rows restored while authoritative tombstones survive.
        con.execute("UPDATE review_collection_runs SET deleted_at=NULL,expires_at='2099-01-01T00:00:00+00:00' WHERE id=?",(submitted['run_id'],))
    reviews.app.state.reviews.purge()
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT deleted_at FROM review_collection_runs WHERE id=?',(submitted['run_id'],)).fetchone()[0]
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0]==0


@pytest.mark.parametrize('withdraw',['place','policy','role'])
def test_late_provider_result_cannot_reactivate_after_withdrawal(reviews,withdraw):
    started,release=threading.Event(),threading.Event()
    provider=reviews.app.state.reviews.provider
    original=provider.fetch_page
    def blocked(remote,cursor,request):
        started.set();assert release.wait(5)
        return original(remote,cursor,request)
    provider.fetch_page=blocked
    submitted=submit(reviews).json()
    assert started.wait(3)
    try:
        if withdraw=='place':
            assert reviews.client.delete('/api/v2/admin/review-places/'+reviews.place['id']).status_code==200
        elif withdraw=='policy':
            assert reviews.client.delete('/api/v2/admin/review-policies/'+reviews.policy['id']).status_code==200
        else:
            with reviews.app.state.db.connect() as con:con.execute("UPDATE users SET role='member' WHERE id=?",(reviews.admin.user['id'],))
    finally:release.set()
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with reviews.app.state.db.connect() as con:
            state=con.execute('SELECT state FROM jobs WHERE id=?',(submitted['job_id'],)).fetchone()[0]
        if state in ('cancelled','failed'):break
        time.sleep(.01)
    assert state in ('cancelled','failed')
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM review_aggregates').fetchone()[0]==0
        assert con.execute('SELECT active_aggregate_id FROM place_identities WHERE id=?',(reviews.place['id'],)).fetchone()[0] is None
        assert 'PRIVATE_REVIEW_BODY' not in '\n'.join(con.iterdump())


def test_unknown_call_outcome_never_automatically_starts_again(reviews):
    calls=[]
    def lost(request):
        calls.append(request.job_id)
        raise TimeoutError('private network payload intentionally lost')
    reviews.app.state.reviews.provider.start=lost
    submitted=submit(reviews).json()
    assert _job(reviews.client,submitted)['state']=='failed'
    assert len(calls)==1
    assert submit(reviews).json()['job_id']==submitted['job_id']
    assert len(calls)==1
    with reviews.app.state.db.connect() as con:
        ledger=con.execute('SELECT state FROM usage_reservations WHERE job_id=?',(submitted['job_id'],)).fetchone()
        assert ledger['state']=='unknown'


def test_collect_only_rights_cannot_write_review_observations(reviews):
    with reviews.app.state.db.connect() as con:
        con.execute('UPDATE provider_policies SET rights_json=? WHERE id=?',
            (json.dumps({k:k in ('access','collect') for k in RIGHTS}),reviews.policy['id']))
    response=submit(reviews)
    assert response.status_code==409
    assert reviews.app.state.reviews.provider.calls==[]
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM review_collection_runs').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0]==0


def test_production_cannot_enable_synthetic_or_korean_no_sample(reviews):
    response=reviews.client.patch('/api/v2/admin/review-controls',json={'expected_version':2,'research_enabled':True,'production_enabled':True})
    assert response.status_code==409
    now=datetime.now(timezone.utc)
    response=reviews.client.post('/api/v2/admin/review-quality-evaluations',json={'city':'tokyo',
        'detector_version':'fixture','domain':'restaurant_reviews','synthetic':False,'heldout_disjoint':True,
        'label_count':100,'original_checks':20,'original_translation_errors':0,
        'local':{'tp':100,'fp':0,'fn':0},'korean':{'tp':0,'fp':0,'fn':0},
        'report_sha256':'a'*64,'evidence_url':'https://example.test/synthetic-report-not-live',
        'expires_at':(now+timedelta(days=1)).isoformat()})
    assert response.status_code==201,response.text
    assert response.json()['passed'] is False and response.json()['korean_recall'] is None

def test_restoring_older_backup_applies_later_research_tombstones(reviews,tmp_path_factory):
    from src.foundation.cli import backup,restore
    from src.foundation.db import Database
    submitted,_=completed(reviews)
    restore_root=tmp_path_factory.mktemp('review-backup-root')
    target=restore_root/'review-backup'
    backup(reviews.settings,target)
    assert reviews.client.delete('/api/v2/admin/review-policies/'+reviews.policy['id']).status_code==200
    output=restore(target,restore_root/'review-restored',reviews.settings.database_path)
    assert output['research_deletion_history']['scrubbed_runs']==1
    restored=Database(output['database_path'])
    with restored.connect() as con:
        run=con.execute('SELECT * FROM review_collection_runs WHERE id=?',(submitted['run_id'],)).fetchone()
        assert run['deleted_at'] and run['request_json']=='{}' and run['summary_json'] is None
        assert con.execute('SELECT COUNT(*) FROM review_call_receipts').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
        assert con.execute('SELECT rights_json,status FROM provider_policies WHERE id=?',(reviews.policy['id'],)).fetchone()['status']=='revoked'

def test_blocked_place_keeps_minimal_admin_receipt_and_closes_job(reviews):
    submitted,_=completed(reviews)
    response=reviews.client.patch('/api/v2/admin/review-places/'+reviews.place['id'],json={
        'expected_version':reviews.place['version'],'status':'blocked','evidence':'Synthetic branch has moved; stop using previous evidence'})
    assert response.status_code==200,response.text
    detail=reviews.client.get(submitted['status_url'])
    assert detail.status_code==200 and detail.json()['counts'] is None
    assert reviews.client.get('/api/v2/admin/review-collection-runs').status_code==200
    assert reviews.client.get('/api/v2/jobs/'+submitted['job_id']).status_code==404


def test_invalid_url_port_and_review_retry_fail_with_actionable_errors(reviews):
    result=reviews.client.post('/api/v2/admin/review-places',json={'provider':'fake','external_place_id':'other-synthetic-id',
        'city':'tokyo','name':'Synthetic','address':'Synthetic address','source_url':'https://www.google.com:bad/maps'})
    assert result.status_code==422
    submitted,_=completed(reviews)
    result=reviews.client.post('/api/v2/jobs/'+submitted['job_id']+'/retry',headers={'Idempotency-Key':'review-retry-generic'})
    assert result.status_code==409 and result.json()['error']['code']=='REVIEW_NEW_COLLECTION_REQUIRED'

def test_offline_role_command_audits_and_revokes_existing_sessions(reviews):
    from src.foundation.cli import set_user_role
    result=set_user_role(reviews.app.state.db,reviews.admin.user['id'],'member')
    assert result['sessions_invalidated'] is True
    assert reviews.client.get('/api/v2/admin/review-controls').status_code==401
    with reviews.app.state.db.connect() as con:
        audit=con.execute("SELECT details_json FROM research_audit WHERE action='offline_role_changed'").fetchone()
        assert json.loads(audit[0])['new_role']=='member'
