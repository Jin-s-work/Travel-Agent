"""Detect disagreeing representations of a user-corrected booking moment.

A summary clock and a leg timestamp are separate edit fields today. We report
conflicts rather than guessing a leg, changing fixed reservations, or spreading
an absolute-date override into a later re-extraction.
"""
from datetime import time


def _parts(local):
    if not local:
        return {'date': None, 'time': None}
    return {'date': local[:10], 'time': local[11:] if len(local) > 10 else None}


def _clock(value):
    return time.fromisoformat(value).isoformat() if value else None


def booking_time_conflicts(extracted, effective, overrides):
    result = []
    original_events = extracted.get('events') or []
    current = {event['id']: event for event in effective.get('events', [])}
    paths = set(overrides)
    for side, date_key, time_key, local_key in (
        ('start', 'date', 'time', 'start_local'),
        ('end', 'date_end', 'time_end', 'end_local'),
    ):
        summary_changed = bool(paths & {date_key, time_key})
        edited_events = {event['id'] for event in original_events if f"events.{event['id']}.{local_key}" in paths}
        if not summary_changed and not edited_events:
            continue
        base = {'date': extracted.get(date_key), 'time': extracted.get(time_key)}
        known = any(value is not None for value in base.values())
        matches = [event for event in original_events if known and
                   (base['date'] is None or _parts(event.get(local_key))['date'] == base['date']) and
                   (base['time'] is None or _clock(_parts(event.get(local_key))['time']) == _clock(base['time']))]
        if not matches and len(original_events) == 1:
            matches = original_events
        summary = {'date': effective.get(date_key), 'time': effective.get(time_key)}
        if len(matches) != 1:
            if summary_changed and original_events:
                result.append({'code':'BOOKING_TIME_CONFLICT','side':side,'event_id':None,
                               'summary':summary,'event_local':None,'summary_origin':'user','event_origin':'unknown','reason':'AMBIGUOUS_EVENT_MATCH'})
            continue
        source = matches[0]
        event = current.get(source['id'])
        if event is None or not (summary_changed or event['id'] in edited_events):
            continue
        actual = _parts(event.get(local_key))
        differences = []
        for field, key in (('date', date_key), ('time', time_key)):
            # An originally unknown summary is not an assertion. Explicitly
            # clearing a previously known summary, however, must be respected.
            if summary[field] is None and key not in paths:
                continue
            equal = _clock(summary[field]) == _clock(actual[field]) if field == 'time' else summary[field] == actual[field]
            if not equal:
                differences.append(key)
        if differences:
            result.append({'code':'BOOKING_TIME_CONFLICT','side':side,'event_id':event['id'],
                           'summary':summary,'event_local':event.get(local_key),
                           'fields':differences,'summary_origin':'user' if summary_changed else 'extracted',
                           'event_origin':'user' if event['id'] in edited_events else 'extracted','reason':'SUMMARY_EVENT_MISMATCH'})
    return result
