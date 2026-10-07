"""Shared tri-state itinerary validator used by generation, preview and undo."""
from datetime import date, datetime, timedelta
from math import isfinite
from zoneinfo import ZoneInfo

from src.recommendations.engine import Facts, _price, movement
from .intervals import bounds, contains, normalize_item, overlaps, subtract, time_interval, utc

DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def issue(code, item_ids=(), *, state='unknown', constraint=None, interval=None, source_refs=()):
    return {'code': code, 'state': state, 'item_ids': list(item_ids), 'interval': interval,
            'constraint': constraint or code.lower(), 'reason': code, 'source_refs': list(source_refs),
            'possible_actions': ['correct_booking'] if code in {'CONFLICTING_LOCKS', 'BOOKING_CHANGED', 'BOOKING_REMOVED', 'BOOKING_TIME_CONFLICT'} else ['change_time', 'remove_optional_item', 'verify_information']}


def _fact_rows(facts, field):
    rows = [row for row in facts.rows if row.get('field') == field and facts.permitted(row)]
    if any(row.get('status') == 'conflict' for row in rows):
        return None, [], 'unknown'
    rows = [row for row in rows if row.get('status') in {'verified', 'provisional'} and row.get('value') is not None]
    if not rows:
        return None, [], 'unknown'
    verified = [row for row in rows if row['status'] == 'verified']
    selected = verified or rows
    if any(row['value'] != selected[0]['value'] for row in selected[1:]):
        return None, selected, 'unknown'
    return selected[0]['value'], selected, 'satisfied' if verified else 'unknown'


def _dated(rows, day):
    return any(row.get('valid_for_date') == day.isoformat() or
               row.get('valid_from') and row.get('valid_until') and row['valid_from'] <= day.isoformat() <= row['valid_until'] for row in rows)


def opening_windows(candidate, day, zone, now):
    """Intervals may be tentative; certainty is returned independently of geometry."""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    facts = Facts(candidate, {'date': day.isoformat(), 'timezone': zone}, utc(now))
    hours, rows, state = _fact_rows(facts, 'opening_hours')
    opening_state = state
    result = {'intervals': [], 'opening_intervals': [], 'opening_state': 'unknown', 'state': 'unknown',
              'reason_codes': [], 'source_refs': facts.refs(rows), 'cutoffs': []}
    closed, closed_rows, _ = facts.get('closed')
    if closed is True:
        return {**result, 'state': 'violated', 'reason_codes': ['PLACE_CLOSED'], 'source_refs': facts.refs(closed_rows)}
    if any(row.get('field') == 'closed' and row.get('status') == 'provisional' and row.get('value') is not None and facts.permitted(row) for row in facts.rows):
        state = 'unknown'; result['reason_codes'].append('BUSINESS_STATUS_UNCONFIRMED')
    closure, closure_rows, closure_state = _fact_rows(facts, 'exceptional_closures')
    if closure is not None:
        try:
            dates = closure.get('dates', []) if isinstance(closure, dict) else closure
            ranges = closure.get('ranges', []) if isinstance(closure, dict) else []
            is_closed = day.isoformat() in dates or any(r['start_date'] <= day.isoformat() <= r['end_date'] for r in ranges)
            if is_closed and closure_state == 'satisfied':
                return {**result, 'state': 'violated', 'reason_codes': ['EXCEPTIONAL_CLOSURE'], 'source_refs': facts.refs(closure_rows)}
            if is_closed:
                result['reason_codes'].append('PROVISIONAL_CLOSURE')
                state = 'unknown'
        except (KeyError, TypeError, ValueError):
            result['reason_codes'].append('UNKNOWN_CLOSURE_RULE'); state = 'unknown'
    if not isinstance(hours, dict) or hours.get('timezone') != zone or not isinstance(hours.get('weekly'), dict):
        return {**result, 'reason_codes': result['reason_codes'] + ['UNKNOWN_OPENING_HOURS']}
    exact_day = _dated(rows, day)
    if not exact_day and day != utc(now).astimezone(ZoneInfo(zone)).date():
        state = opening_state = 'unknown'
        result['reason_codes'].append('FUTURE_OPENING_UNCONFIRMED')
    overrides = {}
    for exception in hours.get('exceptions', []):
        if not isinstance(exception, dict) or not exception.get('date'):
            result['reason_codes'].append('UNKNOWN_CLOSURE_RULE'); state = 'unknown'; continue
        if exception.get('date') in {day.isoformat(), (day-timedelta(days=1)).isoformat()}:
            if exception.get('closed') is True:
                if exception['date'] == day.isoformat() and all(r['status'] == 'verified' for r in rows):
                    return {**result, 'state': 'violated', 'reason_codes': ['EXCEPTIONAL_CLOSURE']}
                overrides[exception['date']] = []
            elif isinstance(exception.get('intervals'), list):
                overrides[exception['date']] = exception['intervals']
                if exception['date'] == day.isoformat() and all(r['status'] == 'verified' for r in rows):
                    state = opening_state = 'satisfied'
            else:
                result['reason_codes'].append('UNKNOWN_CLOSURE_RULE'); state = 'unknown'
    intervals, cutoffs = [], []
    today_known = False
    try:
        for offset in (-1, 0):
            base = day+timedelta(days=offset)
            raw = overrides.get(base.isoformat(), hours['weekly'].get(DAYS[base.weekday()], hours['weekly'].get(str(base.weekday()))))
            if offset == 0:
                today_known = raw is not None
            if raw is None:
                continue
            for entry in raw:
                interval = time_interval(base, entry, zone)
                if interval[1].astimezone(ZoneInfo(zone)).date() < day:
                    continue
                intervals.append(interval)
                if isinstance(entry, dict):
                    for field in ('last_order', 'last_entry'):
                        if entry.get(field):
                            from .intervals import resolve_local
                            cut = resolve_local(base.isoformat()+'T'+entry[field], zone)
                            if cut['state'] != 'satisfied':
                                raise ValueError('Unresolved cutoff')
                            instant = utc(cut['instant'])
                            if instant < interval[0]:
                                cut = resolve_local((base+timedelta(days=1)).isoformat()+'T'+entry[field], zone)
                                if cut['state'] != 'satisfied':
                                    raise ValueError('Unresolved overnight cutoff')
                                instant = utc(cut['instant'])
                            cutoffs.append({'field': field, 'instant': instant, 'interval': interval, 'state': opening_state})
        if not today_known and not intervals:
            state = opening_state = 'unknown'; result['reason_codes'].append('UNKNOWN_WEEKDAY_HOURS')
    except (TypeError, ValueError, KeyError):
        return {**result, 'state': 'unknown', 'reason_codes': result['reason_codes'] + ['INVALID_OPENING_HOURS']}
    # Preserve the certainty and geometry of the opening fact before combining
    # it with independently uncertain break/closure facts. A tentative break
    # cannot weaken a confirmed admission cutoff or closing time.
    result.update(opening_intervals=sorted(intervals), opening_state=opening_state)
    breaks, break_rows, break_state = _fact_rows(facts, 'break_times')
    if breaks is not None:
        try:
            blocked = []
            weekly = breaks.get('weekly', breaks) if isinstance(breaks, dict) else None
            for offset in (-1, 0):
                base = day+timedelta(days=offset)
                raw = weekly.get(DAYS[base.weekday()], weekly.get(str(base.weekday()), [])) if weekly is not None else breaks if offset == 0 else []
                if not isinstance(raw, list):
                    raise ValueError('Invalid break intervals')
                blocked.extend(time_interval(base, entry, zone) for entry in raw)
            intervals = [fragment for opening in intervals for fragment in subtract(opening, blocked)]
            result['source_refs'] = sorted(set(result['source_refs']+facts.refs(break_rows)))
            if break_state != 'satisfied' or not _dated(break_rows, day) and day != utc(now).astimezone(ZoneInfo(zone)).date():
                state = 'unknown'; result['reason_codes'].append('BREAK_TIMES_UNCONFIRMED')
        except (KeyError, ValueError, TypeError):
            state = 'unknown'; result['reason_codes'].append('UNKNOWN_BREAK_TIMES')
    if any(code in result['reason_codes'] for code in ('BUSINESS_STATUS_UNCONFIRMED', 'PROVISIONAL_CLOSURE', 'UNKNOWN_CLOSURE_RULE')):
        state = 'unknown'
    result.update(intervals=sorted(intervals), cutoffs=cutoffs, state=state)
    if state == 'unknown' and not result['reason_codes']:
        result['reason_codes'].append('UNKNOWN_OPENING_HOURS')
    return result


def assess_visit(item, candidate, snapshot, now, distance_evidence=None):
    result = []
    ident = item['item_id']
    span = bounds(item)
    if not span:
        return [issue('MISSING_REQUIRED_TIME', [ident])]
    conditions = snapshot.get('conditions', {})
    party = snapshot.get('party', conditions.get('party', {'adults': 1, 'children': []}))
    zone = item['start_timezone']
    day = span[0].astimezone(ZoneInfo(zone)).date()
    visit = {'date': day.isoformat(), 'local_time': span[0].astimezone(ZoneInfo(zone)).strftime('%H:%M:%S'), 'timezone': zone}
    facts = Facts(candidate, visit, utc(now))

    def add(code, state='unknown', refs=(), field=None):
        result.append(issue(code, [ident], state=state, constraint=field, source_refs=refs))

    if candidate.get('identity_status') != 'verified' or candidate.get('pack_status') != 'approved':
        add('PLACE_UNAVAILABLE', 'violated')
    if candidate.get('city') != snapshot['city']:
        add('CITY_MISMATCH', 'violated')
    if candidate.get('excluded'):
        add('USER_EXCLUDED', 'violated')
    if not facts.sources:
        add('SOURCE_POLICY_UNAVAILABLE', 'violated')
    from .origins import origin_for
    from src.recommendations.engine import straight_line_distance
    selected_origin=origin_for(snapshot,day.isoformat(),span[0].astimezone(ZoneInfo(zone)).strftime('%H:%M'))
    distance_filter=conditions.get('distance_filter') or {}
    maximum=distance_filter.get('max_distance_m') if distance_filter.get('kind')=='straight_line' else conditions.get('radius_m')
    if maximum is not None:
        distance=straight_line_distance(selected_origin,candidate) if candidate.get('coordinate_permitted') is True else None
        if distance is None:add('UNKNOWN_REQUIRED_DISTANCE',field='distance_filter')
        elif distance>maximum:add('MAXIMUM_DISTANCE_EXCEEDED','violated',field='distance_filter')
        else:add('DISTANCE_MATCH','satisfied',field='distance_filter')
    if distance_filter.get('kind')=='walking':
        route=distance_evidence or {};duration=route.get('duration_minutes')
        known=route.get('basis')=='provider' and route.get('mode')=='walking' and type(duration) in (int,float) and isfinite(duration) and duration>=0
        try:known=known and utc(route['checked_at'])<=utc(route.get('observed_at') or now) and max(utc(now),utc(route.get('observed_at') or now))<utc(route['expires_at'])
        except (KeyError,TypeError,ValueError):known=False
        if conditions.get('required',{}).get('accessibility') and route.get('accessibility_status')!='satisfied':known=False
        if not known:add('UNKNOWN_REQUIRED_WALKING_TIME',field='distance_filter')
        elif duration>distance_filter['max_duration_minutes']:add('MAXIMUM_WALKING_TIME_EXCEEDED','violated',field='distance_filter')
        else:add('WALKING_TIME_MATCH','satisfied',field='distance_filter')
    count = party['adults']+len(party.get('children', []))
    for field, compare in (('min_party', lambda value: count >= value), ('max_party', lambda value: count <= value)):
        value, rows, _ = facts.get(field)
        if type(value) is not int or value < 1:
            add('MISSING_REQUIRED_FACT', field=field)
        else:
            add('PARTY_MATCH' if compare(value) else 'PARTY_MISMATCH', 'satisfied' if compare(value) else 'violated', facts.refs(rows), field)
    if party.get('children_status')=='unknown' and not party.get('children'):
        rule,rows,_=facts.get('children_rule')
        if isinstance(rule,dict) and (rule.get('allowed') is False or type(rule.get('minimum_age')) is int and rule['minimum_age']>0):
            add('CHILD_PARTY_UNKNOWN',refs=facts.refs(rows),field='children')
    if party.get('children'):
        rule, rows, _ = facts.get('children_rule')
        if not isinstance(rule, dict) or type(rule.get('allowed')) is not bool:
            add('UNKNOWN_CHILD_RULE')
        elif rule['allowed'] is False:
            add('CHILDREN_NOT_ALLOWED', 'violated', facts.refs(rows))
        elif any(child.get('age') is None for child in party['children']):
            add('UNKNOWN_CHILD_AGE')
        elif type(rule.get('minimum_age')) is not int:
            add('UNKNOWN_CHILD_RULE')
        elif any(child['age'] < rule['minimum_age'] for child in party['children']):
            add('CHILD_AGE_MISMATCH', 'violated', facts.refs(rows))
        else:
            add('CHILDREN_MATCH', 'satisfied', facts.refs(rows))
    for field in ('dietary', 'accessibility'):
        required = conditions.get('required', snapshot.get('required', {})).get(field, [])
        value, rows, _ = facts.get(field)
        for name in required:
            known = value.get(name) if isinstance(value, dict) else None
            add(field.upper()+('_MATCH' if known is True else '_MISMATCH' if known is False else '_UNKNOWN'),
                'satisfied' if known is True else 'violated' if known is False else 'unknown', facts.refs(rows), name)
    if conditions.get('budget'):
        _, state, code, refs, _ = _price(facts, {**conditions, 'party': party})
        add(code, {'confirmed': 'satisfied', 'failed': 'violated'}.get(state, state), refs, 'budget')
    hours = opening_windows(candidate, day, zone, now)
    result.extend(issue(code, [ident], state=hours['state'], source_refs=hours['source_refs']) for code in hours['reason_codes'])
    if hours['state'] == 'violated':
        return result
    matching = [window for window in hours['intervals'] if contains(window, span)]
    opening_matches = [window for window in hours['opening_intervals'] if contains(window, span)]
    if not matching:
        known_violation = hours['state'] == 'satisfied' or hours['opening_state'] == 'satisfied' and not opening_matches
        add('OUTSIDE_OPENING_HOURS' if known_violation else 'UNKNOWN_OPENING_HOURS',
            'violated' if known_violation else 'unknown', hours['source_refs'])
    else:
        add('OPENING_MATCH', hours['state'], hours['source_refs'])
    for cutoff in hours['cutoffs']:
        if contains(cutoff['interval'], span) and span[0] >= cutoff['instant']:
            add('AFTER_'+cutoff['field'].upper(), 'violated' if cutoff['state'] == 'satisfied' else 'unknown', hours['source_refs'])
    for field in ('break_times', 'last_order', 'last_entry'):
        value, rows, state = _fact_rows(facts, field)
        if value is None:
            continue
        refs = facts.refs(rows)
        if not _dated(rows, day) and day != utc(now).astimezone(ZoneInfo(zone)).date():
            state = 'unknown'
        try:
            if field == 'break_times':
                weekly = value.get('weekly', value) if isinstance(value, dict) else None
                for offset in (-1, 0):
                    base = day+timedelta(days=offset)
                    intervals = weekly.get(DAYS[base.weekday()], weekly.get(str(base.weekday()), [])) if weekly is not None else value if offset == 0 else []
                    for entry in intervals:
                        if overlaps(span, time_interval(base, entry, zone)):
                            add('BREAK_TIME_OVERLAP', 'violated' if state == 'satisfied' else 'unknown', refs)
            else:
                from .intervals import resolve_local
                clock = value.get('time') if isinstance(value, dict) else value
                offset = value.get('day_offset', 0) if isinstance(value, dict) else 0
                cutoff = resolve_local((day+timedelta(days=offset)).isoformat()+'T'+clock, zone)
                if cutoff['state'] != 'satisfied':
                    raise ValueError('Unresolved cutoff')
                if span[0] >= utc(cutoff['instant']):
                    add('AFTER_'+field.upper(), 'violated' if state == 'satisfied' else 'unknown', refs)
        except (TypeError, KeyError, ValueError):
            add('UNKNOWN_'+field.upper(), refs=refs)
    return result


def validate(items, candidates, snapshot, legs, now, bookings=None):
    """Re-evaluate current facts. No mutation or implicit correction of locked items."""
    checks, normalized = [], []
    catalog = {p['place_id']: p for p in candidates}
    ids, places = set(), set()
    zone = snapshot['timezone']
    for item in items:
        ident = item['item_id']
        if ident in ids:
            checks.append(issue('DUPLICATE_ITEM', [ident], state='violated'))
        ids.add(ident)
        fresh, time_checks = normalize_item(item)
        normalized.append(fresh)
        for problem in time_checks:
            if item.get('blocking', True):
                checks.append(issue(problem['code'], [ident], state=problem['state']))
            elif item.get('lodging_anchor'):
                checks.append(issue('LODGING_TIME_UNCONFIRMED', [ident]))
        span = bounds(fresh)
        if item.get('item_type') != 'booking' and span:
            if item.get('start_timezone') != zone or item.get('end_timezone') != zone:
                checks.append(issue('CITY_TIMEZONE_MISMATCH', [ident], state='violated'))
            start_date = span[0].astimezone(ZoneInfo(zone)).date().isoformat()
            end_date = (span[1]-timedelta(microseconds=1)).astimezone(ZoneInfo(zone)).date().isoformat()
            if not snapshot['start_date'] <= start_date <= end_date <= snapshot['end_date']:
                checks.append(issue('OUTSIDE_TRIP_WINDOW', [ident], state='violated'))
            windows = [w for w in snapshot.get('activity_windows', []) if w['date'] == start_date]
            if windows:
                try:
                    if not any(contains(time_interval(date.fromisoformat(w['date']), [w['start'], w['end']], zone), span) for w in windows):
                        checks.append(issue('OUTSIDE_ACTIVITY_WINDOW', [ident], state='violated'))
                except (ValueError, TypeError):
                    checks.append(issue('INVALID_ACTIVITY_WINDOW', [ident], state='violated'))
        if item.get('place_id'):
            if item['place_id'] in places:
                checks.append(issue('DUPLICATE_PLACE', [ident], state='violated'))
            places.add(item['place_id'])
            if item['place_id'] not in catalog:
                checks.append(issue('PLACE_UNAVAILABLE', [ident], state='violated'))
            else:
                checks.extend(assess_visit(fresh, catalog[item['place_id']], snapshot, now,next((leg for leg in legs if leg.get('filter_only') and leg.get('to_item_id')==ident),None)))
        if item.get('item_type') == 'booking' and item.get('reservation_status') not in {'source_verified', 'user_confirmed'}:
            checks.append(issue('BOOKING_NEEDS_REVIEW', [ident]))
    busy = [item for item in normalized if item.get('blocking', True) and bounds(item)]
    busy.sort(key=lambda item: (item['start_instant'], item['item_id']))
    for n, left in enumerate(busy):
        for right in busy[n+1:]:
            if overlaps(bounds(left), bounds(right)):
                checks.append(issue('CONFLICTING_LOCKS' if left.get('locked') and right.get('locked') else 'TIME_OVERLAP',
                                    [left['item_id'], right['item_id']], state='violated'))
    by_id = {item['item_id']: item for item in normalized}
    known_pairs = set()
    for leg in legs:
        if leg.get('filter_only'):continue
        origin_id, destination_id = leg.get('from_item_id'), leg.get('to_item_id')
        known_pairs.add((origin_id, destination_id))
        related = [value for value in (origin_id, destination_id) if value in by_id]
        duration, basis = leg.get('duration_minutes'), leg.get('basis')
        if 'ROUTE_IMPOSSIBLE' in leg.get('reason_codes', []):
            checks.append(issue('ROUTE_IMPOSSIBLE', related, state='violated')); continue
        if duration is not None and (type(duration) not in (float, int) or not isfinite(duration) or duration < 0):
            checks.append(issue('INVALID_TRAVEL_DURATION', related, state='violated')); continue
        state = 'satisfied'
        try:
            if basis != 'provider' or duration is None:
                state = 'unknown'
            elif not (utc(leg['checked_at']) <= utc(leg.get('observed_at') or now) and max(utc(now), utc(leg.get('observed_at') or now)) < utc(leg['expires_at'])):
                state = 'unknown'
        except (KeyError, ValueError, TypeError):
            state = 'unknown'
        if state == 'unknown':
            checks.append(issue('ESTIMATED_TRAVEL' if basis == 'estimate' else 'UNKNOWN_TRAVEL', related))
        else:
            checks.append(issue('TRAVEL_CONFIRMED', related, state='satisfied'))
        if 'UNKNOWN_AIRPORT_BUFFER' in leg.get('reason_codes', []):
            checks.append(issue('UNKNOWN_AIRPORT_BUFFER', related))
        previous, following = by_id.get(origin_id), by_id.get(destination_id)
        if following and following.get('start_instant'):
            departure = previous.get('end_instant') if previous else leg.get('departure_instant')
            reserve = duration if duration is not None else leg.get('planning_allowance_minutes')
            if departure and type(reserve) in (int, float):
                required = reserve + leg.get('buffer_minutes', 0)
                if utc(departure)+timedelta(minutes=required) > utc(following['start_instant']):
                    checks.append(issue('INSUFFICIENT_TRAVEL_TIME', related, state='violated'))
    for left, right in zip(busy, busy[1:]):
        if utc(left['end_instant']).astimezone(ZoneInfo(zone)).date() == utc(right['start_instant']).astimezone(ZoneInfo(zone)).date() and (left['item_id'], right['item_id']) not in known_pairs:
            checks.append(issue('MISSING_TRAVEL_LEG', [left['item_id'], right['item_id']]))
    if bookings is not None:
        from .scheduler import booking_items
        authoritative, _ = booking_items(bookings, snapshot)
        expected = {item['booking_event_id']: item for item in authoritative}
        for booking in bookings:
            booking_id = booking.get('booking_id', booking.get('id'))
            for conflict in booking.get('time_conflicts', []):
                affected = [item['item_id'] for item in authoritative if item['booking_id'] == booking_id and (conflict['event_id'] is None or item['booking_event_id'] == conflict['event_id'])]
                corrected_day = (conflict.get('summary') or {}).get('date')
                if affected or corrected_day and snapshot['start_date'] <= corrected_day <= snapshot['end_date']:
                    checks.append(issue('BOOKING_TIME_CONFLICT', affected, state='violated'))
        actual = {item['booking_event_id']: item for item in normalized if item.get('booking_event_id')}
        for event_id, item in actual.items():
            current = expected.get(event_id)
            if current is None:
                checks.append(issue('BOOKING_REMOVED', [item['item_id']], state='violated'))
            elif any(current.get(key) != item.get(key) for key in ('local_start', 'local_end', 'start_timezone', 'end_timezone', 'reservation_status')):
                checks.append(issue('BOOKING_CHANGED', [item['item_id']], state='violated'))
            if item.get('lock_origin') != 'booking' or item.get('locked') is not True:
                checks.append(issue('BOOKING_LOCK_REQUIRED', [item['item_id']], state='violated'))
        for event_id, item in expected.items():
            if event_id not in actual:
                checks.append(issue('FIXED_BOOKING_MISSING', [item['item_id']], state='violated'))
    rest = snapshot.get('rest_preferences') or {}
    if rest.get('required') and rest.get('duration_minutes', rest.get('minutes', 0)):
        duration, every = rest.get('duration_minutes', rest.get('minutes', 0)), rest.get('after_visits', 2)
        daily = {}
        for item in busy:
            if item.get('item_type') == 'place':
                day = str(item.get('local_start', ''))[:10]
                daily.setdefault(day, []).append(item)
        for values in daily.values():
            for n, item in enumerate(values, 1):
                if n % every == 0 and not any(r.get('item_type') == 'rest' and r.get('parent_item_id') == item['item_id'] and r.get('duration_minutes', 0) >= duration and r.get('start_instant') == item.get('end_instant') for r in normalized):
                    checks.append(issue('REQUIRED_REST_MISSING', [item['item_id']], state='violated'))
    conflicts = [item for item in checks if item['state'] == 'violated']
    unresolved = [item for item in checks if item['state'] == 'unknown']
    return {'validation_status': 'conflicted' if conflicts else 'provisional' if unresolved else 'validated',
            'conflicts': conflicts, 'unresolved_conditions': unresolved, 'checks': checks}
