"""Explicit local administration; no automatic ownership guesses or paid imports.

Run with ``python -m src.foundation.cli --help``. Inventory is read-only and
does not initialize a database. Backup/restore require a stopped application.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys

from .db import Database
from .repository import DomainError, Repository, dump, utcnow
from .settings import Settings


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def private_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        temporary.chmod(0o600)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(path)


def inventory(source_root: Path):
    root = source_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('source-root must be a directory')
    files, skipped, hashes = [], [], {}
    for path in sorted(root.rglob('*')):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            skipped.append({'relative_path': relative, 'reason': 'symlink'})
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in ('.txt', '.eml'):
            skipped.append({'relative_path': relative, 'reason': 'unsupported_extension'})
            continue
        digest = file_hash(path)
        files.append({'relative_path': relative, 'sha256': digest, 'bytes': path.stat().st_size,
                      'owner_status': 'unresolved', 'booking_count': None, 'parse_status': 'not_run'})
        hashes.setdefault(digest, []).append(relative)
    return {'schema_version': 1, 'generated_at': utcnow(), 'source_root': str(root),
            'file_count': len(files), 'booking_count': None, 'unresolved_owner_count': len(files),
            'files': files, 'duplicates': [paths for paths in hashes.values() if len(paths) > 1], 'skipped': skipped,
            'note': 'Read-only file inventory; no model, index, or ownership inference was performed.'}


def _assert_new_destination(destination: Path, sources: list[Path]):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Destination must not exist; existing data will never be overwritten')
    for source in sources:
        if destination.is_relative_to(source.resolve()):
            raise ValueError('Destination must be outside source directories')
    destination.mkdir(parents=True, mode=0o700)
    return destination


def _copy_private_tree(source: Path, destination: Path):
    if not source.exists():
        destination.mkdir(mode=0o700)
        return
    if source.is_symlink():
        raise ValueError('Symlink source directories are not supported')
    destination.mkdir(mode=0o700)
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlinks must be resolved manually before backup')
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(mode=0o700)
        elif path.is_file():
            shutil.copyfile(path, target)
            target.chmod(0o600)


def backup(settings: Settings, destination: Path):
    """Caller must stop API/CLI writers before pairing SQL and file snapshots."""
    database = settings.database_path.resolve(strict=True)
    destination = _assert_new_destination(destination, [settings.documents_dir, settings.vectors_dir, database.parent])
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(destination / 'database.sqlite3')) as target:
                source.backup(target)
        (destination / 'database.sqlite3').chmod(0o600)
        _copy_private_tree(settings.documents_dir, destination / 'documents')
        _copy_private_tree(settings.vectors_dir, destination / 'vectors')
        _copy_private_tree(settings.artifacts_dir, destination / 'private-job-artifacts')
        files = [{'path': str(path.relative_to(destination)), 'sha256': file_hash(path), 'bytes': path.stat().st_size}
                 for path in sorted(destination.rglob('*')) if path.is_file()]
        manifest = {'schema_version': 1, 'created_at': utcnow(), 'state': 'complete', 'requires_offline': True,
                    'original_documents_dir': str(settings.documents_dir.resolve()),
                    'original_vectors_dir': str(settings.vectors_dir.resolve()), 'files': files}
        private_json(destination / 'manifest.json', manifest)
        return {'backup_dir': str(destination), 'files': len(files), 'state': 'complete'}
    except Exception:
        # A partial backup is retained for diagnosis but cannot pass verify_backup.
        private_json(destination / 'FAILED.json', {'state': 'incomplete', 'created_at': utcnow()})
        raise


def verify_backup(directory: Path):
    directory = directory.resolve(strict=True)
    if (directory / 'FAILED.json').exists():
        raise ValueError('Backup is marked incomplete')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest.get('schema_version') != 1 or manifest.get('state') != 'complete':
        raise ValueError('Unsupported or incomplete backup')
    expected = set()
    for item in manifest['files']:
        path = directory / item['path']
        if path.is_symlink() or not path.resolve().is_relative_to(directory) or not path.is_file():
            raise ValueError('Backup path is invalid')
        if file_hash(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('Backup checksum mismatch')
        expected.add(item['path'])
    if 'database.sqlite3' not in expected:
        raise ValueError('Backup has no database')
    actual = {str(path.relative_to(directory)) for path in directory.rglob('*') if path.is_file() and path.name != 'manifest.json'}
    if expected != actual:
        raise ValueError('Unexpected or missing backup file')
    # Backup API produced a complete standalone image; immutable reads avoid
    # creating WAL/SHM sidecars while checking an intentionally frozen backup.
    with closing(sqlite3.connect((directory / 'database.sqlite3').as_uri() + '?mode=ro&immutable=1', uri=True)) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup SQLite integrity check failed')
    return manifest


def restore(directory: Path, destination: Path, deletions_from: Path | None = None, *, latest_state=None):
    manifest = verify_backup(directory)
    directory = directory.resolve()
    destination = _assert_new_destination(destination, [directory])
    private_json(destination / 'RESTORE_INCOMPLETE.json', {'state': 'incomplete', 'created_at': utcnow()})
    for folder in ('documents', 'vectors'):
        _copy_private_tree(directory / folder, destination / folder)
    # Phase-one backups legitimately have no artifact directory. Phase-two
    # backups preserve completed provider receipts and handler checkpoints.
    _copy_private_tree(directory / 'private-job-artifacts', destination / 'private-job-artifacts')
    shutil.copyfile(directory / 'database.sqlite3', destination / 'database.sqlite3')
    (destination / 'database.sqlite3').chmod(0o600)
    tombstones, current_users, research_tombstones, discovery_tombstones = [], [], [], []
    if deletions_from:
        with closing(sqlite3.connect(deletions_from.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as current:
            current.row_factory = sqlite3.Row
            tombstones = [dict(row) for row in current.execute('SELECT * FROM deletion_tombstones')]
            current_users = [dict(row) for row in current.execute('SELECT id,status,session_epoch,role FROM users')]
            if current.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_tombstones'").fetchone():
                research_tombstones=[dict(row) for row in current.execute('SELECT * FROM research_tombstones')]
            if current.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='discovery_tombstones'").fetchone():
                discovery_tombstones=[dict(row) for row in current.execute('SELECT * FROM discovery_tombstones')]
    if latest_state is not None:
        tombstones=latest_state['deletion_tombstones']
        current_users=latest_state['users']
        research_tombstones=latest_state['research_tombstones']
        discovery_tombstones=latest_state['discovery_tombstones']
    db = Database(destination / 'database.sqlite3')
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # Recovered sessions must not reactivate expired/revoked access.
        con.execute('DELETE FROM sessions')
        con.execute('UPDATE users SET session_epoch=session_epoch+1')
        con.execute('UPDATE invitations SET revoked_at=COALESCE(revoked_at,?) WHERE used_at IS NULL', (utcnow(),))
        for user in current_users:
            con.execute('UPDATE users SET status=?,session_epoch=MAX(session_epoch,?) WHERE id=?', (user['status'], user['session_epoch'] + 1, user['id']))
            if 'role' in user:con.execute('UPDATE users SET role=? WHERE id=?',(user['role'],user['id']))
        for row in con.execute('SELECT id,opaque_path FROM source_documents').fetchall():
            if not row['opaque_path']:continue
            old = Path(row['opaque_path'])
            original_root = Path(manifest['original_documents_dir'])
            if not old.is_relative_to(original_root):
                raise ValueError('Source path is outside original private document directory')
            con.execute('UPDATE source_documents SET opaque_path=? WHERE id=?', (str(destination / 'documents' / old.relative_to(original_root)), row['id']))
        for item in tombstones:
            if not con.execute('SELECT id FROM trips WHERE id=?', (item['trip_id'],)).fetchone():
                continue
            con.execute('INSERT OR IGNORE INTO deletion_tombstones(target_type,target_id,trip_id,deletion_epoch,requested_at,completed_at) VALUES (?,?,?,?,?,?)',
                        tuple(item[key] for key in ('target_type', 'target_id', 'trip_id', 'deletion_epoch', 'requested_at', 'completed_at')))
        for item in con.execute('SELECT * FROM deletion_tombstones').fetchall():
            stamp = item['requested_at']
            if item['target_type'] == 'trip':
                con.execute('UPDATE trips SET deleted_at=COALESCE(deleted_at,?) WHERE id=?', (stamp, item['target_id']))
                con.execute('UPDATE bookings SET deleted_at=COALESCE(deleted_at,?) WHERE trip_id=?', (stamp, item['trip_id']))
                con.execute('UPDATE source_documents SET deleted_at=COALESCE(deleted_at,?) WHERE trip_id=?', (stamp, item['trip_id']))
            elif item['target_type'] == 'document':
                con.execute('UPDATE source_documents SET deleted_at=COALESCE(deleted_at,?) WHERE id=?', (stamp, item['target_id']))
                con.execute('UPDATE bookings SET deleted_at=COALESCE(deleted_at,?) WHERE document_id=?', (stamp, item['target_id']))
            elif item['target_type'] == 'accommodation':
                from src.accommodations.service import Accommodations
                Accommodations.scrub_one(con,item['target_id'],stamp,destination/'private-job-artifacts')
            elif item['target_type'] == 'booking':
                con.execute('UPDATE bookings SET deleted_at=COALESCE(deleted_at,?) WHERE id=?', (stamp, item['target_id']))
    from src.research.maintenance import merge_tombstones
    research_restore=merge_tombstones(db,research_tombstones)
    from src.discovery.maintenance import merge_tombstones as merge_discovery_tombstones
    discovery_restore=merge_discovery_tombstones(db,discovery_tombstones)
    (destination / 'RESTORE_INCOMPLETE.json').unlink()
    return {'state': 'restored_for_validation', 'database_path': str(db.path), 'documents_dir': str(destination / 'documents'),
            'vectors_dir': str(destination / 'vectors'), 'later_tombstones_applied': len(tombstones), 'sessions_invalidated': True,
            'research_deletion_history':research_restore, 'discovery_deletion_history':discovery_restore,
            'deletion_history': 'independent_checkpoint_applied' if latest_state is not None else 'current_database_applied' if deletions_from else 'backup_only_requires_review'}


def import_legacy(settings: Settings, mapping_path: Path, backup_dir: Path, *, process=False, document_service=None):
    if process and not (document_service and document_service.parser and document_service.embedder and document_service.vector_factory):
        # Fail before backup reads, DB initialization, file copies, or receipts.
        raise ValueError('--process is retired: import without --process, then sign in and reprocess each document through the durable job API')
    verify_backup(backup_dir)
    mapping = json.loads(mapping_path.read_text())
    if mapping.get('schema_version') != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(mapping.get('run_id', ''))):
        raise ValueError('Mapping needs schema_version=1 and a safe explicit run_id')
    root = Path(mapping['source_root']).resolve(strict=True)
    entries = mapping.get('files')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Mapping files must contain explicit file/user_id/trip_id assignments')
    from .documents import DocumentService
    repo = Repository(Database(settings.database_path,url=settings.database_url or None))
    service = document_service or DocumentService(repo, settings)
    prepared = []
    seen = set()
    for item in entries:
        relative = Path(item['relative_path'])
        path = root / relative
        if relative.is_absolute() or '..' in relative.parts or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Mapped path escapes legacy directory')
        # Check every parent, not only the final path, before reading content.
        if any(parent.is_symlink() for parent in path.parents if parent != root.parent):
            raise ValueError('Symlink mapping is not supported')
        if not path.is_file() or path.stat().st_size > settings.max_upload_bytes:
            raise ValueError('Mapped file missing or exceeds configured upload limit')
        key = (str(relative), item['user_id'], item['trip_id'])
        if key in seen:
            raise ValueError('Duplicate mapping row')
        seen.add(key)
        repo.get_trip(item['user_id'], item['trip_id'])
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != item.get('sha256'):
            raise ValueError('Mapped file changed since inventory; regenerate and review mapping')
        mime = 'message/rfc822' if path.suffix.lower() == '.eml' else 'text/plain'
        error = service.validate(path.name, mime, data)
        if error:
            raise ValueError(f'Mapped file failed validation: {error}')
        prepared.append((item, path, data))
    mapping_hash = hashlib.sha256(dump(mapping).encode()).hexdigest()
    run_path = settings.documents_dir / '.migration-runs' / (mapping['run_id'] + '.json')
    if run_path.exists():
        run = json.loads(run_path.read_text())
        if run['mapping_hash'] != mapping_hash:
            raise ValueError('Run ID is already bound to a different immutable mapping')
    else:
        run = {'schema_version': 1, 'run_id': mapping['run_id'], 'mapping_hash': mapping_hash,
               'started_at': utcnow(), 'state': 'running', 'files': []}
        private_json(run_path, run)
    results = []
    for item, path, data in prepared:
        key = hashlib.sha256(dump(item).encode()).hexdigest()
        receipt = repo.create_receipt(item['user_id'], item['trip_id'], key, 'migration:' + mapping['run_id'] + ':' + key)
        previous_files = receipt['result'].get('files', [])
        if receipt['reused'] and previous_files and previous_files[0].get('document_id'):
            try:
                document = repo.get_document(item['user_id'], item['trip_id'], previous_files[0]['document_id'])
            except DomainError as error:
                if error.status != 404:
                    raise
                raise ValueError('A previously imported document was deleted; review a new run instead of resurrecting it') from error
        else:
            document = service.save(item['user_id'], item['trip_id'], path.name, data)
        accepted = [{'document_id': document['id'], 'filename': path.name, 'state': 'accepted'}]
        if process and receipt['status'] != 'succeeded':
            service.process(item['user_id'], item['trip_id'], receipt['id'], accepted, [])
            receipt = repo.get_receipt(item['user_id'], item['trip_id'], receipt['id'])
        elif not process and receipt['status'] == 'queued':
            repo.update_receipt(item['user_id'], item['trip_id'], receipt['id'], 'queued', {'files': accepted, 'note': 'Explicit import only; no durable queue or automatic processing.'})
        results.append({'relative_path': item['relative_path'], 'user_id': item['user_id'], 'trip_id': item['trip_id'],
                        'document_id': document['id'], 'receipt_id': receipt['id'], 'status': receipt['status']})
        run['files'] = results
        private_json(run_path, run)
    statuses = {item['status'] for item in results}
    run['state'] = 'complete' if statuses == {'succeeded'} else ('imported_unprocessed' if statuses <= {'queued', 'succeeded'} else 'needs_review')
    run['updated_at'] = utcnow()
    private_json(run_path, run)
    return run


def set_user_role(db, user_id, role):
    """Offline operator command; roles are never accepted by a public API."""
    if role not in {'member','admin'}:
        raise ValueError('Unsupported role')
    from uuid import uuid4
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        old=con.execute("SELECT role FROM users WHERE id=? AND status='active'",(user_id,)).fetchone()
        if not old:
            raise DomainError('NOT_FOUND','활성 사용자를 찾을 수 없습니다.',404)
        con.execute('UPDATE users SET role=?,session_epoch=session_epoch+1,updated_at=? WHERE id=?',(role,utcnow(),user_id))
        con.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
        con.execute('INSERT INTO research_audit VALUES(?,?,?,?,?,?)',('audit_'+uuid4().hex,user_id,'offline_role_changed',user_id,
            json.dumps({'old_role':old['role'],'new_role':role,'method':'local_operator_cli'}),utcnow()))
    return {'user_id':user_id,'role':role,'sessions_invalidated':True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--documents', type=Path)
    parser.add_argument('--vectors', type=Path)
    sub = parser.add_subparsers(dest='command', required=True)
    invite = sub.add_parser('invite')
    invite.add_argument('--email', required=True)
    invite.add_argument('--hours', type=int, default=72)
    revoke = sub.add_parser('revoke-invitation')
    revoke.add_argument('--token-file', type=Path, help='Otherwise reads one token from stdin; avoids shell history')
    disable = sub.add_parser('disable-user')
    disable.add_argument('--user-id', required=True)
    role = sub.add_parser('set-role')
    role.add_argument('--user-id',required=True)
    role.add_argument('--role',choices=['admin','member'],required=True)
    role.add_argument('--offline',action='store_true',required=True)
    inv = sub.add_parser('legacy-inventory')
    inv.add_argument('--source-root', type=Path, required=True)
    inv.add_argument('--output', type=Path)
    back = sub.add_parser('backup')
    back.add_argument('--destination', type=Path, required=True)
    back.add_argument('--offline', action='store_true', required=True, help='Confirms API and other writers are stopped')
    rest = sub.add_parser('restore')
    rest.add_argument('--backup', type=Path, required=True)
    rest.add_argument('--destination', type=Path, required=True)
    rest.add_argument('--deletions-from-db', type=Path)
    rest.add_argument('--offline', action='store_true', required=True)
    migration = sub.add_parser('legacy-import')
    migration.add_argument('--mapping', type=Path, required=True)
    migration.add_argument('--backup', type=Path, required=True)
    migration.add_argument('--process', action='store_true', help='Retired: import first, then use authenticated UI reanalysis (this flag is rejected before mutation)')
    migration.add_argument('--offline', action='store_true', required=True)
    args = parser.parse_args(argv)
    # Match server environment loading without printing any secret values.
    from dotenv import load_dotenv
    load_dotenv()
    settings = Settings()
    overrides = {name: getattr(args, flag) for flag, name in [('database', 'database_path'), ('documents', 'documents_dir'), ('vectors', 'vectors_dir')] if getattr(args, flag)}
    settings = replace(settings, **overrides)
    if settings.storage_backend=='supabase' and args.command in {'backup','restore','legacy-import'}:
        parser.error('Use src.storage.transfer for cloud import/backup; legacy ownership inventory remains local')
    if args.command == 'legacy-inventory':
        result = inventory(args.source_root)
        if args.output:
            private_json(args.output, result)
            result = {'output': str(args.output), 'files': result['file_count'], 'booking_count': None, 'state': 'inventory_only'}
    elif args.command == 'set-role':
        result=set_user_role(Database(settings.database_path,url=settings.database_url or None),args.user_id,args.role)
    elif args.command == 'backup':
        result = backup(settings, args.destination)
    elif args.command == 'restore':
        result = restore(args.backup, args.destination, args.deletions_from_db)
    elif args.command == 'legacy-import':
        result = import_legacy(settings, args.mapping, args.backup, process=args.process)
    else:
        from .auth import Auth
        auth = Auth(Database(settings.database_path,url=settings.database_url or None), settings)
        if args.command == 'invite':
            result = {'invitation_token': auth.invite(args.email, args.hours), 'expires_in_hours': args.hours,
                      'note': 'Token shown once. Deliver privately; enter in login form, not a public URL.'}
        elif args.command == 'revoke-invitation':
            token = args.token_file.read_text().strip() if args.token_file else sys.stdin.readline().strip()
            if not token:
                raise ValueError('Invitation token is required')
            auth.revoke_invitation(token)
            result = {'state': 'revoked'}
        else:
            auth.disable_user(args.user_id)
            result = {'state': 'disabled', 'sessions': 'revoked'}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No tracebacks containing raw mail, provider responses or tokens.
        print(json.dumps({'error': {'code': getattr(error, 'code', 'ADMIN_COMMAND_FAILED'), 'message': str(error)}}), file=sys.stderr)
        raise SystemExit(1)
