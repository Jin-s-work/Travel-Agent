"""Migration 3: public-place research remains separate from private trip data."""
SCHEMA = """
CREATE TABLE review_controls (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), research_enabled INTEGER NOT NULL DEFAULT 0,
 production_enabled INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
);
INSERT INTO review_controls(singleton) VALUES(1);
CREATE TABLE provider_policies (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL,
 rights_json TEXT NOT NULL, evidence_json TEXT NOT NULL, purpose TEXT NOT NULL,
 reviewed_at TEXT NOT NULL, expires_at TEXT NOT NULL, aggregate_ttl_seconds INTEGER NOT NULL,
 id_ttl_seconds INTEGER NOT NULL, remote_raw_ttl_seconds INTEGER NOT NULL DEFAULT 0, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(provider,version)
);
CREATE TABLE place_identities (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_place_id TEXT NOT NULL,
 city TEXT NOT NULL, name TEXT NOT NULL, address TEXT NOT NULL, source_url TEXT NOT NULL,
 identity_status TEXT NOT NULL DEFAULT 'needs_confirmation', identity_evidence TEXT,
 version INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, active_aggregate_id TEXT,
 rating REAL, total_rating_count INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(provider,external_place_id)
);
CREATE TABLE trip_places (
 trip_id TEXT NOT NULL REFERENCES trips(id), place_id TEXT NOT NULL REFERENCES place_identities(id),
 linked_at TEXT NOT NULL, PRIMARY KEY(trip_id,place_id)
);
CREATE TABLE review_collection_runs (
 id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL REFERENCES jobs(id), actor_id TEXT NOT NULL REFERENCES users(id),
 place_id TEXT NOT NULL REFERENCES place_identities(id), place_version INTEGER NOT NULL,
 policy_id TEXT NOT NULL REFERENCES provider_policies(id), provider TEXT NOT NULL, adapter_version TEXT NOT NULL,
 request_json TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
 finished_at TEXT, expires_at TEXT NOT NULL, summary_json TEXT, deleted_at TEXT
);
CREATE TABLE review_call_receipts (
 call_id TEXT PRIMARY KEY REFERENCES usage_reservations(call_id), run_id TEXT NOT NULL REFERENCES review_collection_runs(id),
 safe_value_json TEXT NOT NULL, units_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE review_remote_cleanup (
 run_id TEXT PRIMARY KEY REFERENCES review_collection_runs(id), remote_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', deadline_at TEXT NOT NULL, lease_until TEXT,
 dataset_deleted INTEGER NOT NULL DEFAULT 0, run_deleted INTEGER NOT NULL DEFAULT 0,
 error_code TEXT, attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE review_aggregates (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES review_collection_runs(id),
 place_id TEXT NOT NULL REFERENCES place_identities(id), policy_id TEXT NOT NULL REFERENCES provider_policies(id),
 evaluation_json TEXT NOT NULL, coverage_json TEXT NOT NULL, counts_json TEXT NOT NULL,
 detector_version TEXT NOT NULL, config_version TEXT NOT NULL, computed_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, invalidated_at TEXT, invalidation_reason TEXT,
 last_full_reconciliation_at TEXT NOT NULL
);
CREATE TABLE review_quality_evaluations (
 id TEXT PRIMARY KEY, city TEXT NOT NULL, detector_version TEXT NOT NULL, report_json TEXT NOT NULL,
 passed INTEGER NOT NULL, synthetic INTEGER NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
 checked_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE research_tombstones (
 target_type TEXT NOT NULL, target_id TEXT NOT NULL, reason TEXT NOT NULL,
 requested_at TEXT NOT NULL, completed_at TEXT, PRIMARY KEY(target_type,target_id)
);
CREATE TABLE research_audit (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 target_id TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX review_runs_place ON review_collection_runs(place_id,created_at);
CREATE INDEX review_aggregate_expiry ON review_aggregates(expires_at,invalidated_at);
"""
