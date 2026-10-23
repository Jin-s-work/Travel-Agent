"""Optional Overpass tag lookup for Photon results (OSM_TAG_ENRICHMENT_ENABLED, off by default).

The public Photon instance returns no `extra` tags, so cuisine and opening hours
never reached public-map candidates. These tests use a fake transport: no network.
"""
import json
from types import SimpleNamespace
from urllib.parse import unquote

from src.discovery import public_places as P

CENTER = P.center('tokyo')


def feature(osm_id, name, *, value='restaurant', extra=None, kind='N', dlat=0.001):
    props = {'osm_id': osm_id, 'osm_type': kind, 'osm_key': 'amenity', 'osm_value': value, 'name': name,
             'countrycode': CENTER['country_code'], 'street': 'Synthetic street'}
    if extra is not None:
        props['extra'] = extra
    return {'type': 'Feature', 'properties': props,
            'geometry': {'type': 'Point', 'coordinates': [CENTER['longitude'], CENTER['latitude'] + dlat]}}


def page(payload, mime='application/json'):
    return SimpleNamespace(mime=mime, content=json.dumps(payload).encode())


class Transport:
    def __init__(self, photon, overpass):
        self.photon, self.overpass, self.urls = photon, overpass, []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        if url.startswith(P.ENDPOINT):
            return page(self.photon)
        if isinstance(self.overpass, Exception):
            raise self.overpass
        return self.overpass


PHOTON = {'type': 'FeatureCollection', 'features': [
    feature(101, 'Synthetic Ramen'), feature(102, 'Synthetic Cafe', value='cafe', dlat=0.002),
    feature(103, 'Synthetic Sushi', extra={'cuisine': 'sushi'}, dlat=0.003)]}
OVERPASS = page({'elements': [
    {'type': 'node', 'id': 101, 'tags': {'cuisine': 'ramen;japanese', 'opening_hours': 'Mo-Su 11:00-22:00',
                                         'phone': '+81 3 0000 0000', 'name': 'ignored', 'addr:street': 'ignored'}},
    {'type': 'node', 'id': 102, 'tags': {'wheelchair': 'yes'}},
    {'type': 'node', 'id': 999, 'tags': {'cuisine': 'not requested'}}]})


def test_overpass_lookup_asks_once_by_id_and_keeps_only_allowed_keys():
    transport = Transport(PHOTON, OVERPASS)
    found, size = P.overpass_tags(PHOTON['features'], fetch_page=transport)
    assert len(transport.urls) == 1 and transport.urls[0].startswith(P.OVERPASS_ENDPOINT)
    query = unquote(transport.urls[0])
    assert 'node(id:101,102);' in query and '103' not in query  # 103 already has Photon `extra`
    assert found == {('N', 101): {'cuisine': 'ramen;japanese', 'opening_hours': 'Mo-Su 11:00-22:00',
                                  'phone': '+81 3 0000 0000'},
                     ('N', 102): {'wheelchair': 'yes'}}
    assert size == len(OVERPASS.content)


def test_enriched_fetch_fills_cuisine_and_hours_and_keeps_the_photon_byte_count():
    transport = Transport(PHOTON, OVERPASS)
    places, received = P.fetch('tokyo', enrich=True, fetch_page=transport)
    by_name = {p['name']: p for p in places}
    assert by_name['Synthetic Ramen']['tags'] == ['ramen', 'japanese']
    assert by_name['Synthetic Ramen']['observed_tags']['opening_hours'] == 'Mo-Su 11:00-22:00'
    assert by_name['Synthetic Cafe']['observed_tags'] == {'wheelchair': 'yes'}
    assert by_name['Synthetic Sushi']['tags'] == ['sushi']  # Photon's own extra is kept
    assert places.enrichment == {'source': 'overpass', 'filled': 2, 'response_bytes': len(OVERPASS.content)}
    assert received == len(json.dumps(PHOTON).encode())  # size guard and ledger count Photon only


def test_enrichment_is_off_by_default_and_then_makes_no_second_call(monkeypatch):
    monkeypatch.delenv('OSM_TAG_ENRICHMENT_ENABLED', raising=False)
    assert P.osm_enrichment_enabled() is False
    transport = Transport(PHOTON, OVERPASS)
    places, _ = P.fetch('tokyo', fetch_page=transport)
    assert len(transport.urls) == 1 and places.enrichment is None
    assert {p['name']: p['tags'] for p in places}['Synthetic Ramen'] == []
    monkeypatch.setenv('OSM_TAG_ENRICHMENT_ENABLED', 'true')
    assert P.osm_enrichment_enabled() is True


def test_overpass_failure_leaves_the_photon_result_unchanged():
    plain, _ = P.fetch('tokyo', enrich=False, fetch_page=Transport(PHOTON, OVERPASS))
    for broken in (RuntimeError('synthetic 429'), page({'elements': []}, mime='text/html'),
                   SimpleNamespace(mime='application/json', content=b'not json')):
        places, received = P.fetch('tokyo', enrich=True, fetch_page=Transport(PHOTON, broken))
        assert [dict(p) for p in places] == [dict(p) for p in plain]
        assert places.enrichment['filled'] == 0 and places.enrichment['response_bytes'] == 0


def test_endpoint_can_point_at_a_self_hosted_overpass(monkeypatch):
    monkeypatch.setenv('OSM_OVERPASS_ENDPOINT', 'https://overpass.example.invalid/api/interpreter')
    transport = Transport(PHOTON, OVERPASS)
    P.overpass_tags(PHOTON['features'], fetch_page=transport)
    assert transport.urls[0].startswith('https://overpass.example.invalid/api/interpreter?data=')
    monkeypatch.delenv('OSM_OVERPASS_ENDPOINT')
    assert P.overpass_endpoint() == P.OVERPASS_ENDPOINT
