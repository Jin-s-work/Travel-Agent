"""CSP and HSTS on every response; the screens must stay loadable under that policy."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from src.foundation.settings import Settings

WEB = Path(__file__).resolve().parents[1] / 'web'


def _client(tmp_path, **changes):
    from api import create_app
    settings = Settings(database_path=tmp_path / 'headers.sqlite', environment='production',
                        public_base_url='https://testserver', oidc_client_id='fixture',
                        oidc_client_secret='fixture', session_secret='s' * 40, **changes)
    return TestClient(create_app(settings), base_url='https://testserver')


def _policy(header):
    return {part.split()[0]: part.split()[1:] for part in header.split(';') if part.strip()}


def test_html_and_api_responses_carry_csp_and_hsts(tmp_path):
    client = _client(tmp_path)
    for path in ['/', '/api/v2/session', '/health/live']:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers['strict-transport-security'] == 'max-age=31536000'
        policy = _policy(response.headers['content-security-policy'])
        assert policy['script-src'] == ["'self'"] and policy['style-src'] == ["'self'"]
        assert policy['object-src'] == ["'none'"] and policy['frame-ancestors'] == ["'none'"]
        assert not any('unsafe' in value for values in policy.values() for value in values)


def test_login_form_may_post_to_the_configured_identity_provider_only(tmp_path):
    default = _policy(_client(tmp_path).get('/').headers['content-security-policy'])
    assert default['form-action'] == ["'self'", 'https://accounts.google.com']
    other = _client(tmp_path, oidc_metadata_url='https://login.example.test/.well-known/openid-configuration')
    assert _policy(other.get('/').headers['content-security-policy'])['form-action'] == ["'self'", 'https://login.example.test']


def test_place_photos_are_limited_to_the_wikimedia_hosts_the_server_accepts(tmp_path):
    policy = _policy(_client(tmp_path).get('/').headers['content-security-policy'])
    assert policy['img-src'] == ["'self'", 'https://upload.wikimedia.org', 'https://thumb.wikimedia.org']
    source = (Path(__file__).resolve().parents[1] / 'src/discovery/photos.py').read_text()
    image_hosts = re.search(r"photo\['url'\], host, [^)]*\) for host in \(([^)]*)\)", source).group(1)
    assert sorted(re.findall(r"'([a-z.]+)'", image_hosts)) == ['thumb.wikimedia.org', 'upload.wikimedia.org']


def test_screens_have_no_inline_code_the_policy_would_block():
    html = (WEB / 'index.html').read_text()
    assert not re.search(r'<script(?![^>]*\bsrc=)[^>]*>', html)
    assert not re.search(r'<style\b', html)
    assert not re.search(r'\son[a-z]+\s*=', html)
    assert not re.search(r'\sstyle\s*=', html)
    for script in sorted((WEB / 'js').glob('*.js')):
        source = script.read_text()
        assert not re.search(r'''\bstyle=["'\\]|setAttribute\(\s*['"]style['"]|\beval\(|new Function\(''', source), script.name
