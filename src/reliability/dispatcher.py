"""A lifespan-owned single dispatcher; synchronous handlers run off the event loop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import random
import threading
import uuid

from src.foundation.repository import DomainError

log = logging.getLogger(__name__)


class RetryableJobError(Exception):
    def __init__(self, code, *, retry_after=None):
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


class JobContext:
    def __init__(self, jobs, job, owner):
        self.jobs, self.job, self.owner = jobs, job, owner
        self.job_id, self.fence = job['id'], job['fencing_token']
        self.lost = threading.Event()

    def guard(self, *, require_version=False, con=None):
        if self.lost.is_set():
            raise DomainError('LEASE_LOST', '작업 실행 권한이 만료되었습니다.', 409)
        return self.jobs.guard(self.job_id, self.fence, con, require_version=require_version)

    def check_cancelled(self):
        self.guard()

    def checkpoint(self, data, *, stage=None, done=None, total=None):
        self.guard()
        value = self.jobs.checkpoint(self.job_id, self.fence, data, stage=stage, done=done, total=total)
        self.job['checkpoint'] = value
        return value

    def progress(self, stage, *, done=None, total=None):
        self.guard()
        self.jobs.progress(self.job_id, self.fence, stage, done=done, total=total)


class Dispatcher:
    def __init__(self, jobs, handler, *, poll_seconds=.25, heartbeat_seconds=20, shutdown_seconds=5):
        if not 0 < heartbeat_seconds < jobs.lease_seconds:
            raise ValueError('Heartbeat interval must be shorter than the job lease')
        self.jobs, self.handler = jobs, handler
        self.poll_seconds, self.heartbeat_seconds, self.shutdown_seconds = poll_seconds, heartbeat_seconds, shutdown_seconds
        self.owner = 'dispatcher_' + uuid.uuid4().hex
        self._task = None
        self._stopping = asyncio.Event()
        self._context = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='travel-job')

    async def start(self):
        if self._task and not self._task.done():
            return
        self.jobs.accepting = True
        self._stopping.clear()
        await asyncio.to_thread(self.jobs.recover_cleanup)
        self._task = asyncio.create_task(self._run(), name='travel-dispatcher')

    async def stop(self):
        self.jobs.accepting = False
        self._stopping.set()
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=self.shutdown_seconds)
            except asyncio.TimeoutError:
                if self._context:
                    self._context.lost.set()
                    await asyncio.to_thread(self.jobs.abandon, self._context.job_id, self._context.fence, self.owner)
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        await asyncio.to_thread(self.jobs.release_dispatcher, self.owner)
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _sleep(self, seconds):
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run(self):
        while not self._stopping.is_set():
            try:
                job = await asyncio.to_thread(self.jobs.claim, self.owner)
                if job:
                    await self._execute(job)
                else:
                    await self._sleep(self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never log handler exceptions or serialized payloads (mail may be present).
                log.warning('dispatcher_iteration_failed')
                await self._sleep(self.poll_seconds)

    async def _heartbeat(self, context):
        while not context.lost.is_set():
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                owned = await asyncio.to_thread(self.jobs.acquire_dispatcher, self.owner)
                if not owned:
                    raise DomainError('LEASE_LOST', '실행 권한 만료', 409)
                await asyncio.to_thread(self.jobs.heartbeat, context.job_id, context.fence, self.owner)
            except Exception:
                context.lost.set()
                return

    async def _execute(self, job):
        context = self._context = JobContext(self.jobs, job, self.owner)
        pulse = asyncio.create_task(self._heartbeat(context))
        try:
            response = await asyncio.get_running_loop().run_in_executor(self._pool, self.handler, job, context)
            context.guard()
            if isinstance(response, dict) and 'state' in response and 'result' in response:
                state, result = response['state'], response['result']
            else:
                state, result = 'succeeded', response or {}
            await asyncio.to_thread(self.jobs.finish, job['id'], context.fence, state=state, result=result)
        except RetryableJobError as exc:
            if not context.lost.is_set():
                delay = min(exc.retry_after if exc.retry_after is not None else 2 ** job['attempt'] + random.random(), 300)
                await self._attempt_update(self.jobs.retry, context, error_code=exc.code, delay_seconds=delay)
        except DomainError as exc:
            if exc.code != 'LEASE_LOST' and not context.lost.is_set():
                state = 'cancelled' if exc.code in {'JOB_CANCELLED', 'NOT_FOUND'} else 'failed'
                await self._attempt_update(self.jobs.finish, context, state=state, error_code=exc.code)
        except asyncio.CancelledError:
            context.lost.set()
            raise
        except Exception:
            if not context.lost.is_set():
                await self._attempt_update(self.jobs.finish, context, state='failed', error_code='INTERNAL_ERROR')
        finally:
            pulse.cancel()
            try:
                await pulse
            except asyncio.CancelledError:
                pass
            self._context = None

    async def _attempt_update(self, method, context, **kwargs):
        try:
            await asyncio.to_thread(method, context.job_id, context.fence, **kwargs)
        except DomainError:
            # An expired worker cannot record terminal state for its successor.
            context.lost.set()
