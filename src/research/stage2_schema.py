"""Schema 14: additive canonical review links and auditable runtime contracts.

No existing identity is replaced or auto-matched. Pending links are not usable.
"""
SCHEMA = """
CREATE TABLE place_external_links (
 id TEXT PRIMARY KEY, canonical_place_id TEXT NOT NULL REFERENCES place_identities(id),
 provider TEXT NOT NULL, external_place_id TEXT NOT NULL, status TEXT NOT NULL,
 source_id TEXT NOT NULL REFERENCES evidence_sources(id), checked_at TEXT NOT NULL,
 expires_at TEXT NOT NULL, identity_version INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 evidence_json TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX external_link_active_identity ON place_external_links(provider,external_place_id) WHERE status='approved';
CREATE UNIQUE INDEX external_link_active_canonical ON place_external_links(canonical_place_id,provider) WHERE status='approved';
CREATE INDEX external_links_canonical ON place_external_links(canonical_place_id,status);
CREATE TABLE review_provider_contracts (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, build TEXT NOT NULL, adapter_version TEXT NOT NULL,
 status TEXT NOT NULL, checked_at TEXT NOT NULL, expires_at TEXT NOT NULL, report_sha256 TEXT NOT NULL,
 evidence_json TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(provider,build,adapter_version,report_sha256)
);
CREATE TABLE review_run_dependencies (
 run_id TEXT PRIMARY KEY REFERENCES review_collection_runs(id), external_link_id TEXT REFERENCES place_external_links(id),
 external_link_version INTEGER, contract_id TEXT REFERENCES review_provider_contracts(id),
 provider_build TEXT NOT NULL, language_profile_version TEXT NOT NULL
);
CREATE TABLE place_review_requests (
 id TEXT PRIMARY KEY, canonical_place_id TEXT NOT NULL REFERENCES place_identities(id),
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_pending_review_request ON place_review_requests(canonical_place_id) WHERE status='queued';
"""

TABLES=('review_run_dependencies','place_review_requests','place_external_links','review_provider_contracts')
