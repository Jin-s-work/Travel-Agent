"""Additive migration 11. Old runs, bookings and explicit condition values are untouched."""
SCHEMA = """
CREATE TABLE discovery_contexts (
 trip_id TEXT PRIMARY KEY REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 stop_id TEXT, overrides_json TEXT NOT NULL, basis_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE discovery_intents (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 key_hash TEXT NOT NULL, request_hash TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES jobs(id),
 run_id TEXT NOT NULL REFERENCES recommendation_runs(id), conditions_version INTEGER NOT NULL,
 resolved_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(owner_id,trip_id,key_hash)
);
"""
