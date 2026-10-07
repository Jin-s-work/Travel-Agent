"""Schema 13: ephemeral workspaces, inactive generation previews, fair maintenance."""
SCHEMA = """
CREATE TABLE workspace_drafts (
 owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 version INTEGER NOT NULL, payload_json TEXT NOT NULL, expires_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(owner_id,trip_id,session_id)
);
CREATE INDEX workspace_drafts_expiry ON workspace_drafts(expires_at);
CREATE TABLE itinerary_generation_drafts (
 itinerary_id TEXT PRIMARY KEY REFERENCES itineraries(id), preview_id TEXT NOT NULL UNIQUE,
 result_json TEXT, expires_at TEXT, applied_revision_id TEXT, created_at TEXT NOT NULL
);
CREATE TABLE maintenance_status (
 name TEXT PRIMARY KEY, owner TEXT, state TEXT NOT NULL, last_started_at TEXT,
 last_succeeded_at TEXT, last_failed_at TEXT, error_code TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE storage_deletion_receipts (
 key TEXT PRIMARY KEY, kind TEXT NOT NULL, requested_at TEXT NOT NULL,
 next_reconcile_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 confirmations INTEGER NOT NULL DEFAULT 0, completed_at TEXT, last_error_code TEXT,
 updated_at TEXT NOT NULL
);
CREATE INDEX storage_deletion_due ON storage_deletion_receipts(next_reconcile_at,completed_at);
"""
