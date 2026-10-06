"""SQLite is the authority for jobs, leases, authorization and replay events.

No network I/O runs in these short transactions. Checkpoints hold references to
private artifacts, never copied mail bodies or provider credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from src.foundation.repository import DomainError

TERMINAL = frozenset({'succeeded', 'partial', 'failed', 'cancelled'})
PERSONAL_OPERATIONS = frozenset({'documents', 'reindex', 'bookmark_resolve', 'recommendations', 'itinerary_generate'})
FORBIDDEN_KEYS = frozenset({'raw_text', 'body', 'access_token', 'refresh_token', 'id_token',
                            'password', 'api_key', 'secret', 'authorization', 'cookie'})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _safe(value, *, max_bytes=262144):
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).casefold() in FORBIDDEN_KEYS:
                    raise DomainError('INVALID_JOB_REFERENCE', '작업에는 원문과 인증 정보를 복사할 수 없습니다.')
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    try:
        result = _json(value)
    except (TypeError, ValueError) as exc:
        raise DomainError('INVALID_JOB_REFERENCE', '작업 참조 형식을 확인해 주세요.') from exc
    if len(result.encode()) > max_bytes:
        raise DomainError('INVALID_JOB_REFERENCE', '작업 참조가 너무 큽니다.')
    return result


class Jobs:
    def __init__(self, db, *, lease_seconds=90, max_attempts=3, admin_scopes=(), clock=None):
        if not 0.05 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 10:
            raise ValueError('Invalid job lease or maximum attempts')
        self.db = db
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.admin_scopes = frozenset(admin_scopes)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.accepting = True

    def now(self):
        return self.clock().astimezone(timezone.utc).isoformat()

    def later(self, seconds):
        return (self.clock().astimezone(timezone.utc) + timedelta(seconds=seconds)).isoformat()

    @contextmanager
    def _transaction(self, con=None):
        if con is not None:
            yield con
        else:
            with self.db.connect() as own:
                own.execute('BEGIN IMMEDIATE')
                yield own

    def _user(self, con, actor_id, session_id):
        user = con.execute("SELECT u.* FROM users u JOIN sessions s ON s.user_id=u.id "
            "WHERE u.id=? AND s.id=? AND s.expires_at>? AND s.epoch=u.session_epoch AND u.status='active'",
            (actor_id, session_id, self.now())).fetchone()
        if not user:
            raise DomainError('AUTH_REQUIRED', '세션이 만료되었거나 회수되었습니다.', 401)
        return user

    def _scope(self, con, actor_id, session_id, scope_kind, scope_id, *, allow_deleted=False):
        user = self._user(con, actor_id, session_id)
        if scope_kind == 'personal_trip':
            trip = con.execute('SELECT * FROM trips WHERE id=? AND owner_id=?', (scope_id, actor_id)).fetchone()
            if not trip or (trip['deleted_at'] and not allow_deleted):
                raise DomainError('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
            return trip
        if scope_kind == 'admin_research':
            if user['role'] != 'admin' or scope_id not in self.admin_scopes:
                raise DomainError('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
            return None
        raise DomainError('INVALID_JOB_SCOPE', '작업 범위를 확인해 주세요.')

    @staticmethod
    def _decode(row):
        value = dict(row)
        value['job_id'] = value['id']
        for name in ('payload', 'checkpoint', 'result'):
            value[name] = json.loads(value.pop(name + '_json'))
        value['retryable'] = bool(value['retryable'])
        return value

    def _public(self, con, row):
        fields = ('id', 'actor_id', 'scope_kind', 'scope_id', 'trip_id', 'operation', 'state',
                  'input_version', 'attempt', 'max_attempts', 'deadline_at', 'cancel_requested_at',
                  'error_code', 'stage', 'done_count', 'total_count', 'started_at', 'finished_at',
                  'created_at', 'updated_at')
        result = {key: row[key] for key in fields}
        result.update(job_id=row['id'], retryable=bool(row['retryable']), result=json.loads(row['result_json']))
        if 'files' not in result['result']:
            completed_files = json.loads(row['checkpoint_json']).get('files')
            if isinstance(completed_files, list):
                allowed = {'document_id', 'filename', 'state', 'error_code', 'bookings_count', 'activated'}
                result['result']['files'] = [{key: value for key, value in item.items() if key in allowed}
                    for item in completed_files if isinstance(item, dict)]
        payload = json.loads(row['payload_json'])
        result['submission'] = {key: payload[key] for key in ('accepted', 'duplicates', 'rejected', 'resume_from') if key in payload}
        result['last_event_id'] = con.execute('SELECT COALESCE(MAX(sequence),0) FROM job_events WHERE job_id=?',
                                              (row['id'],)).fetchone()[0]
        return result

    def _event(self, con, job_id, event_type, payload):
        sequence = con.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0]
        con.execute('INSERT INTO job_events(job_id,sequence,event_type,payload_json,created_at) VALUES (?,?,?,?,?)',
                    (job_id, sequence, event_type, _safe(payload, max_bytes=65536), self.now()))

    def enqueue(self, actor_id, session_id, scope_kind, scope_id, operation, payload,
                input_version, idempotency_key, *, deadline_seconds=900, max_attempts=None, request_fingerprint=None, con=None):
        if not self.accepting:
            raise DomainError('SERVICE_STOPPING', '서버가 종료 중입니다. 잠시 후 다시 시도해 주세요.', 503)
        if not isinstance(scope_id, str) or not scope_id.strip() or len(scope_id) > 200:
            raise DomainError('INVALID_JOB_SCOPE', '작업 범위를 확인해 주세요.')
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
            raise DomainError('IDEMPOTENCY_REQUIRED', '8~200자의 Idempotency-Key가 필요합니다.', 400)
        if not isinstance(operation, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', operation) or operation == 'cleanup_trip':
            raise DomainError('INVALID_JOB_OPERATION', '작업 종류를 확인해 주세요.')
        if scope_kind == 'personal_trip' and operation not in PERSONAL_OPERATIONS:
            raise DomainError('INVALID_JOB_OPERATION', '지원하지 않는 여행 작업입니다.')
        if not isinstance(input_version, int) or input_version < 0 or not 1 <= deadline_seconds <= 86400:
            raise DomainError('INVALID_JOB_INPUT', '작업 버전과 실행 기한을 확인해 주세요.')
        attempts = self.max_attempts if max_attempts is None else max_attempts
        if not 1 <= attempts <= 10:
            raise DomainError('INVALID_JOB_INPUT', '작업 시도 횟수를 확인해 주세요.')
        payload_json = _safe(payload)
        request_hash = request_fingerprint or hashlib.sha256(_json({'payload': payload, 'input_version': input_version}).encode()).hexdigest()
        if not isinstance(request_hash, str) or not 1 <= len(request_hash) <= 256:
            raise DomainError('INVALID_JOB_INPUT', '요청 지문 형식을 확인해 주세요.')
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        with self._transaction(con) as con:
            trip = self._scope(con, actor_id, session_id, scope_kind, scope_id)
            existing = con.execute('SELECT * FROM idempotency_keys WHERE actor_id=? AND scope_kind=? AND scope_id=? AND operation=? AND key_hash=?',
                (actor_id, scope_kind, scope_id, operation, key_hash)).fetchone()
            # Retain keys without automatic expiry deletion: even delayed network retries cannot create duplicate charges.
            if existing:
                if existing['request_hash'] != request_hash:
                    raise DomainError('IDEMPOTENCY_CONFLICT', '같은 실행 키에 다른 입력이 사용되었습니다.', 409)
                return self._public(con, con.execute('SELECT * FROM jobs WHERE id=?', (existing['job_id'],)).fetchone())
            parent_id = payload.get('resume_from') if isinstance(payload, dict) else None
            if parent_id:
                if not isinstance(parent_id, str):
                    raise DomainError('INVALID_JOB_INPUT', '재시도할 작업을 확인해 주세요.')
                parent = self._authorized_job(con, parent_id, actor_id, session_id)
                if parent['scope_kind'] != scope_kind or parent['scope_id'] != scope_id or parent['operation'] != operation:
                    raise DomainError('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
                if parent['state'] not in {'partial', 'failed', 'cancelled'}:
                    raise DomainError('JOB_NOT_RETRYABLE', '실패한 작업에서 다시 실행할 항목을 확인해 주세요.', 409)
                child = con.execute("SELECT * FROM jobs WHERE actor_id=? AND scope_kind=? AND scope_id=? AND operation=? AND json_extract(payload_json,'$.resume_from')=?",
                    (actor_id, scope_kind, scope_id, operation, parent_id)).fetchone()
                if child:
                    # One retry intent per parent, even if another browser or an
                    # old screen invents a new key. A later retry chains from the
                    # failed child and receives fresh budget reservations.
                    con.execute('INSERT INTO idempotency_keys VALUES (?,?,?,?,?,?,?,?,?)',
                        (actor_id, scope_kind, scope_id, operation, key_hash, request_hash, child['id'], self.later(30*86400), self.now()))
                    return self._public(con, child)
            if scope_kind == 'personal_trip' and operation == 'documents' and isinstance(payload, dict):
                accepted = payload.get('accepted', [])
                if accepted:
                    # Content-addressed source_documents already unify equal
                    # uploads in a trip. Deduplicate their work as well, even
                    # when a lost-response screen generates a different key.
                    pending = {}
                    active_jobs = con.execute("SELECT * FROM jobs WHERE actor_id=? AND trip_id=? AND operation='documents' AND state IN ('queued','running') ORDER BY created_at,id",
                        (actor_id, scope_id)).fetchall()
                    for active_job in active_jobs:
                        for entry in json.loads(active_job['payload_json']).get('accepted', []):
                            pending.setdefault(entry['document_id'], active_job)
                    fresh, in_flight = [], []
                    for entry in accepted:
                        active_job = pending.get(entry['document_id'])
                        if active_job is None:
                            fresh.append(entry)
                        else:
                            in_flight.append({'document_id': entry['document_id'], 'filename': entry.get('filename', ''),
                                              'job_id': active_job['id'], 'state': 'processing'})
                    if in_flight:
                        if len(accepted) == len(in_flight) == 1 and not payload.get('duplicates') and not payload.get('rejected'):
                            active_job = pending[accepted[0]['document_id']]
                            con.execute('INSERT INTO idempotency_keys VALUES (?,?,?,?,?,?,?,?,?)',
                                (actor_id, scope_kind, scope_id, operation, key_hash, request_hash, active_job['id'], self.later(30*86400), self.now()))
                            return self._public(con, active_job)
                        payload = {**payload, 'accepted': fresh, 'duplicates': [*payload.get('duplicates', []), *in_flight]}
                        payload_json = _safe(payload)
            job_id = 'job_' + uuid.uuid4().hex
            now = self.now()
            con.execute('INSERT INTO jobs(id,actor_id,session_id,scope_kind,scope_id,owner_id,trip_id,operation,state,payload_json,payload_hash,input_version,max_attempts,available_at,deadline_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (job_id, actor_id, session_id, scope_kind, scope_id, actor_id if trip else None,
                 scope_id if trip else None, operation, 'queued', payload_json, request_hash, input_version,
                 attempts, now, self.later(deadline_seconds), now, now))
            con.execute('INSERT INTO idempotency_keys VALUES (?,?,?,?,?,?,?,?,?)',
                (actor_id, scope_kind, scope_id, operation, key_hash, request_hash, job_id, self.later(30*86400), now))
            self._event(con, job_id, 'progress', {'state': 'queued', 'stage': 'queued', 'done': 0})
            return self._public(con, con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def lookup(self, actor_id, session_id, scope_kind, scope_id, operation, idempotency_key, request_fingerprint):
        """Check the immutable client intent before staging files or derived metadata."""
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
            raise DomainError('IDEMPOTENCY_REQUIRED', '8~200자의 Idempotency-Key가 필요합니다.', 400)
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        with self.db.connect() as con:
            self._scope(con, actor_id, session_id, scope_kind, scope_id)
            row = con.execute('SELECT * FROM idempotency_keys WHERE actor_id=? AND scope_kind=? AND scope_id=? AND operation=? AND key_hash=?',
                              (actor_id, scope_kind, scope_id, operation, key_hash)).fetchone()
            if not row:
                return None
            if row['request_hash'] != request_fingerprint:
                raise DomainError('IDEMPOTENCY_CONFLICT', '같은 실행 키에 다른 입력이 사용되었습니다.', 409)
            return self._public(con, con.execute('SELECT * FROM jobs WHERE id=?', (row['job_id'],)).fetchone())

    def _authorized_job(self, con, job_id, actor_id, session_id):
        self._user(con, actor_id, session_id)
        row = con.execute('SELECT * FROM jobs WHERE id=? AND actor_id=? AND system_cleanup=0', (job_id, actor_id)).fetchone()
        if not row:
            raise DomainError('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
        self._scope(con, actor_id, session_id, row['scope_kind'], row['scope_id'])
        if row['operation']=='review_collection':
            available=con.execute("SELECT 1 FROM review_collection_runs r JOIN provider_policies p ON p.id=r.policy_id JOIN place_identities i ON i.id=r.place_id WHERE r.job_id=? AND r.deleted_at IS NULL AND r.expires_at>? AND p.status='active' AND p.expires_at>? AND i.deleted_at IS NULL AND i.identity_status='verified'",(job_id,self.now(),self.now())).fetchone()
            if not available: raise DomainError('NOT_FOUND','자료를 찾을 수 없습니다.',404)
        return row

    def get(self, job_id, actor_id, session_id):
        with self.db.connect() as con:
            return self._public(con, self._authorized_job(con, job_id, actor_id, session_id))

    def list_for_trip(self, trip_id, actor_id, session_id, *, limit=100, offset=0):
        if not isinstance(offset, int) or offset < 0:
            raise DomainError('INVALID_CURSOR', '작업 목록 위치를 확인해 주세요.', 400)
        with self.db.connect() as con:
            self._scope(con, actor_id, session_id, 'personal_trip', trip_id)
            return [self._public(con, row) for row in con.execute('SELECT * FROM jobs WHERE actor_id=? AND trip_id=? AND system_cleanup=0 ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                (actor_id, trip_id, max(1, min(limit, 200)), offset)).fetchall()]

    def count_for_trip(self, trip_id, actor_id, session_id):
        with self.db.connect() as con:
            self._scope(con, actor_id, session_id, 'personal_trip', trip_id)
            return con.execute('SELECT COUNT(*) FROM jobs WHERE actor_id=? AND trip_id=? AND system_cleanup=0',
                               (actor_id, trip_id)).fetchone()[0]

    def events(self, job_id, actor_id, session_id, *, after=0, limit=100):
        if not isinstance(after, int) or after < 0:
            raise DomainError('INVALID_EVENT_ID', '이벤트 위치를 확인해 주세요.', 400)
        with self.db.connect() as con:
            self._authorized_job(con, job_id, actor_id, session_id)
            return [{'id': row['sequence'], 'sequence': row['sequence'], 'event': row['event_type'],
                     'data': json.loads(row['payload_json']), 'created_at': row['created_at']}
                    for row in con.execute('SELECT * FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                        (job_id, after, max(1, min(limit, 500)))).fetchall()]

    def cancel(self, job_id, actor_id, session_id):
        with self._transaction() as con:
            row = self._authorized_job(con, job_id, actor_id, session_id)
            if row['state'] in TERMINAL:
                return self._public(con, row)
            if not row['cancel_requested_at']:
                con.execute('UPDATE jobs SET cancel_requested_at=?,updated_at=? WHERE id=?', (self.now(), self.now(), job_id))
                self._event(con, job_id, 'progress', {'state': row['state'], 'cancel_requested': True})
            if row['state'] == 'queued':
                self._terminal(con, row, 'cancelled', {}, 'USER_CANCELLED')
            return self._public(con, con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def cancel_trip(self, trip_id, *, con=None):
        with self._transaction(con) as connection:
            rows = connection.execute("SELECT * FROM jobs WHERE trip_id=? AND system_cleanup=0 AND state IN ('queued','running')", (trip_id,)).fetchall()
            for row in rows:
                connection.execute('UPDATE jobs SET cancel_requested_at=?,updated_at=? WHERE id=?', (self.now(), self.now(), row['id']))
                if row['state'] == 'queued':
                    self._terminal(connection, row, 'cancelled', {}, 'TRIP_DELETED')
                else:
                    self._event(connection, row['id'], 'progress', {'cancel_requested': True, 'reason': 'TRIP_DELETED'})

    def enqueue_cleanup(self, actor_id, session_id, trip_id, *, con=None):
        with self._transaction(con) as connection:
            trip = self._scope(connection, actor_id, session_id, 'personal_trip', trip_id, allow_deleted=True)
            return self._enqueue_cleanup(connection, trip, actor_id, session_id)

    def _enqueue_cleanup(self, connection, trip, actor_id, session_id):
        trip_id = trip['id']
        tombstone = connection.execute("SELECT * FROM deletion_tombstones WHERE target_type='trip' AND target_id=?", (trip_id,)).fetchone()
        if not trip['deleted_at'] or not tombstone or actor_id != trip['owner_id']:
            raise DomainError('INVALID_CLEANUP', '접근 차단이 완료된 여행만 정리할 수 있습니다.', 409)
        existing = connection.execute('SELECT * FROM deletion_receipts WHERE trip_id=?', (trip_id,)).fetchone()
        if existing:
            return self._receipt(existing)
        self.cancel_trip(trip_id, con=connection)
        jid, rid, now = 'job_' + uuid.uuid4().hex, 'deletion_' + uuid.uuid4().hex, self.now()
        connection.execute("INSERT INTO jobs(id,actor_id,session_id,scope_kind,scope_id,owner_id,trip_id,operation,state,payload_json,payload_hash,input_version,deletion_epoch,max_attempts,available_at,deadline_at,system_cleanup,created_at,updated_at) VALUES (?,?,?,'personal_trip',?,?,?,'cleanup_trip','queued','{}','cleanup',?,?,?,?,?,1,?,?)",
            (jid, actor_id, session_id, trip_id, actor_id, trip_id, trip['version'], tombstone['deletion_epoch'], 10, now, self.later(86400), now, now))
        connection.execute('INSERT INTO deletion_receipts VALUES (?,?,?,?,?,?,?,?)', (rid, actor_id, trip_id, jid, 'queued', None, now, now))
        self._event(connection, jid, 'progress', {'state': 'queued', 'stage': 'cleanup'})
        return self._receipt(connection.execute('SELECT * FROM deletion_receipts WHERE id=?', (rid,)).fetchone())

    def recover_cleanup(self):
        """Continue already-authorized deletion after a crash between tombstone/job.

        This system capability only schedules removal. It cannot create personal
        results or read deleted travel contents through normal job APIs.
        """
        with self._transaction() as con:
            trips = con.execute("SELECT t.* FROM trips t JOIN deletion_tombstones d ON d.target_type='trip' AND d.target_id=t.id "
                "LEFT JOIN deletion_receipts r ON r.trip_id=t.id WHERE t.deleted_at IS NOT NULL AND r.id IS NULL").fetchall()
            return [self._enqueue_cleanup(con, trip, trip['owner_id'], 'system_cleanup') for trip in trips]

    @staticmethod
    def _receipt(row):
        return {'receipt_id': row['id'], 'state': row['state'], 'error_code': row['error_code'], 'updated_at': row['updated_at']}

    def deletion_receipt(self, receipt_id, actor_id, session_id):
        with self.db.connect() as con:
            self._user(con, actor_id, session_id)
            row = con.execute('SELECT * FROM deletion_receipts WHERE id=? AND actor_id=?', (receipt_id, actor_id)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
            return self._receipt(row)

    def acquire_dispatcher(self, owner):
        with self._transaction() as con:
            now = self.now()
            con.execute("INSERT INTO dispatcher_leases(name,owner,lease_expires_at,heartbeat_at) VALUES ('main',?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,lease_expires_at=excluded.lease_expires_at,heartbeat_at=excluded.heartbeat_at "
                "WHERE dispatcher_leases.owner=excluded.owner OR dispatcher_leases.lease_expires_at<=excluded.heartbeat_at",
                (owner, self.later(self.lease_seconds), now))
            return con.execute("SELECT owner FROM dispatcher_leases WHERE name='main'").fetchone()['owner'] == owner

    def release_dispatcher(self, owner):
        with self._transaction() as con:
            con.execute("DELETE FROM dispatcher_leases WHERE name='main' AND owner=?", (owner,))

    def _release_writer(self, con, row):
        if row['trip_id']:
            con.execute('DELETE FROM trip_index_writers WHERE trip_id=? AND holder_job_id=? AND fencing_token=?',
                        (row['trip_id'], row['id'], row['fencing_token']))

    def _terminal(self, con, row, state, result, error_code=None, retryable=False):
        now = self.now()
        con.execute('UPDATE jobs SET state=?,result_json=?,error_code=?,retryable=?,stage=?,finished_at=?,updated_at=?,lease_owner=NULL,lease_expires_at=NULL WHERE id=?',
                    (state, _safe(result), error_code, int(retryable), state, now, now, row['id']))
        self._release_writer(con, row)
        event_type = {'succeeded': 'completed', 'partial': 'partial_result', 'failed': 'failed', 'cancelled': 'cancelled'}[state]
        self._event(con, row['id'], event_type, {'state': state, 'error_code': error_code, 'retryable': bool(retryable)})
        if row['system_cleanup']:
            con.execute('UPDATE deletion_receipts SET state=?,error_code=?,updated_at=? WHERE job_id=?', (state, error_code, now, row['id']))

    def _recover(self, con):
        now = self.now()
        rows = con.execute("SELECT * FROM jobs WHERE state='running' AND lease_expires_at<=?", (now,)).fetchall()
        for row in rows:
            trip = con.execute('SELECT deleted_at FROM trips WHERE id=?', (row['trip_id'],)).fetchone() if row['trip_id'] else None
            if row['cancel_requested_at'] or (trip and trip['deleted_at'] and not row['system_cleanup']):
                self._terminal(con, row, 'cancelled', {}, 'USER_CANCELLED' if row['cancel_requested_at'] else 'TRIP_DELETED')
            elif row['attempt'] >= row['max_attempts'] or row['deadline_at'] <= now:
                self._terminal(con, row, 'failed', {}, 'ATTEMPTS_EXHAUSTED' if row['attempt'] >= row['max_attempts'] else 'JOB_DEADLINE', True)
            else:
                con.execute("UPDATE jobs SET state='queued',available_at=?,lease_owner=NULL,lease_expires_at=NULL,error_code='WORKER_LOST',stage='recovering',updated_at=? WHERE id=?", (now, now, row['id']))
                self._release_writer(con, row)
                self._event(con, row['id'], 'progress', {'state': 'queued', 'stage': 'recovering', 'checkpoint_available': row['checkpoint_json'] != '{}'})

    def claim(self, owner):
        if not self.accepting or not self.acquire_dispatcher(owner):
            return None
        with self._transaction() as con:
            now = self.now()
            leader = con.execute("SELECT * FROM dispatcher_leases WHERE name='main' AND owner=? AND lease_expires_at>?", (owner, now)).fetchone()
            if not leader:
                return None
            from src.operations.controls import read as read_controls
            if read_controls(con)['mode'] != 'normal':
                return None
            self._recover(con)
            now = self.now()
            if con.execute("SELECT 1 FROM jobs WHERE state='running' AND lease_expires_at>? LIMIT 1", (now,)).fetchone():
                return None
            rows = con.execute("SELECT * FROM jobs WHERE state='queued' AND available_at<=? ORDER BY system_cleanup DESC,created_at,id", (now,)).fetchall()
            for row in rows:
                if row['deadline_at'] <= now or row['attempt'] >= row['max_attempts']:
                    self._terminal(con, row, 'failed', {}, 'JOB_DEADLINE' if row['deadline_at'] <= now else 'ATTEMPTS_EXHAUSTED', True)
                    continue
                try:
                    self._validate_scope_for_worker(con, row)
                except DomainError as exc:
                    self._terminal(con, row, 'cancelled' if exc.code == 'NOT_FOUND' else 'failed', {}, 'TRIP_DELETED' if exc.code == 'NOT_FOUND' else exc.code)
                    continue
                expiry = self.later(self.lease_seconds)
                token = row['fencing_token'] + 1
                changed = con.execute("UPDATE jobs SET state='running',attempt=attempt+1,fencing_token=?,lease_owner=?,lease_expires_at=?,heartbeat_at=?,started_at=COALESCE(started_at,?),updated_at=?,stage='running',error_code=NULL,retryable=0 WHERE id=? AND state='queued' AND fencing_token=?",
                    (token, owner, expiry, now, now, now, row['id'], row['fencing_token'])).rowcount
                if not changed:
                    continue
                if row['trip_id']:
                    lock = con.execute('SELECT * FROM trip_index_writers WHERE trip_id=?', (row['trip_id'],)).fetchone()
                    if lock and lock['lease_expires_at'] > now and lock['holder_job_id'] != row['id']:
                        raise DomainError('WRITER_BUSY', '같은 여행의 갱신 작업이 진행 중입니다.', 409)
                    con.execute('INSERT INTO trip_index_writers(trip_id,holder_job_id,fencing_token,lease_expires_at) VALUES (?,?,?,?) ON CONFLICT(trip_id) DO UPDATE SET holder_job_id=excluded.holder_job_id,fencing_token=excluded.fencing_token,lease_expires_at=excluded.lease_expires_at', (row['trip_id'], row['id'], token, expiry))
                self._event(con, row['id'], 'progress', {'state': 'running', 'stage': 'running', 'attempt': row['attempt'] + 1})
                if row['system_cleanup']:
                    con.execute("UPDATE deletion_receipts SET state='running',updated_at=? WHERE job_id=?", (now, row['id']))
                return self._decode(con.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
            return None

    def _validate_scope_for_worker(self, con, row):
        if row['system_cleanup']:
            trip = con.execute('SELECT * FROM trips WHERE id=? AND owner_id=? AND deleted_at IS NOT NULL', (row['trip_id'], row['actor_id'])).fetchone()
            tombstone = con.execute("SELECT 1 FROM deletion_tombstones WHERE target_type='trip' AND target_id=?", (row['trip_id'],)).fetchone()
            receipt = con.execute('SELECT 1 FROM deletion_receipts WHERE job_id=?', (row['id'],)).fetchone()
            if not trip or not tombstone or not receipt:
                raise DomainError('INVALID_CLEANUP', '삭제 정리 권한을 확인할 수 없습니다.', 409)
            return trip
        return self._scope(con, row['actor_id'], row['session_id'], row['scope_kind'], row['scope_id'])

    def _lease(self, con, job_id, fence):
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['state'] != 'running' or row['fencing_token'] != fence or row['lease_expires_at'] <= self.now():
            raise DomainError('LEASE_LOST', '작업 실행 권한이 만료되었습니다.', 409)
        if row['trip_id']:
            lock = con.execute('SELECT 1 FROM trip_index_writers WHERE trip_id=? AND holder_job_id=? AND fencing_token=? AND lease_expires_at>?',
                (row['trip_id'], job_id, fence, self.now())).fetchone()
            if not lock:
                raise DomainError('LEASE_LOST', '여행 갱신 권한이 만료되었습니다.', 409)
        return row

    def guard(self, job_id, fence, con=None, *, require_version=False, allow_cancel=False):
        with self._transaction(con) as connection:
            row = self._lease(connection, job_id, fence)
            trip = self._validate_scope_for_worker(connection, row)
            if row['deadline_at'] <= self.now():
                raise DomainError('JOB_DEADLINE', '작업 실행 기한이 지났습니다.', 409)
            if row['cancel_requested_at'] and not allow_cancel:
                raise DomainError('JOB_CANCELLED', '작업 취소가 요청되었습니다.', 409)
            if require_version and trip and trip['version'] != row['input_version']:
                raise DomainError('VERSION_CONFLICT', '여행이 변경되어 다시 확인해야 합니다.', 409)
            return self._decode(row)

    def heartbeat(self, job_id, fence, owner):
        with self._transaction() as con:
            row = self._lease(con, job_id, fence)
            if row['lease_owner'] != owner:
                raise DomainError('LEASE_LOST', '작업 실행 권한이 만료되었습니다.', 409)
            leader = con.execute("SELECT 1 FROM dispatcher_leases WHERE name='main' AND owner=? AND lease_expires_at>?", (owner, self.now())).fetchone()
            if not leader:
                raise DomainError('LEASE_LOST', '작업 실행 권한이 만료되었습니다.', 409)
            now, expiry = self.now(), self.later(self.lease_seconds)
            con.execute('UPDATE jobs SET heartbeat_at=?,lease_expires_at=?,updated_at=? WHERE id=?', (now, expiry, now, job_id))
            if row['trip_id']:
                con.execute('UPDATE trip_index_writers SET lease_expires_at=? WHERE trip_id=? AND holder_job_id=? AND fencing_token=?', (expiry, row['trip_id'], job_id, fence))

    def checkpoint(self, job_id, fence, data, *, stage=None, done=None, total=None, con=None):
        with self._transaction(con) as connection:
            row = self.guard(job_id, fence, connection)
            merged = {**row['checkpoint'], **data}
            self._update_progress(connection, row, stage, done, total)
            connection.execute('UPDATE jobs SET checkpoint_json=? WHERE id=?', (_safe(merged), job_id))
            return merged

    def _update_progress(self, con, row, stage, done, total):
        stage = stage if stage is not None else row['stage']
        done = done if done is not None else row['done_count']
        total = total if total is not None else row['total_count']
        if not isinstance(stage, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', stage) or not isinstance(done, int) or done < 0 or (total is not None and (not isinstance(total, int) or total < done)):
            raise DomainError('INVALID_PROGRESS', '진행 단계와 처리 건수를 확인해 주세요.')
        con.execute('UPDATE jobs SET stage=?,done_count=?,total_count=?,updated_at=? WHERE id=?', (stage, done, total, self.now(), row['id']))
        self._event(con, row['id'], 'progress', {'state': 'running', 'stage': stage, 'done': done, 'total': total})

    def progress(self, job_id, fence, stage, *, done=None, total=None):
        with self._transaction() as con:
            row = self.guard(job_id, fence, con)
            self._update_progress(con, row, stage, done, total)

    def finish(self, job_id, fence, *, state='succeeded', result=None, error_code=None, retryable=False, con=None):
        if state not in TERMINAL:
            raise ValueError('finish requires a terminal state')
        with self._transaction(con) as connection:
            row = self._lease(connection, job_id, fence)
            if state in {'succeeded', 'partial'}:
                self.guard(job_id, fence, connection)
            self._terminal(connection, row, state, result or {}, error_code, retryable)
            return self._public(connection, connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def retry(self, job_id, fence, *, error_code, delay_seconds=1):
        with self._transaction() as con:
            row = self._lease(con, job_id, fence)
            if row['cancel_requested_at']:
                self._terminal(con, row, 'cancelled', {}, 'USER_CANCELLED')
            elif row['attempt'] >= row['max_attempts'] or self.later(min(max(delay_seconds, 0), 300)) >= row['deadline_at']:
                self._terminal(con, row, 'failed', {}, error_code, True)
            else:
                con.execute("UPDATE jobs SET state='queued',available_at=?,lease_owner=NULL,lease_expires_at=NULL,error_code=?,retryable=1,stage='retry_wait',updated_at=? WHERE id=?", (self.later(min(max(delay_seconds, 0), 300)), error_code, self.now(), job_id))
                self._release_writer(con, row)
                self._event(con, job_id, 'progress', {'state': 'queued', 'stage': 'retry_wait', 'error_code': error_code})

    def health(self):
        with self.db.connect() as con:
            leader = con.execute("SELECT heartbeat_at,lease_expires_at FROM dispatcher_leases WHERE name='main'").fetchone()
            states = {row['state']: row['n'] for row in con.execute('SELECT state,COUNT(*) n FROM jobs GROUP BY state')}
            return {'dispatcher_alive': bool(leader and leader['lease_expires_at'] > self.now()),
                    'heartbeat_at': leader['heartbeat_at'] if leader else None, 'jobs': states}

    def abandon(self, job_id, fence, owner):
        """Fence a stopped thread immediately; another dispatcher resumes its checkpoint."""
        with self._transaction() as con:
            now = self.now()
            con.execute("UPDATE jobs SET lease_expires_at=?,updated_at=? WHERE id=? AND fencing_token=? AND lease_owner=? AND state='running'",
                        (now, now, job_id, fence, owner))
            con.execute('UPDATE trip_index_writers SET lease_expires_at=? WHERE holder_job_id=? AND fencing_token=?',
                        (now, job_id, fence))
