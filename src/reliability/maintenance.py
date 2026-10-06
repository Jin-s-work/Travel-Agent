"""Conservative startup reclamation of aged, server-named storage orphans.

Only the current DB dispatcher leader may remove files. SQL references of any
status protect originals. JSON provider receipts and job checkpoints are never
blindly collected; this module handles only incomplete *.tmp artifacts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import re
import stat


_HEX = r'[0-9a-f]{32}'
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_TRIP = re.compile(r'trip_' + _HEX + r'\Z')
_SCOPE = re.compile(r'(?:trip_' + _HEX + r'|research_[0-9a-f]{64})\Z')
_JOB = re.compile(r'job_' + _HEX + r'\Z')
_RAW = re.compile(_UUID + r'\.(?:txt|eml)\Z')
_RAW_TMP = re.compile(r'\.' + _UUID + r'\.tmp\Z')
_JOB_TMP = re.compile(_UUID + r'\.tmp\Z')
_PROVIDER_TMP = re.compile(r'call_' + _HEX + r'\.' + _HEX + r'\.tmp\Z')


def _directory(path):
    try:
        return not path.is_symlink() and stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _regular(path, root):
    """No traversal through a symlink at any server-controlled depth."""
    try:
        if not path.is_relative_to(root) or path.is_symlink():
            return None
        parent = path.parent
        while parent != root:
            if not _directory(parent):
                return None
            parent = parent.parent
        value = path.lstat()
        return value if stat.S_ISREG(value.st_mode) else None
    except OSError:
        return None


def _children(path):
    try:
        return list(path.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []


def _candidates(settings):
    raw_root = settings.documents_dir.resolve()
    for directory in _children(raw_root):
        if not _TRIP.fullmatch(directory.name) or not _directory(directory):
            continue
        for path in _children(directory):
            if _RAW.fullmatch(path.name):
                yield path, raw_root, 'raw'
            elif _RAW_TMP.fullmatch(path.name):
                yield path, raw_root, 'temporary'
    artifact_root = settings.artifacts_dir.resolve()
    for scope in _children(artifact_root):
        if not _SCOPE.fullmatch(scope.name) or not _directory(scope):
            continue
        for child in _children(scope):
            if _PROVIDER_TMP.fullmatch(child.name):
                yield child, artifact_root, 'temporary'
            elif _JOB.fullmatch(child.name) and _directory(child):
                for path in _children(child):
                    if _JOB_TMP.fullmatch(path.name):
                        yield path, artifact_root, 'temporary'


def reconcile_storage(db, settings, *, dispatcher_owner, now=None, grace_seconds=None):
    """Return counts only, without filenames, original text or identity data.

    A 24-hour default is intentionally longer than normal upload/provider
    deadlines. It is configurable via settings.storage_orphan_grace_seconds.
    ``now`` is an explicit test clock; production callers omit it.
    """
    grace = grace_seconds if grace_seconds is not None else getattr(settings,'storage_orphan_grace_seconds',86400)
    if isinstance(grace,bool) or not isinstance(grace,(int,float)) or not math.isfinite(grace) or grace <= 0:
        raise ValueError('Storage orphan grace must be positive')
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('Maintenance clock must include timezone')
    current = current.astimezone(timezone.utc)
    stamp, cutoff = current.isoformat(), current.timestamp() - grace
    result = {'raw_removed':0,'temporary_removed':0,'protected_references':0,
              'recent_skipped':0,'active_writer_skipped':0,'skipped':None}
    if not dispatcher_owner:
        return {**result,'skipped':'not_dispatcher_leader'}
    # Even an empty scan checks ownership, making operator status meaningful.
    with db.connect() as con:
        if not con.execute('SELECT 1 FROM dispatcher_leases WHERE name=? AND owner=? AND lease_expires_at>?',
                           ('main',dispatcher_owner,stamp)).fetchone():
            return {**result,'skipped':'not_dispatcher_leader'}
    for path, root, kind in _candidates(settings):
        info = _regular(path,root)
        if info is None:
            continue
        if info.st_mtime > cutoff:
            result['recent_skipped'] += 1
            continue
        with db.connect() as con:
            # Save's rename/reference write, provider artifact persist and all
            # deletion guards use this same short local writer exclusion.
            con.execute('BEGIN IMMEDIATE')
            stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
            if not con.execute('SELECT 1 FROM dispatcher_leases WHERE name=? AND owner=? AND lease_expires_at>?',
                               ('main',dispatcher_owner,stamp)).fetchone():
                result['skipped']='not_dispatcher_leader'
                return result
            info = _regular(path,root)
            if info is None or info.st_mtime > cutoff:
                continue
            if kind == 'raw':
                references = con.execute('SELECT opaque_path FROM source_documents').fetchall()
                if any(Path(row['opaque_path']).resolve() == path for row in references):
                    result['protected_references'] += 1
                    continue
            else:
                # A pending upload is protected by the writer transaction;
                # persistent worker leases additionally protect unfinished
                # provider/checkpoint temp files, regardless of their age.
                if con.execute("SELECT 1 FROM jobs WHERE state='running' AND lease_expires_at>? LIMIT 1",(stamp,)).fetchone():
                    result['active_writer_skipped'] += 1
                    continue
            path.unlink(missing_ok=True)
            result[kind+'_removed'] += 1
    return result
