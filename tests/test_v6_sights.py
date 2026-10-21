"""Stage 4 bundle A: public-map sights (reference only) and provisional map hours.

Real SQL/auth/jobs with fake Photon responses; zero network calls.
"""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from src.destinations import CITIES
from src.discovery.candidate_pool import ExpandedPublicDiscovery
from src.discovery.opening_hours import NOTE_BASIS, fact_value, parse
from src.discovery.public_places import (POLICY, TTL, FetchedPlaces, PublicDiscovery, center, normalize,
                                        normalize_photon, query)
from src.itineraries.constraints import assess_visit, opening_windows
from src.recommendations.engine import Facts, _hours
from tests.test_foundation_api import _job, service  # noqa: F401  (fixture)
from tests.test_public_discovery import photon_payload, public  # noqa: F401  (fixture)

SIGHTS = ['tourism:attraction', 'tourism:gallery', 'tourism:museum', 'tourism:viewpoint']


def elements(city='paris', *, restaurant_hours='Mo-Su 12:00-22:00; PH off', museum_hours='Tu-Su 10:00-18:00'):
    c = center(city)
    def node(i, tags):
        return {'id': 710000000+i, 'type': 'node', 'lat': c['latitude']+.001*i, 'lon': c['longitude'], 'tags': tags}
    restaurant = {'name': 'Synthetic bistro', 'amenity': 'restaurant', 'cuisine': 'regional'}
    if restaurant_hours:
        restaurant['opening_hours'] = restaurant_hours
    museum = {'name': 'Synthetic museum', 'tourism': 'museum'}
    if museum_hours:
        museum['opening_hours'] = museum_hours
    return {'elements': [node(0, restaurant),
                         node(1, {'name': 'Synthetic cafe', 'amenity': 'cafe', 'opening_hours': 'Jan-Mar Mo-Fr 08:00-15:00'}),
                         node(2, museum),
                         node(3, {'name': 'Synthetic hotel', 'tourism': 'hotel'})]}


def test_default_query_is_unchanged_and_sights_share_the_same_request():
    default = parse_qs(query('tokyo'))
    assert default['osm_tag'] == ['amenity:restaurant', 'amenity:cafe'] and default['limit'] == ['60']
    assert parse_qs(query('tokyo', categories=['cafe', 'restaurant']))['osm_tag'] == ['amenity:cafe', 'amenity:restaurant']
    mixed = parse_qs(query('tokyo', categories=['restaurant', 'attraction']))
    assert mixed['osm_tag'] == ['amenity:restaurant'] + SIGHTS
    assert mixed['limit'] == ['60'] and mixed['radius'] == ['3.0']  # one bounded request, no extra quota
    assert parse_qs(query('tokyo', categories=['attraction']))['osm_tag'] == SIGHTS


def test_sights_are_accepted_only_when_attraction_is_requested():
    raw = elements()
    assert [p['category'] for p in normalize(raw, 'paris')] == ['restaurant', 'cafe']
    out = normalize(raw, 'paris', categories=['restaurant', 'cafe', 'attraction'])
    assert [p['category'] for p in out] == ['restaurant', 'cafe', 'attraction']  # hotel never accepted
    assert out[2]['tags'] == ['museum'] and out[2]['observed_tags'] == {'opening_hours': 'Tu-Su 10:00-18:00'}
    photon = photon_payload()
    photon['features'][0]['properties'].update(osm_key='tourism', osm_value='viewpoint',
                                               extra={'opening_hours': '24/7', 'rating': 5, 'author': 'private'})
    assert [p['category'] for p in normalize_photon(photon, 'paris')] == ['restaurant']
    sights = normalize_photon(photon, 'paris', categories=['attraction', 'restaurant'])
    assert sights[0]['category'] == 'attraction' and sights[0]['tags'] == ['viewpoint']
    assert sights[0]['observed_tags'] == {'opening_hours': '24/7'}
    photon['features'][0]['properties']['osm_value'] = 'hotel'
    assert [p['category'] for p in normalize_photon(photon, 'paris', categories=['attraction'])] == ['restaurant']


@pytest.fixture
def sights(public):
    state = SimpleNamespace(calls=[], payload=elements())
    def fake(city, *, categories=None, area=None):
        state.calls.append(categories)
        return normalize(state.payload, city, area=area, categories=categories), 1000
    public.app.state.discovery.public_provider = PublicDiscovery(public.app.state.db, fetcher=fake, clock=lambda: public.clock[0])
    public.state = state
    return public


def prepare(public, city='paris', categories=('restaurant', 'cafe', 'attraction'), types=('local_discovery', 'landmark')):
    trip = public.client.post('/api/v2/trips', json={'title': 'Synthetic sights', 'start_date': '2026-11-06', 'end_date': '2026-11-09',
        'stops': [{'city': city, 'sequence': 1, 'start_date': '2026-11-06', 'end_date': '2026-11-09', 'timezone': CITIES[city]['timezone']}]}).json()
    base = f"/api/v2/trips/{trip['id']}"
    conditions = public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions.update(categories=list(categories), recommendation_types=list(types), origin=None)
    result = public.client.patch(base+'/discovery-conditions', json={'expected_version': 0, 'conditions': conditions})
    assert result.status_code == 200, result.text
    return trip, base


def run(public, trip, base, key):
    receipt = public.client.post(base+'/recommendations', json={'trip_version': trip['version'], 'conditions_version': 1},
                                 headers={'Idempotency-Key': key})
    assert receipt.status_code == 202, receipt.text
    job = _job(public.client, receipt.json())
    assert job['state'] == 'succeeded', job
    return public.client.get(base+'/recommendations/'+receipt.json()['run_id']).json()['result']


def cards(result, kind):
    return [card for group in result['sections'][kind].values() if isinstance(group, list) for card in group]


def test_sights_are_reference_only_and_hours_are_provisional_map_facts(sights):
    trip, base = prepare(sights)
    result = run(sights, trip, base, 'sights-one')
    assert sights.state.calls == [['attraction', 'cafe', 'restaurant']]  # one call, same quota
    museum = 'osm_node_710000002'
    reference = {card['place_id']: card for card in cards(result, 'reference')}
    assert museum in reference and reference[museum]['category'] == 'attraction'
    # Displayed (needs confirmation), not merely present in an excluded group.
    shown = result['sections']['reference']['needs_confirmation'] + result['sections']['reference']['items']
    assert museum in {card['place_id'] for card in shown}
    assert museum not in {card['place_id'] for card in result['sections']['reference']['excluded']}
    for kind in ('local_discovery', 'landmark'):
        assert museum not in {card['place_id'] for card in cards(result, kind)}
    assert not result['sections']['landmark']['items'] and not result['sections']['local_discovery']['items']
    # Strict judgement still ignores provisional hours.
    hours_check = next(c for c in reference[museum]['visit_fit']['checks'] if c['field'] == 'opening_hours')
    assert hours_check['state'] == 'unknown' and hours_check['reason_code'] == 'HOURS_UNKNOWN'
    # 'Check before visiting' says the map lists hours; strict code stays.
    unknowns = reference[museum]['important_unknowns']
    assert unknowns[unknowns.index('HOURS_UNKNOWN') + 1] == 'PUBLIC_MAP_OPENING_HOURS'
    assert reference[museum]['eligibility'] == 'needs_confirmation' and reference[museum]['score'] is None
    cafe = next(card for card in cards(result, 'reference') if card['place_id'] == 'osm_node_710000001')
    assert 'HOURS_UNKNOWN' in cafe['important_unknowns'] and 'PUBLIC_MAP_OPENING_HOURS' not in cafe['important_unknowns']
    with sights.app.state.db.connect() as con:
        kinds = {r['place_id']: json.loads(r['recommendation_types_json']) for r in con.execute('SELECT place_id,recommendation_types_json FROM research_candidates')}
        assert kinds[museum] == [] and kinds['osm_node_710000000'] == ['local_discovery']
        rows = con.execute("SELECT * FROM place_facts WHERE field='opening_hours' ORDER BY id").fetchall()
        assert [r['id'] for r in rows] == ['osm_hours_node_710000000', 'osm_hours_node_710000002']  # cafe: unparsed, none
        for row in rows:
            assert row['status'] == 'provisional' and row['policy_version'] == POLICY
            checked, expires = (datetime.fromisoformat(row[k]) for k in ('checked_at', 'expires_at'))
            assert expires - checked == TTL == timedelta(days=7)
            value = json.loads(row['value_json'])
            assert value['timezone'] == 'Europe/Paris' and value['notes'][0] == NOTE_BASIS
        assert json.loads(rows[0]['value_json'])['notes'][-1] == '해석하지 않은 표기(공휴일 등): PH off'
        assert con.execute("SELECT count(*) FROM place_facts WHERE status='verified'").fetchone()[0] == 0
    detail = sights.client.get(base+'/places/osm_node_710000000/detail').json()
    hours = next(f for f in detail['facts'] if f['field'] == 'opening_hours')
    assert hours['status'] == 'provisional' and hours['usable'] and hours['value']['notes'][0] == NOTE_BASIS
    assert hours['value']['weekly']['monday'] == [['12:00', '22:00']]


def test_refresh_without_readable_hours_expires_the_old_map_hours(sights):
    trip, base = prepare(sights)
    run(sights, trip, base, 'sights-first')
    sights.clock[0] += TTL + timedelta(minutes=1)
    sights.state.payload = elements(restaurant_hours='by appointment', museum_hours=None)
    run(sights, trip, base, 'sights-refresh')
    assert len(sights.state.calls) == 2
    with sights.app.state.db.connect() as con:
        rows = con.execute("SELECT id,expires_at FROM place_facts WHERE field='opening_hours'").fetchall()
        assert len(rows) == 2  # kept for fact reports, but no longer current
        assert all(datetime.fromisoformat(r['expires_at']) <= sights.clock[0] for r in rows)


def test_food_only_conditions_keep_the_original_pack_and_query(sights):
    trip, base = prepare(sights, categories=('restaurant', 'cafe'), types=('local_discovery',))
    result = run(sights, trip, base, 'food-only')
    assert sights.state.calls == [['cafe', 'restaurant']]
    assert result['public_discovery']['pack_id'] == 'osm_pack_paris'
    assert 'attraction' not in {card.get('category') for card in cards(result, 'reference')}


def test_adding_sights_uses_a_separate_pack_and_one_more_call(sights):
    # Documented cost of mixing: the food-only pack is not reused for sights.
    trip, base = prepare(sights, categories=('restaurant', 'cafe'), types=('local_discovery',))
    food = run(sights, trip, base, 'food-first')
    sights.clock[0] += timedelta(seconds=16)
    conditions = sights.client.get(base+'/discovery-conditions').json()
    updated = {**conditions['conditions'], 'categories': ['restaurant', 'cafe', 'attraction']}
    assert sights.client.patch(base+'/discovery-conditions', json={'expected_version': conditions['version'], 'conditions': updated}).status_code == 200
    receipt = sights.client.post(base+'/recommendations', json={'trip_version': trip['version'], 'conditions_version': conditions['version'] + 1},
                                 headers={'Idempotency-Key': 'with-sights'})
    assert receipt.status_code == 202, receipt.text
    assert _job(sights.client, receipt.json())['state'] == 'succeeded'
    mixed = sights.client.get(base+'/recommendations/'+receipt.json()['run_id']).json()['result']
    assert sights.state.calls == [['cafe', 'restaurant'], ['attraction', 'cafe', 'restaurant']]
    assert food['public_discovery']['pack_id'] == 'osm_pack_paris'
    assert mixed['public_discovery']['pack_id'].startswith('osm_pack_paris_')


def test_map_hours_use_the_trip_timezone_not_the_geonames_zone(sights):
    assert CITIES['hanoi']['timezone'] == 'Asia/Ho_Chi_Minh' and center('hanoi')['source_timezone'] == 'Asia/Bangkok'
    sights.state.payload = elements('hanoi')
    trip, base = prepare(sights, city='hanoi')
    run(sights, trip, base, 'hanoi-hours')
    with sights.app.state.db.connect() as con:
        zones = {json.loads(r['value_json'])['timezone'] for r in con.execute("SELECT value_json FROM place_facts WHERE field='opening_hours'")}
    assert zones == {'Asia/Ho_Chi_Minh'}


def test_expanded_pool_counts_sights_within_the_same_call_cap(public):
    calls, clock = [], public.clock
    def fetch(city, *, categories=None, area=None):
        calls.append(categories)
        idx = len(calls)
        items = FetchedPlaces([dict(external_id=f'node/{90000000+idx*10+i}', name=f'Synthetic {idx}-{i}', native_name=None,
            address='Public address', address_status='observed', latitude=area['latitude']+.0001*i, longitude=area['longitude'],
            category='attraction' if i == 2 else 'restaurant', tags=['museum'] if i == 2 else [], neighborhood=None,
            source_url=f'https://www.openstreetmap.org/node/{90000000+idx*10+i}',
            observed_tags={'opening_hours': 'Mo-Su 10:00-18:00'}, center_distance_m=i) for i in range(3)])
        items.raw_count, items.deduped_count, items.rejected_count = 3, 0, 0
        return items, 1000
    start = clock[0]
    settings = SimpleNamespace(candidate_pool_limit=200, candidate_raw_record_limit=400, candidate_query_limit=7, candidate_deadline_seconds=120)
    public.app.state.discovery.public_provider = ExpandedPublicDiscovery(public.app.state.db, settings=settings, fetcher=fetch,
        clock=lambda: clock[0], sleeper=lambda s: clock.__setitem__(0, clock[0]+timedelta(seconds=s)),
        monotonic=lambda: (clock[0]-start).total_seconds())
    trip, base = prepare(public, categories=('restaurant', 'attraction'), types=('local_discovery',))
    result = run(public, trip, base, 'expanded-sights')
    assert len(calls) == 5 and all(c == ['attraction', 'restaurant'] for c in calls)  # per-user daily cap unchanged
    status = result['public_discovery']
    assert status['categories'] == {'restaurant': 10, 'attraction': 5}
    assert status['reason'] == 'PUBLIC_DISCOVERY_DAILY_LIMIT' and status['coverage']['private_origin_sent'] is False


NOW = datetime(2026, 10, 5, 3, tzinfo=timezone.utc)  # Monday 12:00 in Tokyo


def map_candidate(expression='Mo-Fr 11:00-14:00,17:00-22:00; PH off', status='provisional'):
    checked = (NOW - timedelta(hours=1)).isoformat()
    return {'place_id': 'osm_node_1', 'city': 'tokyo', 'category': 'restaurant', 'provider': 'openstreetmap',
            'pack_status': 'public_data', 'identity_status': 'needs_confirmation', 'source_kind': 'public_map',
            'sources': [{'id': 'osm_source_node_1', 'status': 'active', 'read_confirmed': True, 'display_permitted': True,
                         'source_group': 'OpenStreetMap', 'policy_version': POLICY, 'checked_at': checked}],
            'facts': [{'id': 'osm_hours_node_1', 'field': 'opening_hours', 'status': status, 'source_id': 'osm_source_node_1',
                       'value': fact_value(parse(expression), 'Asia/Tokyo'), 'checked_at': checked,
                       'expires_at': (NOW + TTL).isoformat(), 'policy_version': POLICY, 'usable': True, 'freshness': 'fresh'}]}


def test_strict_recommendation_check_never_uses_map_hours():
    visit = {'date': '2026-10-05', 'local_time': '12:30:00', 'timezone': 'Asia/Tokyo'}
    candidate = map_candidate()
    assert _hours(Facts(candidate, visit, NOW), visit) == ('unknown', 'HOURS_UNKNOWN', [])


def test_tentative_plan_uses_map_hours_labelled_as_public_map():
    result = opening_windows(map_candidate(), '2026-10-05', 'Asia/Tokyo', NOW)
    assert result['state'] == 'unknown' and result['opening_state'] == 'unknown'
    assert result['opening_basis'] == 'public_map' and 'PUBLIC_MAP_OPENING_HOURS' in result['reason_codes']
    spans = [(a.astimezone(timezone(timedelta(hours=9))).strftime('%H:%M'), b.astimezone(timezone(timedelta(hours=9))).strftime('%H:%M'))
             for a, b in result['intervals']]
    assert spans == [('11:00', '14:00'), ('17:00', '22:00')]
    closed_day = opening_windows(map_candidate('Sa-Su 10:00-16:00'), '2026-10-05', 'Asia/Tokyo', NOW)
    assert closed_day['intervals'] == [] and closed_day['state'] == 'unknown'
    assert 'PUBLIC_MAP_CLOSED_DAY' in closed_day['reason_codes']
    assert 'PUBLIC_MAP_CLOSED_DAY' not in result['reason_codes']


@pytest.mark.parametrize('expression,closed', [
    ('Sa-Su 10:00-16:00', True),             # Monday left out: closed under the OSM rule
    ('Tu-Su 10:00-16:00; Mo off', True),     # Monday explicitly off
    ('Su 20:00-02:00', False),               # Sunday night spills into Monday morning
    ('Mo-Su 10:00-16:00', False),
])
def test_map_closed_weekday_has_its_own_reason_and_is_never_a_violation(expression, closed):
    hours = opening_windows(map_candidate(expression), '2026-10-05', 'Asia/Tokyo', NOW)
    assert hours['state'] == 'unknown' and ('PUBLIC_MAP_CLOSED_DAY' in hours['reason_codes']) is closed
    item = {'item_id': 'visit-1', 'place_id': 'osm_node_1', 'item_type': 'place', 'start_timezone': 'Asia/Tokyo',
            'end_timezone': 'Asia/Tokyo', 'start_instant': '2026-10-05T03:00:00+00:00', 'end_instant': '2026-10-05T04:00:00+00:00',
            'local_start': '2026-10-05T12:00:00', 'local_end': '2026-10-05T13:00:00'}
    candidate = {**map_candidate(expression), 'identity_basis': 'provider_location', 'coordinate_permitted': True}
    checks = assess_visit(item, candidate, {'city': 'tokyo', 'timezone': 'Asia/Tokyo', 'conditions': {}}, NOW)
    assert all(check['state'] != 'violated' for check in checks)
    assert ('PUBLIC_MAP_CLOSED_DAY' in {check['code'] for check in checks}) is closed


def test_closed_day_reason_is_only_for_public_map_hours():
    candidate = map_candidate('Sa-Su 10:00-16:00')
    candidate['sources'][0]['source_group'] = 'Synthetic editor'
    result = opening_windows(candidate, '2026-10-05', 'Asia/Tokyo', NOW)
    assert 'PUBLIC_MAP_CLOSED_DAY' not in result['reason_codes']


def test_non_map_provisional_hours_are_not_relabelled():
    candidate = map_candidate()
    candidate['sources'][0]['source_group'] = 'Synthetic editor'
    result = opening_windows(candidate, '2026-10-05', 'Asia/Tokyo', NOW)
    assert 'opening_basis' not in result and 'PUBLIC_MAP_OPENING_HOURS' not in result['reason_codes']
