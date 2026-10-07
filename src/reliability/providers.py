"""Durable receipts around synchronous external calls, with no hidden retries.

No exactly-once claim: a remote success followed by a crash before the receipt is
durable is an unknown charge. We stop rather than silently invoke it again.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import os
from pathlib import Path
import random
import threading
import time
from uuid import uuid4

from src.foundation.repository import DomainError
from .budget import CallContext, Budget


@dataclass(frozen=True)
class ProviderResult:
    value: object
    units: dict[str, int] | None


class DefinitelyNotSent(Exception):
    """An adapter's positive proof of a pre-send failure, not an HTTP timeout."""
    def __init__(self, *, retry_after=0):
        super().__init__('Provider request was not sent')
        self.retry_after = max(0, min(float(retry_after), 5))


class ProviderRejected(Exception):
    """A definitive rejection response, distinct from a lost success response.

    A rejection does not by itself prove zero billing. Keep its reservation
    pending and reserve a separate attempt before a permitted rate-limit retry.
    """
    def __init__(self, code, *, retryable=False, retry_after=0, status=503):
        super().__init__(code)
        self.code,self.retryable,self.status=code,retryable,status
        self.retry_after=max(0,min(float(retry_after),5))


class ProviderGateway:
    capabilities = {'remote_idempotency':False,'sdk_retries':0,'receipt_replay':True}

    def __init__(self, budget: Budget, result_dir: str | Path, *, sleeper=time.sleep):
        self.budget = budget
        self.result_dir = Path(result_dir).resolve()
        self.result_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.result_dir.chmod(0o700)
        self.sleeper = sleeper
        from src.storage.artifacts import Artifacts
        self.artifacts = Artifacts(budget.db) if budget.db.backend=='postgres' else None
        # Includes interactive questions as well as the single durable worker.
        # Synchronous callers already run in background/HTTP worker threads.
        self._external_slot = threading.BoundedSemaphore(1)

    def _path(self, record):
        call_id = record['call_id']
        if not call_id.startswith('call_') or any(c not in '0123456789abcdef' for c in call_id[5:]):
            raise DomainError('PROVIDER_RECEIPT_INVALID','공급자 결과 참조가 올바르지 않습니다.',503)
        directory = record['trip_id'] or ('research_' + hashlib.sha256((record['owner_id']+':'+record['scope_id']).encode()).hexdigest())
        if not directory or '/' in directory or '\\' in directory or directory in {'.','..'}:
            raise DomainError('PROVIDER_RECEIPT_INVALID','공급자 결과 범위가 올바르지 않습니다.',503)
        return self.result_dir / directory / (call_id + '.json')

    def _persist(self, record, result):
        path = self._path(record)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp = path.with_suffix('.' + uuid4().hex + '.tmp')
        value = {'call_id':record['call_id'],'request_hash':record['request_hash'], 'value':result.value, 'units':result.units}
        encoded = json.dumps(value, ensure_ascii=False, separators=(',',':'), allow_nan=False).encode('utf-8')
        if len(encoded) > 32 * 1024 * 1024:
            raise ValueError('Provider receipt exceeds configured bound')
        try:
            with temp.open('xb') as output:
                temp.chmod(0o600)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp,path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temp.unlink(missing_ok=True)
        return str(path.relative_to(self.result_dir))

    def _replay(self, record):
        # The deterministic path closes the artifact->SQL crash gap, even when
        # result_ref was not yet committed. We never discover arbitrary files.
        path = self._path(record)
        data = self.artifacts.read(str(path.relative_to(self.result_dir))) if self.artifacts else None
        if self.artifacts and data is None or not self.artifacts and not path.is_file():
            return None
        try:
            if (len(data) if self.artifacts else path.stat().st_size) > 32 * 1024 * 1024:
                raise ValueError('Oversized receipt')
            receipt = json.loads(data if self.artifacts else path.read_text(encoding='utf-8'))
            if receipt['call_id'] != record['call_id'] or receipt['request_hash'] != record['request_hash']:
                raise ValueError('Receipt identity mismatch')
            result = ProviderResult(receipt['value'],receipt['units'])
            if record['state'] != 'settled':
                if result.units is not None:
                    self.budget.settle(record['call_id'],result.units,result_ref=str(path.relative_to(self.result_dir)))
                else:
                    self.budget.uncertain(record['call_id'],pending=True,result_ref=str(path.relative_to(self.result_dir)),error_code='USAGE_PENDING')
            return result
        except (ValueError, KeyError, OSError, TypeError) as exc:
            raise DomainError('PROVIDER_RECEIPT_INVALID','저장된 공급자 결과를 검증할 수 없습니다.',503) from exc

    def _persist_guarded(self, record, result, context, guard):
        # Tombstone/permission writers use SQLite too. Keep the final guard and
        # atomic rename under one short local transaction (never network I/O),
        # so cleanup cannot finish and then be followed by a late new artifact.
        with self.budget.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.budget._scope(con,context)
            if guard:
                if 'con' in inspect.signature(guard).parameters:
                    guard(con=con)
                else:
                    guard()
            if self.artifacts:
                ref=str(self._path(record).relative_to(self.result_dir))
                encoded=json.dumps({'call_id':record['call_id'],'request_hash':record['request_hash'],'value':result.value,'units':result.units},ensure_ascii=False,allow_nan=False).encode()
                return self.artifacts.put(con,ref,record['trip_id'] or ref.split('/')[0],encoded)
            return self._persist(record,result)

    def run(self, context: CallContext, operation: str, call_key: str, estimated_units: dict,
            fn, *, request_hash=None, provider='openai', sku=None, guard=None,
            attempt=1, max_attempts=1, deadline=None):
        """Run tracked attempts; only proven unsent or rejected calls may retry.

        caller supplies a stable stage key, input hash, and lease/session/tombstone
        guard. Guards run both before sending and after cost settlement. Losing
        ownership never discards a real charge, only the product result.
        """
        if not request_hash:
            raise DomainError('CALL_INPUT_HASH_REQUIRED','공급자 입력 해시가 필요합니다.',422)
        max_attempts = min(max(int(max_attempts),1),3)
        if guard:
            guard()
        record = self.budget.reserve(context,provider=provider,sku=sku or operation,operation=operation,
            call_key=call_key,estimated_units=estimated_units,request_hash=request_hash,attempt=attempt)
        replay = self._replay(record)
        if replay is not None:
            if guard:
                guard()
            return replay.value
        if record['state'] in ('sent','unknown','pending_reconciliation','settled'):
            if record['state'] == 'sent':
                self.budget.uncertain(record['call_id'])
            raise DomainError('CALL_OUTCOME_UNKNOWN','공급자 응답·과금 확인이 필요하여 자동 재호출을 중지했습니다.',409)
        if record['state'] == 'released':
            raise DomainError('CALL_NOT_SENT','이 시도는 전송되지 않았습니다. 새 시도로 재시도해 주세요.',503)
        try:
            if deadline is not None and time.time() >= deadline:
                raise DomainError('DEADLINE_EXCEEDED','작업 실행 기한이 지났습니다.',409)
            if guard:
                guard()
        except Exception:
            self.budget.release_unsent(record['call_id'],reason_code='GUARD_REJECTED')
            raise
        saved_path = None
        try:
            with self._external_slot:
                try:
                    if deadline is not None and time.time() >= deadline:
                        raise DomainError('DEADLINE_EXCEEDED','작업 실행 기한이 지났습니다.',409)
                    if guard:
                        guard()
                except Exception:
                    self.budget.release_unsent(record['call_id'],reason_code='GUARD_REJECTED')
                    raise
                try:
                    from src.operations.controls import external_guard
                    external_guard(self.budget.db,provider)
                except DomainError:
                    self.budget.release_unsent(record['call_id'],reason_code='OPERATOR_PAUSED')
                    raise
                self.budget.mark_sent(record['call_id'])
                result = fn()
            if not isinstance(result,ProviderResult):
                raise TypeError('Provider functions must return ProviderResult with observed usage')
            if guard:
                try:
                    guard()
                except Exception:
                    # A cancelled/deleted/stale worker may still incur a bill.
                    # Settle that bill without resurrecting a response artifact.
                    if result.units is not None:
                        self.budget.settle(record['call_id'],result.units)
                    else:
                        self.budget.uncertain(record['call_id'],pending=True,error_code='USAGE_PENDING')
                    raise
            try:
                ref = self._persist_guarded(record,result,context,guard)
            except DomainError:
                if result.units is not None:
                    self.budget.settle(record['call_id'],result.units)
                else:
                    self.budget.uncertain(record['call_id'],pending=True,error_code='USAGE_PENDING')
                raise
            saved_path = self._path(record)
            if result.units is None:
                self.budget.uncertain(record['call_id'],pending=True,result_ref=ref,error_code='USAGE_PENDING')
            else:
                self.budget.settle(record['call_id'],result.units,result_ref=ref)
        except DefinitelyNotSent as exc:
            self.budget.release_unsent(record['call_id'],reason_code='TRANSPORT_NOT_SENT',explicitly_unsent=True)
            delay = min(5, max(exc.retry_after, (2 ** (attempt-1)) * .1 + random.random()*.1))
            if attempt < max_attempts and (deadline is None or time.time()+delay < deadline):
                self.sleeper(delay)
                return self.run(context,operation,call_key,estimated_units,fn,request_hash=request_hash,
                    provider=provider,sku=sku,guard=guard,attempt=attempt+1,max_attempts=max_attempts,deadline=deadline)
            raise DomainError('PROVIDER_UNAVAILABLE','공급자에 요청을 전송하지 못했습니다.',503) from None
        except ProviderRejected as exc:
            self.budget.uncertain(record['call_id'],pending=True,error_code=exc.code)
            delay=min(5,max(exc.retry_after,(2 ** (attempt-1))*.1+random.random()*.1))
            if exc.retryable and attempt < max_attempts and (deadline is None or time.time()+delay < deadline):
                self.sleeper(delay)
                return self.run(context,operation,call_key,estimated_units,fn,request_hash=request_hash,
                    provider=provider,sku=sku,guard=guard,attempt=attempt+1,max_attempts=max_attempts,deadline=deadline)
            raise DomainError(exc.code,'공급자가 요청을 거절했습니다. 설정 또는 공급자 사용량을 확인해 주세요.',exc.status) from None
        except Exception as exc:
            self.budget.uncertain(record['call_id'])
            if isinstance(exc,DomainError):
                raise
            # Do not persist the exception, which can contain source text/API keys.
            raise DomainError('PROVIDER_OUTCOME_UNKNOWN','공급자 응답을 확인할 수 없습니다. 사용량 확인 후 다시 시도해 주세요.',503) from None
        if guard:
            try:
                guard()
            except Exception:
                if saved_path is not None:
                    if self.artifacts: self.artifacts.delete(str(saved_path.relative_to(self.result_dir)))
                    else: saved_path.unlink(missing_ok=True)
                raise
        return result.value


@dataclass
class _MeteredContext:
    gateway: ProviderGateway
    context: CallContext
    prefix: str
    guard: object = None
    deadline: float | None = None
    counters: dict = field(default_factory=dict)


_active: ContextVar[_MeteredContext | None] = ContextVar('provider_metering',default=None)


@contextmanager
def metered_context(gateway, context, call_key, *, guard=None, deadline=None):
    """Use around existing parser/rag functions; context stays request/thread-local."""
    token = _active.set(_MeteredContext(gateway,context,call_key,guard,deadline))
    try:
        yield
    finally:
        _active.reset(token)


@contextmanager
def provider_scope(gateway, context, call_prefix=None, *, guard=None, deadline=None, call_key=None):
    with metered_context(gateway,context,call_prefix or call_key,guard=guard,deadline=deadline):
        yield


def has_metering():
    return _active.get() is not None


def _metered_call(client, kind, kwargs):
    active = _active.get()
    if active is None:
        raise DomainError('PROVIDER_CONTEXT_REQUIRED','외부 호출에는 인증 범위와 비용 관리가 필요합니다.',503)
    model = kwargs['model']
    sku = model
    price = active.gateway.budget.policy.price('openai',sku)
    kwargs = dict(kwargs)
    if kind == 'chat':
        # UTF-8 bytes plus bounded framing is conservative for standard text
        # tokenization. Structured output schema is included, as it is billable.
        cap = min(int(kwargs.get('max_completion_tokens',price['max_units'].get('output_tokens',0))),price['max_units'].get('output_tokens',0))
        if cap <= 0:
            raise DomainError('COST_BOUND_REQUIRED','출력 토큰 상한을 설정해 주세요.',503)
        kwargs['max_completion_tokens'] = cap
        prompt = {key:value for key,value in kwargs.items() if key in ('messages','response_format','tools')}
        input_bound = len(json.dumps(prompt,ensure_ascii=False).encode('utf-8')) + 256
        estimates = {'input_tokens':input_bound,'output_tokens':cap}
    else:
        inputs = kwargs['input']
        if isinstance(inputs,str):
            inputs = [inputs]
        if not isinstance(inputs,list) or not all(isinstance(text,str) for text in inputs):
            raise DomainError('PROVIDER_INPUT_INVALID','임베딩 입력은 문자열이어야 합니다.',422)
        estimates = {'input_tokens':sum(len(text.encode('utf-8'))+16 for text in inputs)}
    index = active.counters.get(kind,0)
    active.counters[kind] = index+1
    request_hash = hashlib.sha256(json.dumps(kwargs,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    key = f'{active.prefix}:{kind}:{index}'

    def invoke():
        # OpenAI's SDK normally retries invisibly. Clone with retries disabled
        # for every attempt, including callers with a cached preconfigured client.
        bounded = client.with_options(max_retries=0,timeout=45.0)
        try:
            response = bounded.chat.completions.create(**kwargs) if kind=='chat' else bounded.embeddings.create(**kwargs)
        except Exception as exc:
            from openai import APIConnectionError, APIStatusError
            import httpx
            if isinstance(exc,APIConnectionError) and isinstance(exc.__cause__,(httpx.ConnectError,httpx.ConnectTimeout)):
                raise DefinitelyNotSent() from None
            if isinstance(exc,APIStatusError) and 400 <= exc.status_code < 500:
                if exc.status_code==429:
                    try:
                        delay=float(exc.response.headers.get('retry-after','0'))
                    except (ValueError,TypeError):
                        delay=0
                    raise ProviderRejected('PROVIDER_RATE_LIMITED',retryable=True,retry_after=delay) from None
                if exc.status_code in (401,403):
                    raise ProviderRejected('PROVIDER_AUTH_FAILED') from None
                raise ProviderRejected('PROVIDER_REQUEST_REJECTED',status=422) from None
            # Read timeouts and 5xx may follow remote execution. No automatic
            # retry without provider-supported lookup/idempotency evidence.
            raise
        usage = response.usage
        if usage is None:
            units = None
        elif kind == 'chat':
            units = {'input_tokens':usage.prompt_tokens,'output_tokens':usage.completion_tokens}
        else:
            units = {'input_tokens':usage.prompt_tokens}
        return ProviderResult(response.model_dump(mode='json'),units)

    value = active.gateway.run(active.context,kind,key,estimates,invoke,request_hash=request_hash,
        sku=sku,guard=active.guard,deadline=active.deadline,max_attempts=3)
    if kind == 'chat':
        from openai.types.chat import ChatCompletion
        return ChatCompletion.model_validate(value)
    from openai.types import CreateEmbeddingResponse
    return CreateEmbeddingResponse.model_validate(value)


def chat_create(client, **kwargs):
    return _metered_call(client,'chat',kwargs)


def embeddings_create(client, **kwargs):
    return _metered_call(client,'embedding',kwargs)
