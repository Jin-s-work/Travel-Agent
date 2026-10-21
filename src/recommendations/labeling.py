"""Blind pooled human-labeling support for hybrid_v5/hybrid_v6 comparison.

This module never produces relevance grades. It freezes query snapshots and a
public candidate catalog, decides which candidates a human must judge (pooling),
renders a blind view without model scores/ranks/names, and stores the grades a
person typed together with the snapshot and candidate fact versions.

Pooling per query (fixed, documented in LABELING.md):
  hybrid_v5 top-10 on the 200 pool  U  hybrid_v6 top-10 on the 200 pool
  U  hybrid_v5 top-10 on the 60 pool  U  5 seeded random candidates of the 200 pool.

Top-10 lists are taken in the product display order (items, then
needs_confirmation). The validated-items-only top-10 is always a prefix subset of
that list, so one frozen pool can be evaluated in either ranking view; the view
is chosen at evaluation time and is not part of the pool hash.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import random
import re

POOL_KIND = 'recommendation_label_pool'
LABEL_KIND = 'recommendation_labels'
QUERY_KIND = 'recommendation_label_queries'
SCHEMA_VERSION = 1
SEED = 20261021
TOP_N = 10
RANDOM_N = 5
SMALL_POOL = 60
FULL_POOL = 200
K = 5
MODELS = ('hybrid_v5', 'hybrid_v6')
SECTIONS = ('local_discovery', 'landmark', 'reference')
SPLITS = ('development', 'holdout')
SCENARIO_SECTIONS = {'famous': 'landmark', 'local_language': 'local_discovery'}
# The product shows `items` first and then `needs_confirmation` (pages.ordered).
RANKING_VIEWS = {'items_only': ('items',), 'displayed': ('items', 'needs_confirmation')}
# Pooling always uses the displayed order: items_only top-10 is a subset of it.
POOLING_VIEW = 'displayed'
SECONDS_PER_JUDGMENT = (15, 25)
# PRD 10절 결정 5 (2026-10-20): 대표 직접 라벨 약 2.5~4시간.
PRD_LABEL_BUDGET_SECONDS = (int(2.5 * 3600), 4 * 3600)
MAX_POOLED_PER_QUERY = 3 * TOP_N + RANDOM_N
# Location keys a query spec may not carry in `conditions`; the origin is set
# only through `origin` (a public sampling point), never a free coordinate or a
# private accommodation reference.
LOCATION_CONDITION_KEYS = ('origin', 'origin_selection')
LABEL_SCALE = {'0': '부적합', '1': '약간 관련', '2': '적합', '3': '매우 적합'}
REVIEWER_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
# Keys that would reveal model output. A blind card is built from a whitelist,
# and tests assert none of these appear anywhere in the rendered view.
FORBIDDEN_VIEW_KEYS = frozenset({'score', 'score_lower', 'score_upper', 'score_complete', 'score_components',
    'ranking_diagnostics', 'rank', 'position_in_model', 'utility', 'utility_lower', 'components', 'matched_tags',
    'recommendation_model_version', 'ranker_version', 'section_models', 'model', 'models', 'eligibility',
    'section_qualified', 'selection_status', 'supported_reasons', 'reason_codes', 'recommendation_types'})
FACT_LABELS = {
    'opening_hours': '영업시간', 'price': '가격', 'closed': '폐업/휴업', 'dietary': '식단', 'accessibility': '접근성',
    'reservation_methods': '예약 방법', 'max_party': '최대 인원', 'min_party': '최소 인원', 'children_rule': '아동',
    'rating': '플랫폼 평점', 'iconic_evidence': '유명함 근거', 'local_evidence': '현지 근거', 'tags': '분류 태그',
    'cuisine': '요리', 'public_map_tags': '지도 태그',
}
PUBLIC_MAP_KEYS = ('cuisine', 'opening_hours', 'wheelchair', 'diet:vegetarian', 'diet:vegan', 'website')
STATUS_LABELS = {'verified': '확인됨', 'provisional': '지도 정보 기준(잠정)', 'conflict': '자료 충돌'}
# Candidate keys that may enter a frozen pool. Anything trip-, owner- or
# photo-specific is dropped so no private or per-request state is stored.
CANDIDATE_KEYS = ('place_id', 'name', 'native_name', 'display_name', 'address', 'city', 'category', 'categories',
    'recommendation_types', 'tags', 'chain_id', 'neighborhood', 'latitude', 'longitude', 'identity_status',
    'pack_status', 'synthetic', 'canonical_url', 'facts', 'sources', 'provider', 'source_kind', 'attribution',
    'review_evidence')
PRIVATE_MARKERS = ('owner_id', 'trip_id', 'accommodation', 'email', 'booking')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def fact_version(candidate):
    """Version of everything a labeler sees or a model reads for one candidate."""
    keys = ('place_id', 'name', 'native_name', 'address', 'category', 'tags', 'neighborhood', 'latitude', 'longitude',
            'identity_status', 'pack_status', 'chain_id')
    facts = sorted(({k: f.get(k) for k in ('id', 'field', 'status', 'value', 'source_id', 'checked_at', 'expires_at',
                                          'valid_for_date', 'policy_version', 'usable')} for f in candidate.get('facts', [])),
                   key=lambda f: (f['id'] or '', f['field'] or ''))
    sources = sorted(({k: s.get(k) for k in ('id', 'url', 'source_type', 'status', 'policy_version')}
                      for s in candidate.get('sources', [])), key=lambda s: s['id'] or '')
    review = (candidate.get('review_evidence') or {})
    return fingerprint({**{k: candidate.get(k) for k in keys}, 'facts': facts, 'sources': sources,
                        'review': {k: review.get(k) for k in ('state', 'aggregate_id', 'counts', 'checked_at')}})[:32]


def public_candidate(candidate):
    """Copy only catalog fields. Rejects rows that carry private markers."""
    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield str(key); yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)
    for key in keys(candidate):
        if any(key.startswith(marker) for marker in PRIVATE_MARKERS):
            raise ValueError(f'Private field {key!r} cannot enter a label pool')
    row = {k: deepcopy(candidate[k]) for k in CANDIDATE_KEYS if k in candidate}
    row.setdefault('review_evidence', {'state': 'unavailable', 'counts': None, 'metrics': None,
        'evaluation': {'strict_pass': False, 'reason_codes': ['NO_REVIEW_OBSERVATION']}})
    return row


# ---------------------------------------------------------------- queries

def origin_choices(city):
    from src.discovery.candidate_pool import public_areas
    return {area['public_area_id']: area for area in public_areas(city)}


def resolve_origin(city, spec):
    """Only public sampling points are allowed; free coordinates are refused."""
    if spec in (None, 'none'):
        return None, 'city_center_anchor'
    if not isinstance(spec, dict) or 'public_area' not in spec or set(spec) - {'public_area', 'synthetic_hotel'}:
        raise ValueError('origin must be null or {"public_area": <id>[, "synthetic_hotel": true]}; raw coordinates are not accepted')
    areas = origin_choices(city)
    area = areas.get(spec['public_area'])
    if not area:
        raise ValueError(f'Unknown public area {spec["public_area"]!r}; choose one of {sorted(areas)}')
    label = '공개 지점 ' + area['public_area_id'] + (' (합성 숙소로 사용)' if spec.get('synthetic_hotel') else '')
    return {'label': label, 'latitude': round(area['latitude'], 6), 'longitude': round(area['longitude'], 6)}, 'public_area:' + area['public_area_id']


def missing_human_fields(spec):
    """Fields a person must fill before the query can be frozen."""
    missing = []
    conditions = spec.get('conditions') or {}
    visit = conditions.get('visit') or {}
    if not visit.get('date'):
        missing.append('conditions.visit.date')
    if spec.get('scenario') == 'food' and not (conditions.get('preferred') or {}).get('tags'):
        missing.append('conditions.preferred.tags')
    if spec.get('scenario') == 'price' and not (conditions.get('budget') or {}).get('amount_max'):
        missing.append('conditions.budget.amount_max')
    if spec.get('section') not in SECTIONS:
        missing.append('section')
    if spec.get('split') not in SPLITS:
        missing.append('split')
    if not str(spec.get('human_query') or '').strip():
        missing.append('human_query')  # a person writes each request; mechanical defaults alone never freeze
    return missing


def build_snapshot(spec, evaluation_at):
    """Frozen request equivalent to Recommendations.submit, without trip/owner data."""
    from src.destinations import CITIES
    from src.discovery.models import Conditions
    from src.recommendations.models import RecommendationInput
    from src.recommendations.service import VERSIONS
    city = spec['city']
    raw = deepcopy(spec.get('conditions') or {})
    blocked = [key for key in LOCATION_CONDITION_KEYS if key in raw]
    if blocked:
        raise ValueError(f'conditions.{blocked[0]} is not accepted; set the origin with "origin": {{"public_area": <id>}}')
    origin, basis = resolve_origin(city, spec.get('origin'))
    raw['visit'] = {**raw.get('visit', {}), 'timezone': CITIES[city]['timezone']}
    # Same as the product for an explicit origin (manual) and no accommodation reference ever.
    raw.update(city=city, origin=origin, origin_selection={'kind': 'manual' if origin else 'none'})
    raw.setdefault('party', {'adults': 2, 'children': [], 'children_status': 'none'})
    raw.setdefault('recommendation_types', ['local_discovery', 'landmark'])
    if raw.get('budget'):
        raw['budget'] = {'currency': CITIES[city]['currency'], **raw['budget']}
    conditions = Conditions.model_validate(raw).model_dump(mode='json')
    body = RecommendationInput.model_validate({'trip_version': 1, 'conditions_version': 1,
        'ordering_profile': spec.get('ordering_profile', 'evidence')}).model_dump(mode='json')
    snapshot = {**body, 'conditions': conditions, 'result_contract': 'discovery_result_v1',
        'candidate_pool_limit': FULL_POOL, 'pipeline_version': 'discovery_pipeline_v2', 'movement_version': 'v2',
        'origin_context': None, 'ranker_versions': VERSIONS, 'explanation_version': 'server_templates_v1',
        'evaluation_at': evaluation_at, 'local_feature_enabled': False, 'soft_avoid_place_ids': [],
        'feedback_policy_version': 'soft_avoid_half_v1', 'shadow_model_version': None}
    return snapshot, basis


# ---------------------------------------------------------------- pooling

def model_result(snapshot, candidates, version):
    from .engine import recommend
    from .registry import model_snapshot
    value = deepcopy(snapshot)
    value.update(model_snapshot(value, version=version))
    return recommend(value, candidates, now=datetime.fromisoformat(snapshot['evaluation_at']))


def section_ranking(result, section, view='items_only'):
    seen, ids = set(), []
    for group in RANKING_VIEWS[view]:
        for item in result['sections'][section][group]:
            if item['place_id'] not in seen:
                seen.add(item['place_id']); ids.append(item['place_id'])
    return ids


def select_pool(snapshot, candidates, limit):
    """Product preselection (scope_distance_evidence_v2) at the given size."""
    from .service import Recommendations
    return sorted(p['place_id'] for p in Recommendations._select(snapshot, candidates, limit=limit))


def _rng(seed, *parts):
    return random.Random(fingerprint([seed, *parts]))


def pool_query(spec, catalog, evaluation_at, *, seed=SEED, min_candidates=K):
    """Return (query_record, None) or (None, skip_reason). Never returns grades."""
    snapshot, basis = build_snapshot(spec, evaluation_at)
    categories = set(snapshot['conditions']['categories'])
    city_rows = [c for c in catalog.values() if c.get('city') == spec['city'] and c.get('category') in categories]
    ids200 = select_pool(snapshot, city_rows, FULL_POOL)
    if len(ids200) < min_candidates:
        return None, {'reason': 'CATALOG_TOO_SMALL', 'candidates': len(ids200), 'required': min_candidates}
    ids60 = select_pool(snapshot, city_rows, SMALL_POOL)
    by_id = {c['place_id']: c for c in city_rows}
    section = spec['section']
    tops = {}
    for name, version, ids in (('v5_200', 'hybrid_v5', ids200), ('v6_200', 'hybrid_v6', ids200), ('v5_60', 'hybrid_v5', ids60)):
        tops[name] = section_ranking(model_result(snapshot, [by_id[i] for i in ids], version), section, POOLING_VIEW)[:TOP_N]
    union = sorted(set().union(*tops.values()))
    if not union:
        return None, {'reason': 'NO_MODEL_RESULTS_IN_SECTION', 'candidates': len(ids200), 'section': section}
    rest = [i for i in ids200 if i not in union]
    random_ids = sorted(_rng(seed, spec['id'], 'random').sample(rest, min(RANDOM_N, len(rest))))
    pooled = sorted(set(union) | set(random_ids))
    # Display order depends only on the seed and the sorted id set, never on model order.
    blind_order = list(pooled)
    _rng(seed, spec['id'], 'display').shuffle(blind_order)
    record = {'id': spec['id'], 'city': spec['city'], 'split': spec['split'], 'scenario': spec.get('scenario'),
        'section': section, 'query_group': spec.get('query_group'), 'travel_group': spec.get('travel_group'),
        'human_query': spec.get('human_query'), 'origin_basis': basis, 'snapshot': snapshot,
        'snapshot_hash': fingerprint(snapshot), 'candidate_ids': ids200, 'candidate_ids_60': ids60,
        'pooled_place_ids': pooled, 'blind_order': blind_order,
        'fact_versions': {i: fact_version(by_id[i]) for i in pooled},
        'pool_counts': {'candidates_200': len(ids200), 'candidates_60': len(ids60), 'pooled': len(pooled),
                        'random_added': len(random_ids), 'pool_60_equals_200': ids60 == ids200}}
    return record, None


def plan_targets(plan):
    targets = {}
    for item in plan.get('items', []):
        key = (item['city'], item['split'])
        targets[key] = targets.get(key, 0) + 1
    return targets


def build_pool(query_doc, catalog_doc, *, seed=SEED, min_candidates=K, plan=None, created_at=None):
    """Freeze all confirmed queries against an exported public catalog."""
    if catalog_doc.get('kind') != 'recommendation_label_catalog':
        raise ValueError('Exported label catalog required')
    synthetic = bool(catalog_doc.get('synthetic'))
    catalog = {}
    for city, rows in catalog_doc['cities'].items():
        for row in rows:
            if bool(row.get('synthetic')) != synthetic:
                raise ValueError('Synthetic and real candidates cannot share a label pool')
            if row.get('city') != city:
                raise ValueError('Candidate city mismatch')
            catalog[row['place_id']] = public_candidate(row)
    evaluation_at = catalog_doc['exported_at']
    queries, skipped, used = [], [], set()
    frozen = {}
    for spec in query_doc.get('items', []):
        if not spec.get('human_confirmed'):
            skipped.append({'id': spec['id'], 'city': spec['city'], 'split': spec.get('split'), 'reason': 'NOT_CONFIRMED_BY_HUMAN'}); continue
        missing = missing_human_fields(spec)
        if missing:
            skipped.append({'id': spec['id'], 'city': spec['city'], 'split': spec.get('split'), 'reason': 'MISSING_HUMAN_FIELDS', 'fields': missing}); continue
        record, reason = pool_query(spec, catalog, evaluation_at, seed=seed, min_candidates=min_candidates)
        if not record:
            skipped.append({'id': spec['id'], 'city': spec['city'], 'split': spec['split'], **reason}); continue
        key = fingerprint({'snapshot': record['snapshot'], 'candidates': record['candidate_ids']})
        if key in frozen and frozen[key] != record['split']:
            raise ValueError(f'Query {spec["id"]} repeats a frozen request from the other split; change its conditions')
        frozen[key] = record['split']
        used.update(record['candidate_ids'])
        queries.append(record)
    targets = plan_targets(plan) if plan else {}
    made = {}
    for q in queries:
        made[(q['city'], q['split'])] = made.get((q['city'], q['split']), 0) + 1
    shortfall = [{'city': c, 'split': s, 'target': t, 'created': made.get((c, s), 0), 'missing': t - made.get((c, s), 0)}
                 for (c, s), t in sorted(targets.items()) if made.get((c, s), 0) < t]
    sections = {}
    for q in queries:
        key = q['city'] + '/' + q['section']
        sections[key] = sections.get(key, 0) + 1
    pool = {'schema_version': SCHEMA_VERSION, 'kind': POOL_KIND, 'synthetic': synthetic,
        'created_at': created_at or utcnow(), 'catalog_exported_at': evaluation_at,
        'catalog_hash': fingerprint(catalog_doc['cities']), 'seed': seed, 'k': K,
        'pooling': {'top_n': TOP_N, 'random_n': RANDOM_N, 'pool_sizes': [SMALL_POOL, FULL_POOL], 'models': list(MODELS),
                    'view': POOLING_VIEW, 'evaluation_views': sorted(RANKING_VIEWS),
                    'view_reason': 'Top-10 in product display order (items then needs_confirmation). The items_only '
                                   'top-10 is a subset, so the ranking view is chosen at evaluation time.',
                    'rule': 'v5 top-10 (200) U v6 top-10 (200) U v5 top-10 (60) U seeded random 5'},
        'attribution': [json.loads(a) for a in sorted({json.dumps(c['attribution'], sort_keys=True, ensure_ascii=False)
                                                       for c in catalog.values() if c.get('attribution')})],
        'catalog': {i: catalog[i] for i in sorted(used)}, 'queries': queries, 'skipped': skipped,
        'shortfall': shortfall, 'sections': sections, 'label_scale': LABEL_SCALE}
    pool['estimate'] = estimate(pool)
    pool['estimate_by_split'] = {split: estimate(pool, (split,)) for split in SPLITS}
    pool['budget_check'] = budget_check(pool['estimate_by_split']['holdout'])
    pool['pool_hash'] = pool_hash(pool)
    return pool


def pool_hash(pool):
    return fingerprint({k: pool[k] for k in ('catalog', 'queries', 'seed', 'k', 'synthetic')})


def estimate(pool, splits=SPLITS):
    queries = [q for q in pool['queries'] if q['split'] in splits]
    judgments = sum(len(q['pooled_place_ids']) for q in queries)
    low, high = SECONDS_PER_JUDGMENT
    upper = len(queries) * MAX_POOLED_PER_QUERY
    return {'queries': len(queries), 'judgments': judgments,
            'seconds_low': judgments * low, 'seconds_high': judgments * high, 'basis': f'{low}~{high}s per judgment [추정]',
            'judgments_upper_bound': upper, 'upper_bound_seconds_high': upper * high,
            'upper_bound_basis': f'{MAX_POOLED_PER_QUERY} pooled candidates per query (no overlap between the three top-10 lists)'}


def budget_check(est):
    """Compare an estimate with the PRD labeling budget. Advice only; nothing is trimmed automatically.

    over_budget: the slow end (25 s/judgment) passes 4 h. certainly_over: even the fast end does.
    The PRD's own 600-judgment estimate is 2 h 30 m ~ 4 h 10 m, so it is over_budget but not certainly_over.
    """
    low_budget, high_budget = PRD_LABEL_BUDGET_SECONDS
    over, certain = est['seconds_high'] > high_budget, est['seconds_low'] > high_budget
    advice = None
    if over:
        advice = (('빠른 가정(판정당 15초)으로도 ' if certain else '느린 가정(판정당 25초)이면 ') +
                  'PRD 라벨 예산(4시간)을 넘습니다. 확정 질의 수(또는 도시)를 줄인 질의 파일로 새 pool 을 동결하거나, '
                  'holdout 을 먼저 달고 실측 속도(진행률 표시)를 본 뒤 정하십시오. 규칙·pooling 은 바꾸지 않습니다.')
    return {'budget_seconds': [low_budget, high_budget], 'estimate_seconds': [est['seconds_low'], est['seconds_high']],
            'queries': est['queries'], 'over_budget': over, 'certainly_over': certain, 'advice': advice}


# ---------------------------------------------------------------- blind view

def _fact_text(field, value):
    if field == 'public_map_tags' and isinstance(value, dict):
        tags = value.get('tags') or {}
        return ', '.join(f'{k}={tags[k]}' for k in PUBLIC_MAP_KEYS if tags.get(k)) or None
    if field == 'opening_hours' and isinstance(value, dict):
        weekly = value.get('weekly') or {}
        names, days = '월화수목금토일', ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
        index = lambda day: int(day) if str(day).isdigit() else days.index(day) if day in days else 99
        parts = []
        for day in sorted(weekly, key=lambda d: (index(d), str(d))):
            spans = weekly[day] or []
            text = ' '.join('-'.join(s) if isinstance(s, list) else f"{s.get('start')}-{s.get('end')}" for s in spans) or '휴무'
            parts.append(f'{names[index(day)] if index(day) < 7 else day} {text}')
        return ' / '.join(parts) or None
    if field == 'rating' and isinstance(value, dict):
        if value.get('rating') is None:
            return None
        return f"{value.get('rating')}/{value.get('scale')} ({value.get('total_rating_count')}건, {value.get('platform')})"
    if field == 'price' and isinstance(value, dict):
        return f"{value.get('amount_min')}~{value.get('amount_max')} {value.get('currency')} ({value.get('basis')}/{value.get('period')})"
    if field == 'closed':
        return '예' if value is True else '아니오' if value is False else None
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value) or None
    if isinstance(value, dict):
        return ', '.join(f'{k}={v}' for k, v in sorted(value.items()) if v not in (None, '', [])) or None
    return None if value is None else str(value)


def attribution_text(value):
    if isinstance(value, dict):
        return ' · '.join(str(value[k]) for k in ('text', 'license') if value.get(k)) or None
    return value or None


def blind_card(candidate, origin=None):
    """Whitelisted public facts only. No ids, scores, ranks or model names."""
    from .engine import straight_line_distance
    facts = []
    for row in sorted(candidate.get('facts', []), key=lambda f: (f.get('field') or '', f.get('id') or '')):
        if row.get('usable') is not True or row.get('field') not in FACT_LABELS:
            continue
        text = _fact_text(row['field'], row.get('value'))
        if text:
            facts.append({'label': FACT_LABELS[row['field']], 'value': text[:300],
                          'status': STATUS_LABELS.get(row.get('status'), row.get('status'))})
    sources = sorted({s.get('source_type') or 'unknown' for s in candidate.get('sources', []) if s.get('status') == 'active'})
    distance = straight_line_distance(origin, candidate) if origin else None
    return {'name': candidate.get('name'), 'native_name': candidate.get('native_name'),
            'category': candidate.get('category'), 'neighborhood': candidate.get('neighborhood'),
            'address': candidate.get('address'), 'tags': list(candidate.get('tags') or [])[:10], 'facts': facts,
            'source_types': sources, 'attribution': attribution_text(candidate.get('attribution')),
            'identity': '공개지도 기록(지점 미확인)' if candidate.get('identity_status') != 'verified' else '확인된 지점',
            'straight_line_m': round(distance) if distance is not None else None}


def query_view(query):
    """Conditions a labeler needs. The section is shown by its product label only."""
    from .general import LABELS
    c = query['snapshot']['conditions']
    return {'city': c['city'], 'date': c['visit']['date'], 'local_time': (c['visit'].get('local_time') or '')[:5] or None,
            'party_adults': (c.get('party') or {}).get('adults'), 'categories': c['categories'],
            'origin': (c.get('origin') or {}).get('label') or '지정 안 함(도시 중심 기준)',
            'taste': (c.get('preferred') or {}).get('tags') or [], 'budget': c.get('budget'),
            'section': LABELS[query['section']], 'human_query': query.get('human_query'),
            'ordering': '가까운 순' if query['snapshot'].get('ordering_profile') == 'nearby' else '기본'}


def blind_view(query, catalog):
    origin = query['snapshot']['conditions'].get('origin')
    return {'query': query_view(query),
            'cards': [blind_card(catalog[i], origin) for i in query['blind_order']]}


def render_card(card, index, total):
    lines = [f'[{index}/{total}] {card["name"]}' + (f' ({card["native_name"]})' if card.get('native_name') else ''),
             f'  분류: {card["category"]} · {card["identity"]}' + (f' · 동네: {card["neighborhood"]}' if card.get('neighborhood') else ''),
             f'  주소: {card.get("address") or "미확인"}']
    if card.get('straight_line_m') is not None:
        lines.append(f'  출발점에서 직선거리: {card["straight_line_m"]}m (도보 경로 아님)')
    if card.get('tags'):
        lines.append('  태그: ' + ', '.join(card['tags']))
    for fact in card['facts']:
        lines.append(f'  {fact["label"]}: {fact["value"]} [{fact["status"]}]')
    lines.append('  출처 유형: ' + (', '.join(card['source_types']) or '미확인') + (f' · {card["attribution"]}' if card.get('attribution') else ''))
    return '\n'.join(lines)


def render_query(view):
    q = view
    budget = q['budget']
    return '\n'.join([
        f'도시 {q["city"]} · {q["date"]} {q["local_time"] or "시간 미지정"} · 성인 {q["party_adults"] or "미지정"}명 · 분류 {", ".join(q["categories"])}',
        f'출발점: {q["origin"]} · 취향: {", ".join(q["taste"]) or "지정 안 함"} · 예산: ' +
        (f'{budget.get("amount_max")} {budget.get("currency")} 이하' if budget else '지정 안 함'),
        f'구획: {q["section"]} · 정렬: {q["ordering"]}' + (f' · 질의: {q["human_query"]}' if q.get('human_query') else '')])


# ---------------------------------------------------------------- labels

def new_labels(pool, reviewer_id):
    if not REVIEWER_PATTERN.match(reviewer_id or '') or '@' in reviewer_id:
        raise ValueError('Pseudonymous reviewer id required (letters, digits, - or _, max 32)')
    return {'schema_version': SCHEMA_VERSION, 'kind': LABEL_KIND,
            'label_source': 'synthetic_rubric' if pool['synthetic'] else 'independent_human',
            'pool_hash': pool['pool_hash'], 'reviewer_id': reviewer_id, 'label_scale': LABEL_SCALE,
            'created_at': utcnow(), 'queries': {}}


def verify_pool(pool):
    """Recompute every integrity value instead of trusting the stored ones."""
    if pool.get('kind') != POOL_KIND or pool.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Not a frozen label pool')
    if pool.get('pool_hash') != pool_hash(pool):
        raise ValueError('Pool file was edited after freezing (pool_hash mismatch)')
    for query in pool['queries']:
        if fingerprint(query['snapshot']) != query['snapshot_hash']:
            raise ValueError(f'Query {query["id"]} snapshot was edited after freezing')
        if set(query['fact_versions']) != set(query['pooled_place_ids']):
            raise ValueError(f'Query {query["id"]} fact versions do not cover its pooled candidates')
        for pid in query['pooled_place_ids']:
            candidate = pool['catalog'].get(pid)
            if candidate is None or fact_version(candidate) != query['fact_versions'][pid]:
                raise ValueError(f'Query {query["id"]} candidate facts changed after freezing')
        if set(query['candidate_ids']) - set(pool['catalog']):
            raise ValueError(f'Query {query["id"]} references a candidate missing from the catalog')


def check_labels(pool, labels):
    verify_pool(pool)
    if labels.get('kind') != LABEL_KIND or labels.get('pool_hash') != pool['pool_hash']:
        raise ValueError('Labels belong to a different frozen pool')
    expected = 'synthetic_rubric' if pool['synthetic'] else 'independent_human'
    if labels.get('label_source') != expected:
        raise ValueError('Label provenance does not match the pool')
    by_id = {q['id']: q for q in pool['queries']}
    for qid, row in labels['queries'].items():
        query = by_id.get(qid)
        if not query:
            raise ValueError(f'Unknown query {qid}')
        if (row['snapshot_hash'] != query['snapshot_hash'] or fingerprint(row['snapshot']) != row['snapshot_hash']
                or row['fact_versions'] != query['fact_versions']):
            raise ValueError(f'Query {qid} snapshot or candidate facts changed after labeling')
        pooled = set(query['pooled_place_ids'])
        if set(row['judgments']) - pooled or set(row.get('reasons', {})) - pooled:
            raise ValueError(f'Query {qid} judged a candidate outside its pool')
        if any(type(v) is not int or v not in range(4) for v in row['judgments'].values()):
            raise ValueError('Grades must be integers 0..3')


def start_query(labels, query):
    row = labels['queries'].get(query['id'])
    if row is None:
        row = labels['queries'][query['id']] = {
            'split': query['split'], 'city': query['city'], 'section': query['section'],
            'snapshot': deepcopy(query['snapshot']), 'snapshot_hash': query['snapshot_hash'],
            'fact_versions': dict(query['fact_versions']), 'judgments': {}, 'reasons': {}, 'skipped': [],
            'status': 'in_progress', 'started_at': utcnow(), 'updated_at': None, 'active_seconds': 0.0,
            'timed_judgments': 0}
    return row


def record(row, place_id, grade, seconds=None):
    if type(grade) is not int or grade not in range(4):
        raise ValueError('Grades must be integers 0..3')
    row['judgments'][place_id] = grade
    if place_id in row['skipped']:
        row['skipped'].remove(place_id)
    if seconds is not None:
        row['active_seconds'] = round(row['active_seconds'] + min(max(seconds, 0.0), 120.0), 3)
        row['timed_judgments'] += 1
    row['updated_at'] = utcnow()


def finish_query(row, query):
    row['status'] = 'complete' if set(row['judgments']) == set(query['pooled_place_ids']) else 'partial'
    row['updated_at'] = utcnow()


def progress(pool, labels, splits=SPLITS):
    queries = [q for q in pool['queries'] if q['split'] in splits]
    total = sum(len(q['pooled_place_ids']) for q in queries)
    rows = [labels['queries'].get(q['id']) for q in queries]
    done = sum(len(r['judgments']) for r in rows if r)
    timed = sum(r.get('timed_judgments', 0) for r in rows if r)
    seconds = sum(r.get('active_seconds', 0.0) for r in rows if r)
    remaining = total - done
    low, high = SECONDS_PER_JUDGMENT
    measured = seconds / timed if timed >= 10 else None
    return {'queries_total': len(queries), 'queries_complete': sum(bool(r) and r['status'] == 'complete' for r in rows),
            'judgments_total': total, 'judgments_done': done, 'judgments_remaining': remaining,
            'measured_seconds_per_judgment': round(measured, 1) if measured else None,
            'remaining_seconds': [round(remaining * measured)] * 2 if measured else [remaining * low, remaining * high]}


def format_duration(seconds):
    minutes = int(round(seconds / 60))
    return f'{minutes // 60}시간 {minutes % 60}분' if minutes >= 60 else f'{minutes}분'


def progress_line(state):
    low, high = state['remaining_seconds']
    eta = format_duration(low) if low == high else f'{format_duration(low)}~{format_duration(high)}'
    basis = f'실측 평균 {state["measured_seconds_per_judgment"]}초' if state['measured_seconds_per_judgment'] else '판정당 15~25초 가정'
    return (f'질의 {state["queries_complete"]}/{state["queries_total"]} 완료 · 판정 {state["judgments_done"]}/{state["judgments_total"]}'
            f' · 남은 예상 {eta} ({basis})')


# ---------------------------------------------------------------- evaluation input

def assemble(pool, labels, *, view, splits=SPLITS):
    """benchmark.evaluate input in pooled mode. Unlabeled queries stay out and are counted."""
    if view not in RANKING_VIEWS:
        raise ValueError(f'ranking view must be one of {sorted(RANKING_VIEWS)}')
    check_labels(pool, labels)
    queries, unlabeled = [], []
    for query in pool['queries']:
        if query['split'] not in splits:
            continue
        row = labels['queries'].get(query['id'])
        if not row:
            unlabeled.append(query['id']); continue
        queries.append({'id': query['id'], 'split': query['split'], 'section': query['section'],
            'query_group': query.get('query_group'), 'travel_group': query.get('travel_group'),
            'snapshot': deepcopy(query['snapshot']),
            'candidates': [deepcopy(pool['catalog'][i]) for i in query['candidate_ids']],
            'candidate_ids_60': list(query['candidate_ids_60']),
            'pooled_place_ids': list(query['pooled_place_ids']),
            'judgments': dict(row['judgments']), 'reviewer_id': labels['reviewer_id']})
    if not queries:
        raise ValueError('No labeled queries to evaluate')
    data = {'schema_version': 1, 'label_source': labels['label_source'], 'k': pool['k'],
            'judgment_mode': 'pooled', 'ranking_view': view, 'queries': queries}
    return data, unlabeled
