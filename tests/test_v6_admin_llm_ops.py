"""v6 stage 2: admin LLM call observations and telemetry linkage.

Synthetic data only: fake SDK clients, no network, no real prices or mail text.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.foundation.db import Database
from src.foundation.repository import DomainError, Repository, utcnow
from src.llm import telemetry
from src.operations.health import llm_call_summary
from src.reliability.budget import Budget, BudgetPolicy, CallContext
from src.reliability.providers import ProviderGateway, chat_create, metered_context
from tests.test_foundation_api import service  # noqa: F401 - fixture

PRIVATE = 'PRIVATE-SYNTHETIC-MARKER'


@pytest.fixture
def admin_service(service):  # noqa: F811
    admin = service.login('llm-ops-admin')
    with service.app.state.db.connect() as con:
        con.execute("UPDATE users SET role='admin' WHERE id=?", (admin.user['id'],))
    return SimpleNamespace(**vars(service), admin=admin)


def _insert(db, *, owner_id, latency, outcome='ok', provider='openai', model='synthetic-model',
            operation='extract', replayed=0, schema_valid=1, repairs=0, cost=10, age_days=0, age_seconds=0,
            call_id='call_' + PRIVATE):
    created = (datetime.now(timezone.utc) - timedelta(days=age_days, seconds=age_seconds)).isoformat()
    with db.connect() as con:
        con.execute(
            'INSERT INTO llm_calls (id,call_id,owner_id,trip_id,job_id,scope_kind,provider,model,operation,prompt_id,'
            'prompt_version,prompt_hash,latency_ms,input_tokens,output_tokens,cost_micros,currency,schema_valid,'
            'repair_attempts,outcome,replayed,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('llm_' + PRIVATE + str(latency) + outcome + str(age_days) + str(replayed), call_id, owner_id, 'trip-' + PRIVATE,
             'job-' + PRIVATE, 'trip', provider, model, operation, 'prompt-' + PRIVATE, 1, 'hash-' + PRIVATE,
             latency, 5, 7, cost, 'USD', schema_valid, repairs, outcome, replayed, created))


def test_admin_operations_reports_llm_calls_nearest_rank_without_identifiers(admin_service):
    db = admin_service.app.state.db
    owner = admin_service.admin.user['id']
    # Ten measured calls 10..100 ms; one failure and one schema-invalid repaired call.
    for latency in range(10, 101, 10):
        outcome = 'EXTRACTION_INVALID' if latency == 30 else ('repaired' if latency == 40 else 'ok')
        _insert(db, owner_id=owner, latency=latency, outcome=outcome, schema_valid=0 if latency == 30 else 1,
                repairs=1 if latency == 40 else 0, age_seconds=100 - latency)
    # A replayed receipt is not a provider call: no latency, cost or failure (counted as `replayed`).
    _insert(db, owner_id=owner, latency=9999, replayed=1)
    # A budget refusal never reached a provider (no reservation): counted as `blocked` only.
    _insert(db, owner_id=owner, latency=1, outcome='BUDGET_EXHAUSTED', call_id=None, cost=None, schema_valid=None)
    # Outside the 7-day window: ignored entirely.
    _insert(db, owner_id=owner, latency=5000, outcome='PROVIDER_OUTCOME_UNKNOWN', age_days=8)
    _insert(db, owner_id=owner, latency=300, provider='anthropic', model='synthetic-claude', operation='answer',
            schema_valid=None, cost=0)

    response = admin_service.admin.client.get('/api/v2/admin/operations')
    assert response.status_code == 200, response.text
    body = response.json()
    observations = body['observations']
    # Existing keys are preserved.
    for key in ('window_samples', 'http_ms_p50', 'http_ms_p95', 'budgets', 'provider_attempts', 'uncertain_charges',
                'queue', 'provider_errors', 'job_errors'):
        assert key in observations
    llm = observations['llm_calls']
    assert llm['available'] is True and llm['window_days'] == 7 and llm['calls'] == 11
    assert llm['replayed'] == 1 and llm['blocked'] == 1 and llm['blocked_by_outcome'] == {'BUDGET_EXHAUSTED': 1}
    groups = {(g['provider'], g['model'], g['operation']): g for g in llm['by_operation']}
    extract = groups[('openai', 'synthetic-model', 'extract')]
    # Nearest rank over [10..100]: p50 = 5th = 50, p95 = ceil(9.5) = 10th = 100
    # (the int((n-1)*p) rule used for HTTP samples would give 90).
    assert (extract['calls'], extract['replayed'], extract['blocked'], extract['latency_samples']) == (10, 1, 1, 10)
    assert extract['p50_ms'] == 50 and extract['p95_ms'] == 100
    assert extract['failure_rate'] == round(1 / 10, 4)
    assert extract['schema_valid_rate'] == round(9 / 10, 4)
    assert extract['repairs'] == 1 and extract['cost_micros'] == 100
    other = groups[('anthropic', 'synthetic-claude', 'answer')]
    assert other['calls'] == 1 and other['p50_ms'] == 300 == other['p95_ms']
    assert other['failure_rate'] == 0 and other['schema_valid_rate'] is None
    assert set(extract) == {'provider', 'model', 'operation', 'calls', 'replayed', 'blocked', 'latency_samples',
                            'p50_ms', 'p95_ms', 'failure_rate', 'schema_valid_rate', 'repairs', 'cost_micros'}
    # Percentiles use only the most recent `sample_cap` sent calls per group.
    capped = llm_call_summary(db, sample_cap=3)
    capped_extract = [g for g in capped['by_operation'] if g['operation'] == 'extract'][0]
    assert capped['sample_cap'] == 3 and capped_extract['latency_samples'] == 3
    assert (capped_extract['p50_ms'], capped_extract['p95_ms'], capped_extract['calls']) == (90, 100, 10)
    # No owner, trip, job, call, prompt identifiers or texts leak into the view.
    text = json.dumps(llm)
    assert PRIVATE not in text and owner not in text
    assert 'owner_id' not in text and 'trip_id' not in text


def test_admin_operations_llm_calls_requires_admin(admin_service):
    member = admin_service.login('llm-ops-member')
    assert member.client.get('/api/v2/admin/operations').status_code == 404
    # No lifespan re-entry: the fixture already runs the app (same as its login clients).
    anonymous = TestClient(admin_service.app)
    try:
        response = anonymous.get('/api/v2/admin/operations')
    finally:
        anonymous.close()
    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_REQUIRED'


def test_admin_operations_llm_calls_unavailable_without_table(admin_service):
    with admin_service.app.state.db.connect() as con:
        con.execute('DROP TABLE llm_calls')
    response = admin_service.admin.client.get('/api/v2/admin/operations')
    assert response.status_code == 200, response.text
    assert response.json()['observations']['llm_calls'] == {'window_days': 7, 'calls': 0, 'by_operation': [],
                                                           'available': False}


# --- telemetry.record linkage through the real metered path -------------------------------

@pytest.fixture
def metered(tmp_path):
    db = Database(tmp_path / 'data.sqlite3')
    with db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    ('A', 'A@example.invalid', 'fake', 'A', 'member', utcnow(), utcnow()))
    trip = Repository(db).create_trip('A', {'title': 'Synthetic', 'start_date': '2026-11-01', 'end_date': '2026-11-03'})
    config = deepcopy(BudgetPolicy.for_tests().config)
    config['prices']['openai/synthetic-model'] = {'currency': 'USD', 'rates_per_million': {'input_tokens': '1', 'output_tokens': '1'},
                                                  'max_units': {'input_tokens': 10000, 'output_tokens': 100}}
    gateway = ProviderGateway(Budget(db, BudgetPolicy(config)), tmp_path / 'receipts')
    return db, CallContext('A', trip['id'], trip['id']), gateway


def _client(fail=False):
    payload = {'id': 'fake', 'object': 'chat.completion', 'created': 0, 'model': 'synthetic-model',
               'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}],
               'usage': {'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6}}
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2), model_dump=lambda **_: payload)

    def create(**_):
        if fail:
            raise RuntimeError('synthetic transport failure')
        return response
    return SimpleNamespace(with_options=lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))


def test_record_links_call_id_tokens_and_cost_after_metered_call(metered):
    db, ctx, gateway = metered
    with metered_context(gateway, ctx, 'chat'):
        answer = chat_create(_client(), model='synthetic-model', messages=[{'role': 'user', 'content': 'MESSAGE-BODY ' + PRIVATE}])
    assert answer.choices[0].message.content == 'ok'
    with db.connect() as con:
        reservation = dict(con.execute('SELECT * FROM usage_reservations').fetchone())
        rows = [dict(r) for r in con.execute('SELECT * FROM llm_calls')]
    assert len(rows) == 1
    row = rows[0]
    assert row['call_id'] == reservation['call_id'] and reservation['state'] == 'settled'
    assert (row['provider'], row['model'], row['operation']) == ('openai', 'synthetic-model', 'chat')
    assert (row['input_tokens'], row['output_tokens']) == (4, 2)
    assert row['cost_micros'] == reservation['actual_cost_micros'] == 6 and row['currency'] == 'USD'
    assert row['owner_id'] == 'A' and row['trip_id'] == ctx.trip_id
    assert row['outcome'] == 'ok' and row['replayed'] == 0 and row['latency_ms'] is not None
    # No prompt or response text is stored.
    assert PRIVATE not in json.dumps(row) and 'MESSAGE-BODY' not in json.dumps(row)


def test_record_keeps_prompt_meta_from_neutral_gateway(metered):
    from src.llm.gateway import chat
    from src.llm.openai_adapter import OpenAIAdapter
    db, ctx, gateway = metered
    client = _client()
    prompt = {'id': 'mail_document_extraction', 'version': 1, 'hash': 'synthetic-hash'}
    with metered_context(gateway, ctx, 'request-1'):
        result = chat('extract', model='synthetic-model', messages=[{'role': 'user', 'content': 'synthetic'}],
                      adapter=OpenAIAdapter(), client_factory=lambda: client, prompt=prompt, operation='extract')
    assert result.text == 'ok' and result.meta['telemetry_id']
    with db.connect() as con:
        row = dict(con.execute('SELECT * FROM llm_calls WHERE id=?', (result.meta['telemetry_id'],)).fetchone())
        call_id = con.execute('SELECT call_id FROM usage_reservations').fetchone()[0]
    assert row['call_id'] == call_id and row['operation'] == 'extract'
    assert (row['prompt_id'], row['prompt_version'], row['prompt_hash']) == ('mail_document_extraction', 1, 'synthetic-hash')


def test_failed_call_records_outcome_and_keeps_error(metered):
    db, ctx, gateway = metered
    with metered_context(gateway, ctx, 'chat'):
        with pytest.raises(DomainError) as error:
            chat_create(_client(fail=True), model='synthetic-model', messages=[{'role': 'user', 'content': 'x'}])
    assert error.value.code == 'PROVIDER_OUTCOME_UNKNOWN'
    with db.connect() as con:
        row = dict(con.execute('SELECT * FROM llm_calls').fetchone())
        state = con.execute('SELECT state FROM usage_reservations').fetchone()[0]
    assert row['outcome'] == 'PROVIDER_OUTCOME_UNKNOWN' and row['cost_micros'] is None and state == 'unknown'
    # The send happened (reservation is 'unknown'), so it is a provider-call failure, not a block.
    group = llm_call_summary(db)['by_operation'][0]
    assert (group['calls'], group['blocked'], group['failure_rate'], group['cost_micros']) == (1, 0, 1.0, 0)


def test_replay_and_budget_block_match_the_ledger(metered):
    db, ctx, gateway = metered
    messages = [{'role': 'user', 'content': 'x'}]
    with metered_context(gateway, ctx, 'resumed'):
        chat_create(_client(), model='synthetic-model', messages=messages)
    # A resumed job reuses the same key: the stored receipt is replayed, the client is never called.
    with metered_context(gateway, ctx, 'resumed'):
        assert chat_create(_client(fail=True), model='synthetic-model', messages=messages).choices[0].message.content == 'ok'
    tight = deepcopy(gateway.budget.policy.config)
    for caps in tight['limits'].values():
        for name in caps:
            caps[name] = 1
    blocked = ProviderGateway(Budget(db, BudgetPolicy(tight)), gateway.result_dir.parent / 'receipts-tight')
    with metered_context(blocked, ctx, 'over-budget'):
        with pytest.raises(DomainError) as error:
            chat_create(_client(), model='synthetic-model', messages=messages)
    with db.connect() as con:
        ledger = con.execute("SELECT count(*) n, COALESCE(SUM(actual_cost_micros),0) cost FROM usage_reservations WHERE state='settled'").fetchone()
        rows = [dict(r) for r in con.execute('SELECT outcome,replayed,call_id,latency_ms FROM llm_calls ORDER BY created_at')]
    assert len(rows) == 3 and rows[1]['replayed'] == 1 and rows[2]['call_id'] is None
    sent_latency = rows[0]['latency_ms']
    llm = llm_call_summary(db)
    group = llm['by_operation'][0]
    assert (ledger['n'], ledger['cost']) == (1, 6)
    assert (llm['calls'], llm['replayed'], llm['blocked']) == (1, 1, 1)
    assert llm['blocked_by_outcome'] == {error.value.code: 1}
    assert group['cost_micros'] == ledger['cost'] and group['calls'] == ledger['n']
    assert group['p50_ms'] == group['p95_ms'] == sent_latency and group['latency_samples'] == 1
    assert group['failure_rate'] == 0.0


def test_telemetry_write_failure_never_changes_call_result_or_billing(metered):
    db, ctx, gateway = metered
    with db.connect() as con:
        con.execute('DROP TABLE llm_calls')
    with metered_context(gateway, ctx, 'chat'):
        answer = chat_create(_client(), model='synthetic-model', messages=[{'role': 'user', 'content': 'x'}])
    assert answer.choices[0].message.content == 'ok'
    with db.connect() as con:
        row = con.execute('SELECT state,actual_cost_micros FROM usage_reservations').fetchone()
    assert (row['state'], row['actual_cost_micros']) == ('settled', 6)
    # A failing provider call still surfaces the gateway's own error, not a telemetry error.
    with metered_context(gateway, ctx, 'chat-2'):
        with pytest.raises(DomainError) as error:
            chat_create(_client(fail=True), model='synthetic-model', messages=[{'role': 'user', 'content': 'x'}])
    assert error.value.code == 'PROVIDER_OUTCOME_UNKNOWN'
    assert telemetry.summary(db)['available'] is False
    assert llm_call_summary(db)['available'] is False


# --- legacy agent: date parser without LangChain -------------------------------------------

def test_daily_question_date_is_reexported_and_langchain_free():
    from src import agent
    from src.foundation import dates
    assert agent.daily_question_date is dates.daily_question_date
    assert agent.resolve_daily_question_date is dates.daily_question_date
    assert dates.daily_question_date('둘째 날 일정', '2026-11-06') == '2026-11-07'
    code = ('import sys; from src.foundation.dates import daily_question_date; '
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('langchain','langgraph','langchain_core','openai')))")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parents[1])
    assert out.stdout.strip() == '[]'


def test_product_resolve_day_does_not_load_langchain():
    code = ('import sys; from src.foundation.search import resolve_day; '
            "trip = {'start_date': '2026-11-06', 'end_date': '2026-11-09'}; "
            "assert resolve_day('둘째 날 일정', trip) == '2026-11-07'; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('langchain','langgraph','langchain_core')))")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parents[1])
    assert out.stdout.strip() == '[]'


@pytest.mark.skipif(not os.getenv('TRAVEL_TEST_POSTGRES_DSN'), reason='Requires disposable loopback PostgreSQL')
def test_llm_call_summary_on_postgres(tmp_path):
    from urllib.parse import urlsplit
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from src.storage.postgres import PostgresDatabase
    dsn = os.environ['TRAVEL_TEST_POSTGRES_DSN']
    assert urlsplit(dsn).hostname in {'127.0.0.1', 'localhost'}
    schema = 'travel_test_' + uuid4().hex
    db = PostgresDatabase(dsn, tmp_path / 'unused.sqlite3', schema=schema)
    try:
        for latency in (10, 20):
            _insert(db, owner_id='pg-owner', latency=latency)
        _insert(db, owner_id='pg-owner', latency=30, replayed=1)
        _insert(db, owner_id='pg-owner', latency=1, outcome='BUDGET_EXHAUSTED', call_id=None, cost=None)
        llm = llm_call_summary(db)
        group = llm['by_operation'][0]
        assert (llm['available'], llm['calls'], llm['replayed'], llm['blocked']) == (True, 2, 1, 1)
        assert (group['p50_ms'], group['p95_ms'], group['cost_micros']) == (10, 20, 20)
    finally:
        db.close()
        with psycopg.connect(dsn, autocommit=True) as con:
            con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
