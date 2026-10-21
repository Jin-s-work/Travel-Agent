"""Deterministic grounding check for generated trip answers (service v6, bundle F).

A generated answer is trusted only when every checkable atom in it -- dates,
times, money with a currency, reservation numbers and head counts -- also
appears in the facts this very request handed to the generator. Those facts
are the current SQL effective values (user corrections applied), so an answer
that repeats a superseded original value is ungrounded by construction.

No model is called here. Normalisation is purely textual: zero padded times,
thousands separators removed, currency symbols and words mapped to ISO codes.
Atom text never leaves this module in a response; callers get counts only.

What ``verified`` means -- and does not mean: no *checkable* atom in the answer
contradicts the facts. Checkable atoms are dates, clock times, ISO instants,
money (with or without a currency), percentages, "N일/시간 전" deadlines,
reservation numbers and head counts. Free-text claims without such an atom
("환불 가능합니다") are not checked, so ``verified`` is not answer correctness;
the question-set evaluation judges accuracy separately.

Only allow-listed fact fields are indexed (see ``FACT_FIELDS``). UTC instants,
time-zone names, internal ids and file names are never a source for a bare
date or time: a Tokyo 15:00 check-in must not ground "06:00" (its UTC hour).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

import json
from datetime import datetime, timezone

__all__ = ['Atom', 'GroundingCheck', 'FACT_FIELDS', 'EVENT_FIELDS', 'extract_atoms', 'fact_index', 'check',
           'strip_source_lines']

# Fields of ``search.facts(booking)`` (plus the hit's ``type``) that may ground
# an atom. ``id``, ``source_file`` and every ``*_timezone`` stay out.
FACT_FIELDS = ('kind', 'type', 'provider', 'confirmation_number', 'date', 'date_end', 'time', 'time_end',
               'location', 'refund_policy', 'status', 'events', 'party')
# ``*_instant`` values are indexed, but they only yield ``instant`` atoms (an
# ISO timestamp with an offset), never a bare date or time.
EVENT_FIELDS = ('event_type', 'start_local', 'end_local', 'location', 'start_instant', 'end_instant')
# Keys dropped when a free-form JSON document (trip agent tool output) is walked.
_NOISE_KEY = re.compile(r'(?:^|_)(?:id|timezone|source_file|content_url|sources|version|hash)$', re.I)


@dataclass(frozen=True)
class Atom:
    kind: str            # date | time | instant | money | number | percent | before | reservation | party
    keys: frozenset      # any one of these normalised keys grounds the atom


@dataclass(frozen=True)
class GroundingCheck:
    text: str                       # answer with model-written source lines removed
    atoms: tuple                    # every atom found in ``text``
    ungrounded: tuple               # atoms with no matching fact

    @property
    def verified(self):
        return not self.ungrounded


# --- source lines ---------------------------------------------------------
# The model is told to finish with "출처: provider (예약번호 X)". Sources are
# attached by code from SQL rows, so that line is neither shown nor checked.
_SOURCE_LINE = re.compile(r'^[ \t>*\-•]*(?:\*\*)?[ \t]*(?:출처|근거[ \t]*출처|sources?)[ \t]*(?:\*\*)?[ \t]*[:：]', re.I)
# Mid-line, only a bracketed "(출처: …)" at the very end of the line is removed;
# "Open source: 09:00" stays and is checked.
_SOURCE_TAIL = re.compile(r'[ \t]*[(\[][ \t]*(?:출처|sources?)[ \t]*[:：][^()\[\]\n]*[)\]][ \t]*[.。]?[ \t]*$', re.I)


def strip_source_lines(text):
    lines = []
    for line in str(text).splitlines():
        if _SOURCE_LINE.match(line):
            continue
        lines.append(_SOURCE_TAIL.sub('', line))
    return '\n'.join(lines).strip()


# --- atom patterns ----------------------------------------------------------
_MONTHS = {name: index for index, names in enumerate((
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'), ('may',),
    ('jun', 'june'), ('jul', 'july'), ('aug', 'august'), ('sep', 'sept', 'september'),
    ('oct', 'october'), ('nov', 'november'), ('dec', 'december')), start=1) for name in names}
# Title case or upper case only, so the English verb "may" is not a month.
_MONTH_ALT = '|'.join(sorted({form for name in _MONTHS for form in (name.title(), name.upper())}, key=len, reverse=True))

_DATE_PATTERNS = (
    ('ymd', re.compile(r'(?<![\d.])(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?![\d])')),
    ('ymd', re.compile(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일')),
    ('md', re.compile(r'(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일')),
    ('en_md', re.compile(r'(?<![A-Za-z])(' + _MONTH_ALT + r')\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?(?![\d])')),
    ('en_dm', re.compile(r'(?<![\d.])(\d{1,2})(?:st|nd|rd|th)?\s+(' + _MONTH_ALT + r')\.?(?:,?\s*(\d{4}))?(?![A-Za-z])')),
    ('slash', re.compile(r'(?<![\d/.])(\d{1,2})/(\d{1,2})(?![\d/])')),
)

_KO_PM = {'오후', '저녁', '밤', '낮'}
_KO_AM = {'오전', '새벽', '아침'}
_TIME_PATTERNS = (
    ('colon', re.compile(r'(?:(오전|오후|새벽|아침|저녁|밤|낮)\s*)?(?<![\d:])(\d{1,2}):(\d{2})(?::\d{2})?(?:\s*([AaPp])\.?[Mm]\.?(?![A-Za-z]))?(?![\d:])')),
    # "2 시티투어", "3시간", "5시내" are not clock times: a spaced "N 시" must not
    # run into another Hangul syllable, and common 시-words are excluded.
    ('ko', re.compile(r'(?:(오전|오후|새벽|아침|저녁|밤|낮)\s*)?(?<!\d)(\d{1,2})(?:시(?!간|티|내|청|설|즌|트|리즈|드니)|\s+시(?![가-힣]))'
                      r'(?:\s*(\d{1,2})\s*분|\s*(반))?')),
    ('ampm', re.compile(r'(?<![\d:])(\d{1,2})\s*([AaPp])\.?[Mm]\.?(?![A-Za-z])')),
)

_SYMBOLS = {'US$': 'USD', 'A$': 'AUD', 'C$': 'CAD', 'HK$': 'HKD', 'S$': 'SGD', 'NT$': 'TWD',
            '$': 'USD', '¥': 'JPY', '￥': 'JPY', '€': 'EUR', '£': 'GBP', '₩': 'KRW', '฿': 'THB'}
_WORDS = {'엔': 'JPY', '유로': 'EUR', '달러': 'USD', '원': 'KRW', '파운드': 'GBP', '위안': 'CNY', '바트': 'THB'}
_CODES = ('JPY', 'EUR', 'USD', 'KRW', 'GBP', 'CNY', 'THB', 'TWD', 'HKD', 'SGD', 'AUD', 'CAD',
          'CHF', 'VND', 'PHP', 'MYR', 'IDR', 'NZD')
_NUMBER = r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)'
_SYMBOL_ALT = '|'.join(re.escape(symbol) for symbol in sorted(_SYMBOLS, key=len, reverse=True))
_CODE_ALT = '|'.join(_CODES)
_WORD_ALT = '|'.join(_WORDS)
_MONEY_PATTERNS = (
    # "1만 5천원" / "1만 5000원" before the single-multiplier word form.
    ('word_combo', re.compile(r'(?<![\d.,])(\d+)\s*만\s*(\d{1,4})\s*(천)?\s*(' + _WORD_ALT + r')')),
    ('symbol', re.compile(r'(' + _SYMBOL_ALT + r')\s*' + _NUMBER + r'(?![\d])')),
    ('code_first', re.compile(r'(?<![A-Za-z])(' + _CODE_ALT + r')\s*' + _NUMBER + r'(?![\d])')),
    ('code_last', re.compile(r'(?<![\d.,])' + _NUMBER + r'\s*(' + _CODE_ALT + r')(?![A-Za-z])')),
    ('word', re.compile(r'(?<![\d.,])' + _NUMBER + r'\s*(만|천)?\s*(엔|유로|달러|원|파운드|위안|바트)')),
)

_PARTY_PATTERNS = (
    re.compile(r'(?:성인|어른|아동|어린이|소아|유아|총)?\s*(?<![\d.])(\d{1,3})\s*명'),
    re.compile(r'(?<![\d.])(\d{1,3})\s*(?:adults?|children|child|persons?|people|guests?|pax)(?![A-Za-z])', re.I),
)

# An ISO timestamp with an offset is one ``instant`` atom (compared in UTC). It
# is scanned first so its UTC date/hour and "+00:00" never become bare atoms.
_INSTANT = re.compile(r'(?<![\d])(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:\d{2})(?![\d])')

# Currency-less amounts are counted only in thousands-grouped form ("15,000"),
# so years and bare small numbers are not treated as money.
_GROUPED_NUMBER = re.compile(r'(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?![\d,])')
_PERCENT = re.compile(r'(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:%|퍼센트|percent(?![A-Za-z]))', re.I)
_BEFORE_PATTERNS = (
    re.compile(r'(?<![\d.])(\d{1,3})\s*(일|시간)\s*(?:전|이전)'),
    re.compile(r'(?<![\d.])(\d{1,3})\s*(days?|hours?|hrs?)\s+(?:before|prior)(?![A-Za-z])', re.I),
)

# A token right after a reservation label is a reservation number whatever its
# case ("예약번호 abc12345"). It needs a digit and at least four characters.
_RESERVATION_LABELLED = re.compile(
    r'(?:예약\s*번호|확인\s*번호|예약\s*코드|booking|confirmation|reservation)'
    r'(?:\s*(?:number|no\.?|code|ref(?:erence)?))?\s*[:：#]?\s*(?:은|는|이|가)?\s*'
    r'(?<![A-Za-z0-9_\-])((?=[A-Za-z0-9\-]*\d)[A-Za-z0-9][A-Za-z0-9\-]{2,}[A-Za-z0-9])(?![A-Za-z0-9_\-])', re.I)
# Unlabelled: letters, digits and hyphens, at least four characters, at least
# one digit, and no lower case unless an upper-case letter is present too
# ("ABC-1234", "Ab12345"). All-lower tokens and underscore ids do not count.
_RESERVATION = re.compile(r'(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9\-]*\d)[A-Za-z0-9][A-Za-z0-9\-]{2,}[A-Za-z0-9](?![A-Za-z0-9_\-])')


def _date_key(year, month, day):
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    tail = f'{month:02d}-{day:02d}'
    return frozenset({f'{year:04d}-{tail}'}) if year else frozenset({tail})


def _time_keys(hour, minute, marker=None, ampm=None):
    if minute > 59 or hour > 24:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        hour = hour % 12 + (12 if ampm.lower() == 'p' else 0)
        return frozenset({f'{hour:02d}:{minute:02d}'})
    if marker in ('밤', '저녁') and hour == 12:
        hour = 0  # "밤 12시" is midnight; "낮/오후 12시" is noon
    elif marker in _KO_PM:
        hour = hour + 12 if hour < 12 else hour
    elif marker in _KO_AM:
        hour = 0 if hour == 12 else hour
    elif marker is None and 1 <= hour <= 11:
        return None  # ambiguous: resolved by the caller with both readings
    if hour > 23:
        return None
    return frozenset({f'{hour:02d}:{minute:02d}'})


def _amount(text, multiplier=None):
    try:
        value = Decimal(text.replace(',', ''))
    except InvalidOperation:
        return None
    if multiplier == '만':
        value *= 10000
    elif multiplier == '천':
        value *= 1000
    normal = format(value.normalize(), 'f')
    return normal.rstrip('0').rstrip('.') if '.' in normal else normal


def _mask(text, start, end):
    return text[:start] + ' ' * (end - start) + text[end:]


def _instant(match):
    date, hour, minute, second, offset = match.groups()
    try:
        value = datetime.fromisoformat(f"{date}T{hour}:{minute}:{second or '00'}{'+00:00' if offset == 'Z' else offset}")
    except ValueError:
        return None
    return Atom('instant', frozenset({value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}))


def extract_atoms(text):
    """Return atoms found in ``text``; matched spans are masked so a date is
    never re-read as a reservation number or a head count."""
    work, atoms = str(text or ''), []

    def scan(pattern, convert):
        nonlocal work
        for match in list(pattern.finditer(work)):
            atom = convert(match)
            if atom is not None:
                atoms.append(atom)
                work = _mask(work, match.start(), match.end())

    scan(_INSTANT, _instant)

    for style, pattern in _DATE_PATTERNS:
        def date(match, style=style):
            groups = match.groups()
            if style == 'ymd':
                keys = _date_key(int(groups[0]), int(groups[1]), int(groups[2]))
            elif style == 'md':
                keys = _date_key(None, int(groups[0]), int(groups[1]))
            elif style == 'en_md':
                keys = _date_key(int(groups[2]) if groups[2] else None, _MONTHS[groups[0].lower()], int(groups[1]))
            elif style == 'en_dm':
                keys = _date_key(int(groups[2]) if groups[2] else None, _MONTHS[groups[1].lower()], int(groups[0]))
            else:
                keys = _date_key(None, int(groups[0]), int(groups[1]))
            return Atom('date', keys) if keys else None
        scan(pattern, date)

    for style, pattern in _TIME_PATTERNS:
        def clock(match, style=style):
            if style == 'colon':
                marker, hour, minute, ampm = match.group(1), int(match.group(2)), int(match.group(3)), match.group(4)
            elif style == 'ko':
                marker, hour, ampm = match.group(1), int(match.group(2)), None
                minute = 30 if match.group(4) else int(match.group(3) or 0)
            else:
                marker, hour, minute, ampm = None, int(match.group(1)), 0, match.group(2)
            keys = _time_keys(hour, minute, marker, ampm)
            if keys is None and marker is None and not ampm and 1 <= hour <= 11 and minute <= 59:
                # "9시" / "9:30" without a marker: either reading may be grounded.
                keys = frozenset({f'{hour:02d}:{minute:02d}', f'{hour + 12:02d}:{minute:02d}'}) if style == 'ko' \
                    else frozenset({f'{hour:02d}:{minute:02d}'})
            return Atom('time', keys) if keys else None
        scan(pattern, clock)

    for style, pattern in _MONEY_PATTERNS:
        def money(match, style=style):
            if style == 'word_combo':
                rest = int(match.group(2)) * (1000 if match.group(3) else 1)
                code, value = _WORDS[match.group(4)], _amount(str(int(match.group(1)) * 10000 + rest))
            elif style == 'symbol':
                code, value = _SYMBOLS[match.group(1)], _amount(match.group(2))
            elif style == 'code_first':
                code, value = match.group(1), _amount(match.group(2))
            elif style == 'code_last':
                code, value = match.group(2), _amount(match.group(1))
            else:
                code, value = _WORDS[match.group(3)], _amount(match.group(1), match.group(2))
            return Atom('money', frozenset({f'{code} {value}'})) if value is not None else None
        scan(pattern, money)

    scan(_PERCENT, lambda match: Atom('percent', frozenset({_amount(match.group(1))})))
    for pattern in _BEFORE_PATTERNS:
        scan(pattern, lambda match: Atom('before', frozenset({
            ('d' if match.group(2).lower().startswith(('일', 'day')) else 'h') + str(int(match.group(1)))})))
    scan(_GROUPED_NUMBER, lambda match: Atom('number', frozenset({_amount(match.group(1))})))

    for pattern in _PARTY_PATTERNS:
        scan(pattern, lambda match: Atom('party', frozenset({str(int(match.group(1)))})))

    scan(_RESERVATION_LABELLED, lambda match: Atom('reservation', frozenset({match.group(1).casefold()})))

    def reservation(match):
        token = match.group(0)
        if any(c.islower() for c in token) and not any(c.isupper() for c in token):
            return None  # all-lower ids ("abc123") only count after a label
        return Atom('reservation', frozenset({token.casefold()}))
    scan(_RESERVATION, reservation)
    return atoms


def _strings(value, party):
    """Yield strings of a free-form JSON value, skipping noise keys; head counts
    found in ``party`` dicts are added to ``party``."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if _NOISE_KEY.search(str(key)):
                continue
            if key == 'party':
                _party_counts(item, party)
                continue
            yield from _strings(item, party)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item, party)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def _party_counts(party, keys):
    """``{'adults': 2, 'children': [..] | 1}`` -> adults, children, total.

    ``search.facts()`` has no party, so on the /ask path this only matters for
    hits that carry one (trip agent ``list_bookings`` output)."""
    if not isinstance(party, dict):
        return
    adults, children = party.get('adults'), party.get('children')
    children = len(children) if isinstance(children, list) else children
    counts = [value for value in (adults, children) if isinstance(value, int) and not isinstance(value, bool)]
    keys.update(str(value) for value in counts)
    if len(counts) == 2:
        keys.add(str(sum(counts)))


def _allowed(metadata, party):
    """Strings of allow-listed ``search.facts()`` fields only."""
    for key in FACT_FIELDS:
        value = metadata.get(key)
        if key == 'party':
            _party_counts(value, party)
        elif key == 'events':
            for event in value if isinstance(value, list) else []:
                if isinstance(event, dict):
                    yield from (event[field] for field in EVENT_FIELDS if isinstance(event.get(field), str))
        elif isinstance(value, str):
            yield value


@dataclass(frozen=True)
class FactIndex:
    keys: dict          # kind -> set of normalised keys
    text: str           # casefolded fact text for reservation-number lookup


_KINDS = ('date', 'time', 'instant', 'money', 'number', 'percent', 'before', 'reservation', 'party')


def _hit_strings(hit, party):
    """Structured hits (``search`` facts in ``metadata``) use the allow list
    and ignore ``document``, which is the same facts serialised with ids and
    UTC instants. A hit without metadata (trip agent tool output) is read from
    ``document``: JSON is walked without noise keys, plain text is kept."""
    metadata = hit.get('metadata') or {}
    if metadata:
        yield from _allowed(metadata, party)
        return
    document = hit.get('document') or ''
    try:
        parsed = json.loads(document)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        yield from _strings(parsed, party)
    else:
        yield str(document)


def fact_index(hits):
    """Index exactly the facts handed to the generator for this request."""
    keys = {kind: set() for kind in _KINDS}
    blobs = []
    for hit in hits:
        for text in _hit_strings(hit, keys['party']):
            blobs.append(text)
            for atom in extract_atoms(text):
                keys[atom.kind].update(atom.keys)
    # A full fact date also grounds a year-less mention of the same day.
    keys['date'].update({key[5:] for key in keys['date'] if len(key) == 10})
    # A fact amount, with or without currency, grounds a currency-less mention.
    keys['number'].update(key.split(' ', 1)[1] for key in keys['money'])
    return FactIndex(keys, '\n'.join(blobs).casefold())


def _grounded(atom, index):
    if atom.keys & index.keys[atom.kind]:
        return True
    if atom.kind == 'reservation':
        # Same boundary rule as search._mentions: ABC1 never matches ABC10.
        return any(re.search(r'(?<![a-z0-9])' + re.escape(key) + r'(?![a-z0-9])', index.text) for key in atom.keys)
    return False


def check(answer, hits):
    """Strip model-written source lines, then compare every atom with facts."""
    text = strip_source_lines(answer)
    atoms = tuple(extract_atoms(text))
    index = fact_index(hits)
    return GroundingCheck(text, atoms, tuple(atom for atom in atoms if not _grounded(atom, index)))
