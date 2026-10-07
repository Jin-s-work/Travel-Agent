"""Private Supabase Storage, immutable server IDs and bounded network I/O.

SQL manifests are durable before upload. Registration is fenced after upload;
crashes leave inspectable orphans, never references to a partial local file.
"""
import hashlib
import re
import uuid
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, quote
import httpx
from src.foundation.repository import DomainError


class SupabaseObjects:
    def __init__(self, settings, db, *, transport=None):
        self.db, self.settings = db, settings
        parsed = urlsplit(settings.supabase_url)
        if parsed.scheme != 'https' or not re.fullmatch(r'[a-z0-9-]+\.supabase\.co', parsed.hostname or '') or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username or parsed.port not in (443, None):
            raise ValueError('SUPABASE_URL must be a project HTTPS origin')
        if not settings.supabase_secret_key or not re.fullmatch(r'[a-z0-9-]{3,63}', settings.supabase_bucket):
            raise ValueError('Private Supabase Storage configuration required')
        self.base = settings.supabase_url.rstrip('/') + '/storage/v1'
        self.bucket = settings.supabase_bucket
        self.private_checked_at = 0
        key = settings.supabase_secret_key
        headers = {'apikey': key}
        # Legacy service_role JWT vs current server secret API key.
        if not key.startswith('sb_secret_'): headers['Authorization'] = 'Bearer ' + key
        self.client = httpx.Client(headers=headers, timeout=httpx.Timeout(15, connect=5),
                                   follow_redirects=False, transport=transport)

    def close(self): self.client.close()

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, self.base + path, **kwargs)
        except httpx.HTTPError:
            raise DomainError('STORAGE_UNAVAILABLE', '원문 저장소에 연결할 수 없습니다. 저장된 예약은 계속 조회할 수 있습니다.', 503) from None
        if not 200 <= response.status_code < 300:
            raise DomainError('STORAGE_UNAVAILABLE', '비공개 원문 저장소 설정 또는 용량을 확인해 주세요.', 503)
        return response

    def verify_private(self):
        data = self.request('GET', '/bucket/' + self.bucket).json()
        if data.get('public') is not False or data.get('id') != self.bucket:
            raise ValueError('Storage bucket must exist and be private')
        self.private_checked_at = time.monotonic()

    def require_private(self):
        if not self.private_checked_at or time.monotonic()-self.private_checked_at>300:
            try: self.verify_private()
            except ValueError: raise DomainError('STORAGE_NOT_PRIVATE','원문 저장소의 비공개 설정을 확인해야 합니다.',503) from None

    @staticmethod
    def validate_key(key):
        if not re.fullmatch(r'trip_[a-f0-9]{32}/[a-f0-9]{32}\.(txt|eml)', key):
            raise DomainError('NOT_FOUND', '원문을 찾을 수 없습니다.', 404)
        return key

    def url(self, key):
        return '/object/' + self.bucket + '/' + quote(self.validate_key(key), safe='/')

    def save(self, repo, user, trip, filename, data):
        self.require_private()
        digest = hashlib.sha256(data).hexdigest()
        extension = filename.rsplit('.', 1)[-1].lower()
        if extension not in {'txt', 'eml'}: raise ValueError('Unsupported original format')
        key = trip + '/' + uuid.uuid4().hex + '.' + extension
        # Includes pending/orphan objects in the app's 100 MiB bound. Shared
        # project/egress quotas still require operator observation.
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); repo._trip(con, user, trip)
            existing = con.execute('SELECT * FROM source_documents WHERE trip_id=? AND content_hash=? AND deleted_at IS NULL', (trip, digest)).fetchone()
            if existing: return dict(existing)
            size = con.execute("SELECT COALESCE(SUM(byte_size),0) FROM cloud_objects WHERE state!='deleted'").fetchone()[0]
            if size + len(data) > 100 * 1024 * 1024:
                raise DomainError('STORAGE_LIMIT', '원문 저장 상한에 도달했습니다. 기존 자료는 계속 조회할 수 있습니다.', 429)
            con.execute("INSERT INTO cloud_objects(key,trip_id,sha256,byte_size,state) VALUES(?,?,?,?,'pending')", (key, trip, digest, len(data)))
        # No SQL write lock is held over the Storage request.
        self.request('POST', self.url(key), content=data, headers={'Content-Type': 'application/octet-stream', 'x-upsert': 'false'})
        try:
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE'); repo._trip(con, user, trip)
                self.check_registration(con, key)
                doc = repo.create_document(user, trip, filename, 'supabase:' + key, digest, connection=con)
                con.execute('UPDATE cloud_objects SET state=? WHERE key=?', ('active' if doc['opaque_path'] == 'supabase:' + key else 'orphan', key))
        except DomainError:
            # The POST can finish after a tombstone (even after its confirmation).
            # Keep that key fenced and schedule another bounded confirmation.
            self.reject_late_upload(key)
            raise
        return doc

    def read(self, document):
        self.require_private()
        ref = document['opaque_path']
        if not ref.startswith('supabase:'): raise DomainError('NOT_FOUND', '원문을 찾을 수 없습니다.', 404)
        key = self.validate_key(ref[9:])
        if key.split('/')[0] != document['trip_id']: raise DomainError('NOT_FOUND', '원문을 찾을 수 없습니다.', 404)
        try:
            with self.client.stream('GET', self.base + self.url(key)) as response:
                if response.status_code != 200: raise DomainError('STORAGE_UNAVAILABLE', '원문을 불러오지 못했습니다.', 503)
                result = bytearray()
                for chunk in response.iter_bytes():
                    result.extend(chunk)
                    if len(result) > self.settings.max_upload_bytes: raise DomainError('STORAGE_CORRUPT', '원문 크기를 확인할 수 없습니다.', 503)
        except httpx.HTTPError:
            raise DomainError('STORAGE_UNAVAILABLE', '원문을 불러오지 못했습니다.', 503) from None
        if hashlib.sha256(result).hexdigest() != document['content_hash']:
            raise DomainError('STORAGE_CORRUPT', '원문 무결성 검사에 실패했습니다.', 503)
        return bytes(result)

    def check_registration(self, con, key):
        if con.execute('SELECT 1 FROM storage_deletion_receipts WHERE key=?', (key,)).fetchone():
            raise DomainError('STORAGE_UPLOAD_EXPIRED', '원문 정리가 시작되어 저장하지 못했습니다. 다시 업로드해 주세요.', 409)

    def _receipt(self, con, key, kind, stamp, *, rearm=False):
        con.execute('INSERT OR IGNORE INTO storage_deletion_receipts(key,kind,requested_at,next_reconcile_at,updated_at) VALUES(?,?,?,?,?)',
                    (key, kind, stamp, stamp, stamp))
        if rearm:
            con.execute('UPDATE storage_deletion_receipts SET confirmations=0,completed_at=NULL,next_reconcile_at=?,updated_at=? WHERE key=?',
                        (stamp, stamp, key))

    def reject_late_upload(self, key, *, kind='object'):
        stamp = datetime.now(timezone.utc).isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._receipt(con, key, kind, stamp, rearm=True)
        # A failed immediate delete stays in the durable retry queue.
        try: self.remove(key, kind=kind)
        except DomainError: pass

    def remove(self, key, *, guard=None, kind='object', now=None):
        self.validate_key(key)
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if guard: guard(con=con)
            self._receipt(con, key, kind, stamp)
        if guard: guard()
        self.request('DELETE', '/object/' + self.bucket, json={'prefixes': [key]})
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if guard: guard(con=con)
            con.execute("UPDATE cloud_objects SET state='deleted' WHERE key=?", (key,))
            # A second, delayed sweep confirms absence after bounded in-flight
            # uploads. The tombstone itself remains permanent after completion.
            con.execute('UPDATE storage_deletion_receipts SET attempts=attempts+1,'
                        'confirmations=CASE WHEN confirmations=0 OR next_reconcile_at<=? THEN confirmations+1 ELSE confirmations END,'
                        'completed_at=CASE WHEN confirmations>=1 AND next_reconcile_at<=? THEN ? ELSE completed_at END,'
                        'next_reconcile_at=?,last_error_code=NULL,updated_at=? WHERE key=?',
                        (stamp, stamp, stamp, (datetime.fromisoformat(stamp)+timedelta(days=1)).isoformat(), stamp, key))

    def reconcile(self, *, guard=None, now=None, limit=100):
        """Advance a bounded durable queue, without retrying completed prefixes.

        A failing key receives backoff and cannot stop other keys in the batch.
        ``now`` is a test clock; live callers use UTC wall time.
        """
        if not 1 <= limit <= 100: raise ValueError('Reconciliation limit must be 1..100')
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None: raise ValueError('Reconciliation clock must include timezone')
        stamp, cutoff = current.isoformat(), (current-timedelta(days=1)).isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if guard: guard(con=con)
            rows = con.execute("SELECT o.key FROM cloud_objects o JOIN trips t ON t.id=o.trip_id "
                "WHERE o.created_at < ? AND (t.deleted_at IS NOT NULL OR o.state IN ('pending','orphan','deleted') "
                "OR EXISTS(SELECT 1 FROM source_documents d WHERE d.opaque_path='supabase:'||o.key AND d.deleted_at IS NOT NULL)) "
                "AND NOT EXISTS(SELECT 1 FROM storage_deletion_receipts r WHERE r.key=o.key) ORDER BY o.created_at,o.key LIMIT ?", (cutoff,limit)).fetchall()
            imports = con.execute("SELECT o.key FROM cloud_import_objects o WHERE o.created_at<? "
                "AND NOT EXISTS(SELECT 1 FROM source_documents d WHERE d.opaque_path='supabase:'||o.key) "
                "AND NOT EXISTS(SELECT 1 FROM storage_deletion_receipts r WHERE r.key=o.key) ORDER BY o.created_at,o.key LIMIT ?", (cutoff,limit)).fetchall()
            for kind, batch in (('object',rows),('import',imports)):
                for row in batch: self._receipt(con,row['key'],kind,stamp)
        with self.db.connect() as con:
            due = con.execute('SELECT key,kind,attempts FROM storage_deletion_receipts WHERE completed_at IS NULL AND next_reconcile_at<=? '
                              'ORDER BY attempts,next_reconcile_at,key LIMIT ?', (stamp,limit)).fetchall()
        result = {'attempted':len(due),'deleted':0,'failed':0}
        for row in due:
            try:
                self.remove(row['key'],guard=guard,kind=row['kind'],now=current)
                result['deleted'] += 1
            except DomainError as exc:
                if exc.code == 'LEASE_LOST': raise
                result['failed'] += 1
                retry = (current+timedelta(seconds=min(86400,300*2**min(row['attempts'],8)))).isoformat()
                with self.db.connect() as con:
                    con.execute('BEGIN IMMEDIATE')
                    if guard: guard(con=con)
                    con.execute('UPDATE storage_deletion_receipts SET attempts=attempts+1,next_reconcile_at=?,last_error_code=?,updated_at=? WHERE key=?',
                                (retry,'STORAGE_DELETE_FAILED',stamp,row['key']))
        return result
