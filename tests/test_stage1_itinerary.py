"""Preview-first creation and public-map itinerary eligibility regressions."""
import json
import pytest
from tests.test_foundation_api import service, _job
from tests.test_discovery_foundation import discovery
from tests.test_itinerary_api import planning, prepare
from tests.test_public_discovery import public, prepare as public_prepare, run


def body(trip,place=None,**changes):
    return {'trip_version':trip['version'],'conditions_version':1,'start_date':'2026-11-06','end_date':'2026-11-06',
        'selected':[{'place_id':place,'duration_minutes':60}] if place else [],'allow_provisional':True,
        'buffers':{'general_minutes':0},'rest_preferences':{'minutes':0},**changes}


def generation_preview(client,base,payload,key='stage1-preview'):
    response=client.post(base+'/itineraries/generation-previews',json=payload,headers={'Idempotency-Key':key})
    assert response.status_code==202,response.text
    assert _job(client,response.json())['state'] in ('succeeded','partial')
    path=base+'/itineraries/'+response.json()['itinerary_id']
    preview=client.get(path+'/generation-preview')
    assert preview.status_code==200,preview.text
    return path,preview.json()


def test_generation_preview_is_inactive_until_apply_and_apply_idempotent(planning):
    trip,_,place,reserved=prepare(planning,booking=True);base=f"/api/v2/trips/{trip['id']}"
    before=planning.client.get(base+'/bookings/'+reserved['id']).json()
    path,preview=generation_preview(planning.client,base,body(trip,place))
    assert preview['can_apply'] and preview['base_version']==0
    assert planning.client.get(base+'/itineraries').json()['items']==[]
    assert planning.client.get(path).json()['active_revision_id'] is None
    assert planning.client.get(base+'/bookings/'+reserved['id']).json()==before
    payload={'expected_version':0,'preview_id':preview['preview_id']}
    response=planning.client.post(path+'/generation-preview/apply',json=payload)
    assert response.status_code==200,response.text
    applied=response.json();assert applied['version']==1 and applied['active_revision_id']
    assert planning.client.post(path+'/generation-preview/apply',json=payload).json()['version']==1
    assert len(planning.client.get(base+'/itineraries').json()['items'])==1
    assert planning.client.get(path).json()['items']==applied['items']
    item=next(i for i in applied['items'] if i.get('place_id'))
    p=planning.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[{'op':'lock','item_id':item['item_id']}]}).json()
    assert planning.client.patch(path,json={'expected_version':1,'preview_id':p['preview_id']}).json()['version']==2
    undo=planning.client.post(path+'/undo-previews',json={'expected_version':2}).json()
    assert planning.client.patch(path,json={'expected_version':2,'preview_id':undo['preview_id']}).json()['version']==3
    assert planning.client.get(base+'/bookings/'+reserved['id']).json()==before


def test_generation_preview_ownership_versions_expiry_and_deleted_trip(planning):
    trip,_,place,_=prepare(planning);base=f"/api/v2/trips/{trip['id']}"
    path,preview=generation_preview(planning.client,base,body(trip,place))
    payload={'expected_version':0,'preview_id':preview['preview_id']}
    other=planning.login('preview-B')
    assert other.client.get(path+'/generation-preview').status_code==404
    assert other.client.post(path+'/generation-preview/apply',json=payload).status_code==404
    with planning.app.state.db.connect() as con:
        con.execute("UPDATE itinerary_generation_drafts SET expires_at='2000-01-01T00:00:00+00:00'")
    assert not planning.client.get(path+'/generation-preview').json()['can_apply']
    assert planning.client.post(path+'/generation-preview/apply',json=payload).status_code==409
    path,new=generation_preview(planning.client,base,body(trip,place),'second-preview')
    assert planning.client.patch(base,json={'expected_version':trip['version'],'title':'Changed'}).status_code==200
    assert planning.client.post(path+'/generation-preview/apply',json={'expected_version':0,'preview_id':new['preview_id']}).status_code==409
    assert planning.client.get(base+'/itineraries').json()['items']==[]
    assert planning.client.delete(base).status_code==202
    assert planning.client.get(path+'/generation-preview').status_code==404


def test_public_candidate_strict_unplaced_provisional_generation_and_add_share_validator(public):
    trip,base=public_prepare(public)
    output=run(public,trip,base);places=[p['place_id'] for p in output['result']['sections']['reference']['needs_confirmation']]
    assert len(places)>=2
    strict_path,strict=generation_preview(public.client,base,body(trip,places[0],allow_provisional=False),'strict-preview')
    assert not any(i.get('place_id')==places[0] for i in strict['result']['items'])
    assert strict['result']['unplaced']
    path,provisional=generation_preview(public.client,base,body(trip,places[0]),'provisional-preview')
    assert provisional['can_apply'],provisional
    assert any(i.get('place_id')==places[0] for i in provisional['result']['items']),provisional
    assert provisional['result']['validation_status']=='provisional'
    applied=public.client.post(path+'/generation-preview/apply',json={'expected_version':0,'preview_id':provisional['preview_id']})
    assert applied.status_code==200,applied.text
    add=public.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[{'op':'add','place_id':places[1],'duration_minutes':60,'local_start':'2026-11-06T16:00'}]})
    assert add.status_code==200,add.text
    assert add.json()['can_apply'],add.text
    assert all(i['name']!='사용할 수 없는 장소' for i in add.json()['items'] if i.get('place_id'))
    with public.app.state.db.connect() as con:
        assert con.execute('SELECT identity_status FROM place_identities WHERE id=?',(places[0],)).fetchone()[0]=='needs_confirmation'
        assert con.execute("SELECT count(*) FROM place_facts WHERE status='verified'").fetchone()[0]==0
        con.execute("UPDATE evidence_sources SET status='withdrawn' WHERE place_id=?",(places[0],))
    rejected=public.client.patch(path,json={'expected_version':1,'preview_id':add.json()['preview_id']})
    assert rejected.status_code==409,rejected.text
    assert public.client.get(path).json()['version']==1


def test_public_provisional_never_relaxes_confirmed_closure_party_or_ambiguous_identity():
    from copy import deepcopy
    from tests.test_itinerary_engine import request, catalog, Routes, NOW, generate, place_items, fact
    source=catalog()[0]
    source.update(source_kind='public_map',provider='openstreetmap',pack_status='public_data',identity_status='needs_confirmation',identity_basis='provider_location',coordinate_permitted=True)
    for change in ('closed','party','ambiguous','withdrawn'):
        row=deepcopy(source)
        if change=='closed':fact(row,'closed')['value']=True
        if change=='party':fact(row,'max_party')['value']=1
        if change=='ambiguous':row.pop('identity_basis')
        if change=='withdrawn':
            for item in row['sources']:item['display_permitted']=False
        result=generate(request(allow_provisional=True),[],[row],Routes(),NOW)
        assert not place_items(result),(change,result)


def assert_revoked_endpoints_hidden(before,after,unavailable_ids):
    """Both travel directions redact only endpoints with revoked provenance."""
    hidden=0;preserved=0
    assert len(before['legs'])==len(after['legs'])
    for old,new in zip(before['legs'],after['legs']):
        for side in ('from','to'):
            assert old.get(side+'_item_id')==new.get(side+'_item_id')
            endpoint=new.get(side+'_endpoint')
            if new.get(side+'_item_id') in unavailable_ids:
                hidden+=1
                assert endpoint['coordinate_permitted'] is False and endpoint['unavailable'] is True
                assert not {'latitude','longitude','label'} & endpoint.keys()
            else:
                preserved+=1
                assert endpoint==old.get(side+'_endpoint')
    assert hidden and preserved


def test_generation_preview_redacts_withdrawn_public_place_and_inherited_rest_endpoints(public):
    trip,base=public_prepare(public)
    output=run(public,trip,base)
    places=[p['place_id'] for p in output['result']['sections']['reference']['needs_confirmation']]
    path,preview=generation_preview(public.client,base,body(trip,
        selected=[{'place_id':place,'duration_minutes':60} for place in places[:2]],
        rest_preferences={'minutes':15,'after_visits':1}),'withdrawn-public-preview')
    assert preview['can_apply'],preview
    before=preview['result'];place_item=next(i for i in before['items'] if i.get('place_id')==places[0])
    assert place_item['location']['coordinate_permitted']
    rest=next(i for i in before['items'] if i.get('parent_item_id')==place_item['item_id'])
    with public.app.state.db.connect() as con:
        con.execute("UPDATE evidence_sources SET status='withdrawn',display_permitted=0 WHERE place_id=?",(places[0],))
    stale=public.client.get(path+'/generation-preview').json()
    assert stale['state']=='stale' and not stale['can_apply']
    hidden={place_item['item_id'],rest['item_id']}
    assert all('location' not in item for item in stale['result']['items'] if item['item_id'] in hidden)
    assert_revoked_endpoints_hidden(before,stale['result'],hidden)
    assert public.client.post(path+'/generation-preview/apply',json={'expected_version':0,'preview_id':preview['preview_id']}).status_code==409
    assert public.client.get(path).json()['active_revision_id'] is None


@pytest.mark.parametrize('change',['source','booking'])
def test_generation_preview_redacts_revoked_approved_or_deleted_booking_endpoints_only(planning,change):
    trip,pack,place,booking=prepare(planning,booking=True);base=f"/api/v2/trips/{trip['id']}"
    selected=[{'place_id':place,'duration_minutes':60},{'place_id':pack['places'][1]['place_id'],'duration_minutes':60}]
    path,preview=generation_preview(planning.client,base,body(trip,selected=selected),'withdrawn-private-preview')
    assert preview['can_apply'],preview
    before=preview['result']
    assert any(leg.get('from_item_id') is None and leg['from_endpoint'].get('latitude') is not None for leg in before['legs'])
    if change=='source':
        hidden={i['item_id'] for i in before['items'] if i.get('place_id')==place}
        with planning.app.state.db.connect() as con:
            con.execute("UPDATE evidence_sources SET status='withdrawn',display_permitted=0 WHERE place_id=?",(place,))
    else:
        hidden={i['item_id'] for i in before['items'] if i.get('booking_id')==booking['id']}
        assert planning.client.delete(base+'/bookings/'+booking['id']).status_code==202
    stale=planning.client.get(path+'/generation-preview').json()
    assert stale['state']=='stale' and not stale['can_apply']
    assert all(not item.get('location') for item in stale['result']['items'] if item['item_id'] in hidden)
    assert_revoked_endpoints_hidden(before,stale['result'],hidden)
    assert planning.client.post(path+'/generation-preview/apply',json={'expected_version':0,'preview_id':preview['preview_id']}).status_code==409
    assert planning.client.get(path).json()['active_revision_id'] is None


def test_safe_view_clears_deleted_booking_coordinates_without_mutating_stored_result():
    from copy import deepcopy
    from src.itineraries.service import Itineraries
    origin={'id':'origin','label':'Synthetic origin','latitude':35.0,'longitude':139.0,'coordinate_permitted':True}
    booking={'id':'booking-event','label':'Synthetic booking location','latitude':35.1,'longitude':139.1,'coordinate_permitted':True}
    result={'items':[{'item_id':'fixed','booking_id':'deleted-booking','location':booking}],
        'legs':[{'from_item_id':None,'to_item_id':'fixed','from_endpoint':origin,'to_endpoint':booking},
                {'from_item_id':'fixed','to_item_id':None,'from_endpoint':booking,'to_endpoint':origin}]}
    stored=deepcopy(result)
    safe=Itineraries._safe_view(result,{'candidates':[],'bookings':[]})
    assert_revoked_endpoints_hidden(result,safe,{'fixed'})
    assert result==stored
