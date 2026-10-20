from src.foundation.db import Database,SCHEMA_VERSION
from src.research.stage2_schema import TABLES as STAGE2_TABLES
TABLES=('place_photo_cache',*STAGE2_TABLES)


def test_schema13_additive_migration_retains_ids_and_is_repeatable(tmp_path):
    path=tmp_path/'stage2.sqlite3';db=Database(path)
    with db.connect() as con:
        con.execute("INSERT INTO place_identities(id,provider,external_place_id,city,name,address,source_url,created_at,updated_at) VALUES('canonical','manual_official','official-ref','tokyo','Same branch','Synthetic address','https://example.org/official','2026-10-07','2026-10-07')")
        before=[dict(r) for r in con.execute('SELECT * FROM place_identities')]
        for table in TABLES:con.execute('DROP TABLE '+table)
        con.execute('UPDATE schema_version SET version=13' if db.backend=='postgres' else 'PRAGMA user_version=13')
    db.close();db=Database(path)
    assert db.schema_version()==SCHEMA_VERSION==15
    with db.connect() as con:
        assert [dict(r) for r in con.execute('SELECT * FROM place_identities')]==before
        assert all(con.execute('SELECT count(*) FROM '+table).fetchone()[0]==0 for table in TABLES)
    db.close();db=Database(path)
    assert db.schema_version()==15;db.close()


def test_read_only_migration_rehearsal_does_not_upgrade_source(tmp_path):
    from src.research.migration import dry_run
    import sqlite3
    path=tmp_path/'source.sqlite3';db=Database(path)
    if db.backend=='postgres':
        import pytest;pytest.skip('SQLite source rehearsal; PostgreSQL migration tested independently')
    with db.connect() as con:
        for table in TABLES:con.execute('DROP TABLE '+table)
        con.execute('PRAGMA user_version=13')
    db.close();report=dry_run(path)
    assert report['source_schema']==13 and report['target_schema']==15
    assert report['source_writes']==report['identities_replaced']==report['automatic_approvals']==0
    with sqlite3.connect(path) as con:assert con.execute('PRAGMA user_version').fetchone()[0]==13
