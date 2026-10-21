"""Bounded tool-calling loop.

Limits [가설, PRD 4.3]: 5 model steps, 8 tool calls, a 25 s request deadline,
a per-step output cap and a per-question cost ceiling. Reaching a limit ends
the loop with a partial answer built only from facts the tools returned, plus
what could not be done. Every model call goes through the metered gateway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import time

from src.foundation.repository import DomainError
from src.trip_agent.registry import BUDGET_STOP_CODES, DEADLINE_STOP_CODES, PROVIDER_STOP_CODES

UNTRUSTED_OPEN = '<tool_result name="{name}" trust="untrusted_data">'
UNTRUSTED_CLOSE = '</tool_result>'


@dataclass(frozen=True)
class AgentLimits:
    max_steps: int = 5
    max_tool_calls: int = 8
    deadline_seconds: float = 25.0
    max_output_tokens: int = 1024
    max_cost_micros: int = int(os.getenv('AGENT_MAX_COST_MICROS', '50000'))  # USD 0.05 per question [가설]


@dataclass
class AgentContext:
    """Server-scoped: the actor decides user and trip; tools never take them."""
    repo: object
    documents: object
    operations: object
    user_id: str
    trip_id: str
    request_id: str
    session_id: str | None = None
    trip_version: int | None = None
    actor: object = None      # authenticated Actor (id, session_id) — services scope with it
    services: object = None   # app.state: itineraries, discovery, alternatives, preparation, weather
    extras: dict = field(default_factory=dict)


@dataclass
class AgentRun:
    answer: str
    stop_reason: str  # final | max_steps | max_tool_calls | deadline | budget | provider_stopped | model_incomplete
    steps: int
    tool_calls: list  # [{'name','ok','error_code','kind'}] — no arguments or content
    facts: list       # tool outputs (strings) used for grounding
    fact_data: list   # [(tool name, data dict)] for deterministic summaries
    previews: list
    drafts: list
    sources: list
    cost_micros: int = 0


def _wrap(outcome):
    """Tool output inside an untrusted-data block the data cannot close.

    The content is JSON text, where `<` and `>` can only occur inside strings,
    so escaping them as \\u003c / \\u003e keeps it the same JSON value while a
    mail field such as `</tool_result>` can no longer fake the block boundary.
    """
    content = outcome.content.replace('<', '\\u003c').replace('>', '\\u003e')
    return UNTRUSTED_OPEN.format(name=outcome.name) + '\n' + content + '\n' + UNTRUSTED_CLOSE


def _history(history, limit=20):
    """Only plain user/assistant text from this trip's conversation (existing policy)."""
    kept = []
    for message in (history or [])[-limit:]:
        if message.get('role') in ('user', 'assistant') and isinstance(message.get('content'), str):
            kept.append({'role': message['role'], 'content': message['content'][:4000]})
    return kept


COST_SQL = ('SELECT call_key, state, estimated_cost_micros, actual_cost_micros FROM usage_reservations '
            'WHERE owner_id=? AND scope_id=? AND (call_key LIKE ? OR call_key LIKE ?)')


def _ledger_prefixes(ctx):
    """Reserved call-key prefixes of this question: the loop's chat steps (the
    active metered scope's prefix) and search_mail's embeddings (`agent-search`)."""
    from src.reliability.providers import _active
    active = _active.get()
    base = 'job:' + active.context.job_id + ':' if active is not None and active.context.job_id else 'request:'
    chat_prefix = base + (active.prefix if active is not None else ctx.request_id + ':agent') + ':'
    return chat_prefix, 'request:' + ctx.request_id + ':agent-search:'


def _question_cost(ctx, prefixes):
    """Everything this question has spent so far, by Budget's rule: every attempt
    of every agent chat step and agent search embedding (released 0, settled
    actual, otherwise the reserved estimate — a 429/unknown attempt still counts).

    Returns None when the ledger cannot be read: the caller then treats the
    per-question ceiling as reached (fail-closed), never as "free".
    """
    try:
        with ctx.repo.db.connect() as con:
            rows = con.execute(COST_SQL, (ctx.user_id, ctx.trip_id, *(prefix + '%' for prefix in prefixes))).fetchall()
        total = 0
        for row in rows:
            # LIKE treats `_`/`%` in ids as wildcards; keep only exact prefixes.
            if not any(row['call_key'].startswith(prefix) for prefix in prefixes):
                continue
            if row['state'] == 'released':
                continue
            total += int(row['actual_cost_micros'] if row['state'] == 'settled' else row['estimated_cost_micros'])
        return total
    except Exception:  # noqa: BLE001 - an unreadable ledger closes the ceiling
        return None


def _step_reservation(model, messages, registry, limits, adapter):
    """The reservation the next chat step would make; None when it cannot be computed
    (chat itself then fails the same way, before any send)."""
    from src.llm import Limits
    from src.llm.gateway import chat_reservation_micros
    try:
        return chat_reservation_micros('agent', model=model, messages=messages, tools=registry.specs(), adapter=adapter,
                                       limits=Limits(max_output_tokens=limits.max_output_tokens))
    except Exception:  # noqa: BLE001
        return None


def _stop_reason_for(code):
    """Where a gateway error (chat step or tool) ends the loop after a step; None = not a stop."""
    if code in BUDGET_STOP_CODES:
        return 'budget'
    if code in DEADLINE_STOP_CODES:
        return 'deadline'
    if code in PROVIDER_STOP_CODES:
        return 'provider_stopped'
    return None


def partial_answer(run_facts, reason):
    labels = {'max_steps': '단계 상한', 'max_tool_calls': '도구 호출 상한', 'deadline': '응답 시간 상한',
              'budget': '질문당 비용 상한', 'model_incomplete': '모델 응답 미완료'}
    if reason == 'provider_stopped':
        # 과금됐을 수 있는 외부 호출을 같은 질문 안에서 다시 보내지 않으려고 멈췄다.
        lines = ['외부 호출 결과를 확인할 수 없거나 외부 호출이 멈춰 있어 끝까지 확인하지 못했어요.']
        retry = '잠시 뒤에 다시 물어봐 주세요.'
    else:
        lines = [f"{labels.get(reason, '상한')}에 닿아 끝까지 확인하지 못했어요."]
        retry = '나머지는 다시 물어봐 주세요.'
    if run_facts:
        lines.append('지금까지 도구로 확인한 결과만 아래에 남겨요. ' + retry)
    else:
        lines.append('확인한 사실이 없어 답을 만들지 않았어요.')
    return '\n'.join(lines)


def run_agent(ctx: AgentContext, question: str, history, registry, *, model, limits: AgentLimits = AgentLimits(),
              adapter=None, clock=time.monotonic) -> AgentRun:
    """Run the loop inside an already-bound provider scope (metered context)."""
    from src.llm import Limits
    from src.llm.gateway import chat
    from src.prompts import load_prompt
    prompt = load_prompt('trip_agent')
    messages = [{'role': 'system', 'content': prompt.body}, *_history(history), {'role': 'user', 'content': question}]
    started = clock()
    calls, facts, fact_data, previews, drafts, sources, trace = 0, [], [], [], [], [], []
    spent, step, stop, answer = 0, 0, None, None
    prefixes = _ledger_prefixes(ctx)
    while step < limits.max_steps:
        if clock() - started >= limits.deadline_seconds:
            stop = 'deadline'
            break
        if step:
            cost = _question_cost(ctx, prefixes)
            spent = max(spent, limits.max_cost_micros) if cost is None else cost
        if spent >= limits.max_cost_micros:
            stop = 'budget'
            break
        # Check the ceiling against what this step would reserve, before sending:
        # the question never goes past AGENT_MAX_COST_MICROS by one step.
        estimate = _step_reservation(model, messages, registry, limits, adapter)
        if estimate is not None and spent + estimate > limits.max_cost_micros:
            stop = 'budget'
            break
        try:
            result = chat('agent', model=model, messages=messages, tools=registry.specs(), adapter=adapter,
                          limits=Limits(max_output_tokens=limits.max_output_tokens), prompt=prompt.meta(),
                          operation='agent')
        except DomainError as error:
            # Before any step the router's own path would fail the same way,
            # so the error propagates unchanged ("same result as the router").
            # After a step: budget → 'budget'; the scope deadline (gateway, wall
            # clock) can pass a moment before the loop's own monotonic check →
            # 'deadline'; an uncertain/blocked provider call → 'provider_stopped'.
            # Tool errors of the same kinds follow the same rule (below).
            reason = _stop_reason_for(error.code)
            if step and reason:
                stop = reason
                break
            raise
        step += 1
        if result.tool_calls:
            messages.append({'role': 'assistant', 'content': result.text,
                             'tool_calls': [{'id': call.id, 'name': call.name, 'arguments': call.arguments}
                                            for call in result.tool_calls]})
            for call in result.tool_calls:
                if calls >= limits.max_tool_calls:
                    outcome_content = json.dumps({'error': 'TOOL_LIMIT', 'message': '도구 호출 상한에 닿았습니다.'}, ensure_ascii=False)
                    messages.append({'role': 'tool', 'tool_call_id': call.id, 'name': call.name, 'content': outcome_content})
                    trace.append({'name': call.name, 'ok': False, 'error_code': 'TOOL_LIMIT'})
                    stop = 'max_tool_calls'
                    continue
                calls += 1
                tool = registry.tools.get(call.name)
                try:
                    outcome = registry.call(ctx, call.name, call.arguments)
                except DomainError as error:
                    # registry.call re-raises only LOOP_STOP_CODES: a tool whose
                    # provider call may have been billed (or cannot be sent) ends
                    # the loop instead of letting the model call it again under a
                    # new call key. A tool runs only after a chat step (step >= 1).
                    reason = _stop_reason_for(error.code)
                    if not step or reason is None:
                        raise
                    trace.append({'name': call.name, 'ok': False, 'error_code': error.code,
                                  'kind': tool.kind if tool else None})
                    stop = reason
                    break
                trace.append({'name': call.name, 'ok': outcome.ok, 'error_code': outcome.error_code,
                              'kind': tool.kind if tool else None})
                messages.append({'role': 'tool', 'tool_call_id': call.id, 'name': call.name, 'content': _wrap(outcome)})
                if outcome.ok:
                    facts.append(outcome.content)
                    fact_data.append((call.name, outcome.data))
                    previews.extend(outcome.data.get('_previews', []))
                    drafts.extend(outcome.data.get('_drafts', []))
                    sources.extend(outcome.data.get('_sources', []))
            if stop:  # max_tool_calls, or a tool's budget/deadline/provider stop
                break
            continue
        if result.finish != 'stop' or not result.text:
            stop = 'model_incomplete'
            break
        answer, stop = result.text.strip(), 'final'
        break
    else:
        stop = stop or 'max_steps'
    if step:
        cost = _question_cost(ctx, prefixes)
        spent = max(spent, limits.max_cost_micros) if cost is None else cost
    if stop != 'final':
        answer = partial_answer(facts, stop)
    return AgentRun(answer=answer, stop_reason=stop, steps=step, tool_calls=trace, facts=facts, fact_data=fact_data,
                    previews=previews, drafts=drafts, sources=sources, cost_micros=spent)
