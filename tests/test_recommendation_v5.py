"""V5 context ordering: fixed public anchor, permitted cuisine, no invented facts."""
from copy import deepcopy
import pytest
from src.recommendations.registry import model_snapshot
from src.recommendations.engine import recommend
from src.recommendations.hybrid_v5 import terms
from tests.test_hybrid_recommender import rows, request, cards
from tests.test_recommendation_engine import NOW, fact


def query():
    value = request('hybrid_v5')
    value['conditions']['preferred']['tags'] = ['해산물']
    return value


def public_rows():
    places = rows()
    for row, cuisine in zip(places, ['ramen', 'seafood', 'sushi']):
        row.update(pack_status='public_data', identity_status='needs_confirmation',provider='openstreetmap')
        for source in row['sources']:
            source.update(source_group='OpenStreetMap',policy_version='osm-public-beta-v1')
        for old in row['facts']:
            old['policy_version']='osm-public-beta-v1'
        tag = deepcopy(fact(row, 'closed'))
        tag.update(id=row['place_id']+'-cuisine', field='public_map_tags', status='provisional',
                   value={'tags': {'cuisine': cuisine}})
        row['facts'].append(tag)
    return places


def test_public_cuisine_matches_korean_preferences_without_qualifying_local_reviews():
    value=query(); places=public_rows()
    result=recommend(value, places, now=NOW)
    refs=result['sections']['reference']['needs_confirmation']
    assert refs[0]['place_id']=='p1'
    assert refs[0]['ranking_diagnostics']['matched_tags']==['seafood']
    assert refs[0]['ranking_diagnostics']['tag_evidence']['kind']=='public_cuisine_provisional'
    assert any('실제 메뉴' in reason['text'] for reason in refs[0]['supported_reasons'])
    assert result['section_status']['local_discovery']['displayable_count']==0
    assert all(not p['strict_badge'] for p in refs)


@pytest.mark.parametrize('change', ['expired','conflict','revoked','missing'])
def test_public_cuisine_never_uses_unavailable_or_conflicting_fact(change):
    places=public_rows(); value=query(); row=places[1]
    tag=next(r for r in row['facts'] if r['field']=='public_map_tags')
    if change=='expired':tag['expires_at']='2000-01-01T00:00:00Z'
    elif change=='conflict':tag['status']='conflict'
    elif change=='missing':tag['value']={'tags':{}}
    else:tag['source_id']='revoked'
    result=recommend(value, places, now=NOW)['sections']['reference']
    item=next(p for group in ('items','needs_confirmation') for p in result[group] if p['place_id']=='p1')
    assert item['ranking_diagnostics']['components']['preference']['value'] is None


def test_city_anchor_orders_cold_start_and_is_frozen_in_snapshot():
    value=request('hybrid_v5'); places=rows(); anchor=value['ranking_reference_origin']
    places[0].update(latitude=anchor['latitude']+.02,longitude=anchor['longitude'])
    places[1].update(latitude=anchor['latitude'],longitude=anchor['longitude'])
    result=cards(value,places)
    assert result[0]['place_id']=='p1'
    assert result[0]['features']['straight_distance']['value'] is None
    assert result[0]['ranking_diagnostics']['proximity_reference']=='city_center'
    assert any(r['code']=='CITY_CENTER_DISTANCE' for r in result[0]['supported_reasons'])
    before=deepcopy(places)
    assert recommend(value,places,now=NOW)==recommend(value,list(reversed(places)),now=NOW)
    assert places==before


def test_unresolved_explicit_origin_does_not_fall_back_to_city_center():
    value=request('hybrid_v5'); value['conditions']['origin']={'label':'Hotel'}
    value.update(model_snapshot(value))
    # Even a stale anchor remaining in an edited test snapshot cannot override origin.
    result=cards(value,rows())
    assert all(p['ranking_diagnostics']['components']['proximity']['value'] is None for p in result)
    assert all(p['ranking_diagnostics']['proximity_reference']=='unknown' for p in result)


def test_multilingual_taxonomy_and_missing_data_have_distinct_meanings():
    assert terms(['해산물, 피자', 'mariscos;寿司'])=={'seafood','pizza','sushi'}
    assert terms(['not seafood'])=={'not seafood'}
    assert terms([])==set()


def test_hard_failure_is_not_rescued_by_distance_or_food_preference():
    places=public_rows(); fact(places[1],'closed')['value']=True
    result=recommend(query(),places,now=NOW)['sections']['reference']
    assert 'p1' not in [p['place_id'] for group in ('items','needs_confirmation') for p in result[group]]
