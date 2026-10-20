"""V5 dates/stop selection use a pinned clock, never the test machine's day."""
from datetime import datetime, timezone
import pytest
from src.discovery.context import resolve
from src.foundation.models import valid_date
from tests.test_discovery_intents import trip_for
from tests.test_discovery_foundation import discovery
from tests.test_foundation_api import service


def trip():
    return dict(id='t',owner_id='a',version=1,start_date='2026-10-08',end_date='2026-10-12',party={'adults':None},stops=[dict(id='tokyo',city='Tokyo',start_date='2026-10-08',end_date='2026-10-10',timezone='Asia/Tokyo'),dict(id='madrid',city='Madrid',start_date='2026-10-10',end_date='2026-10-12',timezone='Europe/Madrid')])


def test_clock_local_today_future_completed_and_overlap():
    t=trip()
    r=resolve(t,{},clock=datetime(2026,10,8,16,tzinfo=timezone.utc))
    assert r['conditions']['visit']['date']=='2026-10-09'
    assert r['selection']['date_reason']=='city_local_today'
    assert r['trip_context']['stop_id']=='tokyo'
    assert resolve(t,{},clock=datetime(2026,9,1,tzinfo=timezone.utc))['conditions']['visit']['date']=='2026-10-08'
    r=resolve(t,{},basis={'visit_date':'2026-10-11'},clock=datetime(2026,11,1,tzinfo=timezone.utc))
    assert r['selection']['date_reason']=='completed_previous'
    assert r['trip_context']['stop_id']=='madrid'
    r=resolve(t,{'visit':{'date':'2026-10-10'}})
    assert r['selection']['state']=='needs_selection'
    assert r['validation'][0]['code']=='STOP_CHOICE_REQUIRED'
    assert resolve(t,{'visit':{'date':'2026-10-10'}},'madrid')['validation']==[]


def test_gap_not_silently_reassigned_and_deleted_stop_preserved():
    t=trip();t['stops'][1]['start_date']='2026-10-12'
    r=resolve(t,{'visit':{'date':'2026-10-11'}})
    assert r['validation'][0]['code']=='STOP_DATE_GAP'
    assert r['trip_context']['stop_id'] is None
    r=resolve(t,{'visit':{'date':'2026-10-08'}},'deleted')
    assert r['validation'][0]['code']=='STOP_REMOVED'
    assert r['trip_context']['stop_id']=='deleted'


@pytest.mark.parametrize('value',['000000-01-01','0000-01-01','2025-02-29','2026-1-01',20261008])
def test_invalid_calendar(value):
    with pytest.raises((TypeError,ValueError)):valid_date(value)


def test_preview_owned_read_without_mutation_or_job(discovery):
    t=trip_for(discovery.client);url=f"/api/v2/trips/{t['id']}/discovery-context/preview"
    payload={'expected_trip_version':t['version'],'expected_conditions_version':0,'overrides':{},'visit_date':t['start_date']}
    r=discovery.client.post(url,json=payload)
    assert r.status_code==200,r.text
    assert r.json()['context_state']=='ready'
    assert r.json()['conditions']['party']['adults'] is None
    assert r.json()['trip_context']['stop_id']==t['stops'][0]['id']
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM jobs WHERE trip_id=?',(t['id'],)).fetchone()[0]==0
        assert con.execute('SELECT count(*) FROM discovery_conditions WHERE trip_id=?',(t['id'],)).fetchone()[0]==0
    other=discovery.login('v5-other')
    assert other.client.post(url,json=payload).status_code==404
    assert discovery.client.post(url,json={**payload,'expected_trip_version':55}).status_code==409
    assert discovery.client.post(url,json={**payload,'expected_conditions_version':10}).status_code==409
    assert discovery.client.post(url,json={**payload,'visit_date':'0000-01-01'}).status_code==422
