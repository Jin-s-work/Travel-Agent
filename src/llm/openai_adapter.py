"""OpenAI Chat Completions / Embeddings adapter.

Byte-compatibility is the contract: the request kwargs, the output cap key, the
cost estimate, the receipt value (`response.model_dump(mode='json')`) and the
exception mapping are exactly what `providers._metered_call` produced before
the adapter layer existed, so in-flight jobs replay their stored receipts and
`usage_reservations.request_hash` stays idempotent.
"""
from __future__ import annotations

from functools import lru_cache
import json

from src.reliability.providers import DefinitelyNotSent, ProviderRejected, ProviderResult

from . import FINISH_LENGTH, FINISH_OTHER, FINISH_REFUSAL, FINISH_STOP, FINISH_TOOLS, ChatResult, EmbedResult, ToolCall, Usage

_FINISH = {'stop': FINISH_STOP, 'length': FINISH_LENGTH, 'content_filter': FINISH_REFUSAL,
           'tool_calls': FINISH_TOOLS, 'function_call': FINISH_TOOLS}


@lru_cache(maxsize=1)
def _default_client():
    from openai import OpenAI
    from src.config import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        raise RuntimeError('OPENAI_API_KEY가 설정되지 않았습니다.')
    return OpenAI(api_key=OPENAI_API_KEY, max_retries=0, timeout=45.0)


def _get(value, key, default=None):
    """Receipts are dicts; the legacy CLI branch and test fakes are objects."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


class OpenAIAdapter:
    name = 'openai'
    needs_client = True
    supports_embeddings = True

    def structured_format(self, schema_name: str, schema: dict) -> dict:
        return {'type': 'json_schema', 'json_schema': {'name': schema_name, 'strict': True, 'schema': schema}}

    def chat_request(self, *, model, messages, schema=None, tools=None, tool_choice=None,
                     reasoning_effort=None, max_output_tokens=None) -> dict:
        kwargs = {'model': model}
        if reasoning_effort is not None:
            kwargs['reasoning_effort'] = reasoning_effort
        from .messages import to_openai
        kwargs['messages'] = to_openai(messages)  # identity unless tool turns are present
        if schema is not None:
            kwargs['response_format'] = self.structured_format(*schema)
        if tools:
            kwargs['tools'] = [{'type': 'function', 'function': {'name': tool['name'], 'description': tool['description'],
                                'parameters': tool['input_schema'], 'strict': True}} for tool in tools]
            if tool_choice:
                kwargs['tool_choice'] = tool_choice
        if max_output_tokens is not None:
            kwargs['max_completion_tokens'] = max_output_tokens
        return kwargs

    def embed_request(self, *, model, texts) -> dict:
        return {'model': model, 'input': texts}

    def preflight(self, kind, kwargs):
        return None

    def make_client(self, client_factory=None):
        # Callers pass their module's cached `_client` (tests monkeypatch it).
        return (client_factory or _default_client)()

    # --- cost bounds (same arithmetic as before the adapter layer) ---
    def output_cap(self, kwargs, price) -> int:
        bound = price['max_units'].get('output_tokens', 0)
        return min(int(kwargs.get('max_completion_tokens', bound)), bound)

    def apply_cap(self, kwargs, cap):
        kwargs['max_completion_tokens'] = cap

    def estimate_chat(self, kwargs, cap) -> dict:
        prompt = {key: value for key, value in kwargs.items() if key in ('messages', 'response_format', 'tools')}
        return {'input_tokens': len(json.dumps(prompt, ensure_ascii=False).encode('utf-8')) + 256, 'output_tokens': cap}

    def estimate_embed(self, kwargs) -> dict:
        inputs = kwargs['input']
        if isinstance(inputs, str):
            inputs = [inputs]
        return {'input_tokens': sum(len(text.encode('utf-8')) + 16 for text in inputs)}

    # --- transport ---
    def send(self, client, kind, kwargs) -> ProviderResult:
        # OpenAI's SDK normally retries invisibly. Clone with retries disabled
        # for every attempt, including callers with a cached preconfigured client.
        bounded = client.with_options(max_retries=0, timeout=45.0)
        try:
            response = bounded.chat.completions.create(**kwargs) if kind == 'chat' else bounded.embeddings.create(**kwargs)
        except Exception as exc:
            from openai import APIConnectionError, APIStatusError
            import httpx
            if isinstance(exc, APIConnectionError) and isinstance(exc.__cause__, (httpx.ConnectError, httpx.ConnectTimeout)):
                raise DefinitelyNotSent() from None
            if isinstance(exc, APIStatusError) and 400 <= exc.status_code < 500:
                if exc.status_code == 429:
                    try:
                        delay = float(exc.response.headers.get('retry-after', '0'))
                    except (ValueError, TypeError):
                        delay = 0
                    raise ProviderRejected('PROVIDER_RATE_LIMITED', retryable=True, retry_after=delay) from None
                if exc.status_code in (401, 403):
                    raise ProviderRejected('PROVIDER_AUTH_FAILED') from None
                raise ProviderRejected('PROVIDER_REQUEST_REJECTED', status=422) from None
            # Read timeouts and 5xx may follow remote execution. No automatic
            # retry without provider-supported lookup/idempotency evidence.
            raise
        usage = response.usage
        if usage is None:
            units = None
        elif kind == 'chat':
            units = {'input_tokens': usage.prompt_tokens, 'output_tokens': usage.completion_tokens}
        else:
            units = {'input_tokens': usage.prompt_tokens}
        return ProviderResult(response.model_dump(mode='json'), units)

    def legacy_send(self, client, kind, kwargs):
        """CLI-only branch without metering; unchanged SDK call shape."""
        return client.chat.completions.create(**kwargs) if kind == 'chat' else client.embeddings.create(**kwargs)

    # --- parsing ---
    def parse_chat(self, response) -> ChatResult:
        choice = _get(response, 'choices')[0]
        message = _get(choice, 'message')
        finish_raw = _get(choice, 'finish_reason', 'stop')
        calls = []
        for call in _get(message, 'tool_calls') or ():
            function = _get(call, 'function')
            calls.append(ToolCall(id=str(_get(call, 'id')), name=str(_get(function, 'name')), arguments=_get(function, 'arguments') or ''))
        usage = _get(response, 'usage')
        return ChatResult(text=_get(message, 'content'), finish=_FINISH.get(finish_raw, FINISH_OTHER),
                          refusal=_get(message, 'refusal'), tool_calls=tuple(calls),
                          usage=Usage(_get(usage, 'prompt_tokens'), _get(usage, 'completion_tokens')) if usage is not None else Usage(),
                          meta={'finish_raw': finish_raw})

    def parse_embed(self, response) -> EmbedResult:
        data = sorted(_get(response, 'data'), key=lambda item: _get(item, 'index'))
        usage = _get(response, 'usage')
        return EmbedResult(vectors=[list(_get(item, 'embedding')) for item in data],
                           usage=Usage(_get(usage, 'prompt_tokens')) if usage is not None else Usage())

    def restore(self, kind, value):
        """Compatibility for `chat_create`/`embeddings_create` callers."""
        if kind == 'chat':
            from openai.types.chat import ChatCompletion
            return ChatCompletion.model_validate(value)
        from openai.types import CreateEmbeddingResponse
        return CreateEmbeddingResponse.model_validate(value)
