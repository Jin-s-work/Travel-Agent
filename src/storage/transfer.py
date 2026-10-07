"""Explicit owner-preserving import and encrypted portable cloud backups.

Run on the operator's computer, with server credentials in environment. No
automatic database replacement, ownership guessing, or paid API calls.
"""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from uuid import uuid4
import psycopg
from psycopg import sql
from src.foundation.db import Database, SCHEMA_VERSION
from src.foundation.repository import DomainError, utcnow
from src.operations.backup import create_archive, restore_archive, write_checkpoint, key_bytes, _sanitize_deleted
from src.storage.objects import SupabaseObjects


def tables(con):
    pending=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
    result=[]
    while pending:
        ready=[name for name in pending if {r[2] for r in con.execute('PRAGMA foreign_key_list('+name+')')} <= set(result)]
        if not ready: raise ValueError('Cyclic schema dependencies require a new transfer version')
        result.extend(ready);pending=[name for name in pending if name not in ready]
    return result


def inspect_source(source, documents_root):
    source=Path(source).resolve(strict=True);root=Path(documents_root).resolve(strict=True)
    files=[]
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Source integrity failed')
        version=con.execute('PRAGMA user_version').fetchone()[0]
        if version not in (7,8,9,12,13,SCHEMA_VERSION):raise ValueError(f'Unsupported source schema; make a validated version 7, 8, 9, 12, 13 or {SCHEMA_VERSION} snapshot first')
        for row in con.execute('SELECT d.* FROM source_documents d JOIN trips t ON t.id=d.trip_id WHERE d.deleted_at IS NULL AND t.deleted_at IS NULL'):
            path=Path(row['opaque_path'])
            if not path.is_absolute() or not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file():raise ValueError('Missing or unsafe owned original')
            data=path.read_bytes()
            if len(data)>1024*1024 or hashlib.sha256(data).hexdigest()!=row['content_hash']:raise ValueError('Source hash/size mismatch')
            files.append({'id':row['id'],'trip_id':row['trip_id'],'path':path,'hash':row['content_hash'],'size':len(data)})
        counts={table:con.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('users','trips','bookings','itineraries','jobs')}
    return {'source_schema':version,'counts':counts,'owned_documents':len(files),'original_bytes':sum(f['size'] for f in files),'unowned_files_imported':0},files


def copy_sqlite(source,target):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(target)) as dst:src.backup(dst)


def import_sqlite(source,documents_root,db,objects,*,apply=False):
    report,files=inspect_source(source,documents_root)
    if not apply:return {**report,'state':'dry_run','writes':0}
    objects.verify_private()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT count(*) FROM users').fetchone()[0]:raise ValueError('Import requires an empty target schema; existing data is never overwritten')
        con.execute("UPDATE operations_controls SET mode='maintenance',external_enabled=0,reason_code='IMPORT_INCOMPLETE'")
    # Target stays closed if any upload or SQL step fails. Uploaded server keys
    # are deterministic within this run and no source file is ever deleted.
    with tempfile.TemporaryDirectory(prefix='travel-import-') as temp:
        clone=Path(temp)/'source.sqlite3';copy_sqlite(source,clone);local=Database(clone)
        # Source deletion/withdrawal markers remain authoritative.
        _sanitize_deleted(local,Path(temp)/'discarded-documents')
        uploads=[]
        for file in files:
            key=file['trip_id']+'/'+uuid4().hex+file['path'].suffix.lower()
            with db.connect() as con:
                con.execute('INSERT INTO cloud_import_objects(key,sha256,byte_size) VALUES(?,?,?)',(key,file['hash'],file['size']))
            objects.request('POST',objects.url(key),content=file['path'].read_bytes(),headers={'Content-Type':'application/octet-stream','x-upsert':'false'})
            uploads.append((key,file))
            try:
                with db.connect() as con:
                    con.execute('BEGIN IMMEDIATE')
                    objects.check_registration(con,key)
            except DomainError:
                objects.reject_late_upload(key,kind='import')
                raise
            with local.connect() as con:con.execute('UPDATE source_documents SET opaque_path=? WHERE id=?',('supabase:'+key,file['id']))
        with local.connect() as source_con, db.connect() as target:
            target.execute('BEGIN IMMEDIATE')
            if target.execute('SELECT count(*) FROM users').fetchone()[0]:raise ValueError('Target changed during import')
            # The same writer transaction excludes reconciliation between this
            # final fence and publishing source_document/cloud_object references.
            for key,_ in uploads:objects.check_registration(target,key)
            for table in ('review_controls','operations_identity','operations_controls'):target.execute('DELETE FROM '+table)
            for table in tables(source_con):
                # These describe the target's own leases and object keys. Never
                # overwrite its tombstones with metadata from the local source.
                if table in {'maintenance_status','storage_deletion_receipts'}:continue
                columns=[r[1] for r in source_con.execute('PRAGMA table_info('+table+')')]
                statement='INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')'
                for row in source_con.execute('SELECT * FROM '+table):target.execute(statement,tuple(row))
            for key,file in uploads:
                target.execute("INSERT INTO cloud_objects(key,trip_id,sha256,byte_size,state) VALUES(?,?,?,?,'active')",(key,file['trip_id'],file['hash'],file['size']))
            for key,_ in uploads:target.execute('DELETE FROM cloud_import_objects WHERE key=?',(key,))
            target.execute('DELETE FROM sessions');target.execute('UPDATE users SET session_epoch=session_epoch+1')
            target.execute('UPDATE invitations SET revoked_at=COALESCE(revoked_at,?) WHERE used_at IS NULL',(utcnow(),))
            target.execute("UPDATE jobs SET state='cancelled',error_code='IMPORT_REVIEW_REQUIRED',fencing_token=fencing_token+1,lease_owner=NULL,lease_expires_at=NULL WHERE state IN ('queued','running')")
            target.execute("UPDATE usage_reservations SET state='unknown',error_code='IMPORT_RECONCILIATION',result_ref=NULL WHERE state IN ('sent','reserved')")
            target.execute('DELETE FROM dispatcher_leases');target.execute('DELETE FROM trip_index_writers');target.execute('DELETE FROM trip_index_readers')
            target.execute('UPDATE trips SET active_index_id=NULL')
            target.execute("UPDATE trip_index_generations SET state='deleted',source_manifest='{}'")
            target.execute("UPDATE operations_controls SET mode='read_only',external_enabled=0,reason_code='IMPORT_REVIEW_REQUIRED'")
            target.execute("UPDATE cost_controls SET halted=1,reason_code='IMPORT_RECONCILIATION'")
            target.execute("INSERT OR IGNORE INTO cost_controls VALUES('USD',1,'IMPORT_RECONCILIATION',?)",(utcnow(),))
            target.execute('DELETE FROM review_call_receipts')
            target.execute('UPDATE review_controls SET research_enabled=0,production_enabled=0')
            target.execute("SELECT setval(pg_get_serial_sequence('operations_audit','id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM operations_audit")
    return {**report,'state':'imported_read_only','sessions_revoked':True,'paid_calls':0,'search':'rebuild_required'}


def cloud_snapshot(settings,db,objects,target,key):
    objects.verify_private()
    with tempfile.TemporaryDirectory(prefix='travel-cloud-backup-') as temp:
        root=Path(temp).resolve();local=Database(root/'service.sqlite3')
        # MVCC consistent database snapshot; no global writer lock/network upload.
        with psycopg.connect(db._url,autocommit=True) as source,source.transaction(),local.connect() as dest:
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            source.execute(sql.SQL('SET LOCAL search_path TO {},extensions,pg_catalog').format(sql.Identifier(db.schema)))
            dest.execute('BEGIN IMMEDIATE')
            for table in ('review_controls','operations_identity','operations_controls'):dest.execute('DELETE FROM '+table)
            for table in tables(dest):
                columns=[r[1] for r in dest.execute('PRAGMA table_info('+table+')')]
                with source.cursor(name='backup_'+table) as cursor:
                    cursor.execute(sql.SQL('SELECT {} FROM {}').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.Identifier(table)))
                    while batch:=cursor.fetchmany(100):
                        dest.executemany('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',batch)
        docs=root/'documents';docs.mkdir()
        with local.connect() as con:
            for row in con.execute('SELECT d.* FROM source_documents d JOIN trips t ON t.id=d.trip_id WHERE d.deleted_at IS NULL AND t.deleted_at IS NULL').fetchall():
                data=objects.read(dict(row));obj=objects.validate_key(row['opaque_path'][9:]);path=docs/obj
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(0o600)
                con.execute('UPDATE source_documents SET opaque_path=? WHERE id=?',(str(path),row['id']))
        local_settings=replace(settings,storage_backend='local',database_url='',database_path=local.path,documents_dir=docs,vectors_dir=root/'vectors')
        return create_archive(local_settings,target,key)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import-sqlite');imp.add_argument('--source',type=Path,required=True);imp.add_argument('--documents-root',type=Path,required=True);imp.add_argument('--apply',action='store_true');imp.add_argument('--offline',action='store_true')
    for command in ('snapshot','checkpoint'):
        p=sub.add_parser(command);p.add_argument('--output',type=Path,required=True)
    rest=sub.add_parser('restore-local');rest.add_argument('--archive',type=Path,required=True);rest.add_argument('--checkpoint',type=Path,required=True);rest.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='import-sqlite' and not args.apply:
        report,_=inspect_source(args.source,args.documents_root);print(json.dumps({**report,'state':'dry_run','writes':0}));return
    if args.command=='import-sqlite' and not args.offline:parser.error('--apply requires stopped source writers and --offline')
    if args.command=='restore-local':
        result=restore_archive(args.archive,args.checkpoint,args.destination,key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']))
    else:
        from src.foundation.settings import Settings
        settings=Settings()
        if settings.storage_backend!='supabase' or not settings.database_url:parser.error('Explicit Supabase settings required')
        db=Database(settings.database_path,url=settings.database_url)
        objects=SupabaseObjects(settings,db)
        try:
            if args.command=='import-sqlite':result=import_sqlite(args.source,args.documents_root,db,objects,apply=True)
            elif args.command=='snapshot':result=cloud_snapshot(settings,db,objects,args.output,key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']))
            else:result=write_checkpoint(db,args.output,key_bytes(os.environ['BACKUP_ENCRYPTION_KEY']))
        finally:objects.close();db.close()
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
