"""Daily city-centre forecast from Open-Meteo, for Plan B prompts only.

Only the GeoNames reference point of a registered trip city leaves the server.
`forecast` takes no coordinate argument: accommodation, chosen origin and
other private locations cannot reach the provider. The answer never changes an
itinerary. Every failure (disabled, paused, out of range, quota, HTTP error,
timeout, malformed body) is "unknown", never a guessed "clear" day.

Free-tier terms (non-commercial, rate limits, shared-IP hosts such as Render,
CC BY 4.0 attribution) were re-checked on 2026-10-21; see the stage 3 report.
Each sent request is a zero-cost row in usage_reservations, the same way the
public map and photo providers record their free calls.
"""
from __future__ import annotations

from datetime import date as Date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import threading
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.discovery.public_places import CENTERS, center
from src.discovery.safe_fetch import fetch_public
from src.foundation.repository import DomainError

PROVIDER = 'open_meteo'
POLICY = 'open-meteo-free-v1'
ENDPOINT = 'https://api.open-meteo.com/v1/forecast'
DAILY = ('precipitation_probability_max', 'precipitation_sum', 'temperature_2m_max', 'temperature_2m_min')
# Provider defaults (no unit parameters are sent); a body in other units is rejected.
UNITS = {'precipitation_probability_max': '%', 'precipitation_sum': 'mm',
         'temperature_2m_max': '°C', 'temperature_2m_min': '°C'}
# Physically possible bounds. Outside them the body is malformed, not weather.
BOUNDS = {'precipitation_probability_max': (0, 100), 'precipitation_sum': (0, 2000),
          'temperature_2m_max': (-90, 60), 'temperature_2m_min': (-90, 60)}
FORECAST_DAYS = 16  # Provider maximum, today included.
CACHE_TTL = timedelta(hours=3)  # [가설] city/date granularity.
CACHE_LIMIT = 512
MAX_BYTES = 65_536
TIMEOUT_SECONDS = 4
# [가설] Far below the provider's 10,000/day; a shared Render IP may hit limits earlier.
GLOBAL_DAILY = 500
USER_DAILY = 50
COOLDOWN = timedelta(minutes=15)
MESSAGE = '날씨를 확인하지 못했어요'
ATTRIBUTION = {'text': 'Weather data by Open-Meteo.com', 'url': 'https://open-meteo.com/', 'license': 'CC BY 4.0'}
CENTER_ATTRIBUTION = {'text': 'GeoNames', 'url': 'https://www.geonames.org/', 'license': 'CC BY 4.0',
                      'license_url': CENTERS['license_url']}
# A connection that never opened sent no provider request and uses no quota.
NOT_SENT = ('CONNECT_FAILED', 'DNS_FAILED', 'DNS_BUSY')
REFUSED = ('HTTP_429', 'HTTP_403')
FATAL = {'NOT_FOUND', 'ACCESS_REVOKED', 'TRIP_DELETED'}


def enabled(settings):
    """PRD decision 4 = A: unset means development on / everywhere else off."""
    value = getattr(settings, 'weather_enabled', None)
    return value is True or value is None and getattr(settings, 'environment', None) == 'development'


def unknown(*codes):
    return {'status': 'unknown', 'reason_codes': list(codes), 'message': MESSAGE}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def _parse_day(value):
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return Date.fromisoformat(value)
    except ValueError:
        return None


def city_timezone(city):
    """The trip city's IANA zone; the GeoNames point's zone only as a fallback.

    They differ for Hanoi (Asia/Ho_Chi_Minh vs Asia/Bangkok in the reference data).
    """
    from src.destinations import CITIES
    return (CITIES.get(city) or {}).get('timezone') or center(city)['source_timezone']


def request_url(city, day):
    """Fixed host/path; the only location is the city reference point."""
    c = center(city)
    return ENDPOINT + '?' + urlencode({
        'latitude': repr(float(c['latitude'])), 'longitude': repr(float(c['longitude'])),
        'daily': ','.join(DAILY), 'timezone': city_timezone(city),
        'start_date': day.isoformat(), 'end_date': day.isoformat()})


def _number(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('non-numeric')
    return value


def parse(content, day):
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get('error') or not isinstance(payload.get('daily'), dict):
        raise ValueError('invalid')
    daily = payload['daily']
    if daily.get('time') != [day.isoformat()]:
        raise ValueError('wrong day')
    values = {}
    for name in DAILY:
        series = daily.get(name)
        if not isinstance(series, list) or len(series) != 1:
            raise ValueError('missing')
        value = values[name] = _number(series[0])
        low, high = BOUNDS[name]
        if value is not None and not low <= value <= high:
            raise ValueError('out of range')
    if all(value is None for value in values.values()):
        raise ValueError('empty')
    hot, cold = values['temperature_2m_max'], values['temperature_2m_min']
    if hot is not None and cold is not None and hot < cold:
        raise ValueError('max below min')
    units = payload.get('daily_units')
    if units is not None and not isinstance(units, dict):
        raise ValueError('units')
    for name, expected in UNITS.items():
        if (units or {}).get(name, expected) != expected:
            raise ValueError('unexpected unit')
    return values, dict(UNITS)


class Weather:
    def __init__(self, db, repo, discovery=None, *, fetcher=fetch_public, clock=None, enabled=True,
                 global_daily=GLOBAL_DAILY, user_daily=USER_DAILY):
        self.db, self.repo, self.discovery, self.fetcher = db, repo, discovery, fetcher
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.enabled, self.global_daily, self.user_daily = enabled, global_daily, user_daily
        self._cache, self._lock, self._keys = {}, threading.Lock(), {}

    # --- city/date ------------------------------------------------------------
    def _city(self, actor, trip_id, requested, now):
        """Returns (city, day) or (None, reason). Raises NOT_FOUND for other trips."""
        trip = self.repo.get_trip(actor.id, trip_id)
        stops = [(s, _key(s.get('city'))) for s in trip.get('stops') or []]
        stops = [(s, city) for s, city in stops if city and center(city)]
        if requested is not None:
            day = _parse_day(requested)
            if day is None:
                return None, 'DATE_INVALID'
            start, end = _parse_day(trip.get('start_date')), _parse_day(trip.get('end_date'))
            if start and end and not start <= day <= end:
                return None, 'DATE_OUTSIDE_TRIP'
            for stop, city in stops:
                if stop['start_date'] <= day.isoformat() <= stop['end_date']:
                    return city, day
            city = self._conditions_city(actor, trip_id) or (stops[0][1] if stops else None)
            return (city, day) if city else (None, 'CITY_UNSUPPORTED')
        # No date: today in the stop's city when travelling, else the next stop's first day.
        for stop, city in stops:
            today = now.astimezone(ZoneInfo(city_timezone(city))).date().isoformat()
            if stop['start_date'] <= today <= stop['end_date']:
                return city, Date.fromisoformat(today)
        for stop, city in stops:
            today = now.astimezone(ZoneInfo(city_timezone(city))).date().isoformat()
            if stop['end_date'] >= today:
                return city, Date.fromisoformat(max(stop['start_date'], today))
        if stops:
            return stops[0][1], Date.fromisoformat(stops[0][0]['start_date'])
        city = self._conditions_city(actor, trip_id)
        day = _parse_day(trip.get('start_date'))
        return (city, day) if city and day else (None, 'CITY_UNSUPPORTED')

    def _conditions_city(self, actor, trip_id):
        if self.discovery is None:
            return None
        try:
            value = self.discovery.get_conditions(actor, trip_id)
        except DomainError as exc:
            if exc.code in FATAL:
                raise
            return None
        if value.get('city_needs_confirmation'):
            return None
        city = (value.get('conditions') or {}).get('city')
        return city if isinstance(city, str) and center(city) else None

    # --- cache -----------------------------------------------------------------
    def _cached(self, key, now):
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
            self._cache.pop(key, None)
        return None

    def _store(self, key, value, expires):
        with self._lock:
            if len(self._cache) >= CACHE_LIMIT:
                for old in sorted(self._cache, key=lambda k: self._cache[k][0])[:len(self._cache) - CACHE_LIMIT + 1]:
                    self._cache.pop(old, None)
            self._cache[key] = (expires, value)

    def _key_lock(self, key):
        with self._lock:
            return self._keys.setdefault(key, threading.Lock())

    # --- public ----------------------------------------------------------------
    def forecast(self, actor, trip_id, date=None):
        try:
            return self._forecast(actor, trip_id, date)
        except DomainError as exc:
            if exc.code in FATAL:
                raise
            return unknown(exc.code)

    def _forecast(self, actor, trip_id, requested):
        if not self.enabled:
            # Ownership still applies even when the feature is off.
            self.repo.get_trip(actor.id, trip_id)
            return unknown('WEATHER_DISABLED')
        now = self.clock().astimezone(timezone.utc)
        city, day = self._city(actor, trip_id, requested, now)
        if city is None:
            return unknown(day)
        today = now.astimezone(ZoneInfo(city_timezone(city))).date()
        if not 0 <= (day - today).days < FORECAST_DAYS:
            return unknown('OUT_OF_FORECAST_RANGE')
        from src.operations.controls import external_guard
        # The operator stop is absolute: no new call and no stale answer.
        external_guard(self.db, PROVIDER)
        key = (city, day.isoformat())
        cached = self._cached(key, now)
        if cached:
            return {**cached, 'cache_hit': True}
        with self._key_lock(key):
            cached = self._cached(key, self.clock().astimezone(timezone.utc))
            if cached:
                return {**cached, 'cache_hit': True}
            return self._fetch(actor, trip_id, city, day, key)

    def _fetch(self, actor, trip_id, city, day, key):
        from src.reliability.budget import Budget, CallContext
        url = request_url(city, day)
        now = self.clock().astimezone(timezone.utc)
        stamp, period = now.isoformat(), now.date().isoformat()
        context = CallContext(actor.id, trip_id, trip_id)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            Budget._scope(con, context)
            refused = con.execute(
                'SELECT updated_at FROM usage_reservations WHERE provider=? AND error_code IN (?,?) ORDER BY updated_at DESC LIMIT 1',
                (PROVIDER, *REFUSED)).fetchone()
            if refused and _stamp(refused['updated_at']) + COOLDOWN > now:
                return unknown('WEATHER_RATE_LIMITED')
            rows = con.execute(
                'SELECT owner_id FROM usage_reservations WHERE provider=? AND period_day=? AND (error_code IS NULL OR error_code NOT IN (?,?,?))',
                (PROVIDER, period, *NOT_SENT)).fetchall()
            if len(rows) >= self.global_daily:
                return unknown('WEATHER_DAILY_LIMIT')
            if sum(row['owner_id'] == actor.id for row in rows) >= self.user_daily:
                return unknown('WEATHER_USER_DAILY_LIMIT')
            call_id = 'call_' + uuid4().hex
            con.execute('INSERT INTO usage_reservations(call_id,owner_id,trip_id,job_id,scope_kind,scope_id,provider,sku,operation,attempt,call_key,request_hash,state,currency,estimated_units_json,estimated_cost_micros,price_version,price_confirmed_at,price_rates_json,period_day,period_month,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (call_id, actor.id, trip_id, None, 'personal_trip', trip_id, PROVIDER, 'forecast_daily', 'weather_forecast', 1,
                 'weather:' + call_id, sha256(url.encode()).hexdigest(), 'sent', 'USD', _dump({'calls': 1, 'response_bytes': MAX_BYTES}),
                 0, POLICY, stamp, _dump({'calls': 0, 'response_bytes': 0}), period, now.strftime('%Y-%m'), stamp, stamp))
            Budget._ledger(con, call_id, 'reserved', 0, {'calls': 1, 'response_bytes': MAX_BYTES}, stamp, reason='EXPLICIT_FREE_PUBLIC_DATA')
        code, received, result = None, None, None
        try:
            page = self.fetcher(url, max_bytes=MAX_BYTES, timeout_seconds=TIMEOUT_SECONDS, max_redirects=0)
            received = len(page.content)
            if received > MAX_BYTES or page.mime != 'application/json':
                raise ValueError('unexpected response')
            daily, units = parse(page.content, day)
        except Exception as exc:  # noqa: BLE001 - any provider failure is "unknown"
            status = getattr(exc, 'http_status', None)
            raw = getattr(exc, 'code', None)
            code = 'HTTP_' + str(status) if status else raw if isinstance(raw, str) and raw.isupper() else 'WEATHER_INVALID'
            reason = ('WEATHER_RATE_LIMITED' if status in (429, 403) else 'WEATHER_REQUEST_REJECTED' if status == 400
                      else 'WEATHER_TIMEOUT' if code == 'FETCH_TIMEOUT' else 'WEATHER_INVALID' if code == 'WEATHER_INVALID'
                      else 'WEATHER_UNAVAILABLE')
            result = unknown(reason)
        finally:
            done = self.clock().astimezone(timezone.utc).isoformat()
            units_used = {'calls': 0 if code in NOT_SENT else 1, 'response_bytes': received}
            with self.db.connect() as con:
                con.execute("UPDATE usage_reservations SET state='settled',actual_units_json=?,actual_cost_micros=0,error_code=?,updated_at=? WHERE call_id=?",
                            (_dump(units_used), code, done, call_id))
                Budget._ledger(con, call_id, 'settled', 0, units_used, done, reason=code or 'EXPLICIT_FREE_PUBLIC_DATA')
        if result is not None:
            return result
        expires = now + CACHE_TTL
        value = {'status': 'ok', 'city': city, 'date': day.isoformat(), 'timezone': city_timezone(city),
                 'daily': daily, 'units': units, 'scope': 'city_center', 'attribution': ATTRIBUTION,
                 'center_attribution': CENTER_ATTRIBUTION, 'fetched_at': stamp, 'expires_at': expires.isoformat()}
        self._store(key, value, expires)
        return {**value, 'cache_hit': False}


def _key(value):
    from src.destinations import city_key
    return city_key(value)
