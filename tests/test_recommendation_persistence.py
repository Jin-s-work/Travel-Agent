import threading
import time
from concurrent.futures import ThreadPoolExecutor
from tests.test_discovery_foundation import discovery, import_pack
from tests.test_foundation_api import service, _trip, _job
from tests.discovery_synthetic import conditions


def setup(discovery):
    pack=import_pack(discovery)
    trip=_trip(discovery.client)
    path=f"/api/v2/trips/{trip['id']}"
    saved=discovery.client.patch(path+'/discovery-conditions',json={'expected_version':0,'conditions':conditions()})
    assert saved.status_code==200,saved.text
    return pack,trip,path,{'trip_version':trip['version'],'conditions_version':1,'rating_filter':{'enabled':False}}


def test_withdrawal_during_scoring_cannot_activate_stale_facts(discovery,monkeypatch):
    from src.recommendations import engine
    pack,trip,path,payload=setup(discovery)
    entered,released=threading.Event(),threading.Event()
    original=engine.recommend
    def pause(*args,**kwargs):
        entered.set();assert released.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(engine,'recommend',pause)
    response=discovery.client.post(path+'/recommendations',json=payload,headers={'Idempotency-Key':'withdraw-during-score'})
    assert response.status_code==202,response.text
    assert entered.wait(5)
    source=pack['places'][0]['sources'][0]
    try:
        result=discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={
            'expected_version':2,'status':'revoked','read_confirmed':True,'display_permitted':False,
            'policy_version':pack['version'],'evidence':'Synthetic concurrent source withdrawal'})
        assert result.status_code==200,result.text
    finally:released.set()
    job=_job(discovery.client,response.json())
    assert job['state']=='failed' and job['error_code']=='SOURCE_DATA_CHANGED'
    run=discovery.client.get(path+'/recommendations/'+response.json()['run_id']).json()
    assert run['result'] is None and run['data_status']=='stale'
    with discovery.app.state.db.connect() as con:
        row=con.execute('SELECT candidates_json,result_json FROM recommendation_runs WHERE id=?',(run['run_id'],)).fetchone()
        assert row['candidates_json'] is not None and row['result_json'] is None


def test_parallel_identical_intent_produces_one_run(discovery):
    _,trip,path,payload=setup(discovery)
    def submit(_):
        return discovery.client.post(path+'/recommendations',json=payload,headers={'Idempotency-Key':'same-concurrent-intent'})
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(submit,range(2)))
    assert all(r.status_code==202 for r in responses),[r.text for r in responses]
    assert len({r.json()['run_id'] for r in responses})==1
    assert _job(discovery.client,responses[0].json())['state'] in {'succeeded','partial'}
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM recommendation_runs WHERE trip_id=?',(trip['id'],)).fetchone()[0]==1


def test_deleted_trip_scrubs_recommendation_snapshots_and_bookmarks(discovery):
    _,trip,path,payload=setup(discovery)
    response=discovery.client.post(path+'/recommendations',json=payload,headers={'Idempotency-Key':'delete-run-snapshot'})
    assert _job(discovery.client,response.json())['state'] in {'succeeded','partial'}
    deletion=discovery.client.delete(path)
    assert deletion.status_code==202,deletion.text
    assert discovery.client.get(path+'/recommendations/'+response.json()['run_id']).status_code==404
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        with discovery.app.state.db.connect() as con:
            row=con.execute('SELECT data_status,snapshot_json,result_json FROM recommendation_runs WHERE trip_id=?',(trip['id'],)).fetchone()
        if row['data_status']=='deleted':break
        time.sleep(.01)
    assert tuple(row)==('deleted','{}',None)
