"""Synthetic itinerary constraints; no live route/provider claims."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.itineraries.intervals import resolve_local, overlaps, utc
from src.itineraries.constraints import validate, opening_windows
from src.itineraries.scheduler import generate, booking_items, rebuild_legs
from src.itineraries.edits import preview, revalidate
from tests.test_recommendation_engine import catalog, fact, conditions, NOW


def request(city='tokyo', **kwargs):
    value = {'trip_id': 'trip-1', 'trip_version': 1, 'city': city, 'start_date': '2026-11-06', 'end_date': '2026-11-06',
             'timezone': 'Asia/Tokyo' if city == 'tokyo' else 'Europe/Madrid', 'party': {'adults': 4 if city == 'tokyo' else 2, 'children': []},
             'conditions': conditions(city), 'selected': [{'place_id': 'place_'+city+'_1', 'duration_minutes': 90, 'duration_origin': 'user', 'priority': 0}],
             'origin': {'id': 'hotel', 'latitude': 35.68, 'longitude': 139.77, 'coordinate_permitted': True},
             'activity_start': '12:00', 'activity_end': '16:00', 'allow_provisional': False, 'transport': 'walking',
             'buffers': {'general_minutes': 0, 'booking_before_minutes': 0, 'booking_after_minutes': 0, 'airport_before_minutes': 120, 'airport_after_minutes': 60, 'unknown_travel_allowance_minutes': 30},
             'rest_preferences': {'duration_minutes': 20, 'after_visits': 2, 'required': False}}
    value.update(kwargs)
    return value


def booking(ident, start, end, *, kind='tour', zone='Asia/Tokyo', end_zone=None, status='user_confirmed'):
    return {'id': ident, 'version': 1, 'kind': kind, 'status': status, 'provider': ident,
            'events': [{'id': 'event-'+ident, 'event_type': 'visit', 'start_local': start, 'end_local': end,
                        'start_timezone': zone, 'end_timezone': end_zone or zone, 'location': {'id': ident, 'label': ident}}]}


def locks(meal_end='13:00'):
    return [booking('lunch', '2026-11-06T12:00', '2026-11-06T'+meal_end),
            booking('entry', '2026-11-06T15:00', '2026-11-06T16:00')]


class Routes:
    def __init__(self, minutes=30, basis='provider', code=None):
        self.minutes, self.basis, self.code = minutes, basis, code
        self.calls = []
    def __call__(self, start, end, departure, mode):
        self.calls.append((start['id'], end['id'], departure, mode))
        return {'basis': self.basis, 'provider': 'synthetic_routes', 'duration_minutes': 0 if start.get('place_id') and start.get('place_id') == end.get('place_id') else self.minutes, 'distance_m': None,
                'checked_at': NOW.isoformat(), 'expires_at': (NOW+timedelta(days=90)).isoformat(),
                'policy_version': 'synthetic-v1', 'source_refs': ['synthetic-route'], 'reason_codes': [self.code] if self.code else []}


def place_items(result):
    return [item for item in result['items'] if item['item_type'] == 'place']


@pytest.mark.parametrize('city', ['tokyo', 'barcelona'])
def test_120_minute_gap_cannot_hold_90_stay_plus_two_30_routes(city):
    zone = request(city)['timezone']
    booked = locks()
    for value in booked:
        for event in value['events']: event['start_timezone'] = event['end_timezone'] = zone
    result = generate(request(city), booked, catalog(city), Routes(), NOW)
    assert not place_items(result)
    assert 'NO_TIME_WINDOW' in result['unplaced'][0]['reason_codes']
    assert {item['booking_id'] for item in result['items']} == {'lunch', 'entry'}


def test_exact_150_gap_fits_but_additional_10_buffer_does_not():
    snapshot = request()
    result = generate(snapshot, locks('12:30'), catalog(), Routes(), NOW)
    item = place_items(result)[0]
    assert item['local_start'].startswith('2026-11-06T13:00')
    assert item['local_end'].startswith('2026-11-06T14:30')
    assert result['validation_status'] == 'validated', result
    snapshot['buffers']['booking_before_minutes'] = 10
    result = generate(snapshot, locks('12:30'), catalog(), Routes(), NOW)
    assert not place_items(result) and 'NO_TIME_WINDOW' in result['unplaced'][0]['reason_codes']


def test_entire_visit_must_fit_break_and_closing_not_only_arrival():
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    hours['value']['weekly'] = {'friday': [['10:00', '14:00'], ['17:00', '22:00']]}
    snapshot = request(activity_start='13:00', activity_end='16:00')
    result = generate(snapshot, [], [row], Routes(), NOW)
    assert not place_items(result)
    assert 'NO_TIME_WINDOW' in result['unplaced'][0]['reason_codes']
    hours['value']['weekly'] = {'friday': [['10:00', '22:00']]}
    extra = deepcopy(hours); extra.update(id='break', field='break_times', value=[['14:00', '17:00']]); row['facts'].append(extra)
    result = generate(snapshot, [], [row], Routes(), NOW)
    # Breaks now split candidate windows before insertion, so the remaining
    # fragments cannot fit the visit at all; no rejected overlap is created.
    assert not place_items(result) and 'NO_TIME_WINDOW' in result['unplaced'][0]['reason_codes']


def test_four_night_hotel_not_a_four_day_busy_block():
    hotel = booking('hotel', '2026-11-05T15:00', '2026-11-09T11:00', kind='hotel')
    result = generate(request(activity_start='09:00', activity_end='18:00'), [hotel], catalog(), Routes(), NOW)
    assert place_items(result)
    stay = next(item for item in result['items'] if item['item_type'] == 'booking')
    assert stay['blocking'] is False and stay['lodging_constraints']['stay_is_not_busy']
    assert stay['local_start'] == '2026-11-05T15:00'


def test_date_only_does_not_create_midnight_and_prevents_unverified_day_plan():
    booked = booking('date-only', '2026-11-06', None, zone=None)
    result = generate(request(), [booked], catalog(), Routes(), NOW)
    assert result['items'][0]['start_instant'] is None and result['items'][0]['end_instant'] is None
    assert not place_items(result)
    assert 'UNRESOLVED_FIXED_BOOKING' in result['unplaced'][0]['reason_codes']


def test_dst_absent_ambiguous_explicit_fold_and_cross_zone_flight():
    assert resolve_local('2026-03-29T02:30', 'Europe/Madrid')['reason_code'] == 'NONEXISTENT_LOCAL_TIME'
    assert resolve_local('2026-10-25T02:30', 'Europe/Madrid')['reason_code'] == 'AMBIGUOUS_LOCAL_TIME'
    first = resolve_local('2026-10-25T02:30', 'Europe/Madrid', fold=0)
    second = resolve_local('2026-10-25T02:30', 'Europe/Madrid', fold=1)
    assert utc(second['instant'])-utc(first['instant']) == timedelta(hours=1)
    flight = booking('flight', '2026-11-06T10:00', '2026-11-06T17:00', kind='flight', end_zone='Europe/Madrid')
    items, errors = booking_items([flight], request())
    assert not errors and items[0]['duration_minutes'] == 15*60
    assert items[0]['start_timezone'] == 'Asia/Tokyo' and items[0]['end_timezone'] == 'Europe/Madrid'


def test_overnight_opening_interval_is_four_hours():
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    hours['valid_for_date'] = '2026-11-03'
    hours['value']['weekly'] = {'monday': [{'start': '22:00', 'end': '02:00', 'end_day_offset': 1}], 'tuesday': []}
    result = opening_windows(row, '2026-11-03', 'Asia/Tokyo', NOW)
    assert result['state'] == 'satisfied'
    assert result['intervals'][0][1]-result['intervals'][0][0] == timedelta(hours=4)


def test_conflicting_locks_preserved_and_never_silently_fixed():
    booked = [booking('one', '2026-11-06T12:00', '2026-11-06T14:00'), booking('two', '2026-11-06T13:00', '2026-11-06T15:00')]
    result = generate(request(selected=[]), booked, catalog(), Routes(), NOW)
    assert result['validation_status'] == 'conflicted'
    assert any(value['code'] == 'CONFLICTING_LOCKS' for value in result['conflicts'])
    assert [item['local_start'] for item in result['items']] == ['2026-11-06T12:00', '2026-11-06T13:00']


@pytest.mark.parametrize('basis,duration,reason', [('unknown', None, 'NO_ROUTE'), ('estimate', 30, None)])
def test_unknown_or_estimated_routes_strict_unplaced_explicit_draft_provisional(basis, duration, reason):
    snapshot = request(activity_start='09:00', activity_end='18:00')
    assert not place_items(generate(snapshot, [], catalog(), Routes(duration, basis, reason), NOW))
    snapshot['allow_provisional'] = True
    result = generate(snapshot, [], catalog(), Routes(duration, basis, reason), NOW)
    assert place_items(result) and result['validation_status'] == 'provisional'
    leg = result['legs'][0]
    assert leg['duration_minutes'] == duration
    if duration is None:
        assert leg['planning_allowance_minutes'] == 30 and leg['reserved_minutes'] == 30


def test_provisional_never_permits_verified_closure_or_party_mismatch():
    snapshot = request(activity_start='09:00', activity_end='18:00', allow_provisional=True)
    row = catalog()[0]
    fact(row, 'closed')['value'] = True
    assert not place_items(generate(snapshot, [], [row], Routes(None, 'unknown'), NOW))
    fact(row, 'closed')['value'] = False
    fact(row, 'max_party')['value'] = 2
    result = generate(snapshot, [], [row], Routes(None, 'unknown'), NOW)
    assert not place_items(result) and 'PARTY_MISMATCH' in result['unplaced'][0]['reason_codes']


def test_future_weekly_only_requires_explicit_provisional_mode():
    row = catalog()[0]; fact(row, 'opening_hours').pop('valid_for_date')
    snapshot = request(activity_start='09:00', activity_end='18:00')
    strict = generate(snapshot, [], [row], Routes(), NOW)
    assert not place_items(strict) and 'FUTURE_OPENING_UNCONFIRMED' in strict['unplaced'][0]['reason_codes']
    snapshot['allow_provisional'] = True
    result = generate(snapshot, [], [row], Routes(), NOW)
    assert place_items(result) and result['validation_status'] == 'provisional'


def test_duplicate_selected_place_not_scheduled_twice_and_input_immutable():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    snapshot['selected'] *= 2
    before, rows = deepcopy(snapshot), catalog()
    result = generate(snapshot, [], rows, Routes(), NOW)
    assert len(place_items(result)) == 1 and result['unplaced'][0]['reason_codes'] == ['DUPLICATE_PLACE']
    assert snapshot == before
    assert result == generate(snapshot, [], list(reversed(rows)), Routes(), NOW)


def test_preview_has_no_mutation_and_explicit_unlock_required():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    rows = catalog(); current = generate(snapshot, [], rows, Routes(), NOW)
    ident = place_items(current)[0]['item_id']; original = deepcopy(current)
    locked = preview(current, [{'op': 'lock', 'item_id': ident}], snapshot, [], rows, Routes(), NOW)
    assert current == original and locked['items'][0]['lock_origin'] == 'user'
    rejected = preview(locked, [{'op': 'move', 'item_id': ident, 'local_start': '2026-11-06T14:00'}], snapshot, [], rows, Routes(), NOW)
    assert any(value['code'] == 'ITEM_LOCKED' for value in rejected['conflicts'])
    moved = preview(locked, [{'op': 'unlock', 'item_id': ident}, {'op': 'move', 'item_id': ident, 'local_start': '2026-11-06T14:00'}], snapshot, [], rows, Routes(), NOW)
    assert moved['items'][0]['local_start'].startswith('2026-11-06T14:00')
    assert moved['items'][0]['reservation_status'] == 'not_booked'
    assert moved['diff']['changed'] == [ident]


@pytest.mark.parametrize('op', ['remove', 'move', 'unlock'])
def test_booking_cannot_be_modified_by_itinerary_command(op):
    snapshot, booked = request(selected=[]), locks()
    current = generate(snapshot, booked, catalog(), Routes(), NOW)
    command = {'op': op, 'item_id': current['items'][0]['item_id']}
    if op == 'move': command['local_start'] = '2026-11-06T09:00'
    changed = preview(current, [command], snapshot, booked, catalog(), Routes(), NOW)
    assert any(value['code'] == 'BOOKING_EDIT_FORBIDDEN' for value in changed['conflicts'])
    assert changed['items'][0]['local_start'] == current['items'][0]['local_start']


def test_undo_revalidation_uses_current_booking_time_and_removal_and_current_policy():
    snapshot, booked, rows = request(), locks('12:30'), catalog()
    current = generate(snapshot, booked, rows, Routes(), NOW)
    changed = deepcopy(booked); changed[0]['events'][0]['end_local'] = '2026-11-06T14:00'
    result = revalidate(current, snapshot, changed, rows, Routes(), NOW)
    assert any(item['booking_id'] == 'lunch' and item['local_end'].endswith('14:00') for item in result['items'])
    assert result['validation_status'] == 'conflicted'
    removed = revalidate(current, snapshot, [], rows, Routes(), NOW)
    assert all(item['item_type'] != 'booking' for item in removed['items'])
    for source in rows[0]['sources']: source['status'] = 'revoked'
    revoked = revalidate(current, snapshot, booked, rows, Routes(), NOW)
    assert any(item['code'] == 'SOURCE_POLICY_UNAVAILABLE' for item in revoked['conflicts'])


def test_half_open_edges_still_require_travel_and_cutoff_is_separate():
    assert not overlaps((utc('2026-11-06T00:00Z'), utc('2026-11-06T01:00Z')), (utc('2026-11-06T01:00Z'), utc('2026-11-06T02:00Z')))
    snapshot, booked = request(selected=[]), [booking('a', '2026-11-06T12:00', '2026-11-06T13:00'), booking('b', '2026-11-06T13:00', '2026-11-06T14:00')]
    result = generate(snapshot, booked, catalog(), Routes(30), NOW)
    assert any(item['code'] == 'INSUFFICIENT_TRAVEL_TIME' for item in result['conflicts'])


def test_search_bound_and_long_stay_return_explicit_unplaced_reason():
    snapshot = request(max_evaluations=1)
    snapshot['selected'][0]['duration_minutes'] = 700
    result = generate(snapshot, locks(), catalog(), Routes(), NOW)
    assert not place_items(result) and result['search']['evaluated_positions'] <= 1
    assert result['unplaced']


def test_density_rest_and_meal_preferences_influence_deterministic_plan():
    rows = catalog()
    for row in rows:
        fact(row, 'max_party')['value'] = 10
        fact(row, 'closed')['value'] = False
    snapshot = request(activity_start='09:00', activity_end='21:00', density='relaxed')
    snapshot['selected'] = [{'place_id': row['place_id'], 'duration_minutes': 60, 'duration_origin': 'user', 'priority': n} for n, row in enumerate(rows[:3])]
    snapshot['rest_preferences']['required'] = True
    result = generate(snapshot, [], rows, Routes(10), NOW)
    assert len(place_items(result)) == 2
    assert any(item['item_type'] == 'rest' and item['duration_minutes'] == 20 for item in result['items'])
    assert any('DENSITY_LIMIT_REACHED' in entry['reason_codes'] for entry in result['unplaced'])
    assert result['validation_status'] == 'validated'
    one = request(activity_start='09:00', activity_end='21:00', meal_time='lunch')
    lunch = generate(one, [], rows, Routes(10), NOW)
    assert place_items(lunch)[0]['local_start'].startswith('2026-11-06T12:00')
    assert any(entry['code'] == 'DEFAULT_MEAL_WINDOW' for entry in lunch['assumptions'])


def test_revalidate_refreshes_coordinates_permissions_and_metadata_before_route_lookup():
    rows = catalog(); snapshot = request(activity_start='09:00', activity_end='18:00')
    current = generate(snapshot, [], rows, Routes(), NOW)
    rows[0].update(latitude=40.0, longitude=140.0, coordinate_permitted=False, name='Current name')
    endpoints = []
    def recorded(a, b, departure, mode):
        endpoints.append(deepcopy(b)); return Routes()(a, b, departure, mode)
    revised = revalidate(current, snapshot, [], rows, recorded, NOW)
    assert endpoints[0]['latitude'] == 40.0 and endpoints[0]['coordinate_permitted'] is False
    assert place_items(revised)[0]['name'] == 'Current name'
    assert place_items(revised)[0]['source_refs']
    endpoints.clear()
    removed = revalidate(current, snapshot, [], [], recorded, NOW)
    assert not endpoints
    assert place_items(removed)[0]['location']['latitude'] is None
    assert not place_items(removed)[0]['source_refs']
    assert any(item['code'] == 'PLACE_UNAVAILABLE' for item in removed['conflicts'])


def test_route_receipt_time_after_run_start_is_valid_future_provider_time_not_valid():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    def fresh(a, b, departure, mode):
        result = Routes()(a, b, departure, mode)
        result.update(checked_at=(NOW+timedelta(seconds=1)).isoformat(), observed_at=(NOW+timedelta(seconds=2)).isoformat())
        return result
    assert place_items(generate(snapshot, [], catalog(), fresh, NOW))
    def forged(a, b, departure, mode):
        result = fresh(a, b, departure, mode); result['checked_at'] = (NOW+timedelta(days=1)).isoformat(); return result
    assert not place_items(generate(snapshot, [], catalog(), forged, NOW))


def test_global_validator_removes_optional_place_when_late_route_is_impossible():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    routes = Routes(); calls = 0
    def changes(a, b, departure, mode):
        nonlocal calls
        calls += 1
        result = routes(a, b, departure, mode)
        if calls >= 2: result['reason_codes'] = ['ROUTE_IMPOSSIBLE']
        return result
    result = generate(snapshot, [], catalog(), changes, NOW)
    assert not place_items(result)
    assert any('ROUTE_IMPOSSIBLE' in entry['reason_codes'] for entry in result['unplaced'])


def test_mandatory_radius_and_airport_buffer_unknown_are_not_ignored():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    rows = catalog(); rows[0]['coordinate_permitted'] = True
    snapshot['conditions']['radius_m'] = 100
    result = generate(snapshot, [], rows, Routes(1), NOW)
    assert not place_items(result) and 'MAXIMUM_DISTANCE_EXCEEDED' in result['unplaced'][0]['reason_codes']
    snapshot['conditions'].pop('radius_m')
    snapshot['selected'] = []
    snapshot['buffers']['airport_before_minutes'] = None
    flight = booking('flight', '2026-11-06T12:00', '2026-11-06T15:00', kind='flight')
    result = generate(snapshot, [flight], rows, Routes(10), NOW)
    assert result['validation_status'] == 'provisional'
    assert any(item['code'] == 'UNKNOWN_AIRPORT_BUFFER' for item in result['unresolved_conditions'])


def test_flight_arriving_next_local_day_keeps_route_to_first_visit():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    snapshot['buffers']['airport_after_minutes'] = 0
    flight = booking('nightflight', '2026-11-05T23:00', '2026-11-06T10:00', kind='flight')
    result = generate(snapshot, [flight], catalog(), Routes(30), NOW)
    assert place_items(result)
    flight_item = next(item for item in result['items'] if item.get('booking_id') == 'nightflight')
    assert any(leg['from_item_id'] == flight_item['item_id'] and leg['to_item_id'] == place_items(result)[0]['item_id'] for leg in result['legs'])
    assert not any(item['code'] == 'MISSING_TRAVEL_LEG' for item in result['unresolved_conditions'])


def test_booking_scope_compares_utc_not_departure_local_calendar_date():
    snapshot = request('barcelona', start_date='2026-11-05', end_date='2026-11-05', selected=[])
    flight = booking('flight', '2026-11-06T00:30', '2026-11-05T22:00', kind='flight', end_zone='Europe/Madrid')
    items, errors = booking_items([flight], snapshot)
    assert len(items) == 1 and not errors
    assert items[0]['local_start'] == '2026-11-06T00:30'


def test_provisional_closed_report_cannot_be_promoted_by_verified_weekly_hours():
    row = catalog()[0]; fact(row, 'closed').update(status='provisional', value=True)
    result = generate(request(activity_start='09:00', activity_end='18:00'), [], [row], Routes(), NOW)
    assert not place_items(result)
    assert 'BUSINESS_STATUS_UNCONFIRMED' in result['unplaced'][0]['reason_codes']


def test_date_only_lodging_can_anchor_but_time_is_not_verified():
    hotel = booking('hotel', '2026-11-05', '2026-11-09', kind='hotel')
    result = generate(request(activity_start='09:00', activity_end='18:00'), [hotel], catalog(), Routes(), NOW)
    assert place_items(result)
    stay = next(item for item in result['items'] if item['item_type'] == 'booking')
    assert stay['start_instant'] is None and stay['verification_status'] == 'provisional'
    assert any(value['code'] == 'LODGING_TIME_UNCONFIRMED' for value in result['unresolved_conditions'])


def test_rest_stays_within_parent_opening_and_does_not_add_transfer_buffer():
    rows = catalog(); snapshot = request(activity_start='09:00', activity_end='18:00')
    snapshot['rest_preferences'].update(after_visits=1, duration_minutes=20, required=True)
    snapshot['buffers']['general_minutes'] = 10
    result = generate(snapshot, [], rows, Routes(10), NOW)
    assert place_items(result) and result['validation_status'] == 'validated'
    rest_leg = next(leg for leg in result['legs'] if any(item['item_type'] == 'rest' and item['item_id'] == leg['to_item_id'] for item in result['items']))
    assert rest_leg['buffer_minutes'] == 0 and rest_leg['duration_minutes'] == 0
    fact(rows[0], 'opening_hours')['value']['weekly'] = {'friday': [['10:00', '11:30']]}
    result = generate(snapshot, [], rows, Routes(0), NOW)
    assert not place_items(result) and 'NO_REST_WINDOW' in result['unplaced'][0]['reason_codes']


def test_explicit_duration_edit_removes_only_default_duration_assumptions():
    snapshot = request(activity_start='09:00', activity_end='18:00')
    snapshot['selected'][0].update(duration_minutes=60, duration_origin='default')
    rows = catalog(); current = generate(snapshot, [], rows, Routes(), NOW)
    item = place_items(current)[0]
    item['assumptions'].extend(['KEEP_OTHER_ASSUMPTION', {'code': 'KEEP_STRUCTURED_ASSUMPTION'}, {'code': 'DEFAULT_DURATION', 'minutes': 60}])
    before = deepcopy(current)
    changed = preview(current, [{'op': 'move', 'item_id': item['item_id'], 'local_start': '2026-11-06T14:00', 'duration_minutes': 40}], snapshot, [], rows, Routes(), NOW)
    updated = place_items(changed)[0]
    assert updated['duration_minutes'] == 40 and updated['duration_origin'] == 'user'
    assert updated['assumptions'] == ['KEEP_OTHER_ASSUMPTION', {'code': 'KEEP_STRUCTURED_ASSUMPTION'}]
    assert current == before
    only_time = preview(current, [{'op': 'move', 'item_id': item['item_id'], 'local_start': '2026-11-06T14:00'}], snapshot, [], rows, Routes(), NOW)
    assert place_items(only_time)[0]['duration_origin'] == 'default'
    assert place_items(only_time)[0]['assumptions'] == item['assumptions']


def test_separate_break_is_subtracted_to_find_later_valid_insertion():
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    extra = deepcopy(hours); extra.update(id='afternoon-break', field='break_times', value=[['14:00', '17:00']]); row['facts'].append(extra)
    result = generate(request(activity_start='13:00', activity_end='20:00'), [], [row], Routes(), NOW)
    item = place_items(result)[0]
    assert item['local_start'].startswith('2026-11-06T17:00') and item['local_end'].startswith('2026-11-06T18:30')
    assert result['validation_status'] == 'validated' and not result['unplaced']
    assert 'source_1_official' in item['source_refs']


def test_break_split_preserves_original_interval_last_entry_constraint():
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    hours['value']['weekly'] = {'friday': [{'start': '10:00', 'end': '22:00', 'last_entry': '16:00'}]}
    extra = deepcopy(hours); extra.update(id='afternoon-break', field='break_times', value=[['14:00', '17:00']]); row['facts'].append(extra)
    result = generate(request(activity_start='13:00', activity_end='20:00'), [], [row], Routes(), NOW)
    assert not place_items(result)
    assert 'AFTER_LAST_ENTRY' in result['unplaced'][0]['reason_codes']


@pytest.mark.parametrize('cutoff', ['last_entry', 'last_order'])
def test_provisional_break_cannot_weaken_verified_cutoff_in_provisional_mode(cutoff):
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    hours['value']['weekly'] = {'friday': [{'start': '10:00', 'end': '22:00', cutoff: '16:00'}]}
    extra = deepcopy(hours); extra.update(id='provisional-break', field='break_times', status='provisional', value=[['14:00', '17:00']]); row['facts'].append(extra)
    snapshot = request(activity_start='13:00', activity_end='20:00', allow_provisional=True)
    result = generate(snapshot, [], [row], Routes(), NOW)
    assert not place_items(result)
    assert 'AFTER_'+cutoff.upper() in result['unplaced'][0]['reason_codes']
    current = generate({**snapshot, 'activity_start': '09:00'}, [], [row], Routes(), NOW)
    candidate = place_items(current)[0]
    edited = preview(current, [{'op': 'move', 'item_id': candidate['item_id'], 'local_start': '2026-11-06T17:00'}], snapshot, [], [row], Routes(), NOW)
    assert any(value['code'] == 'AFTER_'+cutoff.upper() and value['state'] == 'violated' for value in edited['conflicts'])


@pytest.mark.parametrize('local_start,duration', [('2026-11-06T09:30', 90), ('2026-11-06T21:00', 120)])
def test_provisional_break_cannot_weaken_verified_opening_boundaries(local_start, duration):
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    extra = deepcopy(hours); extra.update(id='provisional-break', field='break_times', status='provisional', value=[['14:00', '17:00']]); row['facts'].append(extra)
    snapshot = request(activity_start='09:00', activity_end='23:30', allow_provisional=True)
    current = generate(snapshot, [], [row], Routes(), NOW)
    candidate = place_items(current)[0]
    edited = preview(current, [{'op': 'move', 'item_id': candidate['item_id'], 'local_start': local_start, 'duration_minutes': duration}], snapshot, [], [row], Routes(), NOW)
    assert any(value['code'] == 'OUTSIDE_OPENING_HOURS' and value['state'] == 'violated' for value in edited['conflicts'])


def test_only_uncertain_break_remains_provisional_within_verified_opening():
    row = catalog()[0]
    hours = fact(row, 'opening_hours')
    extra = deepcopy(hours); extra.update(id='provisional-break', field='break_times', status='provisional', value=[['14:00', '17:00']]); row['facts'].append(extra)
    snapshot = request(activity_start='09:00', activity_end='21:00', allow_provisional=True)
    current = generate(snapshot, [], [row], Routes(), NOW)
    candidate = place_items(current)[0]
    edited = preview(current, [{'op': 'move', 'item_id': candidate['item_id'], 'local_start': '2026-11-06T13:30', 'duration_minutes': 90}], snapshot, [], [row], Routes(), NOW)
    assert not edited['conflicts'] and edited['validation_status'] == 'provisional'
    assert any(value['code'] == 'BREAK_TIME_OVERLAP' for value in edited['unresolved_conditions'])
