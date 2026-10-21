#!/usr/bin/env python3
"""Freeze confirmed label queries against an exported catalog. Local only.

No database, network or provider call. Runs hybrid_v5/hybrid_v6 locally only to
decide which candidates must be judged (pooling); the pool file stores the
pooled set, never which model contributed a candidate, a score or a rank.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/service-v5/fixtures/blind-evaluation-plan.json'


def main():
    from src.recommendations.labeling import build_pool, format_duration, K, SEED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True, help='export_v6_label_catalog.py output')
    parser.add_argument('--queries', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--min-candidates', type=int, default=K)
    parser.add_argument('--seed', type=int, default=SEED)
    args = parser.parse_args()
    if args.output.exists():
        parser.error(f'{args.output} exists; a frozen pool is never overwritten (labels are bound to its hash)')
    if args.catalog.stat().st_size > 50 * 1024 * 1024:
        parser.error('Catalog exceeds 50 MiB')
    pool = build_pool(json.loads(args.queries.read_text()), json.loads(args.catalog.read_text()),
                      seed=args.seed, min_candidates=args.min_candidates,
                      plan=json.loads(args.plan.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pool, ensure_ascii=False, indent=1) + '\n')
    span = lambda e: f"{format_duration(e['seconds_low'])}~{format_duration(e['seconds_high'])}"
    by_split = {split: {'queries': e['queries'], 'judgments': e['judgments'], 'estimate': span(e),
                        'upper_bound_judgments': e['judgments_upper_bound']}
                for split, e in pool['estimate_by_split'].items()}
    check = pool['budget_check']
    print(json.dumps({'pool_hash': pool['pool_hash'][:16], 'queries': len(pool['queries']), 'by_split': by_split,
        'holdout_over_prd_budget': check['over_budget'], 'holdout_certainly_over_prd_budget': check['certainly_over'], 'skipped': len(pool['skipped']), 'shortfall': pool['shortfall'],
        'sections': pool['sections'], 'provider_calls': 0}, ensure_ascii=False))
    if check['over_budget']:
        print('경고: holdout 예상 ' + by_split['holdout']['estimate'] + ' (PRD 라벨 예산 2.5~4시간). ' + check['advice'], file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
