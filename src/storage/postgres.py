"""PostgreSQL adapter for the application's deliberately small SQL dialect.

The global transaction advisory lock preserves SQLite BEGIN IMMEDIATE semantics
for budget/lease/version operations across overlapping Render deployments.
This is intentionally a five-user design, not a horizontal scaling claim.
"""
from contextlib import contextmanager
from pathlib import Path
import re
import uuid

import psycopg
from psycopg_pool import ConnectionPool
from psycopg import sql

WRITE_LOCK = 726384102
MIGRATION_LOCK = 726384103


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def row_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Row(zip(names, values))


def translate(statement):
    """Translate only known application syntax, never values/identifiers from clients."""
    statement = statement.strip().rstrip(';')
    statement = re.sub(r"json_extract\((\w+(?:\.\w+)?),'\$\.resume_from'\)", r"(\1::jsonb->>'resume_from')", statement)
    statement = re.sub(r"json_extract\((\w+(?:\.\w+)?),'\$\.strict_pass'\)=1", r"(\1::jsonb->>'strict_pass')='true'", statement)
    statement = statement.replace('MAX(attempt-1,0)', 'GREATEST(attempt-1,0)')
    statement = statement.replace('session_epoch=MAX(session_epoch,?)', 'session_epoch=GREATEST(session_epoch,?)')
    if statement.upper().startswith('INSERT OR IGNORE '):
        statement = re.sub('INSERT OR IGNORE', 'INSERT', statement, count=1, flags=re.I) + ' ON CONFLICT DO NOTHING'
    if statement.upper().startswith('INSERT OR REPLACE '):
        table = re.match(r'INSERT OR REPLACE INTO (\w+)', statement, re.I)[1]
        if table == 'discovery_tombstones':
            suffix = ' ON CONFLICT(kind,target_id) DO UPDATE SET reason=excluded.reason,created_at=excluded.created_at'
        elif table == 'research_tombstones':
            suffix = ' ON CONFLICT(target_type,target_id) DO UPDATE SET reason=excluded.reason,requested_at=excluded.requested_at,completed_at=excluded.completed_at'
        else:
            raise ValueError('Unsupported replace operation')
        statement = re.sub('INSERT OR REPLACE', 'INSERT', statement, count=1, flags=re.I) + suffix
    # Values always remain bound parameters; quoted '?' is a literal.
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", statement)
    return ''.join(part.replace('?', '%s') if index % 2 == 0 else part for index, part in enumerate(parts))


class Connection:
    def __init__(self, raw):
        self.raw, self.in_transaction = raw, False

    def begin(self):
        if not self.in_transaction:
            self.raw.execute('BEGIN')
            self.in_transaction = True
            self.raw.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))

    def execute(self, statement, parameters=None):
        if statement.strip().upper() in {'BEGIN IMMEDIATE', 'BEGIN'}:
            self.begin()
            return self.raw.execute('SELECT 1')
        if statement.strip().upper().startswith('PRAGMA'):
            raise ValueError('SQLite-only operation is unavailable on PostgreSQL')
        if re.match(r'\s*(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b', statement, re.I):
            self.begin()
        query = translate(statement)
        # Psycopg uses % placeholders. Escape literal percent signs only when
        # parameters are present, leaving our generated %s placeholders intact.
        if parameters is not None:
            query = re.sub(r'%(?!s)', '%%', query)
            parameters=tuple(int(value) if isinstance(value,bool) else value for value in parameters)
        return self.raw.execute(query, parameters)

    def executemany(self, statement, parameters):
        self.begin()
        cursor = self.raw.cursor()
        cursor.executemany(translate(statement), parameters)
        return cursor

    def commit(self):
        if self.in_transaction:
            self.raw.execute('COMMIT'); self.in_transaction = False

    def rollback(self):
        if self.in_transaction:
            self.raw.execute('ROLLBACK'); self.in_transaction = False


def schema_sql():
    from src.foundation.db import SCHEMA
    from src.reliability.schema import migration_sql
    from src.research.schema import SCHEMA as reviews
    from src.discovery.schema import SCHEMA as discovery
    from src.recommendations.schema import SCHEMA as recommendations
    from src.itineraries.schema import SCHEMA as itineraries
    from src.operations.schema import SCHEMA as operations
    from src.travel_tools.schema import SCHEMA as travel_tools
    from src.product.schema import SCHEMA as product
    from src.discovery.intent_schema import SCHEMA as intents
    from src.accommodations.schema import SCHEMA as accommodations
    from src.foundation.journey_schema import SCHEMA as journey
    from src.research.stage2_schema import SCHEMA as stage2
    from src.discovery.photo_schema import SCHEMA as photos
    reliability = re.sub(r'UPDATE processing_receipts\s+SET.*?;', '', migration_sql(), flags=re.S)
    combined = '\n'.join([SCHEMA, reliability, reviews, discovery, recommendations, itineraries, operations, travel_tools, product, intents, accommodations, journey, stage2, photos])
    combined = combined.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY')
    combined = re.sub(r'\bINTEGER\b', 'BIGINT', combined)
    combined = combined.replace('lower(hex(randomblob(16)))', "'" + uuid.uuid4().hex + "'")
    combined = combined.replace("json_extract(payload_json,'$.resume_from')", "(payload_json::jsonb->>'resume_from')")
    return combined+'\nALTER TABLE research_candidates ADD COLUMN sort_order BIGINT NOT NULL DEFAULT 0;'


CLOUD_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_artifacts (
 ref TEXT PRIMARY KEY, scope_id TEXT NOT NULL, content BYTEA NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cloud_objects (
 key TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id),
 sha256 TEXT NOT NULL, byte_size BIGINT NOT NULL, state TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cloud_import_objects (
 key TEXT PRIMARY KEY, sha256 TEXT NOT NULL, byte_size BIGINT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS vector_collections (
 name TEXT PRIMARY KEY, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vector_records (
 collection TEXT NOT NULL REFERENCES vector_collections(name) ON DELETE CASCADE,
 id TEXT NOT NULL, metadata_json TEXT NOT NULL, document TEXT NOT NULL,
 embedding extensions.vector NOT NULL, PRIMARY KEY(collection,id)
);
"""


class PostgresDatabase:
    backend = 'postgres'

    def __init__(self, url, path, *, schema='travel', max_connections=5):
        if not re.fullmatch(r'travel(?:_[a-z0-9_]+)?', schema):
            raise ValueError('Invalid private database schema')
        self.path, self.schema = Path(path), schema
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._url = url
        self._migrate()
        def configure(con):
            con.execute(sql.SQL('SET search_path TO {}, extensions, pg_catalog').format(sql.Identifier(schema)))
            con.execute("SET statement_timeout='15s'")
            con.execute("SET lock_timeout='5s'")
            con.execute("SET idle_in_transaction_session_timeout='20s'")
        self.pool = ConnectionPool(url, min_size=0, max_size=max_connections, timeout=8,
            kwargs={'autocommit': True, 'row_factory': row_factory, 'connect_timeout': 8, 'prepare_threshold': None},
            configure=configure, open=True)

    def _migrate(self):
        from src.foundation.db import SCHEMA_VERSION
        # Use the IPv4 session pooler (5432), not transaction pooling (6543).
        with psycopg.connect(self._url, autocommit=True, connect_timeout=8) as con:
            with con.transaction():
                con.execute("SET LOCAL statement_timeout='60s'")
                # Match application BEGIN IMMEDIATE before touching any table.
                # An overlapping old worker may hold rows under WRITE_LOCK; DDL
                # must not take table locks first and deadlock against that worker.
                # All new migrations use this same WRITE -> MIGRATION order.
                con.execute('SELECT pg_advisory_xact_lock(%s)', (WRITE_LOCK,))
                con.execute('SELECT pg_advisory_xact_lock(%s)', (MIGRATION_LOCK,))
                con.execute('CREATE SCHEMA IF NOT EXISTS extensions')
                con.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions')
                con.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(self.schema)))
                con.execute(sql.SQL('SET LOCAL search_path TO {}, extensions, pg_catalog').format(sql.Identifier(self.schema)))
                con.execute('CREATE TABLE IF NOT EXISTS schema_version(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL, cloud_version INTEGER NOT NULL)')
                row = con.execute('SELECT version,cloud_version FROM schema_version WHERE singleton=1').fetchone()
                if row is None:
                    con.execute(schema_sql(), prepare=False)
                    con.execute(CLOUD_SCHEMA, prepare=False)
                    con.execute('INSERT INTO schema_version VALUES(1,%s,1)', (SCHEMA_VERSION,))
                else:
                    if row == (7,1):
                        con.execute('ALTER TABLE research_candidates ADD COLUMN sort_order BIGINT NOT NULL DEFAULT 0')
                        row = (8,1)
                    if row == (8,1):
                        from src.travel_tools.schema import SCHEMA as travel_tools
                        con.execute(travel_tools, prepare=False)
                        con.execute('UPDATE schema_version SET version=9')
                        row = (9,1)
                    if row == (9,1):
                        from src.product.schema import SCHEMA as product
                        con.execute(product, prepare=False)
                        con.execute('UPDATE schema_version SET version=10')
                        row = (10,1)
                    if row == (10,1):
                        from src.discovery.intent_schema import SCHEMA as intents
                        con.execute(intents, prepare=False)
                        con.execute('UPDATE schema_version SET version=11')
                        row = (11,1)
                    if row == (11,1):
                        from src.accommodations.schema import SCHEMA as stays, migrate_legacy
                        con.execute(stays, prepare=False)
                        previous_factory=con.row_factory
                        try:
                            con.row_factory=row_factory
                            wrapped=Connection(con);wrapped.in_transaction=True
                            migrate_legacy(wrapped)
                        finally:
                            con.row_factory=previous_factory
                        con.execute('UPDATE schema_version SET version=12')
                        row = (12,1)
                    if row == (12,1):
                        from src.foundation.journey_schema import SCHEMA as journey
                        con.execute(journey, prepare=False)
                        con.execute('UPDATE schema_version SET version=13')
                        row = (13,1)
                    if row == (13,1):
                        from src.research.stage2_schema import SCHEMA as stage2
                        con.execute(stage2, prepare=False)
                        con.execute('UPDATE schema_version SET version=14')
                        row = (14,1)
                    if row == (14,1):
                        from src.discovery.photo_schema import SCHEMA as photos
                        con.execute(photos, prepare=False)
                        con.execute('UPDATE schema_version SET version=15')
                        row = (15,1)
                    if row != (SCHEMA_VERSION, 1):
                        raise RuntimeError('Unsupported PostgreSQL schema version')
                con.execute(CLOUD_SCHEMA, prepare=False)
                # Private schema is not exposed by PostgREST. Defense in depth:
                # no browser roles may read even if someone exposes the schema.
                con.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(self.schema)))
                roles = [r[0] for r in con.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")]
                for role in roles:
                    con.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(sql.Identifier(self.schema), sql.Identifier(role)))
                    con.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}').format(sql.Identifier(self.schema), sql.Identifier(role)))
                # Even an already-enabled ALTER requires AccessExclusiveLock.
                # Read the catalog first so a routine restart does not block
                # readers or reacquire exclusive locks across every private table.
                tables=con.execute(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relkind IN ('r','p') AND NOT c.relrowsecurity ORDER BY c.relname",
                    (self.schema,)).fetchall()
                for (table,) in tables:
                    con.execute(sql.SQL('ALTER TABLE {}.{} ENABLE ROW LEVEL SECURITY').format(
                        sql.Identifier(self.schema),sql.Identifier(table)))

    @contextmanager
    def connect(self):
        with self.pool.connection() as raw:
            con = Connection(raw)
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    def schema_version(self):
        with self.connect() as con:
            return con.execute('SELECT version FROM schema_version WHERE singleton=1').fetchone()[0]

    def close(self):
        self.pool.close()

    @contextmanager
    def pin_generation(self, ident):
        # Session-scoped pin on its own connection. PostgreSQL releases it on
        # host death; OS process IDs cannot identify another Render instance.
        with psycopg.connect(self._url, autocommit=True, connect_timeout=8) as con:
            key=self.schema+':'+ident
            con.execute('SELECT pg_advisory_lock_shared(hashtextextended(%s,0))',(key,))
            try: yield
            finally: con.execute('SELECT pg_advisory_unlock_shared(hashtextextended(%s,0))',(key,))

    def can_reclaim(self, con, ident):
        return con.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(?,0))',(self.schema+':'+ident,)).fetchone()[0]
