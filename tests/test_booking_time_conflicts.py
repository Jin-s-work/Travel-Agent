"""Corrections never become stale answers or validated itinerary times."""
from copy import deepcopy
from src.foundation.booking_times import booking_time_conflicts
from src.itineraries.constraints import validate
from src.itineraries.scheduler import booking_items
from tests.test_foundation_api import service, _trip, _upload
from tests.test_local_mail import local_mode, PACK
from tests.test_itinerary_engine import request, NOW


def _hotel(service, monkeypatch):
    user = local_mode(service, monkeypatch)
    trip = _trip(user.client)
    _upload(user.client, trip['id'], name='hotel.eml', content=(PACK/'02-tokyo-hotel.eml').read_bytes())
    base = f"/api/v2/trips/{trip['id']}"
    return user, trip, base, user.client.get(base+'/bookings').json()['items'][0]


def test_summary_time_correction_is_answered_and_blocks_itinerary_until_aligned(service, monkeypatch):
    user, trip, base, original = _hotel(service, monkeypatch)
    changed = user.client.patch(base+'/bookings/'+original['id'],json={'expected_version':original['version'],'changes':[{'field_path':'time','value':'16:00'}]}).json()
    assert changed['time_conflicts'][0]['side'] == 'start'
    assert changed['time_conflicts'][0]['summary']['time'] == '16:00'
    assert changed['time_conflicts'][0]['event_local'] == '2026-11-06T15:00:00'
    answer = user.client.post(base+'/ask',json={'question':'첫날 전체 예약 알려줘'}).json()['answer']
    assert '16:00' in answer and '15:00' not in answer and '확인 필요' in answer
    direct = user.client.post(base+'/ask',json={'question':changed['provider']+' 체크인 몇 시야?'}).json()['answer']
    assert '16:00' in direct and '15:00' not in direct
    snapshot = request(end_date='2026-11-09')
    items, _ = booking_items([changed], snapshot)
    validation = validate(items,[],snapshot,[],NOW,bookings=[changed])
    assert validation['validation_status'] == 'conflicted'
    assert any(c['code']=='BOOKING_TIME_CONFLICT' and c['state']=='violated' for c in validation['conflicts'])
    event = changed['events'][0]
    aligned = user.client.patch(base+'/bookings/'+changed['id'],json={'expected_version':changed['version'],'changes':[{'field_path':f"events.{event['id']}.start_local",'value':'2026-11-06T16:00:00'}]}).json()
    assert aligned['time_conflicts'] == []
    items, _ = booking_items([aligned], snapshot)
    assert not any(c['code']=='BOOKING_TIME_CONFLICT' for c in validate(items,[],snapshot,[],NOW,bookings=[aligned])['conflicts'])
    assert original['events'][0]['start_local'] == '2026-11-06T15:00:00'


def test_only_event_correction_is_not_answered_as_old_summary(service, monkeypatch):
    user, trip, base, original = _hotel(service, monkeypatch)
    event = original['events'][0]
    changed = user.client.patch(base+'/bookings/'+original['id'],json={'expected_version':original['version'],'changes':[{'field_path':f"events.{event['id']}.start_local",'value':'2026-11-06T17:00:00'}]}).json()
    assert changed['time_conflicts'][0]['event_origin'] == 'user'
    answer = user.client.post(base+'/ask',json={'question':'첫날 전체 예약 알려줘'}).json()['answer']
    assert '17:00' in answer and '15:00' not in answer
    direct = user.client.post(base+'/ask',json={'question':changed['provider']+' 체크인 몇 시야?'}).json()['answer']
    assert '17:00' in direct and '15:00' not in direct


def test_changed_summary_date_remains_discoverable_with_conflict(service, monkeypatch):
    user, trip, base, original = _hotel(service, monkeypatch)
    changed = user.client.patch(base+'/bookings/'+original['id'],json={'expected_version':original['version'],'changes':[{'field_path':'date','value':'2026-11-07'}]}).json()
    assert changed['time_conflicts']
    answer = user.client.post(base+'/ask',json={'question':'둘째 날 전체 예약 알려줘'}).json()['answer']
    assert '2026-11-07' in answer and '대표 시각과 구간별 시각이 달라' in answer


def test_roundtrip_end_correction_identifies_only_return_arrival():
    extracted = {'date':'2026-11-06','time':'09:00','date_end':'2026-11-09','time_end':'22:40', 'events':[
        {'id':'out','start_local':'2026-11-06T09:00:00','end_local':'2026-11-06T11:30:00'},
        {'id':'return','start_local':'2026-11-09T20:00:00','end_local':'2026-11-09T22:40:00'}]}
    effective = deepcopy(extracted); effective['time_end'] = '23:00'
    conflicts = booking_time_conflicts(extracted,effective,{'time_end':'23:00'})
    assert len(conflicts)==1 and conflicts[0]['event_id']=='return' and conflicts[0]['side']=='end'
    assert effective['events'] == extracted['events']
    effective = deepcopy(extracted); effective['events'][1]['start_local'] = '2026-11-09T19:00:00'
    assert booking_time_conflicts(extracted,effective,{'events.return.start_local':'2026-11-09T19:00:00'}) == []
