"""Load and validate the `mail_gold_v1` synthetic gold set.

One JSON file per case under `evals/mail/gold/`. A field listed in a booking's
`unscored` is ignored by the scorer; any other `null` means "must not be
extracted". The validator rejects anything the scorer could misread.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import PROJECT_ROOT

GOLD_VERSION = 'mail_gold_v1'
GOLD_DIR = PROJECT_ROOT / 'evals' / 'mail' / 'gold'

TYPES = ('flight', 'stay', 'car_rental', 'tour', 'restaurant')
STATUSES = ('confirmed', 'changed', 'cancelled')
LANGUAGES = ('en', 'ja', 'es', 'ko', 'mixed')
BOOKING_FIELDS = ('type', 'provider_name', 'start_local', 'start_tz', 'end_local', 'end_tz',
                  'party', 'amount', 'currency', 'deposit', 'confirmation_code', 'status')
# Scored fields; party is scored as two independent values.
SCORED_FIELDS = ('type', 'provider_name', 'start_local', 'start_tz', 'end_local', 'end_tz',
                 'party.adults', 'party.children', 'amount', 'currency', 'deposit',
                 'confirmation_code', 'status')
FORBIDDEN_RULES = ('payment_date_as_visit', 'deadline_as_visit', 'change_as_new_confirmed',
                   'deposit_as_total', 'cancelled_as_active', 'injection_followed',
                   'midnight_invented', 'city_substituted')
DECOY_FIELDS = ('type', 'provider_name', 'start_local', 'start_tz', 'end_local', 'end_tz',
                'amount', 'currency', 'deposit', 'confirmation_code', 'status', 'booking_count')
CASE_KEYS = {'case_id', 'file', 'language', 'tags', 'synthetic', 'source', 'expected_bookings',
             'must_review', 'forbidden', 'decoys', 'notes'}
_LOCAL = re.compile(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?')
_CASE_ID = re.compile(r'[a-z0-9][a-z0-9-]{2,80}')
_AMOUNT = re.compile(r'\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?')


class GoldError(ValueError):
    pass


def _fail(case_id, message):
    raise GoldError(f'{case_id}: {message}')


def _check_local(case_id, field, value):
    if value is None:
        return
    if not isinstance(value, str) or not _LOCAL.fullmatch(value):
        _fail(case_id, f'{field}는 YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM 이어야 합니다.')


def _check_zone(case_id, field, value):
    if value is None:
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        _fail(case_id, f'{field}는 IANA 시간대여야 합니다.')


def validate_booking(case_id, booking):
    if not isinstance(booking, dict):
        _fail(case_id, '예약은 객체여야 합니다.')
    missing = set(BOOKING_FIELDS) - set(booking)
    extra = set(booking) - set(BOOKING_FIELDS) - {'unscored'}
    if missing or extra or 'unscored' not in booking:
        _fail(case_id, f'예약 필드가 다릅니다. 누락 {sorted(missing)} 추가 {sorted(extra)} (unscored 필수)')
    unscored = booking['unscored']
    if not isinstance(unscored, list) or not set(unscored) <= set(SCORED_FIELDS) | {'party'}:
        _fail(case_id, 'unscored에는 채점 필드 이름만 넣을 수 있습니다.')
    if booking['type'] is not None and booking['type'] not in TYPES:
        _fail(case_id, f"type은 {TYPES} 중 하나여야 합니다.")
    if booking['status'] is not None and booking['status'] not in STATUSES:
        _fail(case_id, f"status는 {STATUSES} 중 하나여야 합니다.")
    for field in ('start_local', 'end_local'):
        _check_local(case_id, field, booking[field])
    for field in ('start_tz', 'end_tz'):
        _check_zone(case_id, field, booking[field])
    party = booking['party']
    if party is not None:
        if not isinstance(party, dict) or set(party) != {'adults', 'children'}:
            _fail(case_id, 'party는 {"adults","children"} 객체여야 합니다.')
        for key in ('adults', 'children'):
            value = party[key]
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                _fail(case_id, f'party.{key}는 0 이상의 정수 또는 null이어야 합니다.')
    for field in ('amount', 'deposit'):
        value = booking[field]
        if value is not None and (not isinstance(value, str) or not _AMOUNT.fullmatch(value)):
            _fail(case_id, f'{field}는 "480" 또는 "25-35" 형식의 문자열이어야 합니다.')
    if booking['currency'] is not None and not re.fullmatch(r'[A-Z]{3}', str(booking['currency'])):
        _fail(case_id, 'currency는 ISO 4217 3자여야 합니다.')
    for field in ('provider_name', 'confirmation_code'):
        if booking[field] is not None and (not isinstance(booking[field], str) or not booking[field].strip()):
            _fail(case_id, f'{field}는 비어 있지 않은 문자열 또는 null이어야 합니다.')


def validate_case(case, *, root: Path = PROJECT_ROOT):
    """Return the case unchanged, or raise GoldError naming the first problem."""
    case_id = case.get('case_id') if isinstance(case, dict) else None
    if not isinstance(case_id, str) or not _CASE_ID.fullmatch(case_id):
        raise GoldError(f'{case_id!r}: case_id는 소문자·숫자·하이픈이어야 합니다.')
    missing = {'case_id', 'file', 'language', 'tags', 'synthetic', 'expected_bookings',
               'must_review', 'forbidden'} - set(case)
    extra = set(case) - CASE_KEYS
    if missing or extra:
        _fail(case_id, f'사례 필드가 다릅니다. 누락 {sorted(missing)} 추가 {sorted(extra)}')
    if case['synthetic'] is not True:
        _fail(case_id, 'synthetic: true 가 필요합니다.')
    if case['language'] not in LANGUAGES:
        _fail(case_id, f'language는 {LANGUAGES} 중 하나여야 합니다.')
    if not isinstance(case['tags'], list) or not all(isinstance(tag, str) for tag in case['tags']):
        _fail(case_id, 'tags는 문자열 배열이어야 합니다.')
    path = (root / case['file']).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        _fail(case_id, f"원문 파일이 없습니다: {case['file']}")
    if path.suffix.lower() not in ('.eml', '.txt'):
        _fail(case_id, '원문은 .eml 또는 .txt 여야 합니다.')
    bookings = case['expected_bookings']
    if not isinstance(bookings, list):
        _fail(case_id, 'expected_bookings는 배열이어야 합니다.')
    for booking in bookings:
        validate_booking(case_id, booking)
    for item in case['must_review']:
        if not isinstance(item, dict) or set(item) - {'booking', 'field', 'reason'} or {'booking', 'field'} - set(item):
            _fail(case_id, 'must_review 항목은 {booking, field, reason} 이어야 합니다.')
        if not isinstance(item['booking'], int) or not 0 <= item['booking'] < len(bookings):
            _fail(case_id, 'must_review.booking이 expected_bookings 범위를 벗어났습니다.')
        if item['field'] not in SCORED_FIELDS:
            _fail(case_id, f"must_review.field '{item['field']}'는 채점 필드가 아닙니다.")
    forbidden = case['forbidden']
    decoys = case.get('decoys', {})
    if not isinstance(forbidden, list) or not set(forbidden) <= set(FORBIDDEN_RULES):
        _fail(case_id, f'forbidden은 {FORBIDDEN_RULES} 중에서만 고릅니다.')
    if not isinstance(decoys, dict) or set(decoys) != set(forbidden):
        _fail(case_id, 'forbidden 규칙마다 decoys 조건이 정확히 하나씩 있어야 합니다.')
    for rule, decoy in decoys.items():
        if not isinstance(decoy, dict) or decoy.get('field') not in DECOY_FIELDS:
            _fail(case_id, f'decoys.{rule}.field가 올바르지 않습니다.')
        if decoy['field'] == 'booking_count':
            if set(decoy) != {'field', 'max'} or not isinstance(decoy['max'], int):
                _fail(case_id, f'decoys.{rule}: booking_count는 max 정수만 가집니다.')
        elif set(decoy) != {'field', 'values'} or not decoy['values'] or not all(isinstance(v, str) and v for v in decoy['values']):
            _fail(case_id, f'decoys.{rule}: values는 비어 있지 않은 문자열 배열이어야 합니다.')
    return case


def load_gold(directory: Path | str = GOLD_DIR, *, root: Path = PROJECT_ROOT) -> list[dict]:
    directory = Path(directory)
    cases = []
    for path in sorted(directory.glob('*.json')):
        case = json.loads(path.read_text(encoding='utf-8'))
        if path.stem != case.get('case_id'):
            raise GoldError(f'{path.name}: 파일 이름과 case_id가 다릅니다.')
        cases.append(validate_case(case, root=root))
    ids = [case['case_id'] for case in cases]
    if len(ids) != len(set(ids)):
        raise GoldError('case_id가 중복됩니다.')
    return cases


def gold_content_sha256(directory: Path | str = GOLD_DIR) -> str:
    """Hash of the gold files' content (expected values, forbidden/decoys,
    must_review ...), normalized JSON sorted by case_id. The gate pins it, so a
    weaker answer under the same case_id fails instead of passing silently."""
    import hashlib
    cases = sorted((json.loads(path.read_text(encoding='utf-8')) for path in Path(directory).glob('*.json')),
                   key=lambda case: str(case.get('case_id')))
    text = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def summary(cases: list[dict]) -> dict:
    """Composition table for reports: languages, tags, sources, bookings."""
    by_language, by_tag, by_source = {}, {}, {}
    for case in cases:
        by_language[case['language']] = by_language.get(case['language'], 0) + 1
        for tag in case['tags']:
            by_tag[tag] = by_tag.get(tag, 0) + 1
        source = case.get('source', 'unknown').split(':')[0]
        by_source[source] = by_source.get(source, 0) + 1
    return {'version': GOLD_VERSION, 'cases': len(cases), 'synthetic': all(c['synthetic'] for c in cases),
            'bookings': sum(len(c['expected_bookings']) for c in cases),
            'by_language': dict(sorted(by_language.items())), 'by_source': dict(sorted(by_source.items())),
            'by_tag': dict(sorted(by_tag.items()))}


if __name__ == '__main__':
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else GOLD_DIR
    print(json.dumps(summary(load_gold(target)), ensure_ascii=False, indent=2))
