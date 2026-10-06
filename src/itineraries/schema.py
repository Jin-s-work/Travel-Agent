SCHEMA = """
CREATE TABLE itineraries (
 id TEXT PRIMARY KEY, trip_id TEXT NOT NULL REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 0, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
 trip_version INTEGER NOT NULL, conditions_version INTEGER NOT NULL, request_version INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL, input_data_json TEXT, input_manifest TEXT,
 validation_status TEXT NOT NULL DEFAULT 'pending', active_revision_id TEXT,
 undo_stack_json TEXT NOT NULL DEFAULT '[]', created_by TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX itineraries_trip ON itineraries(trip_id,created_at,id);
CREATE TABLE itinerary_revisions (
 id TEXT PRIMARY KEY, itinerary_id TEXT NOT NULL REFERENCES itineraries(id), version INTEGER NOT NULL,
 parent_revision_id TEXT, actor_id TEXT NOT NULL, kind TEXT NOT NULL,
 base_version INTEGER NOT NULL, command_json TEXT NOT NULL, result_json TEXT NOT NULL,
 data_manifest TEXT NOT NULL, input_data_json TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(itinerary_id,version)
);
CREATE TABLE itinerary_previews (
 id TEXT PRIMARY KEY, itinerary_id TEXT NOT NULL REFERENCES itineraries(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 owner_id TEXT NOT NULL, base_version INTEGER NOT NULL, trip_version INTEGER NOT NULL,
 conditions_version INTEGER NOT NULL, kind TEXT NOT NULL, commands_json TEXT NOT NULL,
 result_json TEXT NOT NULL, data_manifest TEXT NOT NULL, input_data_json TEXT NOT NULL,
 undo_steps INTEGER NOT NULL DEFAULT 0, applied_revision_id TEXT,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE INDEX itinerary_previews_expiry ON itinerary_previews(expires_at);
"""


def scrub_trip(con,trip_id):
    con.execute('DELETE FROM itinerary_previews WHERE trip_id=?',(trip_id,))
    con.execute("UPDATE itinerary_revisions SET command_json='[]',result_json='{}',input_data_json='{}' WHERE itinerary_id IN (SELECT id FROM itineraries WHERE trip_id=?)",(trip_id,))
    con.execute("UPDATE itineraries SET snapshot_json='{}',input_data_json=NULL,input_manifest=NULL,undo_stack_json='[]',validation_status='deleted' WHERE trip_id=?",(trip_id,))
