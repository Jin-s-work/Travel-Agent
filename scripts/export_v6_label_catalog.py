#!/usr/bin/env python3
"""Read-only export of the *public* recommendation catalog for blind labeling.

Run by the operator (needs the private Render/Supabase env file). It reads only
place/catalog tables through the same visibility rules the product uses
(approved verified packs + unexpired public-map packs, active display-permitted
sources). It never reads trips, mail, accommodations, bookmarks, exclusions,
feedback or users, and it writes nothing to the database.

Review aggregates are not exported (actual active aggregates were 0 on
2026-10-08 [past value]); candidates carry review_evidence=unavailable, the
same default the recommendation service applies when no aggregate exists.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CITIES = ('tokyo', 'barcelona')


class ReadOnlyConnection:
    """Application SQL dialect, SELECT only."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, statement, parameters=()):
        if not statement.lstrip().upper().startswith('SELECT'):
            raise ValueError('READ_ONLY_QUERY_REQUIRED')
        from src.storage.postgres import translate, row_factory
        import re
        query = translate(statement)
        if parameters:
            query = re.sub(r'%(?!s)', '%%', query)
        return self.raw.cursor(row_factory=row_factory).execute(query, tuple(parameters))


def collect(con, cities=CITIES):
    """Public catalog rows per city via DiscoveryService's own read helpers."""
    from src.discovery.service import DiscoveryService
    from src.recommendations.labeling import public_candidate
    reader = DiscoveryService(None, None, None, None, allow_synthetic=False)
    output = {}
    for city in cities:
        approved = reader._candidates(con, reader._catalog_rows(con, city, available_only=True))
        public = reader._public_catalog(con, city, set())
        rows = {}
        for row in approved + public:
            if row.get('synthetic'):
                continue
            rows.setdefault(row['place_id'], public_candidate(row))
        output[city] = [rows[k] for k in sorted(rows)]
    return output


def counts(cities):
    report = {}
    for city, rows in cities.items():
        by = {}
        for row in rows:
            key = ('public_map' if row.get('provider') == 'openstreetmap' else 'approved') + '/' + row['category']
            by[key] = by.get(key, 0) + 1
        report[city] = {'candidates': len(rows), 'by_source_category': by}
    return report


# Fixed categories only: the error text itself can contain the host, user or
# the whole connection string, so it is matched here and never printed.
_FAILURE_KINDS = (
    ('missing_env_value', ('keyerror',)),
    ('dns', ('could not translate host name', 'nodename nor servname', 'name or service not known')),
    ('timeout', ('timeout expired', 'timed out', 'connection timeout')),
    ('network', ('network is unreachable', 'no route to host', 'connection refused', 'connection reset')),
    ('auth', ('password authentication failed', 'authentication failed', 'no password supplied')),
    ('tenant_or_project', ('tenant or user not found', 'tenant/user', 'enotfound', 'project is paused', 'paused',
                           'does not exist')),
    ('ssl', ('ssl', 'certificate', 'sslrootcert')),
    ('too_many_connections', ('too many connections', 'max client connections', 'remaining connection slots')),
)


# Words that may appear in libpq/psycopg connection errors. Only these words are
# ever printed (as a sorted set), so no host, user, password or URL can leak.
_SAFE_WORDS = ('server', 'closed', 'unexpectedly', 'refused', 'reset', 'timeout', 'expired', 'unreachable',
               'route', 'address', 'family', 'supported', 'assign', 'requested', 'permitted', 'operation',
               'invalid', 'dsn', 'missing', 'sslmode', 'ssl', 'certificate', 'verify', 'root', 'file', 'exist',
               'tenant', 'pooler', 'paused', 'password', 'authentication', 'fatal', 'receive', 'send', 'eof',
               'translate', 'host', 'name', 'connection', 'failed', 'network', 'prepared', 'statement',
               'circuit', 'breaker', 'too', 'many', 'clients', 'database', 'role', 'user', 'found', 'denied')


def failure_words(error):
    import re
    words = set(re.findall(r'[a-z]+', (type(error).__name__ + ' ' + str(error)).lower()))
    return sorted(words & set(_SAFE_WORDS))


def target_shape(env):
    """Non-secret shape of the configured target: direct host (IPv6-only on Supabase)
    or pooler, port, sslmode. No host name, user or password is returned."""
    try:
        from urllib.parse import urlsplit, parse_qs
        from dotenv import dotenv_values
        from scripts.prepare_render_env import render_values
        url = urlsplit(render_values(dotenv_values(env, interpolate=False), profile='openai', with_apify=True)['DATABASE_URL'])
        host = (url.hostname or '').lower()
        query = parse_qs(url.query)
        return {'kind': 'pooler' if host.endswith('pooler.supabase.com') else
                        'direct' if host.startswith('db.') and host.endswith('.supabase.co') else 'other',
                'port': url.port, 'sslmode': (query.get('sslmode') or [None])[0],
                'sslrootcert_set': bool(query.get('sslrootcert'))}
    except Exception as error:
        return {'unavailable': type(error).__name__}


def failure_kind(error):
    text = (type(error).__name__ + ' ' + str(error)).lower()
    for kind, needles in _FAILURE_KINDS:
        if any(needle in text for needle in needles):
            return kind
    return 'other'


def read(env):
    from dotenv import dotenv_values
    from scripts.prepare_render_env import render_values, DB_CA_PATH
    import psycopg
    values = render_values(dotenv_values(env, interpolate=False), profile='openai', with_apify=True)
    ca = Path(__file__).resolve().parents[1] / 'deploy/render-supabase/prod-ca-2021.crt'
    url = values['DATABASE_URL'].replace(DB_CA_PATH, str(ca))
    exported_at = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(url, connect_timeout=12) as raw:
        raw.execute('SET TRANSACTION READ ONLY'); raw.execute('SET LOCAL statement_timeout=20000')
        raw.execute('SET LOCAL search_path TO travel,pg_catalog')
        cities = collect(ReadOnlyConnection(raw))
        raw.rollback()
    return document(cities, exported_at)


def document(cities, exported_at, *, synthetic=False):
    return {'schema_version': 1, 'kind': 'recommendation_label_catalog', 'synthetic': synthetic,
            'exported_at': exported_at, 'writes': 0, 'provider_calls': 0,
            'scope': 'public catalog only; no trip, mail, accommodation, bookmark, feedback or user rows',
            'review_evidence': 'not_exported', 'counts': counts(cities), 'cities': cities}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = read(args.env)
    except Exception as error:  # never echo connection strings or secrets
        print(json.dumps({'status': 'unavailable', 'reason': 'READ_ONLY_CATALOG_EXPORT_FAILED',
                          'error_type': type(error).__name__, 'failure_kind': failure_kind(error),
                          'failure_words': failure_words(error), 'target': target_shape(args.env),
                          'sqlstate': getattr(error, 'sqlstate', None), 'writes': 0}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    os.chmod(args.output, 0o600)
    print(json.dumps({'status': 'exported', 'exported_at': report['exported_at'], 'counts': report['counts'],
                      'writes': 0, 'provider_calls': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
