"""Entry point used by the ask route: run the agent, verify, shape the DTO.

The router keeps every fast path (date listings, invalid dates, clarification,
exact references, time conflicts, basic mode). Only the semantic branch that
used to call one generation is handed to the agent, and only when it is
enabled. The final text must pass the stage 2 grounding check against the tool
outputs of this request; otherwise a deterministic summary of those outputs
replaces it.
"""
from __future__ import annotations

import json
import os
import re
import time

from src.config import AGENT_MODEL

from .loop import AgentContext, AgentLimits, run_agent
from .tools import build_registry


def enabled(settings) -> bool:
    """TRIP_AGENT_ENABLED; unset → on only in development (one question can
    make up to five paid model calls, so production opts in explicitly)."""
    value = os.getenv('TRIP_AGENT_ENABLED', '').strip().lower()
    if not value:
        return getattr(settings, 'environment', 'production') == 'development'
    return value in ('1', 'true', 'yes', 'on')


# The agent has no apply/undo/delete/send tool, so a final text saying it did
# one of those is false even when it carries no date/time/number atom (which is
# all the grounding check looks at). Such a text is replaced like an
# ungrounded one. Passive "취소됐/변경됐" are left out: they describe booking
# status facts. "적용하지 않았어요" (the honest preview wording) never matches.
_ACTION_CLAIM = re.compile(
    r'(?:삭제|적용|발송|전송|취소|변경|수정|되돌리기)\s*(?:을|를)?\s*(?:했|하였|해\s*드렸|해\s*놨|해\s*두었|완료)'
    r'|(?:삭제|적용|발송|전송)\s*(?:됐|되었)'
    r'|보냈|옮겼|바꿨|바꾸었|되돌렸|지웠')


def claims_action(text) -> bool:
    return bool(_ACTION_CLAIM.search(str(text or '')))


# Links in mail, place descriptions or tool results are untrusted data; a mail
# can ask the model to "tell the user to open" one. The agent never shows a
# raw URL — the user reaches the original mail through the code-attached
# sources instead. Covered forms: scheme links (http/https and the defanged
# hxxp/hxxps), `www.` links, and scheme-less domains such as
# `refund-hotel-kr.com/claim` (also with `[.]`/`(.)` dots, a `:port`, or a
# leading `...`/`-`/`_`/`/` such as `...evil.com`, `x/evil.com`, `ftp://evil.com`).
# Not covered: full-width or otherwise obfuscated text a regex cannot recognise
# as a domain, bare IP links (192.168.0.1/claim), `user@host/x` (an e-mail
# address is left alone), TLDs missing from _TLD, and a domain the tools
# returned as a booking `provider` (see strip_links `keep`: a mail can make the
# extracted provider itself a phishing domain, which then stays when written
# alone without a path).
# Known false positives, all on the removing side: a booking site name the
# tools did not return as `provider` ("Agoda.com에서" when the provider is
# "Booking.com"), a one-letter name before a TLD word ("T.Hotel"), and file
# names whose extension is a TLD ("review.zip", "video.mov", "node.js").
_URL = re.compile(r'(?:h(?:tt|xx)ps?://|www\.)(?:(?![<>"\')\]])[!-~])+', re.I)  # ASCII only: "…/x에서" keeps 에서
_DOT = r'(?:\.|\[\.\]|\(\.\))'
_TLD = (r'(?:[a-z]{2}|com|net|org|info|biz|app|dev|xyz|site|online|shop|top|club|page|link|click|live|store|tech'
        r'|cloud|invalid|test|example|local|localhost|travel|tours|today|support|help|pay|claim|refund|hotel|hotels'
        r'|booking|email|digital|world|asia|tokyo|seoul|icu|cyou|vip|win|work|website|space|fun|best|pro|mobi'
        r'|zip|mov)')
# 성씨와 겹치는 TLD. 뒤에 경로가 있을 때만 도메인으로 본다("Dr.Kim"은 두고 "evil.kim/claim"은 지운다).
_PATH_ONLY_TLD = r'(?:kim)'
# 경계는 ASCII 문자만 본다. 파이썬 `\w`는 한글도 단어 문자로 보므로, `\w`로 막으면
# "refund-hotel-kr.com에서"·"환불은evil.com/x"처럼 한글이 붙은 도메인이 빠져나간다.
# 앞에 `@`가 오면 메일 주소의 뒷부분, 뒤에 `@`가 오면 앞부분(john.kim@…)이라 도메인으로 보지 않는다.
# 앞의 `.`/`-`는 영문자·숫자에 붙어 있을 때만 막는다("Mr.Kim"의 Kim). 그래서 "...evil.com",
# "-evil.com", "확인했어요.evil.com"은 지운다. `/`·`_`는 막지 않는다("x/evil.com", "ftp://evil.com").
_BARE_DOMAIN = re.compile(r'(?<![A-Za-z0-9@])(?<![A-Za-z0-9][.\-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?' + _DOT + r')+'
                          + r'(?:' + _TLD + r'(?![A-Za-z0-9_@-])|' + _PATH_ONLY_TLD + r'(?=(?::\d{1,5})?[/?#]))'
                          + r'(?::\d{1,5}(?=[/?#]))?(?P<path>[/?#][A-Za-z0-9\-._~:/?#@!$&*+,;=%]*)?', re.I)
LINK_PLACEHOLDER = '(링크는 메일 원문에서 확인해 주세요)'


def strip_links(text, keep=()):
    """Replace links with LINK_PLACEHOLDER; returns (text, count).

    `keep`: names the tools returned as a booking provider (e.g. "Booking.com").
    Such a name is kept only when written alone, without any path or query.
    """
    kept = {str(name).strip().casefold() for name in keep if name}
    text, count = _URL.subn(LINK_PLACEHOLDER, str(text or ''))
    removed = 0

    def bare(match):
        nonlocal removed
        if not match.group('path') and match.group(0).casefold() in kept:
            return match.group(0)
        removed += 1
        return LINK_PLACEHOLDER
    text = _BARE_DOMAIN.sub(bare, text)
    return text, count + removed


def provider_names(run):
    """Every `provider` string the tools returned in this run (booking names)."""
    names, stack = set(), [data for _name, data in run.fact_data]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if isinstance(value.get('provider'), str):
                names.add(value['provider'])
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return names


def _loads(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def checked_scope(run) -> str:
    bookings = places = items = 0
    weather = False
    for name, data in run.fact_data:
        if name == 'list_bookings':
            bookings = max(bookings, data.get('total') or 0)
        elif name == 'search_mail':
            bookings = max(bookings, len(data.get('matches') or []))
        elif name == 'list_saved_places':
            places = max(places, data.get('total') or 0)
        elif name == 'get_itinerary':
            items = max(items, len(data.get('items') or []))
        elif name == 'get_weather':
            # An unknown/disabled forecast was not checked weather.
            weather = weather or data.get('status') == 'ok'
    parts = []
    if bookings:
        parts.append(f'예약 {bookings}건')
    if places:
        parts.append(f'저장한 곳 {places}곳')
    if items:
        parts.append(f'일정 항목 {items}개')
    if weather:
        parts.append('날씨')
    return (' · '.join(parts) + ' 확인') if parts else '확인한 자료 없음'


def weather_signal(run):
    """Last forecast's status for the screen; the Open-Meteo credit is shown
    only when a forecast actually came back (status ok), never for unknown."""
    calls = [data for name, data in run.fact_data if name == 'get_weather']
    if not calls:
        return None
    status = calls[-1].get('status')
    return {'status': status, 'provider': 'open_meteo' if status == 'ok' else None}


def facts_summary(run) -> str:
    """Only values the tools returned; grounded by construction."""
    lines = []
    for name, data in run.fact_data:
        if name in ('list_bookings', 'search_mail'):
            for booking in (data.get('bookings') or data.get('matches') or [])[:10]:
                when = ' '.join(str(value) for value in (booking.get('date'), booking.get('time')) if value) or '날짜·시각 미확인'
                lines.append(f"- {booking.get('provider') or '이름 미확인'} · {when}")
        elif name == 'list_saved_places':
            for place in (data.get('places') or [])[:10]:
                lines.append(f"- 저장한 곳: {place.get('name') or '이름 미확인'}")
        elif name == 'get_weather':
            lines.append('- 날씨: ' + ('확인함' if data.get('status') == 'ok' else '확인하지 못했어요'))
    for preview in run.previews[:3]:
        lines.append('- 미리보기를 만들었어요(아직 적용하지 않음).')
    for _ in run.drafts[:3]:
        lines.append('- 문의 초안을 만들었어요(보내지 않음).')
    return '\n'.join(dict.fromkeys(lines))


def answer_with_agent(services, actor, context, question, history, *, adapter=None, limits=None):
    """`services` is app.state; `context` is search.SearchContext (server actor)."""
    from src.foundation import grounding
    from src.prompts import load_prompt
    from src.reliability.providers import provider_scope
    limits = limits or AgentLimits()
    repo, documents, operations = services.repo, services.documents, services.operations
    ctx = AgentContext(repo=repo, documents=documents, operations=operations, user_id=context.user_id,
                       trip_id=context.trip_id, request_id=context.request_id, session_id=context.session_id,
                       trip_version=context.trip_version, actor=actor, services=services)

    def guard():
        documents.ensure_active_user(context.user_id, context.session_id)
        repo.get_trip(context.user_id, context.trip_id)
    with provider_scope(operations.gateway, operations.context(context.user_id, context.trip_id), guard=guard,
                        call_prefix=context.request_id + ':agent', deadline=time.time() + limits.deadline_seconds):
        # app.state.agent_adapter exists only in synthetic fixtures (scripted model).
        # Only a cookie session may create preview/draft rows; a token actor
        # (Bearer, "read GET and ask only") gets the read tools alone.
        read_only = getattr(actor, 'auth_kind', 'cookie') != 'cookie'
        run = run_agent(ctx, question, history, build_registry(read_only=read_only), model=AGENT_MODEL, limits=limits,
                        adapter=adapter or getattr(services, 'agent_adapter', None))
    hits = [{'metadata': {}, 'document': fact} for fact in run.facts]
    stripped = 0
    if run.stop_reason == 'final':
        check = grounding.check(run.answer, hits)
        # Same rule as the router's _checked_answer: a text that is empty once
        # model-written source lines are removed is not a verified answer.
        # A claim of an action the agent cannot take is not verified either.
        if check.verified and check.text and not claims_action(check.text):
            answer, stripped = strip_links(check.text, keep=provider_names(run))
            state, answer_type = 'verified', 'generated'
        else:
            summary = facts_summary(run)
            answer = ('확인한 사실과 맞지 않는 내용이 있어 도구로 확인한 결과만 보여 드려요.\n' + summary) if summary else \
                '확인한 사실과 맞지 않는 내용이 있어 답을 보여 드리지 않았어요. 질문을 조금 더 구체적으로 해 주세요.'
            state, answer_type = 'fallback', 'fact'
        ungrounded = len(check.ungrounded)
    else:
        summary = facts_summary(run)
        answer = run.answer + ('\n' + summary if summary else '')
        state, answer_type, ungrounded = 'structured', 'partial', 0
    sources, seen = [], set()
    for source in run.sources:
        key = (source.get('document_id'), source.get('booking_id'))
        if key not in seen:
            seen.add(key)
            sources.append(source)
    return {'answer': answer, 'sources': sources, 'links_removed': stripped,
            'tools_used': ['trip_agent', *dict.fromkeys(call['name'] for call in run.tool_calls if call['ok'])],
            'answer_mode': 'agent', 'grounding': state, 'answer_type': answer_type,
            # Atoms in the model's text that no tool output supports (that text is
            # not shown when > 0); the shown answer has 0 by construction.
            'model_ungrounded_atoms': ungrounded,
            'checked_scope': checked_scope(run), 'weather': weather_signal(run),
            'previews': run.previews, 'drafts': run.drafts,
            'agent': {'steps': run.steps, 'stop_reason': run.stop_reason,
                      'tool_calls': [{'name': call['name'], 'ok': call['ok'], 'error_code': call['error_code']} for call in run.tool_calls]},
            'prompt': load_prompt('trip_agent').meta()}
