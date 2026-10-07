"""Additive schema 12: private stays and normalized, durable geocoding receipts."""
import json
from datetime import datetime, timezone
from uuid import uuid4

SCHEMA = """
CREATE TABLE trip_accommodations (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
 trip_id TEXT NOT NULL REFERENCES trips(id), stop_id TEXT REFERENCES trip_stops(id) ON DELETE SET NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1), deleted_at TEXT,
 display_name TEXT NOT NULL, input_kind TEXT NOT NULL CHECK(input_kind IN ('name','map_url','booking')),
 input_value TEXT NOT NULL, private_note TEXT NOT NULL DEFAULT '',
 booking_id TEXT REFERENCES bookings(id) ON DELETE SET NULL,
 identity_state TEXT NOT NULL DEFAULT 'unresolved' CHECK(identity_state IN ('unresolved','candidates','confirmed','needs_reconfirmation','unavailable')),
 identity_json TEXT NOT NULL DEFAULT '{}', candidates_json TEXT NOT NULL DEFAULT '[]',
 reason_codes_json TEXT NOT NULL DEFAULT '[]', city TEXT NOT NULL,
 facility_timezone TEXT NOT NULL, checkin_date TEXT, checkout_date TEXT,
 checkin_time TEXT, checkout_time TEXT, dates_confirmed INTEGER NOT NULL DEFAULT 0,
 source_json TEXT NOT NULL DEFAULT '{}', legacy_stop_id TEXT UNIQUE,
 job_id TEXT REFERENCES jobs(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(checkin_date IS NULL OR checkout_date IS NULL OR checkout_date>checkin_date)
);
CREATE INDEX accommodations_owner_trip ON trip_accommodations(owner_id,trip_id,deleted_at);
CREATE INDEX accommodations_stop ON trip_accommodations(trip_id,stop_id,deleted_at,checkin_date,checkout_date);
CREATE TABLE accommodation_resolutions (
 id TEXT PRIMARY KEY, accommodation_id TEXT NOT NULL REFERENCES trip_accommodations(id),
 owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), accommodation_version INTEGER NOT NULL,
 result_json TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT
);
CREATE INDEX accommodation_resolution_owner ON accommodation_resolutions(owner_id,trip_id);
"""


def migrate_legacy(con):
    """Idempotent, label-only migration; a deleted migrated stay is never recreated."""
    stamp=datetime.now(timezone.utc).isoformat()
    rows=con.execute("SELECT s.*,t.owner_id FROM trip_stops s JOIN trips t ON t.id=s.trip_id WHERE t.deleted_at IS NULL AND s.base_location IS NOT NULL AND trim(s.base_location)<>'' AND NOT EXISTS(SELECT 1 FROM trip_accommodations a WHERE a.legacy_stop_id=s.id)").fetchall()
    for row in rows:
        # Stop dates are a planning suggestion, never extracted booking evidence.
        source={'kind':'legacy_stop_label','version':'legacy_label_v1','permission':'private_user_input','retention':'until_user_deletion','dates_status':'suggested','coordinates_provenance':None}
        con.execute('INSERT INTO trip_accommodations(id,owner_id,trip_id,stop_id,display_name,input_kind,input_value,city,facility_timezone,checkin_date,checkout_date,source_json,legacy_stop_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('stay_'+uuid4().hex,row['owner_id'],row['trip_id'],row['id'],row['base_location'][:300],'name',row['base_location'],row['city'],row['timezone'],row['start_date'] if row['end_date']>row['start_date'] else None,row['end_date'] if row['end_date']>row['start_date'] else None,json.dumps(source,ensure_ascii=False),row['id'],stamp,stamp))
    return len(rows)


def scrub_trip(con, trip_id):
    """Erase private payloads after an authorized trip tombstone or restore replay."""
    con.execute('DELETE FROM accommodation_resolutions WHERE trip_id=?',(trip_id,))
    con.execute("UPDATE trip_accommodations SET display_name='',input_value='',private_note='',identity_json='{}',candidates_json='[]',reason_codes_json='[]',source_json='{}',checkin_date=NULL,checkout_date=NULL,checkin_time=NULL,checkout_time=NULL,booking_id=NULL,identity_state='unavailable',version=version+1,deleted_at=COALESCE(deleted_at,?) WHERE trip_id=?",(datetime.now(timezone.utc).isoformat(),trip_id))
