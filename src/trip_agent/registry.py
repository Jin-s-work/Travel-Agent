"""Tool registry: name, description, strict JSON schema, kind, output bound.

A bad tool name or bad arguments come back as an error result so the loop can
continue. 예외는 하나뿐이다: 비용·전송 상태를 알 수 없거나 외부 호출이 막힌
DomainError(LOOP_STOP_CODES)는 결과로 바꾸지 않고 루프로 다시 던진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Callable

from src.foundation.repository import DomainError

KINDS = ('read', 'preview', 'draft')
# Capabilities the agent must never have. A registry containing any of these
# names (or a kind outside KINDS) refuses to build.
FORBIDDEN_TOOL_NAMES = frozenset({
    'apply_itinerary_change', 'apply_preview', 'undo_itinerary_change', 'undo', 'delete_booking',
    'delete_trip', 'delete_document', 'cancel_booking', 'update_booking', 'send_message', 'send_email',
    'send_inquiry', 'fetch_url', 'open_url', 'http_get', 'browse',
})
_ARGUMENT_LIMIT = 4000

# 도구(예: search_mail 의 임베딩)가 계량 게이트웨이에서 받은 아래 오류는 "도구
# 실패" 결과로 모델에 돌려주지 않는다. 결과로 돌려주면 모델이 같은 도구를 다시
# 부르고, 도구는 호출마다 새 call_key 를 쓰므로 과금됐을 수 있는 요청이 같은
# 질문 안에서 재전송된다. 루프는 이 오류를 chat 단계 오류와 같은 규칙으로 처리한다.
# 코드 출처: src/reliability/budget.py · providers.py, src/llm/*_adapter.py
# (ProviderRejected 는 예약을 pending 으로 남긴다), src/operations/controls.py,
# src/foundation/routes.py 의 재시도 차단 집합.
BUDGET_STOP_CODES = frozenset({
    'BUDGET_EXHAUSTED', 'GLOBAL_BUDGET_STOPPED', 'GLOBAL_BUDGET_EXHAUSTED', 'BUDGET_GLOBAL_STOP', 'COST_BOUND_EXCEEDED',
})
DEADLINE_STOP_CODES = frozenset({'DEADLINE_EXCEEDED'})
PROVIDER_STOP_CODES = frozenset({
    # 응답·과금 불확정 (예약이 unknown/pending 으로 남음)
    'PROVIDER_OUTCOME_UNKNOWN', 'CALL_OUTCOME_UNKNOWN', 'USAGE_PENDING_RECONCILIATION', 'RECONCILIATION_REQUIRED',
    'PROVIDER_RATE_LIMITED', 'PROVIDER_AUTH_FAILED', 'PROVIDER_REQUEST_REJECTED',
    # 전송·과금 뒤 영수증 저장·검증 실패 (providers.py _persist_guarded·_replay) — 결과는 잃었지만
    # 비용은 이미 들었을 수 있다. 새 call_key 로 다시 부르면 같은 질의가 재전송된다.
    'PROVIDER_RECEIPT_INVALID',
    # 전송 안 됨·외부 호출 중지·비용 설정 없음 — 같은 질문 안에서 다시 불러도 나아지지 않음
    'PROVIDER_UNAVAILABLE', 'CALL_NOT_SENT', 'EXTERNAL_CALLS_PAUSED', 'PROVIDER_PAUSED', 'OPERATIONS_PAUSED',
    'BUDGET_NOT_CONFIGURED', 'COST_BOUND_REQUIRED',
})
LOOP_STOP_CODES = BUDGET_STOP_CODES | DEADLINE_STOP_CODES | PROVIDER_STOP_CODES


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    kind: str
    external: bool
    max_output_chars: int
    handler: Callable


@dataclass
class ToolOutcome:
    name: str
    ok: bool
    content: str
    error_code: str | None = None
    data: dict = field(default_factory=dict)


def _check_schema(name, schema):
    if schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
        raise ValueError(f'{name}: 입력 schema는 additionalProperties=false 인 object여야 합니다.')
    properties = schema.get('properties', {})
    if set(schema.get('required', [])) != set(properties):
        raise ValueError(f'{name}: strict 모드는 모든 속성을 required로 둡니다(선택값은 null 허용).')
    for key in ('user_id', 'owner_id', 'trip_id', 'session_id'):
        if key in properties:
            raise ValueError(f'{name}: 범위는 서버 actor가 정합니다. {key} 입력은 받지 않습니다.')


def _types(spec):
    value = spec.get('type')
    return set(value) if isinstance(value, list) else {value}


def validate(schema, value, path='args'):
    """Small strict validator for the subset our tool schemas use."""
    types = _types(schema)
    if value is None:
        if 'null' in types:
            return
        raise ValueError(f'{path}: 값이 필요합니다.')
    checks = {'object': dict, 'array': list, 'string': str, 'boolean': bool}
    if 'integer' in types and isinstance(value, int) and not isinstance(value, bool):
        pass
    elif 'number' in types and isinstance(value, (int, float)) and not isinstance(value, bool):
        pass
    elif not any(kind in checks and isinstance(value, checks[kind]) for kind in types):
        raise ValueError(f'{path}: 형식이 올바르지 않습니다.')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: 허용되지 않은 값입니다.')
    if isinstance(value, str):
        if len(value) > schema.get('maxLength', 1000) or len(value) < schema.get('minLength', 0):
            raise ValueError(f'{path}: 길이가 올바르지 않습니다.')
        if schema.get('pattern'):
            import re
            if not re.fullmatch(schema['pattern'], value):
                raise ValueError(f'{path}: 형식이 올바르지 않습니다.')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 'minimum' in schema and value < schema['minimum'] or 'maximum' in schema and value > schema['maximum']:
            raise ValueError(f'{path}: 범위를 벗어났습니다.')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        extra = set(value) - set(properties)
        missing = set(schema.get('required', [])) - set(value)
        if extra or missing:
            raise ValueError(f'{path}: 알 수 없는 인자 {sorted(extra)} 또는 누락 {sorted(missing)}')
        for key, item in value.items():
            validate(properties[key], item, f'{path}.{key}')
    if isinstance(value, list):
        if len(value) > schema.get('maxItems', 50):
            raise ValueError(f'{path}: 항목이 너무 많습니다.')
        for index, item in enumerate(value):
            validate(schema.get('items', {}), item, f'{path}[{index}]')


class ToolRegistry:
    def __init__(self, tools):
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError('도구 이름이 중복됩니다.')
        for tool in tools:
            if tool.name in FORBIDDEN_TOOL_NAMES or tool.kind not in KINDS:
                raise ValueError(f'{tool.name}: 에이전트에 허용되지 않은 도구입니다.')
            _check_schema(tool.name, tool.input_schema)
        self.tools = {tool.name: tool for tool in tools}

    def names(self):
        return sorted(self.tools)

    def specs(self):
        return [{'name': tool.name, 'description': tool.description, 'input_schema': tool.input_schema}
                for tool in self.tools.values()]

    def call(self, ctx, name, raw_arguments) -> ToolOutcome:
        tool = self.tools.get(name)
        if tool is None:
            return self._error(name, 'TOOL_NOT_FOUND', '등록되지 않은 도구입니다.')
        if not isinstance(raw_arguments, str) or len(raw_arguments) > _ARGUMENT_LIMIT:
            return self._error(name, 'TOOL_ARGUMENTS_INVALID', '도구 인자가 너무 깁니다.')
        try:
            arguments = json.loads(raw_arguments or '{}')
            validate(tool.input_schema, arguments)
        except ValueError as error:
            return self._error(name, 'TOOL_ARGUMENTS_INVALID', str(error)[:200])
        try:
            data = tool.handler(ctx, arguments)
        except DomainError as error:
            if error.code in LOOP_STOP_CODES:
                raise  # 비용·전송 불확정: 모델이 재호출하지 못하게 루프가 멈춘다
            # Another trip's/user's id is NOT_FOUND exactly like the HTTP API.
            return self._error(name, error.code, '도구를 실행하지 못했습니다.')
        except Exception:  # noqa: BLE001 - a tool failure is a result, not a crash
            return self._error(name, 'TOOL_FAILED', '도구를 실행하지 못했습니다.')
        # `_previews`/`_drafts`/`_sources` are for the screen (the loop collects
        # them from `data`); they repeat the facts with ids and file names, so the
        # model sees only the rest and the output bound is spent on facts.
        shown = {k: v for k, v in data.items() if not str(k).startswith('_')} if isinstance(data, dict) else data
        text = json.dumps(shown, ensure_ascii=False, sort_keys=True, default=str)
        if len(text) > tool.max_output_chars:
            text = text[:tool.max_output_chars] + '…(잘림)'
        return ToolOutcome(name=name, ok=True, content=text, data=data if isinstance(data, dict) else {'value': data})

    @staticmethod
    def _error(name, code, message):
        return ToolOutcome(name=name, ok=False, error_code=code,
                           content=json.dumps({'error': code, 'message': message}, ensure_ascii=False))
