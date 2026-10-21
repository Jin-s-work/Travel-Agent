"""OSM opening_hours subset parser. Synthetic expressions modelled on common OSM
spellings (rewritten, not copied from any real venue). No network."""
from datetime import datetime, timedelta, timezone

import pytest

from src.discovery.models import FactInput
from src.discovery.opening_hours import (DAY_NAMES, NOTE_BASIS, NOTE_SCHOOL_HOLIDAY, NOTE_UNMENTIONED, PARSER_VERSION,
                                        fact_value, parse)

WEEKDAYS = DAY_NAMES[:5]
OVERNIGHT = lambda start, end: {'start': start, 'end': end, 'end_day_offset': 1}
FULL_DAY = OVERNIGHT('00:00', '00:00')


def week(**days):
    return {name: days.get(name, []) for name in DAY_NAMES}


def every(intervals, names=DAY_NAMES):
    return {name: list(intervals) if name in names else [] for name in DAY_NAMES}


PARSED = [
    ('single_day', 'Mo 10:00-18:00', week(monday=[['10:00', '18:00']])),
    ('weekday_range', 'Mo-Fr 09:00-18:00', every([['09:00', '18:00']], WEEKDAYS)),
    ('whole_week_range', 'Mo-Su 11:30-22:00', every([['11:30', '22:00']])),
    ('time_only_means_every_day', '10:00-20:00', every([['10:00', '20:00']])),
    ('two_spans_one_day', 'Mo-Fr 11:00-14:00,17:00-22:00', every([['11:00', '14:00'], ['17:00', '22:00']], WEEKDAYS)),
    ('three_spans', 'Sa 08:00-10:00,12:00-14:00,18:00-20:00', week(saturday=[['08:00', '10:00'], ['12:00', '14:00'], ['18:00', '20:00']])),
    ('day_list', 'Mo,We,Fr 10:00-16:00', week(monday=[['10:00', '16:00']], wednesday=[['10:00', '16:00']], friday=[['10:00', '16:00']])),
    ('range_and_day_list', 'Mo-We,Fr 10:00-16:00', every([['10:00', '16:00']], ('monday', 'tuesday', 'wednesday', 'friday'))),
    ('wrapping_range', 'Fr-Mo 12:00-15:00', every([['12:00', '15:00']], ('friday', 'saturday', 'sunday', 'monday'))),
    ('later_rule_overrides_day', 'Mo-Sa 10:00-20:00; We 10:00-14:00',
     {**every([['10:00', '20:00']], DAY_NAMES[:6]), 'wednesday': [['10:00', '14:00']]}),
    ('off_overrides_day', 'Mo-Su 09:00-17:00; Tu off', {**every([['09:00', '17:00']]), 'tuesday': []}),
    ('closed_synonym', 'Mo-Su 09:00-17:00; Su closed', {**every([['09:00', '17:00']]), 'sunday': []}),
    ('weekend_rule_after_weekdays', 'Mo-Fr 08:00-17:00; Sa-Su 10:00-15:00',
     {**every([['08:00', '17:00']], WEEKDAYS), 'saturday': [['10:00', '15:00']], 'sunday': [['10:00', '15:00']]}),
    ('additional_rule_adds_spans', 'Mo-Fr 08:00-12:00, We 14:00-18:00',
     {**every([['08:00', '12:00']], WEEKDAYS), 'wednesday': [['08:00', '12:00'], ['14:00', '18:00']]}),
    ('additional_off_closes_day', 'Mo-Sa 10:00-19:00, Sa off', {**every([['10:00', '19:00']], DAY_NAMES[:5]), 'saturday': []}),
    ('past_midnight_wrap', 'Fr-Sa 18:00-02:00', week(friday=[OVERNIGHT('18:00', '02:00')], saturday=[OVERNIGHT('18:00', '02:00')])),
    ('past_midnight_extended_clock', 'Mo-Su 17:00-26:00', every([OVERNIGHT('17:00', '02:00')])),
    ('end_at_24', 'Th 18:00-24:00', week(thursday=[OVERNIGHT('18:00', '00:00')])),
    ('twenty_four_seven', '24/7', every([FULL_DAY])),
    ('whole_day_clock', 'Mo-Su 00:00-24:00', every([FULL_DAY])),
    ('single_digit_hour', 'Mo-Fr 9:00-17:30', every([['09:00', '17:30']], WEEKDAYS)),
    ('spaces_around_dash_and_semicolon', 'Mo - Fr 09:00 - 18:00 ;  Sa 10:00-13:00',
     {**every([['09:00', '18:00']], WEEKDAYS), 'saturday': [['10:00', '13:00']]}),
    ('overlapping_spans_merged', 'Mo 10:00-14:00,13:00-16:00', week(monday=[['10:00', '16:00']])),
    ('open_modifier_is_plain', 'Mo-Fr 10:00-18:00 open', every([['10:00', '18:00']], WEEKDAYS)),
    ('trailing_semicolon', 'Tu-Su 11:00-21:00;', every([['11:00', '21:00']], DAY_NAMES[1:])),
]


@pytest.mark.parametrize('case,expression,weekly', PARSED, ids=[c[0] for c in PARSED])
def test_supported_subset_is_parsed(case, expression, weekly):
    result = parse(expression)
    assert result['state'] == 'parsed' and result['reason_codes'] == [] and result['unparsed'] == []
    assert result['weekly'] == weekly
    assert result['parser_version'] == PARSER_VERSION


PARTIAL = [
    ('holiday_off_rule', 'Mo-Sa 10:00-20:00; PH off', ['PH off'], every([['10:00', '20:00']], DAY_NAMES[:6])),
    ('holiday_with_weekday_selector', 'Mo-Sa 10:00-20:00; Su,PH off', ['Su,PH off'], every([['10:00', '20:00']], DAY_NAMES[:6])),
    ('holiday_offset_left_unparsed', 'Mo-Fr 10:00-18:00; PH +1 day off', ['PH +1 day off'], every([['10:00', '18:00']], WEEKDAYS)),
    ('holiday_as_additional_rule', 'Mo-Fr 10:00-18:00, PH off', ['PH off'], every([['10:00', '18:00']], WEEKDAYS)),
    ('holiday_in_shared_selector', 'Mo-Fr,PH 10:00-18:00', ['Mo-Fr,PH 10:00-18:00'], every([['10:00', '18:00']], WEEKDAYS)),
]


@pytest.mark.parametrize('case,expression,unparsed,weekly', PARTIAL, ids=[c[0] for c in PARTIAL])
def test_holiday_rules_stay_unparsed_while_ordinary_days_are_read(case, expression, unparsed, weekly):
    result = parse(expression)
    assert result['state'] == 'partial' and result['reason_codes'] == ['HOURS_HOLIDAY_UNPARSED']
    assert result['unparsed'] == unparsed and result['weekly'] == weekly


SCHOOL_HOLIDAY = [
    ('school_holiday', 'Mo-Fr 07:00-19:00; SH 09:00-15:00', ['SH 09:00-15:00'], every([['07:00', '19:00']], WEEKDAYS)),
    ('school_holiday_additional_off', 'Mo-Sa 09:00-17:00, SH off', ['SH off'], every([['09:00', '17:00']], DAY_NAMES[:6])),
]


@pytest.mark.parametrize('case,expression,unparsed,weekly', SCHOOL_HOLIDAY, ids=[c[0] for c in SCHOOL_HOLIDAY])
def test_school_holidays_are_flagged_separately_because_they_last_weeks(case, expression, unparsed, weekly):
    result = parse(expression)
    assert result['state'] == 'partial' and result['weekly'] == weekly and result['unparsed'] == unparsed
    assert result['reason_codes'] == ['HOURS_HOLIDAY_UNPARSED', 'HOURS_SCHOOL_HOLIDAY_UNPARSED']
    notes = fact_value(result, 'Europe/Madrid')['notes']
    assert notes[-1] == NOTE_SCHOOL_HOLIDAY and notes[-2] == '해석하지 않은 표기(공휴일 등): ' + unparsed[0]
    assert NOTE_SCHOOL_HOLIDAY not in fact_value(parse('Mo-Sa 10:00-20:00; PH off'), 'Europe/Madrid')['notes']


UNPARSED = [
    ('month_selector', 'Jan-Mar Mo-Fr 10:00-16:00', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('month_override_after_template', 'Mo-Su 10:00-18:00; Dec 10:00-15:00', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('nth_weekday', 'Mo[1] 10:00-12:00', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('week_number', 'week 01-10 Mo-Fr 09:00-17:00', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('open_end', 'Mo-Fr 18:00+', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('open_end_after_span', 'Fr 18:00-23:00+', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('solar_event', 'Mo-Su sunrise-sunset', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('comment', 'Mo-Fr 10:00-18:00 "by appointment"', 'HOURS_COMMENT'),
    ('fallback_rule', 'Mo-Fr 10:00-18:00 || "call ahead"', 'HOURS_COMMENT'),
    ('fallback_rule_without_comment', 'Mo-Fr 10:00-18:00 || unknown', 'HOURS_FALLBACK_RULE'),
    ('lowercase_days', 'mo-fr 10:00-18:00', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('bare_selector_without_time', 'Mo-Fr', 'HOURS_TIME_MISSING'),
    ('open_without_time', 'Mo-Fr open', 'HOURS_AMBIGUOUS_OPEN'),
    ('partial_day_closure', 'Mo 12:00-14:00 off', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('zero_length_span', 'Mo 10:00-10:00', 'HOURS_AMBIGUOUS_TIME'),
    ('invalid_start_hour', 'Mo 25:00-26:00', 'HOURS_INVALID_TIME'),
    ('end_at_or_after_48', 'Mo 20:00-48:00', 'HOURS_INVALID_TIME'),
    ('invalid_minutes', 'Mo 10:75-12:00', 'HOURS_INVALID_TIME'),
    ('holiday_only', 'PH off', 'HOURS_NO_WEEKDAY_RULE'),
    ('free_text', 'by appointment', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('selector_with_24_7', 'Mo-Fr 24/7', 'HOURS_UNSUPPORTED_SYNTAX'),
    ('dangling_comma', 'Mo-Fr 10:00-18:00,', 'HOURS_UNSUPPORTED_SYNTAX'),
]


@pytest.mark.parametrize('case,expression,code', UNPARSED, ids=[c[0] for c in UNPARSED])
def test_unsupported_grammar_is_unparsed_not_guessed(case, expression, code):
    result = parse(expression)
    assert result['state'] == 'unparsed' and result['weekly'] is None
    assert result['reason_codes'] == [code] and result['unparsed']
    assert fact_value(result, 'Asia/Tokyo') is None


@pytest.mark.parametrize('value', [None, '', '   ', 7, ['Mo 10:00-12:00']])
def test_missing_or_non_text_values_are_missing(value):
    result = parse(value)
    assert result['state'] == 'missing' and result['weekly'] is None and fact_value(result, 'Asia/Tokyo') is None


def test_too_many_intervals_and_overlong_values_are_unparsed():
    many = 'Mo ' + ','.join(f'{h:02d}:00-{h:02d}:30' for h in range(9))
    assert parse(many)['reason_codes'] == ['HOURS_TOO_MANY_INTERVALS']
    assert parse('Mo-Fr 10:00-18:00; ' * 20)['reason_codes'] == ['HOURS_TOO_LONG']


def test_unmentioned_days_follow_osm_closed_rule_and_are_disclosed():
    result = parse('Sa-Su 10:00-16:00')
    assert result['unmentioned_days'] == list(WEEKDAYS)
    value = fact_value(result, 'Europe/Madrid')
    assert value['weekly']['monday'] == [] and NOTE_UNMENTIONED in value['notes']
    assert NOTE_UNMENTIONED not in fact_value(parse('24/7'), 'Europe/Madrid')['notes']


def test_parser_is_deterministic_and_does_not_mutate_input():
    text = 'Mo-Fr 11:00-14:00,17:00-22:00; Sa 17:00-23:00; PH off'
    assert parse(text) == parse(text) and text == 'Mo-Fr 11:00-14:00,17:00-22:00; Sa 17:00-23:00; PH off'


ALL_READABLE = [c[1] for c in PARSED] + [c[1] for c in PARTIAL] + [c[1] for c in SCHOOL_HOLIDAY]


@pytest.mark.parametrize('expression', ALL_READABLE)
def test_every_readable_result_passes_the_existing_fact_contract(expression):
    value = fact_value(parse(expression), 'Asia/Tokyo')
    assert value['timezone'] == 'Asia/Tokyo' and value['notes'][0] == NOTE_BASIS
    now = datetime.now(timezone.utc)
    # The existing structured-fact validator is the contract: provisional, 7 days.
    fact = FactInput.model_validate({'field': 'opening_hours', 'value': value, 'status': 'provisional',
                                     'source_key': 'osm', 'checked_at': now, 'expires_at': now + timedelta(days=7)})
    assert fact.status == 'provisional'


def test_partial_result_notes_name_the_unparsed_holiday_rule():
    value = fact_value(parse('Mo-Sa 10:00-20:00; PH off'), 'Asia/Tokyo')
    assert value['notes'][-1] == '해석하지 않은 표기(공휴일 등): PH off'
    assert set(value) == {'timezone', 'weekly', 'notes'}  # no score/verified marker
    # An additional holiday rule names only itself; the weekday part was applied.
    value = fact_value(parse('Mo-Fr 10:00-18:00, PH off'), 'Asia/Tokyo')
    assert value['notes'][-1] == '해석하지 않은 표기(공휴일 등): PH off'
    assert value['weekly']['friday'] == [['10:00', '18:00']]
