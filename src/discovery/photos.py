"""Read-only, independently reviewed restaurant image metadata.

A photo license does not follow from permission to show a place's factual data.
A curated manifest or a verified, licensed public cache supplies photos. No network fetch, user
URL proxy, provider call, image byte persistence, or ranking input here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote, urlsplit

MANIFEST_PATH = Path(__file__).with_name('restaurant_photos.json')
LICENSE_URLS = {
    'CC BY 2.0': 'https://creativecommons.org/licenses/by/2.0/',
    'CC BY 3.0': 'https://creativecommons.org/licenses/by/3.0/',
    'CC BY 4.0': 'https://creativecommons.org/licenses/by/4.0/',
    'CC BY-SA 2.0': 'https://creativecommons.org/licenses/by-sa/2.0/',
    'CC BY-SA 2.5': 'https://creativecommons.org/licenses/by-sa/2.5/',
    'CC BY-SA 3.0': 'https://creativecommons.org/licenses/by-sa/3.0/',
    'CC BY-SA 4.0': 'https://creativecommons.org/licenses/by-sa/4.0/',
    'CC0': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'Public domain': 'https://creativecommons.org/publicdomain/mark/1.0/',
}
PHOTO_KIND_ORDER = {'food': 0, 'interior': 1, 'exterior': 2, 'other': 3}
MAX_PHOTOS = 3
MAX_MANIFEST_BYTES = 1024 * 1024
IMAGE_PATH = re.compile(r'^/wikipedia/commons/(?:thumb/)?[a-f0-9]/[a-f0-9]{2}/[^/]+(?:/[0-9]+px-[^/]+)?\.(?:jpe?g|png|webp)$', re.I)


def _norm(value):
    return unicodedata.normalize('NFKC', str(value)).strip().casefold()


def _stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def _url(value, host, prefix):
    if not isinstance(value, str) or not 1 <= len(value) <= 2000 or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    parts = urlsplit(value)
    return (parts.scheme == 'https' and parts.netloc == host and not parts.query and not parts.fragment
            and parts.path.startswith(prefix) and not any(c in unquote(parts.path) for c in '\r\n\x00'))


def _plain(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and not any(c in value for c in '<>\x00')


def _taken(value):
    if value is None:
        return True
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', value):
        return False
    try:
        date.fromisoformat(value + ('-01-01' if len(value) == 4 else '-01' if len(value) == 7 else ''))
        return True
    except ValueError:
        return False


def _photo(photo, clock):
    if not isinstance(photo, dict) or photo.get('enabled') is not True:
        return None, 'PHOTO_DISABLED'
    try:
        checked, expires = _stamp(photo['checked_at']), _stamp(photo['expires_at'])
        if checked > clock + timedelta(minutes=5) or expires <= checked:
            raise ValueError('Invalid verification interval')
        if expires <= clock:
            return None, 'PHOTO_POLICY_EXPIRED'
        if not (any(_url(photo['url'], host, '/wikipedia/commons/') for host in ('upload.wikimedia.org','thumb.wikimedia.org'))
                and IMAGE_PATH.fullmatch(urlsplit(photo['url']).path)
                and _url(photo['source_url'], 'commons.wikimedia.org', '/wiki/File:')
                and photo['license_url'] == LICENSE_URLS.get(photo['license'])):
            raise ValueError('Unapproved image, source, or license URL')
        if not all(_plain(photo.get(key), maximum) for key, maximum in (
                ('id', 100), ('author', 500), ('alt', 500), ('identity_evidence', 1500))):
            raise ValueError('Missing plain-text attribution or identity review')
        if photo.get('kind') not in PHOTO_KIND_ORDER:
            raise ValueError('Explicit reviewed photo kind required')
        if not _taken(photo.get('taken_at')):
            raise ValueError('Invalid capture date')
        for key in ('width', 'height'):
            if type(photo.get(key)) is not int or not 1 <= photo[key] <= 20000:
                raise ValueError('Explicit bounded image dimensions required')
    except (KeyError, ValueError, TypeError, OverflowError):
        return None, 'PHOTO_METADATA_INVALID'
    return {key: photo.get(key) for key in (
        'id', 'url', 'source_url', 'author', 'license', 'license_url', 'alt',
        'checked_at', 'expires_at', 'taken_at', 'width', 'height', 'kind')}, None


@lru_cache(maxsize=4)
def _read_manifest(content):
    """Cache parsing of at most four already bounded byte snapshots."""
    try:
        value = json.loads(content.decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if (not isinstance(value, dict) or not _plain(value.get('version'), 100)
            or not isinstance(value.get('places'), list) or len(value['places']) > 1000):
        return None
    return value


def load_manifest():
    """Read current permissions even when replacement preserves file metadata."""
    try:
        # Metadata can collide or be stale on mounted filesystems. Open/read on
        # every access so replacement and access revocation cannot hit old rights.
        with MANIFEST_PATH.open('rb') as source:
            content = source.read(MAX_MANIFEST_BYTES + 1)
    except OSError:
        return None
    if len(content) > MAX_MANIFEST_BYTES:
        return None
    return _read_manifest(content)


def unavailable(reason):
    return {'photos': [], 'photo_status': {'state': 'unavailable', 'available_count': 0, 'reason_codes': [reason]}}


def select_photos(manifest, identity, *, approved, clock=None):
    """Pure selection with exact reviewed branch identity and independent rights."""
    clock = clock or datetime.now(timezone.utc)
    if not approved:
        return unavailable('PLACE_NOT_APPROVED')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('places'), list):
        return unavailable('PHOTO_MANIFEST_UNAVAILABLE')
    entries = [p for p in manifest['places'] if isinstance(p, dict)
               and p.get('city') == identity.get('city') and p.get('external_id') == identity.get('external_place_id')]
    if not entries:
        return unavailable('PHOTO_NOT_REVIEWED')
    if len(entries) != 1:
        return unavailable('PHOTO_IDENTITY_CONFLICT')
    entry = entries[0]
    if (not _plain(entry.get('address'), 1000) or _norm(entry['address']) != _norm(identity.get('address', ''))
            or not entry.get('canonical_url') or entry['canonical_url'].rstrip('/') != (identity.get('source_url') or '').rstrip('/')):
        return unavailable('PHOTO_IDENTITY_MISMATCH')
    if not isinstance(entry.get('photos'), list) or len(entry['photos']) > MAX_PHOTOS:
        return unavailable('PHOTO_METADATA_INVALID')
    result, reasons, ids, urls, sources = [], [], set(), set(), set()
    for raw in entry['photos']:
        photo, reason = _photo(raw, clock)
        source = unicodedata.normalize('NFC', unquote(urlsplit(photo['source_url']).path)).replace(' ', '_') if photo else None
        if photo and (photo['id'] in ids or photo['url'] in urls or source in sources):
            photo, reason = None, 'PHOTO_DUPLICATE'
        if photo:
            result.append(photo)
            ids.add(photo['id']); urls.add(photo['url']); sources.add(source)
        elif reason not in reasons:
            reasons.append(reason)
    # A photo's contents are reviewed metadata, never guessed from a file name.
    # Stable ordering makes food/interiors the lead image without changing ranking.
    result.sort(key=lambda photo: PHOTO_KIND_ORDER[photo['kind']])
    if any(photo['kind'] != 'exterior' for photo in result):
        seen_exterior = False
        selected = []
        for photo in result:
            if photo['kind'] == 'exterior':
                if seen_exterior:
                    continue
                seen_exterior = True
            selected.append(photo)
        result = selected
    return {'photos': result, 'photo_status': {'state': 'available' if result else 'unavailable',
            'available_count': len(result), 'reason_codes': reasons or ([] if result else ['PHOTO_NOT_REVIEWED'])}}


def for_places(con, place_ids, *, manifest=None, clock=None):
    """Current photo permission in at most three reads for bounded identities."""
    ids=sorted(set(place_ids)); result={ident:unavailable('PHOTO_NOT_REVIEWED') for ident in ids}
    public_ids=[ident for ident in ids if isinstance(ident,str) and re.fullmatch(r'osm_(?:node|way|relation)_[1-9][0-9]*',ident)]
    if public_ids:
        from .photo_provider import identity_hash
        from .public_places import POLICY
        public_marks=','.join('?' for _ in public_ids)
        instant=clock or datetime.now(timezone.utc)
        # Permission/identity is rechecked at read time; photo cache cannot revive a revoked place.
        rows=con.execute(f"SELECT p.*,x.identity_hash,x.payload_json,x.expires_at AS cache_expires FROM place_identities p JOIN place_photo_cache x ON x.place_id=p.id WHERE p.id IN ({public_marks}) AND p.deleted_at IS NULL AND p.provider='openstreetmap' AND EXISTS(SELECT 1 FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id JOIN evidence_sources s ON s.place_id=p.id WHERE c.place_id=p.id AND c.status='public_data' AND k.status='public_data' AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1 AND s.policy_version=? AND s.source_group='OpenStreetMap' AND NOT EXISTS(SELECT 1 FROM discovery_tombstones t WHERE (t.kind='pack' AND t.target_id=k.id) OR (t.kind='source' AND t.target_id=s.id)))",[*public_ids,POLICY])
        for row in rows:
            expiry=_stamp(row['cache_expires'])
            if row['identity_hash']!=identity_hash(dict(row)) or not expiry or expiry<=instant:continue
            try:
                payload=json.loads(row['payload_json'])
                if not isinstance(payload,dict):continue
                valid=[]
                for photo in payload.get('photos',[])[:MAX_PHOTOS]:
                    value,_=_photo({**photo,'enabled':True,'identity_evidence':'Exact feature media link'},instant)
                    if value:valid.append(value)
                result[row['id']]={'photos':valid,'photo_status':{'state':'available' if valid else 'unavailable','available_count':len(valid),'reason_codes':[] if valid else payload.get('photo_status',{}).get('reason_codes',['NO_LINKED_PHOTO'])}}
            except (ValueError,TypeError,KeyError):pass
    ids=[ident for ident in ids if ident not in public_ids]
    if not ids:return result
    marks=','.join('?' for _ in ids)
    rows={r['id']:dict(r) for r in con.execute(f'SELECT * FROM place_identities WHERE id IN ({marks}) AND deleted_at IS NULL',ids)}
    approved={r['place_id'] for r in con.execute(
        "SELECT DISTINCT c.place_id FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id "
        "JOIN evidence_sources s ON s.place_id=c.place_id "
        f"WHERE c.place_id IN ({marks}) AND c.status='approved' AND k.status='approved' AND k.synthetic=0 "
        "AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1 AND s.policy_version=k.version "
        "AND NOT EXISTS(SELECT 1 FROM discovery_tombstones t WHERE t.kind='pack' AND t.target_id=k.id) "
        "AND NOT EXISTS(SELECT 1 FROM discovery_tombstones t WHERE t.kind='source' AND t.target_id=s.id)",ids)}
    metadata=load_manifest() if manifest is None else manifest
    for ident in ids:
        identity=rows.get(ident)
        result[ident]=select_photos(metadata,identity,approved=ident in approved and identity['identity_status']=='verified' and identity['provider']=='manual_official',clock=clock) if identity else unavailable('PLACE_NOT_APPROVED')
    return result


def for_place(con, place_id, *, manifest=None, clock=None):
    return for_places(con,[place_id],manifest=manifest,clock=clock)[place_id]
