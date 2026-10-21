"""The agent's ten tools. Each only wraps an existing service function.

Scope (user, session, trip) always comes from `AgentContext`, which the server
builds from the authenticated actor. Outputs are trimmed to what an answer
needs; personal coordinates (accommodation, chosen origin) are never returned,
so they are never sent to a model provider.
"""
from __future__ import annotations

from src.foundation.repository import DomainError

from .registry import Tool, ToolRegistry

NULL_STR = {'type': ['string', 'null'], 'maxLength': 200}
DATE = {'type': ['string', 'null'], 'pattern': r'\d{4}-\d{2}-\d{2}'}
_MAX_BOOKINGS = 40


def _obj(**properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def _feature(ctx, name):
    settings = getattr(ctx.services, 'settings', None)
    if settings is not None and not getattr(settings, name + '_enabled', True):
        raise DomainError('FEATURE_DISABLED', '이 기능은 꺼져 있습니다.', 409)


def _booking_fact(booking):
    from src.foundation.search import facts
    value = facts(booking)
    party = booking.get('party')
    if isinstance(party, dict):
        value['party'] = {'adults': party.get('adults'), 'children': len(party.get('children') or [])}
    return value


def _source(booking, ctx):
    from src.foundation.search import source
    return source(booking, ctx.trip_id)


# --- read -----------------------------------------------------------------
def get_trip_context(ctx, args):
    trip = ctx.repo.get_trip(ctx.user_id, ctx.trip_id)
    result = {'title': trip.get('title'), 'start_date': trip.get('start_date'), 'end_date': trip.get('end_date'),
              'party': trip.get('party'),
              'stops': [{'city': stop.get('city'), 'start_date': stop.get('start_date'), 'end_date': stop.get('end_date'),
                         'timezone': stop.get('timezone')} for stop in trip.get('stops') or []]}
    discovery = getattr(ctx.services, 'discovery', None)
    if discovery is not None:
        try:
            context = discovery.get_conditions(ctx.actor, ctx.trip_id)
        except DomainError:
            context = None
        if context:
            conditions = dict(context.get('conditions') or {})
            # Accommodation/origin coordinates and addresses stay on the server.
            for private in ('origin', 'origin_context', 'base_location', 'accommodation'):
                conditions.pop(private, None)
            result['visit_conditions'] = conditions
            result['context_state'] = context.get('context_state')
    return result


def list_bookings(ctx, args):
    rows = ctx.repo.list_bookings(ctx.user_id, ctx.trip_id, date_from=args['date_from'], date_to=args['date_to'])
    return {'total': len(rows), 'truncated': len(rows) > _MAX_BOOKINGS,
            'bookings': [_booking_fact(row) for row in rows[:_MAX_BOOKINGS]],
            '_sources': [_source(row, ctx) for row in rows[:_MAX_BOOKINGS]]}


def search_mail(ctx, args):
    """v2 vector search with the stage 2 similarity floor; facts come from SQL."""
    from src.config import ANSWER_MIN_SIMILARITY, EMBEDDING_MODEL
    operations = ctx.operations
    documents = ctx.documents
    with operations.generations.reader(ctx.user_id, ctx.trip_id) as reader:
        if reader.generation['embedding_model'] != EMBEDDING_MODEL:
            raise DomainError('SEARCH_REBUILDING', '검색 자료를 복구 중입니다.', 503)
        key = f"{ctx.request_id}:agent-search:{ctx.extras.setdefault('searches', 0)}"
        ctx.extras['searches'] += 1

        def guard():
            documents.ensure_active_user(ctx.user_id, ctx.session_id)
            ctx.repo.get_trip(ctx.user_id, ctx.trip_id)
        vector = operations.embed(operations.context(ctx.user_id, ctx.trip_id), key, [args['query']], guard, query=True)[0]
        hits = reader.query(vector, top_k=5)
    kept = [hit for hit in hits if (hit.get('similarity') or 0) >= ANSWER_MIN_SIMILARITY]
    ids = {hit['metadata']['document_id'] for hit in kept}
    rows = [row for row in ctx.repo.list_bookings(ctx.user_id, ctx.trip_id) if row.get('document_id') in ids]
    return {'matches': [_booking_fact(row) for row in rows[:_MAX_BOOKINGS]], 'below_floor': not kept,
            'min_similarity': ANSWER_MIN_SIMILARITY, '_sources': [_source(row, ctx) for row in rows[:_MAX_BOOKINGS]]}


def list_saved_places(ctx, args):
    saved = ctx.services.discovery.list_bookmarks(ctx.actor, ctx.trip_id)
    places = []
    for item in saved[:50]:
        place = item.get('place') or {}
        places.append({'bookmark_id': item['id'], 'place_id': item.get('matched_place_id'), 'name': place.get('display_name'),
                       'native_name': place.get('native_name'), 'city': place.get('city'), 'state': item.get('resolve_state'),
                       'excluded': item.get('excluded')})
    return {'total': len(saved), 'places': places}


def get_place(ctx, args):
    detail = ctx.services.discovery.detail(ctx.actor, ctx.trip_id, args['place_id'])
    place = detail.get('place') or {}
    facts = [{'field': fact.get('field'), 'value': fact.get('value'), 'status': fact.get('status')}
             for fact in detail.get('facts') or [] if isinstance(fact, dict)][:30]
    return {'place': {key: place.get(key) for key in ('id', 'name', 'native_name', 'city', 'address', 'categories', 'tags', 'attribution')},
            'facts': facts, 'excluded': detail.get('excluded'), 'live_availability': detail.get('live_availability')}


def _pick_itinerary(ctx, itinerary_id):
    itineraries = ctx.services.itineraries
    if itinerary_id:
        return itineraries.get(ctx.actor, ctx.trip_id, itinerary_id)
    listing = itineraries.list(ctx.actor, ctx.trip_id)
    items = listing.get('items', listing) if isinstance(listing, dict) else listing
    active = [item for item in items if item.get('active_revision_id')]
    if not active:
        raise DomainError('ITINERARY_NOT_READY', '적용된 일정이 아직 없습니다.', 404)
    latest = max(active, key=lambda item: item.get('updated_at') or '')
    return itineraries.get(ctx.actor, ctx.trip_id, latest.get('id') or latest.get('itinerary_id'))


def get_itinerary(ctx, args):
    value = _pick_itinerary(ctx, args['itinerary_id'])
    items = [{key: item.get(key) for key in ('item_id', 'place_id', 'title', 'name', 'category', 'local_start', 'local_end',
                                              'booking_id', 'locked')} for item in value.get('items') or []][:60]
    return {'itinerary_id': value.get('id') or value.get('itinerary_id'), 'version': value.get('version'),
            'validation_status': value.get('validation_status'), 'data_status': value.get('data_status'),
            'items': items, 'conflicts': [c.get('code') for c in value.get('conflicts') or [] if isinstance(c, dict)],
            'can_undo': value.get('can_undo')}


# --- preview ----------------------------------------------------------------
# lock/unlock are left out: the agent proposes visits, it does not change protection.
_COMMAND = _obj(op={'type': 'string', 'enum': ['add', 'move', 'remove']},
                item_id=NULL_STR, place_id=NULL_STR,
                local_start={'type': ['string', 'null'], 'pattern': r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?'},
                duration_minutes={'type': ['integer', 'null'], 'minimum': 5, 'maximum': 720})
_FIELDS = {'add': ('place_id', 'local_start', 'duration_minutes'), 'move': ('item_id', 'local_start', 'duration_minutes'),
           'remove': ('item_id',)}


def preview_itinerary_change(ctx, args):
    commands = []
    for command in args['commands']:
        value = {'op': command['op']}
        for field in _FIELDS[command['op']]:
            if command.get(field) is not None:
                value[field] = command[field]
        commands.append(value)
    preview = ctx.services.itineraries.preview(ctx.actor, ctx.trip_id, args['itinerary_id'],
                                               {'expected_version': args['expected_version'], 'commands': commands})
    diff = preview.get('diff') or {}
    summary = {'preview_id': preview.get('preview_id'), 'itinerary_id': preview.get('itinerary_id'),
               'base_version': preview.get('base_version'), 'expires_at': preview.get('expires_at'),
               'can_apply': preview.get('can_apply'), 'validation_status': preview.get('validation_status'),
               'added': [item.get('item_id') if isinstance(item, dict) else item for item in diff.get('added') or []],
               'removed': [item.get('item_id') if isinstance(item, dict) else item for item in diff.get('removed') or []],
               'changed': [item.get('item_id') if isinstance(item, dict) else item for item in diff.get('changed') or []],
               'affected_dates': preview.get('affected_dates'),
               'conflicts': [c.get('code') for c in preview.get('conflicts') or [] if isinstance(c, dict)],
               'applied': False, 'apply_requires_user_click': True}
    return {**summary, '_previews': [{**summary, 'kind': 'edit'}]}


def suggest_plan_b(ctx, args):
    _feature(ctx, 'plan_b')
    from src.travel_tools.models import Alternative
    body = Alternative.model_validate({'expected_version': args['expected_version'], 'item_id': args['item_id'],
                                       'reason': args['reason'], 'basis': 'user_report'}).model_dump(mode='json')
    # No origin is passed: the saved starting point is used server-side and
    # never echoed back to the model.
    result = ctx.services.alternatives.propose(ctx.actor, ctx.trip_id, args['itinerary_id'], body)
    options = []
    reason = (result.get('situation') or {}).get('reason') or args['reason']
    for alternative in result.get('alternatives') or []:
        comparison = alternative.get('comparison') or {}
        after = comparison.get('after') or {}
        before = comparison.get('before') or {}
        # The card shows what is replaced and how many conditions stay
        # unconfirmed, as the existing Plan B view does before its apply.
        options.append({'preview_id': alternative.get('preview_id'), 'base_version': alternative.get('base_version'),
                        'can_apply': alternative.get('can_apply'), 'place_id': after.get('place_id'),
                        'name': after.get('name') or after.get('title'), 'local_start': after.get('local_start'),
                        'before': {'name': before.get('name') or before.get('title'), 'local_start': before.get('local_start')},
                        'unresolved': sorted({x.get('code') for x in alternative.get('unresolved_conditions') or []
                                              if isinstance(x, dict) and x.get('code')}),
                        'reason': reason, 'indoor': comparison.get('indoor'), 'applied': False})
    return {'alternatives': options, 'reason_codes': result.get('reason_codes'), 'situation': result.get('situation'),
            'limits': result.get('limits'), 'external_action': 'none',
            '_previews': [{**option, 'itinerary_id': args['itinerary_id'], 'kind': 'plan_b',
                           'apply_requires_user_click': True} for option in options]}


# --- read (external) --------------------------------------------------------
def get_weather(ctx, args):
    weather = getattr(ctx.services, 'weather', None)
    if weather is None:
        from src.travel_tools.weather import unknown
        return unknown('WEATHER_DISABLED')
    return weather.forecast(ctx.actor, ctx.trip_id, args['date'])


# --- draft --------------------------------------------------------------------
def draft_inquiry(ctx, args):
    _feature(ctx, 'preparation')
    preparation = ctx.services.preparation
    if not args['task_id']:
        tasks = preparation.list(ctx.actor, ctx.trip_id).get('items', [])
        return {'needs_task_choice': True,
                'tasks': [{'task_id': task.get('id'), 'title': task.get('title'), 'place_name': task.get('place_name'),
                           'visit_date': task.get('visit_date'), 'status': task.get('status')} for task in tasks[:20]]}
    from src.travel_tools.models import Inquiry
    task = preparation.get(ctx.actor, ctx.trip_id, args['task_id'])
    body = Inquiry.model_validate({'expected_version': task['version'], 'language': args['language']}).model_dump()
    draft = preparation.draft(ctx.actor, ctx.trip_id, args['task_id'], body)
    value = {'task_id': args['task_id'], 'language': args['language'], 'text': draft.get('text'),
             'korean': draft.get('korean'), 'template_fallback': draft.get('template_fallback'), 'sent': False}
    return {**value, '_drafts': [value]}


def build_registry(*, read_only: bool = False) -> ToolRegistry:
    """All ten tools, or only the `read` kind when `read_only`.

    A personal-token (Bearer) actor may only read and ask, so preview and
    draft tools, which store rows (itinerary_previews, draft records), are not
    offered to it at all; a call to one is then TOOL_NOT_FOUND.
    """
    tools = _tools()
    return ToolRegistry([tool for tool in tools if tool.kind == 'read'] if read_only else tools)


def _tools():
    return ([
        Tool('get_trip_context', '현재 여행의 제목·기간·인원·도시 일정과 방문 조건(개인 좌표 제외)을 읽는다.', _obj(), 'read', False, 3000, get_trip_context),
        Tool('list_bookings', '현재 여행의 예약 사실(교정값 반영)을 날짜 범위로 모두 읽는다. 날짜를 모르면 둘 다 null.',
             _obj(date_from=DATE, date_to=DATE), 'read', False, 12000, list_bookings),
        Tool('search_mail', '현재 여행의 메일에서 질문과 관련된 예약을 찾는다. 유사도 하한 미만이면 결과가 없다.',
             _obj(query={'type': 'string', 'minLength': 1, 'maxLength': 300}), 'read', False, 8000, search_mail),
        Tool('list_saved_places', '현재 여행에 저장한 장소 목록을 읽는다.', _obj(), 'read', False, 6000, list_saved_places),
        Tool('get_place', '장소 하나의 공개 사실(이름·주소·범주·확인된 사실)을 읽는다.',
             _obj(place_id={'type': 'string', 'minLength': 1, 'maxLength': 100}), 'read', False, 6000, get_place),
        Tool('get_itinerary', '현재 여행의 적용된 일정을 읽는다. itinerary_id를 모르면 null(가장 최근 일정).',
             _obj(itinerary_id=NULL_STR), 'read', False, 10000, get_itinerary),
        Tool('preview_itinerary_change', '일정 변경 미리보기만 만든다. 적용하지 않으며, 적용은 사용자가 카드에서 누른다.',
             _obj(itinerary_id={'type': 'string', 'minLength': 1, 'maxLength': 150},
                  expected_version={'type': 'integer', 'minimum': 1, 'maximum': 100000},
                  commands={'type': 'array', 'maxItems': 20, 'items': _COMMAND}), 'preview', False, 4000, preview_itinerary_change),
        Tool('suggest_plan_b', '일정 항목 하나의 대안 미리보기(최대 3개)를 만든다. 적용하지 않는다.',
             _obj(itinerary_id={'type': 'string', 'minLength': 1, 'maxLength': 150},
                  expected_version={'type': 'integer', 'minimum': 1, 'maximum': 100000},
                  item_id={'type': 'string', 'minLength': 1, 'maxLength': 150},
                  reason={'type': 'string', 'enum': ['rain', 'closed', 'long_queue', 'fatigue', 'reservation_failed']}),
             'preview', False, 5000, suggest_plan_b),
        Tool('get_weather', '여행 도시의 일별 예보(강수 확률·강수량·최고/최저기온)를 읽는다. 도시 공개 좌표만 쓴다. 실패하면 미확인.',
             _obj(date=DATE), 'read', True, 2000, get_weather),
        Tool('draft_inquiry', '예약 준비 항목의 문의 초안을 만든다. 보내지 않는다. task_id를 모르면 null(목록을 돌려줌).',
             _obj(task_id=NULL_STR, language={'type': 'string', 'enum': ['ja', 'es', 'ca']}), 'draft', False, 4000, draft_inquiry),
    ])
