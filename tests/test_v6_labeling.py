"""Blind pooled labeling tool contracts. Synthetic catalog only, no network.

Grades typed below are a synthetic test rubric (label_source=synthetic_rubric),
standing in for keystrokes. They are never written to evals/ and never claim
actual venue relevance.
"""
from copy import deepcopy
import io
import json
from math import log2
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.recommendations import labeling as L
from src.recommendations.benchmark import pooled_ranking_metrics, ranking_metrics, validate, evaluate
from scripts.label_recommendations import ScriptedKeys, label, load_labels
from scripts.evaluate_v6_labels import run as evaluate_labels
from scripts.prepare_v6_label_queries import draft, confirm, PLAN
from tests.test_recommendation_engine import NOW, catalog
from tests.test_stage2_general_models import valid_local

TAGS = [['ramen'], ['sushi'], ['tapas'], ['coffee'], ['seafood'], ['yakitori']]


def synthetic_rows(city, count):
    base = catalog(city)
    rows = []
    for i in range(count):
        row = deepcopy(base[i % 3])
        pid = f'syn_{city}_{i:02}'
        row.update(place_id=pid, name=f'합성 장소 {city} {i}', native_name=None, chain_id=None,
                   neighborhood=f'area_{i % 5}', tags=TAGS[i % len(TAGS)],
                   latitude=row['latitude'] + (i % 7) * .003, longitude=row['longitude'] + (i // 7) * .003)
        for source in row['sources']:
            source['id'] = pid + '_' + source['id']
        for fact in row['facts']:
            fact['id'] = pid + '_' + fact['id']
            fact['source_id'] = pid + '_' + fact['source_id']
            if fact['field'] == 'closed':
                fact['value'] = False
        if i % 4 == 0:  # synthetic observed-language evidence so local_discovery can rank
            valid_local(row)
        if i % 4 == 3:  # public-map-like rows: reference only, provisional facts
            row.update(identity_status='needs_confirmation', pack_status='public_data', provider='openstreetmap',
                       source_kind='public_map', attribution='© OpenStreetMap contributors · ODbL 1.0')
            row['facts'] = [{**row['facts'][0], 'id': pid + '_map', 'field': 'public_map_tags', 'status': 'provisional',
                             'value': {'tags': {'cuisine': TAGS[i % len(TAGS)][0], 'opening_hours': 'Mo-Su 11:00-22:00',
                                                'phone': '+00 SYNTHETIC'}, 'address_status': 'observed'}}]
        rows.append(row)
    return rows


def catalog_doc(tokyo=30, barcelona=30):
    from scripts.export_v6_label_catalog import document
    return document({'tokyo': synthetic_rows('tokyo', tokyo), 'barcelona': synthetic_rows('barcelona', barcelona)},
                    NOW.isoformat(), synthetic=True)


def queries_doc(confirmed=True):
    doc = draft(json.loads(PLAN.read_text()))
    for item in doc['items']:
        item['human_query'] = '합성 시험 요청 ' + item['id']  # stands in for the operator's own sentence
        if item['scenario'] == 'food':
            item['conditions']['preferred']['tags'] = ['sushi']
        if item['scenario'] == 'price':
            item['conditions']['budget']['amount_max'] = '3000' if item['city'] == 'tokyo' else '30.00'
    if confirmed:
        confirm(doc, {'all'})
    return doc


@pytest.fixture(scope='module')
def pool():
    return L.build_pool(queries_doc(), catalog_doc(), plan=json.loads(PLAN.read_text()), created_at=NOW.isoformat())


def walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key; yield from walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_keys(item)


# ------------------------------------------------------------------ blind view

def test_blind_view_never_exposes_scores_ranks_or_model_names(pool):
    assert pool['queries']
    for query in pool['queries']:
        view = L.blind_view(query, pool['catalog'])
        assert not set(walk_keys(view)) & L.FORBIDDEN_VIEW_KEYS
        text = json.dumps(view, ensure_ascii=False) + L.render_query(view['query']) + ''.join(
            L.render_card(card, i + 1, len(view['cards'])) for i, card in enumerate(view['cards']))
        for word in ('hybrid', 'v5', 'v6', 'score', 'utility', 'rank', 'diagnostic', 'syn_'):
            assert word not in text.lower(), word
        assert 'phone' not in text  # only whitelisted public-map tags


def test_display_order_is_seeded_and_independent_of_model_order(pool):
    for query in pool['queries']:
        assert sorted(query['blind_order']) == query['pooled_place_ids']
        expected = list(query['pooled_place_ids']); L._rng(pool['seed'], query['id'], 'display').shuffle(expected)
        assert query['blind_order'] == expected
    reversed_doc = catalog_doc()
    for city in reversed_doc['cities']:
        reversed_doc['cities'][city].reverse()
    again = L.build_pool(queries_doc(), reversed_doc, created_at=NOW.isoformat())
    assert [q['blind_order'] for q in again['queries']] == [q['blind_order'] for q in pool['queries']]


def test_pool_records_store_no_model_contribution_score_or_rank(pool):
    for query in pool['queries']:
        assert not set(walk_keys(query)) & {'score', 'ranking', 'ranking_diagnostics', 'tops', 'contributors', 'v5_200'}
        # Per-candidate records carry no model name; the snapshot does carry the product's
        # ranker_versions (same as a real run snapshot). Documented in LABELING.md.
        assert 'hybrid' not in json.dumps({k: v for k, v in query.items() if k != 'snapshot'})
        assert 'hybrid' in json.dumps(query['snapshot']['ranker_versions'])
    assert 'hybrid_v5' in pool['pooling']['models']  # pool-level rule text, not per candidate


# ------------------------------------------------------------------ pooling

def test_pool_is_union_of_three_top10_lists_plus_five_random_fixed_case(monkeypatch):
    """Hand-written rankings, not recomputed with the implementation's own model calls."""
    rows = synthetic_rows('tokyo', 30)
    ids = [r['place_id'] for r in rows]
    catalog = {r['place_id']: L.public_candidate(r) for r in rows}
    spec = next(i for i in queries_doc()['items'] if i['city'] == 'tokyo' and i['section'] == 'reference')
    item = lambda pid: {'place_id': pid}
    rankings = {  # (version, pool size) -> (items, needs_confirmation)
        ('hybrid_v5', 30): (ids[0:6], ids[6:12]),    # displayed top-10 = 00..09
        ('hybrid_v6', 30): (ids[5:15], ids[15:18]),  # top-10 = 05..14
        ('hybrid_v5', 20): ([], ids[15:20] + [ids[0]]),  # 60-pool stand-in: 15..19, 00
    }
    monkeypatch.setattr(L, 'select_pool', lambda snapshot, rows, limit: sorted(r['place_id'] for r in rows)[:30 if limit == 200 else 20])
    def fake_result(snapshot, candidates, version):
        items, confirm = rankings[(version, len(candidates))]
        return {'sections': {spec['section']: {'items': [item(i) for i in items], 'needs_confirmation': [item(i) for i in confirm]}}}
    monkeypatch.setattr(L, 'model_result', fake_result)
    record, skip = L.pool_query(spec, catalog, NOW.isoformat())
    assert skip is None
    union = set(ids[0:20])
    random_added = set(record['pooled_place_ids']) - union
    assert union <= set(record['pooled_place_ids']) and len(record['pooled_place_ids']) == 25
    assert len(random_added) == 5 and random_added <= set(ids[20:30])
    assert set(ids[0:6]) <= set(record['pooled_place_ids'])  # items_only top-10 is covered by the same pool
    assert record['candidate_ids_60'] == ids[:20] and record['pool_counts']['random_added'] == 5
    assert record['fact_versions'] == {i: L.fact_version(catalog[i]) for i in record['pooled_place_ids']}
    again, _ = L.pool_query(spec, catalog, NOW.isoformat())
    assert again['pooled_place_ids'] == record['pooled_place_ids'] and again['blind_order'] == record['blind_order']
    assert record['snapshot']['candidate_pool_limit'] == 200 and record['snapshot']['local_feature_enabled'] is False


def test_unconfirmed_or_incomplete_queries_are_not_frozen_and_shortfall_is_reported():
    doc = queries_doc(confirmed=False)
    confirm(doc, None, 'holdout')
    target = next(i for i in doc['items'] if i['scenario'] == 'food' and i['split'] == 'holdout')
    target['conditions']['preferred']['tags'] = None
    small = L.build_pool(doc, catalog_doc(barcelona=3), plan=json.loads(PLAN.read_text()))
    reasons = {s['id']: s['reason'] for s in small['skipped']}
    assert all(reasons[i['id']] == 'NOT_CONFIRMED_BY_HUMAN' for i in doc['items'] if i['split'] == 'development')
    assert reasons[target['id']] == 'MISSING_HUMAN_FIELDS'
    assert {s['reason'] for s in small['skipped'] if s['city'] == 'barcelona' and s['split'] == 'holdout'} == {'CATALOG_TOO_SMALL'}
    gaps = {(g['city'], g['split']): g for g in small['shortfall']}
    assert gaps[('barcelona', 'holdout')]['created'] == 0 and gaps[('barcelona', 'holdout')]['target'] == 20
    assert gaps[('tokyo', 'development')]['missing'] == 10


def test_draft_queries_follow_plan_and_need_human_confirmation():
    doc = draft(json.loads(PLAN.read_text()))
    assert len(doc['items']) == 60 and not any(i['human_confirmed'] for i in doc['items'])
    for city in ('tokyo', 'barcelona'):
        assert sum(i['city'] == city and i['split'] == 'development' for i in doc['items']) == 10
        assert sum(i['city'] == city and i['split'] == 'holdout' for i in doc['items']) == 20
    confirmed, blocked = confirm(doc, {'all'})
    # Mechanical defaults alone never confirm: every item needs the operator's own human_query.
    assert confirmed == [] and len(blocked) == 60 and all('human_query' in b['missing'] for b in blocked)
    assert sum(b['missing'] != ['human_query'] for b in blocked) == 20
    assert all(i['origin'] is None or set(i['origin']) == {'public_area'} for i in doc['items'])
    confirmed, blocked = confirm(doc, None, 'holdout')
    assert confirmed == [] and len(blocked) == 40


def test_private_coordinates_and_fields_cannot_enter_a_pool():
    with pytest.raises(ValueError, match='raw coordinates'):
        L.resolve_origin('tokyo', {'latitude': 35.6, 'longitude': 139.7})
    origin, basis = L.resolve_origin('tokyo', {'public_area': 'east', 'synthetic_hotel': True})
    assert basis == 'public_area:east' and '합성 숙소' in origin['label']
    row = synthetic_rows('tokyo', 1)[0]
    with pytest.raises(ValueError, match='Private'):
        L.public_candidate({**row, 'owner_id': 'u1'})
    clean = L.public_candidate({**row, 'excluded': True, 'photos': [{'id': 'x'}], 'dependencies': {'pack_id': 'p'}})
    assert not {'excluded', 'photos', 'dependencies'} & set(clean)
    mixed = catalog_doc(); mixed['cities']['tokyo'][0]['synthetic'] = False
    with pytest.raises(ValueError, match='Synthetic and real'):
        L.build_pool(queries_doc(), mixed)


def test_private_accommodation_reference_cannot_enter_a_snapshot():
    spec = next(i for i in queries_doc()['items'] if i['city'] == 'tokyo')
    for key, value in (('origin_selection', {'kind': 'accommodation', 'accommodation_id': 'acc_private_123'}),
                       ('origin', {'label': 'hotel', 'latitude': 35.6, 'longitude': 139.7})):
        bad = deepcopy(spec); bad['conditions'][key] = value
        with pytest.raises(ValueError, match=key):
            L.build_snapshot(bad, NOW.isoformat())
    snapshot, _ = L.build_snapshot({**spec, 'origin': None}, NOW.isoformat())
    assert snapshot['conditions']['origin_selection'] == {'kind': 'none', 'accommodation_id': None, 'expected_version': None}
    snapshot, _ = L.build_snapshot({**spec, 'origin': {'public_area': 'north'}}, NOW.isoformat())
    assert snapshot['conditions']['origin_selection']['kind'] == 'manual'
    assert snapshot['conditions']['origin_selection']['accommodation_id'] is None


def test_same_request_in_both_splits_is_rejected():
    doc = queries_doc()
    dev = next(i for i in doc['items'] if i['split'] == 'development' and i['city'] == 'tokyo')
    hold = next(i for i in doc['items'] if i['split'] == 'holdout' and i['city'] == 'tokyo')
    hold.update({k: deepcopy(dev[k]) for k in ('scenario', 'section', 'origin', 'conditions', 'ordering_profile')})
    with pytest.raises(ValueError, match='other split'):
        L.build_pool(doc, catalog_doc())


# ------------------------------------------------------------------ CLI

def test_cli_saves_every_key_resumes_and_never_prints_model_info(pool, tmp_path):
    path = tmp_path / 'labels.json'
    labels = load_labels(path, pool, 'R1')
    assert labels['label_source'] == 'synthetic_rubric'
    lines = []
    ticks = iter(range(0, 10_000, 20))
    result = label(pool, labels, path, ScriptedKeys(['2', 'r', '  가깝고   조건 맞음 ', '3', 'b', '1', 's', 'x', 'q']),
                   out=lines.append, splits=('holdout',), clock=lambda: next(ticks))
    assert result == 'quit'
    saved = json.loads(path.read_text())
    first = next(q for q in sorted(pool['queries'], key=lambda q: q['id']) if q['split'] == 'holdout')
    row = saved['queries'][first['id']]
    a, b, c = first['blind_order'][:3]
    assert row['judgments'] == {a: 2, b: 1} and row['reasons'] == {b: '가깝고 조건 맞음'} and row['skipped'] == [c]
    assert row['status'] == 'partial' and row['snapshot'] == first['snapshot']
    printed = '\n'.join(lines).lower()
    for word in ('hybrid', 'score', 'rank', 'syn_'):
        assert word not in printed
    assert '남은 예상' in printed
    # Resume: continues at the first unjudged candidate and revisits the skipped one.
    again = load_labels(path, pool, 'R1')
    remaining = [p for p in first['blind_order'] if p not in row['judgments']]
    label(pool, again, path, ScriptedKeys(['0'] * len(remaining) + ['q']), out=lambda _: None, splits=('holdout',))
    saved = json.loads(path.read_text())['queries'][first['id']]
    assert saved['status'] == 'complete' and set(saved['judgments']) == set(first['pooled_place_ids'])
    with pytest.raises(SystemExit):
        load_labels(path, pool, 'R2')


class InterruptAfter(ScriptedKeys):
    def key(self):
        if not self.keys:
            raise KeyboardInterrupt
        return super().key()


def test_cli_ctrl_c_saves_partial_state_without_traceback(pool, tmp_path):
    path = tmp_path / 'labels.json'
    labels = L.new_labels(pool, 'R1')
    lines = []
    assert label(pool, labels, path, InterruptAfter(['2']), out=lines.append, splits=('holdout',)) == 'quit'
    saved = json.loads(path.read_text())
    (row,) = saved['queries'].values()
    assert len(row['judgments']) == 1 and row['status'] == 'partial'
    assert any('Ctrl-C' in line for line in lines)


def test_reviewer_must_be_pseudonymous(pool):
    for bad in ('me@example.com', '', 'a b', 'x' * 33):
        with pytest.raises(ValueError):
            L.new_labels(pool, bad)


def test_progress_estimate_uses_range_then_measured_average(pool, tmp_path):
    labels = L.new_labels(pool, 'R1')
    state = L.progress(pool, labels, ('holdout',))
    total = sum(len(q['pooled_place_ids']) for q in pool['queries'] if q['split'] == 'holdout')
    assert state['judgments_total'] == total and state['remaining_seconds'] == [total * 15, total * 25]
    ticks = iter(range(0, 100_000, 20))
    label(pool, labels, tmp_path / 'l.json', ScriptedKeys(['1'] * 12 + ['q']), out=lambda _: None,
          splits=('holdout',), clock=lambda: next(ticks))
    state = L.progress(pool, labels, ('holdout',))
    assert state['judgments_done'] == 12 and state['measured_seconds_per_judgment'] == 20.0
    assert state['remaining_seconds'] == [(total - 12) * 20] * 2
    assert L.estimate(pool)['seconds_low'] == sum(len(q['pooled_place_ids']) for q in pool['queries']) * 15


def test_changed_candidate_facts_invalidate_saved_labels(pool, tmp_path):
    labels = L.new_labels(pool, 'R1')
    label(pool, labels, tmp_path / 'l.json', ScriptedKeys(['2', 'q']), out=lambda _: None, splits=('holdout',))
    changed = deepcopy(pool)
    qid = next(iter(labels['queries']))
    query = next(q for q in changed['queries'] if q['id'] == qid)
    query['fact_versions'][query['pooled_place_ids'][0]] = 'different'
    changed['pool_hash'] = L.pool_hash(changed)
    relabeled = deepcopy(labels); relabeled['pool_hash'] = changed['pool_hash']
    with pytest.raises(ValueError, match='changed after freezing'):
        L.check_labels(changed, relabeled)
    forged = deepcopy(labels); forged['label_source'] = 'independent_human'
    with pytest.raises(ValueError, match='provenance'):
        L.check_labels(pool, forged)
    edited_row = deepcopy(labels); edited_row['queries'][qid]['snapshot']['conditions']['party']['adults'] = 6
    with pytest.raises(ValueError, match='changed after labeling'):
        L.check_labels(pool, edited_row)


def labeled_everything(pool):
    labels = L.new_labels(pool, 'R1')
    for query in pool['queries']:
        row = L.start_query(labels, query)
        for pid in query['blind_order']:
            L.record(row, pid, 1, 18.0)  # synthetic rubric, stands in for keystrokes
        L.finish_query(row, query)
    return labels


def test_editing_frozen_pool_after_labeling_is_rejected_by_evaluation(pool):
    """Catalog/snapshot edits are caught by recomputation, not by stored hashes."""
    labels = labeled_everything(pool)
    query = next(q for q in pool['queries'] if q['split'] == 'holdout')
    pid = query['pooled_place_ids'][0]

    edited = deepcopy(pool)
    edited['catalog'][pid]['name'] = 'edited'; edited['catalog'][pid]['tags'] = ['edited']
    with pytest.raises(ValueError, match='pool_hash mismatch'):
        evaluate_labels(edited, labels, view='displayed', repeats=1)
    edited['pool_hash'] = L.pool_hash(edited)  # even a recomputed pool hash does not hide changed facts
    labels_again = deepcopy(labels); labels_again['pool_hash'] = edited['pool_hash']
    with pytest.raises(ValueError, match='candidate facts changed'):
        evaluate_labels(edited, labels_again, view='displayed', repeats=1)

    edited = deepcopy(pool)
    next(q for q in edited['queries'] if q['id'] == query['id'])['snapshot']['conditions']['party']['adults'] = 6
    with pytest.raises(ValueError, match='pool_hash mismatch'):
        L.assemble(edited, labels, view='displayed')
    edited['pool_hash'] = L.pool_hash(edited)
    labels_again = deepcopy(labels); labels_again['pool_hash'] = edited['pool_hash']
    with pytest.raises(ValueError, match='snapshot was edited'):
        L.assemble(edited, labels_again, view='displayed')


def test_pooled_mode_requires_explicit_view_and_k5(pool):
    labels = labeled_everything(pool)
    with pytest.raises(TypeError):
        L.assemble(pool, labels)  # no default ranking view
    with pytest.raises(ValueError, match='ranking view'):
        L.assemble(pool, labels, view='best')
    data, _ = L.assemble(pool, labels, view='items_only')
    assert data['k'] == 5 and data['ranking_view'] == 'items_only'
    for key, value, message in (('ranking_view', None, 'explicit ranking_view'), ('k', 3, 'k=5')):
        bad = deepcopy(data); bad.pop(key) if value is None else bad.update({key: value})
        with pytest.raises(ValueError, match=message):
            validate(bad)
    assert 'ranking_view' not in pool and 'ranking_view' not in json.dumps({k: pool[k] for k in ('queries', 'k', 'seed')})


def test_budget_check_reports_worst_case_and_warns_over_prd_budget(pool):
    est = L.estimate(pool, ('holdout',))
    holdout = sum(q['split'] == 'holdout' for q in pool['queries'])
    assert est['judgments_upper_bound'] == holdout * 35 and est['upper_bound_seconds_high'] == holdout * 35 * 25
    assert pool['estimate_by_split']['holdout'] == est and pool['budget_check']['queries'] == holdout
    worst = {'queries': 40, 'judgments': 40 * 35, 'seconds_low': 40 * 35 * 15, 'seconds_high': 40 * 35 * 25}
    check = L.budget_check(worst)
    assert check['over_budget'] is True and check['certainly_over'] is True and check['budget_seconds'] == [9000, 14400]
    assert '줄인' in check['advice']
    prd = L.budget_check({'queries': 40, 'judgments': 600, 'seconds_low': 600 * 15, 'seconds_high': 600 * 25})
    assert prd['over_budget'] is True and prd['certainly_over'] is False and '느린 가정' in prd['advice']
    assert L.budget_check({'queries': 30, 'judgments': 450, 'seconds_low': 450 * 15, 'seconds_high': 450 * 25})['advice'] is None


# ------------------------------------------------------------------ pooled metrics

def test_pooled_metrics_never_fill_unjudged_with_zero():
    m = pooled_ranking_metrics(['a', 'x', 'b'], {'a': 3, 'b': 2}, ['a', 'b', 'x', 'c'], 3)
    assert m['status'] == 'coverage_insufficient' and m['ndcg'] is None and m['precision'] is None
    assert m['judged_at_k'] == pytest.approx(2 / 3) and m['unjudged_top_ids'] == ['x']
    done = pooled_ranking_metrics(['b', 'a', 'c'], {'a': 3, 'b': 2, 'c': 0}, ['a', 'b', 'c', 'z'], 5)
    assert done['ndcg'] == pytest.approx((3 + 7 / log2(3)) / (7 + 3 / log2(3)))
    assert done['precision'] == 2 / 5 and done['judged_at_k'] == 1 and done['recall'] == 1
    full = ranking_metrics(['b', 'a', 'c'], {'a': 3, 'b': 2, 'c': 0}, ['a', 'b', 'c'], 5)
    assert {k: full[k] for k in ('ndcg', 'precision', 'recall', 'mrr')} == {k: done[k] for k in ('ndcg', 'precision', 'recall', 'mrr')}
    assert pooled_ranking_metrics([], {'a': 1}, ['a'], 5)['judged_at_k'] is None
    assert pooled_ranking_metrics(['a'], {}, ['a'], 5)['reason'] == 'NO_INDEPENDENT_JUDGMENTS'
    with pytest.raises(ValueError):
        pooled_ranking_metrics(['a'], {'q': 1}, ['a'], 5)
    with pytest.raises(ValueError):
        pooled_ranking_metrics(['a'], {'a': 4}, ['a'], 5)


def test_full_mode_contract_is_unchanged():
    from scripts.build_recommender_benchmark import build
    data = build()
    result = evaluate(data, repeats=1)
    assert 'judgment_mode' not in result and 'pool_size_comparison' not in result
    assert 'judged_at_k' not in result['summary']['general_v3']['by_split']['development']
    bad = deepcopy(data); bad['ranking_view'] = 'displayed'
    with pytest.raises(ValueError, match='pooled mode only'):
        validate(bad)
    bad = deepcopy(data); bad['judgment_mode'] = 'pooled'
    with pytest.raises(ValueError, match='explicit ranking_view'):
        validate(bad)
    bad.update(ranking_view='items_only', k=5)
    with pytest.raises(ValueError, match='pooled_place_ids'):
        validate(bad)


def test_end_to_end_pooled_evaluation_keeps_v5_and_reports_coverage(pool, tmp_path):
    labels = L.new_labels(pool, 'R1')
    partial_id = next(q['id'] for q in sorted(pool['queries'], key=lambda q: q['id']) if q['split'] == 'holdout')
    # Synthetic rubric standing in for keystrokes: taste tag match = 3, otherwise 1.
    for query in pool['queries']:
        row = L.start_query(labels, query)
        taste = set((query['snapshot']['conditions'].get('preferred') or {}).get('tags') or [])
        order = query['blind_order'][:2] if query['id'] == partial_id else query['blind_order']
        for pid in order:
            L.record(row, pid, 3 if taste & set(pool['catalog'][pid]['tags']) else 1, 18.0)
        L.finish_query(row, query)
    path = tmp_path / 'labels.json'
    path.write_text(json.dumps(labels, ensure_ascii=False))
    result = evaluate_labels(pool, json.loads(path.read_text()), view='displayed', repeats=1)
    assert result['judgment_mode'] == 'pooled' and result['ranking_view'] == 'displayed'
    alternate = result['alternate_views']['items_only']
    assert alternate['ranking_view'] == 'items_only' and alternate['production_model'] == 'hybrid_v5'
    assert alternate['promotion_candidate'] is False and set(result['alternate_views']) == {'items_only'}
    comparison = result['model_comparison']
    assert comparison['production_model'] == 'hybrid_v5' and comparison['promotion_candidate'] is False
    assert 'NO_INDEPENDENT_HUMAN_LABELS' in comparison['reason_codes']
    partial = next(q for q in result['queries'] if q['query_id'] == partial_id)
    assert any(m['metrics']['coverage_insufficient'] for m in partial['models'].values())
    assert 'UNJUDGED_HOLDOUT_QUERIES' in comparison['reason_codes']
    holdout = result['summary']['hybrid_v5']['by_split']['holdout']
    assert holdout['judged_at_k']['value'] is not None and holdout['coverage_insufficient_queries'] >= 1
    assert result['pool_size_comparison']['baseline'] == 'pool_200'
    assert result['provider_calls'] == 0 and result['labeler_count'] == 1


# ------------------------------------------------------------------ export (local SQLite)

def test_export_collect_reads_public_catalog_without_private_fields(service):
    from datetime import datetime, timezone
    from src.discovery.public_places import PublicDiscovery, normalize, center
    from scripts.export_v6_label_catalog import collect, document, ReadOnlyConnection
    c = center('tokyo')
    payload = {'elements': [{'id': 800000000 + i, 'type': 'node', 'lat': c['latitude'] + .001 * i, 'lon': c['longitude'],
        'tags': {'name': f'Synthetic public {i}', 'amenity': 'restaurant', 'cuisine': 'ramen', 'opening_hours': 'Mo-Su 11:00-21:00'}}
        for i in range(8)]}
    operator = service.login('label-export-operator').user
    provider = PublicDiscovery(service.app.state.db, fetcher=lambda city: (normalize(payload, city), 1000),
                               clock=lambda: datetime.now(timezone.utc))
    with service.app.state.db.connect() as con:
        provider._store(con, SimpleNamespace(id=operator['id']), 'tokyo', normalize(payload, 'tokyo'), datetime.now(timezone.utc))
    with service.app.state.db.connect() as con:
        cities = collect(con, ('tokyo',))
    rows = cities['tokyo']
    assert len(rows) == 8 and all(r['provider'] == 'openstreetmap' and not r['synthetic'] for r in rows)
    assert all(not {'excluded', 'dependencies', 'photos'} & set(r) for r in rows)
    assert all(f['value'] is not None for r in rows for f in r['facts'])
    doc = document(cities, datetime.now(timezone.utc).isoformat())
    # Real-shaped public-map rows only qualify for reference, all as needs_confirmation.
    q = {'items': [{'id': 'tokyo-11', 'city': 'tokyo', 'split': 'holdout', 'scenario': 'unspecified', 'section': 'reference',
                    'origin': None, 'conditions': {'visit': {'date': '2026-11-06', 'local_time': '12:00'}},
                    'human_query': '합성 시험 요청', 'human_confirmed': True}]}
    shown = L.build_pool(q, doc)
    assert len(shown['queries']) == 1 and shown['queries'][0]['pool_counts']['pooled'] == 8
    view = L.blind_view(shown['queries'][0], shown['catalog'])
    assert not set(walk_keys(view)) & L.FORBIDDEN_VIEW_KEYS
    assert all('opening_hours=Mo-Su 11:00-21:00' in json.dumps(c, ensure_ascii=False) for c in view['cards'])
    assert all(c['attribution'] == '© OpenStreetMap contributors · ODbL 1.0' for c in view['cards'])
    frozen = shown['queries'][0]
    result = L.model_result(frozen['snapshot'], [shown['catalog'][i] for i in frozen['candidate_ids']], 'hybrid_v5')
    # Public-map rows are always needs_confirmation: items_only ranks nothing, displayed ranks them.
    assert L.section_ranking(result, 'reference', 'items_only') == []
    assert L.section_ranking(result, 'reference', 'displayed')
    with pytest.raises(ValueError, match='READ_ONLY'):
        ReadOnlyConnection(None).execute('UPDATE place_identities SET name=?', ('x',))


from tests.test_foundation_api import service  # noqa: E402,F401  (real local SQLite app fixture)
