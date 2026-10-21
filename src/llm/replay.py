"""Replay adapter: request key → recorded response, no key and no network.

A record never contains the mail or question text, only hashes of the request
parts plus the recorded response. Records made by hand (oracle answers built
from the gold set, deliberate faults) are labelled so their numbers are never
reported as model quality. Live recordings are written only by the person who
runs `--mode live --record-replay`.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re

from src.foundation.repository import DomainError
from src.reliability.providers import ProviderResult

from . import FINISH_LENGTH, FINISH_OTHER, FINISH_REFUSAL, FINISH_STOP, FINISH_TOOLS, ChatResult, EmbedResult, ToolCall, Usage

RECORD_VERSION = 1
SOURCES = ('gold_oracle', 'handmade_fault', 'handmade', 'live')
_log = logging.getLogger(__name__)
_FINISHES = (FINISH_STOP, FINISH_LENGTH, FINISH_REFUSAL, FINISH_TOOLS, FINISH_OTHER)
# Value shapes only: a key, a JWT, a bearer token or an assigned API key. Plain
# words such as "card authorization" in a booking mail are not secrets.
_SECRET = re.compile(r'\bsk-[A-Za-z0-9_-]{20,}|\beyJ[A-Za-z0-9_-]{10,}\.eyJ'
                     r'|\bBearer\s+[A-Za-z0-9._~+/=-]{20,}'
                     r'|api[_-]?key[\\"\']*\s*[:=]\s*[\\"\']*[A-Za-z0-9_-]{16,}', re.I)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def request_key(kwargs: dict) -> str:
    """Vendor-neutral key: what the model sees, not the model id or token cap.

    The same record therefore serves an OpenAI- or Anthropic-shaped request
    whose messages, output schema and tools are identical.
    """
    messages = kwargs.get('messages') or []
    system = kwargs.get('system')
    if system:
        messages = [{'role': 'system', 'content': system}, *messages]
    schema = None
    response_format = kwargs.get('response_format')
    if response_format:
        schema = response_format.get('json_schema', {}).get('schema')
    elif kwargs.get('output_config'):
        schema = kwargs['output_config'].get('format', {}).get('schema')
    tools = sorted(_tool_name(tool) for tool in kwargs.get('tools') or [])
    return _sha({'messages': messages, 'schema': schema, 'tools': tools})


def _tool_name(tool):
    return tool.get('function', {}).get('name') or tool.get('name')


def input_digest(kwargs: dict) -> str:
    """Hash of the non-system message content, stored instead of the text."""
    return _sha([message.get('content') for message in kwargs.get('messages') or [] if message.get('role') != 'system'])


def validate_record(record: dict) -> dict:
    if not isinstance(record, dict) or record.get('version') != RECORD_VERSION:
        raise ValueError('replay 기록 버전이 올바르지 않습니다.')
    if record.get('synthetic') is not True or record.get('source') not in SOURCES:
        raise ValueError('replay 기록에는 synthetic: true 와 source 가 필요합니다.')
    if not re.fullmatch(r'[0-9a-f]{64}', str(record.get('request_key', ''))):
        raise ValueError('replay 기록의 request_key 가 올바르지 않습니다.')
    response = record.get('response')
    if not isinstance(response, dict) or response.get('finish') not in _FINISHES:
        raise ValueError('replay 응답 형식이 올바르지 않습니다.')
    if _SECRET.search(json.dumps(record, ensure_ascii=False)):
        raise ValueError('replay 기록에 비밀처럼 보이는 값이 있습니다.')
    return record


class ReplayStore:
    """A directory of `<request_key>.json` records (one per request)."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def path(self, key):
        return self.directory / f'{key}.json'

    def get(self, key):
        path = self.path(key)
        if not path.is_file():
            return None
        return validate_record(json.loads(path.read_text(encoding='utf-8')))

    def put(self, record):
        validate_record(record)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path(record['request_key']).write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')

    def records(self):
        return [validate_record(json.loads(path.read_text(encoding='utf-8'))) for path in sorted(self.directory.glob('*.json'))]


def make_record(kwargs, *, text=None, finish=FINISH_STOP, refusal=None, tool_calls=(), usage=None,
                source, label, prompt=None) -> dict:
    return {'version': RECORD_VERSION, 'synthetic': True, 'source': source, 'label': label,
            'request_key': request_key(kwargs), 'input_sha256': input_digest(kwargs), 'prompt': prompt,
            'response': {'text': text, 'finish': finish, 'refusal': refusal,
                         'tool_calls': [dict(call) for call in tool_calls],
                         'usage': usage or {'input_tokens': 0, 'output_tokens': 0}}}


class ReplayAdapter:
    """Same request shape as OpenAI (so keys line up with the live recorder)."""
    name = 'replay'
    needs_client = False
    supports_embeddings = False

    def __init__(self, store: ReplayStore, *, on_miss='error'):
        self.store = store
        self.on_miss = on_miss
        self.misses = []

    def structured_format(self, schema_name, schema):
        return {'type': 'json_schema', 'json_schema': {'name': schema_name, 'strict': True, 'schema': schema}}

    def chat_request(self, *, model, messages, schema=None, tools=None, tool_choice=None,
                     reasoning_effort=None, max_output_tokens=None) -> dict:
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter().chat_request(model=model, messages=messages, schema=schema, tools=tools,
                                            tool_choice=tool_choice, reasoning_effort=reasoning_effort,
                                            max_output_tokens=max_output_tokens)

    def make_client(self, client_factory=None):
        return None

    def preflight(self, kind, kwargs):
        # A miss is known before any reservation: never an unknown charge.
        if kind != 'chat':
            raise DomainError('PROVIDER_CAPABILITY_MISSING', 'replay 는 임베딩을 지원하지 않습니다.', 503)
        if self.store.get(request_key(kwargs)) is None:
            self.misses.append(request_key(kwargs))
            raise DomainError('REPLAY_MISS', '이 요청의 replay 기록이 없습니다.', 503)

    def output_cap(self, kwargs, price):
        bound = price['max_units'].get('output_tokens', 0)
        return min(int(kwargs.get('max_completion_tokens', bound)), bound)

    def apply_cap(self, kwargs, cap):
        kwargs['max_completion_tokens'] = cap

    def estimate_chat(self, kwargs, cap):
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter().estimate_chat(kwargs, cap)

    def send(self, client, kind, kwargs) -> ProviderResult:
        record = self.store.get(request_key(kwargs))
        if record is None:  # Removed between preflight and send.
            raise DomainError('REPLAY_MISS', '이 요청의 replay 기록이 없습니다.', 503)
        usage = record['response'].get('usage') or {}
        units = {'input_tokens': int(usage.get('input_tokens') or 0), 'output_tokens': int(usage.get('output_tokens') or 0)}
        return ProviderResult({'replay': True, 'source': record['source'], 'label': record.get('label'),
                               'response': record['response']}, units)

    def legacy_send(self, client, kind, kwargs):
        return self.send(client, kind, kwargs).value

    def parse_chat(self, value) -> ChatResult:
        response = value['response']
        calls = tuple(ToolCall(id=str(call['id']), name=str(call['name']), arguments=str(call['arguments']))
                      for call in response.get('tool_calls') or [])
        usage = response.get('usage') or {}
        return ChatResult(text=response.get('text'), finish=response['finish'], refusal=response.get('refusal'),
                          tool_calls=calls, usage=Usage(usage.get('input_tokens'), usage.get('output_tokens')),
                          meta={'replay_source': value.get('source'), 'replay_label': value.get('label')})

    def parse_embed(self, value) -> EmbedResult:  # pragma: no cover - capability gated
        raise DomainError('PROVIDER_CAPABILITY_MISSING', 'replay 는 임베딩을 지원하지 않습니다.', 503)


class RecordingAdapter:
    """Wrap a live adapter and write replay records of its answers (live mode only)."""

    def __init__(self, inner, store: ReplayStore, *, prompt=None):
        self.inner, self.store, self.prompt = inner, store, prompt
        self.failures = []  # Exception class names of records that could not be written.
        self.name = inner.name
        self.needs_client = inner.needs_client
        self.supports_embeddings = inner.supports_embeddings

    def __getattr__(self, attribute):
        return getattr(self.inner, attribute)

    def send(self, client, kind, kwargs):
        result = self.inner.send(client, kind, kwargs)
        if kind == 'chat':
            # This runs inside the metered send: the call is already paid for,
            # so a recording problem must never turn it into an unknown outcome.
            try:
                parsed = self.inner.parse_chat(result.value)
                self.store.put(make_record(kwargs, text=parsed.text, finish=parsed.finish, refusal=parsed.refusal,
                                           tool_calls=[{'id': c.id, 'name': c.name, 'arguments': c.arguments} for c in parsed.tool_calls],
                                           usage={'input_tokens': parsed.usage.input_tokens or 0, 'output_tokens': parsed.usage.output_tokens or 0},
                                           source='live', label=f'{self.inner.name}:{kwargs.get("model")}', prompt=self.prompt))
            except Exception as error:  # noqa: BLE001 - recording is best effort by design
                self.failures.append(type(error).__name__)
                _log.warning('replay recording skipped: %s', type(error).__name__)
        return result
