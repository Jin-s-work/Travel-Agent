"""Coverage report on a synthetic local SQLite DB. Never touches the production DB."""
import json
import sqlite3
from datetime import timedelta

import pytest

from scripts import v6_data_coverage as coverage
from src.discovery.public_places import normalize
from src.discovery.safe_fetch import FetchRejected
from tests.test_foundation_api import service  # noqa: F401  (fixture)
from tests.test_public_discovery import public  # noqa: F401  (fixture)
from tests.test_v6_sights import elements, prepare, run, sights  # noqa: F401  (fixture)


def test_report_counts_categories_hours_photos_and_quota_from_synthetic_rows(sights, monkeypatch):
    trip, base = prepare(sights)
    provider = sights.app.state.discovery.public_provider
    working = provider.fetcher
    def refused(city, *, categories=None, area=None):
        raise FetchRejected('CONNECT_FAILED', network_errno=111)
    provider.fetcher = refused
    refused_result = run(sights, trip, base, 'coverage-refused')
    assert refused_result['public_discovery']['state'] == 'unavailable'
    sights.clock[0] += timedelta(minutes=3)
    provider.fetcher = working
    run(sights, trip, base, 'coverage-ok')
    with sights.app.state.db.connect() as con:
        report = coverage.collect(coverage.ReadOnly(con), now=sights.clock[0], cities=('paris',), measurement='synthetic_test')
    assert report['measurement'] == 'synthetic_test' and report['writes'] == 0 and report['provider_calls'] == 0
    assert report['historical_reference']['label'].startswith('과거 값')
    assert report['historical_reference']['cities']['tokyo']['restaurant_places'] == 50
    city = report['cities']['paris']
    assert city['catalog']['public_map'] == {'restaurant': 1, 'cafe': 1, 'attraction': 1}
    assert city['catalog']['total_reviewed'] == 0
    hours = city['opening_hours']
    assert hours['public_places_with_current_tags'] == 3 and hours['places_with_raw_hours'] == 3
    assert hours['states'] == {'parsed': 1, 'partial': 1, 'unparsed': 1, 'missing': 0}
    assert hours['parse_success_rate'] == round(2/3, 4)
    assert hours['by_category'] == {'attraction': {'parsed': 1}, 'cafe': {'unparsed': 1}, 'restaurant': {'partial': 1}}
    assert hours['stored_opening_hours_facts_by_status'] == {'provisional': 2}
    assert city['photos']['currently_permitted_photo_places'] == 0
    assert sum(city['photos']['current_photo_availability'].values()) == 3
    calls = report['map_calls']
    assert calls['quota'] == {'global_daily': 20, 'user_daily': 5, 'cooldown_seconds': 15}
    day = calls['by_day'][calls['today']]
    assert day == {'requests_recorded': 2, 'quota_consuming': 1, 'unsent_connection_failures': 1,
                   'max_quota_calls_by_one_user': 1, 'global_quota_remaining': 19}
    assert calls['error_codes'] == {'CONNECT_FAILED': 1}
    text = json.dumps(report, ensure_ascii=False)
    assert sights.user['id'] not in text and trip['id'] not in text  # no owner/trip identifiers


def test_empty_city_reports_unmeasured_rates_as_null(service):
    with service.app.state.db.connect() as con:
        report = coverage.collect(coverage.ReadOnly(con), cities=('tokyo',), measurement='synthetic_test')
    hours = report['cities']['tokyo']['opening_hours']
    assert hours['parse_success_rate'] is None and hours['raw_hours_presence_rate'] is None
    assert report['map_calls']['total_quota_consuming'] == 0


def test_read_only_wrapper_refuses_writes_and_percent_literals():
    con = sqlite3.connect(':memory:')
    guard = coverage.ReadOnly(con)
    with pytest.raises(ValueError, match='READ_ONLY'):
        guard.execute('DELETE FROM x')
    with pytest.raises(ValueError, match='READ_ONLY'):
        guard.execute("  insert into x values(1)")
    with pytest.raises(ValueError, match='PERCENT'):
        coverage.ReadOnly(con, convert=True).execute("SELECT 'a%'")


def test_cli_failure_prints_no_secret_and_writes_nothing(tmp_path, monkeypatch, capsys):
    def broken(*args, **kwargs):
        raise RuntimeError('postgresql://user:secret@host/db')
    monkeypatch.setattr(coverage, 'read', broken)
    out = tmp_path / 'report.json'
    assert coverage.main(['--env', str(tmp_path / 'missing.env'), '--output', str(out)]) == 1
    printed = capsys.readouterr().out
    assert 'secret' not in printed and json.loads(printed)['reason'] == 'READ_ONLY_DATABASE_AUDIT_FAILED'
    assert json.loads(printed)['error_class'] == 'RuntimeError' and json.loads(printed)['sqlstate'] is None
    assert not out.exists()


def test_cli_failure_reports_sqlstate_without_the_message(tmp_path, monkeypatch, capsys):
    class SyntheticDatabaseError(Exception):
        sqlstate = '42P01'
    def broken(*args, **kwargs):
        raise SyntheticDatabaseError('relation "travel.secret_table" does not exist')
    monkeypatch.setattr(coverage, 'read', broken)
    assert coverage.main(['--env', str(tmp_path / 'missing.env'), '--output', str(tmp_path / 'r.json')]) == 1
    printed = capsys.readouterr().out
    assert 'secret' not in printed
    assert json.loads(printed) == {'status': 'unavailable', 'reason': 'READ_ONLY_DATABASE_AUDIT_FAILED', 'writes': 0,
                                   'error_class': 'SyntheticDatabaseError', 'sqlstate': '42P01'}
