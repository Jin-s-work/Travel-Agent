"""Persistent itinerary generation/edit contracts with fake metered routing."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import threading

import pytest

from tests.test_foundation_api import service,_trip,_job
from tests.test_discovery_foundation import discovery,import_pack
from tests.discovery_synthetic import conditions
from tests.test_itinerary_travel import configure
from tests.test_reliability_api import eventually
from src.itineraries.travel_time import TravelTime
from src.reliability.providers import ProviderResult


class VerifiedRoutes:
    name='synthetic_routes';sku='matrix';adapter_version='fixture-v1';policy_version='fixture-permitted';usage_permitted=True
    def __init__(self):self.calls=0
    def lookup(self,request,timeout_seconds):
        self.calls+=1;now=datetime.now(timezone.utc)
        return ProviderResult({'duration_minutes':10,'distance_meters':100,
            'checked_at':now.isoformat(),'expires_at':(now+timedelta(days=1)).isoformat()}, {'matrix_elements':1})


@pytest.fixture
def planning(discovery):
    configure(discovery,limit=10000000)
    routes=VerifiedRoutes()
    discovery.app.state.itineraries.travel=TravelTime(discovery.app.state.gateway,discovery.app.state.jobs,routes)
    discovery.routes=routes
    return discovery


def prepare(planning,booking=False):
    pack=import_pack(planning);trip=_trip(planning.client)
    reserved=None
    if booking:
        res=planning.client.post(f"/api/v2/trips/{trip['id']}/bookings",json={
            'kind':'tour','provider':'Synthetic fixed tour','confirmation_number':'PRIVATE-BOOKING-SECRET',
            'raw_snippet':'PRIVATE-RAW-MAIL','status':'user_confirmed','date':'2026-11-06',
            'events':[{'event_type':'visit','start_local':'2026-11-06T15:00:00','end_local':'2026-11-06T16:00:00',
                       'start_timezone':'Asia/Tokyo','end_timezone':'Asia/Tokyo','location':'Synthetic museum'}]})
        assert res.status_code==201,res.text
        reserved=res.json();trip=planning.client.get(f"/api/v2/trips/{trip['id']}").json()
    response=planning.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",json={'expected_version':0,'conditions':conditions()})
    assert response.status_code==200,response.text
    selected=pack['places'][0]['place_id']
    return trip,pack,selected,reserved


def submit(planning,trip,selected_place,key='itinerary-intent',**changes):
    body={'trip_version':trip['version'],'conditions_version':1,'start_date':'2026-11-06','end_date':'2026-11-06',
          'selected':[{'place_id':selected_place,'duration_minutes':60,'duration_origin':'user'}] if selected_place else [],
          'buffers':{'general_minutes':0},'rest_preferences':{'minutes':0},**changes}
    return planning.client.post(f"/api/v2/trips/{trip['id']}/itineraries",json=body,headers={'Idempotency-Key':key})


def done(planning,trip,response):
    assert response.status_code==202,response.text
    job=_job(planning.client,response.json())
    assert job['state'] in ('succeeded','partial'),job
    path=f"/api/v2/trips/{trip['id']}/itineraries/{response.json()['itinerary_id']}"
    result=planning.client.get(path)
    assert result.status_code==200,result.text
    assert result.json()['active_revision_id'],result.text
    return result.json(),path


def preview(planning,path,version,commands):
    result=planning.client.post(path+'/edit-previews',json={'expected_version':version,'commands':commands})
    assert result.status_code==200,result.text
    return result.json()


def apply(planning,path,value):
    return planning.client.patch(path,json={'expected_version':value['base_version'],'preview_id':value['preview_id']})


def test_generation_persists_snapshot_items_route_cost_and_redacts_booking_secrets(planning):
    trip,pack,selected,reserved=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,selected,allow_provisional=True))
    assert result['version']==1 and result['snapshot']['planning_scope']=='single_city_stop'
    assert result['snapshot']['party']['adults']==4
    assert result['data_status']==result['input_status']=='current'
    assert any(i.get('place_id')==selected for i in result['items']),result['unplaced']
    fixed=next(i for i in result['items'] if i.get('booking_id')==reserved['id'])
    assert fixed['locked'] and fixed['lock_origin']=='booking' and fixed['reservation_status']=='user_confirmed'
    assert result['legs']
    assert any(leg['basis']=='unknown' and leg['duration_minutes'] is None for leg in result['legs'])
    assert result['validation_status']=='provisional'
    assert result['route_usage']['provider_calls']>0
    assert planning.client.get(path).json()['items']==result['items']
    assert planning.client.get(f"/api/v2/trips/{trip['id']}/itineraries").json()['items'][0]['id']==result['id']
    with planning.app.state.db.connect() as con:
        stored=''.join(str(tuple(row)) for table in ('itineraries','itinerary_revisions','jobs','job_events') for row in con.execute('SELECT * FROM '+table))
        assert 'PRIVATE-RAW-MAIL' not in stored and 'PRIVATE-BOOKING-SECRET' not in stored
        assert con.execute("SELECT COUNT(*) FROM usage_reservations WHERE operation='route_matrix'").fetchone()[0]>0
    assert 'no-store' in planning.client.get(path).headers['cache-control']


def test_concurrent_generation_idempotency_and_changed_payload_conflict(planning):
    trip,_,selected,_=prepare(planning)
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(lambda _:submit(planning,trip,selected),range(4)))
    assert all(r.status_code==202 for r in responses),[r.text for r in responses]
    assert len({r.json()['itinerary_id'] for r in responses})==1
    done(planning,trip,responses[0])
    changed=submit(planning,trip,selected,allow_provisional=True)
    assert changed.status_code==409 and changed.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
    with planning.app.state.db.connect() as con:assert con.execute('SELECT COUNT(*) FROM itineraries').fetchone()[0]==1


def test_other_user_and_other_trip_cannot_read_edit_undo_or_watch(planning):
    trip,_,selected,_=prepare(planning)
    result,path=done(planning,trip,submit(planning,trip,selected))
    other=planning.login('itinerary-B');othertrip=_trip(other.client);ownsecond=_trip(planning.client)
    assert any(i.get('place_id') for i in result['items']),result['unplaced']
    item=next(i for i in result['items'] if i.get('place_id'))
    for endpoint in (path,path+'/edit-previews',path+'/undo-previews',path+'/edit-intents'):
        if endpoint==path:assert other.client.get(endpoint).status_code==404
        elif endpoint.endswith('edit-previews'):assert other.client.post(endpoint,json={'expected_version':1,'commands':[{'op':'lock','item_id':item['item_id']}]}).status_code==404
        elif endpoint.endswith('undo-previews'):assert other.client.post(endpoint,json={'expected_version':1}).status_code==404
        else:assert other.client.post(endpoint,json={'expected_version':1,'text':'삭제','item_id':item['item_id']}).status_code==404
    assert planning.client.get(path.replace(trip['id'],ownsecond['id'])).status_code==404
    assert other.client.get(path.replace(trip['id'],othertrip['id'])).status_code==404
    for suffix in ('','/events'):assert other.client.get('/api/v2/jobs/'+result['job_id']+suffix).status_code==404
    assert other.client.post('/api/v2/jobs/'+result['job_id']+'/cancel').status_code==404


def test_preview_is_nonmutating_apply_versioned_and_same_apply_idempotent(planning):
    trip,_,selected,reserved=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,selected,allow_provisional=True))
    item=next(i for i in result['items'] if i.get('place_id'))
    before_book=planning.client.get(f"/api/v2/trips/{trip['id']}/bookings/{reserved['id']}").json()
    proposal=preview(planning,path,1,[{'op':'move','item_id':item['item_id'],'local_start':'2026-11-06T12:00'}])
    assert proposal['can_apply'],proposal
    assert planning.client.get(path).json()['items']==result['items']
    committed=apply(planning,path,proposal)
    assert committed.status_code==200,committed.text
    assert committed.json()['version']==2 and committed.json()['can_undo']
    moved=next(i for i in committed.json()['items'] if i['item_id']==item['item_id'])
    assert moved['local_start'].startswith('2026-11-06T12:00')
    assert apply(planning,path,proposal).json()['version']==2
    assert planning.client.get(f"/api/v2/trips/{trip['id']}/bookings/{reserved['id']}").json()==before_book


def test_locked_booking_cannot_be_unlocked_or_moved_by_plan_or_natural_language(planning):
    trip,_,_,reserved=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,None))
    item=next(i for i in result['items'] if i.get('booking_id')==reserved['id'])
    for cmd in ({'op':'unlock','item_id':item['item_id']},{'op':'remove','item_id':item['item_id']},
                {'op':'move','item_id':item['item_id'],'local_start':'2026-11-06T16:00'}):
        res=planning.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[cmd]})
        assert res.status_code==422 and res.json()['error']['code']=='BOOKING_EDIT_REQUIRED'
    assert planning.client.post(path+'/edit-intents',json={'expected_version':1,'item_id':item['item_id'],'text':'잠금 해제'}).status_code==422
    assert planning.client.get(path).json()['version']==1


def test_ten_edits_and_ten_undos_append_revisions_without_rewinding_versions(planning):
    trip,_,selected,_=prepare(planning)
    current,path=done(planning,trip,submit(planning,trip,selected))
    item=next(i for i in current['items'] if i.get('place_id'))
    for n in range(10):
        proposal=preview(planning,path,current['version'],[{'op':'lock' if n%2==0 else 'unlock','item_id':item['item_id']}])
        response=apply(planning,path,proposal)
        assert response.status_code==200,response.text
        current=response.json()
    assert current['version']==11 and len(current['revisions'])==11
    for _ in range(10):
        response=planning.client.post(path+'/undo-previews',json={'expected_version':current['version'],'steps':1})
        assert response.status_code==200,response.text
        proposal=response.json();assert proposal['can_apply'],proposal
        applied=apply(planning,path,proposal)
        assert applied.status_code==200,applied.text
        current=applied.json()
    assert current['version']==21 and not current['can_undo']
    assert next(i for i in current['items'] if i['item_id']==item['item_id'])['locked'] is False
    assert planning.client.post(path+'/undo-previews',json={'expected_version':21}).status_code==422


def test_expired_preview_and_simultaneous_edit_do_not_overwrite(planning):
    trip,_,selected,_=prepare(planning)
    result,path=done(planning,trip,submit(planning,trip,selected));iid=next(i['item_id'] for i in result['items'] if i.get('place_id'))
    expired=preview(planning,path,1,[{'op':'lock','item_id':iid}])
    with planning.app.state.db.connect() as con:con.execute("UPDATE itinerary_previews SET expires_at='2000-01-01' WHERE id=?",(expired['preview_id'],))
    response=apply(planning,path,expired)
    assert response.status_code==409 and response.json()['error']['code']=='PREVIEW_EXPIRED'
    a=preview(planning,path,1,[{'op':'move','item_id':iid,'local_start':'2026-11-06T12:00'}])
    b=preview(planning,path,1,[{'op':'move','item_id':iid,'local_start':'2026-11-06T13:00'}])
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda proposal:apply(planning,path,proposal),[a,b]))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert planning.client.get(path).json()['version']==2


@pytest.mark.parametrize('change',['source','route_policy','conditions','booking'])
def test_changed_source_policy_or_input_between_preview_and_apply_preserves_plan(planning,change):
    trip,pack,selected,booking=prepare(planning,booking=change=='booking')
    result,path=done(planning,trip,submit(planning,trip,selected,allow_provisional=change=='booking'));iid=next(i['item_id'] for i in result['items'] if i.get('place_id'))
    proposal=preview(planning,path,1,[{'op':'lock','item_id':iid}])
    if change=='source':
        source=pack['places'][0]['sources'][0]
        response=planning.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={'expected_version':2,
            'status':'revoked','read_confirmed':True,'display_permitted':False,'policy_version':pack['version'],'evidence':'Synthetic source use withdrawn'})
        assert response.status_code==200,response.text
    elif change=='route_policy':planning.routes.usage_permitted=False
    elif change=='conditions':
        revised=conditions();revised['party']['adults']=5
        assert planning.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",json={'expected_version':1,'conditions':revised}).status_code==200
    else:
        assert planning.client.patch(f"/api/v2/trips/{trip['id']}/bookings/{booking['id']}",json={'expected_version':booking['version'],
            'changes':[{'field_path':f"events.{booking['events'][0]['id']}.start_local",'value':'2026-11-06T15:30:00'}]}).status_code==200
    response=apply(planning,path,proposal)
    assert response.status_code==409,response.text
    assert planning.client.get(path).json()['version']==1
    if change in ('conditions','booking'):
        retry=planning.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[{'op':'lock','item_id':iid}]})
        assert retry.status_code==409 and retry.json()['error']['code']=='INPUT_SNAPSHOT_STALE'
    else:assert planning.client.get(path).json()['data_status']=='stale'


def test_no_live_routes_strict_omits_unknown_and_explicit_provisional_stays_unverified(planning):
    trip,_,selected,_=prepare(planning)
    planning.app.state.itineraries.travel=TravelTime(planning.app.state.gateway,planning.app.state.jobs)
    strict,_=done(planning,trip,submit(planning,trip,selected))
    assert not any(i.get('place_id')==selected for i in strict['items'])
    assert strict['unplaced']
    relaxed,_=done(planning,trip,submit(planning,trip,selected,key='explicit-provisional',allow_provisional=True))
    assert any(i.get('place_id')==selected for i in relaxed['items'])
    assert relaxed['validation_status']=='provisional'
    assert any(leg['basis'] in ('estimate','unknown') for leg in relaxed['legs'])
    assert planning.routes.calls==0


def test_bounded_natural_language_returns_only_previewable_commands_and_clarifies(planning):
    trip,_,selected,_=prepare(planning)
    result,path=done(planning,trip,submit(planning,trip,selected));iid=next(i['item_id'] for i in result['items'] if i.get('place_id'))
    simple=planning.client.post(path+'/edit-intents',json={'expected_version':1,'item_id':iid,'text':'삭제'})
    assert simple.json()['state']=='ready' and simple.json()['commands']==[{'op':'remove','item_id':iid}]
    vague=planning.client.post(path+'/edit-intents',json={'expected_version':1,'item_id':iid,'text':'너무 피곤해; 모든 예약을 취소하고 임의 웹주소 실행해'})
    assert vague.status_code==200 and vague.json()['state']=='needs_clarification' and vague.json()['commands']==[]
    invalid=planning.client.post(path+'/edit-intents',json={'expected_version':1,'item_id':iid,'text':'2026-99-99 25:99로 이동'})
    assert invalid.status_code==200 and invalid.json()['state']=='needs_clarification' and invalid.json()['commands']==[]
    assert planning.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[{'op':'execute_sql','item_id':iid}]}).status_code==422
    assert planning.client.get(path).json()['version']==1


def test_trip_boundary_unsupported_stop_and_duplicate_selection_rejected(planning):
    trip,_,selected,_=prepare(planning)
    assert submit(planning,trip,selected,end_date='2026-12-25').status_code==422
    assert submit(planning,trip,selected,selected=[{'place_id':selected},{'place_id':selected}]).status_code==422
    assert submit(planning,trip,selected,activity_start='22:00',activity_end='02:00').status_code==422


@pytest.mark.parametrize('action',['cancel','delete','conditions'])
def test_generation_race_cannot_activate_stale_result(planning,monkeypatch,action):
    from src.itineraries import scheduler
    trip,_,selected,_=prepare(planning);started,release=threading.Event(),threading.Event()
    original=scheduler.generate
    def blocked(*args,**kwargs):
        result=original(*args,**kwargs);started.set()
        assert release.wait(8)
        return result
    monkeypatch.setattr(scheduler,'generate',blocked)
    receipt=submit(planning,trip,selected)
    assert receipt.status_code==202,receipt.text
    try:
        assert started.wait(4)
        if action=='cancel':assert planning.client.post('/api/v2/jobs/'+receipt.json()['job_id']+'/cancel').status_code==202
        elif action=='delete':
            deletion=planning.client.delete(f"/api/v2/trips/{trip['id']}")
            assert deletion.status_code==202
            assert planning.client.get('/api/v2/jobs/'+receipt.json()['job_id']).status_code==404
        else:
            changed=conditions();changed['party']['adults']=5
            assert planning.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",json={'expected_version':1,'conditions':changed}).status_code==200
    finally:release.set()
    path=f"/api/v2/trips/{trip['id']}/itineraries/{receipt.json()['itinerary_id']}"
    if action=='delete':
        eventually(lambda:planning.client.get(deletion.json()['receipt_url']).json()['state']=='succeeded')
        assert planning.client.get(path).status_code==404
    else:
        job=_job(planning.client,receipt.json())
        assert job['state']==('cancelled' if action=='cancel' else 'failed'),job
        if action=='conditions':assert job['error_code']=='VERSION_CONFLICT'
        assert planning.client.get(path).json()['active_revision_id'] is None
    with planning.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM itinerary_revisions WHERE itinerary_id=?',(receipt.json()['itinerary_id'],)).fetchone()[0]==0


def test_booking_correction_read_marks_old_item_and_current_source_then_regenerate_required(planning):
    trip,_,_,booking=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,None))
    before=next(i for i in result['items'] if i.get('booking_id')==booking['id'])
    changed=planning.client.patch(f"/api/v2/trips/{trip['id']}/bookings/{booking['id']}",json={'expected_version':booking['version'],
        'changes':[{'field_path':f"events.{booking['events'][0]['id']}.start_local",'value':'2026-11-06T15:30:00'}]})
    assert changed.status_code==200,changed.text
    after=planning.client.get(path).json()
    item=next(i for i in after['items'] if i.get('booking_id')==booking['id'])
    assert after['input_status']==after['data_status']=='stale'
    assert item['verification_status']=='stale' and item['source_status']=='changed'
    assert item['local_start']==before['local_start']
    assert item['current_booking']['events'][0]['start_local']=='2026-11-06T15:30:00'
    assert any(u['code']=='SOURCE_DATA_CHANGED' for u in after['unresolved_conditions'])


def test_conflicting_preview_is_not_applied_even_in_provisional_mode(planning):
    trip,_,selected,_=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,selected,allow_provisional=True))
    iid=next(i['item_id'] for i in result['items'] if i.get('place_id'))
    proposed=preview(planning,path,1,[{'op':'move','item_id':iid,'local_start':'2026-11-06T15:00'}])
    assert not proposed['can_apply'] and proposed['conflicts']
    response=apply(planning,path,proposed)
    assert response.status_code==409 and response.json()['error']['code']=='EDIT_CONFLICT'
    assert planning.client.get(path).json()['items']==result['items']


def test_command_rejection_cannot_be_erased_by_apply_revalidation(planning):
    trip,_,selected,_=prepare(planning)
    result,path=done(planning,trip,submit(planning,trip,selected))
    proposal=preview(planning,path,1,[{'op':'add','place_id':selected,'local_start':'2026-11-06T13:00','duration_minutes':60}])
    assert not proposal['can_apply'] and any(c['code']=='DUPLICATE_PLACE' for c in proposal['conflicts'])
    response=apply(planning,path,proposal)
    assert response.status_code==409 and response.json()['error']['code']=='EDIT_CONFLICT'
    assert planning.client.get(path).json()['version']==1


def test_client_origin_place_id_is_not_proof_of_zero_travel(planning):
    trip,_,selected,_=prepare(planning)
    value=conditions();value['origin']={'label':'Unverified client origin','place_id':selected}
    response=planning.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",json={'expected_version':1,'conditions':value})
    assert response.status_code==200,response.text
    result,_=done(planning,trip,submit(planning,trip,selected,conditions_version=2))
    assert result['snapshot']['origin']['place_id'] is None
    assert not any(i.get('place_id')==selected for i in result['items'])
    assert result['unplaced'] and planning.routes.calls==0
