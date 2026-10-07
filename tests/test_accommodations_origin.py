"""Pure civil-date origin contracts, distinct from live geocoding quality."""
from copy import deepcopy
import pytest
from src.accommodations.origin import resolve_origin,coordinates

CLOCK='2026-10-06T00:00:00+00:00'
def trip(city='tokyo',zone='Asia/Tokyo'):
    return {'id':'trip_a','version':1,'start_date':'2026-11-01','end_date':'2026-11-10','stops':[{'id':'stop_a','city':city,'timezone':zone,'start_date':'2026-11-01','end_date':'2026-11-10'}]}
def stay(ident='stay_a',**changes):
    return {'id':ident,'version':1,'stop_id':'stop_a','display_name':'Synthetic hotel','identity_state':'confirmed','checkin_date':'2026-11-01','checkout_date':'2026-11-05','checkin_time':None,'checkout_time':None,'dates_confirmed':False,
        'identity':{'latitude':35.5,'longitude':0,'provider_place_id':'external_a','checked_at':CLOCK,'expires_at':'2026-10-07T00:00:00+00:00','coordinate_permitted':True},**changes}
def resolve(stays,day='2026-11-03',time=None,overrides=None,t=None):
    return resolve_origin(t or trip(),stays,{'date':day,'local_time':time,'stop_id':'stop_a'},overrides or {},CLOCK)

def test_no_stay_and_unresolved_saved_label_are_distinct():
    assert resolve([])['status']=='missing'
    r=resolve([stay(identity_state='unresolved',identity={})])
    assert r['status']=='unresolved' and r['origin'] is None and r['label']=='Synthetic hotel'

def test_zero_coordinate_is_valid_but_no_coordinate_invention():
    assert resolve([stay()])['origin']['longitude']==0
    for lat,lon in [(91,0),(0,181),(float('nan'),0),(0,float('inf')),(True,0),(None,0)]:
        assert coordinates({'latitude':lat,'longitude':lon}) is None
    r=resolve([stay(identity={'latitude':None,'longitude':None})]);assert r['status']=='unresolved'

def test_checkout_not_all_day_and_explicit_choice():
    r=resolve([stay()],day='2026-11-05');assert r['status']=='unknown' and r['candidates'][0]['origin_role']=='checkout_before'
    r=resolve([stay()],day='2026-11-05',overrides={'origin_selection':{'kind':'accommodation','accommodation_id':'stay_a'}})
    assert r['status']=='ready' and r['origin']['origin_role']=='checkout_before'
    assert resolve([stay(checkout_time='11:00')],day='2026-11-05',time='10:30')['status']=='ready'
    assert resolve([stay(checkout_time='11:00')],day='2026-11-05',time='11:00')['status']=='unknown'

def test_switch_day_checks_time_or_requests_choice():
    stays=[stay(checkout_time='11:00'),stay('stay_b',checkin_date='2026-11-05',checkout_date='2026-11-10',checkin_time='15:00')]
    assert resolve(stays,day='2026-11-05')['status']=='ambiguous'
    assert resolve(stays,day='2026-11-05',time='10:00')['origin']['accommodation_id']=='stay_a'
    assert resolve(stays,day='2026-11-05',time='12:00')['status']=='unknown'
    assert resolve(stays,day='2026-11-05',time='16:00')['origin']['accommodation_id']=='stay_b'

def test_overlap_gap_explicit_version_and_deletion():
    stays=[stay(),stay('stay_b')]
    assert resolve(stays)['status']=='ambiguous'
    chosen=resolve(stays,overrides={'origin_selection':{'kind':'accommodation','accommodation_id':'stay_b','expected_version':1}})
    assert chosen['origin']['accommodation_id']=='stay_b'
    assert resolve(stays,overrides={'origin_selection':{'kind':'accommodation','accommodation_id':'stay_b','expected_version':2}})['status']=='unknown'
    assert resolve(stays,day='2026-11-08')['reason_codes']==['ACCOMMODATION_GAP']
    assert resolve([stay(deleted_at=CLOCK)])['status']=='missing'

def test_unknown_dates_and_dates_outside_trip_not_moved():
    value=stay(checkin_date=None,checkout_date=None);before=deepcopy(value)
    assert resolve([value])['reason_codes']==['STAY_DATES_UNKNOWN'] and value==before
    value=stay(checkin_date='2026-10-01');r=resolve([value]);assert r['warnings'][0]['code']=='STAY_OUTSIDE_TRIP'
    assert r['origin']['dates_status']=='suggested' and r['booking_status']=='not_inferred'

@pytest.mark.parametrize('city,zone',[('tokyo','Asia/Tokyo'),('madrid','Europe/Madrid'),('new-york','America/New_York'),('sydney','Australia/Sydney')])
def test_city_timezones_are_preserved(city,zone):
    value=resolve([stay()],t=trip(city,zone));assert value['status']=='ready'

def test_dst_ambiguous_visit_is_unknown():
    t=trip('new-york','America/New_York')
    assert resolve([stay()],day='2026-11-01',time='01:30',t=t)['reason_codes']==['VISIT_TIME_AMBIGUOUS']

def test_legacy_manual_coordinate_provenance_with_no_stop():
    t=trip();t['stops']=[]
    result=resolve([stay()],t=t,overrides={'origin':{'latitude':0,'longitude':0,'label':'Manual'}})
    assert result['status']=='ready' and result['origin']['provenance']=='user_entered'
    assert result['origin']['checked_at'] is None

def test_versions_are_stable_and_change_on_context_change():
    assert resolve([stay()])==resolve([stay()])
    base=resolve([stay()])['origin_version']
    assert resolve([stay(version=2)])['origin_version']!=base
    assert resolve([stay()],day='2026-11-04')['origin_version']!=base
    assert resolve([stay(identity={**stay()['identity'],'latitude':36})])['origin_version']!=base

def test_expired_coordinates_and_none_do_not_fallback():
    assert resolve([stay(identity={**stay()['identity'],'expires_at':CLOCK})])['reason_codes']==['ACCOMMODATION_COORDINATES_EXPIRED']
    assert resolve([stay()],overrides={'origin_selection':{'kind':'none'}})['origin'] is None
