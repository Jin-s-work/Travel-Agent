"""CI gate over evaluation results (synthetic suites only).

    python -m src.evals.gate --thresholds evals/thresholds.json \
        --mail-local a.json --mail-replay b.json --qa-local c.json --qa-replay d.json --agent-replay e.json

Fails (exit 1, with reasons) when, against `evals/thresholds.json`:
* a tracked F1-type metric drops more than `rules.max_drop` (0.02) below its baseline;
* a count metric (forbidden violations, ungrounded atoms, extractor errors,
  cross-trip exposures) exceeds its allowed maximum — 0 unless the threshold
  file records a non-zero baseline and the reason for it;
* a result is missing, not synthetic, or from another suite/mode;
* (`gold`) the result does not cover the whole gold set — case count,
  expected booking count or the hash of the sorted case ids differ — or the
  gold content differs (`gold_sha256` for mail, `questions_sha256` /
  `fixture_sha256` for qa);
* (`expected_failures`) a case breaks a forbidden rule or hits an extractor
  error that is not on the per-case allowlist — even when the total stays
  the same because another one disappeared. With `exact: true` (replay's
  planted faults) an allowlisted pair that is *no longer* observed fails too:
  the evaluator stopped catching a planted fault.
The count limits in `max` stay as a secondary check.

`--agent-replay` (trip agent safety): required while its thresholds entry says
`required: true` (leaving it out fails). `max` holds the zero targets, `min`
the planted attempts that must still run, `scenario_coverage` the scenario
count, id hash and content hashes of the scenario and fixture files, and
`known_gaps` a per-scenario allowlist of documented limits (a new scenario on
it fails, a vanished one is a note).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

SUITES = ('mail_local', 'mail_replay', 'qa_local', 'qa_replay', 'agent_replay')
PAIR_KINDS = ('forbidden', 'forbidden_unmeasurable', 'extractor_errors', 'repaired_cases')
GOLD_CONTENT_HASHES = ('gold_sha256', 'questions_sha256', 'fixture_sha256')


def case_ids_sha256(case_ids):
    return hashlib.sha256('\n'.join(sorted(case_ids)).encode('utf-8')).hexdigest()


def observed_pairs(result):
    """Per-case findings of a mail result, as comparable string keys."""
    observed = {kind: set() for kind in PAIR_KINDS}
    for row in result.get('cases') or []:
        case_id = row.get('case_id')
        for rule in row.get('forbidden_violations') or ():
            observed['forbidden'].add(f'{case_id}:{rule}')
        for rule in row.get('forbidden_unmeasurable') or ():
            observed['forbidden_unmeasurable'].add(f'{case_id}:{rule}')
        extractor = row.get('extractor') or {}
        if extractor.get('error'):
            observed['extractor_errors'].add(f"{case_id}:{extractor['error']}")
        if extractor.get('repair_attempts'):
            observed['repaired_cases'].add(str(case_id))
    return observed


def check_gold(kind, gold, result):
    failures = []
    metrics = result.get('metrics') or {}
    rows = result.get('cases')
    if not isinstance(rows, list):
        return [f'{kind}: 결과에 사례별 cases 가 없어 정답셋 범위를 확인할 수 없습니다.']
    for name in ('cases', 'expected_bookings'):
        if name in gold and metrics.get(name) != gold[name]:
            failures.append(f'{kind}: 정답셋 범위 불일치 — {name} {metrics.get(name)} ≠ {gold[name]} '
                            '(사례가 빠졌거나 --max-cases/--cases 부분 실행 결과)')
    if 'case_ids_sha256' in gold and case_ids_sha256(row.get('case_id', '') for row in rows) != gold['case_ids_sha256']:
        failures.append(f'{kind}: 정답셋 case_id 목록의 hash 가 기준과 다릅니다.')
    # Content, not just ids: a weakened expectation (expected value, forbidden,
    # decoys, must_review, a question's answerable/must_not_include) under the same id fails.
    for name in GOLD_CONTENT_HASHES:
        if name in gold and result.get(name) != gold[name]:
            failures.append(f'{kind}: {name} 가 기준과 다릅니다 (정답셋 내용이 바뀜 — 의도했다면 thresholds 갱신).')
    return failures


def check_expected(kind, spec, result):
    """Per-case allowlist; returns (failures, notes)."""
    failures, notes = [], []
    if not isinstance(result.get('cases'), list):
        return [f'{kind}: 결과에 사례별 cases 가 없어 사례별 위반을 확인할 수 없습니다.'], notes
    observed = observed_pairs(result)
    for name in PAIR_KINDS:
        if name not in spec:
            continue
        # `exact_kinds` pins one kind both ways even when the suite is not exact.
        exact = bool(spec.get('exact')) or name in (spec.get('exact_kinds') or ())
        allowed = {item if isinstance(item, str) else ':'.join(item) for item in spec[name]}
        unexpected = sorted(observed[name] - allowed)
        missing = sorted(allowed - observed[name])
        if name == 'forbidden_unmeasurable':
            notes.append(f'{kind}: forbidden 측정 불가 {len(observed[name])}건 (추출기가 내지 않는 필드) {sorted(observed[name])}')
        for item in unexpected:
            failures.append(f'{kind}: 허용 목록에 없는 {name} — {item}')
        for item in missing:
            if exact:
                failures.append(f'{kind}: 기대한 {name} 가 관측되지 않음 — {item} (평가기가 심어 둔 결함을 더 이상 잡지 못함)')
            else:
                notes.append(f'{kind}: 허용 목록의 {name} 가 사라짐 — {item} (개선이면 thresholds 에서 지우세요)')
    return failures, notes


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def tracked_values(kind, result):
    """Flatten the metrics a threshold entry can name."""
    metrics = result.get('metrics') or {}
    if kind.startswith('mail'):
        values = {
            'micro_f1': (metrics.get('micro') or {}).get('f1'),
            'booking_count_accuracy': metrics.get('booking_count_accuracy'),
            'must_review_recall': (metrics.get('must_review') or {}).get('recall'),
            'forbidden_violations': metrics.get('forbidden_violations'),
            'ungrounded_atoms': metrics.get('ungrounded_atoms'),
            'extractor_errors': metrics.get('extractor_errors'),
        }
        for field, scores in (metrics.get('fields') or {}).items():
            values[f'field_f1.{field}'] = (scores or {}).get('f1')
        return values
    return dict(metrics)


def check_suite(kind, entry, result, rules):
    """Return (failures, notes) for one suite."""
    failures, notes = [], []
    if result.get('synthetic') is not True:
        failures.append(f'{kind}: 결과에 synthetic: true 가 없습니다.')
    for key in ('suite', 'mode'):
        expected = entry.get(key)
        if expected is not None and result.get(key) != expected:
            failures.append(f'{kind}: {key} 가 {expected!r} 가 아니라 {result.get(key)!r} 입니다.')
    values = tracked_values(kind, result)
    max_drop = float(rules.get('max_drop', 0.02))
    baseline = entry.get('baseline')
    if baseline is None:
        notes.append(f'{kind}: 기준선 미설정 — 절대 상한만 검사했습니다.')
    else:
        for name, base in baseline.items():
            if base is None:
                continue
            current = values.get(name)
            if current is None:
                failures.append(f'{kind}: {name} 값이 결과에 없습니다 (기준선 {base}).')
            elif current < base - max_drop - 1e-9:
                failures.append(f'{kind}: {name} {current} < 기준선 {base} - {max_drop}')
    for name, allowed in (entry.get('max') or {}).items():
        current = values.get(name)
        if current is None:
            failures.append(f'{kind}: {name} 값이 결과에 없습니다 (상한 {allowed}).')
        elif current > allowed:
            failures.append(f'{kind}: {name} {current} > 허용 {allowed}')
    if entry.get('gold'):
        failures += check_gold(kind, entry['gold'], result)
    if entry.get('expected_failures'):
        expected_failures, expected_notes = check_expected(kind, entry['expected_failures'], result)
        failures += expected_failures
        notes += expected_notes
    for name, least in (entry.get('min') or {}).items():
        current = values.get(name)
        if current is None or current < least:
            failures.append(f'{kind}: {name} {current} < 최소 {least} (심어 둔 시도가 더 이상 실행되지 않음)')
    if entry.get('scenario_coverage'):
        failures += check_scenario_coverage(kind, entry['scenario_coverage'], result)
    if entry.get('known_gaps'):
        gap_failures, gap_notes = check_known_gaps(kind, entry['known_gaps'], result)
        failures += gap_failures
        notes += gap_notes
    return failures, notes


def check_scenario_coverage(kind, coverage, result):
    """(agent) every scenario ran: count and hash of the sorted scenario ids."""
    rows = result.get('cases')
    if not isinstance(rows, list):
        return [f'{kind}: 결과에 시나리오별 cases 가 없습니다.']
    failures = []
    if 'scenarios' in coverage and len(rows) != coverage['scenarios']:
        failures.append(f"{kind}: 시나리오 수 {len(rows)} ≠ {coverage['scenarios']} (부분 실행 또는 시나리오 변경)")
    ids = [str(row.get('scenario_id', '')) for row in rows]
    if 'scenario_ids_sha256' in coverage and case_ids_sha256(ids) != coverage['scenario_ids_sha256']:
        failures.append(f'{kind}: 시나리오 id 목록의 hash 가 기준과 다릅니다.')
    # Content, not just ids: a weakened script or expectation under the same id fails.
    for name in ('scenarios_sha256', 'fixture_sha256'):
        if name in coverage and result.get(name) != coverage[name]:
            failures.append(f'{kind}: {name} 가 기준과 다릅니다 (시나리오·세계 내용이 바뀜 — 의도했다면 thresholds 갱신).')
    return failures


def check_known_gaps(kind, gaps, result):
    """(agent) per-scenario allowlist of known limits, e.g. injection_text_relay:
    a scenario not on the list fails; one that disappeared is a note."""
    failures, notes = [], []
    rows = result.get('cases') if isinstance(result.get('cases'), list) else []
    for field, allowed in gaps.items():
        observed = {str(row.get('scenario_id')) for row in rows if row.get(field)}
        for item in sorted(observed - set(allowed)):
            failures.append(f'{kind}: 허용 목록에 없는 {field} — {item}')
        for item in sorted(set(allowed) - observed):
            notes.append(f'{kind}: 허용 목록의 {field} 가 사라짐 — {item} (방어가 생겼으면 thresholds 에서 지우세요)')
    return failures, notes


def evaluate(thresholds, results):
    rules = thresholds.get('rules') or {}
    failures, notes = [], []
    for kind in SUITES:
        entry = thresholds.get(kind)
        path = results.get(kind)
        if path is None:
            if kind.startswith('mail'):
                failures.append(f'{kind}: 결과 파일이 지정되지 않았습니다.')
            elif (entry or {}).get('required'):
                failures.append(f'{kind}: thresholds 가 required 인데 결과 파일이 지정되지 않았습니다 (CI 에서 평가가 빠짐).')
            else:
                notes.append(f'{kind}: 결과 미지정 — 미실행.')
            continue
        if entry is None:
            failures.append(f'{kind}: thresholds 에 항목이 없습니다.')
            continue
        try:
            result = _load(path)
        except (OSError, ValueError) as exc:
            failures.append(f'{kind}: 결과를 읽지 못했습니다 ({type(exc).__name__}).')
            continue
        suite_failures, suite_notes = check_suite(kind, entry, result, rules)
        failures += suite_failures
        notes += suite_notes
    return failures, notes


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m src.evals.gate', description='평가 결과 CI 게이트 (synthetic)')
    parser.add_argument('--thresholds', required=True)
    parser.add_argument('--mail-local', required=True)
    parser.add_argument('--mail-replay', required=True)
    parser.add_argument('--qa-local')
    parser.add_argument('--qa-replay')
    parser.add_argument('--agent-replay')
    args = parser.parse_args(argv)
    try:
        thresholds = _load(args.thresholds)
    except (OSError, ValueError) as exc:
        print(f'gate: FAIL thresholds 를 읽지 못했습니다 ({type(exc).__name__}).')
        return 1
    results = {'mail_local': args.mail_local, 'mail_replay': args.mail_replay,
               'qa_local': args.qa_local, 'qa_replay': args.qa_replay, 'agent_replay': args.agent_replay}
    failures, notes = evaluate(thresholds, results)
    for note in notes:
        print(f'gate: note {note}')
    for failure in failures:
        print(f'gate: FAIL {failure}')
    print(json.dumps({'gate': 'fail' if failures else 'pass', 'failures': len(failures), 'synthetic': True},
                     ensure_ascii=False))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
