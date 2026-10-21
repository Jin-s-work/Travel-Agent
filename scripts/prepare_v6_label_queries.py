#!/usr/bin/env python3
"""Draft (or confirm) the 60 blind query conditions from the v5 work plan.

Drafts contain only mechanical scenario defaults; every item starts with
human_confirmed=false. The pool builder ignores unconfirmed items. Fields that
need a person (human_query for every item, food taste tags, price budget) stay
null and block confirmation, so mechanical defaults alone can never be frozen.
No venue, rank, label or model output is read or written here.

  draft:    prepare_v6_label_queries.py --output evals/recommendation/queries/v6-label-queries.json
  confirm:  prepare_v6_label_queries.py --confirm tokyo-11,tokyo-12 --output <same file>
            (--confirm-split holdout confirms only items whose human fields are filled)
"""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PLAN = Path(__file__).resolve().parents[1] / 'docs/service-v5/fixtures/blind-evaluation-plan.json'
AREAS = ('center', 'north', 'east', 'south', 'west', 'northeast', 'southwest')
FIRST_DATE = date(2026, 11, 2)
TIMES = ('12:00', '19:00')


def draft(plan):
    from src.recommendations.labeling import SCENARIO_SECTIONS, QUERY_KIND
    items = []
    for index, item in enumerate(plan['items']):
        scenario = item['scenario']
        conditions = {'visit': {'date': (FIRST_DATE + timedelta(days=index % 28)).isoformat(), 'local_time': TIMES[index % 2]},
                      'party': {'adults': 2, 'children': [], 'children_status': 'none'},
                      'categories': ['restaurant'], 'preferred': {'tags': [], 'dietary': []}, 'budget': None}
        if scenario == 'food':
            conditions['preferred']['tags'] = None  # a person writes the taste
        if scenario == 'price':
            conditions['budget'] = {'amount_max': None, 'basis': 'per_person', 'period': 'meal'}
        items.append({'id': item['id'], 'city': item['city'], 'split': item['split'], 'scenario': scenario,
            'query_group': item['query_group'], 'travel_group': item['travel_group'],
            'section': SCENARIO_SECTIONS.get(scenario, 'reference'),
            'origin': {'public_area': AREAS[index % len(AREAS)]} if scenario == 'origin' else None,
            'ordering_profile': 'evidence', 'conditions': conditions,
            'human_query': None, 'human_confirmed': False})
    return {'schema_version': 1, 'kind': QUERY_KIND, 'source_plan': 'docs/service-v5/fixtures/blind-evaluation-plan.json',
            'status': 'draft_needs_human_confirmation', 'actual_human_labels': 0,
            'rules': ['조건은 대표가 확인하고 human_confirmed=true 로 바꾼 것만 고정된다.',
                      '모든 항목은 human_query(대표가 모델 순위를 보지 않고 쓴 한 줄 요청)가 있어야 확정된다. 날짜·시각·출발점 초안은 기계적 기본값이므로 human_query 를 쓰며 함께 고친다.',
                      'origin 은 null 또는 {"public_area": center|north|east|south|west|northeast|southwest} 만 허용. 좌표 직접 입력 금지(개인 숙소 좌표 차단). conditions 안의 origin·origin_selection 은 거부된다.',
                      '합성 숙소가 필요하면 {"public_area": "...", "synthetic_hotel": true} 로 공개 지점을 숙소로 간주한다.',
                      'food 는 preferred.tags, price 는 budget.amount_max 를 사람이 채워야 확인할 수 있다.',
                      '같은 조건을 development 와 holdout 에 동시에 두면 동결 단계에서 거부된다.'],
            'items': items}


def confirm(doc, ids=None, split=None):
    from src.recommendations.labeling import missing_human_fields
    confirmed, blocked = [], []
    for item in doc['items']:
        if (ids and item['id'] in ids) or (split and item['split'] == split) or ids == {'all'}:
            missing = missing_human_fields(item)
            if missing:
                blocked.append({'id': item['id'], 'missing': missing}); continue
            item['human_confirmed'] = True; confirmed.append(item['id'])
    if any(i['human_confirmed'] for i in doc['items']):
        doc['status'] = 'partially_confirmed' if not all(i['human_confirmed'] for i in doc['items']) else 'confirmed'
    return confirmed, blocked


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--confirm', help='comma-separated query ids, or "all"')
    parser.add_argument('--confirm-split', choices=('development', 'holdout'))
    parser.add_argument('--force-draft', action='store_true', help='overwrite an existing file with a fresh draft')
    args = parser.parse_args()
    if args.confirm or args.confirm_split:
        doc = json.loads(args.output.read_text())
        ids = set(args.confirm.split(',')) if args.confirm else None
        confirmed, blocked = confirm(doc, ids, args.confirm_split)
        args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'confirmed': len(confirmed), 'blocked': blocked}, ensure_ascii=False))
        return 0
    if args.output.exists() and not args.force_draft:
        parser.error(f'{args.output} exists; edit it, or pass --force-draft to replace it')
    doc = draft(json.loads(args.plan.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'draft_items': len(doc['items']), 'confirmed': 0, 'labels': 0, 'provider_calls': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
