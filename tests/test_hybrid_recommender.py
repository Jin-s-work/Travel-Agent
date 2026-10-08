"""Ranking behavior and independent hand-calculated metric contracts."""
from copy import deepcopy
from math import log2
import pytest
from src.recommendations.engine import recommend
from src.recommendations.hybrid import cosine, terms
from src.recommendations.registry import model_snapshot
from src.recommendations.benchmark import ranking_metrics, evaluate
from tests.test_recommendation_engine import NOW, catalog, snapshot, fact
from tests.test_stage2_general_models import valid_local


def rows():
    result = []
    for i, tags in enumerate([['ramen', 'noodles'], ['sushi'], ['museum', 'art']]):
        row = valid_local(catalog()[0]); row.update(place_id=f'p{i}', tags=tags, chain_id=None, neighborhood=None)
        row['facts'] = [f for f in row['facts'] if f['field'] != 'tags']
        result.append(row)
    return result


def request(version='hybrid_v4'):
    value = snapshot(movement_version='v2', evaluation_at=NOW.isoformat())
    value['conditions'].update(origin=None, preferred={'tags': []}, budget=None)
    value.update(model_snapshot(value, version=version))
    return value


def cards(value, places, kind='landmark'):
    return recommend(value, places, now=NOW)['sections'][kind]['items']


def test_explicit_taste_changes_ranking_without_training_or_extra_calls():
    places = rows(); value = request(); value['conditions']['preferred'] = {'tags': ['스시']}
    before = cards({**value, 'recommendation_model_version': 'general_v3'}, places)
    after = cards(value, places)
    assert before[0]['place_id'] == 'p0' and after[0]['place_id'] == 'p1'
    assert after[0]['ranking_diagnostics']['matched_tags'] == ['sushi']
    assert after[0]['ranking_diagnostics']['trained'] is False
    assert any(x['code'] == 'CONTENT_MATCH' for x in after[0]['supported_reasons'])


def test_input_order_determinism_and_no_candidate_mutation():
    places = rows(); original = deepcopy(places); value = request()
    value['conditions']['preferred']['tags'] = ['ramen']
    assert recommend(value, places, now=NOW) == recommend(value, list(reversed(places)), now=NOW)
    assert original == places


def test_unknown_tags_are_null_and_missing_weight_is_not_redistributed():
    places = rows(); places[0]['tags'] = []; value = request()
    value['conditions']['preferred']['tags'] = ['ramen']
    item = next(x for x in cards(value, places) if x['place_id'] == 'p0')
    detail = item['ranking_diagnostics']
    assert item['score'] is None and not item['score_complete']
    assert detail['components']['preference']['value'] is None
    assert detail['score_upper'] > detail['score_lower']
    assert sum(x['weight'] for x in detail['components'].values()) == 1
    assert detail['missing_components'] == ['preference']


def test_conflicting_tag_fact_cannot_fall_back_to_convenient_catalog_tags():
    places = rows(); value = request(); value['conditions']['preferred']['tags'] = ['ramen']
    tag = deepcopy(fact(places[0], 'closed'))
    tag.update(id='conflict-tags', field='tags', status='conflict', value=['ramen'])
    places[0]['facts'].append(tag)
    item = next(p for p in cards(value, places) if p['place_id'] == 'p0')
    assert item['ranking_diagnostics']['components']['preference']['value'] is None


def test_no_inputs_is_cold_start_rankable_not_zero_results():
    result = cards(request(), rows())
    assert len(result) == 3
    assert all(p['ranking_diagnostics']['profile'] == 'evidence' for p in result)


def test_origin_distance_matters_and_remains_straight_line():
    places = rows(); value = request(); places[0].update(latitude=35.9, longitude=139.9)
    value['conditions']['origin'] = {'label': 'Hotel', 'latitude': places[1]['latitude'], 'longitude': places[1]['longitude']}
    result = cards(value, places)
    assert result[-1]['place_id'] == 'p0'
    assert result[0]['ranking_diagnostics']['distance_basis'] == 'straight_line_not_walking_time'


def test_unknown_origin_geometry_never_gets_positive_proximity_score():
    places = rows(); places[0]['latitude'] = None; value = request()
    value['conditions']['origin'] = {'label': 'Hotel', 'latitude': 35.68, 'longitude': 139.77}
    result = recommend(value, places, now=NOW)['sections']['landmark']
    item = next(p for group in result.values() for p in group if p['place_id'] == 'p0')
    assert item['ranking_diagnostics']['components']['proximity']['value'] is None


def test_shrinkage_can_beat_raw_review_count_within_same_platform_only():
    places = rows()
    for p, rating, count in zip(places, [4.2, 4.9, 5.0], [10000, 600, 1]):
        fact(p, 'iconic_evidence')['value'] = {'evidence_type': 'platform_popular', 'platform': 'synthetic'}
        fact(p, 'rating')['value'].update(platform='synthetic', rating=rating, total_rating_count=count)
    result = cards(request(), places)
    assert result[0]['place_id'] == 'p1'
    assert result[0]['ranking_diagnostics']['rating']['cohort_places'] == 3
    fact(places[2], 'rating')['value']['platform'] = 'another'
    fact(places[2], 'iconic_evidence')['value']['platform'] = 'another'
    result = cards(request(), places)
    isolated = next(p for p in result if p['place_id'] == 'p2')
    assert isolated['ranking_diagnostics']['rating']['cohort_places'] == 1


@pytest.mark.parametrize('bad', ['closed', 'capacity', 'language', 'expired'])
def test_perfect_preference_cannot_rescue_hard_or_language_failures(bad):
    places = rows(); value = request(); value['conditions']['preferred']['tags'] = ['ramen']
    if bad == 'closed': fact(places[0], 'closed')['value'] = True
    elif bad == 'capacity': fact(places[0], 'max_party')['value'] = 1
    elif bad == 'expired': places[0]['review_evidence']['expires_at'] = '2020-01-01T00:00:00+00:00'
    else: places[0]['review_evidence']['counts'].update(classified_count=130, unknown_count=70, local_count=120, korean_count=0)
    assert 'p0' not in [p['place_id'] for p in cards(value, places, 'local_discovery')]


def test_opted_in_soft_avoid_is_honored_without_automatic_learning():
    value = request(); places = rows()
    assert cards(value, places)[0]['place_id'] == 'p0'
    value['soft_avoid_place_ids'] = ['p0']
    result = cards(value, places)
    assert result[-1]['place_id'] == 'p0'
    assert result[-1]['ranking_diagnostics']['soft_avoid_multiplier'] == .5


def test_content_math_unicode_alias_and_no_zero_imputation():
    assert terms(['寿司', '초밥', 'ＳＵＳＨＩ', 'sushi']) == {'sushi'}
    assert cosine({'sushi'}, {'sushi'}, [{'sushi'}]) == 1
    assert cosine({'sushi'}, {'ramen'}, [{'sushi'}, {'ramen'}]) == 0
    assert cosine({'sushi'}, set(), [set()]) is None


def test_metrics_use_hand_computed_graded_dcg_and_fixed_k_denominator():
    grades = {'a': 3, 'b': 2, 'c': 0}
    actual = ranking_metrics(['b', 'a', 'c'], grades, list(grades), 3)
    assert actual['ndcg'] == pytest.approx((3 + 7 / log2(3)) / (7 + 3 / log2(3)))
    assert actual['precision'] == 2 / 3 and actual['recall'] == 1
    assert ranking_metrics(['a'], grades, list(grades), 3)['precision'] == 1 / 3


def test_incomplete_and_zero_denominators_do_not_claim_accuracy():
    assert ranking_metrics(['a'], {}, ['a'], 3)['ndcg'] is None
    result = ranking_metrics([], {'a': 0}, ['a'], 3)
    assert result['ndcg'] is None and result['recall'] is None
    assert ranking_metrics([], {'a': 3}, ['a'], 3)['ndcg'] == 0


@pytest.mark.parametrize('ranking,labels,universe', [(['a', 'a'], {'a': 3}, ['a']), (['b'], {'a': 3}, ['a']), (['a'], {'a': True}, ['a']), (['a'], {'a': -1}, ['a'])])
def test_invalid_metrics_rejected(ranking, labels, universe):
    with pytest.raises(ValueError): ranking_metrics(ranking, labels, universe)


def test_benchmark_never_creates_its_own_labels_or_claims_user_quality():
    fixture = {'schema_version': 1, 'label_source': 'synthetic_rubric', 'queries': [
        {'id': 'q', 'split': 'development', 'section': 'landmark', 'snapshot': request(), 'candidates': rows(), 'judgments': {}}]}
    result = evaluate(fixture, repeats=1)
    assert result['summary']['hybrid_v4']['by_split']['development']['ndcg']['value'] is None
    assert result['actual_user_quality'] == 'unmeasured' and result['provider_calls'] == 0
    fixture['queries'].append({**deepcopy(fixture['queries'][0]), 'id': 'q2', 'split': 'holdout'})
    with pytest.raises(ValueError, match='leaks'): evaluate(fixture)


# The normal authenticated job API must persist the new model and safely replay
# it without additional provider calls. Ownership behavior is shared with v3.
from tests.test_foundation_api import service, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.test_discovery_intents import trip_for, submit


def test_normal_api_saves_new_model_and_repeat_read_does_not_recompute(discovery):
    import_pack(discovery); trip = trip_for(discovery.client)
    response = submit(discovery.client, trip)
    assert response.status_code == 202
    assert _job(discovery.client, response.json())['state'] == 'succeeded'
    url = f"/api/v2/trips/{trip['id']}/recommendations/{response.json()['run_id']}"
    first = discovery.client.get(url).json(); second = discovery.client.get(url).json()
    assert first['snapshot']['recommendation_model_version'] == 'hybrid_v5'
    assert first['snapshot']['ranking_spec']['trained'] is False
    assert first['result'] == second['result']
    items = first['result']['sections']['landmark']['items'] + first['result']['sections']['landmark']['needs_confirmation']
    assert items and all(p['ranking_diagnostics']['version'] == 'hybrid_v5' for p in items)
