#!/usr/bin/env python3
"""Read-only accommodation migration inventory: counts only, no private labels or locations."""
import argparse,json,os,sqlite3
from pathlib import Path

def inventory(con,postgres=False):
    tables={r[0] for r in con.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()' if postgres else "SELECT name FROM sqlite_master WHERE type='table'")}
    version=con.execute('SELECT version FROM schema_version WHERE singleton=1' if postgres else 'PRAGMA user_version').fetchone()[0]
    eligible=con.execute("SELECT count(*) FROM trip_stops s JOIN trips t ON t.id=s.trip_id JOIN users u ON u.id=t.owner_id WHERE t.deleted_at IS NULL AND s.base_location IS NOT NULL AND trim(s.base_location)<>''").fetchone()[0]
    already=con.execute('SELECT count(*) FROM trip_accommodations WHERE legacy_stop_id IS NOT NULL').fetchone()[0] if 'trip_accommodations' in tables else 0
    unresolved=con.execute("SELECT count(*) FROM trip_accommodations WHERE deleted_at IS NULL AND identity_state<>'confirmed'").fetchone()[0] if 'trip_accommodations' in tables else 0
    pending=con.execute("SELECT count(*) FROM trip_stops s JOIN trips t ON t.id=s.trip_id JOIN users u ON u.id=t.owner_id WHERE t.deleted_at IS NULL AND s.base_location IS NOT NULL AND trim(s.base_location)<>'' AND NOT EXISTS(SELECT 1 FROM trip_accommodations a WHERE a.legacy_stop_id=s.id)").fetchone()[0] if 'trip_accommodations' in tables else eligible
    unowned=con.execute("SELECT count(*) FROM trip_stops s JOIN trips t ON t.id=s.trip_id LEFT JOIN users u ON u.id=t.owner_id WHERE u.id IS NULL AND s.base_location IS NOT NULL").fetchone()[0]
    return {'mode':'read_only','database_schema':version,'target_schema':12,'eligible_legacy_labels':eligible,'already_migrated':already,'pending_legacy_labels':pending,'unresolved':unresolved,'unresolved_after_label_migration':unresolved+pending,'unowned_not_assigned':unowned,'writes':0,'provider_calls':0,'strategy':'label_only; original stop/booking/manual coordinates untouched; deleted migrated stay never recreated'}
def main():
    p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True);g.add_argument('--database',type=Path);g.add_argument('--postgres-url-env');p.add_argument('--schema',default='travel');a=p.parse_args()
    if a.database:
        con=sqlite3.connect(a.database.resolve().as_uri()+'?mode=ro',uri=True)
        try:r=inventory(con)
        finally:con.close()
    else:
        import psycopg
        from psycopg import sql
        with psycopg.connect(os.environ[a.postgres_url_env],connect_timeout=8) as con:
            con.execute('SET TRANSACTION READ ONLY');con.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(a.schema)));r=inventory(con,True)
    print(json.dumps(r,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
