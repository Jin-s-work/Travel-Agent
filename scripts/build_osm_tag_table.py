#!/usr/bin/env python3
"""Build the OSM tag lookup table one city uses to fill what public Photon omits.

Public Photon returns no `extra` (cuisine, opening_hours), so public-map
candidates had none. This reads a free Geofabrik regional extract offline and
keeps, for restaurants, cafes and sights inside the same public areas the app
queries (candidate_pool.public_areas), only the tags the app stores
(public_places.OVERPASS_KEYS). No runtime call, no key, no cost.

    pip install osmium==4.3.1        # build-time only, not in requirements.lock
    curl -LO https://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
    python scripts/build_osm_tag_table.py --city tokyo --pbf kanto-latest.osm.pbf

The output (src/discovery/osm_tags/<city>.json) is derived OpenStreetMap data:
ODbL 1.0, "© OpenStreetMap contributors". Refresh by re-running on a newer extract.
"""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FOOD = ('restaurant', 'cafe')


def _distance(lat1, lon1, lat2, lon2):
    rad = math.radians
    a = math.sin(rad(lat2 - lat1) / 2) ** 2 + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(rad(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def inside(areas, lat, lon):
    return any(_distance(a['latitude'], a['longitude'], lat, lon) <= a['radius_m'] for a in areas)


def wanted(tags, sight_values):
    return tags.get('amenity') in FOOD or tags.get('tourism') in sight_values


def kept_tags(tags, keys):
    return {key: tags[key].strip() for key in keys if isinstance(tags.get(key), str) and tags[key].strip()}


def build(city, pbf):
    import osmium
    from src.discovery.candidate_pool import public_areas
    from src.discovery.public_places import OVERPASS_KEYS, SIGHT_VALUES
    areas = public_areas(city)
    if not areas:
        raise SystemExit(f'unsupported city: {city}')
    places, seen = {}, {'node': 0, 'way': 0}
    processor = (osmium.FileProcessor(str(pbf), osmium.osm.NODE | osmium.osm.WAY)
                 .with_locations()
                 .with_filter(osmium.filter.KeyFilter('amenity', 'tourism')))
    for obj in processor:
        tags = dict(obj.tags)
        if not wanted(tags, SIGHT_VALUES):
            continue
        if obj.is_node():
            kind, lat, lon = 'node', obj.location.lat, obj.location.lon
        elif obj.is_way():
            points = [n.location for n in obj.nodes if n.location.valid()]
            if not points:
                continue
            kind = 'way'
            lat = sum(p.lat for p in points) / len(points)
            lon = sum(p.lon for p in points) / len(points)
        else:
            continue
        if not inside(areas, lat, lon):
            continue
        seen[kind] += 1
        kept = kept_tags(tags, OVERPASS_KEYS)
        if kept:
            places[f'{kind}/{obj.id}'] = kept
    return areas, seen, dict(sorted(places.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--city', required=True)
    parser.add_argument('--pbf', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    out = args.out or ROOT / 'src/discovery/osm_tags' / f'{args.city}.json'
    areas, seen, places = build(args.city, args.pbf)
    digest = hashlib.sha256(args.pbf.read_bytes()).hexdigest()
    from src.discovery.public_places import OVERPASS_KEYS
    counts = {key: sum(1 for tags in places.values() if key in tags) for key in OVERPASS_KEYS}
    doc = {'schema_version': 1, 'kind': 'osm_tag_table', 'city': args.city,
           'license': 'ODbL 1.0', 'license_url': 'https://opendatacommons.org/licenses/odbl/1-0/',
           'attribution': '© OpenStreetMap contributors', 'attribution_url': 'https://www.openstreetmap.org/copyright',
           'source': {'file': args.pbf.name, 'sha256': digest, 'provider': 'Geofabrik GmbH (download.geofabrik.de)'},
           'generated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           'areas': [{k: a[k] for k in ('public_area_id', 'latitude', 'longitude', 'radius_m')} for a in areas],
           'scanned_in_areas': seen, 'places_with_tags': len(places), 'tag_counts': counts, 'places': places}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=0, sort_keys=False) + '\n', encoding='utf-8')
    print(json.dumps({k: doc[k] for k in ('city', 'scanned_in_areas', 'places_with_tags', 'tag_counts')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
