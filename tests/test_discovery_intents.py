"""Stage 1 atomic intent, inheritance, ownership and restart contracts; no paid calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import pytest
from tests.test_foundation_api import service, _job
from tests.test_discovery_foundation import discovery
from src.foundation.db import Database
from src.discovery import intents
from src.foundation.repository import DomainError


def trip_for(client,city='tokyo',start='2026-11-06',end='2026-11-09'):
    from src.destinations import CITIES
    city=CITIES[city]
    result=client.post('/api/v2/trips',json={'title':city['name_ko']+' 여행','start_date':start,'end_date':end,'stops':[{'city':city['name_en'],'sequence':1,'start_date':start,'end_date':end,'timezone':city['timezone']}]})
    assert result.status_code==201,result.text
    return result.json()


def body(trip,cv=0,**changes):
    return {'expected_trip_version':trip['version'],'expected_conditions_version':cv,'stop_id':trip['stops'][0]['id'],'visit_date':trip['start_date'],'overrides':{},'filters':{'rating_filter':{'enabled':False}},**changes}


def submit(client,trip,payload=None,key='atomic-intent-one'):
    return client.post(f"/api/v2/trips/{trip['id']}/discovery-intents",json=payload or body(trip),headers={'Idempotency-Key':key})


@pytest.mark.parametrize('city',['tokyo','barcelona','madrid','paris'])
def test_first_request_inherits_and_persists_without_condition_save(discovery,city):
    trip=trip_for(discovery.client,city)
    response=submit(discovery.client,trip)
    assert response.status_code==202,response.text
    data=response.json();resolved=data['resolved_context']
    assert resolved['conditions']['city']==city
    assert resolved['conditions']['party']['children_status']=='unknown'
    assert resolved['provenance']['party.adults']['origin']=='trip_default'
    assert data['conditions_version']==1
    assert _job(discovery.client,data)['state']=='succeeded'
    replay=submit(discovery.client,trip)
    assert replay.status_code==202 and replay.json()['job_id']==data['job_id']
    with Database(discovery.app.state.db.path).connect() as con:
        assert con.execute('SELECT count(*) FROM discovery_intents WHERE trip_id=?',(trip['id'],)).fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]==0
    assert discovery.client.get(data['intent_url']).json()['job_id']==data['job_id']


def test_party_and_dates_inherit_while_explicit_override_survives(discovery):
    t=trip_for(discovery.client)
    assert submit(discovery.client,t,body(t,overrides={'party':{'adults':3},'origin':None})).status_code==202
    t2=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':1,'start_date':'2026-12-01','end_date':'2026-12-04','party':{'adults':5,'children_status':'none'}})
    assert t2.status_code==200,t2.text
    t2=t2.json();assert t2['stops'][0]['id']==t['stops'][0]['id']
    env=discovery.client.get(f"/api/v2/trips/{t['id']}/discovery-conditions").json()
    assert env['conditions']['visit']['date']=='2026-12-01'
    assert env['conditions']['party']=={'adults':3,'children':[],'children_status':'none'}
    assert env['provenance']['origin']['origin']=='user_override'
    assert env['overrides']=={'party':{'adults':3},'origin':None}
    # Explicit reset omits the override: inherited party now 5, not null or zero.
    assert submit(discovery.client,t2,body(t2,cv=1),key='reset-trip-default').status_code==202
    assert discovery.client.get(f"/api/v2/trips/{t['id']}/discovery-conditions").json()['conditions']['party']['adults']==5


def test_same_key_different_input_and_concurrent_versions(discovery):
    t=trip_for(discovery.client);payload=body(t)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:submit(discovery.client,t,payload),range(2)))
    assert [r.status_code for r in responses]==[202,202]
    assert len({r.json()['job_id'] for r in responses})==1
    changed=deepcopy(payload);changed['overrides']={'party':{'adults':2}}
    assert submit(discovery.client,t,changed).status_code==409
    assert submit(discovery.client,t,payload,key='different-key-stale').status_code==409
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM recommendation_runs WHERE trip_id=?',(t['id'],)).fetchone()[0]==1
        assert con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(t['id'],)).fetchone()[0]==1


def test_mid_transaction_failure_rolls_back_conditions_and_job(discovery,monkeypatch):
    t=trip_for(discovery.client);jobs=discovery.app.state.jobs
    original=jobs.enqueue
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise DomainError('TEST_ROLLBACK','Synthetic enqueue failure',503)
    monkeypatch.setattr(jobs,'enqueue',fail)
    response=submit(discovery.client,t)
    assert response.status_code==503,response.text
    with discovery.app.state.db.connect() as con:
        for table in ['discovery_conditions','discovery_contexts','discovery_intents','recommendation_runs','jobs']:
            assert con.execute(f'SELECT count(*) FROM {table} WHERE trip_id=?',(t['id'],)).fetchone()[0]==0
    monkeypatch.setattr(jobs,'enqueue',original)
    assert submit(discovery.client,t).status_code==202


def test_two_users_two_trips_cross_scope_and_delete(discovery):
    a=[trip_for(discovery.client,c) for c in ['tokyo','barcelona']]
    other=discovery.login('intent-other');b=[trip_for(other.client,c) for c in ['madrid','paris']]
    result=submit(discovery.client,a[0]).json()
    assert other.client.get(result['intent_url']).status_code==404
    assert submit(other.client,a[0]).status_code==404
    assert submit(discovery.client,a[1],body(a[1],stop_id=b[0]['stops'][0]['id'])).status_code==404
    assert discovery.client.get(result['intent_url'].replace(a[0]['id'],a[1]['id'])).status_code==404
    assert discovery.client.delete('/api/v2/trips/'+a[0]['id']).status_code in [200,202]
    assert discovery.client.get(result['intent_url']).status_code==404
    assert submit(discovery.client,a[0]).status_code==404


def test_non_contiguous_stop_identity_and_gap(discovery):
    t=trip_for(discovery.client,end='2026-11-15');first=t['stops'][0]
    stops=[{k:v for k,v in first.items() if k in ['id','city','sequence','start_date','end_date','timezone','base_location']}]
    stops[0]['end_date']='2026-11-08';stops.append({**stops[0],'id':None,'sequence':2,'start_date':'2026-11-12','end_date':'2026-11-15'})
    response=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':1,'stops':stops})
    assert response.status_code==200,response.text
    t=response.json();assert t['stops'][0]['id']==first['id'] and t['stops'][1]['id']!=first['id']
    assert submit(discovery.client,t,body(t,visit_date='2026-11-10')).status_code==422
    assert submit(discovery.client,t,body(t,stop_id=t['stops'][1]['id'],visit_date='2026-11-12')).status_code==202


def test_origin_coordinates_do_not_reactivate_after_date_change(discovery):
    t=trip_for(discovery.client);origin={'label':'Synthetic start','latitude':35.6,'longitude':139.7}
    assert submit(discovery.client,t,body(t,overrides={'origin':origin})).status_code==202
    response=submit(discovery.client,t,body(t,1,visit_date='2026-11-07',overrides={'origin':origin}),key='different-visit-day')
    assert response.status_code==202,response.text
    for resolved in [response.json()['resolved_context'],discovery.client.get(f"/api/v2/trips/{t['id']}/discovery-conditions").json()]:
        assert resolved['conditions']['origin']['latitude'] is None
        assert resolved['basis']['origin_invalidated'] is True
    assert response.json()['resolved_context']['overrides']['origin']['latitude']==35.6


@pytest.mark.parametrize('invalid',['0000-01-01','123456-01-01','2026-02-29','2026-13-10'])
def test_invalid_dates_rejected_without_job(discovery,invalid):
    t=trip_for(discovery.client)
    assert submit(discovery.client,t,body(t,visit_date=invalid)).status_code==422


def test_invalid_override_and_null_are_not_silently_removed(discovery):
    t=trip_for(discovery.client)
    for overrides in [{'owner_id':'other'},{'visit':None},{'party':{'adults':None}},{'party':{'children_status':'none','children':[{'age':4}]}}]:
        assert submit(discovery.client,t,body(t,overrides=overrides)).status_code==422


def test_legacy_conditions_remain_explicit_and_old_snapshot_is_immutable(discovery):
    from tests.discovery_synthetic import conditions
    t=trip_for(discovery.client);c=conditions()
    r=discovery.client.patch(f"/api/v2/trips/{t['id']}/discovery-conditions",json={'expected_version':0,'conditions':c})
    assert r.status_code==200,r.text
    env=r.json();assert env['overrides']['party']['adults']==4
    assert env['provenance']['party.adults']['origin']=='user_override'
    r=submit(discovery.client,t,body(t,1,overrides=env['overrides']));assert r.status_code==202,r.text
    result=r.json()
    t2=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':1,'party':{'adults':6}}).json()
    assert t2['party']['adults']==6
    env=discovery.client.get(f"/api/v2/trips/{t['id']}/discovery-conditions").json()
    assert env['conditions']['party']['adults']==4
    assert discovery.client.get(result['intent_url']).json()['resolved_context']==result['resolved_context']


def test_existing_condition_version_zero_can_start_itinerary(discovery):
    t=trip_for(discovery.client)
    response=discovery.client.post(f"/api/v2/trips/{t['id']}/itineraries",json={'trip_version':t['version'],'conditions_version':0,'start_date':t['start_date'],'end_date':t['start_date'],'selected':[]},headers={'Idempotency-Key':'inherited-itinerary'})
    assert response.status_code==202,response.text
    assert _job(discovery.client,response.json())['state']=='succeeded'


def test_schema10_upgrade_and_readonly_inventory_preserve_data(discovery):
    from scripts.discovery_context_inventory import inventory
    t=trip_for(discovery.client)
    from tests.discovery_synthetic import conditions
    path=f"/api/v2/trips/{t['id']}/discovery-conditions"
    assert discovery.client.patch(path,json={'expected_version':0,'conditions':conditions()}).status_code==200
    db=discovery.app.state.db
    with db.connect() as con:
        saved=con.execute('SELECT conditions_json FROM discovery_conditions WHERE trip_id=?',(t['id'],)).fetchone()[0]
        for table in ('place_photo_cache','review_run_dependencies','place_review_requests','place_external_links','review_provider_contracts','workspace_drafts','itinerary_generation_drafts','maintenance_status','storage_deletion_receipts'):con.execute('DROP TABLE '+table)
        con.execute('DROP TABLE accommodation_resolutions');con.execute('DROP TABLE trip_accommodations');con.execute('DROP TABLE discovery_intents');con.execute('DROP TABLE discovery_contexts')
        con.execute('UPDATE schema_version SET version=10' if db.backend=='postgres' else 'PRAGMA user_version=10')
    if db.backend=='sqlite':
        import sqlite3
        with sqlite3.connect(db.path) as con:
            report=inventory(con);assert report['writes']==0 and report['database_schema']==10
    db._migrate()
    assert db.schema_version()==15
    with db.connect() as con:
        assert con.execute('SELECT conditions_json FROM discovery_conditions WHERE trip_id=?',(t['id'],)).fetchone()[0]==saved
        assert con.execute('SELECT id FROM trip_stops WHERE trip_id=?',(t['id'],)).fetchone()[0]==t['stops'][0]['id']
    assert discovery.client.get(path).json()['provenance']['party.adults']['origin']=='user_override'


def test_process_killed_inside_sql_transaction_does_not_publish(discovery,tmp_path):
    if discovery.app.state.db.backend!='sqlite':pytest.skip('SQLite process kill; PostgreSQL transaction fault is tested separately')
    import subprocess,sys,time
    t=trip_for(discovery.client)
    marker=tmp_path/'transaction-open'
    script="""
import sqlite3,sys,time
con=sqlite3.connect(sys.argv[1]);con.execute('BEGIN IMMEDIATE')
con.execute('INSERT INTO discovery_contexts VALUES(?,?,?,?,?,?)',(sys.argv[2],sys.argv[3],sys.argv[4],'{}','{}','synthetic'))
open(sys.argv[5],'w').write('open')
time.sleep(30)
"""
    with discovery.app.state.db.connect() as con:owner=con.execute('SELECT owner_id FROM trips WHERE id=?',(t['id'],)).fetchone()[0]
    proc=subprocess.Popen([sys.executable,'-c',script,str(discovery.app.state.db.path),t['id'],owner,t['stops'][0]['id'],str(marker)])
    try:
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert marker.exists()
        proc.kill();proc.wait(timeout=5)
        assert submit(discovery.client,t).status_code==202
        with discovery.app.state.db.connect() as con:
            assert con.execute('SELECT count(*) FROM discovery_intents WHERE trip_id=?',(t['id'],)).fetchone()[0]==1
    finally:
        if proc.poll() is None:proc.kill();proc.wait()


def test_two_different_intents_cannot_both_save_same_version(discovery):
    t=trip_for(discovery.client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:submit(discovery.client,t,key=key),['concurrent-version-one','concurrent-version-two']))
    assert sorted(r.status_code for r in results)==[202,409]
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(t['id'],)).fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM discovery_intents WHERE trip_id=?',(t['id'],)).fetchone()[0]==1


def test_reorder_keeps_ids_and_removed_selected_stop_needs_confirmation(discovery):
    t=trip_for(discovery.client);s=t['stops'][0]
    fields=['id','city','sequence','start_date','end_date','timezone','base_location']
    stop={k:s[k] for k in fields}
    added={**stop,'id':None,'city':'Barcelona','timezone':'Europe/Madrid','sequence':2}
    t=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':1,'stops':[stop,added]}).json()
    assert submit(discovery.client,t).status_code==202
    reordered=[{**{k:v[k] for k in fields},'sequence':i+1} for i,v in enumerate(reversed(t['stops']))]
    latest=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':t['version'],'stops':reordered})
    assert latest.status_code==200,latest.text
    assert [v['id'] for v in latest.json()['stops']]==[v['id'] for v in reversed(t['stops'])]
    latest=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':latest.json()['version'],'stops':[reordered[0]]}).json()
    context=discovery.client.get(f"/api/v2/trips/{t['id']}/discovery-conditions").json()
    assert context['context_state']=='outdated' and context['validation'][0]['field']=='stop_id'
    assert context['trip_context']['stop_id']==s['id']
