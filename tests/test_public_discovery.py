"""Bounded free discovery: real SQL/auth/jobs; no live requests in pytest."""
from datetime import datetime, timedelta, timezone
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src.discovery.public_places import (PublicDiscovery, CENTERS, ATTRIBUTION, MAX_BYTES, POLICY,
                                        center, normalize, normalize_photon, query, fetch, MAX_CANDIDATES)
from src.discovery.safe_fetch import FetchRejected
from src.destinations import CITIES
from src.foundation.repository import DomainError
from tests.test_foundation_api import service, _job


def payload(city='paris', count=2):
    c=center(city)
    return {'elements':[{'id':700000000+i,'type':'node','lat':c['latitude']+.002*i,'lon':c['longitude'],
                         'tags':{'name':f'Synthetic public restaurant {i}','amenity':'restaurant','cuisine':'regional;vegetarian','opening_hours':'Mo-Su 12:00-22:00','addr:street':'Fixture Street','addr:housenumber':str(i+1),'website':'https://example.org/restaurant'}} for i in range(count)]}


@pytest.fixture
def public(service):
    calls=[]
    clock=[datetime.now(timezone.utc)]
    def fake(city):
        calls.append(city)
        return normalize(payload(city),city),1000
    service.app.state.discovery.public_provider=PublicDiscovery(service.app.state.db,fetcher=fake,clock=lambda:clock[0])
    user=service.login('public-owner')
    return SimpleNamespace(**vars(service),client=user.client,user=user.user,calls_public=calls,clock=clock)


def prepare(public,city='paris'):
    trip=public.client.post('/api/v2/trips',json={'title':'Synthetic public discovery','start_date':'2026-11-06','end_date':'2026-11-09','stops':[{'city':city,'sequence':1,'start_date':'2026-11-06','end_date':'2026-11-09','timezone':CITIES[city]['timezone']}]}).json()
    base=f"/api/v2/trips/{trip['id']}"
    conditions=public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions.update(categories=['restaurant'],recommendation_types=['local_discovery'],origin=None)
    result=public.client.patch(base+'/discovery-conditions',json={'expected_version':0,'conditions':conditions})
    assert result.status_code==200,result.text
    return trip,base


def run(public,trip,base,key='public-search-one',**changes):
    receipt=public.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1,**changes},headers={'Idempotency-Key':key})
    assert receipt.status_code==202,receipt.text
    job=_job(public.client,receipt.json())
    assert job['state']=='succeeded',job
    output=public.client.get(base+'/recommendations/'+receipt.json()['run_id'])
    assert output.status_code==200,output.text
    assert output.json()['result'],output.text
    return output.json()


def test_all_100_sourced_centers_and_constant_bounded_queries():
    assert len(CENTERS['cities'])==100 and set(CENTERS['cities'])==set(CITIES)
    assert CENTERS['attribution'].startswith('City centers: GeoNames')
    for city,c in CENTERS['cities'].items():
        assert c['country_code']==CITIES[city]['country_code']
        assert -90<=c['latitude']<=90 and -180<=c['longitude']<=180
        assert c['source_url']=='https://www.geonames.org/'+c['geonames_id']+'/'
        from urllib.parse import parse_qs
        params=parse_qs(query(city))
        assert c['radius_m']==3000 and params['radius']==['3.0'] and params['limit']==['60']
        assert params['osm_tag']==['amenity:restaurant','amenity:cafe']
    assert center('new-delhi')['geonames_id']=='1261481'
    with pytest.raises(DomainError):query('paris);out;')


def test_normalizer_rejects_bad_geography_tags_ids_and_deduplicates():
    raw=payload();original=copy.deepcopy(raw['elements'][0])
    raw['elements'] += [original,{**original,'id':800000000,'lat':0,'lon':0},
                       {**original,'id':800000001,'type':'way','center':{'lat':original['lat'],'lon':original['lon']}},
                       {**original,'id':True},{**original,'id':800000003,'lat':float('nan')},
                       {**original,'id':800000004,'tags':{'name':'Bad category','amenity':'hospital'}}]
    out=normalize(raw,'paris')
    assert len(out)==2 and out[0]['source_url'].startswith('https://www.openstreetmap.org/node/')
    assert 'rating' not in out[0] and out[0]['observed_tags']['opening_hours']
    raw=payload();raw['elements'][0]['tags']['website']='https://127.0.0.1/admin'
    assert 'website' not in normalize(raw,'paris')[0]['observed_tags']
    with pytest.raises(DomainError):normalize({'elements':[],'remark':'timeout'},'paris')
    with pytest.raises(DomainError):normalize(payload(count=61),'paris')


def test_transport_uses_fixed_https_no_redirect_bounds(monkeypatch):
    seen=[]
    def fake(url,**kwargs):
        seen.append((url,kwargs));return SimpleNamespace(mime='application/json',content=json.dumps(photon_payload()).encode())
    monkeypatch.setattr('src.discovery.public_places.fetch_public',fake)
    result,size=fetch('paris')
    assert len(result)==2 and size>0
    url,limits=seen[0]
    assert url.startswith('https://photon.komoot.io/reverse?lat=')
    assert limits=={'max_bytes':350000,'timeout_seconds':15,'max_redirects':0}
    assert 'hotel' not in url and '2026' not in url


def test_public_candidates_are_provisional_saved_and_cached_while_paid_halted(public):
    trip,base=prepare(public)
    with public.app.state.db.connect() as con:
        con.execute("INSERT INTO cost_controls VALUES('USD',1,'ZERO_SPEND','2026-10-06T00:00:00+00:00')")
    availability=public.client.get(base+'/discovery-conditions').json()['catalog_availability']
    assert availability['public_discovery_enabled'] and availability['state']=='public_search_available'
    assert public.calls_public==[] # reading never fetches
    output=run(public,trip,base)
    result=output['result'];cards=result['sections']['reference']['needs_confirmation']
    assert len(cards)==2 and result['summary']['qualified_count']==0
    assert result['sections']['local_discovery']['items']==[]
    assert result['public_discovery']['calls']==1 and result['public_discovery']['cost']['micros']==0
    assert cards[0]['score'] is None and cards[0]['identity_status']=='needs_confirmation'
    assert cards[0]['source_kind']=='public_map' and cards[0]['attribution']==ATTRIBUTION
    assert cards[0]['facts'][0]['status']=='provisional' and cards[0]['facts'][0]['usable']
    assert cards[0]['source_refs'][0]['source_group']=='OpenStreetMap'
    place=cards[0]['place_id']
    saved=public.client.post(base+'/bookmarks',json={'input_kind':'place','input_value':place,'note':'my place'})
    assert saved.status_code==201,saved.text
    detail=public.client.get(base+'/places/'+place+'/detail').json()
    assert detail['place']['source_kind']=='public_map' and detail['review_evidence']['evaluation']['strict_pass'] is False
    second=run(public,trip,base,'public-search-two')
    assert second['result']['public_discovery']['cache_hit'] and public.calls_public==['paris']
    public.client.get(base+'/recommendations/'+output['run_id'])
    assert public.calls_public==['paris']
    with public.app.state.db.connect() as con:
        ledger=con.execute('SELECT * FROM usage_reservations').fetchall()
        assert len(ledger)==1 and ledger[0]['actual_cost_micros']==0 and ledger[0]['state']=='settled'
        assert con.execute("SELECT halted FROM cost_controls WHERE currency='USD'").fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM place_facts WHERE status='verified'").fetchone()[0]==0
    outsider=public.login('public-outsider')
    assert outsider.client.get(base+'/places/'+place+'/detail').status_code==404
    assert outsider.client.get(base+'/recommendations/'+output['run_id']).status_code==404


def test_strict_review_and_explicit_distance_filters_are_not_fabricated(public):
    trip,base=prepare(public)
    run(public,trip,base,'public-initial-cache')
    out=run(public,trip,base,review_language_filter={'required':True,'apply_to':['local_discovery']})
    group=out['result']['sections']['local_discovery']
    assert group['items']==[] and all('REVIEW_REQUIRED_UNSUPPORTED' in p['reason_codes'] for p in group['needs_confirmation'])
    conditions=public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions['origin']={'label':'Far from center','latitude':0,'longitude':0}
    conditions['radius_m']=1000
    assert public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':conditions}).status_code==200
    receipt=public.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':2},headers={'Idempotency-Key':'public-distance'})
    assert _job(public.client,receipt.json())['state']=='succeeded'
    out=public.client.get(base+'/recommendations/'+receipt.json()['run_id']).json()['result']
    assert out['summary']['displayable_count']==0
    assert all('OUTSIDE_RADIUS' in p['reason_codes'] for p in out['sections']['local_discovery']['excluded'])


def test_empty_and_error_are_cached_actionable_and_charge_zero(public):
    trip,base=prepare(public)
    def failure(city):
        public.calls_public.append(city);raise FetchRejected('FETCH_TIMEOUT')
    public.app.state.discovery.public_provider.fetcher=failure
    first=run(public,trip,base)
    assert first['result']['public_discovery']['reason']=='FETCH_TIMEOUT'
    assert first['result']['summary']['empty_state']['actions']==['retry','save_place']
    second=run(public,trip,base,'public-retry')
    assert second['result']['public_discovery']['reason']=='PUBLIC_DISCOVERY_COOLDOWN'
    assert public.calls_public==['paris']
    with public.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM candidate_packs').fetchone()[0]==0
        row=con.execute('SELECT * FROM usage_reservations').fetchone()
        assert row['actual_cost_micros']==0 and row['error_code']=='FETCH_TIMEOUT'
    public.clock[0]+=timedelta(minutes=16)
    public.app.state.discovery.public_provider.fetcher=lambda city:([],123)
    out=run(public,trip,base,'public-empty')
    assert out['result']['public_discovery']['state']=='empty'
    cached=run(public,trip,base,'public-empty-cached')
    assert cached['result']['public_discovery']['cache_hit']


def test_operator_pause_disables_new_calls(public):
    from src.operations.controls import update
    trip,base=prepare(public)
    update(public.app.state.db,external_enabled=False)
    out=run(public,trip,base)
    assert out['result']['public_discovery']['reason']=='EXTERNAL_CALLS_PAUSED'
    assert public.calls_public==[]


def test_every_city_can_normalize_real_schema_without_live_network():
    for city in CITIES:
        out=normalize(payload(city),city)
        assert len(out)==2 and all(p['center_distance_m']<=3000 for p in out)


def test_display_is_bounded_without_fabricating_ranking(public):
    trip,base=prepare(public)
    public.app.state.discovery.public_provider.fetcher=lambda city:(normalize(payload(city,60),city),MAX_BYTES)
    output=run(public,trip,base)
    cards=output['result']['sections']['reference']['needs_confirmation']
    assert len(cards)==12 and all(p['score'] is None for p in cards)
    assert output['result']['public_discovery']['display_limit']==12


def test_daily_limit_counts_errors_and_blocks_sixth_user_call(public):
    trip,base=prepare(public)
    def failure(city):
        public.calls_public.append(city);raise FetchRejected('FETCH_TIMEOUT')
    public.app.state.discovery.public_provider.fetcher=failure
    for i in range(5):
        out=run(public,trip,base,'limited-attempt-'+str(i))
        assert out['result']['public_discovery']['calls']==1
        public.clock[0]+=timedelta(minutes=16)
    out=run(public,trip,base,'limited-attempt-last')
    assert out['result']['public_discovery']['reason']=='PUBLIC_DISCOVERY_DAILY_LIMIT'
    assert len(public.calls_public)==5


def test_cancel_during_fetch_does_not_publish_or_populate_public_cache(public):
    trip,base=prepare(public);started,release=threading.Event(),threading.Event()
    def blocked(city):
        started.set();assert release.wait(5)
        return normalize(payload(city),city),1000
    public.app.state.discovery.public_provider.fetcher=blocked
    receipt=public.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'cancel-public-fetch'})
    try:
        assert started.wait(4)
        assert public.client.post('/api/v2/jobs/'+receipt.json()['job_id']+'/cancel').status_code==202
    finally:release.set()
    assert _job(public.client,receipt.json())['state']=='cancelled'
    with public.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM candidate_packs').fetchone()[0]==0
        record=con.execute('SELECT * FROM usage_reservations').fetchone()
        assert record['state']=='settled' and record['actual_cost_micros']==0 and record['error_code']=='JOB_CANCELLED'


def test_expired_public_snapshot_is_not_servable_and_requests_fresh_data(public):
    trip,base=prepare(public)
    first=run(public,trip,base)
    cutoff=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat()
    with public.app.state.db.connect() as con:
        con.execute("UPDATE candidate_packs SET updated_at=? WHERE id='osm_pack_paris'",(cutoff,))
        con.execute('UPDATE place_facts SET expires_at=? WHERE policy_version=?',(cutoff,POLICY))
    current=public.client.get(base+'/recommendations/'+first['run_id']).json()
    assert current['data_status']=='stale' and current['result']['withheld_place_ids']
    assert not current['result']['sections']['reference']['needs_confirmation']
    assert public.calls_public==['paris']
    public.clock[0]+=timedelta(minutes=16)
    refreshed=run(public,trip,base,'public-refreshed')
    assert refreshed['result']['public_discovery']['calls']==1 and public.calls_public==['paris','paris']


def test_strict_filter_skips_fresh_public_call(public):
    trip,base=prepare(public)
    out=run(public,trip,base,review_language_filter={'required':True,'apply_to':['local_discovery']})
    assert public.calls_public==[]
    assert out['result']['public_discovery']['reason']=='PUBLIC_REVIEW_FILTER_UNSUPPORTED'
    assert out['result']['summary']['empty_state']['actions']==['edit_conditions','save_place']


def test_insufficient_curated_city_supplements_with_public_call(public):
    from tests.discovery_synthetic import pack
    from tests.test_discovery_foundation import import_pack
    public.admin=SimpleNamespace(id=public.user['id'])
    with public.app.state.db.connect() as con:
        con.execute("UPDATE users SET role='admin' WHERE id=?",(public.user['id'],))
    data=pack('tokyo')
    imported=import_pack(public,data)
    # Explicit synthetic fixture exercises the real flag/approval branch; no real place claim.
    with public.app.state.db.connect() as con:
        con.execute('UPDATE candidate_packs SET synthetic=0 WHERE id=?',(imported['id'],))
    trip,base=prepare(public,'tokyo')
    out=run(public,trip,base)
    assert public.calls_public==['tokyo']
    assert out['result']['public_discovery']['calls']==1
    assert out['result']['summary']['catalog_count']>0


def test_shared_rate_limits_apply_across_cities(public,monkeypatch):
    trip,base=prepare(public,'paris');run(public,trip,base)
    second_trip,second_base=prepare(public,'london')
    out=run(public,second_trip,second_base,'public-other-city-too-soon')
    assert out['result']['public_discovery']['reason']=='PUBLIC_DISCOVERY_COOLDOWN'
    assert public.calls_public==['paris']
    public.clock[0]+=timedelta(seconds=16)
    monkeypatch.setattr('src.discovery.public_places.GLOBAL_DAILY',1)
    out=run(public,second_trip,second_base,'public-global-limit')
    assert out['result']['public_discovery']['reason']=='PUBLIC_DISCOVERY_DAILY_LIMIT'
    assert public.calls_public==['paris']


def test_deletion_during_fetch_keeps_cache_empty_and_private_result_inaccessible(public):
    from tests.test_reliability_api import eventually
    trip,base=prepare(public);started,release=threading.Event(),threading.Event()
    def blocked(city):
        started.set();assert release.wait(5)
        return normalize(payload(city),city),1000
    public.app.state.discovery.public_provider.fetcher=blocked
    receipt=public.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'delete-public-fetch'})
    try:
        assert started.wait(4)
        deletion=public.client.delete(base)
        assert deletion.status_code==202
        assert public.client.get(base+'/recommendations/'+receipt.json()['run_id']).status_code==404
    finally:release.set()
    eventually(lambda:public.client.get(deletion.json()['receipt_url']).json()['state']=='succeeded')
    with public.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM candidate_packs').fetchone()[0]==0
        record=con.execute('SELECT * FROM usage_reservations WHERE provider=?',('openstreetmap',)).fetchone()
        assert record['state']=='settled' and record['actual_cost_micros']==0



def test_public_display_cap_applies_after_requested_category(public):
    trip,base=prepare(public)
    def mixed(city):
        public.calls_public.append(city)
        raw=payload(city,14)
        raw['elements'][12]['tags']['amenity']='cafe'
        return normalize(raw,city),2000
    public.app.state.discovery.public_provider.fetcher=mixed
    restaurants=run(public,trip,base,'public-restaurants-before-cafe')
    assert len(restaurants['result']['sections']['reference']['needs_confirmation'])==12
    conditions=public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions['categories']=['cafe']
    patched=public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':conditions})
    assert patched.status_code==200,patched.text
    cafes=run(public,trip,base,'public-cafe-after-twelve-restaurants',conditions_version=2)
    cards=cafes['result']['sections']['reference']['needs_confirmation']
    assert [p['place_id'] for p in cards]==['osm_node_700000012']
    assert cards[0]['category']=='cafe' and cafes['result']['public_discovery']['cache_hit']
    assert public.calls_public==['paris']
    # Revalidation uses each run's captured categories, not today's conditions.
    prior=public.client.get(base+'/recommendations/'+restaurants['run_id']).json()
    assert prior['data_status']=='current'
    assert len(prior['result']['sections']['reference']['needs_confirmation'])==12


@pytest.mark.parametrize('removed_by',['user_exclusion','source_withdrawal'])
def test_public_display_replenishes_after_exclusion_or_source_withdrawal(public,removed_by):
    trip,base=prepare(public)
    def many(city):
        public.calls_public.append(city)
        return normalize(payload(city,14),city),2000
    public.app.state.discovery.public_provider.fetcher=many
    first=run(public,trip,base,'public-before-removal')
    cards=first['result']['sections']['reference']['needs_confirmation']
    initial={p['place_id'] for p in cards}
    assert len(initial)==12 and 'osm_node_700000012' not in initial
    removed='osm_node_700000000'
    if removed_by=='user_exclusion':
        response=public.client.post(base+'/excluded-places/'+removed)
        assert response.status_code==200,response.text
    else:
        with public.app.state.db.connect() as con:
            con.execute("UPDATE users SET role='admin' WHERE id=?",(public.user['id'],))
        response=public.client.patch('/api/v2/admin/discovery-sources/osm_source_node_700000000',json={
            'expected_version':1,'status':'revoked','read_confirmed':True,'display_permitted':False,
            'policy_version':POLICY,'evidence':'Synthetic source withdrawal for bounded selection test'})
        assert response.status_code==200,response.text
    stale=public.client.get(base+'/recommendations/'+first['run_id']).json()
    assert stale['data_status']=='stale' and removed in stale['result']['withheld_place_ids']
    assert removed not in {p['place_id'] for p in stale['result']['sections']['reference']['needs_confirmation']}
    refreshed=run(public,trip,base,'public-after-removal')
    cards=refreshed['result']['sections']['reference']['needs_confirmation']
    assert {p['place_id'] for p in cards}==initial-{removed}|{'osm_node_700000012'}
    assert len(cards)==12 and all(not p.get('excluded') for p in cards)
    assert refreshed['result']['public_discovery']['cache_hit']
    assert public.calls_public==['paris']



def test_public_ready_cache_with_no_matching_category_offers_condition_edit(public):
    trip,base=prepare(public)
    run(public,trip,base,'public-restaurants-only-cache')
    conditions=public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions['categories']=['cafe']
    patched=public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':conditions})
    assert patched.status_code==200,patched.text
    output=run(public,trip,base,'public-no-cafes-in-cache',conditions_version=2)
    result=output['result']
    assert result['public_discovery']['state']=='ready' and result['public_discovery']['cache_hit']
    assert result['summary']['displayable_count']==0
    assert result['summary']['empty_state']['code']=='PUBLIC_DISCOVERY_NO_MATCHES'
    assert result['summary']['empty_state']['actions']==['edit_conditions','save_place']
    assert public.calls_public==['paris']


def test_provider_retry_after_applies_across_cities_and_is_persisted(public):
    trip,base=prepare(public)
    def refused(city):
        public.calls_public.append(city)
        raise FetchRejected('HTTP_UNAVAILABLE',http_status=429,retry_after_seconds=1800)
    public.app.state.discovery.public_provider.fetcher=refused
    first=run(public,trip,base)
    assert first['result']['public_discovery']['reason']=='PUBLIC_DISCOVERY_RATE_LIMITED'
    assert first['result']['public_discovery']['retry_after_seconds']==1800
    other,other_base=prepare(public,'london')
    public.clock[0]+=timedelta(seconds=60)
    blocked=run(public,other,other_base,'blocked-other-city')
    assert blocked['result']['public_discovery']['retry_after_seconds']==1740
    assert public.calls_public==['paris']
    with public.app.state.db.connect() as con:
        units=json.loads(con.execute("SELECT actual_units_json FROM usage_reservations WHERE provider='openstreetmap'").fetchone()[0])
        assert units['response_bytes'] is None and units['http_status']==429


def test_network_wait_counts_down_and_daily_reset_is_next_utc_midnight(public,monkeypatch):
    trip,base=prepare(public)
    def failure(city):raise FetchRejected('CONNECT_FAILED')
    public.app.state.discovery.public_provider.fetcher=failure
    first=run(public,trip,base)
    assert first['result']['public_discovery']['retry_after_seconds']==120
    assert '연결하지' in first['result']['summary']['empty_state']['title']
    public.clock[0]+=timedelta(seconds=30)
    second=run(public,trip,base,'connection-cooldown')
    assert second['result']['public_discovery']['retry_after_seconds']==90
    assert '연결이 끊겨' in second['result']['summary']['empty_state']['description']
    public.clock[0]+=timedelta(minutes=3)
    monkeypatch.setattr('src.discovery.public_places.USER_DAILY',0)
    last=run(public,trip,base,'public-day-cap')
    status=last['result']['public_discovery']
    assert status['reason']=='PUBLIC_DISCOVERY_DAILY_LIMIT'
    assert status['retry_at'].endswith('T00:00:00+00:00')
    assert '오늘' in last['result']['summary']['empty_state']['title']


def photon_payload(city='paris'):
    c=center(city)
    return {'type':'FeatureCollection','features':[{'type':'Feature','properties':{'osm_type':'N','osm_id':800000000+i,'osm_key':'amenity','osm_value':'restaurant','name':'Synthetic Photon '+str(i),'street':'Fixture Street','housenumber':str(i),'countrycode':c['country_code'].lower()},'geometry':{'type':'Point','coordinates':[c['longitude'],c['latitude']+.001*i]}} for i in range(2)]}


def test_photon_adapter_keeps_osm_identity_radius_and_unconfirmed_metadata():
    raw=photon_payload(); raw['features'][1]['properties']['osm_type']='W'
    out=normalize_photon(raw,'paris')
    assert len(out)==2 and out[1]['external_id']=='way/800000001'
    assert out[0]['source_url']=='https://www.openstreetmap.org/node/800000000'
    assert out[0]['observed_tags']=={} and 'rating' not in out[0]
    raw['features'][0]['geometry']['coordinates']=[0,0]
    raw['features'][1]['properties']['countrycode']='JP'
    assert normalize_photon(raw,'paris')==[]
    with pytest.raises(DomainError):normalize_photon({'features':'broken'},'paris')
    with pytest.raises(DomainError):normalize_photon({'features':photon_payload()['features']*31},'paris')


def test_photon_does_not_invent_identity_for_unclassified_or_malformed_results():
    raw=photon_payload()
    raw['features'][0]['properties']['osm_key']='tourism'
    raw['features'][1]['geometry']['type']='Polygon'
    assert normalize_photon(raw,'paris')==[]
    raw=photon_payload();raw['features'][0]['properties']['extra']={'opening_hours':'Mo-Fr 09:00-18:00','rating':5,'author':'private'}
    out=normalize_photon(raw,'paris')
    assert out[0]['observed_tags']=={'opening_hours':'Mo-Fr 09:00-18:00'}


def test_known_unsent_connection_failures_do_not_consume_http_quota(public,monkeypatch):
    trip,base=prepare(public)
    monkeypatch.setattr('src.discovery.public_places.USER_DAILY',1)
    def failure(city):raise FetchRejected('CONNECT_FAILED',network_errno=111)
    public.app.state.discovery.public_provider.fetcher=failure
    run(public,trip,base,'unsent-first')
    public.clock[0]+=timedelta(minutes=3)
    public.app.state.discovery.public_provider.fetcher=lambda city:(normalize(payload(city),city),1000)
    out=run(public,trip,base,'sent-after-connect-failure')
    assert out['result']['public_discovery']['state']=='ready'
    with public.app.state.db.connect() as con:
        row=con.execute("SELECT actual_units_json FROM usage_reservations WHERE error_code='CONNECT_FAILED'").fetchone()
        assert json.loads(row[0])['calls']==0
