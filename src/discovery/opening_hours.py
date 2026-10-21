"""Conservative parser for a small subset of OSM ``opening_hours``.

Public-map hours are never verified business hours. The parser only reads the
common grammar below and leaves everything else unparsed instead of guessing:

* weekday selectors: ``Mo``, ranges ``Mo-Fr`` (wrapping ``Fr-Mo``), lists ``Mo,We``
* one or more time spans per day: ``10:00-14:00,17:00-22:00``
* ``off`` / ``closed`` without times
* past-midnight spans: ``18:00-02:00`` or ``18:00-26:00`` (end below 48:00)
* ``24/7``
* rules separated by ``;`` (a later rule replaces the days it names) and
  additional rules after ``,`` (they add to the days they name)

Holiday selectors (``PH``/``SH``) are left unparsed; the weekly template still
applies to ordinary days, so the result is ``partial``. Only the holiday-bearing
sub-rule is reported as unparsed (``Mo-Fr 10:00-18:00, PH off`` -> ``PH off``).
School holidays (``SH``) can replace the weekly table for weeks, not one day, so
they get their own reason code and note. Months, weeks, dates,
nth weekdays, comments, ``||`` fallbacks, open ends (``+``) and events such as
``sunrise`` make the whole value ``unparsed`` because they could override a
weekday that the subset cannot see.

Days the expression does not mention are closed under the OSM specification
(https://wiki.openstreetmap.org/wiki/Key:opening_hours/specification, read
2026-10-21). The fact value says so in its notes, and the fact stays provisional.
"""
import re

PARSER_VERSION = 'osm-opening-hours-subset-v1'
DAY_CODES = ('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')
DAY_NAMES = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
MAX_INPUT = 255  # OSM tag values are limited to 255 characters.
MAX_INTERVALS = 8  # FactInput accepts at most eight intervals per weekday.
NOTE_BASIS = '지도 정보 기준 영업시간 · 공개지도 등록값이라 방문 전 공식 안내를 확인해 주세요.'
NOTE_UNMENTIONED = '표기에 없는 요일은 공개지도 규칙에 따라 휴무로 읽었어요.'
NOTE_SCHOOL_HOLIDAY = '학교 방학(SH) 기간에는 몇 주 동안 위 요일별 시간과 다를 수 있어요.'
_SCHOOL_HOLIDAY = re.compile(r'(?<![A-Za-z])SH(?![A-Za-z])')

_DAY = '|'.join(DAY_CODES)
_TOKEN = re.compile(
    r'\s*(?:'
    r'(?P<all>24/7)'
    r'|(?P<time>(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*-\s*(?P<eh>\d{1,2}):(?P<em>\d{2}))(?![\d:+])'
    rf'|(?P<day>(?P<d1>{_DAY})(?:\s*-\s*(?P<d2>{_DAY}))?)(?![A-Za-z\[])'
    r'|(?P<hol>PH|SH)(?![A-Za-z\[])'
    r'|(?P<mod>off|closed|open)(?![A-Za-z])'
    r'|(?P<comma>,)'
    r')')


class Unparsed(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _tokens(rule):
    position, tokens = 0, []
    while position < len(rule):
        if not rule[position:].strip():
            break
        match = _TOKEN.match(rule, position)
        if not match or match.end() == position:
            raise Unparsed('UNSUPPORTED_SYNTAX')
        kind = next(name for name in ('all', 'time', 'day', 'hol', 'mod', 'comma') if match.group(name))
        tokens.append((kind, match))
        position = match.end()
    return tokens


def _span(match):
    sh, sm, eh, em = (int(match.group(k)) for k in ('sh', 'sm', 'eh', 'em'))
    if sh > 23 or sm > 59 or em > 59:
        raise Unparsed('INVALID_TIME')
    start, end = sh * 60 + sm, eh * 60 + em
    if end == start:
        raise Unparsed('AMBIGUOUS_TIME')
    if end < start:
        end += 1440  # e.g. 18:00-02:00 ends on the next day
    if end >= 2880:
        raise Unparsed('INVALID_TIME')
    return start, end


def _days(match):
    first = DAY_CODES.index(match.group('d1'))
    if not match.group('d2'):
        return [first]
    last = DAY_CODES.index(match.group('d2'))
    return [(first + i) % 7 for i in range((last - first) % 7 + 1)]


def _rules(tokens):
    """Group tokens into (days, holiday, intervals, additive, text) rules."""
    index, additive, result = 0, False, []
    def kind(i):
        return tokens[i][0] if i < len(tokens) else None
    while index < len(tokens):
        days, holiday, selected, first = [], False, False, index
        while kind(index) in ('day', 'hol'):
            selected = True
            if kind(index) == 'day':
                days.extend(_days(tokens[index][1]))
            else:
                holiday = True
            index += 1
            if kind(index) == 'comma' and kind(index + 1) in ('day', 'hol'):
                index += 1
                continue
            break
        intervals = None
        if kind(index) == 'all':
            if selected:
                raise Unparsed('UNSUPPORTED_SYNTAX')
            intervals = [(0, 1440)]
            index += 1
        else:
            spans = []
            while kind(index) == 'time':
                spans.append(_span(tokens[index][1]))
                index += 1
                if kind(index) == 'comma' and kind(index + 1) == 'time':
                    index += 1
                    continue
                break
            if spans:
                intervals = spans
        if kind(index) == 'mod':
            modifier = tokens[index][1].group('mod')
            index += 1
            if modifier in ('off', 'closed'):
                if intervals is not None:
                    raise Unparsed('UNSUPPORTED_SYNTAX')  # partial-day closures
                intervals = []
            elif intervals is None:
                raise Unparsed('AMBIGUOUS_OPEN')
        if intervals is None:
            # A bare selector ("Mo-Fr") has no clock time to schedule.
            raise Unparsed('TIME_MISSING')
        source = tokens[first][1].string
        text = source[tokens[first][1].start():tokens[index - 1][1].end()].strip()
        result.append((sorted(set(days)) if days else None if not holiday else [], holiday, intervals, additive, text))
        if kind(index) == 'comma':
            index += 1
            if kind(index) not in ('day', 'hol'):
                raise Unparsed('UNSUPPORTED_SYNTAX')
            additive = True
        elif index < len(tokens):
            raise Unparsed('UNSUPPORTED_SYNTAX')
    return result


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _clock(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def _interval(start, end):
    if end < 1440:
        return [_clock(start), _clock(end)]
    return {'start': _clock(start), 'end': _clock(end - 1440), 'end_day_offset': 1}


def _holiday_only(rule):
    return bool(re.match(r'\s*(PH|SH)(?![A-Za-z])', rule)) and not re.search(rf'(?<![A-Za-z])({_DAY})(?![a-z])', rule)


def parse(value):
    """Return a deterministic parse result; never raises for user data."""
    base = {'parser_version': PARSER_VERSION, 'weekly': None, 'unparsed': [], 'reason_codes': [],
            'unmentioned_days': []}
    if not isinstance(value, str) or not value.strip():
        return {**base, 'state': 'missing', 'reason_codes': ['HOURS_MISSING']}
    text = ' '.join(value.replace('\x00', '').split())
    if len(text) > MAX_INPUT:
        return {**base, 'state': 'unparsed', 'unparsed': [text[:80]], 'reason_codes': ['HOURS_TOO_LONG']}
    if '"' in text or '||' in text:
        code = 'HOURS_COMMENT' if '"' in text else 'HOURS_FALLBACK_RULE'
        return {**base, 'state': 'unparsed', 'unparsed': [text], 'reason_codes': [code]}
    weekly = [None] * 7
    holiday_rules, applied = [], False
    for raw in text.split(';'):
        rule = raw.strip()
        if not rule:
            continue
        try:
            groups = _rules(_tokens(rule))
        except Unparsed as error:
            if _holiday_only(rule):
                holiday_rules.append(rule)
                continue
            return {**base, 'state': 'unparsed', 'unparsed': [rule], 'reason_codes': ['HOURS_' + error.code]}
        for days, holiday, intervals, additive, sub_rule in groups:
            if holiday:
                holiday_rules.append(sub_rule)
            if days == []:
                continue  # holiday-only selector: no weekday to apply
            applied = True
            for day in days if days is not None else range(7):
                if additive and intervals:
                    weekly[day] = (weekly[day] or []) + list(intervals)
                else:
                    weekly[day] = list(intervals)
    if not applied:
        return {**base, 'state': 'unparsed', 'unparsed': list(dict.fromkeys(holiday_rules)) or [text],
                'reason_codes': ['HOURS_NO_WEEKDAY_RULE']}
    output, unmentioned = {}, []
    for day, intervals in enumerate(weekly):
        if intervals is None:
            unmentioned.append(DAY_NAMES[day])
            intervals = []
        merged = _merge(intervals)
        if len(merged) > MAX_INTERVALS:
            return {**base, 'state': 'unparsed', 'unparsed': [text], 'reason_codes': ['HOURS_TOO_MANY_INTERVALS']}
        output[DAY_NAMES[day]] = [_interval(start, end) for start, end in merged]
    holidays = list(dict.fromkeys(holiday_rules))
    codes = ['HOURS_HOLIDAY_UNPARSED'] if holidays else []
    if any(_SCHOOL_HOLIDAY.search(rule) for rule in holidays):
        codes.append('HOURS_SCHOOL_HOLIDAY_UNPARSED')
    return {**base, 'state': 'partial' if holidays else 'parsed', 'weekly': output, 'unparsed': holidays,
            'reason_codes': codes, 'unmentioned_days': unmentioned}


def fact_value(result, timezone):
    """Shape a parsed result as the existing structured ``opening_hours`` fact."""
    if not isinstance(result, dict) or result.get('state') not in ('parsed', 'partial') or not timezone:
        return None
    notes = [NOTE_BASIS]
    if result.get('unmentioned_days'):
        notes.append(NOTE_UNMENTIONED)
    if result.get('unparsed'):
        notes.append('해석하지 않은 표기(공휴일 등): ' + '; '.join(result['unparsed'])[:300])
    if 'HOURS_SCHOOL_HOLIDAY_UNPARSED' in (result.get('reason_codes') or []):
        notes.append(NOTE_SCHOOL_HOLIDAY)
    return {'timezone': timezone, 'weekly': result['weekly'], 'notes': notes}
