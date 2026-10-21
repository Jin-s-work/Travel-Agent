"""v6 mail evaluator: scoring rules, local/replay runs, live cost guard.

Synthetic data only. No test makes a network call: local mode never builds a
client, replay mode answers from evals/replay/mail, and live mode is exercised
only up to its refusals (or with a replay-backed stand-in adapter).
"""
import json
import re
import shutil

import pytest

from src.config import EXTRACTION_MODEL, PROJECT_ROOT
from src.evals import mail
from src.evals.gold import load_gold
from src.llm.replay import ReplayAdapter, ReplayStore

THRESHOLDS = json.loads((PROJECT_ROOT / 'evals' / 'thresholds.json').read_text(encoding='utf-8'))
PRICING = PROJECT_ROOT / 'deploy' / 'render-supabase' / 'pricing-openai.json'


def _gold_booking(**values):
    booking = {'type': 'tour', 'provider_name': 'Demo Museum', 'start_local': '2026-11-07T10:00', 'start_tz': None,
               'end_local': None, 'end_tz': None, 'party': {'adults': 2, 'children': None}, 'amount': None,
               'currency': None, 'deposit': None, 'confirmation_code': 'TEST-1', 'status': 'confirmed', 'unscored': []}
    booking.update(values)
    return booking


def _case(bookings, **values):
    case = {'case_id': 'unit-case', 'language': 'en', 'tags': [], 'expected_bookings': bookings,
            'must_review': [], 'forbidden': [], 'decoys': {}}
    case.update(values)
    return case


def _ai(**values):
    booking = {'kind': '투어', 'provider': 'Demo Museum', 'confirmation_number': 'TEST-1', 'date': '2026-11-07',
               'time': '10:00', 'date_end': None, 'time_end': None, 'events': []}
    booking.update(values)
    return booking


# --- normalisation ---------------------------------------------------------
def test_flight_with_two_legs_unfolds_and_seconds_are_cut():
    booking = {'kind': '항공', 'provider': 'Demo Air', 'confirmation_number': 'TEST-AIR', 'events': [
        {'event_type': 'outbound', 'start_local': '2026-11-06T09:00:00', 'end_local': '2026-11-06T11:30:00',
         'start_timezone': 'Asia/Seoul', 'end_timezone': 'Asia/Tokyo'},
        {'event_type': 'return', 'start_local': '2026-11-09T20:00:00', 'end_local': '2026-11-09T22:40:00',
         'start_timezone': 'Asia/Tokyo', 'end_timezone': 'Asia/Seoul'}],
        'party': {'adults': 2, 'children': [], 'children_status': 'none'}}
    units = mail.predicted_units([booking], ['BASIC_EXTRACTION_REVIEW'])
    assert [u['start_local'] for u in units] == ['2026-11-06T09:00', '2026-11-09T20:00']
    assert [u['end_tz'] for u in units] == ['Asia/Tokyo', 'Asia/Seoul']
    assert units[0]['type'] == 'flight' and units[0]['party.children'] == 0 and units[0]['status'] == 'confirmed'


def test_status_party_and_restaurant_mapping():
    local = {'kind': 'restaurant', 'provider': 'X', 'date': '2026-11-01', 'time': None, 'events': [],
             'party': {'adults': 2, 'children': [{'age': None}], 'children_status': 'present'}}
    unit = mail.predicted_units([local], ['RESERVATION_CANCELLED_REVIEW'])[0]
    assert (unit['type'], unit['status'], unit['party.children'], unit['start_local']) == ('restaurant', 'cancelled', 1, '2026-11-01')
    assert mail.predicted_units([local], ['REQUEST_NOT_CONFIRMATION'])[0]['status'] == 'changed'
    unknown = dict(local, party={'adults': None, 'children': [], 'children_status': 'unknown'})
    assert mail.predicted_units([unknown])[0]['party.children'] is None
    ai = mail.predicted_units([_ai()], ai=True)[0]
    assert ai['status'] is None and ai['party.adults'] is None and ai['start_local'] == '2026-11-07T10:00'


def test_field_comparisons():
    assert mail.same_value('amount', '214.50', '214.5')
    assert mail.same_value('deposit', '25-35', '25.0-35')
    assert not mail.same_value('amount', '480', '48')
    assert mail.same_value('confirmation_code', 'TEST-AIR 001', 'test-air001')
    assert mail.provider_match('Hotel Gracery (Shinjuku)', 'hotel gracery')
    assert mail.provider_match('Demo旅館 山水閣', 'ｄｅｍｏ旅館山水閣')
    assert not mail.provider_match('Air', 'Demo Air')  # shorter side < 4 chars
    assert not mail.same_value('start_tz', None, None)


# --- matching and scoring --------------------------------------------------
def test_optimal_matching_and_unmatchable_pairs():
    gold = mail.gold_units(_case([_gold_booking(start_local='2026-11-07T10:00', confirmation_code='A'),
                                  _gold_booking(start_local='2026-11-08T10:00', confirmation_code='B')]))
    preds = mail.predicted_units([_ai(date='2026-11-08', confirmation_number='B'),
                                  _ai(date='2026-11-07', confirmation_number='A')], ai=True)
    assert mail.match(gold, preds) == [(0, 1), (1, 0)]
    stay = mail.predicted_units([_ai(kind='숙소', date='2026-12-01')], ai=True)
    assert mail.match(gold, stay) == []  # different start day and type


def test_score_counts_unscored_null_and_false_positive():
    case = _case([_gold_booking(amount='10', currency='EUR', unscored=['currency'])])
    extraction = {'bookings': [_ai(), _ai(kind='숙소', date='2026-12-01', confirmation_number='X')], 'ai': True}
    row = mail.score_case(case, extraction, 'TEST-1 X')
    counts = row['field_counts']
    assert counts['type'] == {'tp': 1, 'fp': 1, 'fn': 0}
    assert counts['amount'] == {'tp': 0, 'fp': 0, 'fn': 1}
    assert counts['currency'] == {'tp': 0, 'fp': 0, 'fn': 0}  # unscored
    assert counts['status'] == {'tp': 0, 'fp': 0, 'fn': 1}  # AI output has no status
    assert {'count_mismatch', 'false_positive_booking', 'field_mismatch:amount'} <= set(row['failures'])
    # A value where gold says null ("must not be extracted") is a false positive.
    null_case = _case([_gold_booking(confirmation_code=None)])
    assert mail.score_case(null_case, {'bookings': [_ai()], 'ai': True})['field_counts']['confirmation_code']['fp'] == 1


def test_forbidden_decoys_must_review_and_ungrounded_codes():
    case = _case([_gold_booking(start_local='2026-11-18T13:00')],
                 forbidden=['change_as_new_confirmed', 'injection_followed'],
                 decoys={'change_as_new_confirmed': {'field': 'start_local', 'values': ['2026-11-18T15:00']},
                         'injection_followed': {'field': 'booking_count', 'max': 1}},
                 must_review=[{'booking': 0, 'field': 'start_local', 'reason': 'x'},
                              {'booking': 0, 'field': 'status', 'reason': 'y'}])
    bad = mail.score_case(case, {'bookings': [_ai(date='2026-11-18', time='15:00'), _ai(date='2026-11-18', time='15:00')],
                                 'ai': True}, 'TEST-1')
    assert bad['forbidden_violations'] == ['change_as_new_confirmed', 'injection_followed']
    # Date-only start (time withheld) satisfies the review. status is not in the
    # AI schema, so in AI mode it is unmeasurable rather than satisfied for free.
    good = mail.score_case(case, {'bookings': [_ai(date='2026-11-18', time=None)], 'ai': True}, 'TEST-1')
    assert good['forbidden_violations'] == []
    assert good['must_review'] == {'satisfied': 1, 'total': 1, 'unmeasurable': ['status']}
    # Local review reasons cover fields that still hold a value.
    local = {'kind': '투어', 'provider': 'Demo Museum', 'confirmation_number': 'TEST-1', 'date': '2026-11-18',
             'time': '13:00', 'events': [], 'party': None}
    covered = mail.score_case(case, {'bookings': [local], 'review_reasons': ['REQUEST_NOT_CONFIRMATION']}, 'TEST-1')
    assert covered['must_review']['satisfied'] == 2
    missed = mail.score_case(case, {'bookings': [], 'error': 'LOCAL_EXTRACTION_UNSUPPORTED'}, 'TEST-1')
    assert missed['must_review']['satisfied'] == 0 and 'extractor_error:LOCAL_EXTRACTION_UNSUPPORTED' in missed['failures']
    invented = mail.score_case(case, {'bookings': [_ai(confirmation_number='TEST-9')], 'ai': True}, 'mail says TEST-1')
    assert invented['ungrounded_codes'] == 1


def test_prf_edge_cases():
    assert mail.prf(0, 0, 0)['f1'] is None
    assert mail.prf(0, 0, 3)['f1'] == 0.0 and mail.prf(0, 0, 3)['precision'] is None
    assert mail.prf(2, 1, 1)['f1'] == pytest.approx(0.6667, abs=1e-4)


# --- whole runs ------------------------------------------------------------
def test_local_baseline_matches_thresholds_and_makes_no_provider_call(monkeypatch, tmp_path):
    from src.reliability.providers import ProviderGateway
    monkeypatch.setattr(ProviderGateway, 'run', lambda *a, **k: pytest.fail('local mode called the gateway'))
    result = mail.run('local')
    assert result['synthetic'] is True and result['mode'] == 'local' and len(result['cases']) == len(load_gold())
    baseline = THRESHOLDS['mail_local']['baseline']
    assert result['metrics']['micro']['f1'] == baseline['micro_f1']
    assert result['metrics']['forbidden_violations'] == THRESHOLDS['mail_local']['max']['forbidden_violations']
    assert all(row['cost_usd'] is None and row['latency_ms'] is None for row in result['cases'])
    json_path, md_path = mail.write_outputs(result, tmp_path / 'local.json')
    assert json.loads(json_path.read_text())['synthetic'] is True
    assert '(synthetic)' in md_path.read_text()


def test_replay_runs_real_parser_path_with_repair_and_planted_faults(monkeypatch):
    from src.llm.openai_adapter import OpenAIAdapter
    monkeypatch.setattr(OpenAIAdapter, 'send', lambda *a, **k: pytest.fail('replay mode reached OpenAI'))
    result = mail.run('replay')
    metrics = result['metrics']
    assert result['quality_claim'] == 'pipeline_test_not_model_quality' and result['synthetic'] is True
    assert metrics['forbidden_by_rule'] == {'change_as_new_confirmed': 1, 'payment_date_as_visit': 1}
    assert metrics['extractor_errors'] == 1 and metrics['repair_attempts'] == 2
    rows = {row['case_id']: row for row in result['cases']}
    assert rows['mtp-02-tokyo-hotel']['extractor'] == {'error': None, 'repair_attempts': 1, 'review_reasons': [],
                                                        'replay_sources': ['handmade_fault', 'handmade_fault']}
    assert rows['mtp-06-date-only-unknown-party']['extractor']['error'] == 'EXTRACTION_INVALID'
    assert rows['mtp-06-date-only-unknown-party']['extractor']['repair_attempts'] == 1
    assert rows['mtp-01-tokyo-roundtrip']['cost_usd'] == 0.0 and rows['mtp-01-tokyo-roundtrip']['llm_calls'] == 1
    assert metrics['micro']['f1'] == THRESHOLDS['mail_replay']['baseline']['micro_f1']
    oracle_fields = ('provider_name', 'start_local', 'end_local', 'confirmation_code')
    clean = [row for row in result['cases'] if not row['extractor']['error'] and not row['forbidden_violations']]
    assert all(row['field_counts'][field]['fp'] == 0 for row in clean for field in oracle_fields)


def test_replay_miss_is_an_extractor_error_not_a_charge(tmp_path):
    result = mail.run('replay', replay_dir=tmp_path / 'empty', max_cases=2)
    assert [row['extractor']['error'] for row in result['cases']] == ['REPLAY_MISS', 'REPLAY_MISS']
    assert all(row['llm_calls'] == 0 for row in result['cases'])


def test_replay_records_are_handmade_hashed_and_reproducible():
    from src.evals import replay_build
    store = ReplayStore(mail.DEFAULT_REPLAY_DIR)
    records = store.records()
    assert records and {r['source'] for r in records} == {'gold_oracle', 'handmade_fault'}
    assert all(r['synthetic'] is True for r in records)
    faults = {r['label'].split(':')[2] for r in records if r['source'] == 'handmade_fault'}
    assert faults == {'schema_violation_repaired', 'repair_failed', 'payment_date_as_visit', 'change_as_new_confirmed'}
    text = ''.join(path.read_text(encoding='utf-8') for path in mail.DEFAULT_REPLAY_DIR.glob('*.json'))
    assert '/' + 'Users/' not in text
    assert not re.search(r'\bs' + r'k-[A-Za-z0-9_-]{20,}|ey' + r'J[A-Za-z0-9_-]{10,}\.ey' + 'J', text)
    # No request messages: only hashes of the request. The response's raw_snippet
    # does hold one line of the synthetic source mail (checked by the next test).
    assert all(set(r) == {'version', 'synthetic', 'source', 'label', 'request_key', 'input_sha256', 'prompt', 'response'}
               for r in records)
    assert replay_build.main(['--check']) == 0


def test_oracle_snippet_is_real_text_or_null():
    from src import parser
    from src.evals import replay_build
    for case in load_gold():
        raw = mail.read_case_text(case)
        payload = replay_build.oracle_payload(case, raw)
        parser.validate_document_reservations(payload, raw)  # raises on a fabricated snippet
        for item in payload['reservations']:
            assert item['kind'] in ('항공', '숙소', '렌터카', '투어', None)


# --- live guard (never sends) ----------------------------------------------
def test_live_requires_caps_and_refuses_bound_over_cap(monkeypatch, capsys, tmp_path):
    out = str(tmp_path / 'live.json')
    with pytest.raises(SystemExit):
        mail.main(['--mode', 'live', '--out', out, '--pricing', str(PRICING), '--max-cases', '2'])
    with pytest.raises(SystemExit, match='예상 상한'):
        mail.main(['--mode', 'live', '--out', out, '--pricing', str(PRICING), '--max-cases', '3', '--max-cost-usd', '0.001'])
    assert 'live_upper_bound_usd' in capsys.readouterr().out
    assert mail.main(['--mode', 'live', '--dry-run', '--out', out, '--pricing', str(PRICING),
                      '--max-cases', '1', '--max-cost-usd', '5']) == 0
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    with pytest.raises(SystemExit, match='OPENAI_API_KEY'):
        mail.main(['--mode', 'live', '--out', out, '--pricing', str(PRICING), '--max-cases', '1', '--max-cost-usd', '5'])
    assert not (tmp_path / 'live.json').exists()


def test_live_bound_counts_repair_worst_case():
    pricing = json.loads(PRICING.read_text())
    cases = load_gold()[:2]
    bound = mail.live_upper_bound(cases, [mail.read_case_text(c) for c in cases], pricing)
    price = pricing['prices'][f'openai/{EXTRACTION_MODEL}']
    out_cap = price['max_units']['output_tokens']
    assert all(row['output_tokens_bound'] == 2 * out_cap for row in bound['per_case'])
    assert bound['usd_bound'] == pytest.approx(sum(row['usd_bound'] for row in bound['per_case']), abs=1e-5)
    config = mail.live_policy_config(pricing, 0.25)
    assert set(config['limits']['USD'].values()) == {250000}


class _StandIn(ReplayAdapter):
    """Replay-backed adapter under the OpenAI price key: proves the live path is metered."""
    name = 'openai'


def test_live_path_goes_through_budget_cap(tmp_path):
    pricing = json.loads(PRICING.read_text())
    adapter = _StandIn(ReplayStore(mail.DEFAULT_REPLAY_DIR))
    starved = mail.run('live', max_cases=2, live_adapter=adapter, live_policy=mail.live_policy_config(pricing, 0.000001),
                       workdir=tmp_path / 'a')
    assert [row['extractor']['error'] for row in starved['cases']] == ['BUDGET_EXHAUSTED', 'BUDGET_EXHAUSTED']
    funded = mail.run('live', max_cases=2, live_adapter=adapter, live_policy=mail.live_policy_config(pricing, 1.0),
                      workdir=tmp_path / 'b')
    assert all(row['extractor']['error'] is None and row['llm_calls'] == 1 for row in funded['cases'])
    from src.foundation.db import Database
    with Database(tmp_path / 'b' / 'eval.sqlite3').connect() as con:
        providers = {row['provider'] for row in con.execute('SELECT provider FROM usage_reservations')}
    assert providers == {'openai'}


def test_cli_writes_json_and_markdown(tmp_path):
    shutil.rmtree(tmp_path / 'out', ignore_errors=True)
    assert mail.main(['--mode', 'local', '--out', str(tmp_path / 'out' / 'm.json')]) == 0
    assert (tmp_path / 'out' / 'm.md').read_text(encoding='utf-8').startswith('# mail eval')


# --- review fixes: unmeasurable rules, repair count, live safety -------------
def test_ai_absent_fields_are_really_absent_from_the_schema():
    from src import parser
    item = parser.DOCUMENT_RESERVATIONS_SCHEMA['properties']['reservations']['items']['properties']
    for field in mail.AI_ABSENT_FIELDS:
        assert field.split('.', 1)[0] not in item


def test_forbidden_on_absent_field_is_unmeasurable_per_extractor():
    case = _case([_gold_booking(status='cancelled', amount='30')], forbidden=['cancelled_as_active', 'deposit_as_total'],
                 decoys={'cancelled_as_active': {'field': 'status', 'values': ['confirmed']},
                         'deposit_as_total': {'field': 'amount', 'values': ['30']}},
                 must_review=[{'booking': 0, 'field': 'party.adults', 'reason': 'x'}])
    ai = mail.score_case(case, {'bookings': [_ai()], 'ai': True}, 'TEST-1')
    assert ai['forbidden_violations'] == []
    assert ai['forbidden_unmeasurable'] == ['cancelled_as_active', 'deposit_as_total']
    assert ai['must_review'] == {'satisfied': 0, 'total': 0, 'unmeasurable': ['party.adults']}
    # EVAL-1: the rule parser emits status but never amount/currency/deposit, so in
    # local mode the status rule is measured and the amount rule is unmeasurable.
    local = {'kind': '투어', 'provider': 'Demo Museum', 'confirmation_number': 'TEST-1', 'date': '2026-11-07',
             'time': '10:00', 'events': [], 'party': None, 'status': 'confirmed'}
    scored = mail.score_case(case, {'bookings': [local], 'review_reasons': []}, 'TEST-1')
    assert scored['forbidden_violations'] == ['cancelled_as_active']
    assert scored['forbidden_unmeasurable'] == ['deposit_as_total']


def test_local_absent_fields_match_what_the_rule_parser_really_emits():
    """EVAL-1: LOCAL_ABSENT_FIELDS is checked against parse_local_document's real output keys,
    and the local run reports the two deposit_as_total rules as unmeasurable, not as "0 violations"."""
    from src.evals.gold import load_gold
    keys = set()
    for case in load_gold():
        keys |= {key for booking in mail.extract_local(mail.read_case_text(case))['bookings'] for key in booking}
    assert keys and not keys & set(mail.LOCAL_ABSENT_FIELDS)
    result = mail.run('local')
    pairs = sorted(f"{row['case_id']}:{rule}" for row in result['cases'] for rule in row['forbidden_unmeasurable'])
    assert pairs == ['mtp-03-tokyo-dinner:deposit_as_total', 'syn-17-en-deposit-only:deposit_as_total']
    assert result['unmeasurable_fields'] == list(mail.LOCAL_ABSENT_FIELDS)
    assert '규칙 파서 출력에 없는 필드' in mail.render_markdown(result)


def test_replay_reports_unmeasurable_counts():
    result = mail.run('replay')
    metrics = result['metrics']
    assert metrics['forbidden_unmeasurable_by_rule'] == {'cancelled_as_active': 2, 'deposit_as_total': 2}
    assert metrics['must_review']['unmeasurable_by_field'] == {'party.adults': 1, 'status': 2}
    assert metrics['must_review']['total'] == 3  # start_local only
    assert result['unmeasurable_fields'] == list(mail.AI_ABSENT_FIELDS)
    assert result['ledger']['reservations_by_state'] == {'settled': 37} and result['ledger']['open_reservations'] == 0
    assert '측정 불가' in mail.render_markdown(result)


def test_replay_must_review_zero_is_the_documented_known_gap():
    """EVAL-1 문서: replay must_review 0/3 의 원인을 고정한다. oracle 기록 2건(mtp-08·mtp-10)은 정답 시각을
    그대로 내고 review_reasons 가 없어 구조상 충족 불가, syn-13 은 심어 둔 결함. 바뀌면 thresholds note·README 도 고친다."""
    result = mail.run('replay')
    rows = {row['case_id']: row for row in result['cases']}
    missed = sorted(f"{row['case_id']}:{failure}" for row in result['cases'] for failure in row['failures']
                    if failure.startswith('missed_review:'))
    assert missed == ['mtp-08-madrid-time-change:missed_review:start_local',
                      'mtp-10-dst-ambiguous-madrid:missed_review:start_local',
                      'syn-13-es-change-request-pending:missed_review:start_local']
    assert result['metrics']['must_review']['satisfied'] == 0 and result['metrics']['must_review']['total'] == 3
    for case_id in ('mtp-08-madrid-time-change', 'mtp-10-dst-ambiguous-madrid'):
        row = rows[case_id]
        assert row['extractor']['replay_sources'] == ['gold_oracle'] and row['extractor']['review_reasons'] == []
        assert row['field_counts']['start_local'] == {'tp': 1, 'fp': 0, 'fn': 0}  # 정답 시각을 그대로 냄
    assert rows['syn-13-es-change-request-pending']['extractor']['replay_sources'] == ['handmade_fault']
    note = THRESHOLDS['mail_replay']['note']
    assert all(token in note for token in ('0/3', 'mtp-08', 'mtp-10', 'syn-13', 'gold_oracle', 'known gap'))


def test_failed_repair_call_still_counts_as_a_repair_attempt(tmp_path):
    replay_dir = tmp_path / 'replay'
    shutil.copytree(mail.DEFAULT_REPLAY_DIR, replay_dir)
    for path in replay_dir.glob('*.json'):
        if json.loads(path.read_text(encoding='utf-8'))['label'] == 'mtp-02-tokyo-hotel:fault:schema_violation_repaired:repair':
            path.unlink()
    result = mail.run('replay', replay_dir=replay_dir, case_ids=['mtp-02-tokyo-hotel'])
    assert result['cases'][0]['extractor']['error'] == 'REPLAY_MISS'
    assert result['cases'][0]['extractor']['repair_attempts'] == 1
    assert result['subset'] is True and result['selected_case_ids'] == ['mtp-02-tokyo-hotel']


def test_live_policy_honours_halt_and_never_raises_file_limits():
    pricing = json.loads(PRICING.read_text())
    with pytest.raises(ValueError, match='halted'):
        mail.live_policy_config(dict(pricing, halted=True), 0.10)
    big = mail.live_policy_config(pricing, 100.0)
    assert big['limits']['USD'] == {key: int(pricing['limits']['USD'][key]) for key in mail.LIMIT_KEYS}
    assert mail.effective_cap_usd(big) == min(pricing['limits']['USD'].values()) / 1_000_000
    assert mail.effective_cap_usd(mail.live_policy_config(pricing, 0.05)) == 0.05


def test_live_cli_refuses_halted_pricing(tmp_path):
    pricing = json.loads(PRICING.read_text())
    halted = tmp_path / 'pricing.json'
    halted.write_text(json.dumps(dict(pricing, halted=True)), encoding='utf-8')
    with pytest.raises(SystemExit, match='halted'):
        mail.main(['--mode', 'live', '--dry-run', '--out', str(tmp_path / 'x.json'), '--pricing', str(halted),
                   '--max-cases', '1', '--max-cost-usd', '0.1'])


def test_live_selection_is_stratified_and_recorded(capsys, tmp_path):
    cases = load_gold()
    picked = mail.stratified_case_ids(cases, 5)
    assert picked == mail.stratified_case_ids(cases, 5)  # deterministic
    by_id = {case['case_id']: case for case in cases}
    assert {by_id[case_id]['language'] for case_id in picked} == {'en', 'ja', 'es', 'ko', 'mixed'}
    rules = {rule for case_id in picked for rule in by_id[case_id]['forbidden']}
    assert 'injection_followed' in rules and 'payment_date_as_visit' in rules
    assert not rules & {'cancelled_as_active', 'deposit_as_total'}  # unmeasurable in AI mode: not chosen for coverage
    assert mail.main(['--mode', 'live', '--dry-run', '--out', str(tmp_path / 'x.json'), '--pricing', str(PRICING),
                      '--max-cases', '5', '--max-cost-usd', '0.20']) == 0
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed['case_ids'] == picked and printed['effective_cap_usd'] == 0.2
    explicit = ['syn-02-ja-tour-cancelled', 'syn-15-en-injection']
    assert mail.main(['--mode', 'live', '--dry-run', '--out', str(tmp_path / 'x.json'), '--pricing', str(PRICING),
                      '--max-cases', '2', '--cases', ','.join(explicit), '--max-cost-usd', '0.20']) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])['case_ids'] == explicit
    with pytest.raises(SystemExit, match='정답셋에 없는'):
        mail.main(['--mode', 'live', '--dry-run', '--out', str(tmp_path / 'x.json'), '--pricing', str(PRICING),
                   '--max-cases', '1', '--cases', 'no-such-case', '--max-cost-usd', '0.20'])


def test_live_keeps_ledger_and_receipts_in_a_kept_workdir(tmp_path):
    pricing = json.loads(PRICING.read_text())
    with pytest.raises(ValueError, match='workdir'):
        mail.run('live', max_cases=1, live_adapter=_StandIn(ReplayStore(mail.DEFAULT_REPLAY_DIR)),
                 live_policy=mail.live_policy_config(pricing, 1.0))
    workdir = mail.live_workdir(tmp_path / 'runs' / 'mail-live.json')
    assert workdir.parent == tmp_path / 'runs' and workdir.name.startswith('mail-live-work-')
    result = mail.run('live', case_ids=['mtp-01-tokyo-roundtrip'], live_adapter=_StandIn(ReplayStore(mail.DEFAULT_REPLAY_DIR)),
                      live_policy=mail.live_policy_config(pricing, 1.0), workdir=workdir)
    assert (workdir / 'eval.sqlite3').exists()
    ledger = result['ledger']
    assert ledger['reservations_by_state'] == {'settled': 1} and ledger['open_estimated_cost_usd_upper_bound'] == 0.0


def test_recorder_tags_repair_records_with_the_repair_prompt(tmp_path):
    from src.prompts import load_prompt
    pricing = json.loads(PRICING.read_text())
    store = ReplayStore(tmp_path / 'recorded')
    adapter = mail.recording_adapter(_StandIn(ReplayStore(mail.DEFAULT_REPLAY_DIR)), store)
    result = mail.run('live', case_ids=['mtp-02-tokyo-hotel'], live_adapter=adapter,
                      live_policy=mail.live_policy_config(pricing, 1.0), workdir=tmp_path / 'w')
    assert result['cases'][0]['extractor']['repair_attempts'] == 1
    prompts = sorted(record['prompt']['id'] for record in store.records())
    assert prompts == sorted([load_prompt('mail_document_extraction').meta()['id'],
                              load_prompt('mail_extraction_repair').meta()['id']])
