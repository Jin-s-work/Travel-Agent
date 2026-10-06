SCHEMA = """
CREATE TABLE reservation_tasks (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1, payload_json TEXT NOT NULL, manifest TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(owner_id,trip_id,idempotency_key)
);
CREATE TABLE reservation_task_events (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES reservation_tasks(id), version INTEGER NOT NULL,
 action TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE offline_source_permissions (
 source_id TEXT PRIMARY KEY REFERENCES evidence_sources(id), source_version INTEGER NOT NULL,
 fields_json TEXT NOT NULL, expires_at TEXT NOT NULL, policy_version TEXT NOT NULL,
 reviewed_by TEXT NOT NULL REFERENCES users(id), checked_at TEXT NOT NULL
);
"""

def scrub_trip(con, trip_id):
    con.execute('DELETE FROM reservation_task_events WHERE task_id IN (SELECT id FROM reservation_tasks WHERE trip_id=?)', (trip_id,))
    con.execute('DELETE FROM reservation_tasks WHERE trip_id=?', (trip_id,))
