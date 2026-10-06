SCHEMA = """
CREATE TABLE product_preferences (
 owner_id TEXT PRIMARY KEY REFERENCES users(id), analytics_enabled INTEGER NOT NULL DEFAULT 0,
 version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
ALTER TABLE discovery_events ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE discovery_events ADD COLUMN detail_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE discovery_events ADD COLUMN client_at TEXT;
ALTER TABLE discovery_events ADD COLUMN exclusion_reason TEXT;
CREATE INDEX product_event_period ON discovery_events(created_at,event);
CREATE TABLE product_run_metrics (
 run_id TEXT PRIMARY KEY REFERENCES recommendation_runs(id), owner_id TEXT NOT NULL REFERENCES users(id),
 trip_id TEXT NOT NULL REFERENCES trips(id), summary_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE visit_feedback (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 place_id TEXT NOT NULL REFERENCES place_identities(id), context_key TEXT NOT NULL,
 run_id TEXT REFERENCES recommendation_runs(id), itinerary_id TEXT, item_id TEXT,
 payload_json TEXT NOT NULL, private_note TEXT NOT NULL, version INTEGER NOT NULL,
 idempotency_key TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 withdrawn_at TEXT, UNIQUE(owner_id,trip_id,idempotency_key)
);
CREATE UNIQUE INDEX feedback_current ON visit_feedback(trip_id,place_id,context_key) WHERE withdrawn_at IS NULL;
CREATE TABLE feedback_changes (
 id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES visit_feedback(id), action TEXT NOT NULL,
 version INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE fact_reports (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 place_id TEXT NOT NULL REFERENCES place_identities(id), fact_id TEXT NOT NULL REFERENCES place_facts(id),
 source_id TEXT, category TEXT NOT NULL, observed_date TEXT, description TEXT NOT NULL,
 status TEXT NOT NULL, version INTEGER NOT NULL, resolution_code TEXT, reviewer_id TEXT,
 replacement_fact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, input_hash TEXT NOT NULL, UNIQUE(owner_id,trip_id,idempotency_key)
);
CREATE TABLE fact_report_actions (
 id TEXT PRIMARY KEY, report_id TEXT NOT NULL REFERENCES fact_reports(id), status TEXT NOT NULL,
 reason_code TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE expense_overrides (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 itinerary_id TEXT NOT NULL REFERENCES itineraries(id), item_key TEXT NOT NULL,
 payload_json TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(trip_id,itinerary_id,item_key)
);
"""


def scrub_trip(con, trip):
    con.execute('DELETE FROM feedback_changes WHERE feedback_id IN (SELECT id FROM visit_feedback WHERE trip_id=?)',(trip,))
    con.execute('DELETE FROM fact_report_actions WHERE report_id IN (SELECT id FROM fact_reports WHERE trip_id=?)',(trip,))
    for table in ('visit_feedback','fact_reports','expense_overrides','product_run_metrics','discovery_events'):
        con.execute('DELETE FROM '+table+' WHERE trip_id=?',(trip,))
