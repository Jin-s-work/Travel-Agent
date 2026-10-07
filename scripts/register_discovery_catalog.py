#!/usr/bin/env python3
"""Validate by default; explicitly import reviewed public branch packs as admin.

Dry-run does not open the database. Apply uses STORAGE_BACKEND/DATABASE_URL or
DATABASE_PATH supplied explicitly in the process environment. It never reads a
.env, starts a dispatcher, fetches a URL, sends mail or activates a paid provider.

Examples:
  python scripts/register_discovery_catalog.py --pack data/catalog/madrid.json
  python scripts/register_discovery_catalog.py --pack data/catalog/madrid.json \
    --apply --admin-user-id <existing-admin-id> --approve-reviewed \
    --review-evidence-file /private/path/review-note.txt
"""
from pathlib import Path
import argparse,json,os,sys
os.environ['PYTHON_DOTENV_DISABLED']='1'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.discovery.catalog_tools import inspect_pack,register_reviewed_pack
from src.foundation.repository import DomainError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',type=Path,action='append',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--admin-user-id')
    parser.add_argument('--approve-reviewed',action='store_true')
    parser.add_argument('--review-evidence-file',type=Path)
    args=parser.parse_args()
    if args.approve_reviewed and (not args.apply or not args.review_evidence_file):
        parser.error('--approve-reviewed requires --apply and --review-evidence-file')
    if args.apply and not args.admin_user_id:parser.error('--apply requires --admin-user-id')
    try:
        packs=[]
        for path in args.pack:
            payload=json.loads(path.read_text())
            items=payload.get('packs') if isinstance(payload,dict) and 'packs' in payload else [payload]
            if not isinstance(items,list) or not 1<=len(items)<=100:
                raise DomainError('CATALOG_FORMAT_INVALID','Catalog requires 1 to 100 packs',422)
            packs.extend(inspect_pack(item) for item in items)
        if not args.apply:
            print(json.dumps({'mode':'dry_run','writes':0,'provider_calls':0,'packs':[{'version':p['version'],'city':p['city'],'places':len(p['places'])} for p in packs]},ensure_ascii=False,indent=2))
            return 0
        evidence=args.review_evidence_file.read_text().strip() if args.approve_reviewed else None
        from types import SimpleNamespace
        from src.foundation.settings import Settings
        from src.foundation.db import Database
        from src.foundation.repository import Repository
        from src.reliability.jobs import Jobs
        from src.research.service import ReviewService
        from src.discovery.service import DiscoveryService
        settings=Settings()
        if settings.storage_backend=='supabase' and not settings.database_url:
            raise DomainError('DATABASE_CONFIG_REQUIRED','Supabase DB configuration required',422)
        if settings.storage_backend=='local' and settings.database_url:
            raise DomainError('DATABASE_CONFIG_REQUIRED','DATABASE_URL requires supabase backend',422)
        db=Database(settings.database_path,url=settings.database_url or None)
        try:
            jobs=Jobs(db)
            with db.connect() as con:
                session=con.execute('SELECT s.id FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.id=? AND u.role=\'admin\' AND u.status=\'active\' AND s.epoch=u.session_epoch AND s.expires_at>? ORDER BY s.expires_at DESC,s.id LIMIT 1',(args.admin_user_id,jobs.now())).fetchone()
                if not session:raise DomainError('ADMIN_SESSION_REQUIRED','An active existing admin session is required',403)
            actor=SimpleNamespace(id=args.admin_user_id,session_id=session['id'])
            repo=Repository(db);reviews=ReviewService(db,repo,jobs,None)
            service=DiscoveryService(db,repo,jobs,reviews,allow_synthetic=False)
            receipts=[register_reviewed_pack(service,actor,p,review_evidence=evidence) for p in packs]
            print(json.dumps({'mode':'apply','provider_calls':0,'packs':receipts},ensure_ascii=False,indent=2))
        finally:db.close()
        return 0
    except DomainError as exc:
        print(json.dumps({'error':exc.code,'changed_status':'inspect_existing_pack_before_retry'}),file=sys.stderr)
        return 1
    except Exception:
        # A driver error can contain database credentials; never print it here.
        print(json.dumps({'error':'CATALOG_REGISTRATION_FAILED','details':'Validate input, permissions and database connectivity locally.'}),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
