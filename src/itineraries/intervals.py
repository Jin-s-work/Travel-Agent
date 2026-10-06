"""Civil-time resolution and half-open UTC interval algebra."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A UTC offset is required')
    return result.astimezone(timezone.utc)


def resolve_local(local_iso, zone, fold=None, offset_minutes=None):
    result = {'local': local_iso, 'timezone': zone, 'instant': None, 'state': 'unknown', 'reason_code': None}
    if not local_iso:
        return {**result, 'reason_code': 'MISSING_LOCAL_TIME'}
    if len(str(local_iso)) == 10:
        return {**result, 'reason_code': 'DATE_ONLY'}
    try:
        local = datetime.fromisoformat(local_iso)
        if local.tzinfo is not None or 'T' not in local_iso or fold not in (None, 0, 1):
            raise ValueError('Local time must be naive')
        if not zone:
            return {**result, 'reason_code': 'MISSING_TIMEZONE'}
        target = ZoneInfo(zone)
        choices = []
        for possible_fold in (0, 1):
            civil = local.replace(tzinfo=target, fold=possible_fold)
            instant = civil.astimezone(timezone.utc)
            if instant.astimezone(target).replace(tzinfo=None) == local:
                choices.append((possible_fold, civil.utcoffset().total_seconds()/60, instant))
        if not choices:
            return {**result, 'state': 'violated', 'reason_code': 'NONEXISTENT_LOCAL_TIME'}
        distinct = {instant for _, _, instant in choices}
        if len(distinct) > 1 and fold is None and offset_minutes is None:
            return {**result, 'reason_code': 'AMBIGUOUS_LOCAL_TIME'}
        matching = [instant for possible_fold, offset, instant in choices
                    if (fold is None or possible_fold == fold) and (offset_minutes is None or offset == offset_minutes)]
        if not matching:
            return {**result, 'state': 'violated', 'reason_code': 'OFFSET_TIMEZONE_MISMATCH'}
        return {**result, 'state': 'satisfied', 'instant': matching[0].isoformat()}
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return {**result, 'state': 'violated', 'reason_code': 'INVALID_LOCAL_TIME'}


def normalize_item(item):
    """Recompute instants from civil time; never trust a caller's forged UTC pair."""
    item = dict(item)
    start = resolve_local(item.get('local_start'), item.get('start_timezone'), item.get('start_fold'), item.get('start_offset_minutes'))
    end = resolve_local(item.get('local_end'), item.get('end_timezone') or item.get('start_timezone'), item.get('end_fold'), item.get('end_offset_minutes'))
    checks = []
    for side, value in (('start', start), ('end', end)):
        if value['state'] != 'satisfied':
            checks.append({'state': value['state'], 'code': value['reason_code'], 'side': side})
        elif item.get(side+'_instant') and utc(item[side+'_instant']) != utc(value['instant']):
            checks.append({'state': 'violated', 'code': 'INSTANT_LOCAL_MISMATCH', 'side': side})
        item[side+'_instant'] = value['instant']
    if start['instant'] and end['instant']:
        minutes = (utc(end['instant'])-utc(start['instant'])).total_seconds()/60
        if minutes <= 0:
            checks.append({'state': 'violated', 'code': 'INVALID_INTERVAL', 'side': 'both'})
        item['duration_minutes'] = minutes if minutes > 0 else None
    else:
        item['duration_minutes'] = None
    return item, checks


def bounds(item):
    if not item.get('start_instant') or not item.get('end_instant'):
        return None
    try:
        start, end = utc(item['start_instant']), utc(item['end_instant'])
        return (start, end) if start < end else None
    except (ValueError, TypeError):
        return None


def overlaps(left, right):
    return left[0] < right[1] and right[0] < left[1]


def contains(outer, inner):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def subtract(window, blocked):
    fragments = [window]
    for a, b in sorted(blocked):
        fresh = []
        for start, end in fragments:
            if b <= start or a >= end:
                fresh.append((start, end)); continue
            if start < a:
                fresh.append((start, min(a, end)))
            if b < end:
                fresh.append((max(b, start), end))
        fragments = fresh
    return fragments


def local_iso(instant, zone):
    return utc(instant).astimezone(ZoneInfo(zone)).replace(tzinfo=None).isoformat(timespec='seconds')


def time_interval(day, raw, zone):
    if isinstance(raw, list) and len(raw) == 2:
        start, end, offset = raw[0], raw[1], 0
    elif isinstance(raw, dict):
        start, end, offset = raw.get('start'), raw.get('end'), raw.get('end_day_offset', 0)
    else:
        raise ValueError('Invalid interval')
    if type(offset) is not int or offset not in (0, 1):
        raise ValueError('Invalid next-day offset')
    start_clock, end_clock = time.fromisoformat(start), time.fromisoformat(end)
    a = resolve_local(datetime.combine(day, start_clock).isoformat(), zone)
    b = resolve_local(datetime.combine(day+timedelta(days=offset), end_clock).isoformat(), zone)
    if a['state'] != 'satisfied' or b['state'] != 'satisfied' or utc(a['instant']) >= utc(b['instant']):
        raise ValueError('Unresolved or reversed interval')
    return utc(a['instant']), utc(b['instant'])
