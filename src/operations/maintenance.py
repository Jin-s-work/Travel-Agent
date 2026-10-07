"""Lease-aware maintenance; every process supervises, only the current leader writes."""
import asyncio
from datetime import datetime
import logging
import re
import threading
import time

from src.foundation.repository import DomainError

log = logging.getLogger(__name__)


class Maintenance:
    def __init__(self, app, *, interval=30, storage_interval=1800):
        self.app = app
        self.db, self.jobs = app.state.db, app.state.jobs
        self.owner = app.state.dispatcher.owner
        self.interval, self.storage_interval = interval, storage_interval
        self._stopping = threading.Event()
        self._task = None
        self._storage_due = 0

    def guard(self, *, con=None):
        if self._stopping.is_set():
            raise DomainError('LEASE_LOST', '유지보수 실행 권한이 만료되었습니다.', 409)
        if con is None:
            with self.db.connect() as own:
                return self.guard(con=own)
        row = con.execute("SELECT 1 FROM dispatcher_leases WHERE name='main' AND owner=? AND lease_expires_at>?",
                          (self.owner, self.jobs.now())).fetchone()
        if not row:
            raise DomainError('LEASE_LOST', '유지보수 실행 권한이 만료되었습니다.', 409)

    def _record(self, state, code=None):
        stamp = self.jobs.now()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.guard(con=con)
            con.execute("INSERT INTO maintenance_status(name,owner,state,updated_at) VALUES('main',?,?,?) "
                        'ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,state=excluded.state,updated_at=excluded.updated_at',
                        (self.owner, state, stamp))
            column = {'running': 'last_started_at', 'succeeded': 'last_succeeded_at', 'failed': 'last_failed_at'}[state]
            con.execute(f"UPDATE maintenance_status SET {column}=?,error_code=? WHERE name='main'", (stamp, code))

    def _product(self):
        from src.product.events import purge
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.guard(con=con)
            purge(con)

    def _storage(self):
        if self.app.state.documents.objects:
            result = self.app.state.documents.objects.reconcile(guard=self.guard)
            if result['failed']:
                raise DomainError('STORAGE_CLEANUP_INCOMPLETE', '일부 원문 정리를 다시 시도해야 합니다.', 503)

    def _reviews(self):
        self.app.state.reviews.purge(guard=self.guard)
        result = self.app.state.reviews.cleanup_remote(guard=self.guard)
        if any(item['state'] == 'failed' for item in result['items']):
            raise DomainError('REVIEW_CLEANUP_INCOMPLETE', '일부 리뷰 정리를 확인해야 합니다.', 503)

    def run_once(self):
        try:
            self.guard()
            self._record('running')
        except DomainError as exc:
            if exc.code == 'LEASE_LOST':
                self._storage_due = 0
                return False
            raise
        errors = []
        operations = [self._product, self._reviews]
        if time.monotonic() >= self._storage_due:
            self._storage_due = time.monotonic() + self.storage_interval
            operations.append(self._storage)
        for operation in operations:
            try:
                self.guard()
                operation()
            except DomainError as exc:
                if exc.code == 'LEASE_LOST':
                    self._storage_due = 0
                    return False
                errors.append(exc.code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', exc.code) else 'MAINTENANCE_FAILED')
            except Exception:
                errors.append('MAINTENANCE_FAILED')
        # A storage retry waits longer than the 30-second review cycle. Do not
        # report recovery merely because that cycle did not run the sweep.
        if self.app.state.documents.objects:
            with self.db.connect() as con:
                pending_failure=con.execute('SELECT 1 FROM storage_deletion_receipts WHERE completed_at IS NULL AND last_error_code IS NOT NULL LIMIT 1').fetchone()
            if pending_failure and 'STORAGE_CLEANUP_INCOMPLETE' not in errors:
                errors.append('STORAGE_CLEANUP_INCOMPLETE')
        try:
            self._record('failed' if errors else 'succeeded', errors[0] if errors else None)
        except DomainError as exc:
            if exc.code != 'LEASE_LOST':
                raise
            self._storage_due = 0
            return False
        return not errors

    async def start(self):
        self._stopping.clear()
        # Preserve the existing 30-second first interval and startup/readiness.
        self._task = asyncio.create_task(self._run(), name='travel-maintenance-supervisor')

    async def _run(self):
        while not self._stopping.is_set():
            await asyncio.sleep(self.interval)
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never serialize exceptions, keys, object names or review contents.
                log.warning('maintenance_iteration_failed')

    async def stop(self):
        self._stopping.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def snapshot(self):
        with self.db.connect() as con:
            row = con.execute("SELECT * FROM maintenance_status WHERE name='main'").fetchone()
            leader = con.execute("SELECT owner,lease_expires_at FROM dispatcher_leases WHERE name='main'").fetchone()
        stamp = self.jobs.now()
        current_owner = leader['owner'] if leader and leader['lease_expires_at'] > stamp else None
        data = dict(row) if row else {'owner': None, 'state': 'not_started', 'last_started_at': None,
                                      'last_succeeded_at': None, 'last_failed_at': None, 'error_code': None, 'updated_at': None}
        anchor = data.get('last_started_at')
        delay = max(0, (datetime.fromisoformat(stamp) - datetime.fromisoformat(anchor)).total_seconds()) if anchor else None
        healthy = bool(current_owner and data['owner'] == current_owner and data['state'] in {'running', 'succeeded'}
                       and delay is not None and delay <= max(self.interval * 3, self.jobs.lease_seconds * 2))
        return {**data, 'current_owner': current_owner, 'delay_seconds': round(delay, 3) if delay is not None else None,
                'healthy': healthy, 'interval_seconds': self.interval}
