"""Offline, permission-minimized review deletion history for backup restoration."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from src.foundation.db import Database

FIELDS=('target_type','target_id','reason','requested_at','completed_at')

def merge_tombstones(db,items):
    if len(items)>100000: raise ValueError('Tombstone manifest is too large')
    for row in items:
        if set(row)!=set(FIELDS) or row['target_type'] not in {'place','policy','run','external_link','contract'}:
            raise ValueError('Invalid research tombstone')
        if not isinstance(row['target_id'],str) or len(row['target_id'])>100:
            raise ValueError('Invalid research target')
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        for row in items:
            con.execute('INSERT OR IGNORE INTO research_tombstones VALUES(?,?,?,?,?)',tuple(row[k] for k in FIELDS))
        con.execute("UPDATE provider_policies SET status='revoked' WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='policy')")
        con.execute("UPDATE place_identities SET deleted_at=COALESCE(deleted_at,(SELECT requested_at FROM research_tombstones WHERE target_id=place_identities.id AND target_type='place')),identity_status='blocked' WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='place')")
        con.execute("UPDATE place_external_links SET status='revoked',version=version+1 WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='external_link') AND status!='revoked'")
        con.execute("UPDATE review_provider_contracts SET status='revoked' WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='contract')")
        con.execute("UPDATE review_aggregates SET invalidated_at=COALESCE(invalidated_at,computed_at),invalidation_reason='RESTORE_TOMBSTONE' WHERE run_id IN (SELECT run_id FROM review_run_dependencies WHERE external_link_id IN (SELECT target_id FROM research_tombstones WHERE target_type='external_link') OR contract_id IN (SELECT target_id FROM research_tombstones WHERE target_type='contract'))")
        runs=con.execute("SELECT id,job_id FROM review_collection_runs WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='run') OR policy_id IN (SELECT target_id FROM research_tombstones WHERE target_type='policy') OR place_id IN (SELECT target_id FROM research_tombstones WHERE target_type='place')").fetchall()
        for run in runs:
            con.execute("UPDATE review_collection_runs SET deleted_at=COALESCE(deleted_at,created_at),request_json='{}',summary_json=NULL WHERE id=?",(run['id'],))
            con.execute("UPDATE review_aggregates SET invalidated_at=COALESCE(invalidated_at,computed_at),invalidation_reason='RESTORE_TOMBSTONE',counts_json='{}',coverage_json='{}',evaluation_json='{}' WHERE run_id=?",(run['id'],))
            con.execute('DELETE FROM review_call_receipts WHERE run_id=?',(run['id'],))
            con.execute("UPDATE jobs SET checkpoint_json='{}' WHERE id=?",(run['job_id'],))
    return {'applied':len(items),'scrubbed_runs':len(runs)}

def main():
    parser=argparse.ArgumentParser(description='Stop the server before merging restore history.')
    parser.add_argument('command',choices=['export','merge'])
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    if not args.database.is_file(): parser.error('Existing database required')
    db=Database(args.database)
    if args.command=='export':
        with db.connect() as con: rows=[dict(r) for r in con.execute('SELECT * FROM research_tombstones')]
        with args.manifest.open('x') as output: json.dump({'schema_version':1,'tombstones':rows},output)
        args.manifest.chmod(0o600); print(json.dumps({'exported':len(rows)}))
    else:
        if args.manifest.stat().st_size>16*1024*1024: parser.error('Manifest exceeds limit')
        data=json.loads(args.manifest.read_text())
        if data.get('schema_version')!=1: parser.error('Unknown manifest schema')
        print(json.dumps(merge_tombstones(db,data['tombstones'])))

if __name__=='__main__': main()
