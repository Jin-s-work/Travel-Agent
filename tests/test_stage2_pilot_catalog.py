"""Real source catalogue in isolated normal admin APIs, not a live venue audit.

No provider/network call and no production write. The checked-in citations were
reviewed separately; this test verifies product claims from that exact pack.
"""
import json
from pathlib import Path
import pytest
from tests.test_foundation_api import service,_job
from tests.test_discovery_foundation import discovery

PILOT=Path(__file__).resolve().parents[1]/'docs/service-v4/data/iconic-pilot-2026-10-07.json'


def approve_actual_pack(discovery,data):
    response=discovery.client.post('/api/v2/admin/discovery-packs',json=data)
    assert response.status_code==201,response.text
    ident=response.json()['pack_id']
    stored=next(p for p in discovery.client.get('/api/v2/admin/discovery-packs').json()['items'] if p['id']==ident)
    for place in stored['places']:
        for source in place['sources']:
            response=discovery.client.patch('/api/v2/admin/discovery-sources/'+source['id'],json={
                'expected_version':source['version'],'status':'active','read_confirmed':True,'display_permitted':True,
                'policy_version':data['version'],'evidence':'Isolated contract test of checked-in citations; no live source recheck.'})
            assert response.status_code==200,response.text
    response=discovery.client.patch('/api/v2/admin/discovery-packs/'+ident,json={'status':'approved','evidence':'Isolated import verification of explicit branch/source catalogue; operational facts remain unknown.'})
    assert response.status_code==200,response.text
    return stored


@pytest.mark.parametrize('city,zone',[('tokyo','Asia/Tokyo'),('barcelona','Europe/Madrid')])
def test_actual_pilot_normal_import_and_cold_start_recommendation(discovery,city,zone):
    data=next(p for p in json.loads(PILOT.read_text())['packs'] if p['city']==city)
    assert data['synthetic'] is False and len(data['places'])==6
    assert sorted(p['category'] for p in data['places'])==['attraction']*3+['restaurant']*3
    forbidden={'rating','opening_hours','live_availability','max_party','min_party','reservation_methods','price'}
    for place in data['places']:
        assert place['latitude'] is None and place['longitude'] is None
        assert not forbidden.intersection(f['field'] for f in place['facts'])
    stored=approve_actual_pack(discovery,data)
    assert len({p['place_id'] for p in stored['places']})==6
    trip=discovery.client.post('/api/v2/trips',json={'title':'Pilot isolated import '+city,'start_date':'2026-10-10','end_date':'2026-10-12',
        'stops':[{'city':city,'sequence':1,'start_date':'2026-10-10','end_date':'2026-10-12','timezone':zone}]})
    assert trip.status_code==201,trip.text
    trip=trip.json();base='/api/v2/trips/'+trip['id']
    conditions={'city':city,'visit':{'date':'2026-10-10','local_time':None,'timezone':zone},'party':trip['party'],
        'categories':['restaurant','attraction'],'recommendation_types':['local_discovery','landmark'],'origin':None,'budget':None,'preferred':{'tags':[]}}
    saved=discovery.client.patch(base+'/discovery-conditions',json={'expected_version':0,'conditions':conditions})
    assert saved.status_code==200,saved.text
    response=discovery.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'pilot-cold-start-'+city})
    assert response.status_code==202,response.text
    job=_job(discovery.client,response.json());assert job['state']=='succeeded',job
    output=discovery.client.get(base+'/recommendations/'+response.json()['run_id']).json()['result']
    assert output['external_discovery']['calls']==0
    assert not output['sections']['local_discovery']['items'] and not output['sections']['local_discovery']['needs_confirmation']
    assert not output['sections']['landmark']['items']  # opening/party are unverified
    iconic=output['sections']['landmark']['needs_confirmation']
    assert len(iconic)==6
    assert {p['iconic_kind'] for p in iconic}=={'official_landmark','editorial_recognition'}
    for card in iconic:
        assert card['rankable'] and not card['personalized'] and card['score'] is None
        assert card['movement']['straight_line_m'] is None and card['movement']['duration_minutes'] is None
        assert 'HOURS_UNKNOWN' in card['important_unknowns']
        assert card['review_evidence']['counts'] is None and not card['strict_badge']
        detail=discovery.client.get(base+'/places/'+card['place_id']+'/detail')
        assert detail.status_code==200,detail.text
        assert card['latitude'] is None and card['longitude'] is None
    stranger=discovery.login('pilot-outsider-'+city)
    assert stranger.client.get(base+'/recommendations/'+response.json()['run_id']).status_code==404
    assert not discovery.calls
