"""Neutral `chat`/`embed` entry points over the metered ProviderGateway.

Everything that decides money stays where it was: `ProviderGateway.run`
reserves, guards, sends once, settles and replays receipts. This module only
asks the adapter for the vendor kwargs and for parsing, computes the same cost
bound / request hash / call key as before, and records telemetry afterwards.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import time

from src.foundation.repository import DomainError

from . import ChatResult, EmbedResult, Limits, resolve
from . import telemetry


def _active():
    from src.reliability.providers import _active as active_var
    return active_var.get()


def request_hash(kwargs) -> str:
    return hashlib.sha256(json.dumps(kwargs, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def metered_raw(adapter, client, kind, kwargs, *, call_suffix=None, operation=None, prompt=None, observe=None):
    """Run one metered call; return (vendor raw value, telemetry info dict)."""
    active = _active()
    if active is None:
        raise DomainError('PROVIDER_CONTEXT_REQUIRED', '외부 호출에는 인증 범위와 비용 관리가 필요합니다.', 503)
    model = kwargs['model']
    sku = model
    price = active.gateway.budget.policy.price(adapter.name, sku)
    kwargs = dict(kwargs)
    if kind == 'chat':
        # UTF-8 bytes plus bounded framing is conservative for standard text
        # tokenization. Structured output schema is included, as it is billable.
        cap = adapter.output_cap(kwargs, price)
        if cap <= 0:
            raise DomainError('COST_BOUND_REQUIRED', '출력 토큰 상한을 설정해 주세요.', 503)
        adapter.apply_cap(kwargs, cap)
        estimates = adapter.estimate_chat(kwargs, cap)
    else:
        inputs = kwargs['input']
        if isinstance(inputs, str):
            inputs = [inputs]
        if not isinstance(inputs, list) or not all(isinstance(text, str) for text in inputs):
            raise DomainError('PROVIDER_INPUT_INVALID', '임베딩 입력은 문자열이어야 합니다.', 422)
        estimates = adapter.estimate_embed(kwargs)
    # Main calls keep the historical '<prefix>:<kind>:<n>' key so stored
    # receipts of in-flight jobs still match; extra calls (repair) get their own.
    counter = kind if not call_suffix else f'{call_suffix}:{kind}'
    index = active.counters.get(counter, 0)
    active.counters[counter] = index + 1
    digest = request_hash(kwargs)
    key = f'{active.prefix}:{counter}:{index}'
    seen = {}

    def observer(event, record):
        seen[event] = record

    started = time.monotonic()
    info = {'provider': adapter.name, 'model': model, 'call_key': key, 'request_hash': digest,
            'operation': operation or kind, 'prompt': prompt}
    try:
        value = active.gateway.run(active.context, kind, key, estimates, lambda: adapter.send(client, kind, kwargs),
                                   request_hash=digest, provider=adapter.name, sku=sku, guard=active.guard,
                                   deadline=active.deadline, max_attempts=3, observer=observer)
    except DomainError as exc:
        info.update(latency_ms=int((time.monotonic() - started) * 1000), replayed=False, outcome=exc.code)
        info['telemetry_id'] = _record(active, info)
        raise
    info.update(latency_ms=int((time.monotonic() - started) * 1000), replayed='replay' in seen, outcome='ok')
    info['telemetry_id'] = _record(active, info)
    if observe:
        observe(info)
    return value, info


def _record(active, info):
    return telemetry.record(active.gateway.budget.db, active.context, call_key=info['call_key'],
                            provider=info['provider'], model=info['model'], operation=info['operation'],
                            prompt=info['prompt'], latency_ms=info['latency_ms'], outcome=info['outcome'],
                            replayed=info['replayed'])


def annotate(info, **fields):
    active = _active()
    if active is not None and info and info.get('telemetry_id'):
        telemetry.annotate(active.gateway.budget.db, info['telemetry_id'], **fields)


def _has_metering():
    return _active() is not None


def chat(purpose, *, model, messages, client_factory=None, schema=None, tools=None, tool_choice=None,
         reasoning_effort=None, limits: Limits | None = None, prompt=None, operation=None,
         call_suffix=None, adapter=None) -> ChatResult:
    """One chat call. `schema=(name, json_schema)` asks for strict structured output."""
    adapter = adapter or resolve(purpose)
    kwargs = adapter.chat_request(model=model, messages=messages, schema=schema, tools=tools, tool_choice=tool_choice,
                                  reasoning_effort=reasoning_effort,
                                  max_output_tokens=limits.max_output_tokens if limits else None)
    # Construct the client before any reservation: a missing key is a local
    # configuration error, never an "unknown charge".
    client = adapter.make_client(client_factory)
    adapter.preflight('chat', kwargs)
    if not _has_metering():
        if not hasattr(adapter, 'legacy_send'):
            raise DomainError('PROVIDER_CONTEXT_REQUIRED', '외부 호출에는 인증 범위와 비용 관리가 필요합니다.', 503)
        # Legacy local CLI compatibility only. Product HTTP/dispatcher paths bind
        # metered_context before calling; they never use this branch.
        return adapter.parse_chat(adapter.legacy_send(client, 'chat', kwargs))
    value, info = metered_raw(adapter, client, 'chat', kwargs, call_suffix=call_suffix,
                              operation=operation or purpose, prompt=prompt)
    result = adapter.parse_chat(value)
    return replace(result, meta={**result.meta, **{k: v for k, v in info.items() if k != 'prompt'}, 'prompt': prompt})


def chat_reservation_micros(purpose, *, model, messages, schema=None, tools=None, tool_choice=None,
                            reasoning_effort=None, limits: Limits | None = None, adapter=None):
    """What `chat` with the same arguments would reserve (micros), computed the
    same way as metered_raw (output cap, adapter estimate, policy price) without
    reserving or sending. None when there is no metered scope or no output cap."""
    active = _active()
    if active is None:
        return None
    adapter = adapter or resolve(purpose)
    kwargs = adapter.chat_request(model=model, messages=messages, schema=schema, tools=tools, tool_choice=tool_choice,
                                  reasoning_effort=reasoning_effort,
                                  max_output_tokens=limits.max_output_tokens if limits else None)
    policy = active.gateway.budget.policy
    price = policy.price(adapter.name, model)
    cap = adapter.output_cap(kwargs, price)
    if cap <= 0:
        return None
    adapter.apply_cap(kwargs, cap)
    return policy.calculate(price['rates_per_million'], adapter.estimate_chat(kwargs, cap))


def embed(purpose, *, model, texts, client_factory=None, operation=None, adapter=None) -> EmbedResult:
    adapter = adapter or resolve(purpose)
    if not getattr(adapter, 'supports_embeddings', False):
        raise DomainError('PROVIDER_CAPABILITY_MISSING', '이 공급자는 임베딩을 지원하지 않습니다.', 503)
    kwargs = adapter.embed_request(model=model, texts=texts)
    client = adapter.make_client(client_factory)
    adapter.preflight('embedding', kwargs)
    if not _has_metering():
        if not hasattr(adapter, 'legacy_send'):
            raise DomainError('PROVIDER_CONTEXT_REQUIRED', '외부 호출에는 인증 범위와 비용 관리가 필요합니다.', 503)
        return adapter.parse_embed(adapter.legacy_send(client, 'embedding', kwargs))
    value, info = metered_raw(adapter, client, 'embedding', kwargs, operation=operation or purpose)
    result = adapter.parse_embed(value)
    return replace(result, meta={**result.meta, **{k: v for k, v in info.items() if k != 'prompt'}})
