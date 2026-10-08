"""Frozen labeled ranking evaluation, no network or model-generated judgments.

Synthetic relevance is a regression rubric only. Independent human judgments
are required for an actual relevance claim; incomplete labels yield null metrics.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from math import log2
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from .engine import recommend
from .registry import model_snapshot


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ranking_metrics(ranking, judgments, universe, k=3):
    if type(k) is not int or not 1 <= k <= 12:
        raise ValueError('k must be in 1..12')
    if len(set(ranking)) != len(ranking) or len(set(universe)) != len(universe):
        raise ValueError('Duplicate canonical place ID')
    if set(judgments) - set(universe) or set(ranking) - set(universe):
        raise ValueError('Judgment or ranking references another candidate universe')
    if any(type(v) is not int or v not in range(4) for v in judgments.values()):
        raise ValueError('Relevance grades must be integers 0..3')
    result = {'k': k, 'judged': len(judgments), 'candidates': len(universe),
              'returned': len(ranking[:k]), 'ndcg': None, 'precision': None, 'recall': None,
              'mrr': None, 'status': 'unmeasured'}
    if not universe or set(judgments) != set(universe):
        result['reason'] = 'INCOMPLETE_INDEPENDENT_JUDGMENTS'
        return result
    grades = [judgments[p] for p in ranking[:k]]
    relevant = sum(grade >= 2 for grade in judgments.values())
    hits = sum(grade >= 2 for grade in grades)
    dcg = sum((2 ** grade - 1) / log2(i + 2) for i, grade in enumerate(grades))
    ideal = sum((2 ** grade - 1) / log2(i + 2) for i, grade in enumerate(sorted(judgments.values(), reverse=True)[:k]))
    result.update(ndcg=dcg / ideal if ideal else None, precision=hits / k,
                  recall=hits / relevant if relevant else None,
                  mrr=next((1 / (i + 1) for i, grade in enumerate(grades) if grade >= 2), 0) if relevant else None,
                  status='measured', reason=None if relevant else 'NO_RELEVANT_JUDGMENTS')
    return result


def validate(data):
    if data.get('schema_version') != 1 or data.get('label_source') not in ('synthetic_rubric', 'independent_human'):
        raise ValueError('Explicit schema and judgment provenance required')
    queries = data.get('queries') or []
    if not queries or len(queries) > 200:
        raise ValueError('1..200 bounded frozen queries required')
    if len({q['id'] for q in queries}) != len(queries):
        raise ValueError('Duplicate query ID')
    for query in queries:
        if query['section'] not in ('local_discovery', 'landmark', 'reference'):
            raise ValueError('Unknown recommendation section')
        if not query.get('candidates') or len(query['candidates']) > 100:
            raise ValueError('1..100 candidates per query required')
        if query['split'] not in ('development', 'holdout'):
            raise ValueError('Explicit development/holdout split required')
        if data['label_source'] == 'synthetic_rubric' and any(p.get('synthetic') is not True for p in query['candidates']):
            raise ValueError('Synthetic rubrics cannot masquerade as real venue judgments')
        if data['label_source'] == 'independent_human' and any(p.get('synthetic') for p in query['candidates']):
            raise ValueError('Synthetic candidates cannot establish actual relevance')
        if data['label_source'] == 'independent_human' and query.get('judgments') and not query.get('reviewer_id'):
            raise ValueError('Pseudonymous independent reviewer required')
    # Prevent exact query leakage; same city/place can occur in independently
    # assessed contexts, but the same frozen request cannot be in both splits.
    splits = {}
    for query in queries:
        key = fingerprint({'snapshot': query['snapshot'], 'candidates': query['candidates']})
        if key in splits and splits[key] != query['split']:
            raise ValueError('Frozen query leaks across development/holdout')
        splits[key] = query['split']


def evaluate(data, repeats=3):
    validate(data)
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('repeats must be 1..20')
    rows = []; times = {'general_v3': [], 'hybrid_v4': []}
    for query in data['queries']:
        now = datetime.fromisoformat(query['snapshot']['evaluation_at'])
        if now.tzinfo is None:
            raise ValueError('Frozen clock must include timezone')
        universe = [p['place_id'] for p in query['candidates']]
        entry = {'query_id': query['id'], 'city': query['snapshot']['conditions']['city'],
                 'section': query['section'], 'split': query['split'],
                 'input_hash': fingerprint({'snapshot': query['snapshot'], 'candidates': query['candidates']}),
                 'models': {}}
        for version in times:
            snapshot = deepcopy(query['snapshot'])
            snapshot.update(model_snapshot(snapshot, version=version))
            for _ in range(repeats):
                start = perf_counter()
                result = recommend(snapshot, query['candidates'], now=now)
                times[version].append((perf_counter() - start) * 1000)
            groups = result['sections'][query['section']]
            # The product also separates these groups: never present uncertain
            # visits as validated recommendations in the evaluator.
            selected = groups['items']; confirmation = groups['needs_confirmation']
            ids = [p['place_id'] for p in selected]
            metrics = ranking_metrics(ids, query.get('judgments', {}), universe, data.get('k', 3))
            visible = selected + confirmation
            entry['models'][version] = {'ranking': ids, 'confirmation': [p['place_id'] for p in confirmation],
                'metrics': metrics,
                'hard_violations': sum(any(c['state'] == 'failed' for c in p['visit_fit']['checks']) for p in visible),
                'deterministic': result == recommend(snapshot, list(reversed(query['candidates'])), now=now),
                'diversity': {'categories': len({p['category'] for p in selected}),
                              'neighborhoods': len({p['neighborhood'] for p in selected if p.get('neighborhood')}),
                              'chains': len({p['chain_id'] for p in selected if p.get('chain_id')})},
                'diagnostics': {p['place_id']: p.get('ranking_diagnostics') for p in visible}}
        rows.append(entry)
    summary = {}
    for version, samples in times.items():
        by_split = {}
        for split in ('development', 'holdout'):
            group = [row['models'][version]['metrics'] for row in rows if row['split'] == split]
            by_split[split] = {'queries': len(group), **{
                key: {'value': mean(values) if values else None, 'measured_queries': len(values)}
                for key in ('ndcg', 'precision', 'recall', 'mrr')
                for values in [[r[key] for r in group if r[key] is not None]]}}
        summary[version] = {'by_split': by_split, 'latency_ms': {'median': median(samples),
            'p95': sorted(samples)[max(0, (95 * len(samples) + 99) // 100 - 1)], 'samples': len(samples)},
            'hard_violations': sum(row['models'][version]['hard_violations'] for row in rows),
            'deterministic_queries': sum(row['models'][version]['deterministic'] for row in rows)}
    return {'schema_version': 1, 'label_source': data['label_source'], 'dataset_hash': fingerprint(data),
            'k': data.get('k', 3), 'query_count': len(rows), 'summary': summary, 'queries': rows,
            'actual_user_quality': 'unmeasured', 'automatic_winner': None, 'provider_calls': 0,
            'interpretation': 'Synthetic development regression, not real accuracy or held-out user satisfaction.'
                if data['label_source'] == 'synthetic_rubric' else 'Offline human relevance only; no online satisfaction claim.'}


def markdown(result):
    lines = ['# 추천 모델 고정 입력 평가', '',
        f"자료 출처: `{result['label_source']}` · 질의 {result['query_count']}개 · K={result['k']}",
        '실제 사용자 만족도: **미측정**. 합성 점수는 정해 둔 요구사항의 회귀 시험이며 실제 추천 정확도가 아닙니다.', '',
        '| 모델 | 분할 | 질의 | NDCG@K | Precision@K | Recall@K | MRR@K |',
        '|---|---|---:|---:|---:|---:|---:|']
    for version, summary in result['summary'].items():
        for split, row in summary['by_split'].items():
            fmt = lambda key: '미측정' if row[key]['value'] is None else f"{row[key]['value']:.4f} ({row[key]['measured_queries']}질의)"
            lines.append(f"| {version} | {split} | {row['queries']} | {fmt('ndcg')} | {fmt('precision')} | {fmt('recall')} | {fmt('mrr')} |")
    for version, summary in result['summary'].items():
        lines += ['', f"{version}: 필수 위반 {summary['hard_violations']}건 · 결정성 {summary['deterministic_queries']}/{result['query_count']} · 로컬 순수 엔진 중앙 지연 {summary['latency_ms']['median']:.2f}ms, p95 {summary['latency_ms']['p95']:.2f}ms.", '']
    lines += ['외부 호출 0회. DB·네트워크·큐·화면 지연을 포함하지 않습니다. JSON에 질의별 순위, 성분, 자료 결측, 입력 해시를 기록했습니다.', '']
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    source = Path(args.fixture)
    if source.stat().st_size > 20 * 1024 * 1024:
        parser.error('Fixture exceeds 20 MiB')
    result = evaluate(json.loads(source.read_text()), args.repeats)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n'); output.chmod(0o600)
    output.with_suffix('.md').write_text(markdown(result)); output.with_suffix('.md').chmod(0o600)
    print(json.dumps({'queries': result['query_count'], 'provider_calls': 0, 'actual_user_quality': 'unmeasured'}))


if __name__ == '__main__':
    main()
