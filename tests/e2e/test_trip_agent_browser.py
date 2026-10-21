"""V6 stage 3 browser scenario E06 (S3-07): agent answer -> preview card -> user apply -> reload.

Runs on its own synthetic fixture (scripts/stage3_agent_fixture.py): AI mail mode with local
fake parser/embedder, trip agent on, weather off, and a scripted model that reads the trip,
previews moving one itinerary item and answers. The model has no write tool; the only write
is the user's click on the existing PATCH /itineraries/{id}. E01-E05 and their fixture are
untouched. Opt-in: GOING_E2E=1. Pass criteria: docs/service-v6/prompts/03-trip-agent.md F-2.

The asking user is an ordinary member (only a separate operator approved the place pack).
"External 0" is checked twice: browser requests (track_external) and the fixture process,
whose sockets are loopback-only and log BLOCKED_EXTERNAL on any other attempt.
"""
import re
import time

import pytest

from tests.e2e.conftest import oidc_login, requires_e2e

pytestmark = requires_e2e
pytest.importorskip('playwright.sync_api', reason='Playwright not installed (install needs owner approval)')
from playwright.sync_api import expect  # noqa: E402

from tests.e2e.test_golden_path_browser import (  # noqa: E402
    _no_horizontal_scroll, _tab_until, select_trip, shot, tab, track_external, ui_login)

TRIP = 'A · 바르셀로나 에이전트 · 2026-11-06'
QUESTION = '일정에서 첫 장소를 두 시간 늦추는 안을 보여줘'
APPLY_FOCUSED = ("() => { const cards = document.querySelectorAll('#thread .agent-preview');"
                 " const last = cards[cards.length - 1];"
                 " return !!last && document.activeElement === last.querySelector('.itinerary-apply'); }")


@pytest.fixture(scope='module')
def agent_plan(stage3_agent_server):
    """An applied itinerary (version 1) made through the public API, as the user's own data."""
    from tests.discovery_synthetic import conditions
    api = oidc_login(stage3_agent_server, 'a')
    api.headers['Origin'] = stage3_agent_server['base']
    trips = api.get('/api/v2/trips').json()
    trip = next(t for t in (trips['items'] if isinstance(trips, dict) else trips) if t['title'] == 'A · 바르셀로나 에이전트')
    base = f"/api/v2/trips/{trip['id']}"
    response = api.patch(base + '/discovery-conditions', json={'expected_version': 0, 'conditions': conditions('barcelona', adults=2)})
    assert response.status_code == 200, response.text
    assert api.get('/api/v2/admin/discovery-packs').status_code == 404  # an ordinary member, not an admin
    catalog = api.get(base + '/discovery-catalog').json()['items']
    place = next(p['place_id'] for p in catalog if str(p.get('name', '')).startswith('합성 Barcelona 1 ·'))
    trip = api.get(base).json()
    response = api.post(base + '/itineraries/generation-previews', headers={'Idempotency-Key': 'e06-plan'}, json={
        'trip_version': trip['version'], 'conditions_version': 1, 'start_date': '2026-11-06', 'end_date': '2026-11-06',
        'selected': [{'place_id': place, 'duration_minutes': 60}], 'allow_provisional': True,
        'buffers': {'general_minutes': 0}, 'rest_preferences': {'minutes': 0}})
    assert response.status_code == 202, response.text
    receipt = response.json()
    deadline = time.monotonic() + 30
    while (job := api.get('/api/v2/jobs/' + receipt['job_id']).json())['state'] not in ('succeeded', 'partial', 'failed', 'cancelled'):
        assert time.monotonic() < deadline, job
        time.sleep(0.3)
    assert job['state'] in ('succeeded', 'partial'), job
    path = base + '/itineraries/' + receipt['itinerary_id']
    preview = api.get(path + '/generation-preview').json()
    applied = api.post(path + '/generation-preview/apply', json={'expected_version': 0, 'preview_id': preview['preview_id']})
    assert applied.status_code == 200 and applied.json()['version'] == 1, applied.text
    return {'api': api, 'path': path}


def ask_agent(page):
    tab(page, 'ask')
    view = page.locator('#view-ask')
    view.locator('#question').fill(QUESTION)
    view.locator('#sendQuestion').click()
    card = view.locator('#thread .agent-preview').last
    expect(card).to_be_visible(timeout=30000)
    return view, card


# POSTs the screen makes without being a data write: the question itself and sign-in/session plumbing.
ALLOWED_POST = re.compile(r'/api/v2/trips/[^/]+/ask$|/auth/|/login|/logout|/session')


def watch_writes(page):
    """Every non-read request except the allow-listed POSTs (so a POST apply path is caught too)."""
    writes = []

    def record(request):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        path = request.url.split('?', 1)[0]
        if request.method == 'POST' and ALLOWED_POST.search(path):
            return
        writes.append((request.method, request.url))
    page.on('request', record)
    return writes


def server_blocked(server):
    """Outbound attempts the loopback-only fixture refused (expected: none)."""
    return [line for line in (server['dir'] / 'server.log').read_text(errors='replace').splitlines() if 'BLOCKED_EXTERNAL' in line]


def test_e06_agent_question_preview_card_apply_and_reload(stage3_agent_server, agent_plan, new_page):
    """Pass: scope line + sources + preview card; no write before the click; apply → version +1 survives reload."""
    api, path = agent_plan['api'], agent_plan['path']
    before = api.get(path).json()
    page = new_page()
    external, writes = track_external(page), watch_writes(page)
    ui_login(page, stage3_agent_server, 'a')
    select_trip(page, TRIP)
    view, card = ask_agent(page)
    answer = view.locator('#thread .bubble.assistant').last
    expect(answer).to_contain_text('아직 적용하지 않았어요')
    expect(answer.locator('.agent-scope')).to_have_text('예약 1건 · 일정 항목 1개 확인')
    expect(answer.get_by_role('button', name='근거 1건 보기')).to_be_visible()
    expect(answer.locator('.agent-attribution')).to_have_count(0)  # weather is off in this fixture
    expect(card).to_contain_text('일정 변경 미리보기')
    expect(card).to_contain_text('아직 적용하지 않았습니다')
    expect(card).to_contain_text(f"기준 버전 {before['version']}")
    expect(card).to_contain_text('변경 예정 1개')
    apply = card.locator('.itinerary-apply')
    expect(apply).to_have_text('검증한 변경 적용')
    expect(apply).to_be_enabled()
    assert writes == []  # the agent answer and the card made no write
    assert api.get(path).json()['version'] == before['version']  # previewing never applies
    shot(page, 'e06-1-question-card')

    apply.click()
    expect(card.locator('.agent-status')).to_contain_text(f"새 일정 버전({before['version'] + 1})")
    expect(apply).to_be_disabled()
    expect(apply).to_have_text('적용함')
    assert [method for method, _ in writes] == ['PATCH'] and writes[0][1].endswith(path)

    page.reload()
    tab(page, 'itinerary')
    plan = page.locator('#view-itinerary')
    expect(plan).to_contain_text(f"일정 버전 {before['version'] + 1}")
    after = api.get(path).json()
    assert after['version'] == before['version'] + 1
    moved = [item for item in after['items'] if item.get('place_id')]
    original = [item for item in before['items'] if item.get('place_id')]
    assert moved[0]['local_start'] != original[0]['local_start']
    shot(page, 'e06-2-after-apply-reload')
    assert external == []
    assert server_blocked(stage3_agent_server) == []


def test_e06b_390px_keyboard_reaches_and_applies_card(stage3_agent_server, agent_plan, new_page):
    """Pass: no horizontal scroll at 390x844; Tab alone reaches the card button; Enter applies."""
    api, path = agent_plan['api'], agent_plan['path']
    version = api.get(path).json()['version']
    page = new_page(viewport={'width': 390, 'height': 844})
    external, writes = track_external(page), watch_writes(page)
    ui_login(page, stage3_agent_server, 'a')
    select_trip(page, TRIP)
    view, card = ask_agent(page)
    assert writes == []
    _no_horizontal_scroll(page)
    view.locator('#question').focus()
    _tab_until(page, APPLY_FOCUSED, limit=200)
    _no_horizontal_scroll(page)
    shot(page, 'e06-3-390px-keyboard')
    page.keyboard.press('Enter')
    expect(card.locator('.agent-status')).to_contain_text(f'새 일정 버전({version + 1})')
    assert api.get(path).json()['version'] == version + 1
    assert [method for method, _ in writes] == ['PATCH']
    _no_horizontal_scroll(page)
    assert external == []
    assert server_blocked(stage3_agent_server) == []


def test_e06c_text_size_200_card_and_button(stage3_agent_server, agent_plan, new_page):
    """Pass: with the 200% text setting the card, its text and the apply button stay visible inside 390px."""
    api, path = agent_plan['api'], agent_plan['path']
    version = api.get(path).json()['version']
    page = new_page(viewport={'width': 390, 'height': 844})
    page.add_init_script("try { localStorage.setItem('travel-inbox-text-size', 'large'); } catch (e) {}")
    external, writes = track_external(page), watch_writes(page)
    ui_login(page, stage3_agent_server, 'a')
    assert page.evaluate("() => document.documentElement.dataset.textSize") == 'large'
    select_trip(page, TRIP)
    view, card = ask_agent(page)
    expect(card).to_contain_text('아직 적용하지 않았습니다')
    apply = card.get_by_role('button', name='검증한 변경 적용')
    apply.scroll_into_view_if_needed()
    expect(apply).to_be_visible()
    expect(apply).to_be_enabled()
    box, card_box = apply.bounding_box(), card.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390 + 0.5, box
    assert card_box['x'] >= 0 and card_box['x'] + card_box['width'] <= 390 + 0.5, card_box
    assert box['height'] >= 44, box
    _no_horizontal_scroll(page)
    shot(page, 'e06-4-text-200')
    assert writes == [] and api.get(path).json()['version'] == version
    assert external == []
    assert server_blocked(stage3_agent_server) == []


def test_e06d_stale_preview_409_keeps_version_and_points_to_latest(stage3_agent_server, agent_plan, new_page):
    """Pass: the itinerary changes after the card was made -> click gets 409, nothing applied, latest-itinerary guidance."""
    api, path = agent_plan['api'], agent_plan['path']
    page = new_page()
    external, writes = track_external(page), watch_writes(page)
    ui_login(page, stage3_agent_server, 'a')
    select_trip(page, TRIP)
    view, card = ask_agent(page)
    before = api.get(path).json()
    expect(card).to_contain_text(f"기준 버전 {before['version']}")
    # Another change lands first (same user, public API): the card's base version is now stale.
    item = next(i for i in before['items'] if i.get('place_id') and not i.get('booking_id') and not i.get('locked'))
    start = item['local_start'][:16]
    body = None
    for hour in ('15:00', '16:00', '10:00', '11:00', '17:00'):  # first in-hours slot the validator accepts
        if start[11:16] == hour:
            continue
        preview = api.post(path + '/edit-previews', json={'expected_version': before['version'], 'commands': [
            {'op': 'move', 'item_id': item['item_id'], 'local_start': start[:11] + hour}]})
        assert preview.status_code in (200, 201), preview.text
        if preview.json().get('can_apply'):
            body = preview.json()
            break
    assert body, 'no applicable edit preview to make the card stale'
    bumped = api.patch(path, json={'preview_id': body['preview_id'], 'expected_version': body.get('base_version', before['version'])})
    assert bumped.status_code == 200 and bumped.json()['version'] == before['version'] + 1, bumped.text

    apply = card.locator('.itinerary-apply')
    apply.click()
    expect(card.locator('.agent-status')).to_contain_text('적용하지 않았습니다')
    expect(card.get_by_role('button', name='최신 일정 확인')).to_be_visible()
    expect(apply).to_be_disabled()
    assert [method for method, _ in writes] == ['PATCH']
    assert api.get(path).json()['version'] == before['version'] + 1  # the stale preview was not applied
    card.get_by_role('button', name='최신 일정 확인').click()
    expect(page.locator('#view-itinerary')).to_contain_text(f"일정 버전 {before['version'] + 1}")
    shot(page, 'e06-5-stale-409')
    assert external == []
    assert server_blocked(stage3_agent_server) == []
