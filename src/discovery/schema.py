"""Migration 4, discovery foundation. Public evidence and private notes are separate."""
SCHEMA = """
CREATE TABLE discovery_conditions (
 trip_id TEXT PRIMARY KEY REFERENCES trips(id), owner_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL, trip_version INTEGER NOT NULL, snapshot_json TEXT NOT NULL,
 conditions_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE candidate_packs (
 id TEXT PRIMARY KEY, version TEXT NOT NULL, city TEXT NOT NULL, synthetic INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'needs_review', actor_id TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(version,city)
);
CREATE TABLE research_candidates (
 id TEXT PRIMARY KEY, pack_id TEXT NOT NULL REFERENCES candidate_packs(id),
 place_id TEXT NOT NULL REFERENCES place_identities(id), status TEXT NOT NULL DEFAULT 'needs_review',
 category TEXT NOT NULL, recommendation_types_json TEXT NOT NULL, tags_json TEXT NOT NULL,
 native_name TEXT, canonical_url TEXT NOT NULL, chain_id TEXT, neighborhood TEXT,
 latitude REAL, longitude REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(pack_id,place_id)
);
CREATE TABLE evidence_sources (
 id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES place_identities(id),
 source_key TEXT NOT NULL, url TEXT NOT NULL, source_type TEXT NOT NULL, source_group TEXT NOT NULL,
 checked_at TEXT NOT NULL, published_at TEXT, read_confirmed INTEGER NOT NULL,
 display_permitted INTEGER NOT NULL, status TEXT NOT NULL, evidence_note TEXT NOT NULL,
 policy_version TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(place_id,source_key)
);
CREATE TABLE place_facts (
 id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES place_identities(id),
 field TEXT NOT NULL, value_json TEXT, status TEXT NOT NULL,
 source_id TEXT REFERENCES evidence_sources(id), checked_at TEXT NOT NULL,
 valid_for_date TEXT, valid_from TEXT, valid_until TEXT, expires_at TEXT NOT NULL,
 policy_version TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE INDEX discovery_facts_place ON place_facts(place_id,field,expires_at);
CREATE TABLE bookmarks (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 input_kind TEXT NOT NULL, input_value TEXT NOT NULL, normalized_input TEXT NOT NULL, note TEXT NOT NULL,
 resolve_state TEXT NOT NULL DEFAULT 'unresolved', matched_place_id TEXT REFERENCES place_identities(id),
 candidates_json TEXT NOT NULL DEFAULT '[]', reason_codes_json TEXT NOT NULL DEFAULT '[]',
 job_id TEXT REFERENCES jobs(id), version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE UNIQUE INDEX bookmarks_input ON bookmarks(trip_id,input_kind,normalized_input) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX bookmarks_place ON bookmarks(trip_id,matched_place_id) WHERE deleted_at IS NULL AND matched_place_id IS NOT NULL;
CREATE TABLE discovery_exclusions (
 owner_id TEXT NOT NULL REFERENCES users(id), trip_id TEXT NOT NULL REFERENCES trips(id),
 place_id TEXT NOT NULL REFERENCES place_identities(id), created_at TEXT NOT NULL,
 PRIMARY KEY(trip_id,place_id)
);
CREATE TABLE discovery_tombstones (
 kind TEXT NOT NULL, target_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(kind,target_id)
);
CREATE TABLE discovery_audit (
 id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 target_id TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def scrub_trip(con,trip_id):
    from src.accommodations.schema import scrub_trip as scrub_stays
    scrub_stays(con,trip_id)
    con.execute('DELETE FROM discovery_intents WHERE trip_id=?',(trip_id,))
    con.execute('DELETE FROM discovery_contexts WHERE trip_id=?',(trip_id,))
    con.execute('DELETE FROM discovery_conditions WHERE trip_id=?',(trip_id,))
    con.execute('DELETE FROM discovery_exclusions WHERE trip_id=?',(trip_id,))
    con.execute('DELETE FROM bookmarks WHERE trip_id=?',(trip_id,))
