"""Side-effect-free command preview and current-evidence revalidation."""
from copy import deepcopy
from datetime import timedelta
from zoneinfo import ZoneInfo

from .constraints import issue, validate, identity_eligible
from .intervals import local_iso, resolve_local, utc
from .scheduler import _place_item, booking_items, rebuild_legs


def _diff(before, after):
    left, right = {item['item_id']: item for item in before}, {item['item_id']: item for item in after}
    changed = [ident for ident in sorted(set(left)|set(right)) if left.get(ident) != right.get(ident)]
    return {'before': deepcopy(before), 'after': deepcopy(after),
            'added': [ident for ident in changed if ident not in left],
            'removed': [ident for ident in changed if ident not in right],
            'changed': [ident for ident in changed if ident in left and ident in right],
            'affected_dates': sorted({str(item.get('local_start') or '')[:10] for ident in changed
                                      for item in (left.get(ident), right.get(ident)) if item and item.get('local_start')})}


def revalidate(current, snapshot, bookings, candidates, route_lookup, now):
    result = deepcopy(current)
    old_items = deepcopy(current.get('items', []))
    authoritative, unknown = booking_items(bookings, snapshot)
    old_booking_ids = {item.get('booking_event_id'): item['item_id'] for item in old_items if item.get('booking_event_id')}
    for item in authoritative:
        if item.get('booking_event_id') in old_booking_ids:
            item['item_id'] = old_booking_ids[item['booking_event_id']]
    # An undo is restoration of user intentions, never resurrection of cancelled
    # or deleted external booking events from an old revision.
    items = [deepcopy(item) for item in old_items if item.get('item_type') != 'booking']+authoritative
    catalog = {candidate['place_id']: candidate for candidate in candidates}
    for item in items:
        if not item.get('place_id'):
            continue
        candidate = catalog.get(item['place_id'])
        permitted = identity_eligible(candidate) and any(
            source.get('status') == 'active' and source.get('read_confirmed') and source.get('display_permitted') for source in candidate.get('sources', []))
        item['source_refs'] = []
        if not permitted:
            item.update(name='사용할 수 없는 장소', native_name=None, location={'id': item['place_id'], 'unavailable': True,
                        'latitude': None, 'longitude': None, 'coordinate_permitted': False})
            continue
        item.update(name=candidate.get('name'), native_name=candidate.get('native_name'), category=candidate.get('category'),
                    city=candidate.get('city'), neighborhood=candidate.get('neighborhood'), synthetic=bool(candidate.get('synthetic')))
        item['location'] = {'id': candidate['place_id'], 'place_id': candidate['place_id'], 'label': candidate.get('name'),
                            'city': candidate.get('city'), 'latitude': candidate.get('latitude'), 'longitude': candidate.get('longitude'),
                            'coordinate_permitted': candidate.get('coordinate_permitted', False)}
    parents = {item['item_id']: item for item in items}
    items = [item for item in items if item.get('item_type') != 'rest' or item.get('parent_item_id') in parents]
    for item in items:
        if item.get('item_type') == 'rest':
            item['location'] = deepcopy(parents[item['parent_item_id']]['location'])
    items.sort(key=lambda item: (item.get('start_instant') or '9999', item['item_id']))
    legs = rebuild_legs(items, snapshot, route_lookup, now)
    validation = validate(items, candidates, snapshot, legs, now, bookings=bookings)
    for item in items:
        relevant = [value for value in validation['checks'] if item['item_id'] in value['item_ids']]
        item['verification_status'] = 'conflicted' if any(value['state'] == 'violated' for value in relevant) else 'provisional' if any(value['state'] == 'unknown' for value in relevant) else 'verified'
        item['unresolved_conditions'] = [value for value in relevant if value['state'] == 'unknown']
        if item.get('place_id'):
            item['source_refs'] = sorted({ref for value in relevant for ref in value.get('source_refs', [])})
    result.update(items=items, legs=legs, **validation)
    result.setdefault('unplaced', [])
    result.setdefault('assumptions', [])
    result['booking_changes'] = _diff([item for item in old_items if item.get('item_type') == 'booking'], authoritative)
    return result


def preview(current, commands, snapshot, bookings, candidates, route_lookup, now):
    before = deepcopy(current.get('items', []))
    items, failures = deepcopy(before), []
    catalog = {candidate['place_id']: candidate for candidate in candidates}
    normalized = []
    if not isinstance(commands, list) or not 1 <= len(commands) <= 30:
        failures.append(issue('INVALID_EDIT_COMMANDS', state='violated'))
        commands = []
    for command in commands:
        if not isinstance(command, dict) or command.get('op') not in {'add', 'remove', 'move', 'lock', 'unlock'}:
            failures.append(issue('INVALID_EDIT_COMMAND', state='violated')); break
        operation = command['op']
        allowed = {'op', 'item_id', 'place_id', 'local_start', 'duration_minutes', 'duration_origin', 'fold'} if operation == 'add' else {'op', 'item_id', 'local_start', 'duration_minutes', 'fold'} if operation == 'move' else {'op', 'item_id'}
        if set(command)-allowed:
            failures.append(issue('INVALID_EDIT_FIELD', state='violated')); break
        item = next((value for value in items if value['item_id'] == command.get('item_id')), None)
        if operation != 'add':
            if item is None:
                failures.append(issue('ITEM_NOT_FOUND', [command.get('item_id')], state='violated')); break
            if item.get('lock_origin') == 'booking' or item.get('item_type') == 'booking':
                failures.append(issue('BOOKING_EDIT_FORBIDDEN', [item['item_id']], state='violated')); break
            if operation in {'move', 'remove'} and item.get('locked'):
                failures.append(issue('ITEM_LOCKED', [item['item_id']], state='violated')); break
        if operation == 'add':
            place_id = command.get('place_id')
            if place_id not in catalog:
                failures.append(issue('PLACE_UNAVAILABLE', state='violated')); break
            if any(value.get('place_id') == place_id for value in items):
                failures.append(issue('DUPLICATE_PLACE', state='violated')); break
            if not command.get('item_id') or any(value['item_id'] == command['item_id'] for value in items):
                failures.append(issue('INVALID_ITEM_ID', state='violated')); break
        if operation in {'add', 'move'}:
            zone = snapshot['timezone'] if operation == 'add' else item['start_timezone']
            resolved = resolve_local(command.get('local_start'), zone, command.get('fold'))
            duration = command.get('duration_minutes') or (item.get('duration_minutes') if item else None)
            if resolved['state'] != 'satisfied' or type(duration) not in (int, float) or not 1 <= duration <= 720:
                failures.append(issue(resolved['reason_code'] or 'INVALID_DURATION', [item['item_id']] if item else [], state='violated' if resolved['state'] == 'satisfied' else resolved['state'])); break
            start, end = utc(resolved['instant']), utc(resolved['instant'])+timedelta(minutes=duration)
            if operation == 'add':
                added = _place_item(catalog[command['place_id']], {**command, 'duration_origin': 'user'}, start, end, snapshot)
                items.append(added)
            else:
                item.update(local_start=local_iso(start, zone), local_end=local_iso(end, zone),
                            start_instant=start.isoformat(), end_instant=end.isoformat(), duration_minutes=duration,
                            start_fold=command.get('fold', 0), end_fold=end.astimezone(ZoneInfo(zone)).fold,
                            end_timezone=zone)
                if command.get('duration_minutes') is not None:
                    item['duration_origin'] = 'user'
                    default_duration_codes = {'DEFAULT_DURATION', 'DEFAULT_STAY_DURATION', 'PLANNING_DEFAULT_DURATION'}
                    item['assumptions'] = [assumption for assumption in item.get('assumptions', [])
                                           if (assumption.get('code') if isinstance(assumption, dict) else assumption) not in default_duration_codes]
        elif operation == 'remove':
            items.remove(item)
        elif operation == 'lock':
            item.update(locked=True, lock_origin='user')
        elif operation == 'unlock':
            item.update(locked=False, lock_origin=None)
        normalized.append(deepcopy(command))
    proposed = {**deepcopy(current), 'items': before if failures else items}
    result = revalidate(proposed, snapshot, bookings, candidates, route_lookup, now)
    if failures:
        result['conflicts'] = failures+result['conflicts']
        result['validation_status'] = 'conflicted'
    result['normalized_commands'] = normalized
    result['diff'] = _diff(before, result['items'])
    result['affected_dates'] = result['diff']['affected_dates']
    return result
