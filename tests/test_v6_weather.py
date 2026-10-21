"""Open-Meteo city-centre forecast: fake fetcher only, zero network in pytest."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from src.destinations import CITIES
from src.discovery.public_places import center
from src.discovery.safe_fetch import FetchRejected
from src.foundation.repository import DomainError
from src.foundation.settings import Settings
from src.travel_tools import weather as W
from tests.test_foundation_api import service  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)  # 09:00 in Tokyo


UNITS = {'time': 'iso8601', 'precipitation_probability_max': '%', 'precipitation_sum': 'mm',
         'temperature_2m_max': '°C', 'temperature_2m_min': '°C'}


def body(day, *, pop=80, rain=12.5, high=17.2, low=9.1, units=UNITS):
    return json.dumps({'latitude': 35.7, 'longitude': 139.7, 'timezone': 'Asia/Tokyo', 'daily_units': units,
                       'daily': {'time': [day], 'precipitation_probability_max': [pop], 'precipitation_sum': [rain],
                                 'temperature_2m_max': [high], 'temperature_2m_min': [low]}}).encode()


class Fake:
    def __init__(self):
        self.calls, self.fail = [], None

    def __call__(self, url, **options):
        self.calls.append((url, options))
        if self.fail:
            raise self.fail
        day = parse_qs(urlsplit(url).query)['start_date'][0]
        return SimpleNamespace(mime='application/json', content=body(day), url=url, redirects=0)


@pytest.fixture
def wx(service):  # noqa: F811
    fake, clock = Fake(), [NOW]
    weather = W.Weather(service.app.state.db, service.app.state.repo, service.app.state.discovery,
                        fetcher=fake, clock=lambda: clock[0])
    owner = service.login('weather-owner')
    return SimpleNamespace(service=service, weather=weather, fake=fake, clock=clock, owner=owner,
                           actor=SimpleNamespace(id=owner.user['id'], session_id=None))


def trip(client, city='tokyo', start='2026-11-06', end='2026-11-09', base_location=None):
    stop = {'city': CITIES[city]['name_en'], 'sequence': 1, 'start_date': start, 'end_date': end,
            'timezone': CITIES[city]['timezone']}
    if base_location:
        stop['base_location'] = base_location
    response = client.post('/api/v2/trips', json={'title': 'Synthetic weather trip', 'start_date': start,
                                                  'end_date': end, 'stops': [stop]})
    assert response.status_code == 201, response.text
    return response.json()


def test_success_sends_only_city_centre_with_daily_timezone_and_attribution(wx):
    t = trip(wx.owner.client, base_location='Synthetic private hotel, 1-2-3 Fixture')
    base = f"/api/v2/trips/{t['id']}/discovery-conditions"
    conditions = wx.owner.client.get(base).json()['conditions']
    conditions.update(origin={'label': 'Synthetic hotel origin', 'latitude': 35.7123, 'longitude': 139.8123})
    patched = wx.owner.client.patch(base, json={'expected_version': 0, 'conditions': conditions})
    assert patched.status_code == 200, patched.text
    assert patched.json()['conditions']['origin']['latitude'] == 35.7123

    result = wx.weather.forecast(wx.actor, t['id'], '2026-11-06')
    assert result['status'] == 'ok' and result['city'] == 'tokyo' and result['date'] == '2026-11-06'
    assert result['daily'] == {'precipitation_probability_max': 80, 'precipitation_sum': 12.5,
                               'temperature_2m_max': 17.2, 'temperature_2m_min': 9.1}
    assert result['attribution'] == {'text': 'Weather data by Open-Meteo.com', 'url': 'https://open-meteo.com/', 'license': 'CC BY 4.0'}
    assert result['center_attribution']['text'] == 'GeoNames' and result['center_attribution']['license'] == 'CC BY 4.0'
    assert result['cache_hit'] is False and result['scope'] == 'city_center'

    [(url, options)] = wx.fake.calls
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ('https', 'api.open-meteo.com', '/v1/forecast')
    assert options == {'max_bytes': W.MAX_BYTES, 'timeout_seconds': W.TIMEOUT_SECONDS, 'max_redirects': 0}
    q = parse_qs(parts.query)
    c = center('tokyo')
    assert float(q['latitude'][0]) == c['latitude'] and float(q['longitude'][0]) == c['longitude']
    assert q['daily'] == ['precipitation_probability_max,precipitation_sum,temperature_2m_max,temperature_2m_min']
    assert q['timezone'] == ['Asia/Tokyo'] and q['start_date'] == q['end_date'] == ['2026-11-06']
    assert set(q) == {'latitude', 'longitude', 'daily', 'timezone', 'start_date', 'end_date'}
    for private in ('35.7123', '139.8123', 'hotel', 'Fixture', wx.actor.id, t['id']):
        assert private not in url

    with wx.service.app.state.db.connect() as con:
        row = con.execute("SELECT * FROM usage_reservations WHERE provider='open_meteo'").fetchone()
        ledger = [dict(r) for r in con.execute('SELECT entry_kind,amount_micros,reason_code FROM usage_ledger WHERE call_id=?', (row['call_id'],))]
    assert row['state'] == 'settled' and row['actual_cost_micros'] == 0 and row['estimated_cost_micros'] == 0
    assert row['job_id'] is None and row['scope_kind'] == 'personal_trip' and row['trip_id'] == t['id'] and row['error_code'] is None
    assert sorted((e['entry_kind'], e['amount_micros'], e['reason_code']) for e in ledger) == [
        ('reserved', 0, 'EXPLICIT_FREE_PUBLIC_DATA'), ('settled', 0, 'EXPLICIT_FREE_PUBLIC_DATA')]


def test_cache_hit_makes_no_second_fetch_and_expires_after_three_hours(wx):
    t = trip(wx.owner.client)
    first = wx.weather.forecast(wx.actor, t['id'], '2026-11-07')
    second = wx.weather.forecast(wx.actor, t['id'], '2026-11-07')
    assert first['status'] == second['status'] == 'ok' and second['cache_hit'] is True
    assert len(wx.fake.calls) == 1
    other = wx.service.login('weather-other')
    t2 = trip(other.client)
    shared = wx.weather.forecast(SimpleNamespace(id=other.user['id']), t2['id'], '2026-11-07')
    assert shared['cache_hit'] is True and len(wx.fake.calls) == 1  # public city/date data
    wx.clock[0] = NOW.replace(hour=3, minute=1)
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-07')['cache_hit'] is False
    assert len(wx.fake.calls) == 2


@pytest.mark.parametrize('failure,reason', [
    (FetchRejected('HTTP_UNAVAILABLE', http_status=400), 'WEATHER_REQUEST_REJECTED'),
    (FetchRejected('HTTP_UNAVAILABLE', http_status=429), 'WEATHER_RATE_LIMITED'),
    (FetchRejected('HTTP_UNAVAILABLE', http_status=403), 'WEATHER_RATE_LIMITED'),
    (FetchRejected('FETCH_TIMEOUT'), 'WEATHER_TIMEOUT'),
    (FetchRejected('CONNECT_FAILED'), 'WEATHER_UNAVAILABLE'),
])
def test_provider_failures_are_unknown_never_clear_and_not_cached(wx, failure, reason):
    t = trip(wx.owner.client)
    wx.fake.fail = failure
    result = wx.weather.forecast(wx.actor, t['id'], '2026-11-06')
    assert result == {'status': 'unknown', 'reason_codes': [reason], 'message': '날씨를 확인하지 못했어요'}
    with wx.service.app.state.db.connect() as con:
        row = con.execute("SELECT state,actual_cost_micros,error_code FROM usage_reservations WHERE provider='open_meteo'").fetchone()
    assert row['state'] == 'settled' and row['actual_cost_micros'] == 0 and row['error_code']
    wx.fake.fail = None
    again = wx.weather.forecast(wx.actor, t['id'], '2026-11-06')
    if reason == 'WEATHER_RATE_LIMITED':
        # Provider refusal: cooldown for every city, without another request.
        assert again['reason_codes'] == ['WEATHER_RATE_LIMITED'] and len(wx.fake.calls) == 1
        wx.clock[0] = NOW.replace(minute=16)
        assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['status'] == 'ok'
    else:
        assert again['status'] == 'ok' and len(wx.fake.calls) == 2


@pytest.mark.parametrize('content', [
    b'not json', json.dumps({'error': True, 'reason': 'x'}).encode(), body('2026-11-07'),
    # Impossible values or other units are a malformed body, never an "ok" day.
    body('2026-11-06', pop=999), body('2026-11-06', pop=-1), body('2026-11-06', rain=-5),
    body('2026-11-06', high=75), body('2026-11-06', low=-120), body('2026-11-06', high=5, low=9),
    body('2026-11-06', units={**UNITS, 'temperature_2m_max': '°F'}), body('2026-11-06', units={**UNITS, 'precipitation_sum': 'inch'}),
    body('2026-11-06', units=['%']),
])
def test_malformed_wrong_day_or_impossible_body_is_unknown_and_not_cached(wx, content):
    t = trip(wx.owner.client)
    wx.weather.fetcher = lambda url, **o: SimpleNamespace(mime='application/json', content=content)
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['reason_codes'] == ['WEATHER_INVALID']
    wx.weather.fetcher = wx.fake
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['cache_hit'] is False


def test_boundary_values_and_missing_units_use_provider_defaults(wx):
    t = trip(wx.owner.client)
    content = json.loads(body('2026-11-06', pop=100, rain=0, high=-3.5, low=-3.5))
    del content['daily_units']
    wx.weather.fetcher = lambda url, **o: SimpleNamespace(mime='application/json', content=json.dumps(content).encode())
    result = wx.weather.forecast(wx.actor, t['id'], '2026-11-06')
    assert result['status'] == 'ok' and result['daily']['precipitation_probability_max'] == 100
    assert result['units'] == {'precipitation_probability_max': '%', 'precipitation_sum': 'mm',
                               'temperature_2m_max': '°C', 'temperature_2m_min': '°C'}


def test_timezone_is_the_trip_city_zone_not_the_reference_point_zone(wx):
    t = trip(wx.owner.client, city='hanoi')
    result = wx.weather.forecast(wx.actor, t['id'], '2026-11-06')
    assert result['status'] == 'ok' and result['city'] == 'hanoi'
    assert result['timezone'] == CITIES['hanoi']['timezone'] == 'Asia/Ho_Chi_Minh'
    assert parse_qs(urlsplit(wx.fake.calls[-1][0]).query)['timezone'] == ['Asia/Ho_Chi_Minh']


def test_out_of_range_and_invalid_dates_make_no_call(wx):
    far = trip(wx.owner.client, start='2026-11-25', end='2026-11-27')
    assert wx.weather.forecast(wx.actor, far['id'], '2026-11-25')['reason_codes'] == ['OUT_OF_FORECAST_RANGE']
    near = trip(wx.owner.client)
    assert wx.weather.forecast(wx.actor, near['id'], '2026-11-12')['reason_codes'] == ['DATE_OUTSIDE_TRIP']
    assert wx.weather.forecast(wx.actor, near['id'], '2026-02-30')['reason_codes'] == ['DATE_INVALID']
    past = trip(wx.owner.client, start='2026-11-01', end='2026-11-03')
    assert wx.weather.forecast(wx.actor, past['id'], '2026-11-02')['reason_codes'] == ['OUT_OF_FORECAST_RANGE']
    assert wx.weather.forecast(wx.actor, past['id'], None)['reason_codes'] == ['OUT_OF_FORECAST_RANGE']
    assert wx.fake.calls == []


def test_no_date_uses_today_while_travelling_else_next_stop_day(wx):
    during = trip(wx.owner.client, start='2026-11-04', end='2026-11-08')
    assert wx.weather.forecast(wx.actor, during['id'], None)['date'] == '2026-11-05'
    upcoming = trip(wx.owner.client, city='paris')
    result = wx.weather.forecast(wx.actor, upcoming['id'], None)
    assert result['city'] == 'paris' and result['date'] == '2026-11-06'
    assert parse_qs(urlsplit(wx.fake.calls[-1][0]).query)['timezone'] == [center('paris')['source_timezone']]


@pytest.mark.parametrize('change', [{'external_enabled': False}, {'disabled_providers': ['open_meteo']}, {'mode': 'read_only'}])
def test_kill_switch_is_unknown_with_no_fetch_even_when_cached(wx, change):
    from src.operations import controls
    t = trip(wx.owner.client)
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['status'] == 'ok'
    controls.update(wx.service.app.state.db, **change)
    for day in ('2026-11-06', '2026-11-07'):
        assert wx.weather.forecast(wx.actor, t['id'], day)['reason_codes'] == ['EXTERNAL_CALLS_PAUSED']
    assert len(wx.fake.calls) == 1


def test_daily_quotas_per_user_and_global(wx):
    t = trip(wx.owner.client)
    wx.weather.user_daily = 1
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['status'] == 'ok'
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-07')['reason_codes'] == ['WEATHER_USER_DAILY_LIMIT']
    other = wx.service.login('weather-quota-other')
    t2 = trip(other.client, city='osaka')
    wx.weather.global_daily = 1
    assert wx.weather.forecast(SimpleNamespace(id=other.user['id']), t2['id'], '2026-11-06')['reason_codes'] == ['WEATHER_DAILY_LIMIT']
    assert len(wx.fake.calls) == 1
    wx.clock[0] = NOW.replace(day=6)  # new UTC day
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-07')['status'] == 'ok'


def test_other_users_trip_is_not_found_and_disabled_is_unknown(wx):
    other = wx.service.login('weather-intruder')
    t = trip(wx.owner.client)
    with pytest.raises(DomainError) as error:
        wx.weather.forecast(SimpleNamespace(id=other.user['id']), t['id'], '2026-11-06')
    assert error.value.code == 'NOT_FOUND' and error.value.status == 404
    wx.weather.enabled = False
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['reason_codes'] == ['WEATHER_DISABLED']
    with pytest.raises(DomainError):
        wx.weather.forecast(SimpleNamespace(id=other.user['id']), t['id'], '2026-11-06')
    assert wx.fake.calls == []


def _snapshot(db):
    """Every table except the zero-cost usage records the call is meant to write
    and the background job dispatcher's own heartbeat."""
    with db.connect() as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted(map(tuple, con.execute(f'SELECT * FROM "{name}"')), key=repr)
                for name in tables if name not in {'usage_reservations', 'usage_ledger', 'dispatcher_leases'}}


def test_forecast_never_changes_trip_or_itinerary_state(wx):
    from tests.test_foundation_api import _job
    t = trip(wx.owner.client)
    created = wx.owner.client.post(f"/api/v2/trips/{t['id']}/itineraries", headers={'Idempotency-Key': 'weather-itinerary-unchanged'},
                                   json={'trip_version': t['version'], 'conditions_version': 0,
                                         'start_date': t['start_date'], 'end_date': t['start_date'], 'selected': []})
    assert created.status_code == 202, created.text
    assert _job(wx.owner.client, created.json())['state'] == 'succeeded'
    path = f"/api/v2/trips/{t['id']}/itineraries/{created.json()['itinerary_id']}"
    before, trip_before = wx.owner.client.get(path).json(), wx.owner.client.get('/api/v2/trips/' + t['id']).json()
    assert before['active_revision_id']
    db_before = _snapshot(wx.service.app.state.db)
    assert wx.weather.forecast(wx.actor, t['id'], '2026-11-06')['status'] == 'ok'
    after, trip_after = wx.owner.client.get(path).json(), wx.owner.client.get('/api/v2/trips/' + t['id']).json()
    assert trip_before['version'] == trip_after['version']
    for field in ('version', 'active_revision_id', 'revisions', 'items', 'unplaced', 'state'):
        assert before[field] == after[field], field
    assert _snapshot(wx.service.app.state.db) == db_before
    source = open(W.__file__, encoding='utf-8').read()
    assert 'itineraries' not in source and 'apply' not in source


def test_setting_three_states_render_default_and_app_wiring(wx, monkeypatch):
    assert W.enabled(SimpleNamespace(weather_enabled=None, environment='development')) is True
    assert W.enabled(SimpleNamespace(weather_enabled=None, environment='production')) is False
    assert W.enabled(SimpleNamespace(weather_enabled=False, environment='development')) is False
    assert W.enabled(SimpleNamespace(weather_enabled=True, environment='production')) is True
    monkeypatch.delenv('WEATHER_ENABLED', raising=False)
    assert Settings().weather_enabled is None
    monkeypatch.setenv('WEATHER_ENABLED', '')
    assert Settings().weather_enabled is False
    monkeypatch.setenv('WEATHER_ENABLED', '1')
    assert Settings().weather_enabled is True
    monkeypatch.setenv('WEATHER_ENABLED', ' TRUE \n')
    assert Settings().weather_enabled is True
    monkeypatch.setenv('WEATHER_ENABLED', ' 0 ')
    assert Settings().weather_enabled is False
    from scripts.prepare_render_env import RUNTIME_DEFAULTS
    assert RUNTIME_DEFAULTS['WEATHER_ENABLED'] == '0'
    # conftest sets WEATHER_ENABLED=0, so the development fixture app has no provider.
    assert wx.service.app.state.weather is None


def test_agent_tool_returns_forecast_or_unknown(wx):
    from src.trip_agent.tools import get_weather
    t = trip(wx.owner.client)
    ctx = SimpleNamespace(services=SimpleNamespace(weather=wx.weather), actor=wx.actor, trip_id=t['id'])
    assert get_weather(ctx, {'date': '2026-11-06'})['status'] == 'ok'
    ctx.services.weather = None
    assert get_weather(ctx, {'date': '2026-11-06'})['status'] == 'unknown'


def test_enabled_setting_wires_provider_into_app(tmp_path):
    from api import create_app
    settings = Settings(database_path=tmp_path / 'service.sqlite3', documents_dir=tmp_path / 'documents',
                        vectors_dir=tmp_path / 'vectors', environment='production', weather_enabled=True)
    app = create_app(settings)
    assert isinstance(app.state.weather, W.Weather) and app.state.weather.fetcher is W.fetch_public
    app.state.db.close()
