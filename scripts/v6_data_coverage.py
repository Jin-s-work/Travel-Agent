#!/usr/bin/env python3
"""Read-only per-city coverage of the public catalog (stage 4, bundle A).

Reports, per city: restaurant/cafe/attraction counts split into public-map and
reviewed sources, how many public-map ``opening_hours`` values the local subset
parser reads, photo state distribution, and the global OpenStreetMap call ledger
against its quota. No trip, mail, accommodation, owner id or review text is read
or written; usage rows are reduced to daily counts before leaving this process.

The 2026-10-08 audit values are attached as ``historical_reference`` and are
labelled as past values, never as the current measurement.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VERSION = 'v6_data_coverage_v1'
DEFAULT_CITIES = ('tokyo', 'barcelona')
CATEGORIES = ('restaurant', 'cafe', 'attraction')
UNSENT = ('CONNECT_FAILED', 'DNS_FAILED', 'DNS_BUSY')
HISTORICAL = {
    'label': '과거 값 · 2026-10-08 운영 DB 감사 (현재 측정 아님)',
    'checked_on': '2026-10-08',
    'cities': {'tokyo': {'restaurant_places': 50, 'permitted_photo_places': 2, 'permitted_photo_assets': 5},
               'barcelona': {'restaurant_places': 3, 'permitted_photo_places': 2, 'permitted_photo_assets': 6}},
    'active_real_review_aggregates': 0,
}


class ReadOnly:
    """Accept only SELECT statements written with ``?`` placeholders."""
    def __init__(self, con, *, convert=False, row_factory=None):
        self.con, self.convert, self.row_factory = con, convert, row_factory

    def execute(self, sql, params=()):
        if not sql.lstrip().upper().startswith('SELECT'):
            raise ValueError('READ_ONLY_QUERY_REQUIRED')
        if not self.convert:
            return self.con.execute(sql, params)
        if '%' in sql:
            raise ValueError('PERCENT_LITERAL_UNSUPPORTED')
        cursor = self.con.cursor(row_factory=self.row_factory) if self.row_factory else self.con.cursor()
        return cursor.execute(sql.replace('?', '%s'), params)


def _values(rows):
    """Row values in column order for any row shape: psycopg tuples (read()),
    sqlite3.Row, or the app's PostgreSQL Row, which is a dict (unpacking a dict
    would give the column names)."""
    for row in rows:
        yield tuple(row.values()) if isinstance(row, dict) else tuple(row)


_ACTIVE_SOURCE = ("s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1 "
                  "AND s.policy_version=f.policy_version")


def _catalog(con, city):
    counts = {'public_map': {k: 0 for k in CATEGORIES}, 'reviewed': {k: 0 for k in CATEGORIES}, 'other_categories': {}}
    rows = con.execute(
        "SELECT c.category, p.provider, count(DISTINCT c.place_id) FROM research_candidates c "
        "JOIN place_identities p ON p.id=c.place_id JOIN candidate_packs k ON k.id=c.pack_id "
        "WHERE p.city=? AND p.deleted_at IS NULL AND c.status IN ('approved','public_data') "
        "AND k.status IN ('approved','public_data') AND k.synthetic=0 "
        "AND EXISTS(SELECT 1 FROM evidence_sources s WHERE s.place_id=p.id AND s.status='active' "
        "AND s.read_confirmed=1 AND s.display_permitted=1) GROUP BY c.category, p.provider", (city,))
    for category, provider, n in _values(rows):
        group = 'public_map' if provider == 'openstreetmap' else 'reviewed'
        if category in CATEGORIES:
            counts[group][category] += n
        else:
            counts['other_categories'][category] = counts['other_categories'].get(category, 0) + n
    counts['total_public_map'] = sum(counts['public_map'].values())
    counts['total_reviewed'] = sum(counts['reviewed'].values())
    return counts


def _hours(con, city, now):
    from src.discovery.opening_hours import parse, PARSER_VERSION
    category = {r[0]: r[1] for r in con.execute(
        "SELECT c.place_id, c.category FROM research_candidates c JOIN place_identities p ON p.id=c.place_id "
        "WHERE p.city=? AND p.provider='openstreetmap' AND p.deleted_at IS NULL AND c.status='public_data' "
        "ORDER BY c.updated_at", (city,))}
    latest = {}
    for place_id, value, checked in _values(con.execute(
            "SELECT f.place_id, f.value_json, f.checked_at FROM place_facts f "
            "JOIN place_identities p ON p.id=f.place_id JOIN evidence_sources s ON s.id=f.source_id "
            "WHERE p.city=? AND p.provider='openstreetmap' AND p.deleted_at IS NULL "
            "AND f.field='public_map_tags' AND f.expires_at>? AND " + _ACTIVE_SOURCE, (city, now))):
        if place_id not in latest or checked > latest[place_id][1]:
            latest[place_id] = (value, checked)
    states, by_category, reasons = Counter(), {}, Counter()
    for place_id, (value, _) in latest.items():
        try:
            raw = (json.loads(value or '{}').get('tags') or {}).get('opening_hours')
        except (ValueError, AttributeError):
            raw = None
        result = parse(raw)
        states[result['state']] += 1
        reasons.update(result['reason_codes'])
        bucket = by_category.setdefault(category.get(place_id, 'unknown'), Counter())
        bucket[result['state']] += 1
    with_raw = sum(n for state, n in states.items() if state != 'missing')
    read = states['parsed'] + states['partial']
    stored = con.execute(
        "SELECT f.status, count(DISTINCT f.place_id) FROM place_facts f JOIN place_identities p ON p.id=f.place_id "
        "JOIN evidence_sources s ON s.id=f.source_id WHERE p.city=? AND p.provider='openstreetmap' "
        "AND p.deleted_at IS NULL AND f.field='opening_hours' AND f.expires_at>? AND " + _ACTIVE_SOURCE +
        " GROUP BY f.status", (city, now))
    return {'parser_version': PARSER_VERSION, 'public_places_with_current_tags': len(latest),
            'places_with_raw_hours': with_raw, 'states': {k: states.get(k, 0) for k in ('parsed', 'partial', 'unparsed', 'missing')},
            'parse_success_rate': round(read / with_raw, 4) if with_raw else None,
            'raw_hours_presence_rate': round(with_raw / len(latest), 4) if latest else None,
            'reason_codes': dict(sorted(reasons.items())),
            'by_category': {k: dict(v) for k, v in sorted(by_category.items())},
            'stored_opening_hours_facts_by_status': {status: n for status, n in _values(stored)},
            'success_definition': 'parsed 또는 partial(공휴일 표기만 미해석) / opening_hours 원문이 있는 공개지도 장소'}


def _photos(con, dict_con, city, now):
    from src.discovery.photos import photo_availability, for_places
    cache = Counter()
    for payload, expiry in _values(con.execute(
            "SELECT x.payload_json, x.expires_at FROM place_photo_cache x JOIN place_identities p ON p.id=x.place_id "
            "WHERE p.city=? AND p.deleted_at IS NULL", (city,))):
        data = json.loads(payload or '{}')
        cache['expired' if expiry <= now else 'available' if data.get('photos') else
              photo_availability(data.get('photo_status', {}).get('reason_codes', []))] += 1
    ids = [r[0] for r in con.execute('SELECT id FROM place_identities WHERE city=? AND deleted_at IS NULL', (city,))]
    photos = list(for_places(dict_con, ids, clock=datetime.fromisoformat(now)).values()) if ids else []
    availability = Counter(p['photo_status']['availability_state'] for p in photos)
    return {'photo_cache_states': dict(sorted(cache.items())),
            'current_photo_availability': dict(sorted(availability.items())),
            'currently_permitted_photo_places': sum(bool(p['photos']) for p in photos),
            'currently_permitted_photo_assets': sum(len(p['photos']) for p in photos),
            'actual_browser_img_load_success_rate': None}


def _map_calls(con, now, since):
    from src.discovery.public_places import PROVIDER, GLOBAL_DAILY, USER_DAILY, COOLDOWN
    days, owners, errors = {}, {}, Counter()
    for day, owner, code in _values(con.execute(
            "SELECT period_day, owner_id, error_code FROM usage_reservations WHERE provider=? AND period_day>=? "
            "ORDER BY period_day", (PROVIDER, since))):
        row = days.setdefault(day, {'requests_recorded': 0, 'quota_consuming': 0, 'unsent_connection_failures': 0})
        row['requests_recorded'] += 1
        if code in UNSENT:
            row['unsent_connection_failures'] += 1
        else:
            row['quota_consuming'] += 1
            owners.setdefault(day, Counter())[owner] += 1
        if code:
            errors[code] += 1
    for day, row in days.items():
        # Owner ids never leave this function; only the busiest owner's count does.
        row['max_quota_calls_by_one_user'] = max(owners.get(day, {0: 0}).values())
        row['global_quota_remaining'] = max(0, GLOBAL_DAILY - row['quota_consuming'])
    today = now[:10]
    used = days.get(today, {}).get('quota_consuming', 0)
    return {'provider': PROVIDER, 'since_day': since, 'quota': {'global_daily': GLOBAL_DAILY, 'user_daily': USER_DAILY,
            'cooldown_seconds': int(COOLDOWN.total_seconds())}, 'today': today, 'today_quota_consuming': used,
            'today_global_remaining': max(0, GLOBAL_DAILY - used), 'by_day': days,
            'total_quota_consuming': sum(r['quota_consuming'] for r in days.values()),
            'error_codes': dict(sorted(errors.items())),
            'scope_note': '호출 장부는 도시별 열이 없어 전역 합계만 보고한다. 공급자 비용은 0이다.'}


def collect(con, *, now=None, cities=DEFAULT_CITIES, since_days=7, dict_con=None, measurement='production_read_only'):
    """``con`` yields index-addressable rows; ``dict_con`` key-addressable rows (photos helper)."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = now.isoformat()
    since = (now - timedelta(days=since_days)).date().isoformat()
    report = {'version': VERSION, 'checked_at': stamp, 'measurement': measurement, 'writes': 0, 'provider_calls': 0,
              'cities': {}, 'historical_reference': HISTORICAL}
    for city in cities:
        report['cities'][city] = {'catalog': _catalog(con, city), 'opening_hours': _hours(con, city, stamp),
                                  'photos': _photos(con, dict_con or con, city, stamp)}
    report['map_calls'] = _map_calls(con, stamp, since)
    return report


def read(env, *, cities=DEFAULT_CITIES, since_days=7):
    from dotenv import dotenv_values
    from scripts.prepare_render_env import render_values, DB_CA_PATH
    import psycopg
    from psycopg.rows import dict_row
    values = render_values(dotenv_values(env, interpolate=False), profile='openai', with_apify=True)
    ca = Path(__file__).resolve().parents[1] / 'deploy/render-supabase/prod-ca-2021.crt'
    url = values['DATABASE_URL'].replace(DB_CA_PATH, str(ca))
    with psycopg.connect(url, connect_timeout=12) as con:
        con.execute('SET TRANSACTION READ ONLY')
        con.execute('SET LOCAL statement_timeout=10000')
        con.execute('SET LOCAL search_path TO travel,pg_catalog')
        return collect(ReadOnly(con, convert=True), cities=cities, since_days=since_days,
                       dict_con=ReadOnly(con, convert=True, row_factory=dict_row))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cities', default=','.join(DEFAULT_CITIES))
    parser.add_argument('--since-days', type=int, default=7)
    args = parser.parse_args(argv)
    cities = tuple(c for c in args.cities.split(',') if c)
    try:
        report = read(args.env, cities=cities, since_days=max(1, min(args.since_days, 60)))
    except Exception as error:
        # Only the exception class and SQLSTATE: messages can echo the URL or values.
        sqlstate = getattr(error, 'sqlstate', None)
        print(json.dumps({'status': 'unavailable', 'reason': 'READ_ONLY_DATABASE_AUDIT_FAILED', 'writes': 0,
                          'error_class': type(error).__name__,
                          'sqlstate': sqlstate if isinstance(sqlstate, str) else None}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
