"""Unrounded geometry only: no route or walking-time claims."""
import math


def coordinate_pair(latitude, longitude):
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (latitude, longitude)):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return (latitude, longitude)


def coordinates(endpoint, *, require_permission=True):
    if not isinstance(endpoint, dict) or require_permission and endpoint.get('coordinate_permitted') is not True:
        return None
    return coordinate_pair(endpoint.get('latitude'), endpoint.get('longitude'))


def haversine_straight_line(a, b):
    """Return exact floating-point meters, or None for an invalid lat/lon pair."""
    try:
        a, b = coordinate_pair(*a), coordinate_pair(*b)
    except (TypeError, ValueError):
        return None
    if a is None or b is None:
        return None
    lat_a, lat_b = math.radians(a[0]), math.radians(b[0])
    h = math.sin((lat_b-lat_a)/2)**2 + math.cos(lat_a)*math.cos(lat_b)*math.sin(math.radians(b[1]-a[1])/2)**2
    return 6371000 * 2 * math.asin(math.sqrt(max(0, min(1, h))))


def same_identity(a, b):
    """These IDs must be from server-owned records, never arbitrary client input.

    Equal coordinates alone do not establish the same entrance or place.
    """
    return bool(a.get('place_id') and a.get('place_id') == b.get('place_id')) or bool(
        a.get('identity_confirmed') is True and b.get('identity_confirmed') is True
        and a.get('id') and a.get('id') == b.get('id') and a.get('version') == b.get('version'))
