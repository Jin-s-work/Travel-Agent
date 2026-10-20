"""Stage 1 browser scenarios E01-E05 (Chromium via Playwright).

Status 2026-10-20: skeleton. Playwright is not installed until the owner approves
it (docs/service-v6/reports/STAGE1.md). Each test states its pass criterion so the
implementation cannot quietly weaken it.
"""
import pytest

from tests.e2e.conftest import requires_e2e

pytestmark = requires_e2e
pytest.importorskip('playwright.sync_api', reason='Playwright not installed (install needs owner approval)')

PENDING = 'browser steps not written yet: Playwright was approved after this skeleton'


def test_e01_login_trip_discover_save_preview_apply_reload_undo(stage1_server):
    """Synthetic login -> last trip -> 현지어 리뷰 / 유명한 곳 -> save -> 저장한 곳 -> itinerary
    preview -> apply -> reload -> undo. Pass: DOM checked per step, state survives reload,
    undo creates a new revision."""
    pytest.skip(PENDING)


def test_e02_user_b_direct_urls_show_not_found(stage1_server):
    """B opens A's trip/place/itinerary URLs in the SPA. Pass: not-found view, zero A data in DOM.
    The HTTP half already runs in test_stage1_http.py."""
    pytest.skip(PENDING)


def test_e03_upload_eight_bookings_mail_then_ask_that_day(stage1_server):
    """Upload examples/mail-test-pack/07-eight-bookings-one-day.eml (local mode), ask for that day.
    Pass: all 8 + 1 manual booking listed, zero external calls."""
    pytest.skip(PENDING)


def test_e04_390px_keyboard_only_condition_dialog(stage1_server):
    """390px viewport, keyboard only: open and close 조건 수정. Pass: no horizontal scroll,
    focus returns to the opener."""
    pytest.skip(PENDING)


def test_e05_rapid_filter_changes_and_trip_switch(stage1_server):
    """Change a filter 3 times quickly, then switch trips. Pass: only the latest request renders,
    zero results from the other trip."""
    pytest.skip(PENDING)
