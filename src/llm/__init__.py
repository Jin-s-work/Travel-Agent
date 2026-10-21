"""Vendor-neutral LLM layer inside the existing metered ProviderGateway.

Callers (parser, rag, embedder, trip agent) build a neutral request and get a
normalized result; only adapters know a vendor's wire format. The gateway's
reservation, settlement, receipt replay and retry contracts are unchanged —
an adapter only supplies the request kwargs, the send function, and parsing.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import os

from src.foundation.repository import DomainError

FINISH_STOP, FINISH_LENGTH, FINISH_REFUSAL, FINISH_TOOLS, FINISH_OTHER = 'stop', 'length', 'refusal', 'tool_calls', 'other'
PAID_PROVIDERS = frozenset({'openai', 'anthropic'})


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # Raw JSON text as the vendor returned it; parsed by the caller.


@dataclass(frozen=True)
class ChatResult:
    text: str | None
    finish: str
    refusal: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    usage: Usage = field(default_factory=Usage)
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Limits:
    """Per-call bounds. None means the pricing policy's per-SKU maximum."""
    max_output_tokens: int | None = None


def chain(value: str | None, *, default: str = 'openai') -> tuple[str, ...]:
    """Parse a provider chain such as 'openai' or 'openai,anthropic'.

    A second paid provider is dropped unless LLM_PAID_FALLBACK=1: an automatic
    paid→paid switch doubles spend on a failure and is off by default.

    Not implemented yet: switching to the next provider at runtime. resolve()
    always uses the first entry; the tail (and so LLM_PAID_FALLBACK) only
    changes the parsed chain and the AI pipeline signature.
    """
    names = tuple(dict.fromkeys(part.strip().lower() for part in (value or default).split(',') if part.strip()))
    if not names:
        names = (default,)
    unknown = [name for name in names if name not in ADAPTER_NAMES]
    if unknown:
        raise DomainError('PROVIDER_NOT_SUPPORTED', '지원하지 않는 LLM 공급자 설정입니다.', 503)
    if os.getenv('LLM_PAID_FALLBACK', '0').lower() in ('1', 'true', 'yes'):
        return names
    first_paid = next((name for name in names if name in PAID_PROVIDERS), None)
    return tuple(name for name in names if name not in PAID_PROVIDERS or name == first_paid)


ADAPTER_NAMES = ('openai', 'replay', 'anthropic')
_override: ContextVar[object | None] = ContextVar('llm_adapter_override', default=None)


@contextmanager
def adapter_scope(adapter):
    """Evaluators/tests bind one adapter instance (e.g. a replay store) here."""
    token = _override.set(adapter)
    try:
        yield adapter
    finally:
        _override.reset(token)


def resolve(purpose: str):
    """The adapter for a purpose: an explicit scope wins, else configuration.

    MAIL_EXTRACTION_CHAIN / ANSWER_PROVIDER / EMBEDDING_PROVIDER / AGENT_PROVIDER
    all default to 'openai', which is today's behaviour. Only chain(...)[0] is
    used: there is no runtime fallback to a second provider.
    """
    bound = _override.get()
    if bound is not None:
        return bound
    setting = {'extract': 'MAIL_EXTRACTION_CHAIN', 'answer': 'ANSWER_PROVIDER',
               'embed': 'EMBEDDING_PROVIDER', 'agent': 'AGENT_PROVIDER'}.get(purpose)
    if setting is None:
        raise DomainError('PROVIDER_NOT_SUPPORTED', '알 수 없는 LLM 용도입니다.', 503)
    name = chain(os.getenv(setting))[0]
    return build(name)


def supports_embeddings(name: str) -> bool:
    """Whether a configured provider name can serve EMBEDDING_PROVIDER (no instance built)."""
    if name == 'openai':
        from .openai_adapter import OpenAIAdapter as adapter
    elif name == 'anthropic':
        from .anthropic_adapter import AnthropicAdapter as adapter
    elif name == 'replay':
        from .replay import ReplayAdapter as adapter
    else:
        return False
    return bool(getattr(adapter, 'supports_embeddings', False))


def build(name: str):
    if name == 'openai':
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter()
    if name == 'anthropic':
        from .anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter()
    if name == 'replay':
        from .replay import ReplayAdapter, ReplayStore
        directory = os.getenv('LLM_REPLAY_DIR')
        if not directory:
            raise DomainError('REPLAY_NOT_CONFIGURED', 'replay 기록 경로(LLM_REPLAY_DIR)를 설정해 주세요.', 503)
        return ReplayAdapter(ReplayStore(directory))
    raise DomainError('PROVIDER_NOT_SUPPORTED', '지원하지 않는 LLM 공급자 설정입니다.', 503)
