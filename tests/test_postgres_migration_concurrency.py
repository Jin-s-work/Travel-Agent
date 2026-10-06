"""Startup overlap on disposable loopback Postgres; never production credentials."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from src.foundation.db import SCHEMA_VERSION
from src.storage.postgres import PostgresDatabase, WRITE_LOCK, MIGRATION_LOCK

DSN=os.getenv('TRAVEL_TEST_POSTGRES_DSN','')
pytestmark=pytest.mark.skipif(not DSN,reason='Requires disposable loopback PostgreSQL')


@pytest.fixture
def database(tmp_path):
    assert urlsplit(DSN).hostname in {'127.0.0.1','localhost'},'Never run against a hosted/private service'
    schema='travel_migration_'+uuid4().hex
    with psycopg.connect(DSN,autocommit=True) as admin:
        for role in ('anon','authenticated'):
            if not admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    instances=[]
    def open_database():
        db=PostgresDatabase(DSN,tmp_path/'runtime-cache',schema=schema)
        instances.append(db)
        return db
    db=open_database()
    with db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    ('synthetic-owner','synthetic@example.test','test','subject','2026-10-07','2026-10-07'))
    yield db,open_database
    for item in instances:item.close()
    with psycopg.connect(DSN,autocommit=True) as admin:
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def waiting_startup(admin):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        row=admin.execute("SELECT pid FROM pg_locks WHERE locktype='advisory' AND objid=%s AND NOT granted",(WRITE_LOCK,)).fetchone()
        if row:return row[0]
        time.sleep(.02)
    raise AssertionError('Startup must wait on the application write fence before any DDL')


def test_overlapping_startup_waits_before_table_locks_then_preserves_writer(database):
    db,reopen=database
    with ThreadPoolExecutor(max_workers=1) as executor:
        # This is an actual application transaction, including its advisory fence
        # and a held row/table lock, not a mock migration call.
        with db.connect() as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute('UPDATE users SET display_name=? WHERE id=?',('committed-before-startup','synthetic-owner'))
            future=executor.submit(reopen)
            with psycopg.connect(DSN,autocommit=True) as observer:
                migration_pid=waiting_startup(observer)
                assert not future.done()
                assert observer.execute(
                    "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid=l.relation "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE l.pid=%s AND n.nspname=%s",
                    (migration_pid,db.schema)).fetchone()[0]==0
                # The order is WRITE then MIGRATION, never the reverse.
                assert observer.execute(
                    "SELECT count(*) FROM pg_locks WHERE pid=%s AND locktype='advisory' AND objid=%s AND granted",
                    (migration_pid,MIGRATION_LOCK)).fetchone()[0]==0
            # A second table remains usable by the existing transaction while the
            # replacement process waits; it cannot close a table-lock cycle.
            assert writer.execute('SELECT version FROM schema_version').fetchone()[0]==SCHEMA_VERSION
        new_db=future.result(timeout=6)
    with new_db.connect() as con:
        assert con.execute('SELECT display_name FROM users WHERE id=?',('synthetic-owner',)).fetchone()[0]=='committed-before-startup'
        assert con.execute('SELECT version FROM schema_version').fetchone()[0]==SCHEMA_VERSION


def test_current_rls_restart_finishes_while_existing_reader_keeps_table_locks(database):
    db,reopen=database
    with ThreadPoolExecutor(max_workers=1) as executor:
        with psycopg.connect(DSN) as reader:
            reader.execute(sql.SQL('SET search_path TO {},extensions,pg_catalog').format(sql.Identifier(db.schema)))
            names=reader.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename',(db.schema,)).fetchall()
            # Hold AccessShare on every private table until startup has completed.
            # An unconditional RLS ALTER would need AccessExclusive and time out.
            identifiers=sql.SQL(',').join(sql.Identifier(db.schema,name[0]) for name in names)
            reader.execute(sql.SQL('LOCK TABLE {} IN ACCESS SHARE MODE').format(identifiers))
            reader.execute('SELECT count(*) FROM users')
            future=executor.submit(reopen)
            try:
                restarted=future.result(timeout=4)
                assert restarted.schema_version()==SCHEMA_VERSION
            finally:reader.rollback()


def test_new_and_unprotected_tables_get_rls_and_browser_privileges_removed(database):
    db,reopen=database
    with psycopg.connect(DSN,autocommit=True) as admin:
        rows=admin.execute('SELECT rowsecurity FROM pg_tables WHERE schemaname=%s',(db.schema,)).fetchall()
        assert rows and all(row[0] for row in rows)
        admin.execute(sql.SQL('CREATE TABLE {}.new_private_table(id INTEGER PRIMARY KEY,content TEXT)').format(sql.Identifier(db.schema)))
        admin.execute(sql.SQL('ALTER TABLE {}.users DISABLE ROW LEVEL SECURITY').format(sql.Identifier(db.schema)))
        for role in ('anon','authenticated'):
            admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(db.schema),sql.Identifier(role)))
            admin.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}').format(sql.Identifier(db.schema),sql.Identifier(role)))
    restarted=reopen()
    with restarted.connect() as con:
        rows=con.execute('SELECT tablename,rowsecurity FROM pg_tables WHERE schemaname=?',(db.schema,)).fetchall()
        assert rows and all(row[1] for row in rows)
        assert 'new_private_table' in {row[0] for row in rows}
        for role in ('anon','authenticated'):
            assert con.execute("SELECT has_schema_privilege(?,?, 'USAGE')",(role,db.schema)).fetchone()[0] is False
            assert con.execute("SELECT has_table_privilege(?,?, 'SELECT')",(role,db.schema+'.new_private_table')).fetchone()[0] is False
        assert con.execute('SELECT version FROM schema_version').fetchone()[0]==SCHEMA_VERSION
