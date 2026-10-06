from datetime import datetime,timedelta,timezone
import json
from tests.test_foundation_api import service,_trip
from tests.test_discovery_foundation import discovery
from tests.test_itinerary_api import planning,prepare,submit,done


def test_today_minimal_bundle_permission_namespace_and_ownership(planning):
    planning.settings.offline_enabled=True
    trip,pack,pid,reserved=prepare(planning,booking=True)
    itinerary,path=done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    base=f"/api/v2/trips/{trip['id']}"
    today=planning.client.get(base+'/today?day=2026-11-06').json()
    assert today['date']=='2026-11-06' and today['timezone']=='Asia/Tokyo'
    assert today['location_required'] is False
    assert all(i['recommended_departure'] is None for i in today['items'] if not i['travel'] or i['travel']['duration_minutes'] is None)
    payload={'private_device_confirmed':True}
    response=planning.client.post(base+'/offline-bundles',json=payload);assert response.status_code==200,response.text
    bundle=response.json();assert bundle['read_only'] and not bundle['live_verification']
    serialized=json.dumps(bundle)
    for forbidden in ['PRIVATE-BOOKING-SECRET','PRIVATE-RAW-MAIL','confirmation_number','raw_snippet','csrf_token','source_documents','raw_reviews']:
        assert forbidden not in serialized
    place=next(i for i in bundle['schedules'][0]['items'] if i.get('place_id')==pid)
    assert place['name']=='저장된 방문 장소' and place['address'] is None
    source=planning.client.get(base+'/places/'+pid+'/detail').json()['sources'][0]
    result=planning.client.post('/api/v2/admin/offline-source-permissions',json={'source_id':source['id'],'source_version':source['version'],'policy_version':source['policy_version'],'fields':['name','native_name','address'],'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()})
    assert result.status_code==200,result.text
    second=planning.client.post(base+'/offline-bundles',json=payload).json()
    assert second['manifest']!=bundle['manifest']
    assert next(i for i in second['schedules'][0]['items'] if i.get('place_id')==pid)['address']
    assert second['namespace']==planning.admin.user['id']
    assert planning.client.post(base+'/offline-bundles',json={'private_device_confirmed':False}).status_code==422
    other=planning.login('other')
    assert other.client.post(base+'/offline-bundles',json=payload).status_code==404
    assert other.client.get(base+'/today').status_code==404
    assert planning.client.get(base+'/today?itinerary_id=other').status_code==404
    planning.settings.offline_enabled=False
    assert planning.client.post(base+'/offline-bundles',json=payload).status_code==404


def test_offline_manifest_rejects_changed_permissions_notes_and_deleted_trip(planning):
    planning.settings.offline_enabled=True
    trip,pack,pid,_=prepare(planning)
    done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    base=f"/api/v2/trips/{trip['id']}"
    payload={'private_device_confirmed':True};b=planning.client.post(base+'/offline-bundles',json=payload).json()
    check={'manifest':b['manifest'],'include_notes':False}
    assert planning.client.post(base+'/offline-bundles/validate',json=check).json()['valid']
    note=planning.client.post(base+'/bookmarks',json={'input_kind':'name','input_value':'Private idea','note':'ONLY-WITH-OPT-IN'})
    assert note.status_code==201,note.text
    no_notes=planning.client.post(base+'/offline-bundles',json=payload).json()
    assert no_notes['notes']==[]
    with_notes=planning.client.post(base+'/offline-bundles',json={**payload,'include_notes':True}).json()
    assert with_notes['notes']==[{'note':'ONLY-WITH-OPT-IN'}]
    source=planning.client.get(base+'/places/'+pid+'/detail').json()['sources'][0]
    permission={'source_id':source['id'],'source_version':source['version'],'policy_version':source['policy_version'],'fields':['name'],'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    assert planning.client.post('/api/v2/admin/offline-source-permissions',json=permission).status_code==200
    assert planning.client.post(base+'/offline-bundles/validate',json=check).json()['valid'] is False
    deletion=planning.client.delete(base);assert deletion.status_code==202,deletion.text
    assert planning.client.post(base+'/offline-bundles/validate',json=check).status_code==404
    assert planning.client.get(base+'/reservation-tasks').status_code==404


def test_bundle_race_with_trip_delete_never_returns_private_payload(planning,monkeypatch):
    planning.settings.offline_enabled=True
    trip,pack,pid,_=prepare(planning)
    service=planning.app.state.today;original=service.content
    def race(*args,**kwargs):
        result=original(*args,**kwargs)
        planning.app.state.repo.delete_trip(planning.admin.user['id'],trip['id'])
        return result
    monkeypatch.setattr(service,'content',race)
    response=planning.client.post(f"/api/v2/trips/{trip['id']}/offline-bundles",json={'private_device_confirmed':True})
    assert response.status_code==404,response.text
