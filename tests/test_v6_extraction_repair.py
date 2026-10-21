"""v6 stage 2: one schema repair for document extraction (synthetic replay only).

Every model answer here is a handmade replay record (source handmade_fault /
handmade). The counts below prove control flow and ledger shape, not quality.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.config import EXTRACTION_MODEL
from src.llm import adapter_scope
from src.llm.replay import ReplayAdapter, ReplayStore
from src.reliability.providers import metered_context
import src.parser as parser
from tests.test_v6_llm_gateway import (booking, document, extraction_kwargs, metered_world, put_record, repair_kwargs,
                                       reservations, telemetry_rows, token_price)

RAW = ('SYNTHETIC TEST DATA. Demo Bistro dinner reservation confirmed for 2026-11-07 at 19:00. '
       'Reference SYN-DIN-7. mail-body-sentinel-7781')
GOOD = document(booking('Demo Bistro dinner reservation confirmed for 2026-11-07 at 19:00.', provider='Demo Bistro',
                        confirmation_number='SYN-DIN-7'))
INVALID_SNIPPET = document(booking('A sentence that the mail never contained.', provider='Demo Bistro'))
INVALID_TIME = document(booking('Reference SYN-DIN-7.', provider='Demo Bistro', time='7pm'))


@pytest.fixture
def world(tmp_path):
    # Policy output bound 8192 so the repair's own 1000-token cap is visible.
    value = metered_world(tmp_path, {f'replay/{EXTRACTION_MODEL}': token_price(max_output=8192)})
    value.store = ReplayStore(tmp_path / 'replay')
    return value


def run(world, raw=RAW, **kwargs):
    with adapter_scope(ReplayAdapter(world.store)):
        with metered_context(world.gateway, world.ctx, 'doc1:extract'):
            return parser.extract_document_reservations(raw, **kwargs)


def outputs(rows):
    return [json.loads(row['estimated_units_json'])['output_tokens'] for row in rows]


def test_valid_answer_makes_no_repair_call(world):
    put_record(world.store.directory, extraction_kwargs(RAW), GOOD, source='handmade')
    result = run(world)
    assert result['repair_attempts'] == 0 and 'repair_prompt' not in result
    assert [b['confirmation_number'] for b in result['bookings']] == ['SYN-DIN-7']
    assert result['prompt']['id'] == 'mail_document_extraction'
    rows = reservations(world.db)
    assert [row['call_key'] for row in rows] == ['job:job1:doc1:extract:chat:0']
    calls = telemetry_rows(world.db)
    assert [(c['operation'], c['schema_valid'], c['repair_attempts'], c['outcome']) for c in calls] == [('extract', 1, 0, 'ok')]


@pytest.mark.parametrize('first', [INVALID_SNIPPET, INVALID_TIME, '{"reservations": [', ''])
def test_schema_violation_is_repaired_once_with_own_call_key_and_cap(world, monkeypatch, first):
    monkeypatch.setattr(parser, 'REPAIR_MAX_COMPLETION_TOKENS', 1000)
    put_record(world.store.directory, extraction_kwargs(RAW), first)
    put_record(world.store.directory, repair_kwargs(RAW, first), GOOD, source='handmade')
    result = run(world)
    assert result['repair_attempts'] == 1
    assert result['repair_prompt']['id'] == 'mail_extraction_repair'
    assert [b['provider'] for b in result['bookings']] == ['Demo Bistro']
    rows = reservations(world.db)
    assert [row['call_key'] for row in rows] == ['job:job1:doc1:extract:chat:0', 'job:job1:doc1:extract:repair:chat:0']
    assert outputs(rows) == [8192, 1000]
    assert all(row['state'] == 'settled' and row['actual_cost_micros'] == 0 for row in rows)
    calls = telemetry_rows(world.db)
    assert [(c['operation'], c['prompt_id'], c['schema_valid'], c['repair_attempts'], c['outcome']) for c in calls] == [
        ('extract', 'mail_document_extraction', 0, 0, 'schema_invalid'),
        ('extract_repair', 'mail_extraction_repair', 1, 1, 'repaired')]
    # Telemetry stores identities and numbers only, never the mail text.
    assert 'mail-body-sentinel-7781' not in json.dumps(calls, ensure_ascii=False)


def test_failed_repair_raises_extraction_failed_after_exactly_one_repair(world):
    put_record(world.store.directory, extraction_kwargs(RAW), INVALID_SNIPPET)
    put_record(world.store.directory, repair_kwargs(RAW, INVALID_SNIPPET), INVALID_TIME)
    with pytest.raises(parser.ExtractionFailed) as raised:
        run(world)
    failed = raised.value
    assert isinstance(failed, ValueError)
    assert (failed.repair_attempts, failed.kind) == (1, 'invalid')
    assert any(line.startswith('reservations[0].time') for line in failed.errors)
    assert len(failed.calls) == 2
    assert [row['call_key'] for row in reservations(world.db)] == ['job:job1:doc1:extract:chat:0', 'job:job1:doc1:extract:repair:chat:0']
    calls = telemetry_rows(world.db)
    assert [(c['schema_valid'], c['repair_attempts'], c['outcome']) for c in calls] == [(0, 0, 'schema_invalid'), (0, 1, 'repair_failed')]


def test_repair_answer_cut_short_reports_incomplete(world):
    put_record(world.store.directory, extraction_kwargs(RAW), INVALID_SNIPPET)
    put_record(world.store.directory, repair_kwargs(RAW, INVALID_SNIPPET), '{"reserv', finish='length')
    with pytest.raises(parser.ExtractionFailed) as raised:
        run(world)
    assert (raised.value.repair_attempts, raised.value.kind) == (1, 'incomplete')
    assert '완료되지' in str(raised.value)


@pytest.mark.parametrize('finish,refusal', [('length', None), ('refusal', 'synthetic refusal'), ('stop', 'synthetic refusal')])
def test_truncated_or_refused_answer_is_not_repaired(world, finish, refusal):
    put_record(world.store.directory, extraction_kwargs(RAW), '{"reserv', finish=finish, refusal=refusal)
    with pytest.raises(parser.ExtractionFailed) as raised:
        run(world)
    assert (raised.value.repair_attempts, raised.value.kind) == (0, 'incomplete')
    assert '완료되지' in str(raised.value)
    assert [row['call_key'] for row in reservations(world.db)] == ['job:job1:doc1:extract:chat:0']
    assert [(c['schema_valid'], c['outcome']) for c in telemetry_rows(world.db)] == [(0, 'incomplete')]


def test_repair_can_be_disabled_explicitly(world):
    put_record(world.store.directory, extraction_kwargs(RAW), INVALID_SNIPPET)
    with pytest.raises(parser.ExtractionFailed) as raised:
        run(world, repair=False)
    assert raised.value.repair_attempts == 0
    assert len(reservations(world.db)) == 1


def test_unmetered_legacy_path_never_repairs(monkeypatch):
    """tests/test_foundation_pipeline.py contract: no metering -> one SDK call, ValueError."""
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=INVALID_SNIPPET, refusal=None))])
    monkeypatch.setattr(parser, '_client', lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    with pytest.raises(ValueError, match='raw_snippet'):
        parser.parse_document_reservations(RAW)
    assert len(calls) == 1
    assert calls[0]['response_format']['json_schema']['strict'] is True
    assert 'max_completion_tokens' not in calls[0]


def test_unmetered_replay_scope_never_repairs(tmp_path):
    store = ReplayStore(tmp_path / 'replay')
    put_record(store.directory, extraction_kwargs(RAW), INVALID_SNIPPET)
    with adapter_scope(ReplayAdapter(store)):
        with pytest.raises(parser.ExtractionFailed) as raised:
            parser.extract_document_reservations(RAW)
    assert raised.value.repair_attempts == 0
