"""Conservative, offline extraction of explicit booking facts (no model/network).

This is a basic parser, not a substitute for general natural-language extraction.
Only explicit dates and labelled fields become facts. Everything remains reviewable.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import BookingCreate, local_to_instant
from .repository import DomainError

VERSION = 'local-mail-v1'
MONTHS = {name: number for number, names in enumerate((
    'jan january enero', 'feb february febrero', 'mar march marzo', 'apr april abril',
    'may mayo', 'jun june junio', 'jul july julio', 'aug august agosto',
    'sep sept september septiembre', 'oct october octubre', 'nov november noviembre',
    'dec december diciembre'), 1) for name in names.split()}
_MONTH = '|'.join(sorted(MONTHS, key=len, reverse=True))
_DATE = re.compile(r'(?<!\d)(?:(?P<y>20\d{2})[ \t]*[-/.年년][ \t]*(?P<m>\d{1,2})[ \t]*[-/.月월][ \t]*(?P<d>\d{1,2})[ \t]*[日일]?|(?P<ed>\d{1,2})[ \t]+(?:de[ \t]+)?(?P<em>'+_MONTH+r')\.?[ \t]+(?:de[ \t]+)?(?P<ey>20\d{2})|(?P<am>'+_MONTH+r')\.?[ \t]+(?P<ad>\d{1,2}),?[ \t]+(?P<ay>20\d{2}))(?!\d)', re.I)
_CLOCK = re.compile(r'(?<![\d:])(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*(?P<ap>am|pm))?(?!\d)', re.I)
_ZONE = re.compile(r'\b(?:Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Za-z_+-]+(?:/[A-Za-z_+-]+)?\b')
_LABELS = {
    'checkin': r'チェックイン|체크\s*인|check[ -]?in|entrada',
    'checkout': r'チェックアウト|체크\s*아웃|check[ -]?out|salida',
    'departure': r'출발|departure|depart(?:s|ure)?',
    'arrival': r'도착|arrival|arrives?',
    'pickup': r'픽업|대여\s*일|pick[ -]?up',
    'return': r'반납|drop[ -]?off',
    'visit': r'이용일|방문일|예약일|visit\s*date|date\s*of\s*visit|fecha',
    'meeting': r'집합\s*시간|집합\s*시각|meeting\s*time',
    'starting': r'시작\s*(?:시간|시각)|start\s*time',
    'ending': r'종료\s*(?:예정|시간|시각)|end\s*time',
}


def _dates(text):
    result = []
    for match in _DATE.finditer(text):
        fields = match.groupdict()
        try:
            if fields['y']:
                value = date(int(fields['y']), int(fields['m']), int(fields['d']))
            elif fields['ey']:
                value = date(int(fields['ey']), MONTHS[fields['em'].lower()], int(fields['ed']))
            else:
                value = date(int(fields['ay']), MONTHS[fields['am'].lower()], int(fields['ad']))
        except ValueError:
            continue
        result.append((value.isoformat(), match.start(), match.end()))
        if len(result) > 512:
            raise DomainError('LOCAL_EXTRACTION_UNSUPPORTED', '본문에 날짜가 너무 많아 기본 분석할 수 없습니다. 예약별로 나누어 올려 주세요.', 422)
    return result


_NON_VISIT_DATE = re.compile(r'cancel|refund|payment|pay(?:ment)?\s*due|issued|issue\s*date|booked\s*on|booking\s*(?:made|created|date)|deadline|expiry|expiration|취소|환불|결제|발급|발행|작성|마감|기한|予約受付|申込|発行|取消', re.I)
_TIME_WORDS = re.compile(r'\([A-Za-z가-힣一-龥]{1,12}\)|\b(?:from|by|at|a[ \t]+las)\b|부터|から|まで', re.I)


def _booking_dates(text):
    values = []
    for value in _dates(text):
        before = text[:value[1]]
        context = re.split(r'[\n。;.!]', before)[-1]
        if not context.strip():
            context = before.rstrip().split('\n')[-1] if before.strip() else ''
        if not _NON_VISIT_DATE.search(context):
            values.append(value)
    return values


def _following_clocks(text):
    values = _clocks(text)
    gap = text[:values[0][1]] if values else ''
    return values if values and len(gap) <= 80 and not _TIME_WORDS.sub('', gap).strip(' \t,') else []


def _clocks(text):
    result = []
    for match in _CLOCK.finditer(text):
        hour, minute = int(match['h']), int(match['m'])
        if match['ap']:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if match['ap'].lower() == 'pm' else 0)
        if hour > 23 or minute > 59:
            continue
        result.append((f'{hour:02}:{minute:02}', match.start(), match.end()))
        if len(result) > 1024:
            raise DomainError('LOCAL_EXTRACTION_UNSUPPORTED', '본문의 시간 정보가 너무 많습니다. 예약별로 나누어 올려 주세요.', 422)
    return result


def _zone(text):
    match = _ZONE.search(text)
    if match:
        try:
            ZoneInfo(match[0])
            return match[0]
        except ZoneInfoNotFoundError:
            pass
    return None


def _label_part(text, key):
    """A label applies until the next date/time label, not the rest of the mail."""
    match = re.search(r'(?:^|[\n.。;]\s*)\s*(?:' + _LABELS[key] + r')\s*[:：]?\s*', text, re.I)
    if not match:
        return None
    tail = text[match.end():]
    boundary = re.search(r'\n|(?:' + '|'.join(_LABELS.values()) + r')\s*[:：]', tail, re.I)
    return tail[:boundary.start()] if boundary else tail


def _moment(text, fallback_day=None, *, route=False):
    days = _booking_dates(text or '')
    if days:
        tail = (text or '')[days[0][2]:]
        clocks = _following_clocks(tail)
        if route and not clocks and not _NON_VISIT_DATE.search(tail):
            candidates = _clocks(tail)
            if candidates and re.fullmatch(r'[ \t]*[A-Z]{3}\b[^.;\n]{0,100}', tail[:candidates[0][1]]):
                clocks = candidates
    else:
        clocks = _clocks(text or '') if fallback_day and not _NON_VISIT_DATE.search(text or '') else []
    day = days[0][0] if days else fallback_day
    # Only a clock adjacent to the booking date is a booking time. A later
    # cancellation/payment sentence cannot supply a missing time.
    return (day + 'T' + clocks[0][0] + ':00' if day and clocks else day), _zone(text or '')


def _body(raw):
    # Date-only booking text is not an email header. Conversely a MIME message
    # with headers but no body must not turn its sent date into a booking date.
    if not re.match(r'^(?:From|To|Subject):', raw, re.I):
        return raw.strip(), ''
    lines = raw.splitlines(keepends=True)
    offset, subject = 0, ''
    for line in lines:
        if not line.strip():
            offset += len(line)
            break
        header = re.match(r'^([A-Za-z-]+):[ \t]*(.*)', line)
        if header:
            if header[1].lower() == 'subject': subject = header[2].strip()
            offset += len(line)
        elif line.startswith((' ', '\t')):
            offset += len(line)
        else:
            break
    return raw[offset:].strip(), subject


def _blocks(body):
    # Explicitly numbered independent records; do not split airline legs.
    lines = body.splitlines()
    numbered = [i for i, line in enumerate(lines) if re.match(r'^\s*(?:\d{1,3}[.)]|[A-Z]\))\s+\S', line)]
    starts = [i for i in numbered if _dates(lines[i])]
    if len(starts) >= 2:
        starts = numbered
        return ['\n'.join(lines[start:starts[index + 1] if index + 1 < len(starts) else len(lines)]) for index, start in enumerate(starts)]
    # Blank-line separated, explicitly identified confirmations.
    paragraphs = re.split(r'\n\s*\n', body)
    identified = [paragraph for paragraph in paragraphs if _confirmation(paragraph) and _dates(paragraph)]
    if len(identified) >= 2 and len(set(_confirmation(p) for p in identified)) == len(identified):
        return identified
    return [body]


def _confirmation(text):
    labels = r'예약\s*번호(?:\s*\(PNR\))?|확인\s*번호|予約番号|ご予約番号|booking\s*(?:reference|number|confirmation)|reservation\s*(?:number|reference|no\.?)?|confirmation\s*(?:number|code)?|reference|referencia|PNR|予約'
    match = re.search(r'(?:' + labels + r')\s*[:：#]?\s*([A-Z0-9][A-Z0-9-]{3,79})(?![A-Z0-9])', text, re.I)
    if match and (any(c.isdigit() for c in match[1]) or match[1] == match[1].upper()) and not re.fullmatch(r'20\d{2}', match[1]):
        return match[1].rstrip('.')
    # A standalone alphanumeric confirmation after a sentence/semi-colon is
    # accepted only when it contains both letters and digits (never a price).
    for candidate in re.findall(r'(?:^|[;.。]\s*)([A-Z][A-Z0-9-]{4,79})(?=[.。;]|$)', text, re.M):
        if any(c.isdigit() for c in candidate):
            return candidate
    return None


def _provider(text, subject):
    match = re.search(r'^(?:施設|숙소명|호텔명|업체명|항공사|운영사|제공처|property|hotel\s*name|airline|operator|provider)\s*[:：]?\s*(\S[^\n]{1,280})', text, re.M | re.I)
    if match:
        return re.split(r'[.。]\s*(?:予約|Reservation|Referencia)', match[1], maxsplit=1, flags=re.I)[0].strip().rstrip('.')
    match = re.search(r'\b(?:airline)\s+([^\n.]{2,150})', text, re.I)
    if match:
        return match[1].strip()
    lines = [re.sub(r'^\s*(?:\d+[.)]|[A-Z]\))\s*', '', line).strip() for line in text.splitlines()]
    for line in lines:
        if not line or re.match(r'^(?:SYNTHETIC|실제 예약|Todos los sitios|Dear|안녕|From:|To:|Subject:|Date:)', line, re.I):
            continue
        # A standalone property or leading venue is useful only if it actually
        # names a booking kind; generic prose never becomes a provider.
        if re.search(r'hotel|restaurante|restaurant|café|cafe|カフェ|카페|gallery|museum|museo|식당|食堂|ホテル|旅館|료칸|航空|air\b|렌터카|car rental|tour|투어|visit|stop\s*\d', line, re.I) and not re.search(r'https?://|^(?:예약번호|reference|check[ -]?in|cancellation|refund)', line, re.I):
            leading = re.split(r'[;.]\s|,\s*(?:establishment|establecimiento|same reservation|fictional)|\s+20\d{2}[-年년]', line, maxsplit=1, flags=re.I)[0]
            if 2 <= len(leading) <= 280:
                return re.sub(r'\s*예약이\s*확정되었습니다.*$', '', leading).rstrip('.,;。')
    # Subject contains an explicit property after a common confirmation title.
    match = re.search(r'(?:확정되었습니다|confirmed)\s*[-–]\s*([^()]+)', subject, re.I)
    if match:
        return match[1].strip()[:300]
    match = re.search(r'^\[([^]]+)\]', subject)
    if match and match[1].lower() not in {'test only', 'klook', '예약 확정'}:
        return match[1][:300]
    # An explicit thank-you sentence names an airline/property, never sender.
    match = re.search(r'Thank you for (?:choosing|booking)\s+([^.!\n]+)', text, re.I)
    return match[1].strip()[:300] if match else None


def _kind(text):
    # Explicit stay dates outrank incidental airport-pickup/flight-number prose.
    checkin, checkout = _label_part(text, 'checkin'), _label_part(text, 'checkout')
    if checkin and checkout and _booking_dates(checkin) and _booking_dates(checkout): return '숙소'
    if re.search(r'outbound|flight|항공|航空|항공권', text, re.I): return '항공'
    if re.search(r'check[ -]?in|체크\s*인|チェックイン|hotel|旅館|료칸', text, re.I): return '숙소'
    if re.search(r'car rental|rent.?car|렌터카|반납|drop[ -]?off', text, re.I): return '렌터카'
    if re.search(r'restaurant|restaurante|café|cafe|カフェ|카페|식당|食堂|dinner|夕食', text, re.I): return 'restaurant'
    if re.search(r'tour|투어|museum|museo|gallery|visit|stop\s*\d', text, re.I): return '투어'
    return None


def _party(text):
    match = re.search(r'(?:성인|大人)\s*(\d{1,2})\s*(?:명|名)?|\b(\d{1,2})\s*(?:adults?|adultos?)\b', text, re.I)
    if not match:
        return None
    adults = int(match[1] or match[2])
    if not 1 <= adults <= 50:
        return None
    child = re.search(r'(?:아동|어린이|子ども|子供)\s*(\d{1,2})\s*(?:명|名)?|\b(\d{1,2})\s*(?:children|niños?)\b', text, re.I)
    children = int(child[1] or child[2]) if child else None
    if children is not None and children > 30:
        return None
    return {'adults': adults, 'children': [{'age': None}] * (children or 0), 'children_status': 'present' if children else 'none' if children == 0 else 'unknown'}


def _policy(text):
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.search(r'취소.*(?:규정|정책)|환불.*규정|cancellation\s*(?:policy|conditions)?|キャンセル規定|FARE CONDITIONS', line, re.I)), None)
    if start is None:
        return None
    selected = []
    for line in lines[start:]:
        if selected and (not line.strip() or re.search(r'예약\s*(?:관리|확인)|manage your booking|look forward|본 메일', line, re.I)):
            break
        selected.append(line)
    return '\n'.join(selected)[:12000] or None


def _safe_event(kind, start, end, start_zone, end_zone, location, reasons):
    event = {'event_type': kind, 'start_local': start, 'end_local': end, 'start_timezone': start_zone, 'end_timezone': end_zone, 'location': location}
    for key, zone in [('start_local', start_zone), ('end_local', end_zone)]:
        if event[key] and len(event[key]) > 10 and zone:
            try:
                local_to_instant(event[key], zone)
            except ValueError:
                event[key] = event[key][:10]
                reasons.add('AMBIGUOUS_LOCAL_TIME')
    if event['start_local'] and event['end_local'] and start_zone and end_zone:
        start_i = local_to_instant(event['start_local'], start_zone)
        end_i = local_to_instant(event['end_local'], end_zone)
        if start_i and end_i and end_i < start_i:
            event['end_local'] = event['end_local'][:10]
            reasons.add('EVENT_ORDER_UNCONFIRMED')
    return event


def parse_local_document(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise DomainError('MAIL_BODY_EMPTY', '메일 본문을 읽을 수 없습니다. 텍스트로 저장한 메일을 다시 올려 주세요.', 422)
    if len(raw) > 1024 * 1024:
        raise DomainError('FILE_TOO_LARGE', '메일 본문은 1MB 이내로 올려 주세요.', 422)
    body, subject = _body(raw)
    if not body.strip():
        raise DomainError('MAIL_BODY_EMPTY', '메일 본문이 비어 있습니다. 원문을 확인해 주세요.', 422)
    result, reasons = [], {'BASIC_EXTRACTION_REVIEW'}
    blocks = _blocks(body)
    if len(blocks) > 100:
        raise DomainError('LOCAL_EXTRACTION_UNSUPPORTED', '한 메일의 예약은 100개까지 확인할 수 있습니다.', 422)
    for block in blocks:
        confirmation = _confirmation(block) or (_confirmation(subject) if len(blocks) == 1 else None)
        provider, kind = _provider(block, subject), _kind(block + '\n' + subject)
        days = _booking_dates(block)
        if not days or not (confirmation or provider) or not kind:
            raise DomainError('LOCAL_EXTRACTION_UNSUPPORTED', '기본 분석으로 예약의 날짜와 장소를 확인하지 못했습니다. 원문을 확인해 직접 예약을 추가하거나 상세 분석 설정을 확인해 주세요.', 422)
        location_match = re.search(r'^(?:address|住所|주소|집합[ \t]*장소|location)(?:[ \t　]*[:：][ \t　]*|[ \t　]+)(\S[^\n]{0,990})', block, re.M | re.I)
        location = location_match[1] if location_match else None
        if location and re.match(r'unknown\b|not\s+(?:provided|available|yet)|pending\b|未提供|未定|不明|記載なし|미제공|미확인|없음|불명|확인\s*필요|no\s+(?:disponible|facilitada)', location, re.I):
            location = None
        events = []
        # One confirmation can contain a true round trip, with independent
        # departure/arrival zones and civil dates on either side of the arrow.
        for match in re.finditer(r'^(Outbound|Return)\s*:\s*([^\n]+)', block, re.M | re.I):
            sides = re.split(r'\s*(?:->|→)\s*', match[2], maxsplit=1)
            if len(sides) != 2: continue
            start, sz = _moment(sides[0], route=True); end, ez = _moment(sides[1], start[:10] if start else None, route=True)
            if start and end:
                events.append(_safe_event('outbound' if match[1].lower() == 'outbound' else 'return', start, end, sz, ez, None, reasons))
        if not events:
            pair = ('checkin', 'checkout', 'stay') if kind == '숙소' else ('departure', 'arrival', 'flight') if kind == '항공' else ('pickup', 'return', 'pickup') if kind == '렌터카' else ('starting', 'ending', 'activity')
            if pair:
                first, last = _label_part(block, pair[0]), _label_part(block, pair[1])
                if first and _dates(first):
                    start, sz = _moment(first); end, ez = _moment(last)
                    events.append(_safe_event(pair[2], start, end, sz, ez, location, reasons))
        if not events:
            dated = _label_part(block, 'visit')
            unique = list(dict.fromkeys(day for day, _, _ in days))
            # Multiple unlabelled dates may be booking/issuance/cancellation
            # dates. Preserve an identifiable record without guessing one.
            day = _booking_dates(dated)[0][0] if dated and _booking_dates(dated) else unique[0] if len(unique) == 1 else None
            if day is None:
                reasons.add('DATE_NEEDS_REVIEW')
            first_date = next((v for v in days if v[0] == day), None)
            line = block[first_date[2]:].split('\n', 1)[0] if first_date else ''
            clocks = _following_clocks(line)
            starting = _label_part(block, 'starting')
            if starting: line = starting; clocks = _clocks(line)
            meeting = _label_part(block, 'meeting')
            if meeting: line = meeting; clocks = _clocks(line)
            start = day + 'T' + clocks[0][0] + ':00' if day and clocks else day
            end = None
            # Only a syntactic time range means duration; a later price/policy
            # time must not become the booking's end.
            if len(clocks) > 1 and re.fullmatch(r'\s*[-–~]\s*', line[clocks[0][2]:clocks[1][1]]):
                end = day + 'T' + clocks[1][0] + ':00' if day else None
            ending = _label_part(block, 'ending')
            if day and ending and _clocks(ending): end = day + 'T' + _clocks(ending)[0][0] + ':00'
            events.append(_safe_event('activity', start, end, _zone(block), _zone(block), location, reasons))
        context = block + '\n' + subject
        pending = re.search(r'change request|new requested time|변경\s*요청|変更リクエスト|pending\s*confirmation|awaiting\s*confirmation|not\s+(?:a\s+)?confirmed\s+reservation|confirmed\s+later|확정\s*(?:전|대기)|확인\s*대기', context, re.I)
        cancelled = re.search(r'(?:reservation|booking)\s+(?:has\s+been|was|is)\s+cancelled|cancellation\s+confirmation|예약(?:이|은)?\s*취소(?:되었|되었습니다)|ご予約.{0,10}キャンセル(?:され|済)', context, re.I)
        if pending or cancelled:
            reasons.add('RESERVATION_CANCELLED_REVIEW' if cancelled else 'REQUEST_NOT_CONFIRMATION')
            # This applies to stays/flights as well as activities. Keep the
            # stated dates as review context, but no confirmed-time suggestion.
            for event in events:
                for key in ('start_local', 'end_local'):
                    if event[key]: event[key] = event[key][:10]
        first, last = events[0], events[-1]
        value = {'kind': kind, 'provider': provider, 'confirmation_number': confirmation,
                 'date': first['start_local'][:10] if first['start_local'] else None,
                 'date_end': last['end_local'][:10] if last['end_local'] else None,
                 'time': first['start_local'][11:16] if first['start_local'] and len(first['start_local']) > 10 else None,
                 'time_end': last['end_local'][11:16] if last['end_local'] and len(last['end_local']) > 10 else None,
                 'location': location, 'party': _party(block), 'refund_policy': _policy(block),
                 'raw_snippet': block[:12000], 'status': 'needs_review', 'events': events}
        if value['date'] and value['date_end'] and value['date_end'] < value['date']:
            # Date line crossings are represented by their independent events.
            value['date_end'] = None
        value['stable_item_key'] = hashlib.sha256(json.dumps({'kind': kind, 'provider': provider, 'confirmation': confirmation, 'date': None if confirmation else value['date']}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result.append(BookingCreate.model_validate(value).model_dump())
    return result, sorted(reasons)
