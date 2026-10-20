"""Public, attributed image metadata; never image bytes or private travel data."""
SCHEMA = """
CREATE TABLE place_photo_cache (
 place_id TEXT PRIMARY KEY REFERENCES place_identities(id),
 identity_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
 checked_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
"""
