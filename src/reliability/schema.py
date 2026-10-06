"""Additive version 2 migration. Existing phase-one receipts remain readable."""

SCHEMA = """
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), session_id TEXT NOT NULL,
 scope_kind TEXT NOT NULL CHECK(scope_kind IN ('personal_trip','admin_research')),
 scope_id TEXT NOT NULL CHECK(length(scope_id)>0), owner_id TEXT REFERENCES users(id), trip_id TEXT REFERENCES trips(id),
 operation TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','partial','failed','cancelled')),
 payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL, input_version INTEGER NOT NULL, deletion_epoch INTEGER NOT NULL DEFAULT 0,
 attempt INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL, deadline_at TEXT NOT NULL,
 lease_owner TEXT, lease_expires_at TEXT, heartbeat_at TEXT, fencing_token INTEGER NOT NULL DEFAULT 0,
 cancel_requested_at TEXT, result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT, retryable INTEGER NOT NULL DEFAULT 0,
 stage TEXT NOT NULL DEFAULT 'queued', done_count INTEGER NOT NULL DEFAULT 0, total_count INTEGER,
 checkpoint_json TEXT NOT NULL DEFAULT '{}', system_cleanup INTEGER NOT NULL DEFAULT 0,
 started_at TEXT, finished_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK((scope_kind='personal_trip' AND trip_id IS NOT NULL AND owner_id IS NOT NULL AND scope_id=trip_id)
    OR (scope_kind='admin_research' AND trip_id IS NULL AND owner_id IS NULL AND system_cleanup=0))
);
CREATE INDEX jobs_claim ON jobs(state,available_at,created_at);
CREATE INDEX jobs_scope ON jobs(actor_id,scope_kind,scope_id,created_at);
CREATE UNIQUE INDEX jobs_one_retry_child
 ON jobs(actor_id,scope_kind,scope_id,operation,json_extract(payload_json,'$.resume_from'))
 WHERE json_extract(payload_json,'$.resume_from') IS NOT NULL;
CREATE TABLE idempotency_keys (
 actor_id TEXT NOT NULL REFERENCES users(id), scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL CHECK(length(scope_id)>0),
 operation TEXT NOT NULL, key_hash TEXT NOT NULL, request_hash TEXT NOT NULL,
 job_id TEXT NOT NULL REFERENCES jobs(id), expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(actor_id,scope_kind,scope_id,operation,key_hash)
);
CREATE TABLE job_events (
 job_id TEXT NOT NULL REFERENCES jobs(id), sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(job_id,sequence)
);
CREATE TABLE dispatcher_leases (
 name TEXT PRIMARY KEY, owner TEXT NOT NULL, lease_expires_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL
);
CREATE TABLE trip_index_writers (
 trip_id TEXT PRIMARY KEY REFERENCES trips(id), holder_job_id TEXT NOT NULL REFERENCES jobs(id),
 fencing_token INTEGER NOT NULL, lease_expires_at TEXT NOT NULL
);
CREATE TABLE deletion_receipts (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL UNIQUE REFERENCES trips(id),
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), state TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE trip_index_generations (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id), collection_name TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('staged','ready','active','retired','failed','deleting','deleted')),
 base_trip_version INTEGER NOT NULL, source_manifest TEXT NOT NULL, embedding_model TEXT NOT NULL,
 embedding_dimension INTEGER NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id), fencing_token INTEGER NOT NULL,
 created_at TEXT NOT NULL, verified_at TEXT, activated_at TEXT, retired_at TEXT
);
CREATE INDEX index_generations_trip ON trip_index_generations(trip_id,state);
CREATE TABLE trip_index_readers (
 id TEXT PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES trip_index_generations(id), process_id INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX index_reader_generation ON trip_index_readers(generation_id);
ALTER TABLE document_generations ADD COLUMN job_id TEXT REFERENCES jobs(id);
UPDATE processing_receipts
 SET status='failed',result_json=json_set(result_json,'$.error_code','RECOVERY_REQUIRED','$.recovery_required',json('true')),
 updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
 WHERE status IN ('queued','running');
"""


def migration_sql():
    # Imported when Database is instantiated, after foundation.db is defined.
    from .budget import BUDGET_SCHEMA
    return SCHEMA + '\n' + BUDGET_SCHEMA
