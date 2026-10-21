"""Question-set evaluation (qa_v1): fixture/question validation, the local and
replay runs on production code, refusal judged from response fields, and the
CI gate thresholds. Synthetic data only; no key, no network, no paid call."""
import copy
import json
import re

import pytest

from src.config import PROJECT_ROOT
from src.evals import gate, qa
from src.llm.replay import ReplayStore

THRESHOLDS = json.loads((PROJECT_ROOT / 'evals' / 'thresholds.json').read_text(encoding='utf-8'))
FIXTURE = qa.load_fixture()
ITEMS = qa.load_questions()
BY_ID = {item['id']: item for item in ITEMS}


@pytest.fixture(scope='module')
def local_result():
    return qa.run('local')


@pytest.fixture(scope='module')
def replay_result():
    return qa.run('replay')


def _row(result, item_id):
    return next(row for row in result['items'] if row['id'] == item_id)


# --- fixture and question set ------------------------------------------------
def test_fixture_and_questions_are_valid_and_cover_every_category():
    assert qa.validate_fixture(FIXTURE) == []
    assert qa.validate_questions(ITEMS, FIXTURE) == []
    assert len(ITEMS) >= 40
    assert {item['category'] for item in ITEMS} == set(qa.CATEGORIES)
    assert any(not item['answerable'] for item in ITEMS) and any(item['answerable'] for item in ITEMS)
    assert any(item['history'] for item in ITEMS if item['category'] == 'anaphora')
    owners = {trip['owner'] for trip in FIXTURE['trips']}
    assert len(owners) == 2 and len(FIXTURE['trips']) == 3
    assert all(trip.get('corrections') is not None for trip in FIXTURE['trips'])


def test_validator_rejects_broken_items():
    broken = copy.deepcopy(ITEMS)
    broken[0]['forbidden_trip_atoms'] = ['Demo Air'] if broken[0]['trip'] != 'A' else ['TEST-HOTEL-002']
    broken[1]['category'] = 'smalltalk'
    broken[2]['expected'] = {'grounding': ['nonsense']}
    problems = qa.validate_questions(broken, FIXTURE)
    assert any('다른 여행의 값이 아닙니다' in problem for problem in problems)
    assert any('범주' in problem for problem in problems)
    assert any('grounding 기대값' in problem for problem in problems)
    assert any('40문항' in problem for problem in qa.validate_questions(ITEMS[:10], FIXTURE))


def test_validator_rejects_fixture_without_correction_or_other_user():
    fixture = copy.deepcopy(FIXTURE)
    for trip in fixture['trips']:
        trip['corrections'] = []
        trip['owner'] = 'qa-user-a'
    problems = qa.validate_fixture(fixture)
    assert any('교정값' in problem for problem in problems)
    assert any('다른 사용자' in problem for problem in problems)


def test_files_carry_no_secret_or_absolute_path():
    for path in [qa.FIXTURE_PATH, qa.QUESTIONS_PATH, *sorted(qa.DEFAULT_REPLAY_DIR.glob('*.json'))]:
        text = path.read_text(encoding='utf-8')
        assert '/Users/' not in text
        assert not re.search(r'sk-[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{10,}', text)


def test_replay_records_are_handmade_synthetic_and_hold_no_question_text():
    records = ReplayStore(qa.DEFAULT_REPLAY_DIR).records()
    assert records
    questions = {item['question'] for item in ITEMS}
    for record in records:
        assert record['synthetic'] is True and record['source'] in qa.REPLAY_SOURCES
        assert record['label'].split(':', 1)[0] in BY_ID
        assert not any(question in json.dumps(record, ensure_ascii=False) for question in questions)
    assert {record['source'] for record in records} == {'handmade', 'handmade_fault'}


def test_hash_embedder_is_deterministic_and_normalised():
    first, second = qa.hash_embed(['호텔 체크인 몇 시야?']), qa.hash_embed(['호텔 체크인 몇 시야?'])
    assert first == second and len(first[0]) == qa.HASH_DIMENSION
    assert abs(sum(value * value for value in first[0]) - 1) < 1e-4
    near = qa.cosine(first[0], qa.hash_embed(['호텔 체크인과 체크아웃 안내'])[0])
    far = qa.cosine(first[0], qa.hash_embed(['Entradas no reembolsables'])[0])
    assert near > far


# --- refusal is decided from fields, never keywords ---------------------------
def test_refusal_is_judged_from_grounding_and_answer_type_not_text():
    from src.config import NO_INFO_MESSAGE
    assert qa.is_refusal({'answer': '체크인은 15:00입니다.', 'grounding': 'refused', 'answer_type': 'refusal'})
    assert qa.is_refusal({'answer': NO_INFO_MESSAGE, 'grounding': 'verified', 'answer_type': 'refusal'})
    assert qa.is_refusal({'answer': '어느 예약인가요?', 'grounding': 'clarify', 'answer_type': 'clarification'})
    # Refusal words in the text do not make a refusal; only the fields do.
    assert not qa.is_refusal({'answer': NO_INFO_MESSAGE + ' 확인할 수 없습니다.', 'grounding': 'verified',
                              'answer_type': 'generated'})
    assert not qa.is_refusal({'answer': '거절합니다', 'grounding': 'structured', 'answer_type': 'fact'})


def test_refusal_metrics_follow_the_refused_field():
    base = {'status': 'ok', 'category': 'unanswerable', 'ungrounded_atoms': 0, 'cross_trip_atoms': [],
            'foreign_sources': 0, 'model_ungrounded_atoms': 0, 'generation_calls': 0, 'reached_semantic': False,
            'cost_usd': 0, 'latency_ms': 1, 'llm_latency_ms': None, 'expectation_ok': True, 'grounding': 'refused',
            'expected_error': None}
    rows = [dict(base, id='u1', answerable=False, refused=True, correct=True),
            dict(base, id='u2', answerable=False, refused=False, correct=False, grounding='structured'),
            dict(base, id='a1', answerable=True, refused=True, correct=False),
            dict(base, id='a2', answerable=True, refused=False, correct=True, grounding='verified')]
    metrics = qa.aggregate(rows)
    assert metrics['refusal_precision'] == 0.5 and metrics['refusal_recall'] == 0.5
    assert metrics['answerable_accuracy'] == 0.5 and metrics['false_refusals'] == 1


# --- local (basic analysis) --------------------------------------------------
def test_local_run_has_no_cross_trip_exposure_no_ungrounded_atom_and_no_call(local_result):
    m = local_result['metrics']
    assert local_result['synthetic'] is True and local_result['suite'] == 'qa_v1'
    assert m['measured'] == len(ITEMS) and m['unmeasured'] == 0
    assert m['cross_trip_exposures'] == 0
    assert m['ungrounded_atoms'] == 0
    assert m['answer_phase_reservations'] == 0  # basic analysis: no embedding, no generation
    assert m['generation_calls'] == 0 and m['semantic_retrievals'] == 0
    assert m['ownership_denied'] == m['ownership_checks'] == 2
    assert m['unexpected_errors'] == 0


def test_local_corrections_win_and_listing_is_sql(local_result):
    for item_id in ('a-cor-01', 'a-cor-02', 'b-cor-01', 'b-cor-02', 'a-list-01'):
        row = _row(local_result, item_id)
        assert row['correct'] and not row['present_excludes'], row
    assert _row(local_result, 'a-list-01')['tools_used'] == ['bookings_on_date']


# --- replay (AI path, hand-made records) -------------------------------------
def test_replay_run_has_no_cross_trip_exposure_and_no_ungrounded_atom(replay_result):
    m = replay_result['metrics']
    assert replay_result['quality_claim'] == 'pipeline_test_not_model_quality'
    assert m['unmeasured'] == 0
    assert m['cross_trip_exposures'] == 0
    assert m['ungrounded_atoms'] == 0
    assert m['generation_calls'] == len(ReplayStore(qa.DEFAULT_REPLAY_DIR).records())
    assert m['cost_usd_total'] == 0


def test_replay_faults_are_replaced_by_the_verifier(replay_result):
    faults = [item['id'] for item in ITEMS if (item.get('replay') or {}).get('source') == 'handmade_fault']
    assert len(faults) >= 3
    for item_id in faults:
        row = _row(replay_result, item_id)
        assert row['replay'][0]['source'] == 'handmade_fault'
        assert row['grounding'] == 'fallback' and row['model_ungrounded_atoms'] >= 1, row
        assert row['correct'] and not row['present_excludes'] and not row['cross_trip_atoms'], row
    # The injection fault named another trip's codes; none reach the answer.
    assert 'TEST-MAD-004' not in _row(replay_result, 'a-inj-02')['answer']


def test_replay_correct_answers_are_verified_generations(replay_result):
    correct = [item['id'] for item in ITEMS if (item.get('replay') or {}).get('label') == 'correct']
    assert correct
    for item_id in correct:
        row = _row(replay_result, item_id)
        assert row['grounding'] == 'verified' and row['answer_type'] == 'generated', row


def test_replay_floor_sweep_reports_each_candidate(replay_result):
    sweep = replay_result['floor_sweep']
    assert [entry['floor'] for entry in sweep['candidates']] == list(qa.FLOOR_CANDIDATES)
    assert 'synthetic' in sweep['note'] and 'hash' in sweep['embedder']
    assert all(entry['questions'] == replay_result['metrics']['semantic_retrievals'] for entry in sweep['candidates'])


def test_missing_replay_record_is_unmeasured_not_wrong(tmp_path):
    result = qa.run('replay', replay_dir=tmp_path / 'empty-replay', sweep=False)
    row = _row(result, 'a-det-01')
    assert row['status'] == 'unmeasured' and row['correct'] is None
    assert result['metrics']['unmeasured'] == len(ReplayStore(qa.DEFAULT_REPLAY_DIR).records())
    assert result['metrics']['measured'] == len(ITEMS) - result['metrics']['unmeasured']
    failures, _notes = gate.check_suite('qa_replay', THRESHOLDS['qa_replay'], result, THRESHOLDS['rules'])
    assert any('unmeasured' in failure for failure in failures)


def test_build_replay_writes_the_records_the_run_needs(tmp_path):
    result = qa.run('replay', replay_dir=tmp_path / 'records', build=True, sweep=False)
    assert result['build']['reached_generation_without_spec'] == []
    written = {path.name for path in (tmp_path / 'records').glob('*.json')}
    assert written == {path.name for path in qa.DEFAULT_REPLAY_DIR.glob('*.json')}
    # Same content too: a changed hand-made answer/label/source in questions.json
    # keeps the request key, so names alone would hide a stale committed record.
    for name in written:
        rebuilt = json.loads((tmp_path / 'records' / name).read_text(encoding='utf-8'))
        stored = json.loads((qa.DEFAULT_REPLAY_DIR / name).read_text(encoding='utf-8'))
        assert rebuilt == stored, name


# --- gate --------------------------------------------------------------------
def test_current_results_pass_the_qa_thresholds(local_result, replay_result):
    for kind, result in (('qa_local', local_result), ('qa_replay', replay_result)):
        failures, notes = gate.check_suite(kind, THRESHOLDS[kind], result, THRESHOLDS['rules'])
        assert failures == [] and notes == [], (kind, failures)


def test_disabled_verifier_fails_the_gate(monkeypatch, tmp_path):
    """Deliberate regression: show generated text without the grounding check."""
    from src.foundation import search

    def unchecked(question, rows, hits, result, trip_id):
        return {'answer': result, 'sources': [search.source(row, trip_id) for row in rows],
                'tools_used': ['search_bookings'], 'grounding': 'verified', 'answer_type': 'generated'}
    monkeypatch.setattr(search, '_checked_answer', unchecked)
    result = qa.run('replay', sweep=False)
    m = result['metrics']
    assert m['ungrounded_atoms'] >= 1 and m['cross_trip_exposures'] >= 1
    failures, _notes = gate.check_suite('qa_replay', THRESHOLDS['qa_replay'], result, THRESHOLDS['rules'])
    assert any('ungrounded_atoms' in failure for failure in failures)
    assert any('cross_trip_exposures' in failure for failure in failures)


def _without(result, drop_ids):
    """A copy of `result` as if the dropped questions had never been in the set."""
    trimmed = copy.deepcopy(result)
    trimmed['items'] = [row for row in trimmed['items'] if row['id'] not in drop_ids]
    trimmed['metrics'] = qa.aggregate(trimmed['items'])
    trimmed['cases'] = [case for case in trimmed['cases'] if case['case_id'] not in drop_ids]
    return trimmed


def test_gate_fails_when_questions_are_removed(local_result, tmp_path):
    """Dropping the 11 unanswerable questions local fails to refuse makes recall
    1.0; the question-set hash must still fail the gate."""
    missed = {row['id'] for row in local_result['items'] if not row['answerable']
              and not row.get('expected_error') and not row['correct']}
    assert len(missed) == 11
    trimmed = _without(local_result, missed)
    assert trimmed['metrics']['refusal_recall'] == 1.0
    failures, _notes = gate.check_suite('qa_local', THRESHOLDS['qa_local'], trimmed, THRESHOLDS['rules'])
    assert any('cases' in failure for failure in failures) and any('hash' in failure for failure in failures)
    # The same through a real run on a trimmed question file (>= 40 questions still validates).
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps({'items': [item for item in ITEMS if item['id'] not in missed]}, ensure_ascii=False),
                    encoding='utf-8')
    rerun = qa.run('local', questions_path=path)
    failures, _notes = gate.check_suite('qa_local', THRESHOLDS['qa_local'], rerun, THRESHOLDS['rules'])
    assert rerun['metrics']['refusal_recall'] == 1.0 and failures


def test_partial_run_fails_the_gate_and_samples_every_category():
    sample = qa.select_questions(ITEMS, 10)
    assert len(sample) == 10
    assert {item['category'] for item in sample} == set(qa.CATEGORIES)
    assert [item['id'] for item in sample] == [item['id'] for item in ITEMS if item in sample]  # file order
    result = qa.run('local', max_questions=10)
    assert result['metrics']['items'] == 10
    failures, _notes = gate.check_suite('qa_local', THRESHOLDS['qa_local'], result, THRESHOLDS['rules'])
    assert any('cases' in failure for failure in failures)


def test_must_include_accepts_alternatives():
    assert qa._contains('도착 48시간 전까지 무료 취소', ['48 horas', '48시간'])
    assert qa._contains('Cancelación gratuita hasta 48 horas antes', ['48 horas', '48시간'])
    assert not qa._contains('도착 24시간 전까지', ['48 horas', '48시간'])
    broken = copy.deepcopy(ITEMS)
    broken[0]['expected']['must_include'] = [[]]
    assert any('둘 중 하나' in problem for problem in qa.validate_questions(broken, FIXTURE))


def test_replay_separates_planted_faults_from_verifier_false_fallbacks(replay_result):
    m = replay_result['metrics']
    planted = sorted(item['id'] for item in ITEMS if (item.get('replay') or {}).get('source') == 'handmade_fault')
    caught = sorted(row['id'] for row in replay_result['items'] if row['planted_fault_caught'])
    assert caught == planted and m['planted_faults_caught'] == len(planted) and m['planted_faults_missed'] == 0
    # b-det-04: a correct Korean paraphrase the verifier throws away. Counted
    # apart from the planted faults; the item stays correct whichever answer is shown.
    assert [row['id'] for row in replay_result['items'] if row['verifier_false_fallback']] == ['b-det-04']
    assert m['fallbacks'] == m['planted_faults_caught'] + m['verifier_false_fallbacks']
    assert _row(replay_result, 'b-det-04')['correct']


def test_gate_fails_when_a_planted_fault_is_no_longer_caught(replay_result):
    broken = copy.deepcopy(replay_result)
    for row in broken['items']:
        if row['id'] == 'a-det-05':
            row['planted_fault_caught'], row['planted_fault_missed'] = False, True
    broken['metrics'] = qa.aggregate(broken['items'])
    for case in broken['cases']:
        if case['case_id'] == 'a-det-05':
            case['planted_fault_caught'] = False
    failures, _notes = gate.check_suite('qa_replay', THRESHOLDS['qa_replay'], broken, THRESHOLDS['rules'])
    assert any('planted_faults_missed' in failure for failure in failures)
    assert any('planted_faults_caught' in failure for failure in failures)
    # A new over-strict fallback on another question fails too (per-question allowlist).
    moved = copy.deepcopy(replay_result)
    for case in moved['cases']:
        case['verifier_false_fallback'] = case['case_id'] == 'b-det-01'
    failures, _notes = gate.check_suite('qa_replay', THRESHOLDS['qa_replay'], moved, THRESHOLDS['rules'])
    assert any('verifier_false_fallback' in failure and 'b-det-01' in failure for failure in failures)


def test_injection_text_relay_is_counted_and_pinned(local_result, replay_result):
    for result in (local_result, replay_result):
        relays = [row['id'] for row in result['items'] if row['injection_text_relay']]
        assert relays == ['a-inj-02']  # known limit, see thresholds max_reason
        assert result['metrics']['must_not_include_hits'] == 0


def test_cost_follows_the_budget_rule_and_flags_unsettled_calls():
    call = {'actual_cost_micros': 7, 'estimated_cost_micros': 100}
    assert qa.call_cost_micros(dict(call, state='released')) == 0
    assert qa.call_cost_micros(dict(call, state='settled')) == 7
    for state in qa.UNSETTLED_STATES:
        assert qa.call_cost_micros(dict(call, state=state)) == 100
    base = {'status': 'ok', 'category': 'detail', 'answerable': True, 'refused': False, 'correct': True,
            'ungrounded_atoms': 0, 'cross_trip_atoms': [], 'foreign_sources': 0, 'model_ungrounded_atoms': 0,
            'generation_calls': 1, 'reached_semantic': True, 'latency_ms': 1, 'llm_latency_ms': None,
            'expectation_ok': True, 'grounding': 'verified', 'expected_error': None}
    rows = [dict(base, id='x1', cost_usd=0.0001, call_states={'unknown': 1, 'settled': 1}),
            dict(base, id='x2', cost_usd=0.0002, call_states={'settled': 2})]
    metrics = qa.aggregate(rows)
    assert metrics['unsettled_calls'] == 1 and metrics['answer_calls_by_state'] == {'settled': 3, 'unknown': 1}
    assert metrics['cost_usd_total'] == 0.0003


def test_replay_run_reports_fixture_cost_and_no_unsettled_call(replay_result):
    m = replay_result['metrics']
    assert m['unsettled_calls'] == 0 and m['run_unsettled_calls'] == 0
    assert m['fixture_cost_usd'] == 0 and m['run_cost_usd_total'] == 0  # replay/fake prices are 0
    assert m['fixture_calls_by_state'] and replay_result['warnings'] == []


def test_ownership_counts_only_expected_errors_and_unexpected_errors_stay_wrong():
    base = {'category': 'detail', 'ungrounded_atoms': 0, 'cross_trip_atoms': [], 'foreign_sources': 0,
            'model_ungrounded_atoms': 0, 'generation_calls': 0, 'reached_semantic': False, 'cost_usd': 0,
            'latency_ms': 1, 'llm_latency_ms': None, 'grounding': None, 'refused': False}
    rows = [dict(base, id='own', category='other_trip', answerable=False, status='error', correct=True,
                 expectation_ok=True, expected_error='NOT_FOUND'),
            dict(base, id='boom', answerable=True, status='error', correct=False, expectation_ok=False,
                 expected_error=None),
            dict(base, id='ok', answerable=True, status='ok', correct=True, expectation_ok=True,
                 expected_error=None, grounding='structured'),
            dict(base, id='oth', category='other_trip', answerable=False, status='ok', correct=True,
                 refused=True, expectation_ok=True, expected_error=None, grounding='refused')]
    metrics = qa.aggregate(rows)
    assert metrics['ownership_checks'] == 1 and metrics['ownership_denied'] == 1
    assert metrics['unexpected_errors'] == 1
    assert metrics['answerable_items'] == 2 and metrics['answerable_accuracy'] == 0.5
    assert metrics['by_category']['other_trip']['items'] == 1
    assert metrics['by_category']['ownership_404'] == {'items': 1, 'measured': 1, 'correct': 1, 'accuracy': 1.0}


def test_floor_estimate_comes_from_the_main_run_similarities(replay_result):
    estimate = replay_result['floor_estimate']
    reached = [row for row in replay_result['items'] if row['reached_semantic']]
    assert reached and all(row['top_similarity'] is not None for row in reached)
    assert all(row['top_similarity'] is None for row in replay_result['items'] if not row['reached_semantic'])
    assert [entry['floor'] for entry in estimate['candidates']] == list(qa.FLOOR_CANDIDATES)
    assert all(entry['questions'] == len(reached) for entry in estimate['candidates'])
    # Same hash similarities as the sweep's own re-query.
    sweep = replay_result['floor_sweep']['per_question_top_similarity']
    assert {row['id']: row['top_similarity'] for row in reached} == sweep
    rows = [{'id': 'a', 'answerable': True, 'top_similarity': 0.5},
            {'id': 'u', 'answerable': False, 'top_similarity': 0.05},
            {'id': 'n', 'answerable': False, 'top_similarity': None}]
    table = {entry['floor']: entry for entry in qa.floor_estimate(rows, 'test')['candidates']}
    assert table[0.1]['precision'] == 1.0 and table[0.1]['recall'] == 1.0 and table[0.1]['questions'] == 2


def test_thresholds_entries_are_filled_for_qa():
    for kind in ('qa_local', 'qa_replay'):
        entry = THRESHOLDS[kind]
        assert entry['suite'] == 'qa_v1' and entry['baseline']
        assert entry['max']['ungrounded_atoms'] == 0 and entry['max']['cross_trip_exposures'] == 0
        assert 'synthetic' in entry['note']
        assert entry['gold']['cases'] == len(ITEMS)
        assert entry['gold']['case_ids_sha256'] == gate.case_ids_sha256(item['id'] for item in ITEMS)
        for name, allowed in entry['max'].items():
            assert allowed == 0 or name in entry.get('max_reason', {}), name


# --- live is implement-only ---------------------------------------------------
def test_live_requires_caps_and_dry_run_makes_no_call(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(SystemExit):
        qa.main(['--mode', 'live', '--out', str(tmp_path / 'live.json')])
    pricing = str(PROJECT_ROOT / 'deploy' / 'render-supabase' / 'pricing-openai.json')
    assert qa.main(['--mode', 'live', '--out', str(tmp_path / 'live.json'), '--max-cost-usd', '5',
                    '--max-questions', '3', '--pricing', pricing, '--dry-run']) == 0
    bound = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert bound['questions'] == 3 and bound['live_upper_bound_usd'] > 0
    assert not (tmp_path / 'live.json').exists()
    with pytest.raises(SystemExit):
        qa.main(['--mode', 'live', '--out', str(tmp_path / 'live.json'), '--max-cost-usd', '0.000001',
                 '--max-questions', '3', '--pricing', pricing])


def test_live_policy_honours_halt_and_file_limits_like_mail():
    """B2: qa live 상한은 mail 과 같은 계약이다 — halted 면 거부, 한도는 min(cap, 파일 한도)."""
    from src.evals import mail
    pricing = json.loads((PROJECT_ROOT / 'deploy' / 'render-supabase' / 'pricing-openai.json').read_text())
    halted = dict(pricing, halted=True,
                  limits={'USD': {key: 100000 for key in mail.LIMIT_KEYS}})
    with pytest.raises(ValueError, match='halted'):
        qa.live_policy_config(halted, 5.0)
    big = qa.live_policy_config(pricing, 5.0)
    assert big['limits']['USD'] == {key: min(5_000_000, int(pricing['limits']['USD'][key])) for key in mail.LIMIT_KEYS}
    assert mail.effective_cap_usd(big) == min(5.0, min(pricing['limits']['USD'].values()) / 1_000_000)
    assert mail.effective_cap_usd(qa.live_policy_config(pricing, 0.05)) == 0.05
    assert {'fake/extract', 'fake/embed', 'fake/query_embed', 'fake/generate'} <= set(big['prices'])


def test_live_cli_refuses_halted_pricing_and_compares_to_effective_cap(tmp_path, capsys):
    pricing = json.loads((PROJECT_ROOT / 'deploy' / 'render-supabase' / 'pricing-openai.json').read_text())
    halted = tmp_path / 'pricing.json'
    halted.write_text(json.dumps(dict(pricing, halted=True)), encoding='utf-8')
    with pytest.raises(SystemExit, match='halted'):
        qa.main(['--mode', 'live', '--dry-run', '--out', str(tmp_path / 'x.json'), '--pricing', str(halted),
                 '--max-questions', '1', '--max-cost-usd', '0.1'])
    tight = tmp_path / 'tight.json'
    tight.write_text(json.dumps(dict(pricing, limits={'USD': {key: 1 for key in pricing['limits']['USD']}})),
                     encoding='utf-8')
    # --max-cost-usd 5 여도 파일 한도(0.000001 USD)가 실제 상한이라 거부된다.
    with pytest.raises(SystemExit, match='실제 상한'):
        qa.main(['--mode', 'live', '--dry-run', '--out', str(tmp_path / 'x.json'), '--pricing', str(tight),
                 '--max-questions', '3', '--max-cost-usd', '5'])
    bound = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert bound['effective_cap_usd'] == 0.000001 and bound['max_cost_usd'] == 5


def test_live_run_requires_kept_workdir():
    """B4: live 는 원장·영수증을 임시 디렉터리에 두지 않는다."""
    with pytest.raises(ValueError, match='workdir'):
        qa.run('live', live_policy={}, max_questions=1)


def test_agent_live_run_requires_kept_workdir_and_main_passes_one(monkeypatch, tmp_path):
    """B4: agent live 도 같은 규칙 — workdir 없으면 거부, main 은 --out 옆 보관 경로를 넘긴다.
    (test_v6_eval_agent.py 는 다른 작업 범위라 여기 둔다. 외부 호출 없음: run 을 가로챈다.)"""
    from src.evals import agent
    with pytest.raises(ValueError, match='workdir'):
        agent.run('live', live_adapter=object(), live_policy={})
    seen = {}

    def fake_run(mode, **kwargs):
        seen.update(kwargs, mode=mode)
        raise SystemExit(0)
    monkeypatch.setattr(agent, '_live_setup', lambda args, scenarios: (object(), {}))
    monkeypatch.setattr(agent, 'run', fake_run)
    with pytest.raises(SystemExit):
        agent.main(['--mode', 'live', '--out', str(tmp_path / 'agent-live.json'), '--max-scenarios', '1',
                    '--max-cost-usd', '0.1', '--pricing', 'unused.json'])
    assert seen['mode'] == 'live' and seen['workdir'] is not None
    assert seen['workdir'].parent == tmp_path and seen['workdir'].name.startswith('agent-live-work-')
    replay = {}
    monkeypatch.setattr(agent, 'run', lambda mode, **kwargs: (replay.update(kwargs), (_ for _ in ()).throw(SystemExit(0))))
    with pytest.raises(SystemExit):
        agent.main(['--mode', 'replay', '--out', str(tmp_path / 'agent-replay.json')])
    assert replay['workdir'] is None


def test_weakened_question_under_the_same_id_fails_the_gate(local_result, tmp_path):
    """EVAL-2: a question's expectation changed under the same id changes questions_sha256."""
    assert local_result['questions_sha256'] == THRESHOLDS['qa_local']['gold']['questions_sha256']
    assert local_result['fixture_sha256'] == THRESHOLDS['qa_local']['gold']['fixture_sha256']
    weak = json.loads(qa.QUESTIONS_PATH.read_text(encoding='utf-8'))
    items = weak['items']
    target = next(item for item in items if item['expected'].get('must_include'))
    target['expected']['must_include'], target['answerable'] = [], not target['answerable']
    path = tmp_path / 'questions.json'
    path.write_text(json.dumps(weak, ensure_ascii=False), encoding='utf-8')
    result = copy.deepcopy(local_result)
    result['questions_sha256'] = qa.file_content_sha256(path)
    failures, _notes = gate.check_suite('qa_local', THRESHOLDS['qa_local'], result, THRESHOLDS['rules'])
    assert any('questions_sha256 가 기준과 다릅니다' in f for f in failures)
    failures, _notes = gate.check_suite('qa_local', THRESHOLDS['qa_local'], local_result, THRESHOLDS['rules'])
    assert failures == []


def test_cli_writes_synthetic_json_and_markdown(tmp_path):
    out = tmp_path / 'qa-local.json'
    assert qa.main(['--mode', 'local', '--out', str(out)]) == 0
    data = json.loads(out.read_text(encoding='utf-8'))
    assert data['synthetic'] is True and data['mode'] == 'local'
    assert '(synthetic)' in out.with_suffix('.md').read_text(encoding='utf-8')
