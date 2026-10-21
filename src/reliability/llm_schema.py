"""DDL for per-call LLM telemetry (stage 2, v6).

Additive and idempotent: it is applied on every start after the versioned
migrations (SQLite) and with the cloud schema (PostgreSQL), so no schema
version bump, snapshot import change or downgrade-test change is needed.
Plain TEXT/INTEGER columns only (the PostgreSQL path runs it untranslated).
It holds no mail, question or response text, and no FK, so it never orders or
blocks trip deletion; like usage_reservations it keeps trip_id as metadata.
"""

TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
 id TEXT PRIMARY KEY, call_id TEXT, owner_id TEXT NOT NULL, trip_id TEXT, job_id TEXT,
 scope_kind TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, operation TEXT NOT NULL,
 prompt_id TEXT, prompt_version INTEGER, prompt_hash TEXT, latency_ms INTEGER,
 input_tokens INTEGER, output_tokens INTEGER, cost_micros INTEGER, currency TEXT,
 schema_valid INTEGER, repair_attempts INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL,
 replayed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS llm_calls_created ON llm_calls(created_at, operation);
CREATE INDEX IF NOT EXISTS llm_calls_call ON llm_calls(call_id);
"""
