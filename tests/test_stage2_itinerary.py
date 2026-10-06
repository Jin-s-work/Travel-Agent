"""Hotel/day origins, edit staleness, genuine walking gates and price deltas."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import pytest
from tests.test_foundation_api import service
from tests.test_discovery_foundation import discovery,import_pack
from tests.test_itinerary_api import planning,submit,done,preview,apply
from tests.test_discovery_intents import trip_for
from tests.test_accommodations_api import fake,create,resolved,base,patch_body
from tests.discovery_synthetic import conditions
from tests.test_itinerary_engine import request,Routes,locks,place_items
from tests.test_recommendation_engine import catalog,NOW
from src.itineraries.scheduler import generate,endpoint
from src.itineraries.prices import price_delta


def hotel_plan(planning):
    fake(planning);t=trip_for(planning.client);pack=import_pack(planning);selected=pack['places'][0]['place_id']
    s=create(planning.client,t);s,_=resolved(planning,t,s)
    response=planning.client.post(base(t)+'/'+s['id']+'/select',json={'expected_version':s['version'],'candidate_id':s['candidates'][0]['candidate_id']})
    assert response.status_code==200,response.text;s=response.json()
    c=conditions();c['origin']=None;c['origin_selection']={'kind':'automatic'}
    r=planning.client.patch(f"/api/v2/trips/{t['id']}/discovery-conditions",json={'expected_version':0,'conditions':c});assert r.status_code==200,r.text
    result,path=done(planning,t,submit(planning,t,selected,allow_provisional=True,end_date='2026-11-07'))
    assert any(i.get('place_id')==selected for i in result['items']),result
    return t,s,result,path


def test_daily_hotel_snapshot_and_apply_reject_changed_stay_without_mutation(planning):
    t,s,current,path=hotel_plan(planning)
    assert set(current['snapshot']['origin_contexts'])=={'2026-11-06','2026-11-07'}
    assert current['snapshot']['origin_contexts']['2026-11-07']['origin']['accommodation_id']==s['id']
    item=next(i for i in current['items'] if i.get('place_id'))
    p=preview(planning,path,current['version'],[{'op':'lock','item_id':item['item_id']}]);assert 'price_delta' in p
    assert planning.client.patch(base(t)+'/'+s['id'],json=patch_body(s,display_name='Renamed private hotel')).status_code==200
    response=apply(planning,path,p);assert response.status_code==409,response.text
    assert response.json()['error']['code']=='ORIGIN_CONTEXT_CHANGED'
    stale=planning.client.get(path).json();assert stale['version']==current['version'] and stale['data_status']=='stale'
    with planning.app.state.db.connect() as con:
        original=json.loads(con.execute('SELECT snapshot_json FROM itineraries WHERE id=?',(current['id'],)).fetchone()[0])
        assert original==current['snapshot']
    assert planning.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[{'op':'lock','item_id':item['item_id']}]}).status_code==409


def test_undo_after_hotel_deletion_does_not_restore_coordinates(planning):
    t,s,current,path=hotel_plan(planning);item=next(i for i in current['items'] if i.get('place_id'))
    p=preview(planning,path,1,[{'op':'lock','item_id':item['item_id']}]);applied=apply(planning,path,p)
    assert applied.status_code==200,applied.text
    assert planning.client.delete(base(t)+'/'+s['id'],params={'expected_version':s['version']}).status_code==200
    undo=planning.client.post(path+'/undo-previews',json={'expected_version':2,'steps':1})
    assert undo.status_code==409 and undo.json()['error']['code']=='ORIGIN_CONTEXT_CHANGED'
    after=planning.client.get(path).json();assert after['version']==2 and after['data_status']=='stale'
    assert all(l.get('duration_minutes') is None for l in after['legs'])


def test_expense_change_between_preview_and_apply_rejects_409(planning):
    t,s,current,path=hotel_plan(planning);item=next(i for i in current['items'] if i.get('place_id'))
    p=preview(planning,path,1,[{'op':'lock','item_id':item['item_id']}])
    with planning.app.state.db.connect() as con:
        con.execute('INSERT INTO expense_overrides(id,owner_id,trip_id,itinerary_id,item_key,version,payload_json,updated_at) VALUES(?,?,?,?,?,?,?,?)',('expense_test',planning.admin.user['id'],t['id'],current['id'],item['item_id'],1,json.dumps({'price':None,'party':None,'days':None,'included_by':None}),'2026-10-06T01:00:00+00:00'))
    response=apply(planning,path,p);assert response.status_code==409 and response.json()['error']['code']=='EXPENSE_DATA_CHANGED'
    assert planning.client.get(path).json()['version']==1


def test_per_departure_origin_switches_and_unknown_checkout_no_fallback():
    from tests.test_accommodations_origin import trip,stay
    s=request(start_date='2026-11-05',end_date='2026-11-05');s['stop_id']='stop_a';s['computed_at']='2026-10-06T00:00:00+00:00'
    s['origin_resolution_input']={'trip':trip(),'stays':[stay(checkout_time='11:00'),stay('stay_b',checkin_date='2026-11-05',checkout_date='2026-11-10',checkin_time='15:00')],'overrides':{}}
    assert endpoint(None,s,'2026-11-05T01:00:00+00:00')['id']=='stay_a'
    noon=endpoint(None,s,'2026-11-05T03:00:00+00:00');assert noon['latitude'] is None and noon['origin_status']=='unknown'
    assert endpoint(None,s,'2026-11-05T07:00:00+00:00')['id']=='stay_b'


@pytest.mark.parametrize('provisional',[False,True])
def test_walking_limit_uses_real_route_not_500m_straightline(provisional):
    s=request(allow_provisional=provisional);s['conditions']['distance_filter']={'kind':'walking','max_duration_minutes':15}
    result=generate(s,[],catalog(),Routes(25),NOW)
    assert not place_items(result) and 'MAXIMUM_WALKING_TIME_EXCEEDED' in result['unplaced'][0]['reason_codes']


def test_walking_limit_unknown_is_not_zero_and_straightline_independent():
    s=request();s['conditions']['distance_filter']={'kind':'walking','max_duration_minutes':15}
    result=generate(s,[],catalog(),Routes(None),NOW);assert not place_items(result)
    s['conditions']['distance_filter']={'kind':'straight_line','max_distance_m':1}
    places=catalog();places[0]['coordinate_permitted']=True
    result=generate(s,[],places,Routes(10),NOW);assert not place_items(result) and 'MAXIMUM_DISTANCE_EXCEEDED' in result['unplaced'][0]['reason_codes']


def test_short_previous_leg_cannot_satisfy_long_hotel_walking_limit():
    s=request(activity_end='18:00');s['conditions']['distance_filter']={'kind':'walking','max_duration_minutes':15}
    class SplitRoutes(Routes):
        def __call__(self,a,b,departure,mode):
            self.minutes=25 if a['id']=='hotel' else 10
            return super().__call__(a,b,departure,mode)
    result=generate(s,locks('12:30'),catalog(),SplitRoutes(),NOW)
    assert not place_items(result) and 'MAXIMUM_WALKING_TIME_EXCEEDED' in result['unplaced'][0]['reason_codes']


@pytest.mark.parametrize('meal_end,buffer,placed',[('13:00',0,False),('12:30',0,True),('12:30',10,False)])
def test_120_150_160_minutes_preserve_both_travel_legs(meal_end,buffer,placed):
    s=request();s['buffers']['booking_before_minutes']=buffer
    result=generate(s,locks(meal_end),catalog(),Routes(30),NOW)
    assert bool(place_items(result)) is placed
    assert {i['booking_id'] for i in result['items'] if i.get('booking_id')}=={'lunch','entry'}


def test_price_delta_currency_unknown_free_and_refundable_deposit():
    snapshot={'conditions':{'party':{'adults':2,'children':[]},'visit':{'date':'2026-11-06'}}}
    before={'items':[{'item_id':'ticket','name':'Ticket'},{'item_id':'meal','name':'Included meal'},{'item_id':'unknown','name':'Unknown'}],'legs':[]}
    after={'items':before['items']+[{'item_id':'free','name':'Free'},{'item_id':'eur','name':'EUR fee'}],'legs':[]}
    def price(currency,amount,**extra):return {'currency':currency,'basis':'group','amount_min':amount,'amount_max':amount,'tax':'included','fees_known':True,**extra}
    overrides={'ticket':{'price':price('JPY','1000',deposit={'kind':'refundable','amount':'200'}),'party':None,'days':None,'included_by':None},
        'meal':{'price':None,'party':None,'days':None,'included_by':'ticket'},'free':{'price':price('JPY','0'),'party':None,'days':None,'included_by':None},
        'eur':{'price':price('EUR','10'),'party':None,'days':None,'included_by':None}}
    value=price_delta(before,after,snapshot,[],overrides,'itinerary_test',NOW)
    assert value['currencies']['JPY']['after_known_lower']=='1000' and value['currencies']['JPY']['after_refundable_deposits']=='200'
    assert value['currencies']['JPY']['delta_upper']=='0' and value['currencies']['EUR']['delta_lower']=='10.00'
    assert value['after_unknown_items']==1 and value['after']['included_item_count']==1 and value['krw_total'] is None


def test_legacy_manual_coordinates_with_stops_survive_itinerary_snapshot(planning):
    t=trip_for(planning.client);pack=import_pack(planning);selected=pack['places'][0]['place_id']
    c=conditions();c['origin_selection']={'kind':'automatic'};c['origin']['place_id']=selected
    response=planning.client.patch(f"/api/v2/trips/{t['id']}/discovery-conditions",json={'expected_version':0,'conditions':c})
    assert response.status_code==200,response.text
    envelope=response.json()
    assert envelope['origin_context']['origin']['source']=='explicit_origin'
    assert envelope['overrides']['origin_selection']['kind']=='automatic'
    result,_=done(planning,t,submit(planning,t,selected,allow_provisional=True))
    origin=result['snapshot']['origin_contexts']['2026-11-06']['origin']
    assert origin['provenance']=='user_entered' and origin['source']=='explicit_origin'
    assert origin['latitude']==c['origin']['latitude'] and origin['longitude']==c['origin']['longitude']
    assert origin['place_id'] is None
    assert result['snapshot']['origin_resolution_input']['overrides']['origin_selection']['kind']=='manual'
    assert any(i.get('place_id')==selected for i in result['items'])
    assert all(leg.get('provider')!='identity' for leg in result['legs'])


@pytest.mark.parametrize('rule',[{'allowed':False},{'allowed':True,'minimum_age':12}])
def test_child_restriction_unknown_blocks_strict_but_explicit_none_remains_valid(rule):
    from tests.test_recommendation_engine import fact
    places=catalog();fact(places[0],'children_rule')['value']=rule
    snapshot=request();snapshot['party']['children_status']='unknown'
    result=generate(snapshot,[],places,Routes(10),NOW)
    assert not place_items(result) and 'CHILD_PARTY_UNKNOWN' in result['unplaced'][0]['reason_codes']
    snapshot['party']['children_status']='none'
    result=generate(snapshot,[],places,Routes(10),NOW)
    assert place_items(result) and result['validation_status']=='validated'
    assert not any(i['code']=='CHILD_PARTY_UNKNOWN' for i in result['unresolved_conditions'])


@pytest.mark.parametrize('rule',[{'allowed':False},{'allowed':True,'minimum_age':12}])
def test_child_restriction_unknown_requires_visible_provisional_status(rule):
    from tests.test_recommendation_engine import fact
    places=catalog();fact(places[0],'children_rule')['value']=rule
    snapshot=request(allow_provisional=True);snapshot['party']['children_status']='unknown'
    result=generate(snapshot,[],places,Routes(10),NOW)
    assert place_items(result) and result['validation_status']=='provisional'
    assert any(i['code']=='CHILD_PARTY_UNKNOWN' and i['state']=='unknown' for i in result['unresolved_conditions'])
