"""Stage 1 browser scenarios E01-E05 on the synthetic fixture (headless Chromium via Playwright).

Synthetic users A/B only, local mail analysis, fake public map, no paid provider. The scenarios
run in file order on one fixture server: E02 checks the itinerary that E01 creates.
Each test states its pass criterion from docs/service-v6/prompts/01-baseline-ci-e2e.md.
"""
import json
import os
from pathlib import Path
import re

import pytest

from tests.e2e.conftest import ROOT, oidc_login, requires_e2e

pytestmark = requires_e2e
pytest.importorskip('playwright.sync_api', reason='Playwright not installed (install needs owner approval)')
from playwright.sync_api import expect  # noqa: E402

MAIL = ROOT / 'examples/mail-test-pack/07-eight-bookings-one-day.eml'
LOCAL = re.compile(r'^(https?://127\.0\.0\.1[:/]|data:|blob:|about:)')


def shot(page, name):
    target = os.getenv('GOING_E2E_SCREENSHOTS')
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(target) / f'{name}.png'), full_page=True)


def track_external(page):
    seen = []
    page.on('request', lambda request: seen.append(request.url) if not LOCAL.match(request.url) else None)
    return seen


def ui_login(page, server, who):
    page.goto(server['base'])
    page.get_by_role('button', name='Google 계정으로 로그인').click()
    page.get_by_role('button', name=f'합성 사용자 {who.upper()} 로그인').click()
    page.wait_for_url(server['base'] + '/**')
    expect(page.locator('#tripSelect')).to_be_attached()


def select_trip(page, label):
    page.locator('#tripSelect').select_option(label=label)
    expect(page.locator('#tripSelect option:checked')).to_have_text(label)


def tab(page, name):
    page.locator(f'nav button[data-tab={name}]').click()


def test_e01_login_trip_discover_save_preview_apply_reload_undo(stage1_server, new_page):
    """Pass: DOM checked at each step, state survives reload, undo creates a new revision."""
    page = new_page()
    external = track_external(page)
    ui_login(page, stage1_server, 'a')
    expect(page.locator('#tripSelect option:checked')).to_have_text(re.compile(r'^A · '))  # last trip opens
    select_trip(page, 'A · 바르셀로나 테스트 · 2026-11-06')

    tab(page, 'explore')
    results = page.locator('#recommendationResults')
    local = page.locator('#discovery-mode-local_discovery')
    local.click()
    expect(local).to_have_attribute('aria-selected', 'true')
    expect(results).to_contain_text('현지어 리뷰를 준비하고 있어요')
    expect(results.locator('article.recommendation-card')).to_have_count(0)  # no unverified local claims
    landmark = page.locator('#discovery-mode-landmark')
    landmark.click()
    expect(landmark).to_have_attribute('aria-selected', 'true')
    cards = results.locator('article.recommendation-card')
    expect(cards.first).to_be_visible()
    names = [cards.nth(index).locator('h3').inner_text() for index in range(2)]
    for index in range(2):
        actions = cards.nth(index).locator('.recommendation-primary-actions')
        actions.locator('button', has_text='저장').click()
        expect(actions).to_contain_text('저장됨')
    shot(page, 'e01-1-landmark-saved')

    tab(page, 'saved')
    saved = page.locator('#bookmarkWorkspace')
    expect(saved).to_be_visible()
    for name in names:
        expect(saved.get_by_role('heading', name=name)).to_be_visible()
    for _ in range(2):
        saved.get_by_role('button', name='일정 장소로 선택').first.click()
    saved.get_by_role('button', name='2곳 일정 미리보기').click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('미확인 영업·이동을 표시한 잠정 일정도 검토하기').check()
    dialog.get_by_role('button', name='일정 영향 미리보기').click()
    expect(dialog).to_contain_text('아직 저장하지 않았습니다')
    for name in names:
        expect(dialog).to_contain_text(name)
    shot(page, 'e01-2-preview')
    dialog.get_by_role('button', name='검증한 변경 적용').click()
    expect(page.get_by_role('dialog')).to_have_count(0)

    plan = page.locator('#view-itinerary')
    expect(plan).to_be_visible()
    expect(plan).to_contain_text('일정 버전 1')
    expect(plan).to_contain_text('이동 · 직선거리 기준 추정')  # v6 decision 1 = A, provisional only
    for name in names:
        expect(plan.get_by_role('heading', name=name)).to_be_visible()
    expect(plan.get_by_text(re.compile(r'체류 60분'))).to_have_count(2)

    plan.get_by_role('button', name='시각·체류시간 편집').nth(1).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('체류시간 · 분').fill('45')
    dialog.get_by_role('button', name='변경 영향 미리보기').click()
    apply = dialog.get_by_role('button', name='검증한 변경 적용')
    expect(apply).to_be_enabled()
    apply.click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(plan).to_contain_text('일정 버전 2')
    expect(plan.get_by_text(re.compile(r'체류 45분'))).to_have_count(1)

    page.reload()
    tab(page, 'itinerary')
    expect(plan).to_contain_text('일정 버전 2')
    expect(plan.get_by_text(re.compile(r'체류 45분'))).to_have_count(1)
    shot(page, 'e01-3-after-reload')

    undo = plan.get_by_role('button', name='편집 되돌리기')
    expect(undo).to_be_enabled()
    undo.click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_role('button', name='변경 영향 미리보기').click()
    dialog.get_by_role('button', name='검증한 변경 적용').click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(plan).to_contain_text('일정 버전 3')  # undo appends a revision, never rewinds
    expect(plan.get_by_text(re.compile(r'체류 45분'))).to_have_count(0)
    expect(plan.get_by_text(re.compile(r'체류 60분'))).to_have_count(2)
    shot(page, 'e01-4-undo')
    assert external == []


DELAY_DRAFT_READ = """(() => { const original = window.fetch;
  window.fetch = (input, init) => {
    const url = String((input && input.url) || input), method = (init && init.method) || 'GET';
    if (method === 'GET' && url.includes('/workspace-draft')) return new Promise(r => setTimeout(r, 1500)).then(() => original(input, init));
    return original(input, init);
  }; })();"""


def test_e01b_tab_chosen_while_loading_survives_stale_draft_restore(stage1_server, new_page):
    """D4 regression from the first E01 run: after a reload, a tab the user picks before the
    workspace draft arrives must not be replaced by the draft's older tab (here 저장한 곳)."""
    page = new_page()
    ui_login(page, stage1_server, 'a')
    select_trip(page, 'A · 바르셀로나 테스트 · 2026-11-06')

    def stale_tab(route):
        if route.request.method != 'GET':
            return route.continue_()
        response = route.fetch()
        value = response.json()
        value['context'] = {**(value.get('context') or {}), 'tab': 'explore', 'explore_view': 'saved'}
        route.fulfill(response=response, json=value)

    page.route('**/workspace-draft', stale_tab)
    page.add_init_script(DELAY_DRAFT_READ)
    page.reload()
    tab(page, 'itinerary')  # before the delayed draft read completes
    page.wait_for_timeout(3000)
    expect(page.locator('#view-itinerary')).to_be_visible()
    expect(page.locator('nav button[data-tab=itinerary]')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#bookmarkWorkspace')).to_be_hidden()


def _names(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('name', 'title', 'provider') and isinstance(item, str) and len(item) >= 4:
                found.add(item)
            _names(item, found)
    elif isinstance(value, list):
        for item in value:
            _names(item, found)
    return found


def test_e02_user_b_direct_urls_show_not_found(stage1_server, new_page):
    """Pass: every user A trip/place/itinerary URL is 404 in B's browser, zero A data shown."""
    a, b = oidc_login(stage1_server, 'a'), oidc_login(stage1_server, 'b')
    paths, private = [], set()
    a_trips = a.get('/api/v2/trips').json()['items']
    titles = {trip['title'] for trip in a_trips}
    shared = set()  # strings B legitimately has too (same synthetic booking names) are not A-private
    for trip in b.get('/api/v2/trips').json()['items']:
        _names([b.get('/api/v2/trips/' + trip['id'] + suffix).json() for suffix in ('', '/bookings')], shared)
    for trip in a_trips:
        base = '/api/v2/trips/' + trip['id']
        listed = {suffix: a.get(base + suffix).json() for suffix in ('', '/bookings', '/bookmarks', '/itineraries')}
        _names(listed, private)
        paths += [base + suffix for suffix in listed]
        paths += [base + '/bookmarks/' + item['id'] for item in listed['/bookmarks']['items']]
        paths += [f"{base}/places/{item['matched_place_id']}/detail" for item in listed['/bookmarks']['items'] if item.get('matched_place_id')]
        paths += [base + '/itineraries/' + item['id'] for item in listed['/itineraries']['items']]
    assert any('/itineraries/' in path for path in paths), 'E01 creates the itinerary that E02 probes'
    assert any('/places/' in path for path in paths), 'E01 saves the places that E02 probes'
    private = (private - shared) | titles
    page = new_page()
    ui_login(page, stage1_server, 'b')
    options = page.locator('#tripSelect option').all_inner_texts()
    assert options and all(option.startswith('B · ') for option in options)
    for text in titles:
        expect(page.locator('body')).not_to_contain_text(text)
    for path in paths:
        assert a.get(path).status_code == 200, path
        response = page.goto(stage1_server['base'] + path)
        assert response.status == 404, (path, response.status)
        body = page.content()
        assert not [text for text in private if text in body], path
    page.goto(stage1_server['base'])
    expect(page.locator('#tripSelect option:checked')).to_have_text(re.compile(r'^B · '))
    expect(page.locator('#tripSummary')).to_contain_text('1건')  # B's own synthetic booking, loaded
    for text in titles:
        expect(page.locator('body')).not_to_contain_text(text)
    shot(page, 'e02-user-b-home')


def test_e03_upload_eight_bookings_mail_then_ask_that_day(stage1_server, new_page):
    """Pass: all 8 mail bookings + 1 manual booking answered for that day, zero external calls."""
    api = oidc_login(stage1_server, 'a')
    before = api.get('/api/v2/usage').json()
    page = new_page()
    external = track_external(page)
    ui_login(page, stage1_server, 'a')
    select_trip(page, 'A · 도쿄 테스트 · 2026-11-06')
    tab(page, 'mail')
    page.locator('#mailFiles').set_input_files(str(MAIL))
    page.get_by_role('button', name='선택한 메일 분석').click()
    mail = page.locator('#view-mail')
    expect(mail.get_by_role('button', name='2026-11-07 토요일 · 9건')).to_be_visible(timeout=60000)
    shot(page, 'e03-1-mail-uploaded')

    tab(page, 'ask')
    ask = page.locator('#view-ask')
    ask.locator('#question').fill('11월 7일 예약을 모두 알려줘')
    ask.locator('#sendQuestion').click()
    expect(ask).to_contain_text('2026-11-07 예약은 총 9개입니다.', timeout=30000)
    for number in range(1, 9):
        expect(ask).to_contain_text(f'Fictional Tokyo Demo Stop {number}')
    expect(ask).to_contain_text('합성 점심 예약')
    expect(ask.get_by_role('button', name='근거 9건 보기')).to_be_visible()
    shot(page, 'e03-2-answer')
    assert external == []
    # Earlier scenarios may have recorded free synthetic map calls; this flow must add none.
    after = api.get('/api/v2/usage').json()
    tally = lambda usage: [(row.get('currency'), row.get('calls'), row.get('actual_micros')) for row in usage.get('currencies') or []]
    assert tally(after) == tally(before), (before, after)


def _no_horizontal_scroll(page):
    width, client = page.evaluate('() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]')
    assert width <= client, (width, client)


def _tab_until(page, matches, limit=120):
    for _ in range(limit):
        page.keyboard.press('Tab')
        if page.evaluate(matches):
            return
    raise AssertionError('keyboard focus never reached the target: ' + matches)


def test_e04_390px_keyboard_only_condition_dialog(stage1_server, new_page):
    """Pass: no horizontal scroll at 390px, keyboard opens/closes 조건 수정, focus returns."""
    page = new_page(viewport={'width': 390, 'height': 844})
    ui_login(page, stage1_server, 'a')
    _no_horizontal_scroll(page)
    _tab_until(page, "() => document.activeElement.dataset.tab === 'explore'")
    page.keyboard.press('Enter')
    expect(page.locator('#view-explore')).to_be_visible()
    _no_horizontal_scroll(page)
    _tab_until(page, "() => (document.activeElement.innerText || '').trim() === '조건 수정'")
    page.keyboard.press('Enter')
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    assert page.evaluate("() => !!document.activeElement.closest('[role=dialog], dialog')")
    _no_horizontal_scroll(page)
    shot(page, 'e04-390px-dialog')
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert page.evaluate("() => (document.activeElement.innerText || '').trim()") == '조건 수정'


def test_e05_rapid_filter_changes_and_trip_switch(stage1_server, new_page):
    """Pass: only the latest filter request is applied; another trip's results never render."""
    page = new_page()
    posted = []
    page.on('request', lambda request: posted.append(json.loads(request.post_data or '{}'))
            if request.method == 'POST' and request.url.endswith('/discovery-intents') else None)
    ui_login(page, stage1_server, 'a')
    select_trip(page, 'A · 도쿄 테스트 · 2026-11-06')
    tab(page, 'explore')
    results = page.locator('#recommendationResults')
    expect(results.locator('article.recommendation-card').first).to_be_visible()
    chips = page.locator('.discovery-category-chips button')
    posted.clear()
    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/discovery-intents')):
        for name in ('전체', '카페', '볼거리'):
            chips.filter(has_text=name).first.click()
    expect(chips.filter(has_text='볼거리').first).to_have_attribute('aria-pressed', 'true')
    kinds = results.locator('article.recommendation-card .recommendation-card-heading .hint')
    expect(kinds.first).to_have_text(re.compile(r'^볼거리'), timeout=20000)
    page.wait_for_timeout(1500)  # a stale earlier response, if any, would land in this window
    assert posted and posted[-1]['overrides']['categories'] == ['attraction'], posted
    assert {text.split('·')[0].strip() for text in kinds.all_inner_texts()} == {'볼거리'}
    shot(page, 'e05-1-latest-filter')

    chips.filter(has_text='카페').first.click()
    select_trip(page, 'A · 바르셀로나 테스트 · 2026-11-06')  # switch while the Tokyo request may be in flight
    page.wait_for_load_state('networkidle')
    expect(results.locator('article.recommendation-card').first).to_be_visible()
    expect(results).not_to_contain_text('東京')
    expect(results).to_contain_text('Barcelona')
    shot(page, 'e05-2-after-trip-switch')
