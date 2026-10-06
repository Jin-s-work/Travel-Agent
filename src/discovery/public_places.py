"""Small, explicitly free OSM discovery for the private beta.

Only fixed city-center queries leave the server. This is not a general crawler,
review provider or guarantee of current business information. Public data stays
provisional. Existing SQL holds the cache and zero-cost, rate-capped call ledger;
paid-provider budget/stop settings are never changed.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from src.foundation.repository import DomainError
from src.reliability.budget import Budget, CallContext
from .safe_fetch import fetch_public, FetchRejected, validate_public_url

CENTERS = json.loads(Path(__file__).parents[1].joinpath('destinations/centers.json').read_text())
POLICY = 'osm-public-beta-v1'
PROVIDER = 'openstreetmap'
ENDPOINT = 'https://overpass-api.de/api/interpreter'
MAX_BYTES = 350_000
MAX_CANDIDATES = 60
DISPLAY_CANDIDATES = 12
TTL = timedelta(days=7)
GLOBAL_DAILY = 20
USER_DAILY = 5
COOLDOWN = timedelta(seconds=15)
ERROR_TTL = timedelta(minutes=15)
ATTRIBUTION = {'text': '© OpenStreetMap contributors', 'url': 'https://www.openstreetmap.org/copyright',
               'license': 'ODbL 1.0', 'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/'}
FATAL = {'NOT_FOUND','LEASE_LOST','JOB_CANCELLED','JOB_DEADLINE','VERSION_CONFLICT','TRIP_DELETED','ACCESS_REVOKED'}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def center(city):
    return CENTERS['cities'].get(city)


def query(city):
    c = center(city)
    if not c:
        raise DomainError('CITY_UNSUPPORTED', '등록된 도시를 선택해 주세요.', 422)
    # No user text, private location, date or address is interpolated.
    return (f'[out:json][timeout:10][maxsize:16777216];'
            f'nwr(around:{c["radius_m"]},{c["latitude"]},{c["longitude"]})'
            '[amenity~"^(restaurant|cafe)$"][name];out center tags 60;')


def _text(value, limit=300):
    if not isinstance(value, str):
        return ''
    return ' '.join(value.replace('\x00', '').split())[:limit]


def _distance(a, b, c, d):
    p, q = math.radians(a), math.radians(c)
    hav = math.sin((q-p)/2)**2 + math.cos(p)*math.cos(q)*math.sin(math.radians(d-b)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(hav)))


def normalize(payload, city):
    c = center(city)
    if not isinstance(payload, dict) or payload.get('remark') or not isinstance(payload.get('elements'), list):
        raise DomainError('PUBLIC_DISCOVERY_INCOMPLETE', '공개지도 응답이 불완전해요. 잠시 후 다시 찾아 주세요.', 503)
    if len(payload['elements']) > MAX_CANDIDATES:
        raise DomainError('PUBLIC_DISCOVERY_OVERSIZED', '공개지도 응답이 허용 크기를 넘었어요.', 503)
    result, identities, branches = [], set(), []
    for raw in payload['elements']:
        if not isinstance(raw, dict):
            continue
        tags = raw.get('tags') or {}
        if not isinstance(tags, dict) or tags.get('amenity') not in {'restaurant', 'cafe'}:
            continue
        name = _text(tags.get('name'))
        kind, ident = raw.get('type'), raw.get('id')
        if not name or kind not in {'node', 'way', 'relation'} or type(ident) is not int or ident <= 0:
            continue
        external = f'{kind}/{ident}'
        coords = raw if kind == 'node' else raw.get('center') or {}
        lat, lon = coords.get('lat'), coords.get('lon')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (lat, lon)):
            continue
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or _distance(c['latitude'], c['longitude'], lat, lon) > c['radius_m']:
            continue
        if external in identities:
            continue
        # OSM sometimes maps one branch as both node and building/area.
        if any(name.casefold() == prior[0] and _distance(lat, lon, prior[1], prior[2]) < 25 for prior in branches):
            continue
        identities.add(external); branches.append((name.casefold(), lat, lon))
        source_url = 'https://www.openstreetmap.org/' + external
        address = _text(tags.get('addr:full')) or ', '.join(filter(None, (
            ' '.join(filter(None, (_text(tags.get('addr:housenumber'), 30), _text(tags.get('addr:street'))))),
            _text(tags.get('addr:postcode'), 30), _text(tags.get('addr:city')))))
        website = _text(tags.get('website') or tags.get('contact:website'), 2048)
        try:
            website = validate_public_url(website) if website else None
        except FetchRejected:
            website = None
        observed = {key: _text(tags[key], 500) for key in ('cuisine','opening_hours','wheelchair','diet:vegetarian','diet:vegan','phone','contact:phone') if _text(tags.get(key))}
        if website:
            observed['website'] = website
        result.append({'external_id': external, 'name': name, 'native_name': _text(tags.get('name:en')) or None,
                       'address': address or '상세 주소 미등록 · 공개지도 위치 확인', 'address_status': 'observed' if address else 'missing',
                       'latitude': float(lat), 'longitude': float(lon), 'category': tags['amenity'],
                       'tags': [x.strip()[:80] for x in _text(tags.get('cuisine')).split(';') if x.strip()][:10],
                       'neighborhood': _text(tags.get('addr:suburb')) or None, 'source_url': source_url,
                       'observed_tags': observed, 'center_distance_m': round(_distance(c['latitude'], c['longitude'], lat, lon))})
    return sorted(result, key=lambda p: (p['center_distance_m'], p['external_id']))


def fetch(city):
    page = fetch_public(ENDPOINT + '?' + urlencode({'data': query(city)}), max_bytes=MAX_BYTES,
                        timeout_seconds=15, max_redirects=0)
    if page.mime != 'application/json':
        raise FetchRejected('UNSUPPORTED_MIME')
    try:
        payload = json.loads(page.content)
    except (ValueError, UnicodeError):
        raise DomainError('PUBLIC_DISCOVERY_INVALID', '공개지도 응답을 읽을 수 없어요. 잠시 후 다시 찾아 주세요.', 503) from None
    return normalize(payload, city), len(page.content)


def is_public_candidate(candidate):
    return (candidate.get('provider') == PROVIDER and candidate.get('pack_status') == 'public_data'
            and candidate.get('identity_status') == 'needs_confirmation'
            and any(s.get('source_group') == 'OpenStreetMap' and s.get('policy_version') == POLICY
                    and s.get('status') == 'active' and s.get('read_confirmed') and s.get('display_permitted')
                    for s in candidate.get('sources', [])))


class PublicDiscovery:
    def __init__(self, db, *, fetcher=fetch, clock=None):
        self.db, self.fetcher = db, fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _status(self, city, state, **changes):
        c = center(city)
        return {'state': state, 'provider': PROVIDER, 'calls': 0, 'cost': {'currency': 'USD', 'micros': 0},
                'cache_hit': False, 'scope': 'city_center', 'radius_m': c['radius_m'] if c else None,
                'center': c, 'center_attribution':{'text':'GeoNames','url':'https://www.geonames.org/','license':'CC BY 4.0','license_url':CENTERS['license_url']}, 'attribution': ATTRIBUTION, 'policy_version': POLICY,
                'notice': '도심 3km 주변 공개지도 장소예요. 지점·영업·가격·리뷰는 방문 전에 확인해 주세요.', **changes}

    def ensure(self, actor, trip_id, city, ctx):
        """Called only by an authenticated recommendation job, never by GET."""
        if not center(city):
            return self._status(city, 'unavailable', reason='CITY_UNSUPPORTED')
        ctx.guard()
        now = self.clock().astimezone(timezone.utc)
        request_hash = hashlib.sha256((POLICY + ':' + query(city)).encode()).hexdigest()
        pack_id = 'osm_pack_' + city
        context = CallContext(actor.id, trip_id, trip_id, job_id=ctx.job['id'])
        try:
            from src.operations.controls import external_guard
            # Pauses block fresh external work; a valid cached public snapshot is reusable.
            with self.db.connect() as con:
                Budget._scope(con, context)
                cached = con.execute('SELECT * FROM candidate_packs WHERE id=? AND status=?', (pack_id, 'public_data')).fetchone()
                if cached and stamp(cached['updated_at']) + TTL > now:
                    count = con.execute('SELECT count(*) FROM research_candidates WHERE pack_id=? AND status=?', (pack_id, 'public_data')).fetchone()[0]
                    return self._status(city, 'ready' if count else 'empty', cache_hit=True, candidate_count=count, display_limit=DISPLAY_CANDIDATES,
                                        fetched_at=cached['updated_at'], expires_at=(stamp(cached['updated_at']) + TTL).isoformat())
            external_guard(self.db, PROVIDER)
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE'); ctx.guard(con=con); Budget._scope(con, context)
                recent = con.execute('SELECT * FROM usage_reservations WHERE provider=? AND request_hash=? ORDER BY created_at DESC LIMIT 1', (PROVIDER, request_hash)).fetchone()
                if recent and (recent['state'] != 'settled' and stamp(recent['created_at']) + ERROR_TTL > now or recent['error_code'] and stamp(recent['updated_at']) + ERROR_TTL > now):
                    return self._status(city, 'unavailable', reason='PUBLIC_DISCOVERY_COOLDOWN', retry_after_seconds=900)
                day = now.date().isoformat()
                totals = con.execute('SELECT owner_id,created_at FROM usage_reservations WHERE provider=? AND period_day=?', (PROVIDER, day)).fetchall()
                if len(totals) >= GLOBAL_DAILY or sum(r['owner_id'] == actor.id for r in totals) >= USER_DAILY:
                    return self._status(city, 'unavailable', reason='PUBLIC_DISCOVERY_DAILY_LIMIT', retry_after_seconds=86400)
                latest = con.execute('SELECT created_at FROM usage_reservations WHERE provider=? ORDER BY created_at DESC LIMIT 1', (PROVIDER,)).fetchone()
                if latest and stamp(latest['created_at']) + COOLDOWN > now:
                    return self._status(city, 'unavailable', reason='PUBLIC_DISCOVERY_COOLDOWN', retry_after_seconds=15)
                call_id = 'call_' + uuid4().hex
                date = now.isoformat()
                units = encode({'calls': 1, 'response_bytes': MAX_BYTES})
                con.execute('INSERT INTO usage_reservations(call_id,owner_id,trip_id,job_id,scope_kind,scope_id,provider,sku,operation,attempt,call_key,request_hash,state,currency,estimated_units_json,estimated_cost_micros,price_version,price_confirmed_at,price_rates_json,period_day,period_month,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (call_id, actor.id, trip_id, context.job_id, 'personal_trip', trip_id, PROVIDER, 'public_city_restaurants', 'public_discovery', 1, 'job:' + context.job_id + ':' + city, request_hash, 'sent', 'USD', units, 0, POLICY, date, encode({'calls':0,'response_bytes':0}), day, now.strftime('%Y-%m'), date, date))
                Budget._ledger(con, call_id, 'reserved', 0, {'calls':1,'response_bytes':MAX_BYTES}, date, reason='EXPLICIT_FREE_PUBLIC_DATA')
            try:
                ctx.guard(); items, received = self.fetcher(city); ctx.guard()
                if type(received) is not int or not 0 <= received <= MAX_BYTES or len(items) > MAX_CANDIDATES:
                    raise DomainError('PUBLIC_DISCOVERY_OVERSIZED', '공개지도 응답이 허용 크기를 넘었어요.', 503)
                with self.db.connect() as con:
                    con.execute('BEGIN IMMEDIATE'); ctx.guard(con=con); Budget._scope(con, context)
                    self._store(con, actor, city, items, now)
                    self._settle(con, call_id, received, now)
                return self._status(city, 'ready' if items else 'empty', calls=1, candidate_count=len(items), display_limit=DISPLAY_CANDIDATES, fetched_at=date, expires_at=(now+TTL).isoformat())
            except Exception as exc:
                code = exc.code if isinstance(exc, (DomainError, FetchRejected)) else 'PUBLIC_DISCOVERY_UNAVAILABLE'
                with self.db.connect() as con:
                    con.execute('BEGIN IMMEDIATE'); self._settle(con, call_id, MAX_BYTES, self.clock(), code)
                if isinstance(exc, DomainError) and exc.code in FATAL:
                    raise
                return self._status(city, 'unavailable', calls=1, reason=code, retry_after_seconds=900)
        except DomainError as exc:
            if exc.code in FATAL:
                raise
            return self._status(city, 'unavailable', reason=exc.code, retry_after_seconds=900)

    def _settle(self, con, call_id, received, now, code=None):
        units = {'calls': 1, 'response_bytes': received}
        con.execute("UPDATE usage_reservations SET state='settled',actual_units_json=?,actual_cost_micros=0,error_code=?,updated_at=? WHERE call_id=?", (encode(units), code, now.isoformat(), call_id))
        Budget._ledger(con, call_id, 'settled', 0, units, now.isoformat(), reason=code or 'EXPLICIT_FREE_PUBLIC_DATA')

    def _store(self, con, actor, city, items, now):
        pack_id, date = 'osm_pack_' + city, now.isoformat()
        if con.execute("SELECT 1 FROM discovery_tombstones WHERE kind='pack' AND target_id=?", (pack_id,)).fetchone():
            raise DomainError('PUBLIC_DISCOVERY_WITHDRAWN', '이 도시의 공개지도 자료 제공이 중지되었어요.', 503)
        con.execute('INSERT INTO candidate_packs VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at',
                    (pack_id, POLICY, city, 0, 'public_data', actor.id, date, date))
        # Retain removed identities for existing bookmarks, but remove them from current discovery.
        con.execute("UPDATE research_candidates SET status='expired' WHERE pack_id=?", (pack_id,))
        old_places={r['id']:r for r in con.execute("SELECT id,city,identity_status,deleted_at FROM place_identities WHERE provider='openstreetmap'")}
        withdrawn={r[0] for r in con.execute("SELECT target_id FROM discovery_tombstones WHERE kind='source'")}
        places,candidates,sources,facts=[],[],[],[]
        for i, item in enumerate(items):
            place_id = 'osm_' + item['external_id'].replace('/', '_')
            source_id = 'osm_source_' + item['external_id'].replace('/', '_')
            candidate_id = pack_id + '_' + item['external_id'].replace('/', '_')
            old=old_places.get(place_id)
            if source_id in withdrawn or old and (old['deleted_at'] or old['city']!=city or old['identity_status']!='needs_confirmation'):
                continue
            places.append((place_id, PROVIDER, item['external_id'], city, item['name'], item['address'], item['source_url'], 'needs_confirmation', 'Public OSM record; not independently verified', date, date))
            candidates.append((candidate_id, pack_id, place_id, 'public_data', item['category'], encode(['local_discovery']), encode(item['tags']), item['native_name'], item['source_url'], None, item['neighborhood'], item['latitude'], item['longitude'], date, date, i))
            sources.append((source_id, place_id, 'osm_public_record', item['source_url'], 'provider', 'OpenStreetMap', date, None, 1, 1, 'active', '© OpenStreetMap contributors · ODbL 1.0. Public map record observed; branch, hours and business details are unverified.', POLICY, actor.id, 1, date, date))
            facts.append(('osm_fact_' + item['external_id'].replace('/', '_'), place_id, 'public_map_tags', encode({'tags':item['observed_tags'],'address_status':item['address_status']}), 'provisional', source_id, date, None, None, None, (now+TTL).isoformat(), POLICY, actor.id, date))
        # executemany uses psycopg's pipeline; no remote SQL round trip per tag/place.
        con.executemany('INSERT INTO place_identities(id,provider,external_place_id,city,name,address,source_url,identity_status,identity_evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,address=excluded.address,updated_at=excluded.updated_at', places)
        con.executemany('INSERT INTO research_candidates(id,pack_id,place_id,status,category,recommendation_types_json,tags_json,native_name,canonical_url,chain_id,neighborhood,latitude,longitude,created_at,updated_at,sort_order) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,category=excluded.category,tags_json=excluded.tags_json,native_name=excluded.native_name,neighborhood=excluded.neighborhood,latitude=excluded.latitude,longitude=excluded.longitude,updated_at=excluded.updated_at,sort_order=excluded.sort_order', candidates)
        con.executemany('INSERT INTO evidence_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at,updated_at=excluded.updated_at,evidence_note=excluded.evidence_note', sources)
        con.executemany('INSERT INTO place_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET value_json=excluded.value_json,checked_at=excluded.checked_at,expires_at=excluded.expires_at,created_at=excluded.created_at', facts)
