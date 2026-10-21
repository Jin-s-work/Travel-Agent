"""Read and ask MCP tool definitions over the local Going API (no ``mcp`` import).

Each tool is a thin wrapper over one allowlisted endpoint. There is no apply,
undo, delete, send, upload, token-management or URL-fetch tool, and the API
refuses Bearer writes anyway. Results are size-capped and long free-text fields
(mail snippets, refund policies) are shortened, because they are untrusted data
for the calling model, not instructions.
"""
from __future__ import annotations

import json
import re

from going_client import GoingAPIError, segment

MAX_OUTPUT_CHARS = 20000
MAX_TEXT_FIELD = 600
DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
LONG_TEXT_FIELDS = {'raw_snippet', 'refund_policy', 'note', 'document', 'excerpt'}

ID = {'type': 'string', 'minLength': 1, 'maxLength': 200}
DATE_SCHEMA = {'type': 'string', 'pattern': DATE.pattern}


def _schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


def _shorten(value):
    if isinstance(value, dict):
        return {key: (item[:MAX_TEXT_FIELD] + '…' if key in LONG_TEXT_FIELDS and isinstance(item, str) and len(item) > MAX_TEXT_FIELD
                      else _shorten(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_shorten(item) for item in value]
    return value


def _cap(payload):
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= MAX_OUTPUT_CHARS:
        return payload
    return {'truncated': True, 'max_output_chars': MAX_OUTPUT_CHARS, 'partial_json': text[:MAX_OUTPUT_CHARS]}


def _trip_list(client, args):
    data = client.get('/trips', {'limit': 100})
    return {'items': [{k: t.get(k) for k in ('id', 'title', 'start_date', 'end_date', 'version')} for t in data.get('items', [])],
            'count': data.get('count')}


def _trip(client, args):
    return client.get(f"/trips/{segment(args['trip_id'])}")


def _bookings(client, args):
    params = {'date_from': args.get('date_from'), 'date_to': args.get('date_to'), 'limit': 100}
    return client.get(f"/trips/{segment(args['trip_id'])}/bookings", params)


def _saved(client, args):
    return client.get(f"/trips/{segment(args['trip_id'])}/bookmarks")


def _place(client, args):
    return client.get(f"/trips/{segment(args['trip_id'])}/places/{segment(args['place_id'])}/detail")


def _itineraries(client, args):
    return client.get(f"/trips/{segment(args['trip_id'])}/itineraries", {'limit': 20})


ITINERARY_ITEM_KEYS = ('item_id', 'place_id', 'title', 'name', 'category', 'local_start', 'local_end', 'booking_id', 'locked')


def _itinerary(client, args):
    # 앱 응답의 snapshot(숙소·출발점 좌표와 라벨, stays/overrides)과 legs(출발 지점 좌표)는
    # MCP 클라이언트로 넘기지 않는다. src/trip_agent/tools.py get_itinerary 와 같은 허용 목록만 남긴다.
    data = client.get(f"/trips/{segment(args['trip_id'])}/itineraries/{segment(args['itinerary_id'])}")
    items = [{key: item.get(key) for key in ITINERARY_ITEM_KEYS}
             for item in data.get('items') or [] if isinstance(item, dict)][:60]
    return {'itinerary_id': data.get('id') or data.get('itinerary_id'), 'version': data.get('version'),
            'validation_status': data.get('validation_status'), 'data_status': data.get('data_status'),
            'items': items, 'conflicts': [c.get('code') for c in data.get('conflicts') or [] if isinstance(c, dict)],
            'can_undo': data.get('can_undo')}


def _ask(client, args):
    data = client.ask(args['trip_id'], args['question'])
    keep = ('answer', 'sources', 'answer_mode', 'grounding', 'answer_type', 'checked_scope', 'tools_used')
    result = {k: data[k] for k in keep if k in data}
    previews = data.get('previews') or []
    if previews:
        # MCP cannot apply: the change is only a preview the user may apply in the app.
        result['previews'] = [{'preview_id': p.get('preview_id'), 'kind': p.get('kind'), 'summary': p.get('summary'),
                               'apply_requires_user_click': True} for p in previews]
        result['note'] = '일정 변경은 미리보기일 뿐입니다. 적용은 고잉 앱 화면에서 사용자가 직접 눌러야 합니다.'
    return result


# name -> (description, input schema, HTTP method used, handler)
TOOLS = {
    'list_trips': ('내 여행 목록(제목·기간·버전)을 읽습니다.', _schema(), 'GET', _trip_list),
    'get_trip': ('여행 하나의 기본 정보(기간·도시 경유지·인원)를 읽습니다.', _schema({'trip_id': ID}, ['trip_id']), 'GET', _trip),
    'list_bookings': ('여행의 예약 목록을 읽습니다. 날짜(YYYY-MM-DD)로 좁힐 수 있습니다.',
                      _schema({'trip_id': ID, 'date_from': DATE_SCHEMA, 'date_to': DATE_SCHEMA}, ['trip_id']), 'GET', _bookings),
    'list_saved_places': ('여행 보관함에 저장한 장소를 읽습니다.', _schema({'trip_id': ID}, ['trip_id']), 'GET', _saved),
    'get_place': ('장소 상세(확인된 사실·출처)를 읽습니다.', _schema({'trip_id': ID, 'place_id': ID}, ['trip_id', 'place_id']), 'GET', _place),
    'list_itineraries': ('여행의 일정 목록을 읽습니다.', _schema({'trip_id': ID}, ['trip_id']), 'GET', _itineraries),
    'get_itinerary': ('일정 하나의 항목·버전·검증 상태·충돌 코드를 읽습니다. 숙소·출발점 위치는 보내지 않습니다.', _schema({'trip_id': ID, 'itinerary_id': ID}, ['trip_id', 'itinerary_id']), 'GET', _itinerary),
    'ask_trip': ('여행 범위 안에서 예약·일정에 대해 질문합니다. 답은 근거와 함께 오며, 변경은 미리보기까지만 나옵니다.',
                 _schema({'trip_id': ID, 'question': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}, ['trip_id', 'question']),
                 'POST', _ask),
}


# ask_trip never changes trip data, but it is not a pure read: it may spend the
# answer budget and schedule a search-index rebuild, and repeating it is not free.
READ_ONLY = frozenset(name for name, spec in TOOLS.items() if spec[2] == 'GET')


def tool_specs():
    return [{'name': name, 'description': spec[0], 'input_schema': spec[1], 'read_only': name in READ_ONLY}
            for name, spec in TOOLS.items()]


def _validate(schema, args):
    if not isinstance(args, dict):
        return 'arguments must be an object'
    props = schema['properties']
    extra = set(args) - set(props)
    if extra:
        return 'unknown argument: ' + ', '.join(sorted(extra))
    for key in schema['required']:
        if args.get(key) in (None, ''):
            return 'missing argument: ' + key
    for key, value in args.items():
        if value is None:
            continue
        rule = props[key]
        if not isinstance(value, str):
            return key + ' must be a string'
        if len(value) < rule.get('minLength', 0) or len(value) > rule.get('maxLength', 10 ** 6):
            return key + ' has an invalid length'
        if 'pattern' in rule and not re.match(rule['pattern'], value):
            return key + ' must look like YYYY-MM-DD'
    return None


def call_tool(client, name, args=None):
    """Run one tool. Never raises: failures come back as ``{'ok': False, ...}``."""
    spec = TOOLS.get(name)
    if spec is None:
        return {'ok': False, 'error_code': 'UNKNOWN_TOOL', 'message': '등록되지 않은 도구입니다.'}
    args = {} if args is None else args
    problem = _validate(spec[1], args)
    if problem:
        return {'ok': False, 'error_code': 'INVALID_ARGUMENT', 'message': problem}
    clean = {k: v for k, v in args.items() if v is not None}
    try:
        data = spec[3](client, clean)
    except GoingAPIError as exc:
        return {'ok': False, 'error_code': exc.code, 'status': exc.status, 'message': exc.message}
    return {'ok': True, 'data': _cap(_shorten(data))}
