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


def pooled_ranking_metrics(ranking, judgments, pooled, k=5):
    """Pooled-judgment metrics. Unjudged candidates are never treated as grade 0.

    judged_at_k = judged share of the returned top-k. Any unjudged candidate in the
    top-k marks the query coverage_insufficient and leaves every metric null.
    Gain 2^grade-1, Precision@k denominator k, recall over judged relevant only.
    """
    if type(k) is not int or not 1 <= k <= 12:
        raise ValueError('k must be in 1..12')
    if len(set(ranking)) != len(ranking) or len(set(pooled)) != len(pooled):
        raise ValueError('Duplicate canonical place ID')
    if set(judgments) - set(pooled):
        raise ValueError('Judgment references a candidate outside the judged pool')
    if any(type(v) is not int or v not in range(4) for v in judgments.values()):
        raise ValueError('Relevance grades must be integers 0..3')
    top = ranking[:k]
    unjudged = [p for p in top if p not in judgments]
    result = {'k': k, 'mode': 'pooled', 'judged': len(judgments), 'pooled': len(pooled), 'returned': len(top),
              'judged_at_k': (len(top) - len(unjudged)) / len(top) if top else None, 'unjudged_top_ids': unjudged,
              'coverage_insufficient': bool(unjudged), 'ndcg': None, 'precision': None, 'recall': None, 'mrr': None,
              'recall_basis': 'pooled_judged_relevant', 'status': 'unmeasured'}
    if not judgments:
        result['reason'] = 'NO_INDEPENDENT_JUDGMENTS'
        return result
    if unjudged:
        result.update(status='coverage_insufficient', reason='UNJUDGED_IN_TOP_K')
        return result
    grades = [judgments[p] for p in top]
    relevant = sum(grade >= 2 for grade in judgments.values())
    hits = sum(grade >= 2 for grade in grades)
    dcg = sum((2 ** grade - 1) / log2(i + 2) for i, grade in enumerate(grades))
    ideal = sum((2 ** grade - 1) / log2(i + 2) for i, grade in enumerate(sorted(judgments.values(), reverse=True)[:k]))
    result.update(ndcg=dcg / ideal if ideal else None, precision=hits / k,
                  recall=hits / relevant if relevant else None,
                  mrr=next((1 / (i + 1) for i, grade in enumerate(grades) if grade >= 2), 0) if relevant else None,
                  status='measured', reason=None if relevant else 'NO_RELEVANT_JUDGMENTS')
    return result


RANKING_VIEWS = {'items_only': ('items',), 'displayed': ('items', 'needs_confirmation')}


POOLED_K = 5


def _validate_pooled(data):
    # Both are explicit in pooled mode: no silent view default, metrics are @5.
    if data.get('ranking_view') not in RANKING_VIEWS:
        raise ValueError(f'Pooled mode requires an explicit ranking_view: {sorted(RANKING_VIEWS)}')
    if data.get('k') != POOLED_K:
        raise ValueError('Pooled mode requires k=5 (judged@5, NDCG@5, Precision@5)')
    for query in data['queries']:
        ids = [p['place_id'] for p in query['candidates']]
        pooled = query.get('pooled_place_ids')
        if not isinstance(pooled, list) or not 1 <= len(pooled) <= 200 or len(set(pooled)) != len(pooled):
            raise ValueError('Pooled mode requires 1..200 unique pooled_place_ids per query')
        if set(pooled) - set(ids) or set(query.get('judgments') or {}) - set(pooled):
            raise ValueError('Pooled judgments must stay inside the frozen candidate pool')
        small = query.get('candidate_ids_60')
        if small is not None and set(small) - set(ids):
            raise ValueError('60-candidate pool must be a subset of the frozen candidates')


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
        if not query.get('candidates') or len(query['candidates']) > 200:
            raise ValueError('1..200 candidates per query required')
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
    mode = data.get('judgment_mode', 'full')
    if mode not in ('full', 'pooled'):
        raise ValueError('judgment_mode must be full or pooled')
    if mode == 'pooled':
        _validate_pooled(data)
    elif 'ranking_view' in data:
        raise ValueError('ranking_view applies to pooled mode only')


def evaluate(data, repeats=3, models=None):
    validate(data)
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('repeats must be 1..20')
    models=models or ('general_v3','hybrid_v4')
    if len(models)!=2 or len(set(models))!=2 or any(m not in ('general_v3','hybrid_v4','hybrid_v5','hybrid_v6') for m in models):raise ValueError('Two distinct supported models required')
    rows = []; times = {m:[] for m in models}
    from .evaluation_v5 import candidate_recall,leakage_audit,compare_report
    leakage=leakage_audit(data['queries'])
    if data['label_source']=='independent_human' and (leakage['shared_query_groups'] or leakage['shared_review_groups']):raise ValueError('Evaluation group leakage')
    pooled = data.get('judgment_mode', 'full') == 'pooled'
    for query in data['queries']:
        now = datetime.fromisoformat(query['snapshot']['evaluation_at'])
        if now.tzinfo is None:
            raise ValueError('Frozen clock must include timezone')
        universe = [p['place_id'] for p in query['candidates']]
        entry = {'query_id': query['id'], 'city': query['snapshot']['conditions']['city'],
                 'section': query['section'], 'split': query['split'],
                 'input_hash': fingerprint({'snapshot': query['snapshot'], 'candidates': query['candidates']}),
                 'candidate_recall_at_200':candidate_recall(universe,query.get('independent_relevant_place_ids')),
                 'models': {}}
        for version in times:
            snapshot = deepcopy(query['snapshot'])
            snapshot.update(model_snapshot(snapshot, version=version))
            recommend(snapshot,query['candidates'],now=now) # unmeasured warm-up, same frozen corpus
            for _ in range(repeats):
                start = perf_counter()
                result = recommend(snapshot, query['candidates'], now=now)
                times[version].append((perf_counter() - start) * 1000)
            groups = result['sections'][query['section']]
            # The product also separates these groups: never present uncertain
            # visits as validated recommendations in the evaluator.
            selected = groups['items']; confirmation = groups['needs_confirmation']
            if pooled:
                # Explicit, recorded view; the full-judgment path below is unchanged.
                selected = _ranked_view(groups, data['ranking_view'])
            ids = [p['place_id'] for p in selected]
            metrics = (pooled_ranking_metrics(ids, query.get('judgments', {}), query['pooled_place_ids'], data.get('k', 3))
                       if pooled else ranking_metrics(ids, query.get('judgments', {}), universe, data.get('k', 3)))
            visible = groups['items'] + confirmation
            top=selected[:data.get('k',3)]
            entry['models'][version] = {'ranking': ids, 'confirmation': [p['place_id'] for p in confirmation],
                'metrics': metrics,'result_k_fulfilled':len(selected)>=data.get('k',3),
                'unjudged_top_ids':[p['place_id'] for p in top if p['place_id'] not in query.get('judgments',{})],
                'hard_violations': sum(any(c['state'] == 'failed' for c in p['visit_fit']['checks']) for p in visible),
                'deterministic': result == recommend(snapshot, list(reversed(query['candidates'])), now=now),
                'diversity': {'categories': len({p['category'] for p in top}),
                              'neighborhoods': len({p['neighborhood'] for p in top if p.get('neighborhood')}),
                              'chains': len({p['chain_id'] for p in top if p.get('chain_id')})},
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
            if pooled:
                coverage = [r['judged_at_k'] for r in group if r['judged_at_k'] is not None]
                by_split[split].update(judged_at_k={'value': mean(coverage) if coverage else None, 'measured_queries': len(coverage)},
                    coverage_insufficient_queries=sum(r['coverage_insufficient'] for r in group),
                    unjudged_queries=sum(r.get('reason') == 'NO_INDEPENDENT_JUDGMENTS' for r in group))
        summary[version] = {'by_split': by_split, 'latency_ms': {'median': median(samples),
            'p95': sorted(samples)[max(0, (95 * len(samples) + 99) // 100 - 1)], 'samples': len(samples)},
            'hard_violations': sum(row['models'][version]['hard_violations'] for row in rows),
            'deterministic_queries': sum(row['models'][version]['deterministic'] for row in rows)}
    comparison=compare_report(rows,summary,label_source=data['label_source']) if set(models)=={'hybrid_v5','hybrid_v6'} else None
    extra = {'judgment_mode': 'pooled', 'ranking_view': data['ranking_view'],
             'pool_size_comparison': pool_size_report(data, rows)} if pooled else {}
    return {**extra, 'leakage_audit':leakage,'model_comparison':comparison,'schema_version': 1, 'label_source': data['label_source'], 'dataset_hash': fingerprint(data),
            'k': data.get('k', 3), 'query_count': len(rows), 'summary': summary, 'queries': rows,
            'actual_user_quality': 'unmeasured', 'automatic_winner': None, 'provider_calls': 0,
            'interpretation': 'Synthetic development regression, not real accuracy or held-out user satisfaction.'
                if data['label_source'] == 'synthetic_rubric' else 'Offline human relevance only; no online satisfaction claim.'}


def _ranked_view(groups, view):
    seen, ordered = set(), []
    for group in RANKING_VIEWS[view]:
        for item in groups[group]:
            if item['place_id'] not in seen:
                seen.add(item['place_id']); ordered.append(item)
    return ordered


def pool_size_report(data, rows, version='hybrid_v5'):
    """Auxiliary 60 vs 200 candidate-pool comparison; never a promotion input."""
    from .evaluation_v5 import bootstrap95
    by_id = {row['query_id']: row for row in rows}
    queries, deltas = [], []
    for query in data['queries']:
        small = query.get('candidate_ids_60')
        if small is None:
            continue
        keep = set(small)
        snapshot = deepcopy(query['snapshot']); snapshot.update(model_snapshot(snapshot, version=version))
        now = datetime.fromisoformat(query['snapshot']['evaluation_at'])
        result = recommend(snapshot, [p for p in query['candidates'] if p['place_id'] in keep], now=now)
        ids = [p['place_id'] for p in _ranked_view(result['sections'][query['section']], data['ranking_view'])]
        metrics = pooled_ranking_metrics(ids, query.get('judgments', {}), query['pooled_place_ids'], data['k'])
        full = by_id[query['id']]['models'].get(version, {}).get('metrics') if query['id'] in by_id else None
        identical = keep == {p['place_id'] for p in query['candidates']}
        queries.append({'query_id': query['id'], 'split': query['split'], 'pool_60_equals_200': identical,
                        'pool_60': metrics, 'pool_200_ndcg': full['ndcg'] if full else None})
        if query['split'] == 'holdout' and full and full['ndcg'] is not None and metrics['ndcg'] is not None:
            deltas.append(metrics['ndcg'] - full['ndcg'])
    return {'model': version, 'baseline': 'pool_200', 'challenger': 'pool_60', 'holdout_ndcg_delta': bootstrap95(deltas),
            'identical_pool_queries': sum(q['pool_60_equals_200'] for q in queries), 'queries': queries,
            'interpretation': 'Auxiliary candidate-pool comparison; not part of promotion rules.'}


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
    if result.get('judgment_mode') == 'pooled':
        lines += [f"pooled 판정 · 순위 보기 `{result['ranking_view']}` · 미판정 후보를 0점으로 채우지 않음.", '',
                  '| 모델 | 분할 | judged@K | 커버리지 부족 질의 | 미판정 질의 |', '|---|---|---:|---:|---:|']
        for version, summary in result['summary'].items():
            for split, row in summary['by_split'].items():
                value = row['judged_at_k']['value']
                lines.append(f"| {version} | {split} | {'미측정' if value is None else f'{value:.3f}'} | {row['coverage_insufficient_queries']} | {row['unjudged_queries']} |")
        lines.append('')
    lines += ['외부 호출 0회. DB·네트워크·큐·화면 지연을 포함하지 않습니다. JSON에 질의별 순위, 성분, 자료 결측, 입력 해시를 기록했습니다.', '']
    return '\n'.join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--models',nargs=2,default=['general_v3','hybrid_v4'])
    args = parser.parse_args()
    source = Path(args.fixture)
    if source.stat().st_size > 20 * 1024 * 1024:
        parser.error('Fixture exceeds 20 MiB')
    result = evaluate(json.loads(source.read_text()), args.repeats,models=args.models)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n'); output.chmod(0o600)
    output.with_suffix('.md').write_text(markdown(result)); output.with_suffix('.md').chmod(0o600)
    print(json.dumps({'queries': result['query_count'], 'provider_calls': 0, 'actual_user_quality': 'unmeasured'}))


if __name__ == '__main__':
    main()
