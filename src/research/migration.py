"""Read-only schema14 rehearsal on a private SQLite backup copy; no identity merge."""
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
from .contracts import norm
from src.foundation.db import Database


def dry_run(source):
    source=Path(source).resolve(strict=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original:
        old_version=original.execute('PRAGMA user_version').fetchone()[0]
        before=original.execute('SELECT count(*) FROM place_identities').fetchone()[0]
        with tempfile.TemporaryDirectory(prefix='going-schema14-dry-run-') as temp:
            path=Path(temp)/'copy.sqlite3'
            with sqlite3.connect(path) as cloned:original.backup(cloned)
            db=Database(path)
            with db.connect() as con:
                records=[dict(r) for r in con.execute('SELECT id,provider,external_place_id,name,address,city FROM place_identities WHERE deleted_at IS NULL')]
                collisions=[]
                for i,a in enumerate(records):
                    for b in records[i+1:]:
                        if a['city']==b['city'] and a['provider']!=b['provider'] and (a['external_place_id']==b['external_place_id'] or norm(a['name'])==norm(b['name'])):
                            collisions.append({'canonical_candidates':[a['id'],b['id']],'same_external_id':a['external_place_id']==b['external_place_id'],
                                'address_matches':norm(a['address'])==norm(b['address']),'action':'manual_branch_review_required'})
                after=con.execute('SELECT count(*) FROM place_identities').fetchone()[0]
                integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
                report={'state':'dry_run','source_schema':old_version,'target_schema':db.schema_version(),'source_writes':0,
                    'identities_before':before,'identities_after':after,'identities_replaced':0,'integrity':integrity,
                    'ambiguous_mapping_candidates':collisions,'automatic_approvals':0}
            db.close()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();result=dry_run(args.database)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');args.report.chmod(0o600)
    print(json.dumps({'state':result['state'],'source_writes':0,'identity_conflicts':len(result['ambiguous_mapping_candidates'])}))
if __name__=='__main__':main()
