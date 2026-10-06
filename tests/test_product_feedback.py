from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4
import pytest
from tests.test_foundation_api import service,_trip,_job
from tests.test_discovery_foundation import discovery,import_pack
from tests.test_recommendation_persistence import setup
from src.product.reporting import metric
from src.product.events import purge
from tests.test_itinerary_api import planning

def prepared(discovery,opt_in=True):
    if opt_in:
        r=discovery.client.patch('/api/v2/product-preferences',json={'expected_version':0,'analytics_enabled':True});assert r.status_code==200,r.text
    pack,trip,path,body=setup(discovery)
    response=discovery.client.post(path+'/recommendations',json=body,headers={'Idempotency-Key':'product-run'});assert response.status_code==202,response.text
    job=_job(discovery.client,response.json());assert job['state']=='succeeded',job
    return pack,trip,path,response.json()['run_id']

def event(run,place,name='recommendation_view',**extra):
    return {'event_id':str(uuid4()),'event_name':name,'run_id':run,'place_id':place,'client_at':datetime.now(timezone.utc).isoformat(),**extra}

def test_events_consent_scope_dedup_and_server_mutation(discovery):
    pack,trip,path,run=prepared(discovery,False);place=pack['places'][0]['place_id'];body=event(run,place)
    assert discovery.client.post(path+'/events',json=body).json()['recorded'] is False
    assert discovery.client.patch('/api/v2/product-preferences',json={'expected_version':0,'analytics_enabled':True}).status_code==200
    r=discovery.client.post(path+'/events',json=body);assert r.status_code==200,r.text
    assert discovery.client.post(path+'/events',json=body).json()['duplicate']
    assert discovery.client.post(path+'/events',json={**body,'private_note':'PRIVATE'}).status_code==422
    assert discovery.client.post(path+'/events',json={**body,'event_name':'recommendation_save'}).status_code==422
    other=discovery.login('B')
    assert other.client.post(path+'/events',json=body).status_code==404
    t2=_trip(discovery.client)
    assert discovery.client.post('/api/v2/trips/'+t2['id']+'/events',json=body).status_code==404
    source=pack['places'][0]['sources'][0]['id']
    assert discovery.client.post(path+'/events',json=event(run,place,'source_open',source_id=source)).status_code==200
    assert discovery.client.post(path+'/events',json=event(run,place,'source_open',source_id=pack['places'][1]['sources'][0]['id'])).status_code==404
    save={'input_kind':'place','input_value':place,'note':'PRIVATE-BOOKMARK','run_id':run}
    assert discovery.client.post(path+'/bookmarks',json=save).status_code==201
    assert discovery.client.post(path+'/bookmarks',json=save).json()['duplicate']
    with discovery.app.state.db.connect() as con:
        assert con.execute("SELECT count(*) FROM discovery_events WHERE event='recommendation_save'").fetchone()[0]==1
        assert 'PRIVATE' not in json.dumps([dict(r) for r in con.execute('SELECT * FROM discovery_events')])
    assert discovery.client.patch('/api/v2/product-preferences',json={'expected_version':1,'analytics_enabled':False}).status_code==200
    with discovery.app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM discovery_events').fetchone()[0]==0

def test_feedback_experience_edit_withdraw_and_notes_separate(discovery):
    pack,trip,path,run=prepared(discovery);place=pack['places'][0]['place_id'];url=path+'/places/'+place+'/feedback'
    body={'feedback_kind':'visit','visit_status':'not_visited','reason_codes':['reservation_failed'],'private_note':'PRIVATE-NOTE-secret','run_id':run}
    assert discovery.client.post(url,json={**body,'wait_feeling':'long'},headers={'Idempotency-Key':'bad'}).status_code==422
    r=discovery.client.post(url,json=body,headers={'Idempotency-Key':'f1'});assert r.status_code==201,r.text
    saved=r.json();fid=saved['id'];fp=path+'/feedback/'+fid
    assert discovery.client.post(url,json=body,headers={'Idempotency-Key':'f1'}).json()['id']==fid
    assert discovery.client.post(url,json=body,headers={'Idempotency-Key':'f2'}).status_code==409
    changed={**body,'reason_codes':['schedule'],'private_note':'edited-private'}
    assert discovery.client.patch(fp,json={'expected_version':1,'feedback':changed}).status_code==200
    assert discovery.client.patch(fp,json={'expected_version':1,'feedback':changed}).status_code==409
    with discovery.app.state.db.connect() as con:
        output=json.dumps([dict(r) for r in con.execute('SELECT * FROM discovery_events')])
        assert 'PRIVATE' not in output and 'edited-private' not in output
    assert discovery.client.request('DELETE',fp,json={'expected_version':2}).status_code==200
    assert discovery.client.get(fp).json()['private_note']==''
    assert not discovery.client.get(path+'/feedback').json()['items']
    with discovery.app.state.db.connect() as con:
        assert not con.execute("SELECT * FROM discovery_events WHERE detail_json LIKE '%created%'").fetchall()
    pref={'feedback_kind':'preference','reflect_preference':True,'run_id':run,'reason_codes':['price']}
    r=discovery.client.post(url,json=pref,headers={'Idempotency-Key':'pref'});assert r.status_code==201,r.text
    rec=discovery.client.post(path+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1,'rating_filter':{'enabled':False}},headers={'Idempotency-Key':'after-feedback'}).json()
    _job(discovery.client,rec)
    latest=discovery.client.get(path+'/recommendations/'+rec['run_id']).json()
    assert latest['snapshot']['soft_avoid_place_ids']==[place]
    assert latest['conditions_snapshot']['required']=={'dietary':[],'accessibility':[]}

def test_report_zero_synthetic_cohort_exposures_and_deletion(discovery):
    zero=discovery.client.get('/api/v2/admin/product-report').json();assert zero['metrics']['exposure_to_save']['ratio'] is None
    assert metric(0,0)['display']=='미측정'
    pack,trip,path,run=prepared(discovery);place=pack['places'][0]['place_id']
    for _ in range(2):assert discovery.client.post(path+'/events',json=event(run,place)).status_code==200
    assert discovery.client.post(path+'/events',json=event(run,place,client_at='2000-01-01T00:00:00+00:00')).status_code==200
    assert discovery.client.post(path+'/bookmarks',json={'input_kind':'place','input_value':place,'note':'','run_id':run}).status_code==201
    live=discovery.client.get('/api/v2/admin/product-report').json();assert live['cohort']['completed_runs']==0
    result=discovery.client.get('/api/v2/admin/product-report?synthetic=true').json()
    assert result['metrics']['exposure_to_save']['numerator']==result['metrics']['exposure_to_save']['denominator']==1
    assert result['excluded_events']['CLOCK_SKEW']==1
    other=discovery.login('nonadmin');assert other.client.get('/api/v2/admin/product-report').status_code==404
    assert discovery.client.delete(path).status_code==202
    result=discovery.client.get('/api/v2/admin/product-report?synthetic=true').json();assert result['cohort']['completed_runs']==0
    assert discovery.client.get(path+'/feedback').status_code==404

def test_fact_report_requires_official_confirmation_and_preserves_booking(planning):
    discovery=planning
    from tests.test_itinerary_api import submit,done
    pack,trip,path,run=prepared(discovery);place=pack['places'][0]['place_id']
    booking=discovery.client.post(path+'/bookings',json={'provider':'Synthetic confirmed reservation','date':'2026-11-06','status':'user_confirmed','events':[{'event_type':'visit','start_local':'2026-11-06T15:00:00','end_local':'2026-11-06T16:00:00','start_timezone':'Asia/Tokyo','end_timezone':'Asia/Tokyo','location':'Synthetic museum'}]})
    assert booking.status_code==201,booking.text
    saved_booking=booking.json();trip=discovery.client.get(path).json()
    plan,plan_path=done(planning,trip,submit(planning,trip,place,allow_provisional=True))
    detail=discovery.client.get(path+'/places/'+place+'/detail').json();fact=next(f for f in detail['facts'] if f['field']=='price')
    r=discovery.client.post(path+'/places/'+place+'/fact-reports',json={'fact_id':fact['id'],'category':'price','description':'合成 incorrect price'},headers={'Idempotency-Key':'report-1'});assert r.status_code==201,r.text
    ident=r.json()['id'];adminpath='/api/v2/admin/fact-reports/'+ident
    assert discovery.client.patch(adminpath,json={'expected_version':1,'status':'resolved','reason_code':'official_update'}).status_code==422
    assert discovery.client.patch(adminpath,json={'expected_version':1,'status':'needs_evidence','reason_code':'insufficient_evidence'}).status_code==200
    assert discovery.client.get(path+'/fact-reports').json()['items'][0]['status']=='needs_evidence'
    # A user report has not changed the recommendation facts.
    assert discovery.client.get(path+'/recommendations/'+run).json()['data_status']=='current'
    source=next(s for s in detail['sources'] if s['id']==fact['source_id'])
    # Synthetic official-source stand-in is marked in the fixture, no network assertion.
    with discovery.app.state.db.connect() as con:con.execute("UPDATE evidence_sources SET source_type='official' WHERE id=?",(source['id'],))
    correction={'field':'price','value':{**fact['value'],'amount_min':'900','amount_max':'1400'},'status':'verified','source_key':source['source_key'],'checked_at':datetime.now(timezone.utc).isoformat(),'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'valid_for_date':fact['valid_for_date'],'valid_from':fact['valid_from'],'valid_until':fact['valid_until']}
    r=discovery.client.patch(adminpath,json={'expected_version':2,'status':'resolved','reason_code':'official_update','official_source_read':True,'correction':correction});assert r.status_code==200,r.text
    assert r.json()['bookings_changed'] is False
    assert discovery.client.get(path+'/recommendations/'+run).json()['data_status']=='stale'
    assert discovery.client.get(path+'/bookings/'+saved_booking['id']).json()==saved_booking
    after=discovery.client.get(plan_path).json()
    assert after['data_status']=='stale' and after['version']==plan['version'] and after['active_revision_id']==plan['active_revision_id']

def test_evaluation_frozen_and_expired_refused(discovery):
    pack,trip,path,run=prepared(discovery);url=path+'/recommendations/'+run+'/evaluation'
    a=discovery.client.post(url,json={'candidate_version':'diversity-v2'});assert a.status_code==200,a.text
    assert a.json()==discovery.client.post(url,json={'candidate_version':'diversity-v2'}).json()
    assert a.json()['language_gate_unchanged'] and a.json()['automatic_winner'] is None
    assert all(x['hard_violations']['after']==0 for x in a.json()['comparison'])
    with discovery.app.state.db.connect() as con:con.execute("UPDATE place_facts SET expires_at='2000-01-01T00:00:00+00:00'")
    assert discovery.client.post(url,json={'candidate_version':'diversity-v2'}).status_code==409

def test_retention_and_later_withdrawals_on_old_backup(discovery,tmp_path):
    import sqlite3
    from src.foundation.db import Database
    from src.discovery.maintenance import merge_tombstones
    pack,trip,path,run=prepared(discovery);place=pack['places'][0]['place_id']
    response=discovery.client.post(path+'/places/'+place+'/feedback',json={'feedback_kind':'preference','private_note':'MUST-NOT-REVIVE','run_id':run},headers={'Idempotency-Key':'withdraw-backup'});assert response.status_code==201,response.text
    fid=response.json()['id']
    with discovery.app.state.db.connect() as con:
        con.execute("UPDATE discovery_events SET created_at='2000-01-01T00:00:00+00:00'")
        purge(con);assert con.execute('SELECT count(*) FROM discovery_events').fetchone()[0]==0
    backup=tmp_path/'before.sqlite3'
    if not hasattr(discovery.app.state.db,'pool'):
        with sqlite3.connect(discovery.app.state.db.path) as source,sqlite3.connect(backup) as dest:source.backup(dest)
    assert discovery.client.request('DELETE',path+'/feedback/'+fid,json={'expected_version':1}).status_code==200
    assert discovery.client.patch('/api/v2/product-preferences',json={'expected_version':1,'analytics_enabled':False}).status_code==200
    with discovery.app.state.db.connect() as con:tombs=[dict(r) for r in con.execute("SELECT * FROM discovery_tombstones WHERE kind IN ('feedback','analytics_owner')")]
    if backup.exists():
        db=Database(backup);merge_tombstones(db,tombs)
        with db.connect() as con:
            assert tuple(con.execute('SELECT private_note,payload_json FROM visit_feedback WHERE id=?',(fid,)).fetchone())==('','{}')
            assert con.execute('SELECT count(*) FROM product_run_metrics').fetchone()[0]==0
            assert con.execute('SELECT analytics_enabled FROM product_preferences').fetchone()[0]==0

def test_server_itinerary_and_completion_events_not_preview(planning):
    from tests.test_itinerary_api import prepare as prep,submit,done
    planning.client.patch('/api/v2/product-preferences',json={'expected_version':0,'analytics_enabled':True})
    trip,pack,place,_=prep(planning);plan,path=done(planning,trip,submit(planning,trip,place))
    with planning.app.state.db.connect() as con:
        before=con.execute("SELECT count(*) FROM discovery_events WHERE event='itinerary_add'").fetchone()[0]
        assert before==1
    item=next(i for i in plan['items'] if i.get('place_id'))
    preview=planning.client.post(path+'/edit-previews',json={'expected_version':plan['version'],'commands':[{'op':'lock','item_id':item['item_id']}]})
    # Exact endpoint contract lives in the existing itinerary router.
    assert preview.status_code==200,preview.text
    with planning.app.state.db.connect() as con:assert con.execute("SELECT count(*) FROM discovery_events WHERE event='itinerary_add'").fetchone()[0]==before
    base=f"/api/v2/trips/{trip['id']}";r=planning.client.post(base+'/reservation-tasks',json={'title':'준비','visit_date':'2026-11-06','timezone':'Asia/Tokyo'},headers={'Idempotency-Key':'complete'}).json()
    saved=planning.client.patch(base+'/reservation-tasks/'+r['id'],json={'expected_version':1,'action':'report_complete'});assert saved.status_code==200
    with planning.app.state.db.connect() as con:
        e=con.execute("SELECT detail_json FROM discovery_events WHERE event='booking_task_complete'").fetchone()
        assert json.loads(e[0])['completion_kind']=='user_completed'

def test_stale_recommendation_does_not_lock_owned_feedback(discovery):
    pack,trip,path,run=prepared(discovery);place=pack['places'][0]['place_id']
    body={'feedback_kind':'preference','run_id':run,'reason_codes':['distance']}
    saved=discovery.client.post(path+'/places/'+place+'/feedback',json=body,headers={'Idempotency-Key':'historical'}).json()
    with discovery.app.state.db.connect() as con:
        con.execute("UPDATE recommendation_runs SET data_status='stale',result_json=NULL,candidates_json=NULL WHERE id=?",(run,))
    url=path+'/feedback/'+saved['id']
    changed=discovery.client.patch(url,json={'expected_version':1,'feedback':{**body,'private_note':'edited after expiry'}})
    assert changed.status_code==200,changed.text
    assert discovery.client.patch(url,json={'expected_version':2,'feedback':{**body,'run_id':'another-run'}}).status_code==422
    assert discovery.login('B').client.patch(url,json={'expected_version':2,'feedback':body}).status_code==404
    assert discovery.client.request('DELETE',url,json={'expected_version':2}).status_code==200

def test_latest_opt_out_checkpoint_wins_and_event_id_collision(discovery):
    from src.discovery.maintenance import merge_tombstones
    pack,trip,path,run=prepared(discovery);place=pack['places'][0]['place_id']
    value=event(run,place)
    assert discovery.client.post(path+'/events',json=value).status_code==200
    assert discovery.client.post(path+'/events',json={**value,'place_id':pack['places'][1]['place_id']}).status_code==409
    db=discovery.app.state.db
    with db.connect() as con:owner=con.execute('SELECT owner_id FROM trips WHERE id=?',(trip['id'],)).fetchone()[0]
    tomb=lambda stamp:{'kind':'analytics_owner','target_id':owner,'reason':'ANALYTICS_REVOKED','created_at':stamp}
    merge_tombstones(db,[tomb('2000-01-01T00:00:00+00:00')])
    with db.connect() as con:assert con.execute('SELECT count(*) FROM product_run_metrics').fetchone()[0]==1
    merge_tombstones(db,[tomb((datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat())])
    with db.connect() as con:
        assert con.execute('SELECT count(*) FROM product_run_metrics').fetchone()[0]==0
        assert con.execute('SELECT count(*) FROM discovery_events').fetchone()[0]==0
        assert con.execute('SELECT analytics_enabled FROM product_preferences WHERE owner_id=?',(owner,)).fetchone()[0]==0

def test_report_filters_preserve_unmeasured_denominators(discovery):
    prepared(discovery)
    measured=discovery.client.get('/api/v2/admin/product-report?synthetic=true&recommendation_type=local_discovery&language_required=false').json()
    assert measured['cohort']['completed_runs']==1
    zero=discovery.client.get('/api/v2/admin/product-report?synthetic=true&language_required=true').json()
    assert zero['cohort']['completed_runs']==0 and zero['metrics']['candidate_shortage']['ratio'] is None
    assert discovery.client.get('/api/v2/admin/product-report?start=2026-10-10&end=2026-10-01').status_code==422

def test_personal_feedback_without_run_is_measured_only_after_consented_action(discovery):
    pack,trip,path,run=prepared(discovery,False);place=pack['places'][0]['place_id']
    body={'feedback_kind':'visit','visit_status':'not_visited','reason_codes':['schedule'],'private_note':'NO_ANALYTICS_NOTE'}
    item=discovery.client.post(path+'/places/'+place+'/feedback',json=body,headers={'Idempotency-Key':'no-run'}).json()
    discovery.client.patch('/api/v2/product-preferences',json={'expected_version':0,'analytics_enabled':True})
    endpoint='/api/v2/admin/product-report?synthetic=true'
    assert discovery.client.get(endpoint).json()['reason_counts']=={}
    changed=discovery.client.patch(path+'/feedback/'+item['id'],json={'expected_version':1,'feedback':body});assert changed.status_code==200
    data=discovery.client.get(endpoint).json()
    assert data['feedback_cohort']=={'users':1,'without_recommendation_run':1,'basis':'current_unwithdrawn_updated_in_period'}
    assert data['reason_counts']=={'schedule':1} and 'NO_ANALYTICS_NOTE' not in json.dumps(data)
    discovery.client.patch('/api/v2/product-preferences',json={'expected_version':1,'analytics_enabled':False})
    discovery.client.patch('/api/v2/product-preferences',json={'expected_version':2,'analytics_enabled':True})
    assert discovery.client.get(endpoint).json()['reason_counts']=={}

def test_schema9_upgrade_preserves_trips_and_excludes_legacy_events(discovery):
    pack,trip,path,run=prepared(discovery)
    db=discovery.app.state.db
    with db.connect() as con:
        for table in ('feedback_changes','fact_report_actions','visit_feedback','fact_reports','expense_overrides','product_run_metrics','product_preferences'):con.execute('DROP TABLE '+table)
        con.execute('DROP INDEX product_event_period')
        for column in ('schema_version','detail_json','client_at','exclusion_reason'):con.execute('ALTER TABLE discovery_events DROP COLUMN '+column)
        con.execute('UPDATE schema_version SET version=9' if hasattr(db,'pool') else 'PRAGMA user_version=9')
        owner=con.execute('SELECT owner_id FROM trips WHERE id=?',(trip['id'],)).fetchone()[0]
        con.execute('INSERT INTO discovery_events VALUES(?,?,?,?,?,?,?)',('old-event',owner,trip['id'],'recommendation_view',pack['places'][0]['place_id'],run,datetime.now(timezone.utc).isoformat()))
    db._migrate()
    assert db.schema_version()==10
    assert discovery.client.get(path).status_code==200
    with db.connect() as con:assert con.execute("SELECT schema_version FROM discovery_events WHERE id='old-event'").fetchone()[0]==0
    assert discovery.client.get('/api/v2/admin/product-report?synthetic=true').json()['excluded_events']['legacy_semantics']==1

def test_cost_report_keeps_retries_unknown_charge_and_failed_request(discovery):
    pack,trip,path,run=prepared(discovery)
    with discovery.app.state.db.connect() as con:
        row=con.execute('SELECT owner_id,job_id FROM recommendation_runs WHERE id=?',(run,)).fetchone();stamp=datetime.now(timezone.utc).isoformat()
        con.execute("UPDATE jobs SET state='failed',updated_at=? WHERE id=?",(stamp,row['job_id']))
        con.execute('DELETE FROM product_run_metrics WHERE run_id=?',(run,))
        for attempt,state,actual in ((1,'settled',100),(2,'unknown',None)):
            values={'call_id':str(uuid4()),'owner_id':row['owner_id'],'trip_id':trip['id'],'job_id':row['job_id'],'scope_kind':'personal_trip','scope_id':trip['id'],'provider':'fake','sku':'fake','operation':'explain','attempt':attempt,'call_key':'retry','request_hash':'fake','state':state,'currency':'USD','estimated_units_json':'{}','estimated_cost_micros':200,'actual_cost_micros':actual,'price_version':'synthetic','price_confirmed_at':stamp,'price_rates_json':'{}','period_day':stamp[:10],'period_month':stamp[:7],'created_at':stamp,'updated_at':stamp}
            con.execute('INSERT INTO usage_reservations('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
    result=discovery.client.get('/api/v2/admin/product-report?synthetic=true').json()
    cost=result['failed_cancelled_unattributed_costs']['unattributed:USD']
    assert cost['requests']==1 and cost['calls']==2
    assert cost['settled_per_failed_request_micros']==100 and cost['unknown_upper_per_failed_request_micros']==200
