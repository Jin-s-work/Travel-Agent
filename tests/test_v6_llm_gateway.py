"""v6 stage 2: vendor-neutral LLM gateway regression tests (synthetic only).

No network, no key and no SDK: OpenAI and Anthropic clients are in-process
fakes, replay records are handmade and labelled synthetic. Nothing here measures
model quality; it pins request bytes, cost bounds and error mapping.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.config import ANSWER_MODEL, EMBEDDING_MODEL, EXTRACTION_MODEL
from src.foundation.db import Database
from src.foundation.repository import DomainError, Repository, utcnow
from src.llm import ChatResult, Limits, adapter_scope, chain, resolve
from src.llm.anthropic_adapter import AnthropicAdapter
from src.llm.gateway import chat, embed
from src.llm.openai_adapter import OpenAIAdapter
from src.llm.replay import ReplayAdapter, ReplayStore, make_record, request_key, validate_record
from src.prompts import load_prompt
from src.reliability.budget import BUDGET_SCHEMA, Budget, BudgetPolicy, CallContext
from src.reliability.providers import ProviderGateway, ProviderRejected, metered_context
import src.reliability.providers as providers

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- helpers --
# Shared with tests/test_v6_prompts.py, test_v6_extraction_repair.py and
# test_v6_extraction_fallback.py (helpers only; no test functions are imported).

def token_price(max_output=4096, rate='0'):
    return {'currency': 'USD', 'rates_per_million': {'input_tokens': rate, 'output_tokens': rate},
            'max_units': {'input_tokens': 200000, 'output_tokens': max_output}}


def embedding_price(rate='0.02'):
    return {'currency': 'USD', 'rates_per_million': {'input_tokens': rate}, 'max_units': {'input_tokens': 100000}}


def policy_with(prices):
    config = deepcopy(BudgetPolicy.for_tests().config)
    config['prices'].update(prices)
    return BudgetPolicy(config)


def replay_runtime_prices(*, replay_output=4096):
    """Prices for 'extraction via replay, embedding/answer configured as OpenAI'.

    The OpenAI entries only satisfy mail_capabilities; embedding in these tests
    goes through an injected fake embedder and the answer path is not used.
    """
    return {f'replay/{EXTRACTION_MODEL}': token_price(replay_output),
            f'openai/{EMBEDDING_MODEL}': embedding_price(),
            f'openai/{ANSWER_MODEL}': token_price()}


def metered_world(tmp_path, prices):
    db = Database(Path(tmp_path) / 'gateway.sqlite3')
    with db.connect() as con:
        con.executescript(BUDGET_SCHEMA)
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    ('A', 'A@example.invalid', 'fake', 'A', 'member', utcnow(), utcnow()))
    trip = Repository(db).create_trip('A', {'title': 'Synthetic', 'start_date': '2026-11-01', 'end_date': '2026-11-03'})
    ctx = CallContext('A', trip['id'], trip['id'], job_id='job1')
    gateway = ProviderGateway(Budget(db, policy_with(prices)), Path(tmp_path) / 'receipts')
    gateway.sleeper = lambda delay: None
    return SimpleNamespace(db=db, ctx=ctx, gateway=gateway, receipts=Path(tmp_path) / 'receipts')


def reservations(db):
    with db.connect() as con:
        return [dict(row) for row in con.execute(
            'SELECT provider,sku,operation,call_key,attempt,request_hash,estimated_units_json,estimated_cost_micros,'
            'actual_units_json,actual_cost_micros,state FROM usage_reservations ORDER BY created_at, call_key')]


def telemetry_rows(db):
    with db.connect() as con:
        return [dict(row) for row in con.execute('SELECT * FROM llm_calls ORDER BY created_at, id')]


def extraction_messages(raw):
    from src.parser import DOCUMENT_SYSTEM_PROMPT
    return [{'role': 'system', 'content': DOCUMENT_SYSTEM_PROMPT}, {'role': 'user', 'content': raw}]


def extraction_kwargs(raw, model=EXTRACTION_MODEL):
    from src.parser import DOCUMENT_RESERVATIONS_SCHEMA
    return OpenAIAdapter().chat_request(model=model, messages=extraction_messages(raw),
                                        schema=('document_reservations', DOCUMENT_RESERVATIONS_SCHEMA))


def repair_kwargs(raw, first_text, model=EXTRACTION_MODEL):
    """The exact request src.parser sends after `first_text` failed validation."""
    from src.parser import DOCUMENT_RESERVATIONS_SCHEMA, _PREVIOUS_OUTPUT_CHARS, _check_document
    bookings, failure = _check_document(ChatResult(text=first_text, finish='stop'), raw)
    assert bookings is None and failure[0] == 'invalid', 'repair fixture must start from an invalid answer'
    messages = extraction_messages(raw)
    if first_text:
        messages.append({'role': 'assistant', 'content': first_text[:_PREVIOUS_OUTPUT_CHARS]})
    messages.append({'role': 'user', 'content': load_prompt('mail_extraction_repair').render(
        ERRORS='\n'.join(f'- {line}' for line in failure[2]))})
    return OpenAIAdapter().chat_request(model=model, messages=messages,
                                        schema=('document_reservations', DOCUMENT_RESERVATIONS_SCHEMA))


def put_record(directory, kwargs, text, *, finish='stop', refusal=None, source='handmade_fault', label='synthetic test fixture'):
    ReplayStore(directory).put(make_record(kwargs, text=text, finish=finish, refusal=refusal,
                                           usage={'input_tokens': 120, 'output_tokens': 40},
                                           source=source, label=label))


def booking(snippet, **changes):
    value = {'kind': '투어', 'provider': 'Synthetic Provider', 'confirmation_number': 'SYN-1', 'date': '2026-11-07',
             'date_end': None, 'time': '19:00', 'time_end': None, 'location': None, 'refund_policy': None,
             'raw_snippet': snippet, 'events': []}
    value.update(changes)
    return value


def document(*items):
    return json.dumps({'reservations': list(items)}, ensure_ascii=False)


# ------------------------------------------------ 3. OpenAI byte goldens --
# Values produced by the pre-adapter HEAD `providers._metered_call` for the same
# inputs (scratch comparison 2026-10-21). They must never change: stored
# usage_reservations and receipts of in-flight jobs replay against them.
GOLDEN_MESSAGES = [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': '메일 본문'}]
GOLDEN_CHAT_PAYLOAD = {'id': 'x', 'object': 'chat.completion', 'created': 0, 'model': 'gpt-5-mini',
                       'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"reservations":[]}'}}],
                       'usage': {'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13}}
GOLDEN_EMBED_PAYLOAD = {'object': 'list', 'model': 'text-embedding-3-small',
                        'data': [{'object': 'embedding', 'index': 0, 'embedding': [0.1, 0.2]}],
                        'usage': {'prompt_tokens': 5, 'total_tokens': 5}}
GOLDEN_ROWS = [
    {'provider': 'openai', 'sku': 'gpt-5-mini', 'operation': 'chat', 'call_key': 'job:job1:doc1:extract:chat:0', 'attempt': 1,
     'request_hash': '8eb2d11212b7c79124c0cc00badb0be3684ceb1150ca4b59fa51b46d5fb8d5c2',
     'estimated_units_json': '{"input_tokens":492,"output_tokens":4096}', 'estimated_cost_micros': 8315,
     'actual_units_json': '{"input_tokens":10,"output_tokens":3}', 'actual_cost_micros': 9, 'state': 'settled'},
    {'provider': 'openai', 'sku': 'gpt-5-mini', 'operation': 'chat', 'call_key': 'job:job1:doc1:extract:chat:1', 'attempt': 1,
     'request_hash': '47077205832807729a2021860e317f922269ebff60ebe03080a35c4e46358eaf',
     'estimated_units_json': '{"input_tokens":352,"output_tokens":4096}', 'estimated_cost_micros': 8280,
     'actual_units_json': '{"input_tokens":10,"output_tokens":3}', 'actual_cost_micros': 9, 'state': 'settled'},
    {'provider': 'openai', 'sku': 'text-embedding-3-small', 'operation': 'embedding', 'call_key': 'job:job1:doc1:extract:embedding:0', 'attempt': 1,
     'request_hash': 'dcae8ad3c97800f4e832d99cb2f0a0462fbcc2e3647532a60abab9e86c4e3ebc',
     'estimated_units_json': '{"input_tokens":34}', 'estimated_cost_micros': 1,
     'actual_units_json': '{"input_tokens":5}', 'actual_cost_micros': 1, 'state': 'settled'},
]
GOLDEN_SENT = [
    ('opts', {'max_retries': 0, 'timeout': 45.0}),
    ('chat', {'model': 'gpt-5-mini', 'messages': GOLDEN_MESSAGES, 'max_completion_tokens': 4096,
              'response_format': {'type': 'json_schema', 'json_schema': {'name': 'document_reservations', 'strict': True, 'schema': {'type': 'object'}}}}),
    ('opts', {'max_retries': 0, 'timeout': 45.0}),
    ('chat', {'model': 'gpt-5-mini', 'reasoning_effort': 'low', 'messages': GOLDEN_MESSAGES, 'max_completion_tokens': 4096}),
    ('opts', {'max_retries': 0, 'timeout': 45.0}),
    ('embedding', {'model': 'text-embedding-3-small', 'input': ['a', 'b']}),
]
OPENAI_GOLDEN_PRICES = {
    'openai/gpt-5-mini': {'currency': 'USD', 'rates_per_million': {'input_tokens': '0.25', 'output_tokens': '2.00'},
                          'max_units': {'input_tokens': 100000, 'output_tokens': 4096}},
    'openai/text-embedding-3-small': {'currency': 'USD', 'rates_per_million': {'input_tokens': '0.02'},
                                      'max_units': {'input_tokens': 100000}},
}


def openai_fake(log):
    def chat_create(**kwargs):
        log.append(('chat', kwargs))
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3), model_dump=lambda **_: deepcopy(GOLDEN_CHAT_PAYLOAD))

    def embed_create(**kwargs):
        log.append(('embedding', kwargs))
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=5), model_dump=lambda **_: deepcopy(GOLDEN_EMBED_PAYLOAD))
    inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
                            embeddings=SimpleNamespace(create=embed_create))
    return SimpleNamespace(with_options=lambda **kwargs: (log.append(('opts', kwargs)) or inner))


def run_golden_calls(world, client, entry):
    schema = {'type': 'object'}
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        if entry == 'gateway':
            adapter = OpenAIAdapter()
            first = chat('extract', model='gpt-5-mini', messages=GOLDEN_MESSAGES, schema=('document_reservations', schema),
                         client_factory=lambda: client, adapter=adapter)
            second = chat('answer', model='gpt-5-mini', messages=GOLDEN_MESSAGES, reasoning_effort='low',
                          client_factory=lambda: client, adapter=adapter)
            vectors = embed('embed', model='text-embedding-3-small', texts=['a', 'b'], client_factory=lambda: client, adapter=adapter)
            return first.text, second.text, vectors.vectors
        first = providers.chat_create(client, model='gpt-5-mini', messages=GOLDEN_MESSAGES, response_format={
            'type': 'json_schema', 'json_schema': {'name': 'document_reservations', 'strict': True, 'schema': schema}})
        second = providers.chat_create(client, model='gpt-5-mini', reasoning_effort='low', messages=GOLDEN_MESSAGES)
        vectors = providers.embeddings_create(client, model='text-embedding-3-small', input=['a', 'b'])
        return first.choices[0].message.content, second.choices[0].message.content, [item.embedding for item in vectors.data]


@pytest.mark.parametrize('entry', ['gateway', 'compat_chat_create'])
def test_openai_adapter_request_hash_call_key_estimates_and_receipts_match_head(tmp_path, entry):
    world = metered_world(tmp_path, OPENAI_GOLDEN_PRICES)
    log = []
    texts = run_golden_calls(world, openai_fake(log), entry)
    assert texts == ('{"reservations":[]}', '{"reservations":[]}', [[0.1, 0.2]])
    assert log == GOLDEN_SENT  # with_options(max_retries=0, timeout=45.0) exactly once per call
    assert reservations(world.db) == GOLDEN_ROWS
    receipts = {}
    for path in world.receipts.rglob('*.json'):
        raw = path.read_text(encoding='utf-8')
        value = json.loads(raw)
        assert set(value) == {'call_id', 'request_hash', 'value', 'units'}
        assert raw == json.dumps(value, separators=(',', ':'), ensure_ascii=False)  # compact local receipt bytes
        receipts[value['request_hash']] = (value['value'], value['units'])
    assert receipts == {
        GOLDEN_ROWS[0]['request_hash']: (GOLDEN_CHAT_PAYLOAD, {'input_tokens': 10, 'output_tokens': 3}),
        GOLDEN_ROWS[1]['request_hash']: (GOLDEN_CHAT_PAYLOAD, {'input_tokens': 10, 'output_tokens': 3}),
        GOLDEN_ROWS[2]['request_hash']: (GOLDEN_EMBED_PAYLOAD, {'input_tokens': 5}),
    }
    # The same job/prefix again replays stored receipts: no second send, same answer.
    again = run_golden_calls(world, openai_fake(log), entry)
    assert again == texts and log == GOLDEN_SENT
    assert reservations(world.db) == GOLDEN_ROWS


@pytest.mark.parametrize('requested,sent', [(None, 4096), (10000, 4096), (100, 100)])
def test_openai_output_cap_never_exceeds_policy_bound(tmp_path, requested, sent):
    world = metered_world(tmp_path, OPENAI_GOLDEN_PRICES)
    log = []
    with metered_context(world.gateway, world.ctx, 'cap'):
        chat('extract', model='gpt-5-mini', messages=GOLDEN_MESSAGES, client_factory=lambda: openai_fake(log),
             adapter=OpenAIAdapter(), limits=Limits(max_output_tokens=requested) if requested else None)
    assert [entry for entry in log if entry[0] == 'opts'] == [('opts', {'max_retries': 0, 'timeout': 45.0})]
    assert log[1][1]['max_completion_tokens'] == sent
    assert json.loads(reservations(world.db)[0]['estimated_units_json'])['output_tokens'] == sent


# The real parser request (DOCUMENT_SYSTEM_PROMPT + mail + DOCUMENT_RESERVATIONS_SCHEMA).
# Same values from the pre-registry HEAD parser for this synthetic mail
# (scratch comparison 2026-10-21): a change in message assembly would make
# in-flight jobs disagree with their stored receipts.
PARSER_GOLDEN_MAIL = ('SYNTHETIC TEST DATA. Demo Bistro dinner reservation confirmed for 2026-11-07 at 19:00. '
                      'Reference SYN-DIN-7.')
PARSER_GOLDEN_ROW = {'call_key': 'job:job1:d:extract:chat:0',
                     'request_hash': '7e878338605573bafb60212c4ac5e24772fc82896bbbe711dda0167cc8e50f92',
                     'estimated_units': {'input_tokens': 4160, 'output_tokens': 4096}}


def test_parser_document_request_hash_matches_head(tmp_path, monkeypatch):
    import src.parser as parser
    snippet = 'Demo Bistro dinner reservation confirmed for 2026-11-07 at 19:00.'
    payload = deepcopy(GOLDEN_CHAT_PAYLOAD)
    payload['choices'][0]['message']['content'] = document(booking(snippet, provider='Demo Bistro', confirmation_number='SYN-DIN-7'))
    log = []

    def create(**kwargs):
        log.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3), model_dump=lambda **_: deepcopy(payload))
    client = SimpleNamespace(with_options=lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(parser, '_client', lambda: client)
    monkeypatch.delenv('MAIL_EXTRACTION_CHAIN', raising=False)
    world = metered_world(tmp_path, OPENAI_GOLDEN_PRICES)
    with metered_context(world.gateway, world.ctx, 'd:extract'):
        result = parser.extract_document_reservations(PARSER_GOLDEN_MAIL, model='gpt-5-mini')
    assert [b['confirmation_number'] for b in result['bookings']] == ['SYN-DIN-7'] and result['repair_attempts'] == 0
    rows = reservations(world.db)
    assert [{'call_key': r['call_key'], 'request_hash': r['request_hash'],
             'estimated_units': json.loads(r['estimated_units_json'])} for r in rows] == [PARSER_GOLDEN_ROW]
    assert len(log) == 1 and log[0]['messages'] == extraction_messages(PARSER_GOLDEN_MAIL)
    assert log[0]['response_format']['json_schema']['name'] == 'document_reservations'


def test_openai_status_errors_keep_their_mapping():
    import httpx
    from openai import APIStatusError

    def failing(status, headers=None):
        request = httpx.Request('POST', 'https://api.invalid/v1/chat/completions')
        response = httpx.Response(status, headers=headers or {}, request=request)
        error = APIStatusError('synthetic', response=response, body=None)

        def create(**kwargs):
            raise error
        inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return SimpleNamespace(with_options=lambda **kwargs: inner)
    adapter = OpenAIAdapter()
    for status, code, retryable in [(429, 'PROVIDER_RATE_LIMITED', True), (401, 'PROVIDER_AUTH_FAILED', False),
                                    (403, 'PROVIDER_AUTH_FAILED', False), (400, 'PROVIDER_REQUEST_REJECTED', False)]:
        with pytest.raises(ProviderRejected) as raised:
            adapter.send(failing(status, {'retry-after': '2'}), 'chat', {'model': 'm', 'messages': []})
        assert (raised.value.code, raised.value.retryable) == (code, retryable)
    with pytest.raises(ProviderRejected) as raised:
        adapter.send(failing(429, {'retry-after': '2'}), 'chat', {'model': 'm', 'messages': []})
    assert raised.value.retry_after == 2.0
    with pytest.raises(ProviderRejected) as raised:
        adapter.send(failing(422), 'chat', {'model': 'm', 'messages': []})
    assert raised.value.status == 422


# ------------------------------------------------------------- 4. Replay --
def test_replay_key_ignores_model_and_cap_but_not_content():
    schema = {'type': 'object', 'properties': {}}
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'synthetic mail'}]
    openai_small = OpenAIAdapter().chat_request(model='gpt-5-mini', messages=messages, schema=('s', schema))
    openai_capped = OpenAIAdapter().chat_request(model='another-model', messages=messages, schema=('s', schema), max_output_tokens=10)
    anthropic_shaped = AnthropicAdapter().chat_request(model='claude-synthetic', messages=messages, schema=('s', schema), max_output_tokens=99)
    assert request_key(openai_small) == request_key(openai_capped) == request_key(anthropic_shaped)
    changed = OpenAIAdapter().chat_request(model='gpt-5-mini', messages=[messages[0], {'role': 'user', 'content': 'other'}], schema=('s', schema))
    assert request_key(changed) != request_key(openai_small)
    other_schema = OpenAIAdapter().chat_request(model='gpt-5-mini', messages=messages, schema=('s', {'type': 'array'}))
    assert request_key(other_schema) != request_key(openai_small)


def test_replay_record_validation_rejects_unlabelled_or_secret_shaped_records():
    kwargs = OpenAIAdapter().chat_request(model='m', messages=[{'role': 'user', 'content': 'mail-text-sentinel'}])
    good = make_record(kwargs, text='ok', source='handmade', label='synthetic')
    assert validate_record(deepcopy(good))['synthetic'] is True
    assert 'mail-text-sentinel' not in json.dumps(good, ensure_ascii=False)  # only hashes of the request
    bad = []
    bad.append({**good, 'synthetic': False})
    bad.append({**good, 'source': 'production'})
    bad.append({**good, 'version': 99})
    bad.append({**good, 'request_key': 'not-a-hash'})
    bad.append({**good, 'response': {**good['response'], 'finish': 'done'}})
    # Secret-shaped values are assembled at runtime so this file never contains one.
    bad.append({**good, 'response': {**good['response'], 'text': 'sk-' + 'A1b2C3d4' * 3}})
    bad.append({**good, 'label': 'ey' + 'J' + 'x' * 12 + '.ey' + 'J' + 'y' * 12 + '.' + 'z' * 12})
    for record in bad:
        with pytest.raises(ValueError):
            validate_record(record)


def test_replay_miss_creates_no_reservation(tmp_path):
    world = metered_world(tmp_path, {'replay/synthetic-model': token_price()})
    adapter = ReplayAdapter(ReplayStore(tmp_path / 'empty'))
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        with pytest.raises(DomainError) as raised:
            chat('extract', model='synthetic-model', messages=[{'role': 'user', 'content': 'x'}], adapter=adapter)
    assert raised.value.code == 'REPLAY_MISS'
    assert reservations(world.db) == []
    assert len(adapter.misses) == 1


def test_replay_hit_settles_zero_cost_and_replays_receipt(tmp_path):
    world = metered_world(tmp_path, {'replay/synthetic-model': token_price(max_output=512)})
    store = ReplayStore(tmp_path / 'records')
    messages = [{'role': 'user', 'content': 'synthetic question'}]
    put_record(store.directory, OpenAIAdapter().chat_request(model='any', messages=messages), 'synthetic answer', source='handmade')
    adapter = ReplayAdapter(store)
    sends = []
    original = adapter.send
    adapter.send = lambda client, kind, kwargs: (sends.append(kind), original(client, kind, kwargs))[1]
    for _ in range(2):
        with metered_context(world.gateway, world.ctx, 'doc1:extract'):
            result = chat('extract', model='synthetic-model', messages=messages, adapter=adapter)
        assert result.text == 'synthetic answer' and result.finish == 'stop'
        assert result.meta['provider'] == 'replay' and result.meta['replay_source'] == 'handmade'
    assert sends == ['chat']  # second call came from the stored receipt
    rows = reservations(world.db)
    assert len(rows) == 1
    assert rows[0]['provider'] == 'replay' and rows[0]['sku'] == 'synthetic-model' and rows[0]['state'] == 'settled'
    assert rows[0]['actual_cost_micros'] == 0
    assert json.loads(rows[0]['estimated_units_json'])['output_tokens'] == 512
    calls = telemetry_rows(world.db)
    assert [row['replayed'] for row in calls] == [0, 1] and all(row['provider'] == 'replay' for row in calls)


def test_replay_without_price_is_blocked_before_reservation(tmp_path):
    world = metered_world(tmp_path, {})
    store = ReplayStore(tmp_path / 'records')
    messages = [{'role': 'user', 'content': 'synthetic question'}]
    put_record(store.directory, OpenAIAdapter().chat_request(model='any', messages=messages), 'synthetic answer', source='handmade')
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        with pytest.raises(DomainError) as raised:
            chat('extract', model='synthetic-model', messages=messages, adapter=ReplayAdapter(store))
    assert raised.value.code == 'BUDGET_NOT_CONFIGURED'
    assert reservations(world.db) == []


def recording_world(tmp_path, text):
    from src.llm.replay import RecordingAdapter
    world = metered_world(tmp_path, OPENAI_GOLDEN_PRICES)
    payload = deepcopy(GOLDEN_CHAT_PAYLOAD)
    payload['choices'][0]['message']['content'] = text
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3), model_dump=lambda **_: deepcopy(payload))
    client = SimpleNamespace(with_options=lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    store = ReplayStore(tmp_path / 'recorded')
    adapter = RecordingAdapter(OpenAIAdapter(), store, prompt={'id': 'synthetic', 'version': 1, 'hash': '0' * 64})
    return world, client, store, adapter, sent


def record_once(world, client, adapter):
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        return chat('extract', model='gpt-5-mini', messages=GOLDEN_MESSAGES, client_factory=lambda: client, adapter=adapter)


@pytest.mark.parametrize('text', ['{"reservations":[]}', '{"reservations":[{"raw_snippet":"Card authorization hold of 100 USD"}]}'])
def test_recording_adapter_writes_a_live_record_that_replays(tmp_path, text):
    world, client, store, adapter, sent = recording_world(tmp_path, text)
    result = record_once(world, client, adapter)
    assert result.text == text and len(sent) == 1 and adapter.failures == []
    assert [row['state'] for row in reservations(world.db)] == ['settled']
    (record,) = store.records()
    assert record['source'] == 'live' and record['synthetic'] is True and record['label'] == 'openai:gpt-5-mini'
    assert record['response']['text'] == text and record['prompt']['id'] == 'synthetic'
    replayed = ReplayAdapter(store).parse_chat(ReplayAdapter(store).send(None, 'chat', sent[0]).value)
    assert replayed.text == text and replayed.finish == 'stop'


def test_recording_failure_never_changes_the_settled_paid_call(tmp_path):
    # A secret-shaped answer cannot be stored; the paid call must still settle.
    secret_like = 'sk-' + 'A1b2C3d4' * 3
    world, client, store, adapter, sent = recording_world(tmp_path, '{"note":"' + secret_like + '"}')
    result = record_once(world, client, adapter)
    assert result.finish == 'stop' and len(sent) == 1
    assert adapter.failures == ['ValueError'] and not store.directory.exists()
    rows = reservations(world.db)
    assert [row['state'] for row in rows] == ['settled'] and rows[0]['actual_cost_micros'] == 9


# ---------------------------------------------------------- 5. Anthropic --
ANTHROPIC_MODEL = 'claude-synthetic-model'
ANTHROPIC_PAYLOAD = {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant', 'model': ANTHROPIC_MODEL,
                     'content': [{'type': 'text', 'text': '{"reservations":[]}'}], 'stop_reason': 'end_turn',
                     'usage': {'input_tokens': 7, 'output_tokens': 3}}


class FakeStatusError(Exception):
    def __init__(self, status, headers=None):
        super().__init__(f'synthetic status {status}')
        self.status_code = status
        self.response = SimpleNamespace(headers=headers or {})


def anthropic_fake(log, *, payload=None, error=None):
    def create(**kwargs):
        log.append(('create', kwargs))
        if error is not None:
            raise error
        value = deepcopy(payload or ANTHROPIC_PAYLOAD)
        return SimpleNamespace(usage=SimpleNamespace(**value['usage']), model_dump=lambda **_: value)
    return SimpleNamespace(with_options=lambda **kwargs: (log.append(('opts', kwargs)) or SimpleNamespace(messages=SimpleNamespace(create=create))))


def test_anthropic_request_shape_cap_and_usage_through_metered_gateway(tmp_path):
    world = metered_world(tmp_path, {f'anthropic/{ANTHROPIC_MODEL}': token_price(max_output=2048, rate='1')})
    log = []
    schema = {'type': 'object', 'properties': {'reservations': {'type': 'array'}}}
    adapter = AnthropicAdapter(client_factory=lambda: anthropic_fake(log))
    messages = [{'role': 'system', 'content': 'synthetic rules'}, {'role': 'user', 'content': 'synthetic mail'}]
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        result = chat('extract', model=ANTHROPIC_MODEL, messages=messages, schema=('document_reservations', schema),
                      limits=Limits(max_output_tokens=99999), adapter=adapter, client_factory=lambda: pytest.fail('OpenAI factory used'))
    assert log[0] == ('opts', {'max_retries': 0, 'timeout': 45.0}) and len(log) == 2
    sent = log[1][1]
    assert sent == {'model': ANTHROPIC_MODEL, 'messages': [{'role': 'user', 'content': 'synthetic mail'}],
                    'system': 'synthetic rules', 'output_config': {'format': {'type': 'json_schema', 'schema': schema}},
                    'max_tokens': 2048}
    assert result.text == '{"reservations":[]}' and result.finish == 'stop'
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 3)
    row = reservations(world.db)[0]
    assert row['provider'] == 'anthropic' and row['sku'] == ANTHROPIC_MODEL and row['call_key'] == 'job:job1:doc1:extract:chat:0'
    assert json.loads(row['actual_units_json']) == {'input_tokens': 7, 'output_tokens': 3}
    assert row['actual_cost_micros'] == 10 and json.loads(row['estimated_units_json'])['output_tokens'] == 2048


@pytest.mark.parametrize('stop,finish', [('end_turn', 'stop'), ('stop_sequence', 'stop'), ('max_tokens', 'length'),
                                         ('refusal', 'refusal'), ('tool_use', 'tool_calls'), ('pause_turn', 'other')])
def test_anthropic_stop_reason_mapping(stop, finish):
    payload = {**ANTHROPIC_PAYLOAD, 'stop_reason': stop}
    if stop == 'tool_use':
        payload['content'] = [{'type': 'tool_use', 'id': 'toolu_synthetic', 'name': 'lookup', 'input': {'day': '2026-11-07'}}]
    result = AnthropicAdapter().parse_chat(payload)
    assert result.finish == finish
    assert (result.refusal is not None) == (finish == 'refusal')
    if stop == 'tool_use':
        assert result.text is None
        assert [(c.id, c.name, json.loads(c.arguments)) for c in result.tool_calls] == [('toolu_synthetic', 'lookup', {'day': '2026-11-07'})]


@pytest.mark.parametrize('status,code,retryable', [(429, 'PROVIDER_RATE_LIMITED', True), (401, 'PROVIDER_AUTH_FAILED', False),
                                                   (403, 'PROVIDER_AUTH_FAILED', False), (400, 'PROVIDER_REQUEST_REJECTED', False)])
def test_anthropic_status_error_mapping(status, code, retryable):
    log = []
    with pytest.raises(ProviderRejected) as raised:
        AnthropicAdapter().send(anthropic_fake(log, error=FakeStatusError(status, {'retry-after': '1'})), 'chat', {'model': 'm'})
    assert (raised.value.code, raised.value.retryable) == (code, retryable)
    if status == 400:
        assert raised.value.status == 422
    # A non-status failure is not claimed as "not sent": it stays unknown.
    with pytest.raises(RuntimeError):
        AnthropicAdapter().send(anthropic_fake(log, error=RuntimeError('synthetic transport loss')), 'chat', {'model': 'm'})


def test_anthropic_auth_failure_through_gateway_is_a_domain_error(tmp_path):
    world = metered_world(tmp_path, {f'anthropic/{ANTHROPIC_MODEL}': token_price()})
    log = []
    adapter = AnthropicAdapter(client_factory=lambda: anthropic_fake(log, error=FakeStatusError(401)))
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        with pytest.raises(DomainError) as raised:
            chat('extract', model=ANTHROPIC_MODEL, messages=[{'role': 'user', 'content': 'x'}], adapter=adapter)
    assert raised.value.code == 'PROVIDER_AUTH_FAILED'
    assert [row['state'] for row in reservations(world.db)] == ['pending_reconciliation']


def test_anthropic_without_sdk_fails_before_reservation(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'anthropic', None)  # import anthropic -> ImportError
    world = metered_world(tmp_path, {f'anthropic/{ANTHROPIC_MODEL}': token_price()})
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        with pytest.raises(DomainError) as raised:
            chat('extract', model=ANTHROPIC_MODEL, messages=[{'role': 'user', 'content': 'x'}], adapter=AnthropicAdapter())
    assert raised.value.code == 'PROVIDER_SDK_MISSING'
    assert reservations(world.db) == []


def test_anthropic_without_price_is_blocked_before_send(tmp_path):
    world = metered_world(tmp_path, {})
    log = []
    with metered_context(world.gateway, world.ctx, 'doc1:extract'):
        with pytest.raises(DomainError) as raised:
            chat('extract', model=ANTHROPIC_MODEL, messages=[{'role': 'user', 'content': 'x'}],
                 adapter=AnthropicAdapter(client_factory=lambda: anthropic_fake(log)))
    assert raised.value.code == 'BUDGET_NOT_CONFIGURED'
    assert log == [] and reservations(world.db) == []


def test_no_pricing_file_enables_anthropic():
    for path in ROOT.glob('deploy/**/*.json'):
        text = path.read_text(encoding='utf-8')
        if '"prices"' in text:
            assert 'anthropic/' not in text, path.name


def test_anthropic_and_replay_have_no_embeddings(tmp_path):
    world = metered_world(tmp_path, {f'anthropic/{ANTHROPIC_MODEL}': token_price()})
    with metered_context(world.gateway, world.ctx, 'doc1:embed'):
        for adapter in (AnthropicAdapter(client_factory=lambda: anthropic_fake([])), ReplayAdapter(ReplayStore(tmp_path / 'r'))):
            with pytest.raises(DomainError) as raised:
                embed('embed', model=ANTHROPIC_MODEL, texts=['x'], adapter=adapter)
            assert raised.value.code == 'PROVIDER_CAPABILITY_MISSING'
    assert reservations(world.db) == []


# ------------------------------------------- 6. chain / resolve / capability --
def test_chain_defaults_and_paid_fallback_gate(monkeypatch):
    monkeypatch.delenv('LLM_PAID_FALLBACK', raising=False)
    assert chain(None) == ('openai',)
    assert chain('') == ('openai',)
    assert chain(' OpenAI , anthropic ') == ('openai',)
    assert chain('replay,openai,anthropic') == ('replay', 'openai')
    assert chain('openai,openai') == ('openai',)
    monkeypatch.setenv('LLM_PAID_FALLBACK', '1')
    assert chain('openai,anthropic') == ('openai', 'anthropic')
    for value in ('gemini', 'openai,unknown'):
        with pytest.raises(DomainError) as raised:
            chain(value)
        assert raised.value.code == 'PROVIDER_NOT_SUPPORTED'


def test_resolve_uses_configuration_and_scope_wins(monkeypatch, tmp_path):
    for name in ('MAIL_EXTRACTION_CHAIN', 'ANSWER_PROVIDER', 'EMBEDDING_PROVIDER', 'AGENT_PROVIDER', 'LLM_REPLAY_DIR'):
        monkeypatch.delenv(name, raising=False)
    for purpose in ('extract', 'answer', 'embed', 'agent'):
        assert isinstance(resolve(purpose), OpenAIAdapter)
    with pytest.raises(DomainError):
        resolve('translate')
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'replay')
    with pytest.raises(DomainError) as raised:
        resolve('extract')
    assert raised.value.code == 'REPLAY_NOT_CONFIGURED'
    monkeypatch.setenv('LLM_REPLAY_DIR', str(tmp_path))
    assert isinstance(resolve('extract'), ReplayAdapter)
    assert isinstance(resolve('answer'), OpenAIAdapter)
    bound = ReplayAdapter(ReplayStore(tmp_path))
    with adapter_scope(bound):
        assert resolve('answer') is bound
    assert isinstance(resolve('answer'), OpenAIAdapter)


def capability_env(tmp_path, prices):
    from tests.test_reliability_runtime import construct
    env = construct(tmp_path, policy=policy_with(prices))
    env.documents.parser = None
    env.documents.settings.mail_analysis_mode = 'auto'
    return env


def test_mail_capabilities_default_configuration_is_unchanged(tmp_path, monkeypatch):
    for name in ('MAIL_EXTRACTION_CHAIN', 'ANSWER_PROVIDER', 'EMBEDDING_PROVIDER', 'LLM_PAID_FALLBACK'):
        monkeypatch.delenv(name, raising=False)
    openai_prices = {f'openai/{EXTRACTION_MODEL}': token_price(), f'openai/{EMBEDDING_MODEL}': embedding_price(),
                     f'openai/{ANSWER_MODEL}': token_price()}
    env = capability_env(tmp_path, openai_prices)
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    result = env.operations.mail_capabilities()
    assert (result['analysis_mode'], result['ai_available'], result['reason_code']) == ('ai', True, None)
    monkeypatch.setattr('src.config.OPENAI_API_KEY', None)
    result = env.operations.mail_capabilities()
    assert (result['analysis_mode'], result['reason_code']) == ('local', 'AI_KEY_NOT_CONFIGURED')
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    env.budget.policy = policy_with({f'openai/{EMBEDDING_MODEL}': embedding_price()})
    result = env.operations.mail_capabilities()
    assert (result['analysis_mode'], result['reason_code']) == ('local', 'BUDGET_NOT_CONFIGURED')
    # Injected parser: no key or price check at all, as before.
    env.documents.parser = lambda raw: []
    monkeypatch.setattr('src.config.OPENAI_API_KEY', None)
    assert env.operations.mail_capabilities()['analysis_mode'] == 'ai'


def test_mail_capabilities_replay_extraction_needs_no_openai_extraction_price(tmp_path, monkeypatch):
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'replay')
    monkeypatch.setenv('ANSWER_PROVIDER', 'replay')
    monkeypatch.delenv('EMBEDDING_PROVIDER', raising=False)
    prices = {f'replay/{EXTRACTION_MODEL}': token_price(), f'replay/{ANSWER_MODEL}': token_price(),
              f'openai/{EMBEDDING_MODEL}': embedding_price()}
    env = capability_env(tmp_path, prices)
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    result = env.operations.mail_capabilities()
    assert (result['analysis_mode'], result['reason_code']) == ('ai', None)
    # Embedding is still OpenAI, so its key is still required.
    monkeypatch.setattr('src.config.OPENAI_API_KEY', None)
    result = env.operations.mail_capabilities()
    assert (result['analysis_mode'], result['reason_code']) == ('local', 'AI_KEY_NOT_CONFIGURED')
    # Without the replay price the replay chain is not available either.
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    env.budget.policy = policy_with({f'openai/{EMBEDDING_MODEL}': embedding_price()})
    assert env.operations.mail_capabilities()['reason_code'] == 'BUDGET_NOT_CONFIGURED'


def test_mail_capabilities_rejects_unknown_chain_and_embedding_without_embeddings(tmp_path, monkeypatch):
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    prices = {**replay_runtime_prices(), f'replay/{EMBEDDING_MODEL}': token_price(),
              f'anthropic/{EMBEDDING_MODEL}': token_price()}
    env = capability_env(tmp_path, prices)
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'gemini')
    result = env.operations.mail_capabilities()
    assert (result['analysis_mode'], result['reason_code']) == ('local', 'PROVIDER_NOT_SUPPORTED')
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'replay')
    for provider in ('replay', 'anthropic'):
        monkeypatch.setenv('EMBEDDING_PROVIDER', provider)
        result = env.operations.mail_capabilities()
        assert (result['analysis_mode'], result['reason_code']) == ('local', 'PROVIDER_CAPABILITY_MISSING'), provider
