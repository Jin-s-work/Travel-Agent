#!/usr/bin/env python3
"""Pooled hybrid_v5 vs hybrid_v6 evaluation from a frozen pool and human labels.

Local only, no provider calls. Uses benchmark.evaluate in pooled mode and the
unchanged evaluation_v5 promotion rules. Unjudged candidates are never zero.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _view_summary(result):
    comparison = result['model_comparison']
    return {'ranking_view': result['ranking_view'], 'summary': {v: s['by_split'] for v, s in result['summary'].items()},
            'promotion_candidate': comparison['promotion_candidate'], 'reason_codes': comparison['reason_codes'],
            'production_model': comparison['production_model']}


def run(pool, labels, *, view, splits=('development', 'holdout'), repeats=3):
    """Evaluate in the chosen ranking view and report the other view alongside.

    The promotion fields at the top level belong to `view` only; the alternate
    view is supplementary and never a second chance at promotion.
    """
    from src.recommendations.benchmark import evaluate
    from src.recommendations.labeling import assemble, RANKING_VIEWS
    data, unlabeled = assemble(pool, labels, view=view, splits=splits)
    result = evaluate(data, repeats=repeats, models=('hybrid_v5', 'hybrid_v6'))
    alternates = {}
    for other in sorted(set(RANKING_VIEWS) - {view}):
        other_data, _ = assemble(pool, labels, view=other, splits=splits)
        alternates[other] = _view_summary(evaluate(other_data, repeats=1, models=('hybrid_v5', 'hybrid_v6')))
    result['alternate_views'] = alternates
    result['alternate_views_note'] = 'Supplementary only; promotion is read from ranking_view, chosen before evaluation.'
    result['unlabeled_queries'] = unlabeled
    result['pool_hash'] = pool['pool_hash']
    result['pool_shortfall'] = pool['shortfall']
    result['labeler_count'] = 1
    return result


def main():
    from src.recommendations.benchmark import markdown
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ranking-view', choices=('displayed', 'items_only'), required=True,
                        help='대표 결정 1번. displayed = 제품 페이지 순서, items_only = 검증된 항목만')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    result = run(json.loads(args.pool.read_text()), json.loads(args.labels.read_text()), view=args.ranking_view,
                 repeats=args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n'); os.chmod(args.output, 0o600)
    args.output.with_suffix('.md').write_text(markdown(result))
    comparison = result['model_comparison']
    print(json.dumps({'queries': result['query_count'], 'unlabeled_queries': len(result['unlabeled_queries']),
        'promotion_candidate': comparison['promotion_candidate'], 'reason_codes': comparison['reason_codes'],
        'production_model': comparison['production_model'], 'ranking_view': result['ranking_view'],
        'alternate_views': {k: {'promotion_candidate': v['promotion_candidate'], 'reason_codes': v['reason_codes']}
                            for k, v in result['alternate_views'].items()}, 'provider_calls': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
