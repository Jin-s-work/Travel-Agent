"""Consistent online SQL/original snapshots, authenticated encrypted archives.

Vectors are derived and deliberately rebuilt; in-flight provider results are not
replayed after disaster recovery. All plaintext lives in a private temp directory.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from src.foundation.cli import private_json, file_hash, verify_backup, restore as legacy_restore
from src.foundation.db import Database, SCHEMA_VERSION
from src.foundation.repository import utcnow

MAGIC=b'TRAVEL-BACKUP-1\n'
MAX_BYTES=2*1024**3
CHECKPOINT_TABLES=('users','deletion_tombstones','research_tombstones','discovery_tombstones','usage_reservations','usage_ledger')


def key_bytes(value):
    try:key=base64.b64decode(value,validate=True)
    except Exception:raise ValueError('BACKUP_ENCRYPTION_KEY must be base64') from None
    if len(key)!=32:raise ValueError('BACKUP_ENCRYPTION_KEY must encode 32 bytes')
    return key


def encrypt(source,target,key):
    nonce=os.urandom(12);header=MAGIC+nonce
    cipher=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor();cipher.authenticate_additional_data(header)
    with open(source,'rb') as src,open(target,'xb') as dst:
        os.chmod(target,0o600);dst.write(header)
        for chunk in iter(lambda:src.read(65536),b''):dst.write(cipher.update(chunk))
        dst.write(cipher.finalize());dst.write(cipher.tag);dst.flush();os.fsync(dst.fileno())


def decrypt(source,target,key):
    size=Path(source).stat().st_size;prefix=len(MAGIC)+12
    if not prefix+16<=size<=MAX_BYTES:raise ValueError('Invalid encrypted archive size')
    created=False
    try:
        with open(source,'rb') as src,open(target,'xb') as dst:
            created=True;os.chmod(target,0o600);header=src.read(prefix)
            if not header.startswith(MAGIC):raise ValueError('Unsupported archive format')
            src.seek(-16,2);tag=src.read(16);src.seek(prefix)
            cipher=Cipher(algorithms.AES(key),modes.GCM(header[len(MAGIC):],tag)).decryptor();cipher.authenticate_additional_data(header)
            remaining=size-prefix-16
            while remaining:
                chunk=src.read(min(65536,remaining));remaining-=len(chunk);dst.write(cipher.update(chunk))
            dst.write(cipher.finalize())
    except Exception:
        if created:Path(target).unlink(missing_ok=True)
        raise ValueError('Archive authentication failed') from None


def rows(con,table):return [dict(r) for r in con.execute('SELECT * FROM '+table)]


def checkpoint(db):
    with db.connect() as con:
        con.execute('BEGIN')
        value={'format':1,'created_at':utcnow(),'instance_id':con.execute('SELECT instance_id FROM operations_identity').fetchone()[0],
               **{table:rows(con,table) for table in CHECKPOINT_TABLES},
               'trips':[dict(r) for r in con.execute('SELECT id,owner_id FROM trips')]}
    return value


def write_checkpoint(db,destination,key):
    with tempfile.TemporaryDirectory(prefix='travel-checkpoint-') as temp:
        plain=Path(temp)/'checkpoint.json';private_json(plain,checkpoint(db));encrypt(plain,destination,key)
    return {'sha256':file_hash(destination),'bytes':Path(destination).stat().st_size}


def _copy_file(source,target,root):
    source=Path(source);root=Path(root).resolve()
    if not source.is_file() or not source.resolve().is_relative_to(root) or any(p.is_symlink() for p in [source,*source.parents]):
        raise ValueError('Original path is missing or unsafe')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(source,target);target.chmod(0o600)


def snapshot(settings,destination):
    if settings.storage_backend!='local':
        raise ValueError('Use the PostgreSQL cloud backup command for this backend')
    started=time.monotonic();db=Database(settings.database_path)
    destination=Path(destination);destination.mkdir(mode=0o700)
    for folder in ('documents','vectors','private-job-artifacts'):(destination/folder).mkdir(mode=0o700)
    # Writers persist originals/reference atomically under BEGIN IMMEDIATE.
    # WAL still permits readers. Chroma is not copied during concurrent writes.
    with db.connect() as barrier:
        barrier.execute('BEGIN IMMEDIATE')
        with closing(sqlite3.connect(settings.database_path.resolve().as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(destination/'database.sqlite3')) as target:
            source.backup(target)
        docs=barrier.execute('SELECT d.* FROM source_documents d JOIN trips t ON t.id=d.trip_id WHERE d.deleted_at IS NULL AND t.deleted_at IS NULL').fetchall()
        size=0
        for doc in docs:
            path=Path(doc['opaque_path']);size+=path.stat().st_size
            if size>MAX_BYTES//2:raise ValueError('Backup original size exceeds configured beta capacity')
            _copy_file(path,destination/'documents'/path.relative_to(settings.documents_dir.resolve()),settings.documents_dir)
        generations=[dict(r) for r in barrier.execute("SELECT g.id,g.trip_id,g.source_manifest,g.embedding_model,g.embedding_dimension FROM trip_index_generations g JOIN trips t ON t.active_index_id=g.id WHERE t.deleted_at IS NULL")]
        identity=barrier.execute('SELECT instance_id FROM operations_identity').fetchone()[0]
        cutoff=utcnow()
    # Do not distribute stored review samples or obsolete provider receipts.
    # Their policy must be revalidated after restore; budget receipts remain SQL.
    clean=Database(destination/'database.sqlite3')
    _sanitize_deleted(clean,destination/'documents')
    with clean.connect() as con:
        con.execute('DELETE FROM review_call_receipts')
        con.execute("UPDATE jobs SET checkpoint_json='{}' WHERE operation='review_collection'")
        con.execute("UPDATE review_collection_runs SET request_json='{}',summary_json=NULL")
        con.execute("UPDATE review_aggregates SET counts_json='{}',coverage_json='{}',evaluation_json='{}',invalidated_at=?,invalidation_reason='BACKUP_REVIEW_REQUIRED'",(utcnow(),))
        con.execute('UPDATE review_controls SET research_enabled=0,production_enabled=0,version=version+1')
    with clean.connect() as con:con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    for suffix in ('-wal','-shm'):(destination/('database.sqlite3'+suffix)).unlink(missing_ok=True)
    (destination/'.migration.lock').unlink(missing_ok=True)
    files=[{'path':str(p.relative_to(destination)),'bytes':p.stat().st_size,'sha256':file_hash(p)} for p in sorted(destination.rglob('*')) if p.is_file()]
    manifest={'schema_version':1,'database_schema':SCHEMA_VERSION,'created_at':cutoff,'completed_at':utcnow(),'state':'complete','requires_offline':False,
              'instance_id':identity,'app_revision':os.getenv('RENDER_GIT_COMMIT',os.getenv('APP_REVISION','working-tree')),
              'pricing_config_sha256':file_hash(Path(settings.pricing_config)) if settings.pricing_config and Path(settings.pricing_config).is_file() else None,
              'original_documents_dir':str(settings.documents_dir.resolve()),'original_vectors_dir':str(settings.vectors_dir.resolve()),
              'active_generations':generations,'vectors':'rebuild_required','provider_artifacts':'excluded_replay_disabled','files':files}
    private_json(destination/'manifest.json',manifest);verify_backup(destination)
    return {'snapshot_seconds':round(time.monotonic()-started,3),'files':len(files),'raw_bytes':size,'created_at':cutoff}


def _sanitize_deleted(db,documents):
    """Erase private payloads even when deletion cleanup had not run at snapshot."""
    from src.discovery.maintenance import merge_tombstones
    merge_tombstones(db,[])
    with db.connect() as con:
        from src.product.events import purge
        purge(con)
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        dead=[r[0] for r in con.execute('SELECT id FROM trips WHERE deleted_at IS NOT NULL')]
        for trip in dead:
            from src.travel_tools.schema import scrub_trip as scrub_tools
            scrub_tools(con,trip)
            from src.product.schema import scrub_trip as scrub_product
            scrub_product(con,trip)
            shutil.rmtree(Path(documents)/trip,ignore_errors=True)
            con.execute("UPDATE source_documents SET deleted_at=COALESCE(deleted_at,?),opaque_path='',display_filename='',content_hash='' WHERE trip_id=?",(utcnow(),trip))
            con.execute("UPDATE bookings SET deleted_at=COALESCE(deleted_at,?) WHERE trip_id=?",(utcnow(),trip))
            con.execute("UPDATE trips SET title='',start_date='',end_date='',conditions_json='{}',active_index_id=NULL WHERE id=?",(trip,))
            con.execute('DELETE FROM trip_stops WHERE trip_id=?',(trip,))
        for doc in con.execute('SELECT * FROM source_documents WHERE deleted_at IS NOT NULL').fetchall():
            path=Path(doc['opaque_path'])
            if path.is_absolute() and path.resolve().is_relative_to(Path(documents).resolve()):path.unlink(missing_ok=True)
            con.execute("UPDATE document_generations SET extracted_json=NULL WHERE document_id=?",(doc['id'],))
            con.execute("UPDATE source_documents SET opaque_path='',display_filename='',content_hash='' WHERE id=?",(doc['id'],))
        con.execute("UPDATE bookings SET extracted_json='{}',effective_json='{}',conflicts_json='[]',kind=NULL,date_start=NULL,date_end=NULL,stable_item_key=NULL WHERE deleted_at IS NOT NULL")
        con.execute('DELETE FROM booking_events WHERE booking_id IN (SELECT id FROM bookings WHERE deleted_at IS NOT NULL)')
        con.execute('DELETE FROM booking_overrides WHERE booking_id IN (SELECT id FROM bookings WHERE deleted_at IS NOT NULL)')
        affected='SELECT DISTINCT trip_id FROM deletion_tombstones'
        con.execute("UPDATE jobs SET payload_json='{}',checkpoint_json='{}',result_json='{}' WHERE trip_id IN ("+affected+")")
        con.execute('DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE trip_id IN ('+affected+'))')
        con.execute("UPDATE processing_receipts SET result_json='{}' WHERE trip_id IN ("+affected+")")
        con.execute("UPDATE trip_index_generations SET source_manifest='{}' WHERE trip_id IN ("+affected+")")


def create_archive(settings,target,key):
    with tempfile.TemporaryDirectory(prefix='travel-backup-') as temp:
        root=Path(temp);result=snapshot(settings,root/'snapshot')
        with tarfile.open(root/'snapshot.tar','w') as tar:tar.add(root/'snapshot',arcname='snapshot',recursive=True)
        encrypt(root/'snapshot.tar',target,key)
    return {**result,'encrypted_bytes':Path(target).stat().st_size,'sha256':file_hash(target)}


def _unpack(archive,destination):
    with tarfile.open(archive,'r:') as tar:
        members=tar.getmembers()
        if len(members)>100000 or sum(m.size for m in members)>MAX_BYTES:raise ValueError('Archive capacity exceeded')
        for m in members:
            path=Path(m.name)
            if path.is_absolute() or '..' in path.parts or path.parts[0]!='snapshot' or not (m.isfile() or m.isdir()):raise ValueError('Unsafe archive entry')
        tar.extractall(destination,members=members,filter='data')


def restore_archive(archive,checkpoint_path,destination,key,*,max_checkpoint_age_seconds=600):
    start=time.monotonic();destination=Path(destination)
    if destination.exists():raise ValueError('Restore destination must not exist')
    with tempfile.TemporaryDirectory(prefix='travel-restore-') as temp:
        root=Path(temp);decrypt(archive,root/'snapshot.tar',key);decrypt(checkpoint_path,root/'checkpoint.json',key)
        state=json.loads((root/'checkpoint.json').read_text());_unpack(root/'snapshot.tar',root)
        manifest=verify_backup(root/'snapshot')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(state['created_at'])).total_seconds()
        if state.get('format')!=1 or state.get('instance_id')!=manifest.get('instance_id'):raise ValueError('Checkpoint identity mismatch')
        if state['created_at']<manifest['created_at'] or age<0 or age>max_checkpoint_age_seconds:raise ValueError('Checkpoint is stale; restore remains closed')
        result=legacy_restore(root/'snapshot',destination,latest_state=state)
        # Marker is created before recovery can be accepted by the production launcher.
        private_json(destination/'RESTORE_PENDING.json',{'created_at':utcnow(),'checkpoint_at':state['created_at'],'checks_required':['integrity','ownership','search_rebuild','budget_reconciliation']})
        db=Database(destination/'database.sqlite3')
        with db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            # Retain financial records for post-snapshot principals without
            # reviving their service membership or their missing trip contents.
            for user in state['users']:
                if not con.execute('SELECT 1 FROM users WHERE id=?',(user['id'],)).fetchone():
                    user={**user,'status':'disabled','display_name':None,'session_epoch':user['session_epoch']+1}
                    _insert(con,'users',user)
            for trip in state['trips']:
                if not con.execute('SELECT 1 FROM trips WHERE id=?',(trip['id'],)).fetchone():
                    con.execute("INSERT INTO trips(id,owner_id,title,start_date,end_date,deleted_at,created_at,updated_at) VALUES(?,?,'','','',?,?,?)",(trip['id'],trip['owner_id'],utcnow(),utcnow(),utcnow()))
            for row in state['usage_reservations']:
                row={**row,'result_ref':None}
                if row['state'] in ('sent','reserved'):row['state']='unknown';row['error_code']='RESTORE_RECONCILIATION'
                _insert(con,'usage_reservations',row,replace=True)
            for row in state['usage_ledger']:_insert(con,'usage_ledger',row,replace=True)
            con.execute("UPDATE cost_controls SET halted=1,reason_code='RESTORE_RECONCILIATION',updated_at=?",(utcnow(),))
            con.execute("INSERT OR IGNORE INTO cost_controls VALUES('USD',1,'RESTORE_RECONCILIATION',?)",(utcnow(),))
            con.execute("UPDATE jobs SET state='cancelled',error_code='RESTORE_REVIEW_REQUIRED',lease_owner=NULL,lease_expires_at=NULL,fencing_token=fencing_token+1,finished_at=? WHERE state IN ('queued','running')",(utcnow(),))
            con.execute('DELETE FROM dispatcher_leases');con.execute('DELETE FROM trip_index_writers');con.execute('DELETE FROM trip_index_readers')
            con.execute('UPDATE trips SET active_index_id=NULL')
            con.execute("UPDATE trip_index_generations SET state='deleted',source_manifest='{}'")
            con.execute("UPDATE operations_controls SET mode='read_only',external_enabled=0,reason_code='RESTORE_REVIEW_REQUIRED'")
        _sanitize_deleted(db,destination/'documents')
        # Research policies/quality must be reviewed again, never resurrect OFF.
        from src.research.maintenance import merge_tombstones
        merge_tombstones(db,state['research_tombstones'])
        with db.connect() as con:
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('Restored database integrity failed')
        private_json(destination/'RESTORE_REPORT.json',{'snapshot_at':manifest['created_at'],'checkpoint_at':state['created_at'],'sessions_invalidated':True,'external_calls':'off','search':'rebuild_required','seconds':round(time.monotonic()-start,3)})
    return {**result,'state':'restored_closed_for_validation','seconds':round(time.monotonic()-start,3),'checkpoint_age_seconds':round(age,3)}


def _insert(con,table,row,replace=False):
    allowed={r[1] for r in con.execute('PRAGMA table_info('+table+')')}
    if set(row)!=allowed:raise ValueError('Checkpoint schema mismatch')
    columns=list(row)
    # UPSERT updates in place; REPLACE would violate dependent ledger FKs.
    pk=next(r[1] for r in con.execute('PRAGMA table_info('+table+')') if r[5])
    sql='INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')'
    if replace:sql+=' ON CONFLICT('+pk+') DO UPDATE SET '+','.join(c+'=excluded.'+c for c in columns if c!=pk)
    con.execute(sql,[row[c] for c in columns])
