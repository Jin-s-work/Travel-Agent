"""Resolve photos linked to an exact OSM feature, without name/image search.

Only Commons files with explicit reusable licenses are returned. Chain-level
Wikidata, arbitrary image URLs, author profiles and full source responses are
never persisted. Network work runs in the existing leased dispatcher.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from uuid import uuid4

from src.foundation.repository import DomainError
from src.reliability.budget import Budget, CallContext
from .safe_fetch import fetch_public, FetchRejected
from .photos import LICENSE_URLS, _photo, unavailable
from .public_places import _distance

POLICY = 'commons-branch-photo-v1'
PROVIDER = 'public_place_photos'
MAX_PLACES = 6
MAX_BYTES = 384_000
GLOBAL_DAILY = 120
USER_DAILY = 60
TTL = timedelta(days=7)
OSM_ID = re.compile(r'^(node|way|relation)/([1-9][0-9]{0,15})$')
FILE = re.compile(r'^File:([^|<>\x00-\x1f]{1,240}\.(?:jpe?g|png|webp))$', re.I)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity_hash(place):
    return sha256(dump({k: place.get(k) for k in ('id', 'provider', 'external_place_id', 'city', 'name', 'address')}).encode()).hexdigest()


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value, limit=500):
    if not isinstance(value, str) or len(value) > 8000:
        return ''
    parser = Plain(); parser.feed(value)
    return ' '.join(''.join(parser.parts).split())[:limit]


def media_url(value):
    # Imageinfo now appends attribution tracking parameters. Strip only those
    # public analytics fields, never accept an arbitrary proxy/signed URL.
    if not isinstance(value, str):return None
    try:
        url=urlsplit(value)
        if url.query and not set(k for k,_ in parse_qsl(url.query,keep_blank_values=True)) <= {'utm_source','utm_campaign','utm_content'}:
            return None
        return urlunsplit((url.scheme,url.netloc,url.path,'',url.fragment))
    except ValueError:return None


def metadata(payload, title, name, clock):
    """No inference of food/interior, capture date, or photographer identity."""
    pages = payload.get('query', {}).get('pages', {})
    if not isinstance(pages, dict):
        return None
    for page in pages.values():
        if not isinstance(page, dict) or page.get('title', '').replace('_', ' ') != title.replace('_', ' '):
            continue
        info = (page.get('imageinfo') or [{}])[0]
        ext = info.get('extmetadata') or {}
        value = lambda key: (ext.get(key) or {}).get('value', '')
        license_name = plain(value('LicenseShortName'))
        license_url = str(value('LicenseUrl')).replace('http://', 'https://').rstrip('/') + '/'
        if license_name not in LICENSE_URLS or LICENSE_URLS[license_name] != license_url:
            return None
        photo = dict(id='commons_' + sha256(title.encode()).hexdigest()[:24],
            url=media_url(info.get('thumburl') or info.get('url')), source_url=info.get('descriptionurl'),
            author=plain(value('Artist')), license=license_name, license_url=license_url,
            alt=name + ' · 지점에 연결된 Wikimedia Commons 사진', kind='other', taken_at=None,
            width=info.get('thumbwidth') or info.get('width'), height=info.get('thumbheight') or info.get('height'),
            checked_at=clock.isoformat(), expires_at=(clock + TTL).isoformat(), enabled=True,
            identity_evidence='Exact OSM feature media link; Wikidata images require nearby entity coordinates.')
        return _photo(photo, clock)[0]
    return None


def linked_titles(place, read):
    ident = place['external_place_id']; match = OSM_ID.fullmatch(ident or '')
    if not match:
        return []
    data = read('https://api.openstreetmap.org/api/0.6/' + ident + '.json')
    elements = data.get('elements', [])
    feature = next((e for e in elements if e.get('type') == match[1] and e.get('id') == int(match[2])), None)
    if not feature or feature.get('visible') is False:
        return []
    tags = feature.get('tags') or {}
    if tags.get('amenity') not in ('restaurant', 'cafe'):
        return []
    titles = []
    # File links are branch-specific; category and arbitrary URL tags are not.
    for key in ('wikimedia_commons', 'image'):
        title = tags.get(key, '')
        if isinstance(title, str) and FILE.fullmatch(title) and title not in titles:
            titles.append(title)
    qid = tags.get('wikidata', '')
    if re.fullmatch(r'Q[1-9][0-9]{0,12}', qid) and qid != tags.get('brand:wikidata') and len(titles) < 3:
        entity = read('https://www.wikidata.org/wiki/Special:EntityData/' + qid + '.json').get('entities', {}).get(qid, {})
        claims = entity.get('claims', {})
        coordinates = [c.get('mainsnak', {}).get('datavalue', {}).get('value', {})
                       for c in claims.get('P625', []) if c.get('rank') != 'deprecated']
        near = False
        for point in coordinates:
            if point.get('globe') != 'http://www.wikidata.org/entity/Q2':
                continue
            values = [point.get('latitude'), point.get('longitude'), place.get('latitude'), place.get('longitude')]
            if all(type(v) in (int, float) for v in values) and _distance(*values) <= 100:
                near = True
        if near:
            for claim in claims.get('P18', []):
                file = claim.get('mainsnak', {}).get('datavalue', {}).get('value')
                if claim.get('rank') != 'deprecated' and isinstance(file, str) and FILE.fullmatch('File:' + file):
                    if 'File:' + file not in titles:
                        titles.append('File:' + file)
    return titles[:3]


class PlacePhotos:
    def __init__(self, db, repo, jobs, discovery, *, enabled=True, fetcher=fetch_public):
        self.db, self.repo, self.jobs, self.discovery = db, repo, jobs, discovery
        self.enabled, self.fetcher = enabled, fetcher

    def _place(self, con, actor, trip_id, ident):
        self.jobs._scope(con, actor.id, actor.session_id, 'personal_trip', trip_id)
        self.repo._trip(con, actor.id, trip_id)
        linked = con.execute('SELECT 1 FROM trip_places WHERE trip_id=? AND place_id=?', (trip_id, ident)).fetchone()
        if not linked:
            # Pre-schema13 recommendations did not have trip_places. Recover
            # only an exact identity from this owner's saved candidate snapshot.
            snapshots = con.execute('SELECT candidates_json FROM recommendation_runs WHERE owner_id=? AND trip_id=? AND candidates_json LIKE ? ORDER BY created_at DESC LIMIT 5',
                                    (actor.id, trip_id, '%' + ident + '%')).fetchall()
            for snapshot in snapshots:
                try:
                    candidates = json.loads(snapshot['candidates_json'])
                    linked = isinstance(candidates, list) and any(isinstance(p, dict) and p.get('place_id') == ident for p in candidates)
                except (ValueError, TypeError):
                    linked = False
                if linked:break
        row = self.discovery._visible_place(con, ident) if linked else None
        if not row or row['provider'] != 'openstreetmap':
            raise DomainError('NOT_FOUND', '장소를 찾을 수 없습니다.', 404)
        value = dict(row)
        coordinates = con.execute('SELECT latitude,longitude FROM research_candidates WHERE place_id=? ORDER BY updated_at DESC LIMIT 1', (ident,)).fetchone()
        if coordinates:
            value.update(dict(coordinates))
        return value

    def submit(self, actor, trip_id, ids, key):
        ids = sorted(set(ids))
        if not 1 <= len(ids) <= MAX_PLACES:
            raise DomainError('INVALID_PHOTO_REQUEST', '한 번에 최대 6곳의 사진을 확인할 수 있어요.', 422)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            places = [self._place(con, actor, trip_id, ident) for ident in ids]
            if not self.enabled:
                return {'state': 'unavailable', 'reason': 'PHOTOS_DISABLED'}
            pending = []
            now = datetime.now(timezone.utc).isoformat()
            for place in places:
                cached = con.execute('SELECT identity_hash,expires_at FROM place_photo_cache WHERE place_id=?', (place['id'],)).fetchone()
                if not cached or cached['identity_hash'] != identity_hash(place) or cached['expires_at'] <= now:
                    pending.append(place['id'])
            if not pending and not key:
                return {'state': 'succeeded', 'cached': True}
            # Auto requests coalesce for 15 minutes, including partial failures.
            # An explicit key still has the normal same-key/different-payload 409 contract.
            fingerprint = sha256(dump({'ids': ids, 'identities': [identity_hash(p) for p in places]}).encode()).hexdigest()
            auto_key = 'photos:' + fingerprint + ':' + str(int(datetime.now(timezone.utc).timestamp()) // 900)
            trip = self.repo._trip(con, actor.id, trip_id)
            job = self.jobs.enqueue(actor.id, actor.session_id, 'personal_trip', trip_id, 'place_photos',
                {'place_ids': ids}, trip['version'], key or auto_key,
                request_fingerprint=fingerprint, deadline_seconds=180, max_attempts=1, con=con)
            return {'job_id': job['id'], 'state': job['state']}

    def _read(self, url, actor, trip_id, ctx):
        from src.operations.controls import external_guard
        external_guard(self.db, PROVIDER)
        ctx.guard(); now = datetime.now(timezone.utc); date = now.isoformat()
        context = CallContext(actor.id, trip_id, trip_id, job_id=ctx.job['id'])
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); ctx.guard(con=con); Budget._scope(con, context)
            counts = con.execute('SELECT owner_id FROM usage_reservations WHERE provider=? AND period_day=?', (PROVIDER, date[:10])).fetchall()
            if len(counts) >= GLOBAL_DAILY or sum(r['owner_id'] == actor.id for r in counts) >= USER_DAILY:
                raise DomainError('PHOTO_DAILY_LIMIT', '오늘 사진 확인 한도에 도달했습니다.', 429)
            recent = con.execute("SELECT updated_at FROM usage_reservations WHERE provider=? AND error_code IN ('HTTP_429','HTTP_403') ORDER BY updated_at DESC LIMIT 1", (PROVIDER,)).fetchone()
            if recent and datetime.fromisoformat(recent['updated_at']) + timedelta(minutes=15) > now:
                raise DomainError('PHOTO_PROVIDER_COOLDOWN', '사진 공급자 연결을 잠시 쉬고 있습니다.', 429)
            call_id = 'call_' + uuid4().hex
            con.execute('INSERT INTO usage_reservations(call_id,owner_id,trip_id,job_id,scope_kind,scope_id,provider,sku,operation,attempt,call_key,request_hash,state,currency,estimated_units_json,estimated_cost_micros,price_version,price_confirmed_at,price_rates_json,period_day,period_month,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (call_id, actor.id, trip_id, ctx.job['id'], 'personal_trip', trip_id, PROVIDER, 'commons_metadata', 'place_photos', 1, call_id,
                 sha256(url.encode()).hexdigest(), 'sent', 'USD', dump({'calls': 1}), 0, POLICY, date, dump({'calls': 0}), date[:10], date[:7], date, date))
            Budget._ledger(con, call_id, 'reserved', 0, {'calls': 1}, date, reason='EXPLICIT_FREE_PUBLIC_DATA')
        code = None; received = None
        try:
            response = self.fetcher(url, max_bytes=MAX_BYTES, timeout_seconds=4, max_redirects=0)
            received = len(response.content)
            if received > MAX_BYTES:
                raise FetchRejected('RESPONSE_TOO_LARGE')
            if response.mime != 'application/json':
                raise FetchRejected('UNSUPPORTED_MIME')
            result = json.loads(response.content)
            if not isinstance(result, dict) or result.get('error'):
                raise FetchRejected('INVALID_JSON')
            return result
        except Exception as exc:
            code = 'HTTP_' + str(exc.http_status) if getattr(exc, 'http_status', None) else 'PHOTO_FETCH_FAILED'
            raise
        finally:
            with self.db.connect() as con:
                con.execute('UPDATE usage_reservations SET state=?,actual_units_json=?,actual_cost_micros=0,error_code=?,updated_at=? WHERE call_id=?',
                    ('settled', dump({'calls': 1, 'response_bytes': received}), code, datetime.now(timezone.utc).isoformat(), call_id))
                Budget._ledger(con, call_id, 'settled', 0, {'calls': 1}, date)

    def execute(self, job, ctx):
        actor = SimpleNamespace(id=job['actor_id'], session_id=job['session_id'])
        ids = job['payload']['place_ids']; done = 0; results = []
        for ident in ids:
            ctx.guard()
            with self.db.connect() as con:
                place = self._place(con, actor, job['trip_id'], ident)
                cached = con.execute('SELECT identity_hash,expires_at,payload_json FROM place_photo_cache WHERE place_id=?', (ident,)).fetchone()
            now = datetime.now(timezone.utc)
            if cached and cached['identity_hash'] == identity_hash(place) and cached['expires_at'] > now.isoformat():
                results.append(json.loads(cached['payload_json'])); done += 1; continue
            ctx.progress('place_photos', done=done, total=len(ids))
            read = lambda url: self._read(url, actor, job['trip_id'], ctx)
            result = unavailable('NO_LINKED_PHOTO'); expiry = now + TTL
            try:
                titles = linked_titles(place, read)
                if titles:
                    data = read('https://commons.wikimedia.org/w/api.php?' + urlencode({'action': 'query', 'format': 'json', 'prop': 'imageinfo',
                        'titles': '|'.join(titles), 'iiprop': 'url|size|extmetadata', 'iiurlwidth': 960}))
                    photos = [p for title in titles if (p := metadata(data, title, place['name'], now))]
                    if photos:
                        result = {'photos': photos, 'photo_status': {'state': 'available', 'available_count': len(photos), 'reason_codes': [], 'provider': 'Wikimedia Commons'}}
            except DomainError as exc:
                if exc.code in {'NOT_FOUND','LEASE_LOST','JOB_CANCELLED','JOB_DEADLINE','TRIP_DELETED','ACCESS_REVOKED','AUTH_REQUIRED'}:
                    raise
                result = unavailable(exc.code); expiry = now + timedelta(minutes=15)
            except Exception:
                result = unavailable('PHOTO_PROVIDER_UNAVAILABLE'); expiry = now + timedelta(minutes=15)
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE'); ctx.guard(con=con)
                current = self._place(con, actor, job['trip_id'], ident)
                if identity_hash(current) != identity_hash(place):
                    raise DomainError('PLACE_CHANGED', '장소 정보가 바뀌었습니다.', 409)
                con.execute('INSERT INTO place_photo_cache VALUES(?,?,?,?,?) ON CONFLICT(place_id) DO UPDATE SET identity_hash=excluded.identity_hash,payload_json=excluded.payload_json,checked_at=excluded.checked_at,expires_at=excluded.expires_at',
                    (ident, identity_hash(place), dump(result), now.isoformat(), expiry.isoformat()))
            results.append(result); done += 1
            ctx.checkpoint({'photos_checked': done}, stage='place_photos', done=done, total=len(ids))
        available = sum(bool(value['photos']) for value in results)
        missing = sum(not value['photos'] and value['photo_status']['reason_codes'] == ['NO_LINKED_PHOTO'] for value in results)
        unresolved = done - available - missing
        return {'state': 'partial' if unresolved else 'succeeded', 'result': {
            'photos_checked': done, 'places_with_photos': available,
            'places_without_photos': missing, 'places_unresolved': unresolved}}
