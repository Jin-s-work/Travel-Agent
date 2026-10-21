"""Stage 3: tool registry contract and the bounded loop (scripted model, no network)."""
from copy import deepcopy
import json

import pytest

from src.foundation.db import Database
from src.foundation.repository import DomainError, Repository, utcnow
from src.llm import ChatResult, ToolCall, Usage
from src.reliability.budget import Budget, BudgetPolicy, CallContext
from src.reliability.providers import ProviderGateway, ProviderResult, metered_context
from src.trip_agent.loop import AgentContext, AgentLimits, run_agent
from src.trip_agent.registry import FORBIDDEN_TOOL_NAMES, Tool, ToolRegistry

MODEL = 'synthetic-agent-model'


def _schema(**properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


def echo_tool(name='list_things', kind='read', handler=None, max_output=4000):
    return Tool(name=name, description='synthetic', input_schema=_schema(day={'type': ['string', 'null']}),
                kind=kind, external=False, max_output_chars=max_output,
                handler=handler or (lambda ctx, args: {'day': args['day'], 'items': ['TEST-ITEM-01 2026-11-07 10:00']}))


class ScriptedAdapter:
    """Answers in a fixed order; records every request it was given."""
    name = 'replay'
    needs_client = False
    supports_embeddings = False

    def __init__(self, script):
        self.script, self.requests = list(script), []

    def chat_request(self, *, model, messages, schema=None, tools=None, tool_choice=None, reasoning_effort=None, max_output_tokens=None):
        from src.llm.openai_adapter import OpenAIAdapter
        return OpenAIAdapter().chat_request(model=model, messages=messages, schema=schema, tools=tools,
                                            max_output_tokens=max_output_tokens)

    def make_client(self, client_factory=None):
        return None

    def preflight(self, kind, kwargs):
        return None

    def output_cap(self, kwargs, price):
        bound = price['max_units']['output_tokens']
        return min(int(kwargs.get('max_completion_tokens', bound)), bound)

    def apply_cap(self, kwargs, cap):
        kwargs['max_completion_tokens'] = cap

    def estimate_chat(self, kwargs, cap):
        return {'input_tokens': 10, 'output_tokens': cap}

    def send(self, client, kind, kwargs):
        self.requests.append(deepcopy(kwargs))
        return ProviderResult({'step': len(self.requests) - 1}, {'input_tokens': 1, 'output_tokens': 1})

    def parse_chat(self, value):
        return self.script[min(value['step'], len(self.script) - 1)]


def tool_turn(*calls):
    return ChatResult(text=None, finish='tool_calls', usage=Usage(1, 1),
                      tool_calls=tuple(ToolCall(id=f'c{i}', name=name, arguments=args) for i, (name, args) in enumerate(calls)))


def final(text):
    return ChatResult(text=text, finish='stop', usage=Usage(1, 1))


@pytest.fixture
def world(tmp_path):
    db = Database(tmp_path / 'data.sqlite3')
    with db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    ('A', 'a@example.invalid', 'fake', 'A', 'member', utcnow(), utcnow()))
    repo = Repository(db)
    trip = repo.create_trip('A', {'title': 'Synthetic', 'start_date': '2026-11-06', 'end_date': '2026-11-09'})
    config = deepcopy(BudgetPolicy.for_tests().config)
    config['prices'][f'replay/{MODEL}'] = {'currency': 'USD', 'rates_per_million': {'input_tokens': '0', 'output_tokens': '0'},
                                           'max_units': {'input_tokens': 100000, 'output_tokens': 1024}}
    gateway = ProviderGateway(Budget(db, BudgetPolicy(config)), tmp_path / 'receipts')
    ctx = AgentContext(repo=repo, documents=None, operations=None, user_id='A', trip_id=trip['id'], request_id='req1')
    return db, gateway, ctx, CallContext('A', trip['id'], trip['id'])


def run(world, script, registry, prefix='req1', **kwargs):
    # A new request id per run: the same id and request would replay its receipt.
    db, gateway, ctx, call_ctx = world
    adapter = ScriptedAdapter(script)
    with metered_context(gateway, call_ctx, prefix + ':agent'):
        result = run_agent(ctx, '내일 일정 알려줘', [], registry, model=MODEL, adapter=adapter, **kwargs)
    return result, adapter


def test_registry_refuses_forbidden_capabilities_and_scope_arguments():
    for name in ('apply_itinerary_change', 'undo_itinerary_change', 'delete_booking', 'send_message', 'fetch_url'):
        assert name in FORBIDDEN_TOOL_NAMES
        with pytest.raises(ValueError):
            ToolRegistry([echo_tool(name=name)])
    with pytest.raises(ValueError):
        ToolRegistry([echo_tool(kind='write')])
    with pytest.raises(ValueError):
        ToolRegistry([Tool('scoped', 'x', _schema(trip_id={'type': 'string'}), 'read', False, 100, lambda c, a: {})])
    with pytest.raises(ValueError):
        ToolRegistry([Tool('loose', 'x', {'type': 'object', 'properties': {}}, 'read', False, 100, lambda c, a: {})])


def test_bad_tool_name_or_arguments_are_results_not_exceptions():
    registry = ToolRegistry([echo_tool()])
    assert registry.call(None, 'delete_booking', '{}').error_code == 'TOOL_NOT_FOUND'
    assert registry.call(None, 'list_things', '{not json').error_code == 'TOOL_ARGUMENTS_INVALID'
    assert registry.call(None, 'list_things', '{"day": null, "user_id": "B"}').error_code == 'TOOL_ARGUMENTS_INVALID'
    failing = ToolRegistry([echo_tool(handler=lambda ctx, args: (_ for _ in ()).throw(DomainError('NOT_FOUND', 'x', 404)))])
    assert failing.call(None, 'list_things', '{"day": null}').error_code == 'NOT_FOUND'
    crashing = ToolRegistry([echo_tool(handler=lambda ctx, args: 1 / 0)])
    assert crashing.call(None, 'list_things', '{"day": null}').error_code == 'TOOL_FAILED'
    long = ToolRegistry([echo_tool(handler=lambda ctx, args: {'text': 'x' * 500}, max_output=100)])
    outcome = long.call(None, 'list_things', '{"day": null}')
    assert outcome.ok and outcome.content.endswith('…(잘림)') and len(outcome.content) <= 100 + len('…(잘림)')


def test_tool_then_final_answer_with_untrusted_result_block_and_metered_steps(world):
    db, *_ = world
    registry = ToolRegistry([echo_tool()])
    result, adapter = run(world, [tool_turn(('list_things', '{"day": "2026-11-07"}')), final('TEST-ITEM-01 2026-11-07 10:00 확인했어요.')], registry)
    assert result.stop_reason == 'final' and result.steps == 2
    assert result.tool_calls == [{'name': 'list_things', 'ok': True, 'error_code': None, 'kind': 'read'}]
    second = adapter.requests[1]['messages']
    tool_message = [m for m in second if m['role'] == 'tool'][0]
    assert tool_message['content'].startswith('<tool_result name="list_things" trust="untrusted_data">')
    assert [m['role'] for m in second][-2:] == ['assistant', 'tool']
    assert second[-2]['tool_calls'][0]['function']['name'] == 'list_things'
    assert adapter.requests[0]['tools'][0]['function']['strict'] is True
    with db.connect() as con:
        keys = [row['call_key'] for row in con.execute("SELECT call_key FROM usage_reservations ORDER BY created_at")]
    assert keys == ['request:req1:agent:chat:0', 'request:req1:agent:chat:1']


def test_tool_call_limit_gives_partial_answer(world):
    registry = ToolRegistry([echo_tool()])
    nine = tool_turn(*[('list_things', '{"day": null}')] * 9)
    result, _ = run(world, [nine], registry)
    assert result.stop_reason == 'max_tool_calls'
    assert sum(call['ok'] for call in result.tool_calls) == 8
    assert result.tool_calls[-1]['error_code'] == 'TOOL_LIMIT'
    assert '상한' in result.answer and len(result.facts) == 8


def test_step_limit_and_deadline_and_cost_ceiling(world):
    registry = ToolRegistry([echo_tool()])
    looping = [tool_turn(('list_things', '{"day": null}'))]
    result, adapter = run(world, looping, registry)
    assert result.stop_reason == 'max_steps' and result.steps == 5 and len(adapter.requests) == 5
    ticks = iter([0, 0, 30, 30, 30])
    result, adapter = run(world, looping, registry, prefix='req2', clock=lambda: next(ticks))
    assert result.stop_reason == 'deadline' and len(adapter.requests) == 1
    result, adapter = run(world, looping, registry, prefix='req3', limits=AgentLimits(max_cost_micros=0))
    assert result.stop_reason == 'budget' and adapter.requests == []


class FullUsageAdapter(ScriptedAdapter):
    """Settles each step at its full reservation (worst case)."""

    def send(self, client, kind, kwargs):
        self.requests.append(deepcopy(kwargs))
        return ProviderResult({'step': len(self.requests) - 1},
                              {'input_tokens': 10, 'output_tokens': kwargs['max_completion_tokens']})


def _priced(world, rate):
    db, gateway, ctx, call_ctx = world
    gateway.budget.policy.config['prices'][f'replay/{MODEL}']['rates_per_million'] = {
        'input_tokens': str(rate), 'output_tokens': str(rate)}
    return 10 * rate + 1024 * rate  # ScriptedAdapter.estimate_chat: 10 input + output cap


def test_cost_ceiling_is_checked_against_the_next_reservation_not_only_spent(world):
    """B3: 단계당 예약 10340, 상한 25000 → 2단계 뒤 멈춘다 (예전에는 3단계 31020 까지 갔다)."""
    db, gateway, ctx, call_ctx = world
    per_step = _priced(world, 10)
    registry = ToolRegistry([echo_tool()])
    adapter = FullUsageAdapter([tool_turn(('list_things', '{"day": null}'))])
    with metered_context(gateway, call_ctx, 'req1:agent'):
        result = run_agent(ctx, '내일 일정 알려줘', [], registry, model=MODEL, adapter=adapter,
                           limits=AgentLimits(max_cost_micros=25000))
    assert result.stop_reason == 'budget' and result.steps == 2 and len(adapter.requests) == 2
    assert result.cost_micros == 2 * per_step <= 25000


def test_question_cost_counts_every_attempt_and_agent_search_embeddings(world):
    """B3: 429 로 남은 앞선 시도(예상비용)와 search_mail 임베딩도 질문 비용에 들어간다."""
    from src.trip_agent.loop import _question_cost
    db, gateway, ctx, call_ctx = world
    budget = gateway.budget
    budget.policy.config['prices'][f'replay/{MODEL}']['rates_per_million'] = {'input_tokens': '1', 'output_tokens': '1'}
    units = {'input_tokens': 100, 'output_tokens': 100}  # 200 micros each

    def reserve(key, attempt=1):
        return budget.reserve(call_ctx, provider='replay', sku=MODEL, operation='chat', call_key=key,
                              estimated_units=units, request_hash='h-' + key, attempt=attempt)
    first = reserve('req1:agent:chat:0')
    budget.uncertain(first['call_id'], pending=True, error_code='RATE_LIMITED')  # 429: estimate kept
    second = reserve('req1:agent:chat:0', attempt=2)
    budget.mark_sent(second['call_id'])
    budget.settle(second['call_id'], {'input_tokens': 10, 'output_tokens': 10})  # 20 actual
    search = reserve('req1:agent-search:0:embedding:0')                         # 200 reserved
    released = reserve('req1:agent:chat:1')
    budget.release_unsent(released['call_id'], reason_code='NOT_SENT')          # 0
    reserve('other-request:agent:chat:0')                                        # another question
    prefixes = ('request:req1:agent:', 'request:req1:agent-search:')
    assert _question_cost(ctx, prefixes) == 200 + 20 + 200


class _UncertainEmbedOps:
    """search_mail 의 operations: 임베딩은 매번 응답·과금 불확정으로 끝난다 (합성, 네트워크 없음)."""

    def __init__(self, code):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from src.config import EMBEDDING_MODEL
        self.code, self.keys = code, []

        @contextmanager
        def reader(user_id, trip_id):
            yield SimpleNamespace(generation={'embedding_model': EMBEDDING_MODEL}, query=lambda vector, top_k: [])
        self.generations = SimpleNamespace(reader=reader)

    def context(self, user_id, trip_id):
        return (user_id, trip_id)

    def embed(self, context, key, texts, guard, query=False):
        self.keys.append(key)
        raise DomainError(self.code, '합성 불확정', 503)


@pytest.mark.parametrize('code,stop', [('PROVIDER_OUTCOME_UNKNOWN', 'provider_stopped'),
                                       ('CALL_OUTCOME_UNKNOWN', 'provider_stopped'),
                                       ('PROVIDER_RATE_LIMITED', 'provider_stopped'),
                                       ('EXTERNAL_CALLS_PAUSED', 'provider_stopped'),
                                       ('BUDGET_EXHAUSTED', 'budget'),
                                       ('DEADLINE_EXCEEDED', 'deadline')])
def test_uncertain_search_mail_is_not_resent_in_the_same_question(world, code, stop):
    """B1: 도구의 과금·전송 불확정 오류는 도구 결과가 아니다. 모델이 search_mail 을 여러 번
    부르게 해도 임베딩은 한 번만 나가고, 루프는 지금까지의 사실로 멈춘다."""
    from types import SimpleNamespace
    from src.trip_agent.tools import build_registry
    db, gateway, ctx, call_ctx = world
    ops = _UncertainEmbedOps(code)
    ctx.operations = ops
    ctx.documents = SimpleNamespace(ensure_active_user=lambda *args: None)
    search = ('search_mail', json.dumps({'query': '호텔 체크인'}))
    script = [tool_turn(('list_bookings', json.dumps({'date_from': None, 'date_to': None}))),
              tool_turn(search, search), tool_turn(search), tool_turn(search), final('끝')]
    result, adapter = run(world, script, build_registry(read_only=True))
    assert ops.keys == ['req1:agent-search:0']  # 같은 질문 안에서 재전송 없음
    assert result.stop_reason == stop and result.steps == 2 and len(adapter.requests) == 2
    assert result.tool_calls[-1] == {'name': 'search_mail', 'ok': False, 'error_code': code, 'kind': 'read'}
    assert [call['name'] for call in result.tool_calls] == ['list_bookings', 'search_mail']
    assert len(result.facts) == 1 and '끝까지 확인하지 못했어요' in result.answer
    if stop == 'provider_stopped':
        assert '외부 호출' in result.answer and '잠시 뒤에' in result.answer


def test_registry_reraises_billing_uncertain_tool_errors_only():
    from src.trip_agent.registry import LOOP_STOP_CODES
    for code in ('PROVIDER_OUTCOME_UNKNOWN', 'CALL_OUTCOME_UNKNOWN', 'CALL_NOT_SENT', 'PROVIDER_UNAVAILABLE',
                 'PROVIDER_RATE_LIMITED', 'EXTERNAL_CALLS_PAUSED', 'BUDGET_EXHAUSTED', 'GLOBAL_BUDGET_STOPPED',
                 'COST_BOUND_EXCEEDED', 'COST_BOUND_REQUIRED', 'DEADLINE_EXCEEDED', 'PROVIDER_RECEIPT_INVALID'):
        assert code in LOOP_STOP_CODES
        raising = ToolRegistry([echo_tool(handler=lambda ctx, args, c=code: (_ for _ in ()).throw(DomainError(c, 'x', 503)))])
        with pytest.raises(DomainError) as caught:
            raising.call(None, 'list_things', '{"day": null}')
        assert caught.value.code == code
    for code in ('NOT_FOUND', 'SEARCH_REBUILDING', 'VERSION_CONFLICT'):
        failing = ToolRegistry([echo_tool(handler=lambda ctx, args, c=code: (_ for _ in ()).throw(DomainError(c, 'x', 409)))])
        assert failing.call(None, 'list_things', '{"day": null}').error_code == code


def test_uncertain_chat_step_after_a_tool_keeps_the_facts(world, monkeypatch):
    """B1 과 같은 규칙: 첫 단계 뒤의 chat 불확정 오류도 사실 요약으로 멈춘다 (첫 단계는 그대로 오류)."""
    import src.llm.gateway as llm_gateway
    registry = ToolRegistry([echo_tool()])
    original, seen = llm_gateway.chat, []

    def flaky(*args, **kwargs):
        seen.append(1)
        if len(seen) > 1:
            raise DomainError('PROVIDER_OUTCOME_UNKNOWN', '합성 불확정', 503)
        return original(*args, **kwargs)
    monkeypatch.setattr(llm_gateway, 'chat', flaky)
    result, _ = run(world, [tool_turn(('list_things', '{"day": null}')), final('끝')], registry)
    assert result.stop_reason == 'provider_stopped' and result.steps == 1 and len(result.facts) == 1
    seen.clear()
    seen.append(1)  # 다음 호출이 첫 단계부터 실패
    with pytest.raises(DomainError) as caught:
        run(world, [final('끝')], registry, prefix='req2')
    assert caught.value.code == 'PROVIDER_OUTCOME_UNKNOWN'


def test_incomplete_model_answer_is_not_shown(world):
    registry = ToolRegistry([echo_tool()])
    result, _ = run(world, [ChatResult(text='반쯤 쓴', finish='length')], registry)
    assert result.stop_reason == 'model_incomplete' and '반쯤' not in result.answer


def test_agent_answer_never_shows_raw_links_from_untrusted_data():
    from src.trip_agent.service import LINK_PLACEHOLDER, strip_links
    text, count = strip_links('환불은 https://refund-claim.example.invalid/x?a=1 에서, 안내는 www.example.com 참고.')
    assert count == 2 and 'http' not in text and 'www.' not in text
    assert text.count(LINK_PLACEHOLDER) == 2
    assert strip_links('링크 없는 답 2026-11-07 10:00') == ('링크 없는 답 2026-11-07 10:00', 0)


def test_agent_answer_strips_scheme_less_domains_but_keeps_tool_provider_names():
    """SEC-1: 스킴 없는 도메인(경로 포함)·hxxp·[.] 표기도 지운다. 도구가 준 예약처 이름 단독은 남긴다."""
    from src.trip_agent.service import LINK_PLACEHOLDER, strip_links
    text, count = strip_links('환불 규정에 따라 refund-hotel-kr.com/claim 에서 예약번호로 신청하시면 돼요.')
    assert count == 1 and 'refund-hotel-kr' not in text and LINK_PLACEHOLDER in text
    text, count = strip_links('example-refund.com/claim, refund-claim.example.invalid/r?c=RMN-6610, '
                              'hxxps://evil.example/x, evil[.]com/a, bare-phish.net')
    assert count == 5 and '.com' not in text and 'invalid' not in text and 'evil' not in text and 'phish' not in text
    # A provider name the tools returned stays when written alone, never with a path.
    assert strip_links('Booking.com 예약은 10:00 이에요', keep={'Booking.com'}) == ('Booking.com 예약은 10:00 이에요', 0)
    assert strip_links('booking.com/claim 에서 신청', keep={'Booking.com'})[1] == 1
    assert strip_links('Booking.com 예약')[1] == 1
    # Dates, times, decimals, versions and e-mail addresses are not domains.
    plain = '2026.11.07 10:00, 3.5km, v6.1, e.g. 예약, hotel@example.com 문의'
    assert strip_links(plain) == (plain, 0)
