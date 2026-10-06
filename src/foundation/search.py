"""Trip-scoped retrieval; current SQL facts always override derived vector text."""
from dataclasses import dataclass
from contextlib import ExitStack
import json
import re

@dataclass(frozen=True)
class SearchContext:
    user_id: str
    trip_id: str
    trip_version: int
    request_id: str
    session_id: str | None = None


_DETAIL_REQUEST = re.compile(r'환불|취소|수수료|규정|정책|체크인|체크아웃|몇\s*시|예약번호|확인번호|주소|집합\s*장소')
_DATE_REFERENCE = re.compile(r'\d{4}-\d{2}-\d{2}|\d+\s*월\s*\d+\s*일|\d+\s*일\s*차|째\s*날|첫\s*날|마지막\s*날|그\s*날|당일')
_ANAPHORIC_REFERENCE = re.compile(r'그거|그것|그건|그곳|그쪽|거기|아까|(?:그|해당|이)\s*(?:예약|호텔|숙소|투어|항공편)')


def resolve_day(question, trip, history=None, *, listing_only=True):
    """Resolve both listing dates and date-qualified fact questions in SQL.

    The shared calendar parser normally expects a whole-day request. A synthetic
    intent suffix reuses only its date parsing for a detail question; it is never
    sent to a model or persisted as the user's question.
    """
    from src.agent import daily_question_date
    if listing_only and _DETAIL_REQUEST.search(question):
        return None
    if re.search(r'마지막\s*날', question) and (not listing_only or re.search(r'일정|예약|뭐',question)):
        return trip['end_date']
    text = question if listing_only else question + ' 일정 전체'
    day = daily_question_date(text, trip['start_date'], history, trip['end_date'])
    if day and re.search(r'일\s*차|째\s*날|첫\s*날', question) and not trip['start_date'] <= day <= trip['end_date']:
        return None
    return day


def _mentions(text, value):
    """Avoid matching reservation ABC1 inside ABC10; allow Korean particles."""
    return bool(value and re.search(r'(?<![a-z0-9])' + re.escape(value.casefold()) + r'(?![a-z0-9])', text.casefold()))


def _exact_references(text, bookings):
    return [booking for booking in bookings if
            _mentions(text, booking.get('provider')) or
            _mentions(text, booking.get('confirmation_number'))]


def _followup_target(history, bookings, trip, eligible_ids, dated_booking_ids):
    """Use only identities still belonging to this trip, never history facts.

    The most recent identity-bearing turn is decisive. If that turn lists two
    possible bookings, do not silently fall back to an older single-booking turn.
    History is not sent as authority to the answer model and cannot change scope.
    """
    for message in reversed(history):
        matches = _exact_references(message['content'], bookings)
        if not matches:
            continue
        day = resolve_day(message['content'], trip, listing_only=False)
        if day:
            # History dates are only hints for disambiguating referenced IDs.
            # The outer SQL pool still enforces the current question's date/kind.
            day_ids = dated_booking_ids(day)
            matches = [b for b in matches if b['id'] in day_ids]
        matches = [b for b in matches if b['id'] in eligible_ids]
        return matches[0] if len(matches) == 1 else None
    return None


def _clarify_target():
    return {'answer':'어느 예약을 말씀하시는지 확인해 주세요. 장소·제공처 이름이나 예약번호를 알려주시면 해당 예약의 근거로 답할게요.',
            'sources':[], 'tools_used':[]}


def source(booking, trip_id):
    did = booking.get('document_id')
    return {'document_id':did,'booking_id':booking['id'],'source_file':booking.get('source_file') or '직접 입력',
        'provider':booking.get('provider'),'content_url':f'/api/v2/trips/{trip_id}/documents/{did}/content' if did else None}


def facts(booking):
    keys = ('id','kind','provider','confirmation_number','date','date_end','time','time_end','location','refund_policy','status','events')
    return {key:booking.get(key) for key in keys}


def _event_on_day(event, day):
    """A daily answer includes only the event's own known civil-date span.

    Keep departure/arrival local dates independent; a date-line crossing can
    put arrival on the previous civil day. Unknown dates are not assigned a day.
    """
    dates = [event[key][:10] for key in ('start_local', 'end_local') if event.get(key)]
    return bool(dates and min(dates) <= day <= max(dates))


def _event_description(event, kind):
    labels = {'outbound':'출국', 'flight':'항공편', 'stay':'숙박', 'checkin':'체크인',
              'checkout':'체크아웃', 'pickup':'픽업', 'activity':'방문'}
    event_type = event.get('event_type') or '일정'
    if event_type == 'return':
        label = ('반납' if kind in ('렌터카', 'car_rental') else
                 '귀국' if kind in ('항공', 'flight', 'air') else 'return')
    else:
        label = labels.get(event_type, re.sub(r'[\x00-\x1f\x7f]', ' ', event_type))

    def local(key, zone_key):
        value = event.get(key)
        if not value:
            return '시각 미확인'
        if len(value) == 10:
            return f'{value} (시각 미확인)'
        return f"{value} ({event.get(zone_key) or '시간대 미확인'})"

    return f"{label}: {local('start_local', 'start_timezone')} → {local('end_local', 'end_timezone')}"


def _daily_booking_line(booking, day):
    events = [event for event in booking.get('events', []) if _event_on_day(event, day)]
    label = f"- {booking.get('provider') or '이름 미확인'} · {booking.get('kind') or '기타'}"
    if booking.get('status') == 'cancelled':
        label += ' · 취소됨'
    # A round-trip booking's aggregate time can belong to the outbound flight.
    # When there is an event for this day, show its corrected time instead.
    if not events:
        label += f" · {booking.get('time') or '시각 미확인'}"
    label += f" · {booking.get('location') or '장소 미확인'}"
    if events:
        label += '\n  ' + '; '.join(_event_description(event, booking.get('kind')) for event in events)
    return label


def answer(repo, documents, context, question, history, generator=None):
    with ExitStack() as readers:
        return _answer(repo,documents,context,question,history,generator,readers)


def _answer(repo, documents, context, question, history, generator, readers):
    trip = repo.get_trip(context.user_id,context.trip_id)
    day = resolve_day(question,trip,history)
    detail_day = resolve_day(question,trip,history,listing_only=False) if _DATE_REFERENCE.search(question) else None
    if _DATE_REFERENCE.search(question) and not (day or detail_day):
        return {'answer':'여행 기간에 맞는 유효한 날짜 또는 N일차를 알려주세요.','sources':[],'tools_used':[]}
    if day:
        bookings = repo.list_bookings(context.user_id,context.trip_id,date_from=day,date_to=day)
        lines = [f'{day} 예약은 총 {len(bookings)}개입니다.']
        cancelled = sum(row.get('status') == 'cancelled' for row in bookings)
        if cancelled:
            lines[0] += f' 취소된 예약 {cancelled}개가 포함되어 있습니다.'
        lines.extend(_daily_booking_line(row, day) for row in bookings)
        return {'answer':'\n'.join(lines),'sources':[source(b,context.trip_id) for b in bookings],'tools_used':['bookings_on_date']}
    all_rows = repo.list_bookings(context.user_id,context.trip_id)
    eligible = repo.list_bookings(context.user_id,context.trip_id,date_from=detail_day,date_to=detail_day) if detail_day else all_rows
    from src.rag import detect_reservation_type
    kind = detect_reservation_type(question)
    if kind:
        eligible = [b for b in eligible if b.get('kind') == kind]
    eligible_ids = {b['id'] for b in eligible}
    exact = _exact_references(question, all_rows)
    if exact:
        # An explicit provider on another date must not be replaced by a random
        # available provider when the date-qualified exact lookup returns none.
        rows = [b for b in exact if b['id'] in eligible_ids]
    elif _ANAPHORIC_REFERENCE.search(question):
        selected = _followup_target(history, all_rows, trip, eligible_ids,
            lambda value: {b['id'] for b in repo.list_bookings(context.user_id,context.trip_id,date_from=value,date_to=value)})
        if selected is None:
            return _clarify_target()
        rows = [selected]
    elif detail_day:
        # Date-qualified policy/time questions need every matching SQL fact,
        # including manual rows and more than the semantic top-k limit.
        rows = eligible
    else:
        docs = repo.list_documents(context.user_id,context.trip_id)
        active = [d['active_generation_id'] for d in docs if d.get('active_generation_id')]
        document_ids = set()
        if active:
            operations=getattr(documents,'operations',None)
            if operations:
                def guard():
                    documents.ensure_active_user(context.user_id,context.session_id)
                    repo.get_trip(context.user_id,context.trip_id)
                try:
                    reader=readers.enter_context(operations.generations.reader(context.user_id,context.trip_id))
                    from src.config import EMBEDDING_MODEL
                    from .repository import DomainError
                    if reader.generation['embedding_model']!=EMBEDDING_MODEL:
                        raise DomainError('SEARCH_REBUILDING','검색 모델이 변경되어 검색 자료를 복구 중입니다.',503)
                    vector=operations.embed(operations.context(context.user_id,context.trip_id),context.request_id+':query',
                        [question],guard,query=True)[0]
                    hits=reader.query(vector,top_k=5)
                except Exception as exc:
                    from .repository import DomainError
                    if isinstance(exc,DomainError) and exc.code in {'SEARCH_REBUILDING','INDEX_REBUILD_INPUT_REQUIRED'}:
                        operations.jobs.enqueue(context.user_id,context.session_id,'personal_trip',context.trip_id,'reindex',{},
                            trip['version'],'repair:'+str(trip.get('active_index_id'))+':'+str(trip['version']))
                    raise
            else:
                hits = documents.vector(context.trip_id).search(question,top_k=5,where={'generation_id':{'$in':active}})
            document_ids = {hit['metadata']['document_id'] for hit in hits}
        rows = [b for b in eligible if b.get('document_id') in document_ids or b.get('document_id') is None]
    if not rows:
        return {'answer':'이 여행의 예약 자료에서 해당 정보를 찾지 못했습니다.','sources':[],'tools_used':['search_bookings']}
    # Pass only current effective facts, never stale raw chunk times or a tool instruction from mail.
    hits=[{'metadata':{**facts(b),'type':b.get('kind'),'source_file':b.get('source_file') or '직접 입력'},'document':json.dumps(facts(b),ensure_ascii=False),'similarity':1.0} for b in rows]
    from src.rag import generate
    operations=getattr(documents,'operations',None)
    if operations:
        def guard():
            documents.ensure_active_user(context.user_id,context.session_id)
            repo.get_trip(context.user_id,context.trip_id)
        # The API passes the current adapter to preserve request-local test hooks.
        if generator is not None:
            result=operations.fake_call(operations.context(context.user_id,context.trip_id),'generate',context.request_id,
                                       [question,hits],lambda:generator(question,hits),guard)
        else:
            result=operations.generate(context.user_id,context.trip_id,context.request_id,question,hits,guard)
    else:
        result = (generator or generate)(question,hits)
    return {'answer':result,'sources':[source(b,context.trip_id) for b in rows],'tools_used':['search_bookings']}
