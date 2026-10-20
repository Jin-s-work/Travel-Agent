#!/usr/bin/env python3
"""Server RSS on the synthetic stage1 fixture: after start, one recommendation, one local mail analysis.

    PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/measure_stage1_memory.py
    PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/measure_stage1_memory.py --lingua

Local storage backend, ZERO_SPEND, MAIL_ANALYSIS_MODE=local, synthetic OIDC users. No paid
or public-map call. RSS is read from /proc (Linux) or `ps` (macOS) and printed as JSON.
--lingua separately loads Lingua with all languages in a fresh process, because the
detector is lazy and is only reached by the admin review-research job.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
MAIL = ROOT / 'examples/mail-test-pack/07-eight-bookings-one-day.eml'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def rss_mib(pid):
    status = Path(f'/proc/{pid}/status')
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith('VmRSS:'):
                return round(int(line.split()[1]) / 1024, 1)
    out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True, check=True).stdout
    return round(int(out.strip()) / 1024, 1)


def lingua_mapped(pid):
    maps = Path(f'/proc/{pid}/maps')
    if maps.exists():
        return 'lingua' in maps.read_text()
    out = subprocess.run(['lsof', '-p', str(pid)], capture_output=True, text=True).stdout
    return 'lingua' in out


def login(base, idp, who):
    client = httpx.Client(base_url=base, follow_redirects=False, timeout=30, headers={'Origin': base})
    authorize = client.get('/api/v2/auth/login').headers['location']
    assert authorize.startswith(idp), authorize
    approved = httpx.get(authorize + '&fixture_account=' + who, follow_redirects=False, timeout=30)
    client.get(approved.headers['location'])
    session = client.get('/api/v2/session').json()
    assert session['authenticated'], session
    client.headers['X-CSRF-Token'] = session['csrf_token']
    return client


def finish(client, receipt, limit=180):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        job = client.get('/api/v2/jobs/' + receipt['job_id']).json()
        if job['state'] in {'succeeded', 'partial', 'failed', 'cancelled'}:
            return job['state']
        time.sleep(0.5)
    return 'timeout'


def measure_server():
    app_port, idp_port = free_port(), free_port()
    base, idp = f'http://127.0.0.1:{app_port}', f'http://127.0.0.1:{idp_port}'
    sandbox = Path(tempfile.mkdtemp(prefix='going-memory-'))
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1', 'STAGE1_APP_PORT': str(app_port),
           'STAGE1_IDP_PORT': str(idp_port), 'BROWSER_FIXTURE_DIR': str(sandbox)}
    log = (sandbox / 'server.log').open('w')
    server = subprocess.Popen([sys.executable, str(ROOT / 'scripts/stage1_browser_fixture.py')], cwd=ROOT,
                              env=env, stdout=log, stderr=subprocess.STDOUT)
    result = {'python': sys.version.split()[0], 'platform': sys.platform, 'steps': []}
    try:
        deadline = time.monotonic() + 180
        while True:
            try:
                if httpx.get(base + '/health/ready', timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('fixture did not start; see ' + str(sandbox / 'server.log'))
            time.sleep(0.5)
        time.sleep(2)
        result['steps'].append({'step': 'ready', 'rss_mib': rss_mib(server.pid)})
        client = login(base, idp, 'a')
        trip = next(t for t in client.get('/api/v2/trips').json()['items'] if '도쿄' in t['title'])
        trip = client.get('/api/v2/trips/' + trip['id']).json()
        conditions = client.get(f"/api/v2/trips/{trip['id']}/discovery-conditions").json()
        intent = client.post(f"/api/v2/trips/{trip['id']}/discovery-intents", headers={'Idempotency-Key': 'memory-recommendation-1'},
                             json={'expected_trip_version': trip['version'], 'expected_conditions_version': conditions.get('version') or 0,
                                   'stop_id': trip['stops'][0]['id'], 'visit_date': trip['start_date'], 'overrides': {},
                                   'filters': {'rating_filter': {'enabled': False}}})
        assert intent.status_code == 202, intent.text
        state = finish(client, intent.json())
        result['steps'].append({'step': 'after_recommendation', 'job_state': state, 'rss_mib': rss_mib(server.pid)})
        upload = client.post(f"/api/v2/trips/{trip['id']}/documents", headers={'Idempotency-Key': 'memory-mail-1'},
                             files=[('files', (MAIL.name, MAIL.read_bytes(), 'message/rfc822'))])
        assert upload.status_code == 202, upload.text
        state = finish(client, upload.json())
        result['steps'].append({'step': 'after_local_mail_analysis', 'job_state': state, 'rss_mib': rss_mib(server.pid)})
        result['lingua_loaded_in_server'] = lingua_mapped(server.pid)
    finally:
        server.terminate()
        try:
            server.wait(10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
    return result


LINGUA_PROBE = r'''
import json, os, resource, sys, time
from lingua import LanguageDetectorBuilder
start = time.monotonic()
detector = LanguageDetectorBuilder.from_all_languages().build()
for text in ('駅前の小さな店で食べた。', 'Comimos en un bar pequeño cerca de la estación.', '역 근처 작은 가게에서 먹었다.',
             'We ate at a small place near the station.', 'Vam menjar en un bar petit a prop de l\'estació.'):
    detector.compute_language_confidence_values(text)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
peak_mib = peak / 1024 / 1024 if sys.platform == 'darwin' else peak / 1024
print(json.dumps({'lingua_all_languages_peak_rss_mib': round(peak_mib, 1), 'seconds': round(time.monotonic() - start, 1)}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lingua', action='store_true', help='also measure Lingua all-language load in a fresh process')
    args = parser.parse_args()
    result = measure_server()
    if args.lingua:
        probe = subprocess.run([sys.executable, '-c', LINGUA_PROBE], capture_output=True, text=True, timeout=600)
        result['lingua_probe'] = json.loads(probe.stdout) if probe.returncode == 0 else {'error': probe.stderr[-400:]}
    for name in ('memory.peak', 'memory.max'):  # cgroup v2, present inside a container
        path = Path('/sys/fs/cgroup') / name
        if path.exists():
            raw = path.read_text().strip()
            result['cgroup_' + name.split('.')[1] + '_mib'] = raw if not raw.isdigit() else round(int(raw) / 1048576, 1)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
