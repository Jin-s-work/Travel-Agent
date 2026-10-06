"""Normalized adapters; all real calls must be wrapped by ProviderGateway.

No SDK retries, no URL navigation, no implicit public geocoder and no fake
production fallback. Standard Google terms are not a durable-storage grant:
real adapters require an explicitly reviewed permission record before activation.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import time
from typing import Protocol
from urllib.parse import quote

import httpx
from src.foundation.repository import DomainError, dump
from src.reliability.providers import ProviderResult, DefinitelyNotSent, ProviderRejected
from .geometry import coordinates, same_identity


class GeocodingProvider(Protocol):
    def resolve(self, query: str, city: str, limits: dict) -> ProviderResult: ...


class RouteProvider(Protocol):
    def matrix(self, origins: list, destinations: list, mode: str, departure: str, limits: dict) -> ProviderResult: ...


def stamp(clock=None):
    return (clock() if clock else datetime.now(timezone.utc)).astimezone(timezone.utc)


def bounded(limits, name, default, maximum):
    value = (limits or {}).get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise DomainError('LOCATION_LIMIT_INVALID', '장소 조회 상한을 확인해 주세요.', 422)
    return value


def identity(point):
    # No private address in route evidence or cache key diagnostics.
    return {key: point.get(key) for key in ('id', 'version', 'place_id', 'coordinate_version')}


def empty_element(origin, destination, oi, di, mode, provider, reason, now):
    return {'origin_index': oi, 'destination_index': di, 'origin_identity': identity(origin),
        'destination_identity': identity(destination), 'mode': mode, 'status': 'unknown',
        'distance_m': None, 'duration_seconds': None, 'provider': provider.name,
        'adapter_version': provider.adapter_version, 'policy_version': provider.policy_version,
        'checked_at': now.isoformat(), 'expires_at': None, 'reason_codes': [reason],
        'usage_permission': provider.permission(), 'accessibility_status': 'unknown'}


class DisabledGeocodingProvider:
    name = 'disabled'; sku = 'geocoding'; adapter_version = 'disabled_v1'
    policy_version = None; enabled = False; usage_permitted = False; external = False
    def permission(self):
        return {'display': False, 'durable_storage': False, 'cache': False, 'namespace': 'private_trip'}
    def resolve(self, query, city, limits=None):
        return ProviderResult({'status': 'unavailable', 'provider': self.name,
            'adapter_version': self.adapter_version, 'checked_at': None, 'expires_at': None,
            'usage_permission': self.permission(), 'candidates': [], 'reason_codes': ['GEOCODING_DISABLED']}, {'requests': 0})


class DisabledRouteProvider(DisabledGeocodingProvider):
    sku = 'matrix'
    def matrix(self, origins, destinations, mode, departure, limits=None):
        now = stamp()
        return ProviderResult({'provider': self.name, 'adapter_version': self.adapter_version,
            'elements': [empty_element(a, b, i, j, mode, self, 'ROUTES_DISABLED', now)
                for i, a in enumerate(origins) for j, b in enumerate(destinations)]}, {'matrix_elements': 0})


class FakeGeocodingProvider(DisabledGeocodingProvider):
    """Explicit test dependency; never returned by the production factory."""
    name = 'synthetic_location'; sku = 'geocoding'; adapter_version = 'synthetic_geocoding_v1'
    policy_version = 'synthetic_fixture_v1'; enabled = True; usage_permitted = True; external = False
    def __init__(self, candidates=None, *, clock=None, fail=False):
        self.candidates = candidates or []; self.clock = clock; self.fail = fail; self.calls = 0
    def permission(self):
        return {'display': True, 'durable_storage': True, 'cache': True, 'namespace': 'private_trip',
                'ttl_seconds': 86400, 'reference': 'synthetic_fixture', 'synthetic': True}
    def resolve(self, query, city, limits=None):
        self.calls += 1
        if self.fail: raise TimeoutError('synthetic response loss')
        now = stamp(self.clock); expiry = (now + timedelta(days=1)).isoformat()
        cap = int(bounded(limits, 'max_candidates', 5, 5))
        supplied = self.candidates.get((query, city), []) if isinstance(self.candidates, dict) else self.candidates
        candidates = []
        for n, row in enumerate(supplied[:cap]):
            value = deepcopy(row)
            value.setdefault('candidate_id', 'synthetic_candidate_' + str(n))
            value.setdefault('provider_place_id', 'synthetic_place_' + str(n))
            value.setdefault('city', city)
            value.setdefault('name', 'Synthetic hotel ' + str(n))
            value.setdefault('original_name', value['name'])
            value.setdefault('address', None); value.setdefault('timezone', None)
            value.setdefault('map_url', None); value.setdefault('official_url', None)
            value.update({'checked_at': now.isoformat(), 'expires_at': expiry,
                'coordinate_permitted': coordinates(value, require_permission=False) is not None,
                'source_id': 'synthetic_fixture', 'precision': 'synthetic', 'synthetic': True,
                'retention_policy': self.permission()})
            candidates.append(value)
        return ProviderResult({'status': 'candidates' if candidates else 'empty', 'provider': self.name,
            'adapter_version': self.adapter_version, 'checked_at': now.isoformat(), 'expires_at': expiry,
            'usage_permission': self.permission(), 'candidates': candidates, 'reason_codes': []}, {'requests': 1})


class FakeRouteProvider(FakeGeocodingProvider):
    sku = 'matrix'; adapter_version = 'synthetic_routes_v1'
    def __init__(self, routes=None, *, clock=None, fail=False):
        super().__init__(clock=clock, fail=fail); self.routes = routes or {}
    def matrix(self, origins, destinations, mode, departure, limits=None):
        self.calls += 1
        if self.fail: raise TimeoutError('synthetic response loss')
        now = stamp(self.clock)
        if len(origins)*len(destinations) > bounded(limits, 'max_elements', 30, 625):
            raise ValueError('Synthetic matrix bound exceeded')
        elements = []
        for i, a in enumerate(origins):
            for j, b in enumerate(destinations):
                element = empty_element(a, b, i, j, mode, self, 'NO_ROUTE', now)
                row = self.routes.get((a.get('id'), b.get('id'), mode))
                if row is not None:
                    element.update(deepcopy(row))
                    element.update({'status': 'ok', 'expires_at': (now+timedelta(days=1)).isoformat(),
                        'reason_codes': [], 'synthetic': True})
                elements.append(element)
        return ProviderResult({'provider': self.name, 'adapter_version': self.adapter_version,
            'elements': elements}, {'matrix_elements': len(elements)})


class _GoogleBase:
    name = 'google_maps'; external = True
    def __init__(self, api_key='', policy=None, *, clock=None, transport=None):
        self._api_key = api_key; self.policy = deepcopy(policy or {}); self.clock = clock
        self.transport = transport; self.calls = 0
        self.policy_version = self.policy.get('version')
        self.enabled = bool(api_key and self.policy.get('enabled') is True)
        # A short TTL is not permission to keep immutable historical snapshots.
        # The reviewed record must explicitly allow the application's full data life cycle.
        self.usage_permitted = bool(self.enabled and self.policy_version and self.policy.get('permission_reference')
            and all(self.policy.get(k) is True for k in ('usage_permitted', 'private_input_permitted',
                'durable_storage_permitted', 'display_without_map_permitted', 'immutable_history_permitted'))
            and isinstance(self.policy.get('cache_ttl_seconds'), int)
            and not isinstance(self.policy.get('cache_ttl_seconds'), bool)
            and 0 < self.policy['cache_ttl_seconds'] <= 86400)
    def permission(self):
        return {'display': self.usage_permitted, 'durable_storage': self.usage_permitted,
            'cache': self.usage_permitted, 'namespace': 'private_trip',
            'ttl_seconds': self.policy.get('cache_ttl_seconds', 0),
            'reference': self.policy.get('permission_reference'), 'attribution': 'Google Maps',
            'immutable_history': self.policy.get('immutable_history_permitted') is True}
    def policy_fingerprint(self, con=None):
        return hashlib.sha256(dump(self.policy).encode()).hexdigest()
    def _expires(self, now):
        return (now+timedelta(seconds=self.policy['cache_ttl_seconds'])).isoformat()
    def _post(self, url, body, fields, limits):
        if not self.usage_permitted:
            raise DomainError('LOCATION_POLICY_UNAVAILABLE', '지도 자료의 이용·보관 설정이 확인되지 않았습니다.', 503)
        timeout = bounded(limits, 'timeout_seconds', 8, 8)
        self.calls += 1
        started = time.monotonic()
        # Fixed provider endpoint only, no caller-controlled URL or redirects.
        transport = self.transport or httpx.HTTPTransport(retries=0)
        try:
            with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as client:
                with client.stream('POST', url, json=body, headers={'X-Goog-Api-Key': self._api_key,
                        'X-Goog-FieldMask': fields, 'Content-Type': 'application/json'}) as response:
                    if 400 <= response.status_code < 500:
                        raise ProviderRejected('MAPS_RATE_LIMITED' if response.status_code == 429 else 'MAPS_REQUEST_REJECTED')
                    if response.status_code != 200:
                        raise RuntimeError('Maps response outcome not known')
                    if 'application/json' not in response.headers.get('content-type', '').lower():
                        raise ValueError('Unexpected maps MIME')
                    data = bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic()-started > timeout: raise TimeoutError('Maps response deadline exceeded')
                        data.extend(part)
                        if len(data) > 1024*1024: raise ValueError('Maps response exceeds cap')
                    return json.loads(data)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise DefinitelyNotSent() from None


class GooglePlacesGeocodingProvider(_GoogleBase):
    """Text Search is used for branch candidates; never auto-selects first hit."""
    sku = 'text_search_pro'; adapter_version = 'google_places_text_v1'
    fields = 'places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri,places.addressComponents,places.attributions'
    def resolve(self, query, city, limits=None):
        if not self.usage_permitted:
            value = DisabledGeocodingProvider().resolve(query, city, limits).value
            value.update({'provider': self.name, 'reason_codes': ['LOCATION_POLICY_UNAVAILABLE']})
            return ProviderResult(value, {'requests': 0})
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500 or not isinstance(city, str) or not city:
            raise DomainError('GEOCODING_INPUT_INVALID', '숙소 이름과 도시를 확인해 주세요.', 422)
        # A map URL is private saved input, not a text-search address or fetch target.
        if re.match(r'^https?://', query, flags=re.I):
            raise DomainError('MAP_LINK_NAME_REQUIRED', '지도 링크를 저장했습니다. 지점 확인에는 숙소 이름을 함께 입력해 주세요.', 422)
        cap = int(bounded(limits, 'max_candidates', 5, 5))
        data = self._post('https://places.googleapis.com/v1/places:searchText',
            {'textQuery': query.strip()+' '+city, 'pageSize': cap}, self.fields, limits)
        if not isinstance(data, dict) or not isinstance(data.get('places', []), list):
            raise ValueError('Invalid place response')
        now = stamp(self.clock); candidates = []
        for place in data.get('places', [])[:cap]:
            pid = place.get('id'); location = place.get('location') or {}
            point = {'latitude': location.get('latitude'), 'longitude': location.get('longitude')}
            if not isinstance(pid, str) or not pid or coordinates(point, require_permission=False) is None:
                continue
            # Do not copy the requested city as provider-confirmed city evidence.
            locality = next((c.get('longText') for c in place.get('addressComponents', [])
                if 'locality' in c.get('types', [])), None)
            name = (place.get('displayName') or {}).get('text')
            if not isinstance(name, str) or not name: continue
            candidates.append({'candidate_id': 'google_candidate_'+hashlib.sha256(pid.encode()).hexdigest()[:24],
                'provider_place_id': pid, 'name': name, 'original_name': name,
                'name_language': (place.get('displayName') or {}).get('languageCode'),
                'address': place.get('formattedAddress'), 'city': locality, 'query_city': city,
                **point, 'timezone': None, 'precision': 'place', 'coordinate_permitted': True,
                'map_url': 'https://www.google.com/maps/search/?api=1&query='+quote(name)+'&query_place_id='+quote(pid),
                'official_url': None, 'source_id': 'google_places:'+pid,
                'checked_at': now.isoformat(), 'expires_at': self._expires(now),
                'attributions': [{'provider': a.get('provider'), 'provider_uri': a.get('providerUri')}
                    for a in place.get('attributions', []) if isinstance(a, dict)],
                'retention_policy': self.permission()})
        return ProviderResult({'status': 'candidates' if candidates else 'empty', 'provider': self.name,
            'adapter_version': self.adapter_version, 'checked_at': now.isoformat(), 'expires_at': self._expires(now),
            'usage_permission': self.permission(), 'candidates': candidates, 'reason_codes': []}, {'requests': 1})


class GoogleRoutesProvider(_GoogleBase):
    sku = 'route_matrix_essentials'; adapter_version = 'google_routes_matrix_v1'
    fields = 'originIndex,destinationIndex,status,condition,distanceMeters,duration,fallbackInfo'
    def matrix(self, origins, destinations, mode, departure, limits=None):
        now = stamp(self.clock)
        if not self.usage_permitted:
            result = DisabledRouteProvider().matrix(origins, destinations, mode, departure, limits)
            return ProviderResult(result.value, {'matrix_elements': 0})
        mode_map = {'walking': 'WALK', 'transit': 'TRANSIT', 'car': 'DRIVE'}
        if mode not in mode_map: raise DomainError('ROUTE_MODE_UNSUPPORTED', '지원하지 않는 이동 수단입니다.', 422)
        count = len(origins)*len(destinations)
        if not origins or not destinations or count > bounded(limits, 'max_elements', 30, 625) or mode == 'transit' and count > 100:
            raise DomainError('ROUTE_ELEMENT_LIMIT', '경로 조회 상한을 초과했습니다.', 422)
        if any(coordinates(p) is None for p in origins+destinations):
            raise DomainError('ROUTE_COORDINATES_UNKNOWN', '경로에 필요한 위치가 미확인입니다.', 422)
        try:
            when = datetime.fromisoformat(departure)
            if when.tzinfo is None: raise ValueError()
        except (TypeError, ValueError):
            raise DomainError('ROUTE_DEPARTURE_UNKNOWN', '출발 시각의 시간대를 확인해 주세요.', 422) from None
        if when < now and mode != 'transit':
            raise DomainError('ROUTE_DEPARTURE_PAST', '과거 출발 시각은 이 이동 수단으로 조회할 수 없습니다.', 422)
        def waypoint(p):
            return {'waypoint': {'location': {'latLng': {'latitude': p['latitude'], 'longitude': p['longitude']}}}}
        body = {'origins': [waypoint(p) for p in origins], 'destinations': [waypoint(p) for p in destinations],
            'travelMode': mode_map[mode], 'departureTime': when.astimezone(timezone.utc).isoformat()}
        if mode == 'car': body['routingPreference'] = 'TRAFFIC_UNAWARE'
        data = self._post('https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix', body, self.fields, limits)
        if not isinstance(data, list): raise ValueError('Invalid matrix response')
        indexed = {}; duplicate = set()
        for row in data:
            if not isinstance(row, dict): continue
            # protobuf zero scalar indices can be omitted; negative/out-of-range are invalid.
            i, j = row.get('originIndex', 0), row.get('destinationIndex', 0)
            if type(i) is not int or type(j) is not int or not 0 <= i < len(origins) or not 0 <= j < len(destinations): continue
            if (i,j) in indexed: duplicate.add((i,j))
            indexed[i,j] = row
        elements = []
        for i,a in enumerate(origins):
            for j,b in enumerate(destinations):
                element = empty_element(a,b,i,j,mode,self,'ROUTE_ELEMENT_MISSING',now)
                row = indexed.get((i,j))
                if (i,j) in duplicate: element['reason_codes'] = ['ROUTE_ELEMENT_DUPLICATE']
                elif row is not None:
                    seconds = row.get('duration'); distance = row.get('distanceMeters')
                    valid_duration = isinstance(seconds,str) and re.fullmatch(r'\d+(?:\.\d{1,9})?s', seconds)
                    seconds = float(seconds[:-1]) if valid_duration else None
                    if row.get('status', {}).get('code', 0) != 0: element['reason_codes'] = ['ROUTE_ELEMENT_ERROR']
                    elif row.get('condition') != 'ROUTE_EXISTS': element['reason_codes'] = ['NO_ROUTE']
                    elif row.get('fallbackInfo'): element['reason_codes'] = ['ROUTE_PROVIDER_FALLBACK']
                    elif (seconds is None or not math.isfinite(seconds) or not 0 <= seconds <= 86400
                          or isinstance(distance,bool) or not isinstance(distance,(int,float))
                          or not math.isfinite(distance) or distance < 0
                          or (seconds == 0 or distance == 0) and not same_identity(a,b)):
                        element['reason_codes'] = ['ROUTE_RESPONSE_INVALID']
                    else:
                        element.update({'status': 'ok', 'distance_m': distance, 'duration_seconds': seconds,
                            'expires_at': self._expires(now), 'reason_codes': [], 'traffic_live': False})
                elements.append(element)
        # Every attempted element is reserved/settled, including no-route/error elements.
        return ProviderResult({'provider': self.name, 'adapter_version': self.adapter_version,
            'elements': elements}, {'matrix_elements': count})


def build_location_providers(config=None, *, api_key='', clock=None, transport=None):
    """One optional reviewed policy object. Absent/invalid config stays OFF.

    config={provider:'google_maps', geocoding:{...policy...}, routes:{...policy...}}.
    Price and total budget remain in the existing PRICING_CONFIG, never here.
    """
    if not config or config.get('provider') != 'google_maps':
        return DisabledGeocodingProvider(), DisabledRouteProvider()
    return (GooglePlacesGeocodingProvider(api_key,config.get('geocoding'),clock=clock,transport=transport),
        GoogleRoutesProvider(api_key,config.get('routes'),clock=clock,transport=transport))
