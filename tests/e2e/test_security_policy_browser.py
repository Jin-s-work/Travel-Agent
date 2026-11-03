"""E13: the enforced Content-Security-Policy keeps login, place photos and the service worker working.

Runs on the stage 1 synthetic fixture. Wikimedia and the blocked probe host are answered by a
Playwright route, so no request leaves the machine.
"""
import base64

import pytest

from tests.e2e.conftest import requires_e2e
from tests.e2e.test_golden_path_browser import ui_login

pytestmark = requires_e2e
pytest.importorskip('playwright.sync_api', reason='Playwright not installed (install needs owner approval)')

PIXEL = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
PHOTO = 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.jpg'
# The e2e console check ignores this host only; any other blocked load still fails the test.
BLOCKED = 'https://csp-probe.invalid/pixel.png'

LOAD = '''async (src) => {
  const blocked = [];
  document.addEventListener('securitypolicyviolation', event => blocked.push(event.blockedURI));
  const image = new Image();
  const loaded = await new Promise(done => { image.onload = () => done(true); image.onerror = () => done(false); image.src = src; });
  await new Promise(done => setTimeout(done, 200));
  return {loaded, blocked};
}'''


def test_e13_csp_keeps_login_photos_and_service_worker(stage1_server, new_page):
    page = new_page()
    page.route('https://upload.wikimedia.org/**', lambda route: route.fulfill(status=200, content_type='image/png', body=PIXEL))
    page.route('https://csp-probe.invalid/**', lambda route: route.fulfill(status=200, content_type='image/png', body=PIXEL))
    response = page.goto(stage1_server['base'])
    policy = response.headers['content-security-policy']
    assert "script-src 'self'" in policy and 'report-only' not in response.headers
    # Login is a form POST that redirects to the identity provider (form-action).
    ui_login(page, stage1_server, 'a')
    assert page.evaluate("navigator.serviceWorker.ready.then(registration => registration.active.scriptURL)").endswith('/sw.js')
    assert page.evaluate(LOAD, PHOTO) == {'loaded': True, 'blocked': []}
    probe = page.evaluate(LOAD, BLOCKED)
    assert probe['loaded'] is False and probe['blocked'] == [BLOCKED]
