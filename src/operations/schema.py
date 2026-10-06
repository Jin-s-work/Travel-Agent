SCHEMA = """
CREATE TABLE operations_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), instance_id TEXT NOT NULL UNIQUE);
INSERT INTO operations_identity VALUES(1,lower(hex(randomblob(16))));
CREATE TABLE operations_controls (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 mode TEXT NOT NULL DEFAULT 'normal' CHECK(mode IN ('normal','read_only','maintenance')),
 external_enabled INTEGER NOT NULL DEFAULT 1,
 disabled_providers_json TEXT NOT NULL DEFAULT '[]',
 updated_at TEXT NOT NULL DEFAULT '', reason_code TEXT NOT NULL DEFAULT 'INITIAL'
);
INSERT INTO operations_controls(singleton) VALUES(1);
CREATE TABLE operations_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, created_at TEXT NOT NULL,
 details_json TEXT NOT NULL
);
"""
