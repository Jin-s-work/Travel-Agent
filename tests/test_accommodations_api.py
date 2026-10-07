"""Owned lodging storage, durable fake identification and no-spend defaults."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from types import SimpleNamespace
import pytest
from tests.test_foundation_api import service,_job
from tests.test_discovery_foundation import discovery
from tests.test_discovery_intents import trip_for
from src.accommodations.schema import migrate_legacy
from src.location.providers import FakeGeocodingProvider
from src.reliability.budget import BudgetPolicy


def base(t):return f"/api/v2/trips/{t['id']}/accommodations"
def create(client,t,**changes):
    payload={'stop_id':t['stops'][0]['id'],'input_value':'Synthetic branch hotel',**changes}
    r=client.post(base(t),json=payload);assert r.status_code==201,r.text;return r.json()
def fake(discovery,**options):
    provider=FakeGeocodingProvider(candidates=[{'name':'Synthetic Branch Hotel East','address':'Synthetic east street','latitude':35.5,'longitude':0},{'name':'Synthetic Branch Hotel West','address':'Synthetic west street','latitude':35.6,'longitude':1}],**options)
    discovery.app.state.accommodations.geocoder=provider
    policy=deepcopy(discovery.app.state.accommodations.gateway.budget.policy.config)
    policy['prices']['synthetic_location/geocoding']={'currency':'USD','rates_per_million':{'requests':'1'},'max_units':{'requests':1}}
    discovery.app.state.accommodations.gateway.budget.policy=BudgetPolicy(policy)
    return provider

def resolved(discovery,t,s,key='identify-stay-once'):
    path=base(t)+'/'+s['id']
    r=discovery.client.post(path+'/resolve',json={'expected_version':s['version']},headers={'Idempotency-Key':key})
    assert r.status_code==202,r.text
    result=_job(discovery.client,r.json());assert result['state']=='succeeded',result
    return discovery.client.get(path).json(),r.json()

def patch_body(value,**changes):
    keys={'stop_id','display_name','input_kind','input_value','private_note','booking_id','checkin_date','checkout_date','checkin_time','checkout_time','facility_timezone','dates_confirmed'}
    return {**{k:v for k,v in value.items() if k in keys},'expected_version':value['version'],**changes}


def test_save_and_get_make_no_external_calls_and_disabled_resolve_is_honest(discovery):
    t=trip_for(discovery.client);s=create(discovery.client,t)
    assert s['identity_state']=='unresolved' and not s['identity'] and not s['dates_confirmed']
    assert s['checkin_date']==t['start_date'] and s['checkout_date']==t['end_date']
    assert discovery.client.get(base(t)).json()['provider']['enabled'] is False
    r,job=resolved(discovery,t,s);assert r['identity_state']=='unavailable' and 'GEOCODING_DISABLED' in r['reason_codes']
    with discovery.app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]==0
    origin=discovery.client.get(f"/api/v2/trips/{t['id']}/origin-context",params={'visit_date':t['start_date'],'stop_id':t['stops'][0]['id']}).json()
    assert origin['status']=='unresolved' and origin['origin'] is None


def test_two_users_two_trips_reject_cross_references(discovery):
    a=[trip_for(discovery.client,c) for c in ['tokyo','madrid']];b=discovery.login('stay-user-b');bt=[trip_for(b.client,c) for c in ['tokyo','sydney']]
    stay=create(discovery.client,a[0]);path=base(a[0])+'/'+stay['id']
    for client,url in [(b.client,path),(discovery.client,base(a[1])+'/'+stay['id'])]:
        assert client.get(url).status_code==404
        assert client.patch(url,json=patch_body(stay)).status_code==404
        assert client.delete(url,params={'expected_version':1}).status_code==404
        assert client.post(url+'/resolve',json={'expected_version':1},headers={'Idempotency-Key':'cross-owner-stay'}).status_code==404
    assert discovery.client.post(base(a[0]),json={'input_value':'Other','stop_id':bt[0]['stops'][0]['id']}).status_code==404
    assert discovery.client.get(f"/api/v2/trips/{a[1]['id']}/origin-context",params={'visit_date':a[1]['start_date'],'accommodation_id':stay['id']}).status_code==404
    assert discovery.client.post(base(a[0]),json={'input_value':'x','stop_id':a[0]['stops'][0]['id'],'owner_id':b.user['id']}).status_code==422


def test_candidates_are_never_auto_selected_and_selection_is_versioned(discovery):
    provider=fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t);r,job=resolved(discovery,t,s)
    assert r['identity_state']=='candidates' and len(r['candidates'])==2 and r['identity']=={} and provider.calls==1
    path=base(t)+'/'+s['id']
    assert discovery.client.post(path+'/select',json={'expected_version':r['version'],'candidate_id':'other-candidate'}).status_code==404
    body={'expected_version':r['version'],'candidate_id':r['candidates'][0]['candidate_id']}
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:discovery.client.post(path+'/select',json=body),range(2)))
    assert sorted(x.status_code for x in responses)==[200,409]
    selected=next(x.json() for x in responses if x.status_code==200)
    assert selected['identity']['longitude']==0 and selected['identity_state']=='confirmed'
    assert selected['identity']['provenance']=='provider_candidate_selected'
    replay=discovery.client.post(path+'/resolve',json={'expected_version':s['version']},headers={'Idempotency-Key':'identify-stay-once'})
    assert replay.status_code==202 and replay.json()['job_id']==job['job_id'] and provider.calls==1
    different=discovery.client.post(path+'/resolve',json={'expected_version':selected['version']},headers={'Idempotency-Key':'identify-stay-once'})
    assert different.status_code==409
    with discovery.app.state.db.connect() as con:
        checkpoint=con.execute('SELECT checkpoint_json FROM jobs WHERE id=?',(job['job_id'],)).fetchone()[0]
        assert 'Synthetic' not in checkpoint and 'latitude' not in checkpoint
        assert con.execute("SELECT count(*) FROM usage_reservations WHERE state='settled'").fetchone()[0]==1


def test_legacy_label_migration_is_idempotent_and_deleted_not_resurrected(discovery):
    t=trip_for(discovery.client)
    with discovery.app.state.db.connect() as con:
        con.execute('UPDATE trip_stops SET base_location=? WHERE id=?',('Original private label',t['stops'][0]['id']))
        assert migrate_legacy(con)==1 and migrate_legacy(con)==0
    s=discovery.client.get(base(t)).json()['items'][0]
    assert s['input_value']=='Original private label' and s['identity']=={} and s['identity_state']=='unresolved'
    assert discovery.client.delete(base(t)+'/'+s['id'],params={'expected_version':s['version']}).status_code==200
    with discovery.app.state.db.connect() as con:
        assert migrate_legacy(con)==0
        assert con.execute('SELECT base_location FROM trip_stops WHERE id=?',(t['stops'][0]['id'],)).fetchone()[0]=='Original private label'
        assert con.execute("SELECT count(*) FROM deletion_tombstones WHERE target_type='accommodation'").fetchone()[0]==1
    assert discovery.client.get(base(t)).json()['items']==[]


def test_booking_suggestion_keeps_original_override_and_synthetic_never_live_queries(discovery):
    t=trip_for(discovery.client)
    r=discovery.client.post(f"/api/v2/trips/{t['id']}/bookings",json={'kind':'hotel','provider':'Synthetic fixture hotel','date':'2026-11-06','date_end':'2026-11-09','confirmation_number':'SENSITIVE-NUMBER','raw_snippet':'Synthetic test fixture','status':'user_confirmed'})
    assert r.status_code==201,r.text;b=r.json()
    suggestions=discovery.client.get(base(t)).json()['booking_suggestions'];assert len(suggestions)==1 and 'confirmation_number' not in suggestions[0]
    s=create(discovery.client,t,input_kind='booking',input_value='',booking_id=b['id'])
    assert s['source']['booking_version']==b['version'] and s['source']['synthetic']
    assert discovery.client.get(f"/api/v2/trips/{t['id']}/bookings/{b['id']}").json()==b
    class NeverLive:
        name='blocked-live';enabled=True;usage_permitted=True;external=True;adapter_version='1'
        def resolve(self,*args):raise AssertionError('Synthetic mail must not call live geocoding')
    discovery.app.state.accommodations.geocoder=NeverLive()
    response=discovery.client.post(base(t)+'/'+s['id']+'/resolve',json={'expected_version':1,'consent_to_provider':True},headers={'Idempotency-Key':'synthetic-mail-no-live'})
    assert response.status_code==202,response.text
    assert _job(discovery.client,response.json())['state']=='succeeded'
    assert discovery.client.get(base(t)+'/'+s['id']).json()['reason_codes']==['SYNTHETIC_INPUT_NO_LIVE_LOOKUP']


def test_changes_invalidate_origin_snapshot_without_moving_stay_dates(discovery):
    fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t);r,_=resolved(discovery,t,s)
    path=base(t)+'/'+s['id'];selected=discovery.client.post(path+'/select',json={'expected_version':r['version'],'candidate_id':r['candidates'][0]['candidate_id']}).json()
    url=f"/api/v2/trips/{t['id']}/origin-context";params={'visit_date':'2026-11-07','stop_id':t['stops'][0]['id']}
    before=discovery.client.get(url,params=params).json();assert before['status']=='ready'
    patch=discovery.client.patch(path,json=patch_body(selected,checkout_date='2026-11-08'))
    assert patch.status_code==200,patch.text
    after=discovery.client.get(url,params=params).json();assert after['origin_version']!=before['origin_version']
    changedtrip=discovery.client.patch('/api/v2/trips/'+t['id'],json={'expected_version':1,'start_date':'2026-12-01','end_date':'2026-12-04'})
    assert changedtrip.status_code==200,changedtrip.text
    assert discovery.client.get(path).json()['checkout_date']=='2026-11-08'


def test_input_validation_and_private_url_no_fetch(discovery):
    t=trip_for(discovery.client)
    for update in [{'input_kind':'map_url','input_value':'http://127.0.0.1/admin'},{'checkin_date':'0000-01-01'},{'checkin_date':'2026-11-09','checkout_date':'2026-11-06'},{'latitude':0,'longitude':0}]:
        r=discovery.client.post(base(t),json={'stop_id':t['stops'][0]['id'],'input_value':'Hotel',**update});assert r.status_code==422,r.text
    t=trip_for(discovery.client,'new-york',start='2026-11-01',end='2026-11-03')
    r=discovery.client.post(base(t),json={'stop_id':t['stops'][0]['id'],'input_value':'Hotel','checkin_date':'2026-11-01','checkout_date':'2026-11-03','checkin_time':'01:30','facility_timezone':'America/New_York'})
    assert r.status_code==422,r.text
    r=discovery.client.post(base(t),json={'stop_id':t['stops'][0]['id'],'input_value':'Hotel','checkin_date':'2026-11-01','checkout_date':'2026-11-03','checkin_time':'01:30'})
    assert r.status_code==422,r.text


def test_deleted_stay_erases_private_provider_receipt_and_cannot_replay(discovery):
    fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t);r,job=resolved(discovery,t,s)
    gateway=discovery.app.state.accommodations.gateway
    with discovery.app.state.db.connect() as con:
        call=con.execute('SELECT * FROM usage_reservations WHERE job_id=?',(job['job_id'],)).fetchone()
    path=gateway.result_dir/call['result_ref']
    assert gateway.artifacts.read(call['result_ref']) is not None if gateway.artifacts else path.exists()
    assert discovery.client.delete(base(t)+'/'+s['id'],params={'expected_version':r['version']}).status_code==200
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM accommodation_resolutions WHERE accommodation_id=?',(s['id'],)).fetchone()[0]==0
        assert con.execute('SELECT result_ref FROM usage_reservations WHERE job_id=?',(job['job_id'],)).fetchone()[0] is None
    assert gateway.artifacts.read(call['result_ref']) is None if gateway.artifacts else not path.exists()
    assert discovery.client.post(base(t)+'/'+s['id']+'/resolve',json={'expected_version':s['version']},headers={'Idempotency-Key':'identify-stay-once'}).status_code==404


def test_schema11_to12_migration_and_readonly_inventory_preserve_stops(discovery):
    from scripts.accommodation_inventory import inventory
    from src.foundation.db import Database
    db=discovery.app.state.db
    if db.backend=='postgres':pytest.skip('SQLite downgrade fixture; PostgreSQL schema12 covered by isolated plugin creation')
    t=trip_for(discovery.client)
    with db.connect() as con:
        con.execute('UPDATE trip_stops SET base_location=? WHERE id=?',('Original label only',t['stops'][0]['id']))
        before=tuple(con.execute('SELECT id,base_location,start_date,end_date FROM trip_stops WHERE id=?',(t['stops'][0]['id'],)).fetchone())
        con.execute('DROP TABLE accommodation_resolutions');con.execute('DROP TABLE trip_accommodations');con.execute('PRAGMA user_version=11')
        snapshot=inventory(con);assert snapshot['eligible_legacy_labels']==1 and snapshot['writes']==0
    reopened=Database(db.path)
    with reopened.connect() as con:
        assert tuple(con.execute('SELECT id,base_location,start_date,end_date FROM trip_stops WHERE id=?',(t['stops'][0]['id'],)).fetchone())==before
        assert con.execute('SELECT count(*) FROM trip_accommodations').fetchone()[0]==1
        assert migrate_legacy(con)==0


def test_response_loss_is_unknown_and_does_not_call_again(discovery):
    provider=fake(discovery,fail=True);t=trip_for(discovery.client);s=create(discovery.client,t)
    path=base(t)+'/'+s['id']+'/resolve'
    response=discovery.client.post(path,json={'expected_version':1},headers={'Idempotency-Key':'response-lost-once'})
    assert response.status_code==202,response.text
    job=_job(discovery.client,response.json());assert job['state']=='failed' and provider.calls==1
    assert discovery.client.post(path,json={'expected_version':1},headers={'Idempotency-Key':'response-lost-once'}).json()['job_id']==job['id']
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT state FROM usage_reservations WHERE job_id=?',(job['id'],)).fetchone()[0]=='unknown'
    assert provider.calls==1


def claim_identification(discovery,t,s,key='manual-lease-identification'):
    from src.reliability.dispatcher import JobContext
    discovery.lifetime.portal.call(discovery.app.state.dispatcher.stop)
    jobs=discovery.app.state.jobs;jobs.accepting=True
    response=discovery.client.post(base(t)+'/'+s['id']+'/resolve',json={'expected_version':s['version']},headers={'Idempotency-Key':key})
    assert response.status_code==202,response.text
    job=jobs.claim('synthetic-worker');assert job is not None
    return job,JobContext(jobs,job,'synthetic-worker')


def test_checkpoint_resume_and_activated_receipt_do_not_repeat_provider(discovery):
    from src.reliability.dispatcher import JobContext
    provider=fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t)
    job,ctx=claim_identification(discovery,t,s);normal_checkpoint=ctx.checkpoint
    def simulate_crash(*args,**kwargs):
        normal_checkpoint(*args,**kwargs);raise RuntimeError('Synthetic crash after durable receipt')
    ctx.checkpoint=simulate_crash
    with pytest.raises(RuntimeError):discovery.app.state.accommodations.execute_resolution(job,ctx)
    assert provider.calls==1
    resumed=JobContext(discovery.app.state.jobs,job,'synthetic-worker')
    result=discovery.app.state.accommodations.execute_resolution(job,resumed);assert result['identity_state']=='candidates' and provider.calls==1
    # A second crash between activation and job.finish recovers from the activated version.
    final=discovery.app.state.accommodations.execute_resolution(job,resumed)
    assert final['checkpoint_reused'] and provider.calls==1


def test_delete_while_provider_inflight_cannot_restore_location(discovery):
    from src.foundation.repository import DomainError
    provider=fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t);normal_resolve=provider.resolve
    job,ctx=claim_identification(discovery,t,s)
    def delete_then_return(*args):
        result=normal_resolve(*args)
        response=discovery.client.delete(base(t)+'/'+s['id'],params={'expected_version':s['version']});assert response.status_code==200,response.text
        return result
    provider.resolve=delete_then_return
    with pytest.raises(DomainError) as rejected:discovery.app.state.accommodations.execute_resolution(job,ctx)
    assert rejected.value.status==404
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM accommodation_resolutions').fetchone()[0]==0
        row=con.execute('SELECT * FROM trip_accommodations WHERE id=?',(s['id'],)).fetchone()
        assert row['deleted_at'] and row['identity_json']=='{}' and row['input_value']==''
        # The actual sent request remains charged even though its product payload was discarded.
        assert con.execute('SELECT state FROM usage_reservations WHERE job_id=?',(job['id'],)).fetchone()[0]=='settled'


def test_expired_worker_fence_cannot_activate_candidates(discovery):
    from datetime import timedelta
    from src.foundation.repository import DomainError
    from src.reliability.dispatcher import JobContext
    provider=fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t)
    jobs=discovery.app.state.jobs;jobs.lease_seconds=.1
    job,old=claim_identification(discovery,t,s)
    later=jobs.clock()+timedelta(seconds=1);jobs.clock=lambda:later
    successor=jobs.claim('replacement-worker');assert successor and successor['fencing_token']>job['fencing_token']
    with pytest.raises(DomainError) as rejected:discovery.app.state.accommodations.execute_resolution(job,old)
    assert rejected.value.code=='LEASE_LOST' and provider.calls==0
    result=discovery.app.state.accommodations.execute_resolution(successor,JobContext(jobs,successor,'replacement-worker'))
    assert result['identity_state']=='candidates' and provider.calls==1


def test_restore_newer_stay_tombstone_erases_older_backup_label(discovery,tmp_path):
    from src.operations.backup import create_archive,write_checkpoint,restore_archive
    from src.foundation.db import Database
    import os
    if discovery.app.state.db.backend=='postgres':pytest.skip('Online SQLite archive; cloud archive roundtrip has its dedicated suite')
    t=trip_for(discovery.client);s=create(discovery.client,t,input_value='Original label in an older backup')
    key=os.urandom(32);archive=tmp_path/'stay-snapshot.enc';checkpoint=tmp_path/'stay-deleted.enc'
    create_archive(discovery.settings,archive,key)
    assert discovery.client.delete(base(t)+'/'+s['id'],params={'expected_version':s['version']}).status_code==200
    write_checkpoint(discovery.app.state.db,checkpoint,key)
    restored=tmp_path/'isolated-stay-restore'
    report=restore_archive(archive,checkpoint,restored,key)
    assert report['state']=='restored_closed_for_validation'
    with Database(restored/'database.sqlite3').connect() as con:
        row=con.execute('SELECT * FROM trip_accommodations WHERE id=?',(s['id'],)).fetchone()
        assert row['deleted_at'] and row['input_value']=='' and row['identity_json']=='{}'
        assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==0


def test_pending_resolution_binds_additional_idempotency_key(discovery):
    provider=fake(discovery);t=trip_for(discovery.client);s=create(discovery.client,t)
    job,ctx=claim_identification(discovery,t,s,key='original-inflight-key')
    path=base(t)+'/'+s['id']+'/resolve'
    r=discovery.client.post(path,json={'expected_version':1},headers={'Idempotency-Key':'second-inflight-key'})
    assert r.status_code==202 and r.json()['job_id']==job['id']
    changed=discovery.client.post(path,json={'expected_version':1,'max_candidates':1},headers={'Idempotency-Key':'second-inflight-key'})
    assert changed.status_code==409 and changed.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
    assert provider.calls==0
