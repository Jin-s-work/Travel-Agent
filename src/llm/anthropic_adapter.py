"""Anthropic Messages adapter — interface and fake-response tests only.

PRD 10절 결정 2 = A (2026-10-20): no SDK install and no real key call until the
owner sets a key and a cap. Every vendor detail below came from the bundled
claude-api reference (cached 2026-10-06), not from a live call, so each is
[미확인] until checked against the official docs at activation time:
- request: `messages.create(model, max_tokens, system, messages,
  output_config={"format": {"type": "json_schema", "schema": ...}})`
- response: `stop_reason` (end_turn / max_tokens / refusal / tool_use),
  `usage.input_tokens` / `usage.output_tokens`, `content[]` blocks with `type`
- client: `anthropic.Anthropic(max_retries=0, timeout=45.0)`
- small model candidate `claude-haiku-5-5` ($0.10 / $0.50 per 1M input/output
  tokens for prompts up to 100K) — deliberately absent from every pricing file,
  so the gateway refuses a call before sending (BUDGET_NOT_CONFIGURED).
- whether strict structured output accepts `"type": ["string", "null"]` with a
  null enum member, as the document schema uses.
There is no embeddings endpoint, so `supports_embeddings` is False.
"""
from __future__ import annotations

import json
import os

from src.foundation.repository import DomainError
from src.reliability.providers import ProviderRejected, ProviderResult

from . import FINISH_LENGTH, FINISH_OTHER, FINISH_REFUSAL, FINISH_STOP, FINISH_TOOLS, ChatResult, ToolCall, Usage

_FINISH = {'end_turn': FINISH_STOP, 'stop_sequence': FINISH_STOP, 'max_tokens': FINISH_LENGTH,
           'refusal': FINISH_REFUSAL, 'tool_use': FINISH_TOOLS}


def _get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


class AnthropicAdapter:
    name = 'anthropic'
    needs_client = True
    supports_embeddings = False

    def __init__(self, client_factory=None):
        self.client_factory = client_factory

    def chat_request(self, *, model, messages, schema=None, tools=None, tool_choice=None,
                     reasoning_effort=None, max_output_tokens=None) -> dict:
        from .messages import to_anthropic
        system, converted = to_anthropic(messages)
        kwargs = {'model': model, 'messages': converted}
        if system:
            kwargs['system'] = system
        if schema is not None:
            kwargs['output_config'] = {'format': {'type': 'json_schema', 'schema': schema[1]}}
        if tools:
            kwargs['tools'] = [{'name': tool['name'], 'description': tool['description'],
                                'input_schema': tool['input_schema'], 'strict': True} for tool in tools]
            # Forced tool_choice is rejected on current models; 'auto' only. [미확인]
        if max_output_tokens is not None:
            kwargs['max_tokens'] = max_output_tokens
        return kwargs

    def make_client(self, client_factory=None):
        # The OpenAI factory a caller passes is ignored; this vendor has its own.
        factory = self.client_factory or _sdk_client
        return factory()

    def preflight(self, kind, kwargs):
        if kind != 'chat':
            raise DomainError('PROVIDER_CAPABILITY_MISSING', '이 공급자는 임베딩을 지원하지 않습니다.', 503)

    def output_cap(self, kwargs, price) -> int:
        bound = price['max_units'].get('output_tokens', 0)
        return min(int(kwargs.get('max_tokens', bound)), bound)

    def apply_cap(self, kwargs, cap):
        kwargs['max_tokens'] = cap

    def estimate_chat(self, kwargs, cap) -> dict:
        prompt = {key: value for key, value in kwargs.items() if key in ('system', 'messages', 'output_config', 'tools')}
        return {'input_tokens': len(json.dumps(prompt, ensure_ascii=False).encode('utf-8')) + 256, 'output_tokens': cap}

    def send(self, client, kind, kwargs) -> ProviderResult:
        bounded = client.with_options(max_retries=0, timeout=45.0)
        try:
            response = bounded.messages.create(**kwargs)
        except Exception as exc:
            status = getattr(exc, 'status_code', None)
            if isinstance(status, int) and 400 <= status < 500:
                if status == 429:
                    try:
                        delay = float(exc.response.headers.get('retry-after', '0'))
                    except (AttributeError, ValueError, TypeError):
                        delay = 0
                    raise ProviderRejected('PROVIDER_RATE_LIMITED', retryable=True, retry_after=delay) from None
                if status in (401, 403):
                    raise ProviderRejected('PROVIDER_AUTH_FAILED') from None
                raise ProviderRejected('PROVIDER_REQUEST_REJECTED', status=422) from None
            # Connection errors are not mapped to DefinitelyNotSent: the SDK's
            # transport exception chain is [미확인], and a wrong "not sent" could
            # retry a billed call. Unknown outcomes stop for reconciliation.
            raise
        usage = _get(response, 'usage')
        units = None if usage is None else {'input_tokens': _get(usage, 'input_tokens'), 'output_tokens': _get(usage, 'output_tokens')}
        raw = response.model_dump(mode='json') if hasattr(response, 'model_dump') else response
        return ProviderResult(raw, units)

    def parse_chat(self, response) -> ChatResult:
        texts, calls = [], []
        for block in _get(response, 'content') or ():
            kind = _get(block, 'type')
            if kind == 'text':
                texts.append(_get(block, 'text') or '')
            elif kind == 'tool_use':
                calls.append(ToolCall(id=str(_get(block, 'id')), name=str(_get(block, 'name')),
                                      arguments=json.dumps(_get(block, 'input') or {}, ensure_ascii=False)))
        stop = _get(response, 'stop_reason')
        usage = _get(response, 'usage')
        finish = _FINISH.get(stop, FINISH_OTHER)
        return ChatResult(text=''.join(texts) if texts else None, finish=finish,
                          refusal='refusal' if finish == FINISH_REFUSAL else None, tool_calls=tuple(calls),
                          usage=Usage(_get(usage, 'input_tokens'), _get(usage, 'output_tokens')) if usage is not None else Usage(),
                          meta={'finish_raw': stop})


def _sdk_client():
    """Real client, only after the owner installs the SDK and sets a key."""
    try:
        import anthropic  # noqa: F401 - optional dependency, not in requirements
    except ImportError:
        raise DomainError('PROVIDER_SDK_MISSING', 'Anthropic SDK가 설치되지 않았습니다. 설치·키·상한은 대표가 정합니다.', 503) from None
    key = os.getenv('ANTHROPIC_API_KEY')
    if not key:
        raise DomainError('AI_KEY_NOT_CONFIGURED', 'ANTHROPIC_API_KEY가 설정되지 않았습니다.', 503)
    return anthropic.Anthropic(api_key=key, max_retries=0, timeout=45.0)
