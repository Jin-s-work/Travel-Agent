"""No network: route estimates, usage receipts, limits and ownership."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from tests.test_foundation_api import service,_trip
from src.foundation.repository import DomainError
from src.itineraries.travel_time import TravelTime
from src.reliability.providers import ProviderResult
from src.reliability.budget import BudgetPolicy

NOW=datetime(2026,10,5,tzinfo=timezone.utc)
A={'id':'a','place_id':'pa','latitude':35.68,'longitude':139.77,'city':'tokyo','coordinate_permitted':True}
B={'id':'b','place_id':'pb','latitude':35.681,'longitude':139.771,'city':'tokyo','coordinate_permitted':True}
DEPART='2026-11-06T03:00:00+00:00'

class FakeRoutes:
    name='synthetic_routes';sku='matrix';adapter_version='test-v1';policy_version='test-policy-v1';usage_permitted=True
    def __init__(self,fail=False):self.calls=0;self.fail=fail
    def lookup(self,request,timeout_seconds):
        self.calls+=1
        if self.fail:raise TimeoutError('synthetic response lost')
        return ProviderResult({'duration_minutes':30,'distance_meters':1200,
            'checked_at':NOW.isoformat(),'expires_at':(NOW+timedelta(days=1)).isoformat(),
            'raw_response':'must not survive normalization'}, {'matrix_elements':1})

@pytest.fixture
def route(service):
    actor=service.login('route');trip=_trip(actor.client)
    with service.app.state.db.connect() as con:
        sid=con.execute('SELECT id FROM sessions WHERE user_id=?',(actor.user['id'],)).fetchone()[0]
    principal=SimpleNamespace(id=actor.user['id'],session_id=sid)
    return service,principal,trip

def configure(service,limit=100):
    cfg=deepcopy(BudgetPolicy.for_tests().config)
    cfg['prices']['synthetic_routes/matrix']={'currency':'USD','rates_per_million':{'matrix_elements':'1'},'max_units':{'matrix_elements':1}}
    cfg['limits']['USD']={k:limit for k in cfg['limits']['USD']}
    service.app.state.budget.policy=BudgetPolicy(cfg)

def session(route,provider=None,**kw):
    svc,actor,trip=route
    return TravelTime(svc.app.state.gateway,svc.app.state.jobs,provider,clock=lambda:NOW,**kw).lookup(actor,trip['id'],request_key='route-test')

def test_estimate_is_explicit_and_no_billing(route):
    lookup=session(route)
    value=lookup(A,B,DEPART,'walking')
    assert value['basis']=='estimate' and value['duration_minutes']>0
    assert value['assumption']['barriers_verified'] is False
    assert lookup(A,B,DEPART,'walking')==value and lookup.stats['cache_hits']==1
    assert lookup.stats['provider_calls']==0 and lookup.stats['cost']['actual_micros']==0

@pytest.mark.parametrize('origin,target,mode',[
    ({'id':'origin'},{'id':'origin'},'walking'),
    ({**A,'coordinate_permitted':False},B,'walking'),
    ({**A,'latitude':float('nan')},B,'walking'),
    (A,{**B,'city':'barcelona','longitude':2.17},'walking'),
    (A,{**B,'longitude':140},'walking'),(A,B,'transit'),(A,B,'car')])
def test_unknowns_never_become_zero(route,origin,target,mode):
    result=session(route)(origin,target,DEPART,mode)
    assert result['basis']=='unknown' and result['duration_minutes'] is None

def test_only_verified_same_location_zero(route):
    assert session(route)(A,A,DEPART,'walking')['duration_minutes']==0

def test_element_bound_and_departure_specific_cache(route):
    lookup=session(route,max_elements=1)
    lookup(A,B,DEPART,'walking')
    assert lookup(A,B,'2026-11-06T04:00:00+00:00','walking')['reason_codes']==['ROUTE_ELEMENT_LIMIT']
    assert lookup.stats['matrix_elements']==1

def test_provider_normalization_cost_and_receipt_resume(route):
    svc,actor,trip=route;configure(svc);provider=FakeRoutes()
    first=session(route,provider);v=first(A,B,DEPART,'walking')
    assert v['basis']=='provider' and v['duration_minutes']==30 and 'raw_response' not in v
    assert first.stats['cost']['actual_micros']==1 and provider.calls==1
    assert all('raw_response' not in p.read_text() for p in svc.settings.artifacts_dir.rglob('*.json'))
    resumed=session(route,provider);assert resumed(A,B,DEPART,'walking')==v
    assert provider.calls==1 and resumed.stats['receipt_replays']==1


def test_response_loss_retains_charge_does_not_reinvoke(route):
    svc,actor,trip=route;configure(svc);provider=FakeRoutes(fail=True)
    lookup=session(route,provider);result=lookup(A,B,DEPART,'walking')
    assert result['basis']=='unknown' and lookup.stats['cost']['actual_micros'] is None
    assert lookup.stats['cost']['pending_micros']==1
    session(route,provider)(A,B,DEPART,'walking')
    assert provider.calls==1
    with svc.app.state.db.connect() as con:
        assert con.execute("SELECT state FROM usage_reservations WHERE operation='route_matrix'").fetchone()[0]=='unknown'


def test_last_budget_for_two_distinct_routes_is_atomic(route):
    svc,actor,trip=route;configure(svc,limit=1);provider=FakeRoutes()
    def call(day):return session(route,provider)(A,B,f'2026-11-0{day}T03:00:00+00:00','walking')
    with ThreadPoolExecutor(2) as pool:values=list(pool.map(call,[6,7]))
    assert sorted(v['basis'] for v in values)==['provider','unknown']
    assert provider.calls==1


def test_policy_and_deleted_scope_reject_cached_use(route):
    svc,actor,trip=route;configure(svc);provider=FakeRoutes()
    travel=TravelTime(svc.app.state.gateway,svc.app.state.jobs,provider,clock=lambda:NOW)
    before=travel.policy_fingerprint();lookup=travel.lookup(actor,trip['id'],request_key='policy')
    lookup(A,B,DEPART,'walking');provider.usage_permitted=False
    assert travel.policy_fingerprint()!=before
    # Policy itself is part of identity, including its permission flag.
    denied=travel.lookup(actor,trip['id'],request_key='policy2')(A,B,DEPART,'walking')
    assert denied['reason_codes']==['ROUTE_POLICY_UNAVAILABLE']
    svc.app.state.repo.delete_trip(actor.id,trip['id'])
    with pytest.raises(DomainError):lookup(A,B,DEPART,'walking')
