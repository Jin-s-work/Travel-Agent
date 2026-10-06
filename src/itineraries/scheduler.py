"""Bounded deterministic insertion around unchanged bookings and user locks."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
from math import isfinite
from zoneinfo import ZoneInfo

from .intervals import bounds, contains, local_iso, normalize_item, resolve_local, subtract, time_interval, utc
from .constraints import assess_visit, issue, opening_windows, validate

ENGINE_VERSION = 'itinerary-insertion-v1'
HOTELS = {'hotel', 'lodging', 'accommodation', '숙소', '호텔'}
FLIGHTS = {'flight', 'air', '항공', '항공편'}


def stable_id(*parts):
    return 'item_'+hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:28]


def activity_windows(snapshot):
    if snapshot.get('activity_windows'):
        return deepcopy(snapshot['activity_windows'])
    start, end = date.fromisoformat(snapshot['start_date']), date.fromisoformat(snapshot['end_date'])
    if end < start or (end-start).days >= 14:
        raise ValueError('Schedule supports 1 to 14 days')
    return [{'date': (start+timedelta(days=n)).isoformat(), 'start': snapshot.get('activity_start', '09:00'),
             'end': snapshot.get('activity_end', '21:00')} for n in range((end-start).days+1)]


def booking_items(bookings, snapshot):
    """Only current noncancelled events are retained. A hotel stay is not busy."""
    items, unresolved = [], []
    for booking in bookings:
        data = booking.get('effective') or booking
        status = data.get('status', booking.get('status', 'needs_review'))
        if status == 'cancelled':
            continue
        booking_id = booking.get('booking_id', booking.get('id'))
        kind = data.get('kind', booking.get('kind')) or ''
        events = data.get('events', [])
        if not events:
            events = [{'id': 'date_'+str(booking_id), 'event_type': 'date_only', 'start_local': data.get('date'), 'end_local': data.get('date_end'),
                       'start_timezone': None, 'end_timezone': None, 'location': data.get('location')}]
        for event in events:
            start, end = event.get('start_local'), event.get('end_local')
            start_day, end_day = str(start or '')[:10], str(end or start or '')[:10]
            event_id = event.get('id', event.get('event_id'))
            item_id = stable_id('booking', booking_id, event_id)
            lodging = kind.casefold() in HOTELS
            location = event.get('location')
            endpoint = deepcopy(location) if isinstance(location, dict) else {'label': location or data.get('location') or '', 'latitude': None, 'longitude': None}
            endpoint.setdefault('id', str(event_id)); endpoint.setdefault('city', snapshot['city'])
            endpoint.setdefault('coordinate_permitted', False)
            item = {'item_id': item_id, 'item_type': 'booking', 'booking_id': booking_id, 'booking_event_id': event_id,
                    'booking_version': booking.get('version'), 'booking_kind': kind, 'event_type': event.get('event_type'),
                    'place_id': None, 'name': data.get('provider') or data.get('name') or kind or '예약', 'native_name': None,
                    'local_start': start, 'local_end': end, 'start_timezone': event.get('start_timezone'),
                    'end_timezone': event.get('end_timezone') or event.get('start_timezone'),
                    'locked': True, 'lock_origin': 'booking', 'reservation_status': status,
                    'verification_status': 'verified' if status in {'source_verified', 'user_confirmed'} else 'provisional',
                    'source_refs': [{'kind': 'booking_event', 'booking_id': booking_id, 'event_id': event_id, 'version': booking.get('version')}],
                    'blocking': not lodging, 'lodging_anchor': lodging, 'location': endpoint, 'assumptions': []}
            for key in ('start_fold', 'end_fold', 'start_offset_minutes', 'end_offset_minutes'):
                if key in event: item[key] = event[key]
            item, errors = normalize_item(item)
            if errors: item['verification_status'] = 'provisional'
            span = bounds(item)
            lower = resolve_local(snapshot['start_date']+'T00:00', snapshot['timezone'])
            upper = resolve_local((date.fromisoformat(snapshot['end_date'])+timedelta(days=1)).isoformat()+'T00:00', snapshot['timezone'])
            if span:
                if span[1] <= utc(lower['instant']) or span[0] >= utc(upper['instant']):
                    continue
            elif start_day and (max(start_day, end_day) < snapshot['start_date'] or min(start_day, end_day) > snapshot['end_date']):
                continue
            if lodging:
                item['lodging_constraints'] = {'check_in_window': start, 'check_out_deadline': end,
                                               'luggage_storage': 'unknown', 'stay_is_not_busy': True}
            else:
                unresolved.extend(issue(error['code'], [item_id], state=error['state']) for error in errors)
            items.append(item)
    return sorted(items, key=lambda item: (item.get('start_instant') or '9999', item['item_id'])), unresolved


def endpoint(item, snapshot, departure=None):
    if item is None:
        from .origins import origin_for
        local=utc(departure).astimezone(ZoneInfo(snapshot['timezone'])) if departure else None
        value=origin_for(snapshot,local.date().isoformat() if local else snapshot['start_date'],local.strftime('%H:%M') if local else None)
        value.setdefault('id', 'origin'); value.setdefault('label', '출발점 미확인')
    else:
        value = deepcopy(item.get('location') or {})
        value.setdefault('id', item.get('place_id') or item['item_id'])
        value.setdefault('place_id', item.get('place_id'))
        value.setdefault('label', item.get('name', ''))
    value.setdefault('city', snapshot['city']); value.setdefault('timezone', snapshot['timezone'])
    value.setdefault('latitude', None); value.setdefault('longitude', None)
    value.setdefault('coordinate_permitted', False)
    value['accessibility_constraints']=sorted((snapshot.get('conditions') or {}).get('required',{}).get('accessibility',[]))
    return value


def _buffer(previous, following, snapshot):
    if previous and following and following.get('item_type') == 'rest' and following.get('parent_item_id') == previous['item_id']:
        return 0, []
    configured = snapshot.get('buffers') or {}
    minutes = configured.get('general_minutes', 15)
    unknown = []
    for item, side in ((previous, 'after'), (following, 'before')):
        if item is None or item.get('item_type') != 'booking':
            continue
        key = ('airport_' if item.get('booking_kind', '').casefold() in FLIGHTS else 'booking_')+side+'_minutes'
        number = configured.get(key, None if key.startswith('airport_') else 0)
        if number is None:
            unknown.append('UNKNOWN_AIRPORT_BUFFER')
        else:
            minutes += number
    return minutes, unknown


def travel(previous, following, departure, snapshot, route_lookup, now):
    """A planning reservation is separate from an unconfirmed travel duration."""
    source, destination = endpoint(previous, snapshot, departure), endpoint(following, snapshot, departure)
    response = ({'basis': 'unknown', 'duration_minutes': None, 'reason_codes': ['PLACE_UNAVAILABLE']}
                if source.get('unavailable') or destination.get('unavailable') else
                route_lookup(source, destination, utc(departure).isoformat(), snapshot.get('transport', 'walking')))
    leg = deepcopy(response)
    minutes = leg.get('duration_minutes')
    if minutes is not None and (type(minutes) not in (int, float) or not isfinite(minutes) or minutes < 0):
        leg.update(basis='unknown', duration_minutes=None, reason_codes=['INVALID_TRAVEL_DURATION'])
        minutes = None
    leg.setdefault('basis', 'unknown'); leg.setdefault('duration_minutes', None)
    leg.setdefault('distance_m', leg.get('distance_meters')); leg.setdefault('source_refs', []); leg.setdefault('reason_codes', [])
    buffer_minutes, unknown = _buffer(previous, following, snapshot)
    leg['reason_codes'] = list(dict.fromkeys(leg['reason_codes']+unknown))
    allowance = (snapshot.get('buffers') or {}).get('unknown_travel_allowance_minutes', 30) if minutes is None else None
    reserve = minutes if minutes is not None else allowance
    leg.update(from_item_id=previous['item_id'] if previous else None, to_item_id=following['item_id'],
               mode=snapshot.get('transport', 'walking'), departure_instant=utc(departure).isoformat(),
               from_endpoint=source, to_endpoint=destination, buffer_minutes=buffer_minutes,
               planning_allowance_minutes=allowance, reserved_minutes=reserve,
               start_instant=utc(departure).isoformat(),
               end_instant=(utc(departure)+timedelta(minutes=reserve)).isoformat() if reserve is not None else None)
    known = leg['basis'] == 'provider' and minutes is not None
    try:
        observed = utc(leg.get('observed_at') or now)
        known = known and utc(leg['checked_at']) <= observed and max(utc(now), observed) < utc(leg['expires_at'])
    except (KeyError, ValueError, TypeError):
        known = False
    leg['verification_status'] = 'verified' if known and not unknown else 'provisional'
    return leg


def rebuild_legs(items, snapshot, route_lookup, now):
    legs = []
    blocking = [item for item in items if item.get('blocking', True) and bounds(item)]
    windows = activity_windows(snapshot)
    for window in windows:
        day = window['date']
        local_items = [item for item in blocking if utc(item['start_instant']).astimezone(ZoneInfo(snapshot['timezone'])).date().isoformat() == day]
        local_items.sort(key=lambda item: (utc(item['start_instant']), item['item_id']))
        if not local_items:
            continue
        day_start, _ = time_interval(date.fromisoformat(day), [window['start'], window['end']], snapshot['timezone'])
        previous = max((item for item in blocking if utc(item['start_instant']).astimezone(ZoneInfo(snapshot['timezone'])).date().isoformat() < day
                        and utc(item['end_instant']).astimezone(ZoneInfo(snapshot['timezone'])).date().isoformat() == day),
                       key=lambda item: utc(item['end_instant']), default=None)
        for item in local_items:
            # A first booking stays authoritative even when outside sightseeing
            # hours. Do not invent an arrival from the daily activity start.
            departure = max(utc(previous['end_instant']), day_start) if previous else day_start
            if previous is None and item['item_type'] == 'booking' and utc(item['start_instant']) <= day_start:
                previous = item; continue
            legs.append(travel(previous, item, departure, snapshot, route_lookup, now))
            previous = item
    # A walking-limit condition is measured from that day's origin, independently
    # of the actual previous appointment. Constraint-only legs never reserve time.
    if (snapshot.get('conditions',{}).get('distance_filter') or {}).get('kind')=='walking':
        for item in blocking:
            if item.get('item_type')=='place':
                legs.append(distance_leg(item,snapshot,route_lookup,now))
    return legs


def distance_leg(item,snapshot,route_lookup,now):
    local=utc(item['start_instant']).astimezone(ZoneInfo(snapshot['timezone']))
    source=endpoint(None,snapshot,item['start_instant']);destination=endpoint(item,snapshot,item['start_instant'])
    response=route_lookup(source,destination,utc(item['start_instant']).isoformat(),'walking')
    return {**deepcopy(response),'filter_only':True,'from_item_id':None,'to_item_id':item['item_id'],'from_endpoint':source,'to_endpoint':destination,
        'mode':'walking','departure_instant':item['start_instant'],'constraint':'origin_walking_limit'}


def _place_item(candidate, selection, start, end, snapshot):
    zone = snapshot['timezone']
    place_id = candidate['place_id']
    ident = selection.get('item_id') or snapshot.get('item_ids', {}).get(place_id) or stable_id(snapshot.get('trip_id'), 'place', place_id)
    return {'item_id': ident, 'item_type': 'place', 'place_id': place_id, 'booking_id': None, 'booking_event_id': None,
            'synthetic': bool(candidate.get('synthetic')),
            'name': candidate.get('name'), 'native_name': candidate.get('native_name'), 'category': candidate.get('category'),
            'city': candidate.get('city'), 'neighborhood': candidate.get('neighborhood'),
            'local_start': local_iso(start, zone), 'local_end': local_iso(end, zone), 'start_timezone': zone, 'end_timezone': zone,
            'start_instant': utc(start).isoformat(), 'end_instant': utc(end).isoformat(),
            'start_fold': utc(start).astimezone(ZoneInfo(zone)).fold, 'end_fold': utc(end).astimezone(ZoneInfo(zone)).fold,
            'duration_minutes': (utc(end)-utc(start)).total_seconds()/60, 'duration_origin': selection.get('duration_origin', 'planning_default'),
            'locked': False, 'lock_origin': None, 'reservation_status': 'not_booked', 'verification_status': 'provisional',
            'source_refs': [], 'blocking': True,
            'location': {'id': place_id, 'place_id': place_id, 'label': candidate.get('name'), 'city': candidate.get('city'),
                         'latitude': candidate.get('latitude'), 'longitude': candidate.get('longitude'),
                         'coordinate_permitted': candidate.get('coordinate_permitted', candidate.get('pack_status') == 'approved')},
            'assumptions': [] if selection.get('duration_origin') == 'user' else ['PLANNING_DEFAULT_DURATION']}


def _unplaced(place_id, reasons, checks=()):
    return {'place_id': place_id, 'reason_codes': sorted(set(reasons or ['NO_TIME_WINDOW'])),
            'missing_facts': sorted({item.get('constraint') for item in checks if item.get('state') == 'unknown' and item.get('constraint')}),
            'reconsider_conditions': ['change_date_or_time', 'shorter_visit', 'verify_missing_information']}


def _rest_item(parent, duration, snapshot):
    start = utc(parent['end_instant']); end = start+timedelta(minutes=duration); zone = snapshot['timezone']
    return {'item_id': stable_id(parent['item_id'], 'rest'), 'item_type': 'rest', 'parent_item_id': parent['item_id'],
            'place_id': None, 'booking_id': None, 'booking_event_id': None, 'name': '휴식', 'native_name': None,
            'synthetic': bool(parent.get('synthetic')), 'local_start': local_iso(start, zone), 'local_end': local_iso(end, zone),
            'start_timezone': zone, 'end_timezone': zone, 'start_instant': start.isoformat(), 'end_instant': end.isoformat(),
            'start_fold': start.astimezone(ZoneInfo(zone)).fold, 'end_fold': end.astimezone(ZoneInfo(zone)).fold,
            'duration_minutes': duration, 'duration_origin': 'planning_preference', 'locked': False, 'lock_origin': None,
            'reservation_status': 'not_applicable', 'verification_status': 'verified', 'blocking': True,
            'location': deepcopy(parent['location']), 'source_refs': [], 'assumptions': ['PLANNED_REST']}


def generate(snapshot, bookings, candidates, route_lookup, now):
    snapshot = deepcopy(snapshot)
    default_activity = not snapshot.get('activity_start') and not snapshot.get('activity_windows')
    snapshot['activity_windows'] = activity_windows(snapshot)
    current = utc(now)
    selected = list(snapshot.get('selected', []))
    if len(selected) > 30:
        raise ValueError('At most 30 selected places')
    catalog = {candidate['place_id']: candidate for candidate in candidates}
    items, booking_unknown = booking_items(bookings, snapshot)
    for locked in snapshot.get('locked_items', []):
        if locked.get('lock_origin') != 'booking':
            value, _ = normalize_item(deepcopy(locked)); value['locked'] = True; value['lock_origin'] = 'user'; items.append(value)
    unplaced, attempts = [], 0
    max_attempts = min(1000, snapshot.get('max_evaluations', 500))
    seen = {item.get('place_id') for item in items if item.get('place_id')}
    assumptions = deepcopy(snapshot.get('assumptions', []))
    if default_activity:
        assumptions.append({'code': 'DEFAULT_ACTIVITY_WINDOW', 'origin': 'planning_default'})
    density = snapshot.get('density', (snapshot.get('conditions') or {}).get('density', 'balanced'))
    density_limit = {'relaxed': 2, 'balanced': 3, 'packed': 5}.get(density, 3)
    assumptions.append({'code': 'DAILY_VISIT_TARGET', 'origin': 'planning_preference', 'density': density, 'count': density_limit})
    rest_preferences = snapshot.get('rest_preferences') or {}
    rest_duration = rest_preferences.get('duration_minutes', rest_preferences.get('minutes', 0))
    rest_after = rest_preferences.get('after_visits', 2)
    meal_time = snapshot.get('meal_time') or (snapshot.get('conditions') or {}).get('meal_time')
    if meal_time and not snapshot.get('meal_windows'):
        proposed = {'tokyo': {'breakfast': ('08:00', '10:00'), 'lunch': ('12:00', '14:00'), 'dinner': ('18:00', '21:00')},
                    'barcelona': {'breakfast': ('09:00', '11:00'), 'lunch': ('13:00', '16:00'), 'dinner': ('20:00', '23:00')}}
        if meal_time in proposed.get(snapshot['city'], {}):
            start, end = proposed[snapshot['city']][meal_time]
            snapshot['meal_windows'] = [{'meal': meal_time, 'start': start, 'end': end}]
            assumptions.append({'code': 'DEFAULT_MEAL_WINDOW', 'origin': 'planning_default', 'meal': meal_time, 'start': start, 'end': end})
    for selection in sorted(selected, key=lambda value: (value.get('priority', 0), value['place_id'])):
        ident = selection['place_id']
        if ident in seen:
            unplaced.append(_unplaced(ident, ['DUPLICATE_PLACE'])); continue
        seen.add(ident)
        candidate = catalog.get(ident)
        if candidate is None:
            unplaced.append(_unplaced(ident, ['PLACE_UNAVAILABLE'])); continue
        duration = selection.get('duration_minutes')
        if duration is None:
            duration = 90 if candidate.get('category') in {'restaurant', 'attraction'} else 60
            selection = {**selection, 'duration_origin': 'planning_default'}
        if type(duration) not in (float, int) or not 1 <= duration <= 720:
            unplaced.append(_unplaced(ident, ['INVALID_DURATION'])); continue
        options, failed_codes, failed_checks = [], [], []
        for window in snapshot['activity_windows']:
            day = date.fromisoformat(window['date'])
            day_span = time_interval(day, [window['start'], window['end']], snapshot['timezone'])
            day_visits = [item for item in items if item.get('item_type') == 'place' and str(item.get('local_start', ''))[:10] == day.isoformat()]
            if len(day_visits) >= density_limit:
                failed_codes.append('DENSITY_LIMIT_REACHED'); continue
            if any(item.get('blocking', True) and bounds(item) is None and str(item.get('local_start') or '')[:10] == day.isoformat() for item in items):
                failed_codes.append('UNRESOLVED_FIXED_BOOKING'); continue
            busy = [item for item in items if item.get('blocking', True) and bounds(item)]
            busy.sort(key=lambda item: (utc(item['start_instant']), item['item_id']))
            hours = opening_windows(candidate, day, snapshot['timezone'], current)
            if hours['state'] == 'violated':
                failed_codes.extend(hours['reason_codes']); continue
            if hours['state'] == 'unknown' and not snapshot.get('allow_provisional', False):
                failed_codes.extend(hours['reason_codes'] or ['UNKNOWN_OPENING_HOURS']); continue
            open_windows = hours['intervals'] or hours.get('opening_intervals') or [day_span]
            if candidate.get('category') == 'restaurant' and snapshot.get('meal_windows'):
                preferred_windows = [time_interval(day, [m['start'], m['end']], snapshot['timezone']) for m in snapshot['meal_windows']]
                open_windows = open_windows+[(max(a, c), min(b, d)) for a, b in open_windows for c, d in preferred_windows if max(a, c) < min(b, d)]
            for free in subtract(day_span, [bounds(item) for item in busy]):
                previous = max((item for item in busy if utc(item['end_instant']) <= free[0]), key=lambda item: item['end_instant'], default=None)
                following = min((item for item in busy if utc(item['start_instant']) >= free[1]), key=lambda item: item['start_instant'], default=None)
                # Previous/next days do not become a spurious overnight route.
                if previous and utc(previous['end_instant']).astimezone(ZoneInfo(snapshot['timezone'])).date() < day:
                    previous = None
                if following and utc(following['start_instant']).astimezone(ZoneInfo(snapshot['timezone'])).date() > day:
                    following = None
                for opening in open_windows:
                    if attempts >= max_attempts:
                        failed_codes.append('SEARCH_LIMIT_REACHED'); break
                    attempts += 1
                    seed = max(free[0], opening[0])
                    item = _place_item(candidate, selection, seed, seed+timedelta(minutes=duration), snapshot)
                    incoming = travel(previous, item, free[0], snapshot, route_lookup, current)
                    if incoming['verification_status'] != 'verified' and not snapshot.get('allow_provisional', False):
                        failed_codes.extend(incoming['reason_codes'] or ['UNKNOWN_TRAVEL']); continue
                    reserve = incoming.get('reserved_minutes')
                    if reserve is None:
                        failed_codes.append('UNKNOWN_TRAVEL'); continue
                    start = max(seed, free[0]+timedelta(minutes=reserve+incoming['buffer_minutes']))
                    end = start+timedelta(minutes=duration)
                    if end > min(free[1], opening[1]):
                        failed_codes.append('NO_TIME_WINDOW'); continue
                    item = _place_item(candidate, selection, start, end, snapshot)
                    planned_rest = _rest_item(item, rest_duration, snapshot) if rest_duration and (len(day_visits)+1) % rest_after == 0 else None
                    reserved_end = utc(planned_rest['end_instant']) if planned_rest else end
                    if reserved_end > min(free[1], opening[1]):
                        if rest_preferences.get('required'):
                            failed_codes.append('NO_REST_WINDOW'); continue
                        planned_rest, reserved_end = None, end
                    outgoing = travel(planned_rest or item, following, reserved_end, snapshot, route_lookup, current) if following else None
                    if outgoing:
                        if outgoing['verification_status'] != 'verified' and not snapshot.get('allow_provisional', False):
                            failed_codes.extend(outgoing['reason_codes'] or ['UNKNOWN_TRAVEL']); continue
                        if outgoing.get('reserved_minutes') is None or reserved_end+timedelta(minutes=outgoing['reserved_minutes']+outgoing['buffer_minutes']) > utc(following['start_instant']):
                            if planned_rest and not rest_preferences.get('required'):
                                planned_rest, reserved_end = None, end
                                outgoing = travel(item, following, end, snapshot, route_lookup, current)
                            if outgoing.get('reserved_minutes') is None or reserved_end+timedelta(minutes=outgoing['reserved_minutes']+outgoing['buffer_minutes']) > utc(following['start_instant']):
                                failed_codes.append('NO_TIME_WINDOW'); continue
                    distance_evidence=distance_leg(item,snapshot,route_lookup,current) if (snapshot.get('conditions',{}).get('distance_filter') or {}).get('kind')=='walking' else None
                    checks = assess_visit(item, candidate, snapshot, current, distance_evidence)
                    if any(check['state'] == 'violated' or check['state'] == 'unknown' and not snapshot.get('allow_provisional', False) for check in checks):
                        failed_codes.extend(check['code'] for check in checks if check['state'] != 'satisfied'); failed_checks.extend(checks); continue
                    if 'ROUTE_IMPOSSIBLE' in incoming['reason_codes'] or outgoing and 'ROUTE_IMPOSSIBLE' in outgoing['reason_codes']:
                        failed_codes.append('ROUTE_IMPOSSIBLE'); continue
                    item['verification_status'] = 'provisional' if any(check['state'] == 'unknown' for check in checks) or incoming['verification_status'] != 'verified' or outgoing and outgoing['verification_status'] != 'verified' else 'verified'
                    item['source_refs'] = sorted({ref for check in checks for ref in check.get('source_refs', [])})
                    item['unresolved_conditions'] = [check for check in checks if check['state'] == 'unknown']
                    added_travel = incoming['reserved_minutes']+(outgoing['reserved_minutes'] if outgoing else 0)
                    meal_penalty = 0
                    if candidate.get('category') == 'restaurant' and snapshot.get('meal_windows'):
                        meal_penalty = 0 if any(contains(time_interval(day, [m['start'], m['end']], snapshot['timezone']), (start, end)) for m in snapshot['meal_windows']) else 1
                    neighborhood_return = int(bool(previous and following and previous.get('neighborhood') == following.get('neighborhood') and candidate.get('neighborhood') != previous.get('neighborhood')))
                    cost = (added_travel, meal_penalty, neighborhood_return, len(day_visits), start, ident)
                    options.append((cost, item, planned_rest))
        if options:
            _, best, planned_rest = min(options, key=lambda value: value[0]); items.append(best)
            if planned_rest: items.append(planned_rest)
        else:
            unplaced.append(_unplaced(ident, failed_codes, failed_checks))
    items.sort(key=lambda item: (item.get('start_instant') or '9999', item['item_id']))
    legs = rebuild_legs(items, snapshot, route_lookup, current)
    validation = validate(items, candidates, snapshot, legs, current, bookings=bookings)
    # Changing adjacent visits changes departure-dependent routes. The final
    # validator can reject a formerly plausible insertion; never publish it as
    # successful just because a local gap check passed earlier.
    for _ in range(len(selected)):
        invalid = validation['conflicts'] + ([] if snapshot.get('allow_provisional') else validation['unresolved_conditions'])
        implicated = {ident for problem in invalid for ident in problem['item_ids']}
        unsafe = [item for item in items if item.get('item_type') == 'place' and not item.get('locked') and item['item_id'] in implicated]
        if not unsafe:
            break
        removed = unsafe[-1]
        codes = [problem['code'] for problem in invalid if removed['item_id'] in problem['item_ids']]
        unplaced.append(_unplaced(removed['place_id'], codes, invalid))
        items = [item for item in items if item['item_id'] != removed['item_id'] and item.get('parent_item_id') != removed['item_id']]
        legs = rebuild_legs(items, snapshot, route_lookup, current)
        validation = validate(items, candidates, snapshot, legs, current, bookings=bookings)
    # Preserve unknown times as facts; no midnight or default reservation duration.
    existing = {(value['code'], tuple(value['item_ids'])) for value in validation['unresolved_conditions']}
    validation['unresolved_conditions'].extend(value for value in booking_unknown if (value['code'], tuple(value['item_ids'])) not in existing)
    if validation['unresolved_conditions'] and not validation['conflicts']:
        validation['validation_status'] = 'provisional'
    for item in items:
        related = [check for check in validation['checks'] if item['item_id'] in check['item_ids']]
        item['verification_status'] = 'conflicted' if any(check['state'] == 'violated' for check in related) else 'provisional' if any(check['state'] == 'unknown' for check in related) else item.get('verification_status', 'verified')
        item['unresolved_conditions'] = [check for check in related if check['state'] == 'unknown']
    return {'items': items, 'legs': legs, **validation, 'unplaced': unplaced, 'assumptions': assumptions,
            'engine_version': ENGINE_VERSION, 'cost_function_version': 'insertion-cost-v1',
            'search': {'evaluated_positions': attempts, 'max_evaluations': max_attempts, 'limit_reached': attempts >= max_attempts}}
