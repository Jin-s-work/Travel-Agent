"""Local operator commands. Secrets come only from host environment/secret files."""
import argparse
import json
import os
from pathlib import Path
from src.foundation.db import Database
from src.foundation.settings import Settings
from .backup import create_archive,write_checkpoint,restore_archive,key_bytes
from .controls import update,read

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    control=sub.add_parser('control');control.add_argument('--mode',choices=['normal','read_only','maintenance']);control.add_argument('--external',choices=['on','off']);control.add_argument('--disabled-providers',nargs='*');control.add_argument('--reason',default='OPERATOR_CHANGE')
    for command in ('snapshot','checkpoint'):
        arg=sub.add_parser(command);arg.add_argument('--output',type=Path,required=True)
    restore=sub.add_parser('restore');restore.add_argument('--archive',type=Path,required=True);restore.add_argument('--checkpoint',type=Path,required=True);restore.add_argument('--destination',type=Path,required=True);restore.add_argument('--max-checkpoint-age-seconds',type=int,default=600)
    sub.add_parser('remote-list')
    fetch=sub.add_parser('remote-fetch');fetch.add_argument('--object',required=True);fetch.add_argument('--output',type=Path,required=True)
    remote=sub.add_parser('offhost');remote.add_argument('--full',action='store_true')
    release=sub.add_parser('release-restored-reads');release.add_argument('--verified-report',type=Path,required=True)
    sub.add_parser('status')
    args=p.parse_args();settings=Settings()
    if args.command in ('remote-list','remote-fetch'):
        from .remote import ObjectStore
        store=ObjectStore.from_env()
        if args.command=='remote-list':result={'objects':store.recent()}
        else:
            store.download(args.object,args.output);result={'downloaded':True}
    elif args.command=='restore':
        result=restore_archive(args.archive,args.checkpoint,args.destination,key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']),max_checkpoint_age_seconds=args.max_checkpoint_age_seconds)
    else:
        if settings.storage_backend=='supabase' and args.command not in ('control','status','checkpoint'):
            p.error('Use the cloud backup/restore command for PostgreSQL')
        if not settings.database_url and not settings.database_path.is_file():p.error('An existing service database is required')
        db=Database(settings.database_path,url=settings.database_url or None)
        if args.command=='control':result=update(db,mode=args.mode,external_enabled=None if args.external is None else args.external=='on',disabled_providers=args.disabled_providers,reason_code=args.reason)
        elif args.command=='status':
            with db.connect() as con:
                result={'controls':read(con),'backups':[dict(r) for r in con.execute("SELECT action,created_at FROM operations_audit WHERE action IN ('offhost_backup','backup_failed') ORDER BY id DESC LIMIT 5")]}
        elif args.command=='snapshot':result=create_archive(settings,args.output,key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']))
        elif args.command=='checkpoint':result=write_checkpoint(db,args.output,key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']))
        elif args.command=='offhost':
            from types import SimpleNamespace
            from .remote import cycle,ObjectStore
            result=cycle(SimpleNamespace(state=SimpleNamespace(settings=settings,db=db)),ObjectStore.from_env(),key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']),full=args.full,retention=int(os.getenv('BACKUP_RETENTION_DAYS','7')))
        else:
            # Explicit human gate. It enables saved reads only, never paid work.
            report=json.loads(args.verified_report.read_text())
            needed=('integrity','owner_isolation','deletion_scrub','saved_itinerary','search_rebuild_or_degraded')
            if any(report.get(k) is not True for k in needed):p.error('All restore review checks are required')
            with db.connect() as con:
                if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():p.error('Integrity check failed')
            if (settings.database_path.parent/'RESTORE_INCOMPLETE.json').exists():p.error('Restore is incomplete')
            (settings.database_path.parent/'RESTORE_PENDING.json').unlink()
            result=update(db,mode='read_only',external_enabled=False,reason_code='RESTORE_READS_VERIFIED')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
