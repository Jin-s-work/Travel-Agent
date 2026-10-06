"""Versioned SQLite storage; no import-time connection or legacy-data migration."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import fcntl


SCHEMA_VERSION = 10
SCHEMA = """
CREATE TABLE users (
 id TEXT PRIMARY KEY, email TEXT NOT NULL, auth_provider TEXT NOT NULL,
 auth_subject TEXT NOT NULL, display_name TEXT, role TEXT NOT NULL DEFAULT 'member',
 status TEXT NOT NULL DEFAULT 'active', session_epoch INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(auth_provider, auth_subject)
);
CREATE TABLE invitations (
 id TEXT PRIMARY KEY, email TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL, used_at TEXT, revoked_at TEXT,
 user_id TEXT REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE INDEX invitations_email ON invitations(email);
CREATE TABLE sessions (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 token_hash TEXT NOT NULL UNIQUE, csrf_token TEXT NOT NULL,
 expires_at TEXT NOT NULL, epoch INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX sessions_user_expiry ON sessions(user_id, expires_at);
CREATE TABLE trips (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
 title TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 conditions_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
 active_index_id TEXT, deleted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX trips_owner ON trips(owner_id, deleted_at, updated_at);
CREATE TABLE trip_stops (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id),
 sequence INTEGER NOT NULL, city TEXT NOT NULL, start_date TEXT NOT NULL,
 end_date TEXT NOT NULL, timezone TEXT NOT NULL, base_location TEXT,
 recommendation_supported INTEGER NOT NULL DEFAULT 0,
 UNIQUE(trip_id, sequence)
);
CREATE TABLE source_documents (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id),
 display_filename TEXT NOT NULL, opaque_path TEXT NOT NULL,
 content_hash TEXT NOT NULL, active_generation_id TEXT,
 status TEXT NOT NULL DEFAULT 'pending', deleted_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX document_active_hash ON source_documents(trip_id, content_hash) WHERE deleted_at IS NULL;
CREATE TABLE document_generations (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES source_documents(id),
 generation_no INTEGER NOT NULL, parse_version TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'processing', extracted_json TEXT,
 error_code TEXT, created_at TEXT NOT NULL, completed_at TEXT,
 UNIQUE(document_id, generation_no)
);
CREATE TABLE bookings (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id),
 document_id TEXT REFERENCES source_documents(id), generation_id TEXT REFERENCES document_generations(id),
 stable_item_key TEXT, kind TEXT, status TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, extracted_json TEXT NOT NULL,
 effective_json TEXT NOT NULL, conflicts_json TEXT NOT NULL DEFAULT '[]',
 date_start TEXT, date_end TEXT, deleted_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX bookings_trip ON bookings(trip_id, deleted_at, date_start);
CREATE INDEX bookings_document ON bookings(document_id, stable_item_key);
CREATE TABLE booking_events (
 id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
 trip_id TEXT NOT NULL REFERENCES trips(id), event_type TEXT NOT NULL,
 start_local TEXT, end_local TEXT, start_timezone TEXT, end_timezone TEXT,
 start_instant TEXT, end_instant TEXT, location TEXT
);
CREATE INDEX booking_events_time ON booking_events(trip_id, start_instant, end_instant);
CREATE INDEX booking_events_local ON booking_events(trip_id, start_local, end_local);
CREATE TABLE booking_overrides (
 id TEXT PRIMARY KEY, booking_id TEXT NOT NULL REFERENCES bookings(id),
 field_path TEXT NOT NULL, value_json TEXT NOT NULL,
 editor_id TEXT NOT NULL REFERENCES users(id), revision INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, reason TEXT, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX booking_active_override ON booking_overrides(booking_id,field_path) WHERE active=1;
CREATE TABLE deletion_tombstones (
 target_type TEXT NOT NULL, target_id TEXT NOT NULL, trip_id TEXT NOT NULL REFERENCES trips(id),
 deletion_epoch INTEGER NOT NULL DEFAULT 1, requested_at TEXT NOT NULL, completed_at TEXT,
 PRIMARY KEY(target_type, target_id)
);
CREATE TABLE processing_receipts (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 trip_id TEXT NOT NULL REFERENCES trips(id), idempotency_key TEXT,
 payload_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(user_id,trip_id,idempotency_key)
);
"""


class Database:
    """Each operation gets a closed-after-use connection and short transaction.

    Auth and audit timestamps use UTC ISO-8601 text. No network calls belong
    inside a connection context. In-memory databases are deliberately rejected:
    each context has an independent connection; tests should use tmp_path.
    """

    backend = 'sqlite'

    def __new__(cls, path, *, url=None):
        if url:
            from src.storage.postgres import PostgresDatabase
            return PostgresDatabase(url, path)
        return super().__new__(cls)

    def __init__(self, path: str | Path, *, url=None):
        self.path = Path(path)
        if str(path) == ':memory:':
            raise ValueError('Use a temporary file, not :memory:, for independent connections')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.parent / '.migration.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self._migrate()
        self.path.chmod(0o600)

    def schema_version(self):
        with self.connect() as con:
            return con.execute('PRAGMA user_version').fetchone()[0]

    def close(self):
        pass

    def _migrate(self):
        with self.connect() as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError('Database schema is newer than this application')
            if version == 0:
                # executescript starts its own transaction; migration is all-or-nothing.
                connection.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nPRAGMA user_version=1;\nCOMMIT;')
                version = 1
            if version == 1:
                from src.reliability.schema import migration_sql
                connection.executescript('BEGIN IMMEDIATE;\n' + migration_sql() + '\nPRAGMA user_version=2;\nCOMMIT;')
                version = 2
            if version == 2:
                from src.research.schema import SCHEMA as review_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + review_schema + '\nPRAGMA user_version=3;\nCOMMIT;')
                version = 3
            if version == 3:
                from src.discovery.schema import SCHEMA as discovery_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + discovery_schema + '\nPRAGMA user_version=4;\nCOMMIT;')
                version = 4
            if version == 4:
                from src.recommendations.schema import SCHEMA as recommendation_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + recommendation_schema + '\nPRAGMA user_version=5;\nCOMMIT;')
                version = 5
            if version == 5:
                from src.itineraries.schema import SCHEMA as itinerary_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + itinerary_schema + '\nPRAGMA user_version=6;\nCOMMIT;')
                version = 6
            if version == 6:
                from src.operations.schema import SCHEMA as operations_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + operations_schema + '\nPRAGMA user_version=7;\nCOMMIT;')
                version = 7
            if version == 7:
                connection.executescript('''BEGIN IMMEDIATE;
ALTER TABLE research_candidates ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;
UPDATE research_candidates SET sort_order=(SELECT count(*) FROM research_candidates prior WHERE prior.pack_id=research_candidates.pack_id AND prior.rowid<research_candidates.rowid);
PRAGMA user_version=8;
COMMIT;''')
                version = 8
            if version == 8:
                from src.travel_tools.schema import SCHEMA as travel_tools_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + travel_tools_schema + '\nPRAGMA user_version=9;\nCOMMIT;')
                version = 9
            if version == 9:
                from src.product.schema import SCHEMA as product_schema
                connection.executescript('BEGIN IMMEDIATE;\n' + product_schema + '\nPRAGMA user_version=10;\nCOMMIT;')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level='DEFERRED')
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
