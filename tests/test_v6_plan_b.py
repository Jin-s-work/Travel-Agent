"""S3-05 Plan B ordering and rain suitability (G12). Network-free: synthetic packs,
fake route provider, no weather provider is ever called."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone

from tests.test_foundation_api import service,_trip
from tests.test_discovery_foundation import discovery,import_pack
from tests.test_itinerary_api import planning,prepare,submit,done,apply
from tests.discovery_synthetic import pack as synthetic_pack,conditions
from src.recommendations.engine import movement
from src.travel_tools import alternatives as module
from src.travel_tools.alternatives import MAX_CANDIDATES,indoor_status,order_pool,run_scores

NOW=datetime(2026,10,21,tzinfo=timezone.utc)
VISIT={'date':'2026-11-06','timezone':'Asia/Tokyo'}
ORIGIN={'latitude':35.68,'longitude':139.77}


def place(pid,category='attraction',tags=(),lat=None,lon=None,facts=(),sources=()):
    return {'place_id':pid,'category':category,'tags':list(tags),'latitude':lat,'longitude':lon,'facts':list(facts),'sources':list(sources)}


def test_order_pool_without_linked_run_is_distance_then_place_id():
    pool=[place('p_c',lat=35.70,lon=139.77),place('p_a',lat=35.69,lon=139.77),place('p_unknown'),
          place('p_b',lat=35.69,lon=139.77),place('p_0unknown')]
    ordered=order_pool(pool,ORIGIN,{})
    assert [p['place_id'] for p,_ in ordered]==['p_a','p_b','p_c','p_0unknown','p_unknown']
    assert ordered[0][1]==movement(pool[1],{'origin':ORIGIN})['distance_m'] and ordered[-1][1] is None


def test_order_pool_linked_run_score_first_unscored_after_scored():
    pool=[place('p_near',lat=35.681,lon=139.77),place('p_far',lat=35.75,lon=139.77),
          place('p_mid',lat=35.70,lon=139.77),place('p_unscored',lat=35.6801,lon=139.77),place('p_tie',lat=35.69,lon=139.77)]
    scores={'p_far':.9,'p_mid':.5,'p_tie':.5,'p_near':.1}
    assert [p['place_id'] for p,_ in order_pool(pool,ORIGIN,scores)]==['p_far','p_tie','p_mid','p_near','p_unscored']


def test_distances_are_ranked_over_the_whole_pool_before_the_twelve_cap():
    # place_id order is the reverse of distance order: the old place_id slice
    # would have examined the 12 farthest candidates.
    pool=[place(f'p_{i:02d}',lat=35.68+(20-i)*.001,lon=139.77) for i in range(20)]
    head=[p['place_id'] for p,_ in order_pool(pool,ORIGIN,{})[:MAX_CANDIDATES]]
    assert MAX_CANDIDATES==12 and head==[f'p_{i:02d}' for i in range(19,7,-1)]


def _source():
    return {'id':'src_1','status':'active','read_confirmed':True,'display_permitted':True,
            'checked_at':(NOW-timedelta(days=1)).isoformat(),'policy_version':'policy-1'}


def _tags_fact(value,status='verified'):
    return {'id':'fact_tags','field':'tags','value':value,'status':status,'usable':True,'source_id':'src_1',
            'checked_at':(NOW-timedelta(days=1)).isoformat(),'expires_at':(NOW+timedelta(days=30)).isoformat(),'policy_version':'policy-1'}


def test_indoor_status_rules():
    assert indoor_status(place('a',tags=['museum','Indoor']),VISIT,NOW)=={'status':'indoor_confirmed','basis':'tag'}
    assert indoor_status(place('r',category='restaurant',tags=['noodles']),VISIT,NOW)=={'status':'indoor_likely','basis':'category_default'}
    assert indoor_status(place('c',category='cafe'),VISIT,NOW)=={'status':'indoor_likely','basis':'category_default'}
    assert indoor_status(place('v',tags=['viewpoint']),VISIT,NOW)=={'status':'unconfirmed','basis':None}
    # A confirmed outdoor place is excluded even when its category would default to indoor.
    assert indoor_status(place('t',category='restaurant',tags=['outdoor']),VISIT,NOW)=={'status':'outdoor_confirmed','basis':'tag'}
    verified=place('f',category='cafe',facts=[_tags_fact(['outdoor','terrace'])],sources=[_source()])
    assert indoor_status(verified,VISIT,NOW)=={'status':'outdoor_confirmed','basis':'verified_fact'}
    # An unverified (provisional) tags fact is not evidence.
    provisional=place('g',category='cafe',facts=[_tags_fact(['outdoor'],status='provisional')],sources=[_source()])
    assert indoor_status(provisional,VISIT,NOW)=={'status':'indoor_likely','basis':'category_default'}
    # Expired evidence is not evidence either.
    expired=place('h',facts=[_tags_fact(['indoor'])],sources=[_source()])
    assert indoor_status(expired,VISIT,NOW+timedelta(days=60))['status']=='unconfirmed'


class FakeRecommendations:
    def __init__(self,run=None,error=None):self.run,self.error,self.calls=run,error,[]
    def get(self,actor,trip_id,ident):
        self.calls.append((actor.id,trip_id,ident))
        if self.error:raise self.error
        return deepcopy(self.run)


def _run(**changes):
    result={'sections':{'landmark':{'items':[{'place_id':'p_old','ranking_diagnostics':{'score_lower':.9}},
                                             {'place_id':'p1','ranking_diagnostics':{'score_lower':.4}},
                                             {'place_id':'p2','ranking_diagnostics':{'score_lower':float('nan')}},
                                             {'place_id':'p3','score':None}],
                                   'needs_confirmation':[{'place_id':'p1','ranking_diagnostics':{'score_lower':.7}}],
                                   'excluded':[{'place_id':'p5','ranking_diagnostics':{'score_lower':.99}}]},
                        'local_discovery':{'items':[{'place_id':'p4','ranking_diagnostics':{'score_lower':.2}}],'needs_confirmation':[]}}}
    run={'data_status':'current','origin_status':'current','result':result}
    run.update(changes);return run


def test_run_scores_uses_the_owner_scoped_read_path_and_only_valid_same_section_scores():
    from src.foundation.repository import DomainError
    actor=type('Actor',(),{'id':'user_1'})();linked={'recommendation_run_id':'run_1'}
    recs=FakeRecommendations(_run())
    # Same section as the replaced place only; excluded/deferred groups and NaN are ignored.
    assert run_scores(recs,actor,'trip_1',linked,'p_old')=={'p_old':.9,'p1':.7}
    assert recs.calls==[('user_1','trip_1','run_1')]
    assert run_scores(FakeRecommendations(_run()),actor,'trip_1',linked,'p4')=={'p4':.2}
    # Replaced place not ranked in the run -> no common scale -> distance only.
    assert run_scores(FakeRecommendations(_run()),actor,'trip_1',linked,'p_missing')=={}
    none=FakeRecommendations(_run())
    assert run_scores(none,actor,'trip_1',{},'p_old')=={} and not none.calls
    # Not current: changed source data, changed origin, stale ranking, no result, not found/other owner.
    assert run_scores(FakeRecommendations(_run(data_status='stale')),actor,'trip_1',linked,'p_old')=={}
    assert run_scores(FakeRecommendations(_run(origin_status='stale')),actor,'trip_1',linked,'p_old')=={}
    stale=_run();stale['result']['ranking_status']='stale'
    assert run_scores(FakeRecommendations(stale),actor,'trip_1',linked,'p_old')=={}
    assert run_scores(FakeRecommendations(_run(result=None)),actor,'trip_1',linked,'p_old')=={}
    missing=FakeRecommendations(error=DomainError('NOT_FOUND','x',404))
    assert run_scores(missing,actor,'trip_1',linked,'p_old')=={}


def _pack(specs):
    """Synthetic Tokyo pack whose places are (category, tags, lat offset) specs."""
    data=synthetic_pack();template=data['places'][0];places=[]
    for n,(category,tags,offset) in enumerate(specs,1):
        p=deepcopy(template)
        p.update(external_id=f'synthetic-planb-{n}',name=f'합성 Plan B {n}',native_name=f'合成 Plan B {n}',
                 address=f'SYNTHETIC ONLY PLANB {n}',category=category,tags=list(tags),chain_id=None,
                 canonical_url=f'https://example.org/planb/{n}',latitude=35.68+offset,longitude=139.77)
        for s in p['sources']:s['url']=f'https://example.org/planb/{n}/{s["key"]}'
        for f in p['facts']:
            if f['field']=='closed':f['value']=False
        places.append(p)
    data.update(version='synthetic-discovery-planb-'+str(len(specs)),places=places)
    return data


def _setup(planning,specs,selected_index=0):
    # Only this pack is imported, so the candidate pool is exactly `specs`.
    stored=import_pack(planning,_pack(specs));trip=_trip(planning.client)
    r=planning.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",json={'expected_version':0,'conditions':conditions()})
    assert r.status_code==200,r.text
    by_name={p['name']:p['place_id'] for p in stored['places']}
    ids=[by_name[f'합성 Plan B {n}'] for n in range(1,len(specs)+1)]
    original,path=done(planning,trip,submit(planning,trip,ids[selected_index],allow_provisional=True))
    item=next(i for i in original['items'] if i.get('place_id')==ids[selected_index])
    return trip,ids,original,path,item


def test_rain_keeps_indoor_tagged_and_drops_outdoor_and_unevidenced_attractions(planning):
    specs=[('attraction',['art'],.001),('attraction',['outdoor','garden'],.002),('attraction',['museum'],.003),
           ('attraction',['indoor','museum'],.004),('attraction',['viewpoint'],.005)]
    trip,ids,original,path,item=_setup(planning,specs)
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'rain'})
    assert r.status_code==200,r.text
    body=r.json();rejected={x['place_id']:x['reason_codes'] for x in body['rejected']}
    assert rejected[ids[1]]==['OUTDOOR_CONFIRMED']
    assert rejected[ids[2]]==['INDOOR_UNCONFIRMED'] and rejected[ids[4]]==['INDOOR_UNCONFIRMED']
    assert [a['comparison']['after']['place_id'] for a in body['alternatives']]==[ids[3]],body
    assert body['alternatives'][0]['comparison']['indoor']=={'status':'indoor_confirmed','basis':'tag'}
    assert body['situation']['live_weather'] is None and body['external_action']=='none'
    assert planning.client.get(path).json()['items']==original['items']


def test_candidate_cap_examines_the_twelve_nearest_not_the_first_place_ids(planning,monkeypatch):
    # 15 untagged attractions plus the scheduled one; rain rejects every one, so
    # the rejected list shows exactly which 12 were examined and in what order.
    specs=[('attraction',['art'],.0005)]+[('attraction',['museum'],.0001*(17-n)+.001) for n in range(1,16)]
    trip,ids,original,path,item=_setup(planning,specs)
    discovery=planning.app.state.alternatives.discovery;real=discovery.catalog;calls=[]
    def spy(*args,**kwargs):calls.append(kwargs);return real(*args,**kwargs)
    monkeypatch.setattr(discovery,'catalog',spy)
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'rain'})
    assert r.status_code==200,r.text
    body=r.json()
    assert body['limits']['examined']==MAX_CANDIDATES and body['limits']['termination']=='candidate_cap'
    assert body['limits']['ordering']==['straight_line_distance','place_id']
    pool=sorted(ids[1:],key=lambda pid:(.0001*(17-(ids.index(pid)))+.001,pid))
    assert [x['place_id'] for x in body['rejected']]==pool[:MAX_CANDIDATES]
    assert all(x['reason_codes']==['INDOOR_UNCONFIRMED'] for x in body['rejected'])
    # The stored-row LIMIT (1000) is applied in the SQL spatial order from the
    # Plan B origin, not by place_id, and only the replaced item's category is read.
    assert calls and calls[0]['origin']['latitude']==ORIGIN['latitude'] and calls[0]['categories']==['attraction']


def test_linked_run_score_orders_candidates_before_distance(planning,monkeypatch):
    specs=[('restaurant',['noodles'],.001)]+[('restaurant',['noodles'],.001*n) for n in range(2,6)]
    trip,ids,original,path,item=_setup(planning,specs)
    seen={}
    def fake_scores(recommendations,actor,trip_id,snapshot,replaced):
        seen.update(actor=actor.id,trip=trip_id,replaced=replaced);return {ids[4]:.9,ids[2]:.5}
    monkeypatch.setattr(module,'run_scores',fake_scores)
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'closed'})
    assert r.status_code==200,r.text
    body=r.json()
    assert seen['trip']==trip['id'] and seen['replaced']==ids[0] and body['limits']['ordering']==['score_lower','straight_line_distance','place_id']
    assert [a['comparison']['after']['place_id'] for a in body['alternatives']]==[ids[4],ids[2],ids[1]],body
    assert all(a['comparison']['indoor']['status']=='indoor_likely' for a in body['alternatives'])
    assert all('score' not in a['comparison'] for a in body['alternatives'])


def _linked_setup(planning,specs):
    """Real recommendation run (no patching) linked to the itinerary via recommendation_run_id."""
    from tests.test_recommendation_api import submit as recommend,completed
    stored=import_pack(planning,_pack(specs));trip=_trip(planning.client)
    r=planning.client.patch(f"/api/v2/trips/{trip['id']}/discovery-conditions",json={'expected_version':0,'conditions':conditions()})
    assert r.status_code==200,r.text
    run=completed(planning,trip,recommend(planning,trip))
    by_name={p['name']:p['place_id'] for p in stored['places']}
    ids=[by_name[f'합성 Plan B {n}'] for n in range(1,len(specs)+1)]
    original,path=done(planning,trip,submit(planning,trip,ids[0],allow_provisional=True,recommendation_run_id=run['run_id']))
    assert original['snapshot']['recommendation_run_id']==run['run_id']
    item=next(i for i in original['items'] if i.get('place_id')==ids[0])
    return trip,ids,run,path,item


# 4 is the farthest but carries the preferred 'noodles' tag, so the real run
# ranks it above the nearer 2 and 3 (distance-only order would be 2, 3, 4).
LINKED_SPECS=[('restaurant',['noodles'],.001),('restaurant',['sushi'],.002),('restaurant',['sushi'],.003),('restaurant',['noodles'],.006),('restaurant',['sushi'],.02)]


def test_real_linked_run_orders_alternatives_by_score_lower_end_to_end(planning):
    trip,ids,run,path,item=_linked_setup(planning,LINKED_SPECS)
    scores={x['place_id']:x['ranking_diagnostics']['score_lower'] for x in run['result']['sections']['landmark']['items']}
    assert scores[ids[3]]>scores[ids[1]]>scores[ids[2]]>scores[ids[4]]
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'closed'})
    assert r.status_code==200,r.text
    body=r.json()
    assert body['limits']['ordering']==['score_lower','straight_line_distance','place_id']
    assert [a['comparison']['after']['place_id'] for a in body['alternatives']]==[ids[3],ids[1],ids[2]],body


def test_source_change_after_run_drops_scores_and_falls_back_to_distance(planning):
    trip,ids,run,path,item=_linked_setup(planning,LINKED_SPECS)
    with planning.app.state.db.connect() as con:
        con.execute("UPDATE place_facts SET expires_at='2000-01-01T00:00:00+00:00' WHERE place_id=?",(ids[4],))
    after=planning.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{run['run_id']}").json()
    assert after['data_status']=='stale'
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'closed'})
    assert r.status_code==200,r.text
    body=r.json()
    assert body['limits']['ordering']==['straight_line_distance','place_id']
    assert [a['comparison']['after']['place_id'] for a in body['alternatives']]==[ids[1],ids[2],ids[3]],body


def test_rain_on_fixed_booking_still_requires_separate_cancel_review(planning):
    trip,pack,pid,reserved=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    fixed=next(i for i in result['items'] if i.get('booking_id'))
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':fixed['item_id'],'reason':'rain'})
    assert r.status_code==200 and not r.json()['alternatives']
    assert r.json()['reason_codes']==['FIXED_BOOKING_REQUIRES_SEPARATE_CANCEL_REVIEW']
    item=next(i for i in result['items'] if i.get('place_id'))
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'rain'})
    assert r.status_code==200,r.text
    chosen=r.json()['alternatives'][0]
    assert chosen['comparison']['preserved_fixed_item_ids']==[fixed['item_id']]
    changed=apply(planning,path,chosen);assert changed.status_code==200,changed.text
    after=next(i for i in changed.json()['items'] if i['item_id']==fixed['item_id'])
    assert {k:after.get(k) for k in ('local_start','local_end','booking_id','locked')}=={k:fixed.get(k) for k in ('local_start','local_end','booking_id','locked')}
    stale=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'rain'})
    assert stale.status_code==409
