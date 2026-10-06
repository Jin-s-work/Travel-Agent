#!/usr/bin/env python3
"""Read-only pre-migration inventory. No credentials, names, dates or mail contents in output."""
import argparse
import json
import os
from pathlib import Path
import sqlite3


def inventory(con, postgres=False):
    if postgres:
        version=con.execute('SELECT version FROM schema_version WHERE singleton=1').fetchone()[0]
        tables={r[0] for r in con.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()')}
    else:
        version=con.execute('PRAGMA user_version').fetchone()[0]
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts={table:con.execute('SELECT count(*) FROM '+table).fetchone()[0] if table in tables else 0 for table in ('trips','trip_stops','discovery_conditions','discovery_contexts','discovery_intents','recommendation_runs','itineraries')}
    missing=con.execute('SELECT count(*) FROM trips t LEFT JOIN users u ON u.id=t.owner_id WHERE u.id IS NULL').fetchone()[0]
    return {'mode':'read_only','database_schema':version,'target_schema':11,'counts':counts,'unassigned_trips':missing,
        'strategy':'add_two_empty_tables; legacy explicit JSON remains explicit on read; preserve all stop IDs and past snapshots',
        'writes':0,'paid_calls':0,'ready_for_migration':version in (10,11) and missing==0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--database',type=Path)
    source.add_argument('--postgres-url-env',help='Environment variable containing the server connection URL; value is never printed')
    parser.add_argument('--schema',default='travel')
    args=parser.parse_args()
    if args.database:
        con=sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True)
        try: result=inventory(con)
        finally:con.close()
    else:
        import psycopg
        from psycopg import sql
        with psycopg.connect(os.environ[args.postgres_url_env],connect_timeout=8) as con:
            con.execute('SET TRANSACTION READ ONLY')
            con.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(args.schema)))
            result=inventory(con,True)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
