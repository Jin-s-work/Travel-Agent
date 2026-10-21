"""Stage 3 S3-01/S3-02 tools: registry contract and every tool against the real app.

Synthetic users A/B, the real create_app with fake parser/embedder fixtures
(tests.test_foundation_api / test_itinerary_api). Tools are called through the
registry exactly as the loop calls them; no model and no network are involved.
"""
from datetime import datetime, timezone
import json
import re

import pytest

from src.foundation.auth import Actor, digest
from src.trip_agent.loop import AgentContext
from src.trip_agent.registry import FORBIDDEN_TOOL_NAMES, KINDS
from src.trip_agent.tools import build_registry
from tests.test_discovery_foundation import bookmark, discovery  # noqa: F401 - fixtures
from tests.test_foundation_api import _fact, _trip, _upload, service  # noqa: F401 - fixtures
from tests.test_itinerary_api import done, planning, prepare, submit  # noqa: F401 - fixtures

EXPECTED_TOOLS = {'get_trip_context', 'list_bookings', 'search_mail', 'list_saved_places', 'get_place', 'get_itinerary',
                  'preview_itinerary_change', 'suggest_plan_b', 'get_weather', 'draft_inquiry'}
SCOPE_KEYS = {'user_id', 'owner_id', 'trip_id', 'session_id', 'actor_id'}
PRIVATE_KEYS = {'latitude', 'longitude', 'lat', 'lng', 'lon', 'owner_id', 'actor_id', 'origin', 'base_location'}
# tests.discovery_synthetic.conditions(): the saved starting point.
ORIGIN_LABEL, ORIGIN_VALUES = '합성 출발점', (35.68, 139.77)


def _walk_schema(schema, path='schema'):
    yield path, schema
    for key, child in (schema.get('properties') or {}).items():
        yield from _walk_schema(child, f'{path}.{key}')
    if isinstance(schema.get('items'), dict):
        yield from _walk_schema(schema['items'], path + '[]')


def _keys_and_numbers(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield 'key', key
            yield from _keys_and_numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys_and_numbers(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield 'number', value


def assert_no_private_location(outcome):
    assert outcome.ok, outcome.content
    parsed = json.loads(outcome.content)
    found = list(_keys_and_numbers(parsed))
    assert not {value for kind, value in found if kind == 'key'} & PRIVATE_KEYS, outcome.content
    assert not any(kind == 'number' and value in ORIGIN_VALUES for kind, value in found), outcome.content
    assert ORIGIN_LABEL not in outcome.content


def actor_for(app, token):
    with app.state.db.connect() as con:
        row = con.execute('SELECT u.id,u.email,u.display_name,u.role,s.id AS session_id,s.csrf_token,s.expires_at '
                          'FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?', (digest(token),)).fetchone()
    return Actor(**dict(row))


def agent_ctx(app, actor, trip_id, request_id='tool-test'):
    state = app.state
    return AgentContext(repo=state.repo, documents=state.documents, operations=state.operations, user_id=actor.id,
                        trip_id=trip_id, request_id=request_id, session_id=actor.session_id, actor=actor, services=state)


def call(ctx, name, **args):
    return build_registry().call(ctx, name, json.dumps(args))


def itinerary_row(app, itinerary_id):
    with app.state.db.connect() as con:
        row = con.execute('SELECT version,active_revision_id,undo_stack_json,updated_at FROM itineraries WHERE id=?',
                          (itinerary_id,)).fetchone()
        previews = con.execute('SELECT COUNT(*) FROM itinerary_previews WHERE itinerary_id=?', (itinerary_id,)).fetchone()[0]
    return tuple(row), previews


@pytest.fixture
def world(planning):
    """A: a trip with a fixed booking, saved conditions (with origin), an applied
    itinerary, a bookmark and a reservation task. A2: A's second trip. B: another user."""
    trip, pack, selected, reserved = prepare(planning, booking=True)
    itinerary, path = done(planning, trip, submit(planning, trip, selected, allow_provisional=True))
    bookmark(planning.client, trip, 'place', selected)
    task = planning.client.post(f"/api/v2/trips/{trip['id']}/reservation-tasks",
                                json={'title': '예약 문의', 'visit_date': '2026-11-06', 'timezone': 'Asia/Tokyo'},
                                headers={'Idempotency-Key': 'agent-prep-1'})
    assert task.status_code == 201, task.text
    second = _trip(planning.client, 'Second')
    other = planning.login('agent-B')
    other_trip = _trip(other.client, 'B trip')
    actor_a, actor_b = actor_for(planning.app, planning.admin.token), actor_for(planning.app, other.token)
    return dict(app=planning.app, client=planning.client, trip=trip, selected=selected, reserved=reserved,
                itinerary=itinerary, path=path, task=task.json(),
                a=agent_ctx(planning.app, actor_a, trip['id']), a2=agent_ctx(planning.app, actor_a, second['id']),
                b=agent_ctx(planning.app, actor_b, other_trip['id']),
                b_on_a=agent_ctx(planning.app, actor_b, trip['id']))


def place_item(itinerary):
    return next(item for item in itinerary['items'] if item.get('place_id') and not item.get('locked'))


# --- S3-01 ---------------------------------------------------------------------
def test_registry_has_only_read_preview_draft_tools_with_strict_unscoped_schemas():
    registry = build_registry()
    names = set(registry.names())
    assert names == EXPECTED_TOOLS
    assert not names & FORBIDDEN_TOOL_NAMES
    assert not [name for name in names if re.search(r'apply|undo|delete|remove|send|fetch|cancel|update|browse|url', name)]
    kinds = {tool.name: tool.kind for tool in registry.tools.values()}
    assert set(kinds.values()) <= set(KINDS) == {'read', 'preview', 'draft'}
    assert {name for name, kind in kinds.items() if kind == 'preview'} == {'preview_itinerary_change', 'suggest_plan_b'}
    assert {name for name, kind in kinds.items() if kind == 'draft'} == {'draft_inquiry'}
    assert {tool.name for tool in registry.tools.values() if tool.external} == {'get_weather'}
    for spec in registry.specs():
        for path, schema in _walk_schema(spec['input_schema'], spec['name']):
            if 'object' in (schema['type'] if isinstance(schema['type'], list) else [schema['type']]):
                assert schema.get('additionalProperties') is False, path
                assert set(schema.get('required', [])) == set(schema.get('properties', {})), path
                assert not set(schema.get('properties', {})) & SCOPE_KEYS, path


# --- scope: other user / other trip -------------------------------------------------
def test_other_user_and_other_trip_ids_are_not_found_results(world):
    app, itinerary, task = world['app'], world['itinerary'], world['task']
    item = place_item(itinerary)
    before = itinerary_row(app, itinerary['id'])
    for ctx in (world['a2'], world['b']):
        outcomes = [
            call(ctx, 'get_itinerary', itinerary_id=itinerary['id']),
            call(ctx, 'preview_itinerary_change', itinerary_id=itinerary['id'], expected_version=1,
                 commands=[{'op': 'move', 'item_id': item['item_id'], 'place_id': None,
                            'local_start': '2026-11-06T12:00', 'duration_minutes': None}]),
            call(ctx, 'suggest_plan_b', itinerary_id=itinerary['id'], expected_version=1, item_id=item['item_id'],
                 reason='closed'),
            call(ctx, 'draft_inquiry', task_id=task['id'], language='ja'),
        ]
        assert [(o.ok, o.error_code) for o in outcomes] == [(False, 'NOT_FOUND')] * 4, [o.content for o in outcomes]
        assert all(world['task']['id'] not in o.content for o in outcomes)
    # A server context bound to someone else's trip fails closed for every read.
    b_on_a = world['b_on_a']
    for name, args in (('get_trip_context', {}), ('list_bookings', {'date_from': None, 'date_to': None}),
                       ('list_saved_places', {}), ('get_itinerary', {'itinerary_id': None})):
        outcome = call(b_on_a, name, **args)
        assert (outcome.ok, outcome.error_code) == (False, 'NOT_FOUND'), (name, outcome.content)
        assert 'Synthetic fixed tour' not in outcome.content
    assert itinerary_row(app, itinerary['id']) == before


def test_scope_arguments_are_rejected_before_any_handler(world):
    outcome = call(world['b'], 'list_bookings', date_from=None, date_to=None, trip_id=world['trip']['id'])
    assert (outcome.ok, outcome.error_code) == (False, 'TOOL_ARGUMENTS_INVALID')
    outcome = call(world['b'], 'get_itinerary', itinerary_id=None, user_id=world['a'].user_id)
    assert (outcome.ok, outcome.error_code) == (False, 'TOOL_ARGUMENTS_INVALID')


# --- outputs ---------------------------------------------------------------------------
def test_context_itinerary_and_plan_b_outputs_hide_origin_coordinates_and_actor_ids(world):
    ctx, itinerary = world['a'], world['itinerary']
    # The fixture really holds a private origin, and the raw itinerary carries coordinates.
    origin = ctx.services.discovery.get_conditions(ctx.actor, ctx.trip_id)['conditions']['origin']
    assert (origin['latitude'], origin['longitude']) == ORIGIN_VALUES and 'latitude' in json.dumps(itinerary)
    context = call(ctx, 'get_trip_context')
    assert_no_private_location(context)
    value = json.loads(context.content)
    assert value['start_date'] == '2026-11-06' and value['visit_conditions']['city'] == 'tokyo'
    plan = call(ctx, 'get_itinerary', itinerary_id=None)
    assert_no_private_location(plan)
    assert json.loads(plan.content)['itinerary_id'] == itinerary['id']
    assert json.loads(plan.content)['version'] == 1
    before = itinerary_row(world['app'], itinerary['id'])
    alternatives = call(ctx, 'suggest_plan_b', itinerary_id=itinerary['id'], expected_version=1,
                        item_id=place_item(itinerary)['item_id'], reason='closed')
    assert_no_private_location(alternatives)
    result = json.loads(alternatives.content)
    assert result['alternatives'] and result['external_action'] == 'none'
    assert all(card['kind'] == 'plan_b' and card['apply_requires_user_click'] and card['applied'] is False
               for card in alternatives.data['_previews'])
    # The card names the replaced item and the reason, like the existing Plan B view.
    replaced = place_item(itinerary)
    assert all(card['reason'] == 'closed' and card['before']['local_start'] == replaced['local_start']
               and isinstance(card['unresolved'], list) for card in alternatives.data['_previews'])
    rows, previews = itinerary_row(world['app'], itinerary['id'])
    assert rows == before[0] and previews == before[1] + len(result['alternatives'])
    # The fixed booking is never replaced by Plan B.
    fixed = next(item for item in itinerary['items'] if item.get('booking_id'))
    guarded = call(ctx, 'suggest_plan_b', itinerary_id=itinerary['id'], expected_version=1, item_id=fixed['item_id'],
                   reason='reservation_failed')
    assert guarded.ok and json.loads(guarded.content)['alternatives'] == []


def test_list_bookings_returns_corrected_values_and_sources(service):
    a = service.login('A')
    trip = _trip(a.client)
    service.state.parsed = [_fact(provider='Alpha tour', time='09:00', raw_snippet='Old time 09:00')]
    _upload(a.client, trip['id'])
    booking = a.client.get(f"/api/v2/trips/{trip['id']}/bookings").json()['items'][0]
    patched = a.client.patch(f"/api/v2/trips/{trip['id']}/bookings/{booking['id']}", json={
        'expected_version': booking['version'], 'changes': [{'field_path': 'time', 'value': '14:00'}]})
    assert patched.status_code == 200, patched.text
    ctx = agent_ctx(service.app, actor_for(service.app, a.token), trip['id'])
    outcome = call(ctx, 'list_bookings', date_from='2026-11-07', date_to='2026-11-07')
    assert outcome.ok, outcome.content
    value = json.loads(outcome.content)
    assert value['total'] == 1 and value['bookings'][0]['time'] == '14:00'
    assert '09:00' not in outcome.content and 'raw_snippet' not in outcome.content
    assert outcome.data['_sources'][0]['booking_id'] == booking['id']
    # Screen-only keys stay out of what the model reads.
    assert '_sources' not in outcome.content and 'content_url' not in outcome.content
    assert json.loads(call(ctx, 'list_bookings', date_from='2026-11-08', date_to=None).content)['total'] == 0
    bad = call(ctx, 'list_bookings', date_from='tomorrow', date_to=None)
    assert (bad.ok, bad.error_code) == (False, 'TOOL_ARGUMENTS_INVALID')


def test_search_mail_uses_similarity_floor_and_sql_facts_in_scope(service, monkeypatch):
    a, b = service.login('A'), service.login('B')
    trip, other = _trip(a.client), _trip(b.client)
    service.state.parsed = [_fact(provider='Alpha tour', time='09:00')]
    _upload(a.client, trip['id'])
    ctx = agent_ctx(service.app, actor_for(service.app, a.token), trip['id'], request_id='search-1')
    found = call(ctx, 'search_mail', query='Alpha 투어')
    assert found.ok, found.content
    value = json.loads(found.content)
    assert value['below_floor'] is False and [m['provider'] for m in value['matches']] == ['Alpha tour']
    assert found.data['_sources'][0]['provider'] == 'Alpha tour'
    # Below the stage 2 floor nothing is returned, even though a vector hit exists.
    monkeypatch.setattr('src.config.ANSWER_MIN_SIMILARITY', 1.01)
    weak = call(ctx, 'search_mail', query='Alpha 투어')
    assert weak.ok and json.loads(weak.content)['matches'] == [] and json.loads(weak.content)['below_floor'] is True
    # B's server context cannot reach A's trip.
    b_on_a = agent_ctx(service.app, actor_for(service.app, b.token), trip['id'], request_id='search-2')
    denied = call(b_on_a, 'search_mail', query='Alpha 투어')
    assert not denied.ok and 'Alpha' not in denied.content
    # B's own indexed trip answers from B's mail only, never from A's (floor restored).
    monkeypatch.undo()
    service.state.parsed = [_fact(provider='Beta tour', time='11:00')]
    _upload(b.client, other['id'], key='upload-b')
    own = call(agent_ctx(service.app, actor_for(service.app, b.token), other['id'], request_id='search-3'), 'search_mail',
               query='Alpha 투어')
    assert own.ok, own.content
    assert [m['provider'] for m in json.loads(own.content)['matches']] == ['Beta tour']
    assert 'Alpha' not in own.content and 'Alpha' not in json.dumps(own.data, ensure_ascii=False)


def test_preview_tool_only_adds_a_preview_row_and_keeps_fixed_booking_and_version_guards(world):
    app, ctx, itinerary = world['app'], world['a'], world['itinerary']
    item = place_item(itinerary)
    target = '2026-11-06T14:00' if str(item.get('local_start', '')).startswith('2026-11-06T12:00') else '2026-11-06T12:00'
    before_row, before_previews = itinerary_row(app, itinerary['id'])
    outcome = call(ctx, 'preview_itinerary_change', itinerary_id=itinerary['id'], expected_version=1,
                   commands=[{'op': 'move', 'item_id': item['item_id'], 'place_id': None, 'local_start': target,
                              'duration_minutes': None}])
    assert outcome.ok, outcome.content
    value = json.loads(outcome.content)
    assert value['applied'] is False and value['apply_requires_user_click'] is True
    assert value['base_version'] == 1 and value['itinerary_id'] == itinerary['id'] and value['preview_id']
    assert outcome.data['_previews'] == [{**{k: v for k, v in outcome.data.items() if not k.startswith('_')}, 'kind': 'edit'}]
    row, previews = itinerary_row(app, itinerary['id'])
    assert row == before_row and previews == before_previews + 1
    with app.state.db.connect() as con:
        stored = con.execute('SELECT kind,owner_id,applied_revision_id FROM itinerary_previews WHERE id=?',
                             (value['preview_id'],)).fetchone()
    assert tuple(stored) == ('user_edit', ctx.user_id, None)
    assert world['client'].get(world['path']).json()['items'] == itinerary['items']
    fixed = next(i for i in itinerary['items'] if i.get('booking_id'))
    guarded = call(ctx, 'preview_itinerary_change', itinerary_id=itinerary['id'], expected_version=1,
                   commands=[{'op': 'remove', 'item_id': fixed['item_id'], 'place_id': None, 'local_start': None,
                              'duration_minutes': None}])
    assert (guarded.ok, guarded.error_code) == (False, 'BOOKING_EDIT_REQUIRED')
    stale = call(ctx, 'preview_itinerary_change', itinerary_id=itinerary['id'], expected_version=7,
                 commands=[{'op': 'move', 'item_id': item['item_id'], 'place_id': None, 'local_start': target,
                            'duration_minutes': None}])
    assert (stale.ok, stale.error_code) == (False, 'VERSION_CONFLICT')
    assert itinerary_row(app, itinerary['id']) == (before_row, before_previews + 1)


def test_draft_inquiry_is_never_sent_and_saved_places_and_weather_are_read_only(world, monkeypatch):
    ctx, task = world['a'], world['task']
    choice = call(ctx, 'draft_inquiry', task_id=None, language='ja')
    assert choice.ok and json.loads(choice.content)['needs_task_choice'] is True
    assert [t['task_id'] for t in json.loads(choice.content)['tasks']] == [task['id']]
    draft = call(ctx, 'draft_inquiry', task_id=task['id'], language='ja')
    assert draft.ok, draft.content
    value = json.loads(draft.content)
    assert value['sent'] is False and value['text'] and value['task_id'] == task['id']
    assert draft.data['_drafts'] == [{k: value[k] for k in ('task_id', 'language', 'text', 'korean', 'template_fallback', 'sent')}]
    after = world['client'].get(f"/api/v2/trips/{world['trip']['id']}/reservation-tasks/{task['id']}").json()
    assert after['version'] == task['version'] and after['status'] == task['status']
    saved = call(ctx, 'list_saved_places')
    assert saved.ok and json.loads(saved.content)['places'][0]['place_id'] == world['selected']
    place = call(ctx, 'get_place', place_id=world['selected'])
    assert place.ok and json.loads(place.content)['place']['id'] == world['selected']
    missing = call(ctx, 'get_place', place_id='place_does_not_exist')
    assert (missing.ok, missing.error_code) == (False, 'NOT_FOUND')
    # Test apps run with weather off (no network); the tool reports unknown, never sunny.
    weather = call(ctx, 'get_weather', date='2026-11-06')
    assert weather.ok and json.loads(weather.content)['status'] == 'unknown'
    # Weather on with a failing fake fetcher (no network): unknown, and the plan is untouched.
    from src.travel_tools.weather import Weather
    fetched = []

    def failing(url, **kwargs):
        fetched.append(url)
        raise OSError('synthetic provider failure')
    app = world['app']
    monkeypatch.setattr(app.state, 'weather', Weather(app.state.db, app.state.repo, app.state.discovery, fetcher=failing,
                                                      clock=lambda: datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)))
    before = itinerary_row(app, world['itinerary']['id'])
    failed = call(ctx, 'get_weather', date='2026-11-06')
    assert failed.ok, failed.content
    value = json.loads(failed.content)
    assert value['status'] == 'unknown' and 'daily' not in value and len(fetched) == 1
    assert itinerary_row(app, world['itinerary']['id']) == before


# --- personal-token actor: read tools only ------------------------------------------------
def test_read_only_registry_has_no_preview_or_draft_tool(world):
    registry = build_registry(read_only=True)
    assert set(registry.names()) == {n for n, t in build_registry().tools.items() if t.kind == 'read'}
    assert {tool.kind for tool in registry.tools.values()} == {'read'}
    app, itinerary = world['app'], world['itinerary']
    before = itinerary_row(app, itinerary['id'])
    outcome = registry.call(world['a'], 'preview_itinerary_change', json.dumps({
        'itinerary_id': itinerary['id'], 'expected_version': 1,
        'commands': [{'op': 'remove', 'item_id': place_item(itinerary)['item_id'], 'place_id': None,
                      'local_start': None, 'duration_minutes': None}]}))
    assert (outcome.ok, outcome.error_code) == (False, 'TOOL_NOT_FOUND')
    assert itinerary_row(app, itinerary['id']) == before
