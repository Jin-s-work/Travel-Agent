"""Discovery foundation: owner isolation, asynchronous resolution, source review."""
import copy
import json
import time
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from tests.test_foundation_api import service,_trip,_job
from tests.discovery_synthetic import pack,conditions


@pytest.fixture
def discovery(service):
    admin=service.login('discovery-admin')
    with service.app.state.db.connect() as con:con.execute("UPDATE users SET role='admin' WHERE id=?",(admin.user['id'],))
    return SimpleNamespace(**vars(service),admin=admin,client=admin.client)


def import_pack(discovery,data=None,approve=True):
    data=data or pack()
    response=discovery.client.post('/api/v2/admin/discovery-packs',json=data)
    assert response.status_code==201,response.text
    ident=response.json()['pack_id']
    stored=next(p for p in discovery.client.get('/api/v2/admin/discovery-packs').json()['items'] if p['id']==ident)
    if approve:
        for place in stored['places']:
            for source in place['sources']:
                res=discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={
                    'expected_version':source['version'],'status':'active','read_confirmed':True,'display_permitted':True,
                    'policy_version':data['version'],'evidence':'Synthetic source reviewed for integration fixture'})
                assert res.status_code==200,res.text
        res=discovery.client.patch('/api/v2/admin/discovery-packs/'+ident,json={'status':'approved','evidence':'Synthetic branch identity and evidence verified'})
        assert res.status_code==200,res.text
    return stored


def bookmark(client,trip,input_kind,input_value,note='private-note'):
    response=client.post(f"/api/v2/trips/{trip['id']}/bookmarks",json={'input_kind':input_kind,'input_value':input_value,'note':note})
    assert response.status_code==201,response.text
    return response.json()


def resolve(client,trip,value,key='resolve-one'):
    base=f"/api/v2/trips/{trip['id']}/bookmarks/{value['id']}"
    res=client.post(base+'/resolve',json={'expected_version':value['version']},headers={'Idempotency-Key':key})
    assert res.status_code==202,res.text
    job=_job(client,res.json())
    assert job['state']=='succeeded',job
    result=client.get(base)
    assert result.status_code==200,result.text
    return result.json(),res.json()


def test_conditions_reuse_trip_snapshot_version_and_preserve_on_conflict(discovery):
    trip=_trip(discovery.client)
    base=f"/api/v2/trips/{trip['id']}/discovery-conditions"
    initial=discovery.client.get(base).json()
    assert initial['version']==0 and initial['conditions']['party']==trip['party']
    value=conditions();value['party']['adults']=4
    saved=discovery.client.patch(base,json={'expected_version':0,'conditions':value})
    assert saved.status_code==200,saved.text
    assert saved.json()['version']==1 and saved.json()['conditions']['party']['adults']==4
    assert discovery.client.get(f"/api/v2/trips/{trip['id']}").json()['party']['adults']==2
    assert discovery.client.patch(base,json={'expected_version':0,'conditions':conditions()}).status_code==409
    assert discovery.client.get(base).json()['conditions']==saved.json()['conditions']
    outsider=discovery.login('other')
    assert outsider.client.get(base).status_code==404
    assert outsider.client.patch(base,json={'expected_version':1,'conditions':value}).status_code==404
    bad=copy.deepcopy(value);bad['visit']['date']='2026-11-20'
    assert discovery.client.patch(base,json={'expected_version':1,'conditions':bad}).status_code==422


def test_unsupported_city_is_not_implicitly_rewritten_to_tokyo(discovery):
    response=discovery.client.post('/api/v2/trips',json={'title':'Hakone','start_date':'2026-11-06','end_date':'2026-11-09',
        'stops':[{'city':'Hakone','sequence':1,'start_date':'2026-11-06','end_date':'2026-11-09','timezone':'Asia/Tokyo'}]})
    assert response.status_code==201,response.text
    base=f"/api/v2/trips/{response.json()['id']}/discovery-conditions"
    value=discovery.client.get(base).json()
    assert value['conditions']['city'] is None and value['city_needs_confirmation'] is True
    assert value['unsupported_cities']==['Hakone']
    assert discovery.client.patch(base,json={'expected_version':0,'conditions':conditions()}).status_code==422


def test_bookmark_storage_resolution_reload_and_duplicate_preserves_note(discovery):
    data=import_pack(discovery);trip=_trip(discovery.client)
    place=data['places'][0]
    saved=bookmark(discovery.client,trip,'url',place['canonical_url'],'original-private-note')
    assert saved['resolve_state']=='unresolved' and saved['matched_place_id'] is None
    duplicate=bookmark(discovery.client,trip,'url',place['canonical_url']+'?utm_source=test','overwrite-attempt')
    assert duplicate['id']==saved['id'] and duplicate['duplicate'] and duplicate['note']=='original-private-note'
    result,job=resolve(discovery.client,trip,saved)
    assert result['resolve_state']=='resolved' and result['matched_place_id']==place['place_id']
    replay=discovery.client.post(f"/api/v2/trips/{trip['id']}/bookmarks/{saved['id']}/resolve",json={'expected_version':saved['version']},headers={'Idempotency-Key':'resolve-one'})
    assert replay.json()['job_id']==job['job_id']
    duplicate=bookmark(discovery.client,trip,'place',place['place_id'],'overwrite-by-id')
    assert duplicate['id']==saved['id'] and duplicate['note']=='original-private-note'
    with discovery.app.state.db.connect() as con:
        persisted=con.execute('SELECT payload_json FROM jobs WHERE id=?',(job['job_id'],)).fetchone()[0]
        assert place['canonical_url'] not in persisted and 'original-private-note' not in persisted
    outsider=discovery.login('bookmark-other')
    path=f"/api/v2/trips/{trip['id']}/bookmarks/{saved['id']}"
    for method in ('GET','DELETE'):
        assert outsider.client.request(method,path).status_code==404
    assert outsider.client.get('/api/v2/jobs/'+job['job_id']).status_code==404


def test_ambiguous_same_chain_requires_selection_and_partial_match_not_auto_resolved(discovery):
    data=pack();data['places']=data['places'][:2]
    for p in data['places']:p['name']=p['native_name']='Same Chain'
    stored=import_pack(discovery,data);trip=_trip(discovery.client)
    saved=bookmark(discovery.client,trip,'name','Same Chain')
    result,_=resolve(discovery.client,trip,saved)
    assert result['resolve_state']=='ambiguous' and len(result['candidates'])==2
    chosen=result['candidates'][1]
    path=f"/api/v2/trips/{trip['id']}/bookmarks/{result['id']}/select"
    assert discovery.client.post(path,json={'expected_version':result['version'],'place_id':'arbitrary-id'}).status_code==404
    selected=discovery.client.post(path,json={'expected_version':result['version'],'place_id':chosen['place_id']})
    assert selected.status_code==200 and selected.json()['matched_place_id']==chosen['place_id']
    # A loose title fragment with one result still asks the user to confirm.
    one=pack('barcelona');one['places']=one['places'][:1];one['places'][0]['name']='Unique Partial Cafe';one['places'][0]['native_name']=None
    import_pack(discovery,one)
    result,_=resolve(discovery.client,trip,bookmark(discovery.client,trip,'name','Unique Partial'),key='partial-name')
    assert result['resolve_state']=='ambiguous'


def test_unknown_url_preserves_note_and_spoofed_place_id_not_resolved(discovery):
    stored=import_pack(discovery);trip=_trip(discovery.client)
    place=stored['places'][0]
    raw='https://example.net/?place_id=synthetic-tokyo-1'
    saved=bookmark(discovery.client,trip,'url',raw,'keep-this-note')
    result,_=resolve(discovery.client,trip,saved)
    assert result['resolve_state']=='unsupported' and result['note']=='keep-this-note' and result['input_value']==raw
    assert result['reason_codes']==['REMOTE_RESOLUTION_UNAVAILABLE']


def test_exclusions_are_private_per_trip_and_do_not_mutate_catalog(discovery):
    stored=import_pack(discovery);a=_trip(discovery.client);b=_trip(discovery.client,'Barcelona')
    pid=stored['places'][0]['place_id'];url=f"/api/v2/trips/{a['id']}/excluded-places/{pid}"
    assert discovery.client.post(url,json={}).json()['excluded'] is True
    first=discovery.client.get(f"/api/v2/trips/{a['id']}/discovery-catalog").json()['items']
    second=discovery.client.get(f"/api/v2/trips/{b['id']}/discovery-catalog").json()['items']
    assert next(p for p in first if p['place_id']==pid)['excluded'] is True
    assert next(p for p in second if p['place_id']==pid)['excluded'] is False
    assert discovery.client.delete(url).json()['excluded'] is False


def test_unreviewed_sources_cannot_be_published_and_source_revocation_hides_values(discovery):
    stored=import_pack(discovery,approve=False);trip=_trip(discovery.client)
    assert discovery.client.patch('/api/v2/admin/discovery-packs/'+stored['id'],json={'status':'approved','evidence':'Merely a URL is not a source rights review'}).status_code==422
    assert discovery.client.get(f"/api/v2/trips/{trip['id']}/discovery-catalog").json()['items']==[]
    # Activate each source and pack with a separate recorded operator decision.
    for place in stored['places']:
        for source in place['sources']:
            assert discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={'expected_version':1,'status':'active','read_confirmed':True,'display_permitted':True,'policy_version':stored['version'],'evidence':'Synthetic rights review only'}).status_code==200
    assert discovery.client.patch('/api/v2/admin/discovery-packs/'+stored['id'],json={'status':'approved','evidence':'Synthetic identity confirmed'}).status_code==200
    pid=stored['places'][0]['place_id'];source=stored['places'][0]['sources'][0]
    detail=f"/api/v2/trips/{trip['id']}/places/{pid}/detail"
    before=discovery.client.get(detail).json()
    assert next(f for f in before['facts'] if f['field']=='max_party')['value']==6
    response=discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={'expected_version':2,'status':'revoked','read_confirmed':True,'display_permitted':False,'policy_version':stored['version'],'evidence':'Synthetic rights withdrawn after test'})
    assert response.status_code==200,response.text
    after=discovery.client.get(detail).json()
    affected=[f for f in after['facts'] if f['source_id']==source['id']]
    assert affected and all(f['value'] is None and not f['usable'] for f in affected)
    assert next(s for s in after['sources'] if s['id']==source['id'])['status']=='unavailable'
    with discovery.app.state.db.connect() as con:con.execute("UPDATE evidence_sources SET status='active',display_permitted=1 WHERE id=?",(source['id'],))
    discovery.app.state.discovery.reapply_tombstones()
    assert all(f['value'] is None for f in discovery.client.get(detail).json()['facts'] if f['source_id']==source['id'])


def test_stale_facts_hide_value_and_conflicting_party_limits_both_retained(discovery):
    data=pack();data['places']=data['places'][:1]
    original=next(f for f in data['places'][0]['facts'] if f['field']=='max_party')
    altered=copy.deepcopy(original);altered['value']=9
    data['places'][0]['facts'].append(altered)
    stored=import_pack(discovery,data);trip=_trip(discovery.client);pid=stored['places'][0]['place_id']
    path=f"/api/v2/trips/{trip['id']}/places/{pid}/detail"
    values=discovery.client.get(path).json()['facts'];limits=[f for f in values if f['field']=='max_party']
    assert len(limits)==2 and all(f['status']=='conflict' for f in limits)
    with discovery.app.state.db.connect() as con:con.execute("UPDATE place_facts SET expires_at='2000-01-01T00:00:00+00:00' WHERE place_id=? AND field='price'",(pid,))
    price=next(f for f in discovery.client.get(path).json()['facts'] if f['field']=='price')
    assert price['value'] is None and price['freshness']=='expired' and 'FACT_STALE' in price['reason_codes']


def test_pack_disable_preserves_personal_bookmark_and_admin_scope(discovery):
    stored=import_pack(discovery);trip=_trip(discovery.client);pid=stored['places'][0]['place_id']
    saved=bookmark(discovery.client,trip,'place',pid,'keep-private-note')
    assert saved['place']['id']==pid and saved['place']['name']==stored['places'][0]['name']
    assert saved['place']['display_name']==stored['places'][0]['name']
    assert set(saved['place'])=={'id','name','display_name','native_name','address','city'}
    member=discovery.login('ordinary-member')
    assert member.client.get('/api/v2/admin/discovery-packs').status_code==404
    assert member.client.post('/api/v2/admin/discovery-packs',json=pack()).status_code==404
    assert discovery.client.patch('/api/v2/admin/discovery-packs/'+stored['id'],json={'status':'disabled','evidence':'Operator disabled synthetic candidate pack'}).status_code==200
    assert discovery.client.get(f"/api/v2/trips/{trip['id']}/discovery-catalog").json()['items']==[]
    retained=discovery.client.get(f"/api/v2/trips/{trip['id']}/bookmarks/{saved['id']}").json()
    assert retained['note']=='keep-private-note' and retained['place']==saved['place']
    assert member.client.get(f"/api/v2/trips/{trip['id']}/bookmarks/{saved['id']}").status_code==404
    assert discovery.client.get(f"/api/v2/trips/{trip['id']}/places/{pid}/detail").status_code==200


@pytest.mark.parametrize('field,value',[('price',{'currency':'JPY','amount_max':'1.20','basis':'per_person','period':'meal'}),
    ('price',{'currency':'EUR','amount_max':12.5,'basis':'per_person','period':'meal'}),
    ('max_party','20 seats'),('live_availability',{'available':True}),
    ('reservation_url','http://127.0.0.1/private'),('opening_hours',{'timezone':'Asia/Tokyo','weekly':{'monday':[['20:00','02:00']]}})])
def test_typed_facts_reject_invented_capacity_availability_and_money(discovery,field,value):
    data=pack();data['places']=data['places'][:1]
    data['places'][0]['facts'][0].update(field=field,value=value)
    assert discovery.client.post('/api/v2/admin/discovery-packs',json=data).status_code==422


@pytest.mark.parametrize('field,value',[
    ('reservation_required','not sure'),
    ('price',{'currency':'EUR','amount_max':'20'}),
    ('price',{'currency':'EUR','basis':'per_person','period':'meal'}),
    ('opening_hours',{'timezone':'Bad/Zone','weekly':{}}),
    ('opening_hours',{'timezone':'Asia/Tokyo','weekly':{'monday':[{'end':'18:00'}]}}),
    ('live_availability',{'provider':'','checked_at':'2099-01-01T00:00:00Z','slots':['definitely open'],
        'visit':{'date':'2026-11-06','local_time':'12:00','timezone':'Asia/Tokyo'},'party':{'adults':4,'children':[]}}),
    ('live_availability',{'provider':'fake','checked_at':'2020-01-01T00:00:00Z','slots':['definitely open'],
        'visit':{'date':'2026-11-06','local_time':'12:00','timezone':'Asia/Tokyo'},'party':{'adults':4,'children':[]}}),
    ('local_evidence',{'source_groups':[{'publisher':'not-a-string'}]}),
    ('iconic_evidence',{'source_groups':['same','same'],'level':'city'}),
    ('local_evidence',{'direct_confirmation':'yes'}),
    ('rating',{'platform':'example','scale':5,'rating':9,'total_rating_count':20,'usage_permitted':True}),
    ('dietary',{'allergen_free':'probably'}),
])
def test_malformed_fact_structures_return_422_instead_of_internal_error(discovery,field,value):
    data=pack();data['places']=data['places'][:1]
    data['places'][0]['facts'][0].update(field=field,value=value)
    response=discovery.client.post('/api/v2/admin/discovery-packs',json=data)
    assert response.status_code==422,response.text


def test_base_location_reused_without_invented_coordinates_and_city_choice_saved(discovery):
    response=discovery.client.post('/api/v2/trips',json={'title':'Tokyo and Barcelona','start_date':'2026-11-06','end_date':'2026-11-09',
        'stops':[{'city':'Tokyo','sequence':1,'start_date':'2026-11-06','end_date':'2026-11-09',
            'timezone':'Asia/Tokyo','base_location':'User chosen hotel area'}]})
    assert response.status_code==201,response.text
    path=f"/api/v2/trips/{response.json()['id']}/discovery-conditions"
    initial=discovery.client.get(path).json()
    assert initial['conditions']['city']=='tokyo'
    assert initial['conditions']['origin']['label']=='User chosen hotel area'
    assert initial['conditions']['origin']['latitude'] is None and initial['conditions']['origin']['longitude'] is None
    blank=_trip(discovery.client)
    blankpath=f"/api/v2/trips/{blank['id']}/discovery-conditions"
    assert discovery.client.get(blankpath).json()['city_needs_confirmation']
    saved=discovery.client.patch(blankpath,json={'expected_version':0,'conditions':conditions()})
    assert saved.status_code==200 and not saved.json()['city_needs_confirmation']


def test_source_recheck_allows_new_facts_only_after_recorded_read(discovery):
    stored=import_pack(discovery)
    place=stored['places'][0];source=place['sources'][0]
    checked=datetime.now(timezone.utc)
    fact={'field':'reservation_required','value':True,'status':'verified','source_key':source['source_key'],
        'checked_at':checked.isoformat(),'expires_at':(checked+timedelta(days=7)).isoformat()}
    facts_path='/api/v2/admin/discovery-places/'+place['place_id']+'/facts'
    assert discovery.client.post(facts_path,json=fact).status_code==422
    path='/api/v2/admin/discovery-sources/'+source['id']
    body={'expected_version':2,'status':'active','read_confirmed':True,'display_permitted':True,
        'policy_version':stored['version'],'evidence':'Read the updated synthetic source again',
        'checked_at':checked.isoformat()}
    assert discovery.client.patch(path,json=body).status_code==200
    assert discovery.client.post(facts_path,json=fact).status_code==201
    body.update(expected_version=3,checked_at='2099-01-01T00:00:00Z')
    assert discovery.client.patch(path,json=body).status_code==422
    body['checked_at']='2000-01-01T00:00:00Z'
    assert discovery.client.patch(path,json=body).status_code==422


def test_whitespace_bookmark_rejected_without_creating_record(discovery):
    trip=_trip(discovery.client)
    path=f"/api/v2/trips/{trip['id']}/bookmarks"
    response=discovery.client.post(path,json={'input_kind':'name','input_value':'   '})
    assert response.status_code==422
    assert discovery.client.get(path).json()['items']==[]
