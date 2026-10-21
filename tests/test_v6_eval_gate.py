"""v6 CI gate: threshold rules and a deliberate regression that must fail.

The regression copies the hand-made replay records to a temporary directory,
breaks one of them, re-runs the real replay evaluation and expects exit 1.
The stored records under evals/replay/mail are never modified.
"""
import copy
import json
import shutil

import pytest

from src.config import PROJECT_ROOT
from src.evals import gate, mail

THRESHOLDS_PATH = PROJECT_ROOT / 'evals' / 'thresholds.json'
THRESHOLDS = json.loads(THRESHOLDS_PATH.read_text(encoding='utf-8'))


def _mail_result(mode, *, f1=0.9, field_f1=0.9, forbidden=0, ungrounded=0, errors=0, synthetic=True):
    return {'suite': 'mail_gold_v1', 'mode': mode, 'synthetic': synthetic, 'metrics': {
        'micro': {'f1': f1}, 'booking_count_accuracy': 1.0, 'must_review': {'recall': 1.0},
        'fields': {'start_local': {'f1': field_f1}}, 'forbidden_violations': forbidden,
        'ungrounded_atoms': ungrounded, 'extractor_errors': errors}}


def _thresholds():
    entry = {'suite': 'mail_gold_v1', 'baseline': {'micro_f1': 0.9, 'field_f1.start_local': 0.9,
                                                   'booking_count_accuracy': 1.0, 'must_review_recall': 1.0},
             'max': {'forbidden_violations': 0, 'ungrounded_atoms': 0, 'extractor_errors': 0}}
    qa = {'suite': None, 'baseline': None, 'max': {'ungrounded_atoms': 0, 'cross_trip_exposures': 0}}
    return {'rules': {'max_drop': 0.02}, 'mail_local': {**entry, 'mode': 'local'},
            'mail_replay': {**copy.deepcopy(entry), 'mode': 'replay'},
            'qa_local': {**qa, 'mode': 'local'}, 'qa_replay': {**copy.deepcopy(qa), 'mode': 'replay'}}


def _write(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding='utf-8')
    return str(path)


def _gate(tmp_path, thresholds, local, replay, qa_local=None, qa_replay=None):
    argv = ['--thresholds', _write(tmp_path, 't.json', thresholds),
            '--mail-local', _write(tmp_path, 'l.json', local), '--mail-replay', _write(tmp_path, 'r.json', replay)]
    if qa_local is not None:
        argv += ['--qa-local', _write(tmp_path, 'ql.json', qa_local)]
    if qa_replay is not None:
        argv += ['--qa-replay', _write(tmp_path, 'qr.json', qa_replay)]
    return gate.main(argv)


def test_gate_passes_at_baseline_and_at_exact_allowed_drop(tmp_path):
    assert _gate(tmp_path, _thresholds(), _mail_result('local'), _mail_result('replay')) == 0
    assert _gate(tmp_path, _thresholds(), _mail_result('local', f1=0.88, field_f1=0.88), _mail_result('replay')) == 0


@pytest.mark.parametrize('change', [
    {'field_f1': 0.879},      # field F1 drop > 0.02
    {'f1': 0.85},             # micro F1 drop
    {'forbidden': 1},         # one forbidden violation
    {'ungrounded': 1},        # one ungrounded atom
    {'errors': 1},            # extractor errors over baseline
    {'synthetic': False},     # non-synthetic result
])
def test_gate_fails_on_each_rule(tmp_path, change, capsys):
    assert _gate(tmp_path, _thresholds(), _mail_result('local', **change), _mail_result('replay')) == 1
    assert 'gate: FAIL mail_local' in capsys.readouterr().out


def test_gate_fails_on_wrong_mode_missing_metric_and_unreadable_file(tmp_path):
    assert _gate(tmp_path, _thresholds(), _mail_result('replay'), _mail_result('replay')) == 1
    broken = _mail_result('local')
    del broken['metrics']['fields']
    assert _gate(tmp_path, _thresholds(), broken, _mail_result('replay')) == 1
    argv = ['--thresholds', _write(tmp_path, 't.json', _thresholds()),
            '--mail-local', str(tmp_path / 'absent.json'), '--mail-replay', _write(tmp_path, 'r.json', _mail_result('replay'))]
    assert gate.main(argv) == 1


def test_nonzero_baseline_count_fails_only_when_it_increases(tmp_path):
    thresholds = _thresholds()
    thresholds['mail_local']['max']['forbidden_violations'] = 2
    assert _gate(tmp_path, thresholds, _mail_result('local', forbidden=2), _mail_result('replay')) == 0
    assert _gate(tmp_path, thresholds, _mail_result('local', forbidden=3), _mail_result('replay')) == 1


def test_qa_slots_check_absolute_limits_without_baseline(tmp_path, capsys):
    good = {'suite': 'qa_v1', 'mode': 'local', 'synthetic': True,
            'metrics': {'answerable_accuracy': 0.9, 'refusal_precision': 1.0, 'refusal_recall': 1.0,
                        'ungrounded_atoms': 0, 'cross_trip_exposures': 0}}
    replay = dict(good, mode='replay')
    assert _gate(tmp_path, _thresholds(), _mail_result('local'), _mail_result('replay'), good, replay) == 0
    assert '기준선 미설정' in capsys.readouterr().out
    leaky = copy.deepcopy(good)
    leaky['metrics']['cross_trip_exposures'] = 1
    assert _gate(tmp_path, _thresholds(), _mail_result('local'), _mail_result('replay'), leaky, replay) == 1
    ungrounded = copy.deepcopy(replay)
    ungrounded['metrics']['ungrounded_atoms'] = 1
    assert _gate(tmp_path, _thresholds(), _mail_result('local'), _mail_result('replay'), good, ungrounded) == 1
    with_baseline = _thresholds()
    with_baseline['qa_local']['baseline'] = {'answerable_accuracy': 0.95}
    assert _gate(tmp_path, with_baseline, _mail_result('local'), _mail_result('replay'), good, replay) == 1


def test_repository_thresholds_are_consistent():
    assert THRESHOLDS['synthetic'] is True and THRESHOLDS['rules']['max_drop'] == 0.02
    for kind in gate.SUITES:
        assert kind in THRESHOLDS
    for kind in ('mail_local', 'mail_replay'):
        entry = THRESHOLDS[kind]
        assert entry['suite'] == 'mail_gold_v1' and entry['baseline']
        for name, allowed in entry['max'].items():
            if allowed > 0:
                assert entry['max_reason'][name]  # every non-zero allowance says why


def test_current_results_pass_the_repository_gate(tmp_path):
    local = mail.write_outputs(mail.run('local'), tmp_path / 'local.json')[0]
    replay = mail.write_outputs(mail.run('replay'), tmp_path / 'replay.json')[0]
    # The required agent and QA parts have their own repository-gate tests
    # (test_v6_eval_agent, test_v6_eval_qa); mail only here.
    mail_only = copy.deepcopy(THRESHOLDS)
    for kind in ('agent_replay', 'qa_local', 'qa_replay'):
        mail_only[kind]['required'] = False
    path = _write(tmp_path, 'mail-only.json', mail_only)
    assert gate.main(['--thresholds', path, '--mail-local', str(local), '--mail-replay', str(replay)]) == 0


def test_deliberately_broken_replay_record_fails_the_gate(tmp_path, capsys):
    broken_dir = tmp_path / 'replay-broken'
    shutil.copytree(mail.DEFAULT_REPLAY_DIR, broken_dir)
    target = next(path for path in broken_dir.glob('*.json')
                  if json.loads(path.read_text(encoding='utf-8'))['label'] == 'syn-04-ja-injection:oracle:first')
    record = json.loads(target.read_text(encoding='utf-8'))
    payload = json.loads(record['response']['text'])
    payload['reservations'].append(dict(payload['reservations'][0]))  # "injection followed": an extra booking
    record['response']['text'] = json.dumps(payload, ensure_ascii=False)
    target.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    local = mail.write_outputs(mail.run('local'), tmp_path / 'local.json')[0]
    replay = mail.write_outputs(mail.run('replay', replay_dir=broken_dir), tmp_path / 'replay.json')[0]
    code = gate.main(['--thresholds', str(THRESHOLDS_PATH), '--mail-local', str(local), '--mail-replay', str(replay)])
    output = capsys.readouterr().out
    assert code == 1
    assert 'mail_replay: 허용 목록에 없는 forbidden — syn-04-ja-injection:injection_followed' in output
    assert 'mail_replay: forbidden_violations 3 > 허용 2' in output  # secondary count check


# --- per-case allowlist and gold completeness ---------------------------------
def _replay_copy(tmp_path):
    broken_dir = tmp_path / 'replay-copy'
    shutil.copytree(mail.DEFAULT_REPLAY_DIR, broken_dir)
    return broken_dir


def _rewrite(directory, label, mutate):
    target = next(path for path in directory.glob('*.json')
                  if json.loads(path.read_text(encoding='utf-8'))['label'] == label)
    record = json.loads(target.read_text(encoding='utf-8'))
    record['response']['text'] = json.dumps(mutate(json.loads(record['response']['text'])), ensure_ascii=False)
    target.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')


def _oracle(case_id):
    from src.evals import replay_build
    from src.evals.gold import load_gold
    case = next(case for case in load_gold() if case['case_id'] == case_id)
    return lambda _payload: replay_build.oracle_payload(case, mail.read_case_text(case))


def _run_gate(tmp_path, replay_dir):
    local = mail.write_outputs(mail.run('local'), tmp_path / 'local.json')[0]
    replay = mail.write_outputs(mail.run('replay', replay_dir=replay_dir), tmp_path / 'replay.json')[0]
    return gate.main(['--thresholds', str(THRESHOLDS_PATH), '--mail-local', str(local), '--mail-replay', str(replay)])


def test_swapped_violation_with_same_total_fails(tmp_path, capsys):
    """syn-01's planted fault disappears while syn-10 gains deadline_as_visit: total stays 2."""
    from src.evals.gold import load_gold
    replay_dir = _replay_copy(tmp_path)
    _rewrite(replay_dir, 'syn-01-ja-ryokan-payment-date:fault:payment_date_as_visit:first', _oracle('syn-01-ja-ryokan-payment-date'))
    syn10 = next(case for case in load_gold() if case['case_id'] == 'syn-10-es-hotel-cancel-deadline')
    decoy_day = syn10['decoys']['deadline_as_visit']['values'][0][:10]

    def plant(payload):
        item = payload['reservations'][0]
        item['date'] = decoy_day
        for event in item['events']:
            if event.get('start_local'):
                event['start_local'] = decoy_day + event['start_local'][10:]
        return payload
    _rewrite(replay_dir, 'syn-10-es-hotel-cancel-deadline:oracle:first', plant)
    assert _run_gate(tmp_path, replay_dir) == 1
    output = capsys.readouterr().out
    assert 'mail_replay: 허용 목록에 없는 forbidden — syn-10-es-hotel-cancel-deadline:deadline_as_visit' in output
    assert '기대한 forbidden 가 관측되지 않음 — syn-01-ja-ryokan-payment-date:payment_date_as_visit' in output
    assert 'forbidden_violations 2 > 허용' not in output  # the total alone would have passed


def test_planted_faults_no_longer_caught_fails(tmp_path, capsys):
    replay_dir = _replay_copy(tmp_path)
    _rewrite(replay_dir, 'syn-01-ja-ryokan-payment-date:fault:payment_date_as_visit:first', _oracle('syn-01-ja-ryokan-payment-date'))
    _rewrite(replay_dir, 'syn-13-es-change-request-pending:fault:change_as_new_confirmed:first',
             _oracle('syn-13-es-change-request-pending'))
    assert _run_gate(tmp_path, replay_dir) == 1
    output = capsys.readouterr().out
    assert output.count('기대한 forbidden 가 관측되지 않음') == 2


def _with_cases(result, rows):
    result = copy.deepcopy(result)
    result['cases'] = rows
    return result


def test_allowlist_unit_rules(tmp_path, capsys):
    thresholds = _thresholds()
    thresholds['mail_local']['max']['forbidden_violations'] = 1
    thresholds['mail_local']['expected_failures'] = {'exact': False, 'forbidden': ['a:cancelled_as_active'],
                                                     'extractor_errors': []}
    thresholds['mail_replay']['max']['forbidden_violations'] = 1
    thresholds['mail_replay']['expected_failures'] = {'exact': True, 'forbidden': ['a:payment_date_as_visit']}
    row = {'case_id': 'a', 'forbidden_violations': [], 'extractor': {'error': None, 'repair_attempts': 0}}
    hit = lambda rule: dict(row, forbidden_violations=[rule])  # noqa: E731
    local_ok = _with_cases(_mail_result('local', forbidden=1), [hit('cancelled_as_active')])
    replay_ok = _with_cases(_mail_result('replay', forbidden=1), [hit('payment_date_as_visit')])
    assert _gate(tmp_path, thresholds, local_ok, replay_ok) == 0
    # Non-exact: an allowlisted violation that disappears is only a note.
    assert _gate(tmp_path, thresholds, _with_cases(_mail_result('local'), [row]), replay_ok) == 0
    assert '허용 목록의 forbidden 가 사라짐 — a:cancelled_as_active' in capsys.readouterr().out
    # Exact: a planted fault that disappears fails.
    assert _gate(tmp_path, thresholds, local_ok, _with_cases(_mail_result('replay'), [row])) == 1
    # A different rule on the same case fails although the total is unchanged.
    assert _gate(tmp_path, thresholds, _with_cases(_mail_result('local', forbidden=1), [hit('deposit_as_total')]), replay_ok) == 1
    # An unlisted extractor error fails although max is not exceeded.
    thresholds['mail_local']['max']['extractor_errors'] = 3
    errored = dict(row, extractor={'error': 'REPLAY_MISS', 'repair_attempts': 0})
    assert _gate(tmp_path, thresholds, _with_cases(_mail_result('local', errors=1), [errored, hit('cancelled_as_active')]),
                 replay_ok) == 1
    # Without per-case rows the allowlist cannot be checked: fail.
    assert _gate(tmp_path, thresholds, _mail_result('local', forbidden=1), replay_ok) == 1


def test_partial_or_shrunken_gold_fails(tmp_path, capsys):
    full = mail.write_outputs(mail.run('local'), tmp_path / 'local.json')[0]
    replay = mail.write_outputs(mail.run('replay'), tmp_path / 'replay.json')[0]
    partial = mail.write_outputs(mail.run('local', max_cases=5), tmp_path / 'partial.json')[0]
    assert gate.main(['--thresholds', str(THRESHOLDS_PATH), '--mail-local', str(partial), '--mail-replay', str(replay)]) == 1
    assert '정답셋 범위 불일치 — cases 5 ≠ 35' in capsys.readouterr().out
    renamed = json.loads(full.read_text(encoding='utf-8'))
    renamed['cases'][0]['case_id'] = 'zz-renamed'
    path = _write(tmp_path, 'renamed.json', renamed)
    assert gate.main(['--thresholds', str(THRESHOLDS_PATH), '--mail-local', path, '--mail-replay', str(replay)]) == 1
    assert 'case_id 목록의 hash' in capsys.readouterr().out


def test_repository_thresholds_pin_planted_faults_exactly():
    replay = THRESHOLDS['mail_replay']['expected_failures']
    assert replay['exact'] is True
    assert len(replay['forbidden']) == THRESHOLDS['mail_replay']['max']['forbidden_violations']
    assert len(replay['extractor_errors']) == THRESHOLDS['mail_replay']['max']['extractor_errors']
    local = THRESHOLDS['mail_local']['expected_failures']
    assert len(local['extractor_errors']) == THRESHOLDS['mail_local']['max']['extractor_errors']
    assert THRESHOLDS['mail_local']['gold'] == THRESHOLDS['mail_replay']['gold']


def test_tightened_threshold_fails_the_gate(tmp_path):
    local = mail.write_outputs(mail.run('local'), tmp_path / 'local.json')[0]
    replay = mail.write_outputs(mail.run('replay'), tmp_path / 'replay.json')[0]
    tight = copy.deepcopy(THRESHOLDS)
    tight['mail_replay']['max']['forbidden_violations'] = 0
    tight['mail_local']['baseline']['micro_f1'] = 0.85
    path = _write(tmp_path, 'tight.json', tight)
    assert gate.main(['--thresholds', path, '--mail-local', str(local), '--mail-replay', str(replay)]) == 1


def test_weakened_gold_under_the_same_ids_fails_the_gate(tmp_path, capsys):
    """EVAL-2: same case ids, weaker answer (syn-13 expected start = decoy, forbidden/decoys/
    must_review emptied) — must_review_recall goes up, so only the content hash catches it."""
    gold_dir = tmp_path / 'gold'
    shutil.copytree(mail.GOLD_DIR, gold_dir)
    path = next(gold_dir.glob('syn-13*.json'))
    case = json.loads(path.read_text(encoding='utf-8'))
    case['forbidden'], case['decoys'], case['must_review'] = [], {}, []
    path.write_text(json.dumps(case, ensure_ascii=False), encoding='utf-8')
    replay = mail.write_outputs(mail.run('replay'), tmp_path / 'replay.json')[0]
    weak = mail.write_outputs(mail.run('local', gold_dir=gold_dir), tmp_path / 'weak.json')[0]
    mail_only = copy.deepcopy(THRESHOLDS)
    for kind in ('agent_replay', 'qa_local', 'qa_replay'):
        mail_only[kind]['required'] = False
    thresholds = _write(tmp_path, 'mail-only.json', mail_only)
    assert gate.main(['--thresholds', thresholds, '--mail-local', str(weak), '--mail-replay', str(replay)]) == 1
    assert 'mail_local: gold_sha256 가 기준과 다릅니다' in capsys.readouterr().out
    from src.evals.gold import gold_content_sha256
    assert THRESHOLDS['mail_local']['gold']['gold_sha256'] == gold_content_sha256() != gold_content_sha256(gold_dir)


def test_local_unmeasurable_rules_are_pinned_both_ways(tmp_path, capsys):
    """EVAL-1: the local allowlist of unmeasurable rules is exact (exact_kinds)."""
    local = mail.run('local')
    for row in local['cases']:
        row['forbidden_unmeasurable'] = []
    failures, _ = gate.check_expected('mail_local', THRESHOLDS['mail_local']['expected_failures'], local)
    assert any('기대한 forbidden_unmeasurable 가 관측되지 않음 — mtp-03-tokyo-dinner:deposit_as_total' in f for f in failures)


def test_qa_results_are_required(tmp_path, capsys):
    """EVAL-4: leaving the QA results out fails the gate (it used to pass with a 'not run' note)."""
    assert THRESHOLDS['qa_local']['required'] is True and THRESHOLDS['qa_replay']['required'] is True
    failures, _ = gate.evaluate(THRESHOLDS, {'mail_local': None, 'mail_replay': None, 'qa_local': None,
                                             'qa_replay': None, 'agent_replay': None})
    assert any(f.startswith('qa_local: thresholds 가 required') for f in failures)
    assert any(f.startswith('qa_replay: thresholds 가 required') for f in failures)
