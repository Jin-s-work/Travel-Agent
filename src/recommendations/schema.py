SCHEMA = """
CREATE TABLE recommendation_runs (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), trip_version INTEGER NOT NULL,
 conditions_version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, candidates_json TEXT,
 manifest_hash TEXT, result_json TEXT, data_status TEXT NOT NULL DEFAULT 'pending',
 ranker_versions_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX recommendation_trip ON recommendation_runs(trip_id,created_at,id);
CREATE TABLE comparison_sets (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 run_id TEXT NOT NULL REFERENCES recommendation_runs(id), place_ids_json TEXT NOT NULL,
 context_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE discovery_events (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 event TEXT NOT NULL, place_id TEXT, run_id TEXT, created_at TEXT NOT NULL
);
"""


def scrub_trip(con, trip_id):
    con.execute('DELETE FROM comparison_sets WHERE trip_id=?', (trip_id,))
    con.execute('DELETE FROM discovery_events WHERE trip_id=?', (trip_id,))
    con.execute("UPDATE recommendation_runs SET snapshot_json='{}',candidates_json=NULL,result_json=NULL,data_status='deleted' WHERE trip_id=?", (trip_id,))
