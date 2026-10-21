"""Stage 4 D3: saved places ordered by straight-line distance. No provider call.

Anchor (the point distances are measured from):
  * ``anchor=<bookmark id>``: another saved place of this trip with coordinates;
  * otherwise the trip's *currently resolved* discovery origin (``origin``,
    from ``resolved_origin(get_conditions(...))``), used only inside the
    server. Its coordinates are never returned.
Distances are Haversine meters labelled ``straight_line_not_walking_time``.
Places without permitted coordinates keep their saved order at the end with a
null distance. Nothing is sent to any external service.
"""
from src.foundation.repository import DomainError

BASIS = 'straight_line_not_walking_time'


def _coordinates(con, discovery, trip_id, place_ids):
    if not place_ids:
        return {}
    rows = discovery._catalog_on(con, trip_id, None, include_photos=False, place_ids=sorted(set(place_ids)))
    return {r['place_id']: r for r in rows if r.get('latitude') is not None and r.get('longitude') is not None}


def resolved_origin(envelope):
    """Origin point from ``DiscoveryService.get_conditions`` (the resolved envelope).

    Reuses the recommendation path's rules: only an origin whose
    ``origin_context.status`` is ``ready`` counts. A deleted or invalidated
    accommodation, an expired coordinate or an outdated context gives None,
    so stored ``conditions_json`` coordinates are never read directly.
    """
    context = envelope.get('origin_context') or {}
    if envelope.get('validation') or context.get('status') != 'ready':
        return None
    origin = (envelope.get('conditions') or {}).get('origin') or {}
    return origin if origin.get('latitude') is not None and origin.get('longitude') is not None else None


def nearest(con, discovery, trip_id, items, anchor=None, origin=None):
    """Return (ordered items with ``straight_line_m``, order metadata)."""
    from src.recommendations.engine import straight_line_distance
    coords = _coordinates(con, discovery, trip_id, [i['matched_place_id'] for i in items if i.get('matched_place_id')])
    point, kind, anchor_id = None, 'none', None
    if anchor:
        base = next((i for i in items if i['id'] == anchor), None)
        if base is None:
            raise DomainError('NOT_FOUND', '기준으로 고른 저장 장소를 찾을 수 없습니다.', 404)
        point = coords.get(base.get('matched_place_id'))
        if point is None:
            raise DomainError('ANCHOR_COORDINATES_UNKNOWN', '기준 장소의 좌표를 확인할 수 없어 가까운 순서를 만들 수 없습니다.', 422)
        kind, anchor_id = 'saved_place', anchor
    else:
        point = origin
        kind = 'trip_origin' if point else 'none'
    output = []
    for index, item in enumerate(items):
        place = coords.get(item.get('matched_place_id'))
        distance = straight_line_distance(point, place) if point and place else None
        output.append({**item, 'straight_line_m': round(distance, 1) if distance is not None else None,
                       'distance_basis': BASIS if distance is not None else None, '_index': index})
    if point:
        output.sort(key=lambda i: (i['straight_line_m'] is None, i['straight_line_m'] or 0, i['_index']))
    for item in output:
        item.pop('_index')
    meta = {'order': 'nearest' if point else 'recent', 'anchor_kind': kind, 'anchor_bookmark_id': anchor_id,
            'distance_basis': BASIS, 'provider_calls': 0,  # by construction: no provider client is used here
            'with_distance': sum(i['straight_line_m'] is not None for i in output), 'without_distance': sum(i['straight_line_m'] is None for i in output),
            'reason': None if point else 'ANCHOR_UNAVAILABLE'}
    return output, meta
