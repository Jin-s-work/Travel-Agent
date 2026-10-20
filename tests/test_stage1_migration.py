"""Disposable schema12 dry-run, repeat-open and rollback boundary checks."""
from src.foundation.db import Database, SCHEMA_VERSION

NEW_TABLES=('place_photo_cache','review_run_dependencies','place_review_requests','place_external_links','review_provider_contracts','workspace_drafts','itinerary_generation_drafts','maintenance_status','storage_deletion_receipts')


def test_schema12_upgrade_preserves_existing_rows_and_reopen_is_idempotent(tmp_path):
    path=tmp_path/'migration-only.sqlite3';db=Database(path)
    with db.connect() as con:
        con.execute("INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES('fixture','fixture@example.test','test','migration','2026-10-07','2026-10-07')")
        con.execute("INSERT INTO trips(id,owner_id,title,start_date,end_date,created_at,updated_at) VALUES('trip','fixture','Synthetic','2026-11-06','2026-11-09','2026-10-07','2026-10-07')")
        before=[dict(r) for r in con.execute('SELECT * FROM trips')]
        for table in NEW_TABLES:con.execute('DROP TABLE '+table)
        con.execute('UPDATE schema_version SET version=12' if db.backend=='postgres' else 'PRAGMA user_version=12')
    db.close();upgraded=Database(path)
    assert upgraded.schema_version()==SCHEMA_VERSION==15
    with upgraded.connect() as con:
        assert [dict(r) for r in con.execute('SELECT * FROM trips')]==before
        assert [con.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in NEW_TABLES]==[0]*len(NEW_TABLES)
    upgraded.close();again=Database(path)
    assert again.schema_version()==15
    with again.connect() as con:
        assert [dict(r) for r in con.execute('SELECT * FROM trips')]==before
    again.close()
