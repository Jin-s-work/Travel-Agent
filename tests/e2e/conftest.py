"""Stage 1 E2E fixtures: the synthetic app + IdP from scripts/stage1_browser_fixture.py.

Opt-in only: GOING_E2E=1. Ports are always fresh free ports, so a fixture the user
already runs on 8766/8767 is never touched. Synthetic users a/b, no paid provider.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
E2E_ENABLED = os.getenv('GOING_E2E') == '1'
requires_e2e = pytest.mark.skipif(not E2E_ENABLED, reason='E2E is opt-in: set GOING_E2E=1 (make e2e)')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture(scope='session')
def stage1_server(tmp_path_factory):
    app_port, idp_port = free_port(), free_port()
    fixture_dir = tmp_path_factory.mktemp('stage1-browser')
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'STAGE1_APP_PORT': str(app_port),
           'STAGE1_IDP_PORT': str(idp_port), 'BROWSER_FIXTURE_DIR': str(fixture_dir)}
    log = (fixture_dir / 'server.log').open('w')
    process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/stage1_browser_fixture.py')],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{app_port}'
    deadline = time.monotonic() + 120
    try:
        while True:
            if process.poll() is not None:
                raise RuntimeError('stage1 fixture exited early; see ' + str(fixture_dir / 'server.log'))
            try:
                if httpx.get(base + '/health/ready', timeout=2).status_code == 200 and \
                        httpx.get(f'http://127.0.0.1:{idp_port}/.well-known/openid-configuration', timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('stage1 fixture did not become ready in 120s')
            time.sleep(0.5)
        yield {'base': base, 'idp': f'http://127.0.0.1:{idp_port}', 'dir': fixture_dir}
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


def oidc_login(server, who):
    """Real authorize/callback round trip against the synthetic IdP, as a browser would do it."""
    client = httpx.Client(base_url=server['base'], follow_redirects=False, timeout=15)
    start = client.get('/api/v2/auth/login')
    assert start.status_code in (302, 303), start.text
    authorize = start.headers['location']
    assert urlsplit(authorize).netloc == urlsplit(server['idp']).netloc
    approved = httpx.get(authorize + '&fixture_account=' + who, follow_redirects=False, timeout=15)
    assert approved.status_code == 303, approved.text
    callback = client.get(approved.headers['location'])
    assert callback.status_code == 303 and callback.headers['location'] == '/', callback.text
    session = client.get('/api/v2/session').json()
    assert session['authenticated'] and session['csrf_token'], json.dumps(session)
    client.headers['X-CSRF-Token'] = session['csrf_token']
    return client


# --- Browser layer (Playwright, Chromium only) --------------------------------
# Traces are written only for failed tests, to GOING_E2E_TRACE_DIR (default .e2e-traces/,
# git-ignored). Screenshots are opt-in through GOING_E2E_SCREENSHOTS (make e2e-evidence).

@pytest.fixture(scope='session')
def browser():
    sync_api = pytest.importorskip('playwright.sync_api', reason='Playwright not installed (install needs owner approval)')
    with sync_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, 'rep_' + report.when, report)


@pytest.fixture
def new_page(browser, request):
    contexts = []

    def make(**options):
        context = browser.new_context(**{'viewport': {'width': 1280, 'height': 900}, 'locale': 'ko-KR', **options})
        context.tracing.start(screenshots=True, snapshots=True)
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(20000)
        return page

    yield make
    report = getattr(request.node, 'rep_call', None)
    failed = bool(report and report.failed)
    trace_dir = Path(os.getenv('GOING_E2E_TRACE_DIR') or ROOT / '.e2e-traces')
    for index, context in enumerate(contexts):
        if failed:
            trace_dir.mkdir(parents=True, exist_ok=True)
            context.tracing.stop(path=str(trace_dir / f'{request.node.name}-{index}.zip'))
        else:
            context.tracing.stop()
        context.close()
