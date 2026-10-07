"""All inputs, transports and providers are synthetic. No Maps key or HTTP call."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
import math

import httpx
import pytest

from src.foundation.repository import DomainError
from src.location.geometry import coordinate_pair, haversine_straight_line
from src.location.providers import (build_location_providers, FakeRouteProvider, FakeGeocodingProvider,
    GoogleRoutesProvider, GooglePlacesGeocodingProvider, DisabledRouteProvider)
from src.location.service import MatrixService
from src.reliability.budget import BudgetPolicy
from tests.test_itinerary_travel import route, service, NOW, A, B, DEPART, session

POLICY={'enabled':True,'version':'synthetic_review_1','usage_permitted':True,
    'private_input_permitted':True,'durable_storage_permitted':True,'display_without_map_permitted':True,
    'immutable_history_permitted':True,'permission_reference':'synthetic_fixture_only','cache_ttl_seconds':60}
POINT_A={**A,'version':1};POINT_B={**B,'version':1}


def prices(svc, limit=100):
    cfg=deepcopy(BudgetPolicy.for_tests().config)
    cfg['prices']['synthetic_location/matrix']={'currency':'USD','rates_per_million':{'matrix_elements':'1'},'max_units':{'matrix_elements':30}}
    cfg['limits']['USD']={k:limit for k in cfg['limits']['USD']}
    svc.app.state.budget.policy=BudgetPolicy(cfg)


def matrix(route,provider=None,**kwargs):
    svc,actor,trip=route
    return MatrixService(svc.app.state.gateway,svc.app.state.jobs,provider,clock=lambda:NOW,**kwargs)


def collect(svc,route,*,origins=None,destinations=None,key='stage2',mode='walking',departure=DEPART,**kwargs):
    _,actor,trip=route
    return svc.collect(actor,trip['id'],origins or [POINT_A],destinations or [POINT_B],mode,departure,request_key=key,**kwargs)


def fake(**kwargs):
    return FakeRouteProvider({('a','b','walking'):{'distance_m':2000,'duration_seconds':1500},
        ('b','a','walking'):{'distance_m':2500,'duration_seconds':1800}},clock=lambda:NOW,**kwargs)


def test_geometry_valid_zero_and_invalid_range():
    assert coordinate_pair(0,0)==(0,0)
    assert 500 < haversine_straight_line((0,0),(0,.005)) < 560
    assert haversine_straight_line((35.1,139.1),(35.1,139.1))==0
    for pair in [(91,0),(0,-181),(True,0),(float('nan'),0),(0,float('inf')),(None,0)]:
        assert coordinate_pair(*pair) is None
        assert haversine_straight_line(pair,(0,0)) is None


def test_no_credentials_or_no_review_remain_off():
    geo,routes=build_location_providers()
    assert not geo.enabled and not routes.enabled
    geo,routes=build_location_providers({'provider':'google_maps','geocoding':POLICY,'routes':POLICY})
    assert not geo.enabled and not routes.enabled
    geo,routes=build_location_providers({'provider':'google_maps','geocoding':{'enabled':True},'routes':{'enabled':True}},api_key='synthetic')
    assert not geo.usage_permitted and not routes.usage_permitted
    assert geo.resolve('hotel','tokyo',{}).value['status']=='unavailable'
    assert geo.calls==0


def test_fake_geocoding_is_explicit_and_ambiguous():
    p=FakeGeocodingProvider([{'name':'Synthetic branch A','latitude':0,'longitude':0},
        {'name':'Synthetic branch B','latitude':0,'longitude':1}],clock=lambda:NOW)
    response=p.resolve('Synthetic','tokyo',{})
    assert len(response.value['candidates'])==2 and response.value['status']=='candidates'
    assert all(c['synthetic'] for c in response.value['candidates'])
    assert response.value.get('selected_candidate_id') is None


def test_google_text_search_fixed_endpoint_minimal_mask_and_normalization():
    captured=[]
    def transport(req):
        captured.append(req)
        return httpx.Response(200,json={'places':[{'id':'fixture-id','displayName':{'text':'Synthetic branch','languageCode':'en'},
            'formattedAddress':'Synthetic London address','location':{'latitude':51.5,'longitude':0},
            'addressComponents':[{'longText':'London','types':['locality']}], 'author':'not retained'}]})
    geo=GooglePlacesGeocodingProvider('not-a-real-key',POLICY,clock=lambda:NOW,transport=httpx.MockTransport(transport))
    result=geo.resolve('Synthetic Hotel','london',{'max_candidates':5})
    c=result.value['candidates'][0]
    assert c['longitude']==0 and c['city']=='London' and c['query_city']=='london'
    assert 'author' not in c and c['timezone'] is None
    assert len(captured)==1 and captured[0].url.host=='places.googleapis.com'
    assert '*' not in captured[0].headers['X-Goog-FieldMask']
    assert json.loads(captured[0].content)['pageSize']==5
    with pytest.raises(DomainError):geo.resolve('https://maps.app.goo.gl/synthetic','london',{})
    assert len(captured)==1


def test_google_matrix_each_element_error_and_missing_are_unknown():
    captured=[]
    def transport(req):
        captured.append(req)
        return httpx.Response(200,json=[{'condition':'ROUTE_EXISTS','status':{},'distanceMeters':2000,'duration':'1500s'},
            {'originIndex':0,'destinationIndex':1,'condition':'ROUTE_EXISTS','status':{'code':7},'distanceMeters':1,'duration':'1s'}])
    p=GoogleRoutesProvider('fixture',POLICY,clock=lambda:NOW,transport=httpx.MockTransport(transport))
    result=p.matrix([POINT_A],[POINT_B,{**POINT_B,'id':'c'},{**POINT_B,'id':'d'}],'walking',DEPART,{})
    assert result.units=={'matrix_elements':3}
    assert [e['status'] for e in result.value['elements']]==['ok','unknown','unknown']
    assert result.value['elements'][1]['duration_seconds'] is None
    assert result.value['elements'][2]['reason_codes']==['ROUTE_ELEMENT_MISSING']
    assert 'status' in captured[0].headers['X-Goog-FieldMask']
    assert json.loads(captured[0].content)['travelMode']=='WALK'


@pytest.mark.parametrize('distance,duration',[(0,'60s'),(100,'0s'),(-1,'20s'),(float('inf'),'20s'),(100,'NaNs')])
def test_google_invalid_numeric_or_unconfirmed_zero_rejected(distance,duration):
    # JSON transport is a fixture even for nonstandard Infinity.
    payload=json.dumps([{'condition':'ROUTE_EXISTS','status':{},'distanceMeters':distance,'duration':duration}])
    p=GoogleRoutesProvider('fixture',POLICY,clock=lambda:NOW,transport=httpx.MockTransport(lambda req:httpx.Response(200,content=payload,headers={'content-type':'application/json'})))
    result=p.matrix([POINT_A],[POINT_B],'walking',DEPART,{})
    assert result.value['elements'][0]['duration_seconds'] is None


def test_google_caps_reject_before_http():
    p=GoogleRoutesProvider('fixture',POLICY,clock=lambda:NOW,transport=httpx.MockTransport(lambda _:pytest.fail('No HTTP expected')))
    with pytest.raises(DomainError):p.matrix([POINT_A]*11,[POINT_B]*10,'transit',DEPART,{'max_elements':120})
    assert p.calls==0


def test_matrix_disabled_zero_spend_and_budget_zero_do_not_call(route):
    svc,_,_=route;prices(svc,limit=0);provider=fake();m=matrix(route,provider)
    assert collect(m,route)['elements'][0]['status']=='unknown'
    assert provider.calls==0
    prices(svc)
    assert collect(m,route,limits={'zero_spend':True})['elements'][0]['reason_codes']==['ZERO_SPEND']
    assert provider.calls==0
    assert collect(matrix(route),route)['elements'][0]['reason_codes']==['ROUTES_DISABLED']


def test_matrix_caps_and_coordinates_fail_before_call(route):
    svc,_,_=route;prices(svc);provider=fake();m=matrix(route,provider,max_candidates=1)
    result=collect(m,route,destinations=[POINT_B,{**POINT_B,'id':'c'}])
    assert result['elements'][0]['reason_codes']==['ROUTE_ELEMENT_LIMIT'] and provider.calls==0
    assert collect(m,route,origins=[{**POINT_A,'latitude':999}])['elements'][0]['reason_codes']==['ROUTE_COORDINATES_UNKNOWN']


def test_direction_version_mode_accessibility_and_private_scope_cache(route):
    svc,actor,trip=route;prices(svc);provider=fake();m=matrix(route,provider)
    first=collect(m,route);again=collect(m,route)
    assert first['elements'][0]['duration_seconds']==1500 and again['stats']['cache_hits']==1
    reverse=collect(m,route,origins=[POINT_B],destinations=[POINT_A])
    assert reverse['elements'][0]['duration_seconds']==1800
    collect(m,route,origins=[{**POINT_A,'version':2}]);collect(m,route,accessibility={'wheelchair':True})
    assert provider.calls==4
    assert collect(m,route,mode='transit')['elements'][0]['status']=='unknown' and provider.calls==5
    assert len(m.cache)==4
    svc.app.state.repo.delete_trip(actor.id,trip['id'])
    with pytest.raises(DomainError):collect(m,route)
    assert provider.calls==5


def test_receipt_survives_service_restart_without_second_call(route):
    svc,_,_=route;prices(svc);provider=fake()
    first=collect(matrix(route,provider),route)
    resumed=collect(matrix(route,provider),route)
    assert provider.calls==1 and resumed['stats']['receipt_replays']==1
    assert first['elements']==resumed['elements']
    assert resumed['stats']['cost']['actual_micros']==1
    assert all('latitude' not in p.read_text() for p in svc.settings.artifacts_dir.rglob('*.json'))


def test_stale_receipt_never_recalled_or_presented_current(route):
    svc,_,_=route;prices(svc);provider=fake();collect(matrix(route,provider),route)
    m=matrix(route,provider);m.clock=lambda:NOW+timedelta(days=2)
    response=collect(m,route)
    assert response['elements'][0]['reason_codes']==['ROUTE_RESPONSE_STALE'] and provider.calls==1


def test_unknown_charge_not_retried_and_last_budget_atomic(route):
    svc,_,_=route;prices(svc);provider=fake(fail=True)
    result=collect(matrix(route,provider),route)
    assert result['stats']['cost']['actual_micros'] is None and result['stats']['cost']['pending_micros']==1
    collect(matrix(route,provider),route)
    assert provider.calls==1
    # Use fresh call keys. The previous unknown consumes one of two micro units.
    prices(svc,limit=2);provider=fake()
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda key:collect(matrix(route,provider),route,key=key),['second','third']))
    assert sorted(r['elements'][0]['status'] for r in results)==['ok','unknown']
    assert provider.calls==1


def test_travel_time_keeps_straight_line_on_failure_and_equal_coords_not_zero(route):
    svc,_,_=route;prices(svc);provider=fake(fail=True)
    value=session(route,provider)(POINT_A,POINT_B,DEPART,'walking')
    assert value['duration_minutes'] is None and value['straight_line_meters']>0
    equal_coords={**POINT_B,'latitude':POINT_A['latitude'],'longitude':POINT_A['longitude']}
    value=session(route)(POINT_A,equal_coords,DEPART,'walking')
    assert value['basis']=='estimate' and value['duration_minutes']>0
    assert value['straight_line_meters']==0


def test_new_matrix_provider_works_with_legacy_travel_time(route):
    svc,_,_=route;prices(svc);provider=fake()
    value=session(route,provider)(POINT_A,POINT_B,DEPART,'walking')
    assert value['basis']=='provider' and value['duration_minutes']==25
    assert value['straight_line_method']=='haversine_straight_line' and provider.calls==1


def test_zero_spend_environment_overrides_positive_test_budget(route,monkeypatch):
    svc,_,_=route;prices(svc);provider=fake();monkeypatch.setenv('ZERO_SPEND','1')
    assert collect(matrix(route,provider),route)['elements'][0]['reason_codes']==['ZERO_SPEND']
    assert session(route,provider)(POINT_A,POINT_B,DEPART,'walking')['reason_codes']==['ZERO_SPEND']
    assert provider.calls==0


def test_same_points_in_four_private_trips_never_share_cache(service):
    from types import SimpleNamespace
    from tests.test_foundation_api import _trip
    prices(service);provider=fake()
    m=MatrixService(service.app.state.gateway,service.app.state.jobs,provider,clock=lambda:NOW)
    scopes=[]
    for name in ('location-A','location-B'):
        login=service.login(name)
        with service.app.state.db.connect() as con:
            sid=con.execute('SELECT id FROM sessions WHERE user_id=?',(login.user['id'],)).fetchone()[0]
        actor=SimpleNamespace(id=login.user['id'],session_id=sid)
        for _ in range(2):scopes.append((actor,_trip(login.client)))
    for actor,trip in scopes:
        m.collect(actor,trip['id'],[POINT_A],[POINT_B],'walking',DEPART,request_key='same')
    assert provider.calls==4 and len(m.cache)==4
    with pytest.raises(DomainError) as denied:
        m.collect(scopes[0][0],scopes[-1][1]['id'],[POINT_A],[POINT_B],'walking',DEPART,request_key='same')
    assert denied.value.status==404 and provider.calls==4


def test_deleted_trip_late_matrix_cannot_publish_but_charge_settles(route):
    from threading import Event
    svc,actor,trip=route;prices(svc);entered=Event();release=Event()
    class Delayed(FakeRouteProvider):
        def matrix(self,*args,**kwargs):
            entered.set();assert release.wait(5)
            return super().matrix(*args,**kwargs)
    provider=Delayed({('a','b','walking'):{'distance_m':700,'duration_seconds':600}},clock=lambda:NOW)
    m=matrix(route,provider)
    with ThreadPoolExecutor(1) as pool:
        pending=pool.submit(collect,m,route)
        assert entered.wait(5);svc.app.state.repo.delete_trip(actor.id,trip['id']);release.set()
        with pytest.raises(DomainError) as rejected:pending.result()
    assert rejected.value.status==404 and not m.cache
    with svc.app.state.db.connect() as con:
        row=con.execute("SELECT state,result_ref FROM usage_reservations WHERE operation='route_matrix'").fetchone()
    assert row['state']=='settled' and row['result_ref'] is None


def test_explicit_disabled_route_preserves_geometry_without_minutes(route):
    value=session(route,DisabledRouteProvider())(POINT_A,POINT_B,DEPART,'walking')
    assert value['duration_minutes'] is None and value['reason_codes']==['ROUTES_DISABLED']
    assert value['straight_line_meters']>0


def test_travel_accessibility_constraints_are_keyed_and_preserved(route):
    svc,_,_=route;prices(svc)
    provider=FakeRouteProvider({('a','b','walking'):{'distance_m':700,'duration_seconds':600,'accessibility_status':'satisfied'}},clock=lambda:NOW)
    lookup=session(route,provider)
    origin={**POINT_A,'accessibility_constraints':['step_free']}
    assert lookup(origin,POINT_B,DEPART,'walking')['accessibility_status']=='satisfied'
    assert lookup(origin,POINT_B,DEPART,'walking')['usage_permission']['durable_storage'] is True
    lookup({**origin,'accessibility_constraints':['wheelchair']},POINT_B,DEPART,'walking')
    assert provider.calls==2
