"""Durable recommendation API with real SQL/auth/jobs and approved synthetic facts.

These tests run the actual pure ranking engine. No live provider, paid request,
or production recommendation quality is implied by the synthetic fixture.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from fastapi.testclient import TestClient
import pytest

from tests.test_foundation_api import service, _trip, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.discovery_synthetic import conditions
from tests.test_reliability_api import eventually


def prepare(discovery):
    stored=import_pack(discovery)
    trip=_trip(discovery.client)
    response=discovery.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",
        json={'expected_version':0,'conditions':conditions()})
    assert response.status_code==200,response.text
    return trip,stored


def submit(discovery,trip,key='recommendation-intent',**changes):
    body={'trip_version':trip['version'],'conditions_version':1,**changes}
    return discovery.client.post(f"/api/v2/trips/{trip['id']}/recommendations",json=body,
        headers={'Idempotency-Key':key})


def completed(discovery,trip,receipt):
    assert receipt.status_code==202,receipt.text
    job=_job(discovery.client,receipt.json())
    assert job['state']=='succeeded',job
    response=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{receipt.json()['run_id']}")
    assert response.status_code==200,response.text
    assert response.json()['result'] is not None,response.text
    return response.json()


def eligible_ids(run):
    return list(dict.fromkeys(p['place_id'] for section in run['result']['sections'].values()
        for name in ('items','needs_confirmation','insufficient_data') for p in section[name]))


def block_engine(monkeypatch):
    from src.recommendations import engine
    original=engine.recommend
    started,release=threading.Event(),threading.Event()
    def blocked(*args,**kwargs):
        started.set()
        assert release.wait(8),'Test did not release the recommendation calculation'
        return original(*args,**kwargs)
    monkeypatch.setattr(engine,'recommend',blocked)
    return started,release


def test_saved_snapshot_reloads_with_no_external_cost_even_when_budget_halted(discovery):
    trip,_=prepare(discovery)
    with discovery.app.state.db.connect() as con:
        con.execute("INSERT INTO cost_controls VALUES('USD',1,'TEST_BUDGET_EXHAUSTED','2026-10-05T00:00:00+00:00')")
    receipt=submit(discovery,trip)
    run=completed(discovery,trip,receipt)
    result=run['result']
    assert set(result['sections'])=={'local_discovery','landmark','reference'}
    assert not result['sections']['local_discovery']['items']  # No synthetic language gate in this fixture.
    assert result['sections']['landmark']['items']
    assert run['input_status']==run['data_status']=='current'
    assert run['conditions_snapshot']['party']['adults']==4
    assert result['external_discovery']['calls']==0
    assert result['external_discovery']['reason']=='EXTERNAL_DISCOVERY_NOT_CONFIGURED'
    assert result['requested_constraints']['conditions']==run['conditions_snapshot']
    assert run['snapshot']['ranker_versions']==run['ranker_versions']
    path=f"/api/v2/trips/{trip['id']}/recommendations/{run['run_id']}"
    assert discovery.client.get(path).json()['result']==result
    assert discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations").json()['items'][0]['run_id']==run['run_id']
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0]==0
        assert con.execute('SELECT COUNT(*) FROM usage_ledger').fetchone()[0]==0
        payload=json.loads(con.execute('SELECT payload_json FROM jobs WHERE id=?',(run['job_id'],)).fetchone()[0])
        assert payload=={'run_id':run['run_id']}
    assert discovery.calls==[]


def test_private_run_job_sse_comparison_and_events_are_owner_scoped(discovery):
    trip,_=prepare(discovery)
    run=completed(discovery,trip,submit(discovery,trip))
    other=discovery.login('recommendation-B');othertrip=_trip(other.client)
    another=_trip(discovery.client,'Same owner second trip')
    base=f"/api/v2/trips/{trip['id']}"
    for suffix in ('/recommendations','/recommendations/'+run['run_id']):
        assert other.client.get(base+suffix).status_code==404
    assert other.client.post(base+'/recommendations',json={'trip_version':1,'conditions_version':1},headers={'Idempotency-Key':'B-intent'}).status_code==404
    assert discovery.client.get(f"/api/v2/trips/{another['id']}/recommendations/{run['run_id']}").status_code==404
    assert other.client.get(f"/api/v2/trips/{othertrip['id']}/recommendations/{run['run_id']}").status_code==404
    for path in ('/api/v2/jobs/'+run['job_id'],'/api/v2/jobs/'+run['job_id']+'/events'):
        assert other.client.get(path).status_code==404
    assert other.client.post('/api/v2/jobs/'+run['job_id']+'/cancel').status_code==404
    compare=discovery.client.post(base+'/comparisons',json={'run_id':run['run_id'],'place_ids':eligible_ids(run)[:2]})
    assert compare.status_code==201,compare.text
    comparison_id=compare.json()['comparison_id']
    assert other.client.get(base+'/comparisons/'+comparison_id).status_code==404
    assert other.client.post(base+'/comparisons',json={'run_id':run['run_id'],'place_ids':eligible_ids(run)[:2]}).status_code==404
    assert other.client.post(base+'/discovery-events',json={'event':'recommendation_view','run_id':run['run_id']}).status_code==404
    anonymous=TestClient(discovery.app)
    try:
        assert anonymous.get(base+'/recommendations').status_code==401
    finally:anonymous.close()


def test_concurrent_idempotency_returns_one_run_and_rejects_changed_payload(discovery):
    trip,_=prepare(discovery)
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses=list(pool.map(lambda _:submit(discovery,trip),range(5)))
    assert all(r.status_code==202 for r in responses),[r.text for r in responses]
    assert len({r.json()['run_id'] for r in responses})==1
    assert len({r.json()['job_id'] for r in responses})==1
    run=completed(discovery,trip,responses[0])
    mismatch=submit(discovery,trip,limit=3)
    assert mismatch.status_code==409 and mismatch.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
    assert submit(discovery,trip).json()['run_id']==run['run_id']
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM recommendation_runs').fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM jobs WHERE operation='recommendations'").fetchone()[0]==1


def test_completed_sse_resumes_without_restarting_recommendation(discovery):
    trip,_=prepare(discovery)
    run=completed(discovery,trip,submit(discovery,trip))
    path='/api/v2/jobs/'+run['job_id']+'/events'
    first=discovery.client.get(path)
    assert first.status_code==200 and 'event: completed' in first.text
    ids=[int(line[4:]) for line in first.text.splitlines() if line.startswith('id: ')]
    assert ids==list(range(1,run['job']['last_event_id']+1))
    middle=ids[len(ids)//2]
    replay=discovery.client.get(path,headers={'Last-Event-ID':str(middle)})
    assert [int(line[4:]) for line in replay.text.splitlines() if line.startswith('id: ')]==list(range(middle+1,ids[-1]+1))
    assert 'example.org' not in replay.text and 'conditions_snapshot' not in replay.text
    with discovery.app.state.db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM jobs WHERE operation='recommendations'").fetchone()[0]==1
    assert discovery.calls==[]


def test_comparison_accepts_two_or_three_distinct_same_run_places(discovery):
    trip,_=prepare(discovery)
    run=completed(discovery,trip,submit(discovery,trip))
    ids=eligible_ids(run);assert len(ids)>=2
    base=f"/api/v2/trips/{trip['id']}/comparisons"
    good=discovery.client.post(base,json={'run_id':run['run_id'],'place_ids':ids[:2]})
    assert good.status_code==201,good.text
    assert len(good.json()['items'])==2 and good.json()['conditions']==run['conditions_snapshot']
    assert discovery.client.get(base+'/'+good.json()['comparison_id']).json()==good.json()
    for selected in ([ids[0]], [ids[0],ids[0]], [*ids[:2],'third','fourth']):
        assert discovery.client.post(base,json={'run_id':run['run_id'],'place_ids':selected}).status_code==422
    assert discovery.client.post(base,json={'run_id':run['run_id'],'place_ids':[ids[0],'unknown-place']}).status_code==404


def test_changed_conditions_and_trip_versions_preserve_old_snapshot_as_stale(discovery):
    trip,_=prepare(discovery)
    run=completed(discovery,trip,submit(discovery,trip))
    changed=conditions();changed['party']['adults']=5
    path=f"/api/v2/trips/{trip['id']}"
    assert discovery.client.patch(path+'/discovery-conditions',json={'expected_version':1,'conditions':changed}).status_code==200
    after=discovery.client.get(path+'/recommendations/'+run['run_id']).json()
    assert after['input_status']=='stale' and after['conditions_snapshot']['party']['adults']==4
    assert after['result']==run['result']
    stale=submit(discovery,trip,key='outdated-new-intent')
    assert stale.status_code==409 and stale.json()['error']['code']=='VERSION_CONFLICT'
    assert discovery.client.patch(path,json={'expected_version':trip['version'],'title':'Revised trip'}).status_code==200
    stale=submit(discovery,trip,key='outdated-trip',conditions_version=2)
    assert stale.status_code==409 and stale.json()['error']['code']=='VERSION_CONFLICT'


@pytest.mark.parametrize('change',['revoke','expire','disable'])
def test_withdrawn_or_expired_sources_withhold_affected_cards_and_preserve_snapshot(discovery,change):
    trip,stored=prepare(discovery)
    run=completed(discovery,trip,submit(discovery,trip))
    base=f"/api/v2/trips/{trip['id']}"
    comparison=discovery.client.post(base+'/comparisons',json={'run_id':run['run_id'],'place_ids':eligible_ids(run)[:2]}).json()
    if change=='revoke':
        source=stored['places'][0]['sources'][0]
        response=discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={
            'expected_version':2,'status':'revoked','read_confirmed':True,'display_permitted':False,
            'policy_version':stored['version'],'evidence':'Synthetic source permissions withdrawn'})
        assert response.status_code==200,response.text
    elif change=='expire':
        with discovery.app.state.db.connect() as con:
            con.execute("UPDATE place_facts SET expires_at='2000-01-01T00:00:00+00:00' WHERE place_id=?",(stored['places'][0]['place_id'],))
    else:
        assert discovery.client.patch('/api/v2/admin/discovery-packs/'+stored['id'],json={
            'status':'disabled','evidence':'Synthetic evidence pack deactivated'}).status_code==200
    after=discovery.client.get(base+'/recommendations/'+run['run_id']).json()
    assert after['result'] is not None and after['data_status']=='stale' and 'SOURCE_DATA_CHANGED' in after['reason_codes']
    withheld=set(after['result']['withheld_place_ids'])
    assert stored['places'][0]['place_id'] in withheld
    assert not set(eligible_ids(after))&withheld
    assert after['result']['ranking_status'] == 'stale'
    assert all('ranking_diagnostics' not in item and item['score'] is None
               for groups in after['result']['sections'].values() for items in groups.values() for item in items)
    compared=discovery.client.get(base+'/comparisons/'+comparison['comparison_id']).json()
    assert not {p['place_id'] for p in compared['items']}&withheld and compared['data_status']=='stale'
    if change!='disable':assert set(eligible_ids(run))-withheld<=set(eligible_ids(after))
    with discovery.app.state.db.connect() as con:
        row=con.execute('SELECT result_json,candidates_json FROM recommendation_runs WHERE id=?',(run['run_id'],)).fetchone()
        assert row['result_json'] is not None and row['candidates_json'] is not None


def test_cancellation_during_calculation_does_not_activate_late_result(discovery,monkeypatch):
    trip,_=prepare(discovery);started,release=block_engine(monkeypatch)
    receipt=submit(discovery,trip);assert receipt.status_code==202,receipt.text
    try:
        assert started.wait(4)
        response=discovery.client.post('/api/v2/jobs/'+receipt.json()['job_id']+'/cancel')
        assert response.status_code==202 and response.json()['cancel_requested_at'] is not None
    finally:release.set()
    job=_job(discovery.client,receipt.json())
    assert job['state']=='cancelled',job
    result=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{receipt.json()['run_id']}").json()
    assert result['result'] is None
    retry=discovery.client.post('/api/v2/jobs/'+receipt.json()['job_id']+'/retry',headers={'Idempotency-Key':'retry-does-not-reuse-stale-snapshot'})
    assert retry.status_code==409


def test_delete_during_calculation_blocks_read_and_scrubs_late_result(discovery,monkeypatch):
    trip,_=prepare(discovery);started,release=block_engine(monkeypatch)
    receipt=submit(discovery,trip);assert receipt.status_code==202,receipt.text
    try:
        assert started.wait(4)
        deletion=discovery.client.delete(f"/api/v2/trips/{trip['id']}")
        assert deletion.status_code==202,deletion.text
        assert discovery.client.get('/api/v2/jobs/'+receipt.json()['job_id']).status_code==404
        assert discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{receipt.json()['run_id']}").status_code==404
    finally:release.set()
    eventually(lambda:discovery.client.get(deletion.json()['receipt_url']).json()['state']=='succeeded')
    with discovery.app.state.db.connect() as con:
        row=con.execute('SELECT * FROM recommendation_runs WHERE id=?',(receipt.json()['run_id'],)).fetchone()
        assert row['result_json'] is None and row['candidates_json'] is None and row['snapshot_json']=='{}'
        assert row['data_status']=='deleted'
        assert con.execute('SELECT COUNT(*) FROM discovery_conditions WHERE trip_id=?',(trip['id'],)).fetchone()[0]==0


def test_source_revoked_while_calculating_prevents_publication(discovery,monkeypatch):
    trip,stored=prepare(discovery);started,release=block_engine(monkeypatch)
    receipt=submit(discovery,trip);assert receipt.status_code==202,receipt.text
    try:
        assert started.wait(4)
        source=stored['places'][0]['sources'][0]
        response=discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={
            'expected_version':2,'status':'revoked','read_confirmed':True,'display_permitted':False,
            'policy_version':stored['version'],'evidence':'Revocation races an in-flight calculation'})
        assert response.status_code==200,response.text
    finally:release.set()
    job=_job(discovery.client,receipt.json())
    assert job['state']=='failed' and job['error_code']=='SOURCE_DATA_CHANGED',job
    response=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{receipt.json()['run_id']}")
    assert response.json()['result'] is None


def test_review_only_withdrawal_preserves_iconic_and_reference_results(discovery,monkeypatch):
    trip,_=prepare(discovery)
    run=completed(discovery,trip,submit(discovery,trip))
    ids={p['place_id'] for p in run['result']['sections']['landmark']['items']}
    original=discovery.app.state.reviews._evidence_on
    def changed(con,place_ids):
        rows=original(con,place_ids)
        for row in rows.values():row['withdrawal_test_version']='review-only-change'
        return rows
    monkeypatch.setattr(discovery.app.state.reviews,'_evidence_on',changed)
    output=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{run['run_id']}").json()
    assert output['data_status']=='stale'
    assert {p['place_id'] for p in output['result']['sections']['landmark']['items']}==ids
    assert output['result']['withheld_review_place_ids']
    assert not output['result']['withheld_place_ids']
    assert all(p['review_evidence']['counts'] is None for p in output['result']['sections']['landmark']['items'])
