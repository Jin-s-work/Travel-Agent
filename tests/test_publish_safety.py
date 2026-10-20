"""Publish scan rules. Fixture secrets are assembled at runtime so this file never matches."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('check_publish_safety', Path(__file__).resolve().parents[1] / 'scripts/check_publish_safety.py')
safety = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(safety)

KEY_BODY = 'A1b2C3d4' * 4


@pytest.mark.parametrize('line,rule', [
    ('OPENAI_API_KEY=' + 'sk-' + 'proj-' + KEY_BODY, 'openai_key'),
    ('token: ' + 'sk-' + KEY_BODY, 'openai_key'),
    ('-----BEGIN ' + 'RSA PRIVATE KEY' + '-----', 'private_key_block'),
    ('-----BEGIN ' + 'PRIVATE KEY' + '-----', 'private_key_block'),
    ('url=' + 'postgresql' + '://app:' + 'Real' + 'Pass9@db.example-host.net:5432/postgres', 'db_url_password'),
])
def test_secret_rules_fail(line, rule):
    assert ('fail', rule) in safety.line_rules(line)


@pytest.mark.parametrize('line,rule', [
    ('cwd /' + 'Users/someone/Desktop/project', 'absolute_user_path'),
    ('postgresql' + '://postgres:disposable@127.0.0.1:55439/postgres', 'db_url_loopback_password'),
])
def test_exposure_rules_warn(line, rule):
    assert safety.line_rules(line) == [('warn', rule)]


@pytest.mark.parametrize('line', [
    'OPENAI_API_KEY=test-placeholder',
    'sk-short',
    'DATABASE_URL=' + 'postgresql' + '://postgres.[ref]:[YOUR-PASSWORD]@aws-0.pooler.supabase.com:6543/postgres',
    'DATABASE_URL=' + 'postgresql' + '://user:password@host:5432/db',
    'TRAVEL_TEST_POSTGRES_DSN=' + 'postgresql' + '://postgres@localhost:5432/postgres',
    'see /' + 'Users/<사용자명>/ for the local path',
    "SUPABASE_SECRET_KEY='sb_secret_fixture'",
])
def test_placeholders_and_passwordless_urls_pass(line):
    assert safety.line_rules(line) == []


@pytest.mark.parametrize('path,expected', [
    ('.env', [('fail', 'tracked_env_file')]),
    ('deploy/render-supabase/.env.render', [('fail', 'tracked_env_file')]),
    ('.env.example', []),
    ('deploy/render-supabase/.env.example', []),
    ('docs/presentation/v4/SCRIPT.md', [('warn', 'presentation_script_tracked')]),
    ('docs/presentation/README.md', []),
])
def test_path_rules(path, expected):
    assert safety.path_rules(path) == expected


def test_output_never_contains_the_value(tmp_path, monkeypatch, capsys):
    secret = 'sk-' + KEY_BODY
    (tmp_path / 'notes.md').write_text('first\nkey ' + secret + '\n')
    monkeypatch.setattr(safety, 'ROOT', tmp_path)
    assert safety.main(['--ci', 'notes.md']) == 1
    out = capsys.readouterr().out
    assert 'FAIL notes.md:2 openai_key' in out and secret not in out and KEY_BODY not in out


def test_ci_mode_passes_warnings_but_default_mode_fails(tmp_path, monkeypatch):
    (tmp_path / 'doc.md').write_text('path /' + 'Users/someone/x\n')
    monkeypatch.setattr(safety, 'ROOT', tmp_path)
    assert safety.main(['--ci', 'doc.md']) == 0
    assert safety.main(['doc.md']) == 1
