"""Synthetic ordering contracts; never measured venue/language relevance."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from tests.test_recommendation_engine import NOW,catalog,snapshot,review,fact
from src.recommendations.engine import recommend
from src.recommendations.models import RecommendationInput


def request(**overrides):
    s=snapshot(recommendation_model_version='general_v3',movement_version='v2',**overrides)
    s['conditions'].update(preferred={'tags':[]},origin=None,budget=None)
    return s


def valid_local(row,l=150,k=4,u=10):
    review(row);r=row['review_evidence'];r['counts'].update(text_count=200,classified_count=200-u,unknown_count=u,local_count=l,korean_count=k)
    r['metrics'].update(local_share_lower_bound=l/200,korean_share_upper_bound=(k+u)/200)
    return row


def test_optional_input_omission_rankable_null_score_and_legacy_replay():
    row=valid_local(catalog()[0]);s=request();out=recommend(s,[row],now=NOW)
    for kind in ('local_discovery','landmark'):
        card=out['sections'][kind]['items'][0]
        assert card['rankable'] and card['score'] is None and card['score_complete'] is False
        assert card['personalized'] is False and card['ranker_version'].endswith('_general_v3')
    del s['recommendation_model_version']
    old=recommend(s,[row],now=NOW)
    assert not old['sections']['local_discovery']['items']
    assert old['sections']['local_discovery']['insufficient_data'][0]['ranker_version']=='local_editorial_v2'


def test_local_no_editorial_fallback_iconic_independent_and_reference_separate():
    rows=catalog()[:2];valid_local(rows[0]);out=recommend(request(),rows,now=NOW)
    assert [x['place_id'] for x in out['sections']['local_discovery']['items']]==[rows[0]['place_id']]
    assert len(out['sections']['landmark']['items'])==2
    assert len(out['sections']['reference']['items'])==2
    assert not out['sections']['local_discovery']['insufficient_data']
    assert out['section_status']['local_discovery']['insufficient_count']==1


def test_rule_order_is_fixed_deterministic_not_weighted_fallback():
    rows=[]
    for index,(l,k,u) in enumerate([(150,4,10),(160,4,10),(160,2,10)]):
        r=deepcopy(catalog()[0]);r.update(place_id=str(index),chain_id=None,neighborhood=None);rows.append(valid_local(r,l,k,u))
    s=request();a=recommend(s,rows,now=NOW);b=recommend(s,list(reversed(rows)),now=NOW)
    assert a==b
    assert [r['place_id'] for r in a['sections']['local_discovery']['items']]==['2','1','0']


def test_explicit_nearby_changes_only_declared_ordering_profile():
    rows=[valid_local(catalog()[0],150),valid_local(catalog()[1],160)]
    s=request();s['conditions']['origin']={'label':'Origin','latitude':rows[0]['latitude'],'longitude':rows[0]['longitude']}
    assert recommend(s,rows,now=NOW)['sections']['local_discovery']['items'][0]['place_id']==rows[1]['place_id']
    s['ordering_profile']='nearby'
    assert recommend(s,rows,now=NOW)['sections']['local_discovery']['items'][0]['place_id']==rows[0]['place_id']


def test_numeric_relaxation_custom_badge_not_immutable_quality_gate():
    row=valid_local(catalog()[0],110,4,10);row['review_evidence']['evaluation'].update(decision='fail',strict_pass=False,reason_codes=['LOCAL_SHARE_BELOW_MIN'])
    assert not recommend(request(),[row],now=NOW)['sections']['local_discovery']['items']
    s=request(review_language_filter={'min_local_share':.5,'max_korean_share':.1})
    card=recommend(s,[row],now=NOW)['sections']['local_discovery']['items'][0]
    assert card['criteria_group']=='custom_criteria' and not card['strict_badge']
    row['review_evidence']['evaluation']['reason_codes'].append('CLASSIFICATION_QUALITY_UNVERIFIED')
    assert not recommend(s,[row],now=NOW)['sections']['local_discovery']['items']


@pytest.mark.parametrize('field,value',[('min_classified_texts',0),('max_unknown_share',1),('ignore_quality',True)])
def test_quality_cannot_be_user_overridden(field,value):
    with pytest.raises(ValidationError):RecommendationInput.model_validate({'trip_version':1,'conditions_version':1,'review_language_filter':{field:value}})


def test_unknown_70_no_korean_does_not_qualify_and_invalid_counts_block():
    row=valid_local(catalog()[0],120,0,70)
    assert not recommend(request(review_language_filter={'min_local_share':0,'max_korean_share':1}),[row],now=NOW)['sections']['local_discovery']['items']
    row['review_evidence']['counts']['unknown_count']=-1
    assert not recommend(request(),[row],now=NOW)['sections']['local_discovery']['items']


def test_known_violation_excluded_unknown_separate_even_when_language_pass():
    row=valid_local(catalog()[0]);fact(row,'max_party')['value']=1
    assert not recommend(request(),[row],now=NOW)['sections']['local_discovery']['items']
    fact(row,'max_party').update(value=None,status='unknown')
    out=recommend(request(),[row],now=NOW)
    assert out['sections']['local_discovery']['needs_confirmation'][0]['language_qualified']


def test_plain_map_or_editorial_never_implies_popularity():
    row=catalog()[0];row['facts']=[f for f in row['facts'] if f['field']!='iconic_evidence']
    out=recommend(request(),[row],now=NOW)
    assert not out['sections']['landmark']['items'] and out['sections']['reference']['items']


def test_platform_cohorts_not_cross_platform_scores():
    rows=[]
    for index,(platform,count) in enumerate([('B',900),('A',200),('A',400)]):
        r=catalog()[0];r.update(place_id=str(index),chain_id=None,neighborhood=None)
        fact(r,'iconic_evidence')['value']={'evidence_type':'platform_popular','platform':platform}
        fact(r,'rating')['value'].update(platform=platform,total_rating_count=count)
        rows.append(r)
    out=recommend(request(),rows,now=NOW)['sections']['landmark']['items']
    assert [r['place_id'] for r in out]==['2','1','0']
    assert all(r['score'] is None for r in out)


def test_frozen_comparison_writes_no_relevance_claim_and_optional_regression():
    from src.product.evaluation import compare,markdown
    row=valid_local(catalog()[0]);s=request();s['evaluation_at']=NOW.isoformat()
    out=compare(s,[row],'general-v3')
    assert out['retrieval_calls']==0 and out['deterministic']
    assert out['optional_inputs_omitted']['local_discovery']['ranked']==1
    assert out['relevance_evaluation']['ndcg'] is None and out['automatic_winner'] is None
    assert all(r['hard_violations']['after']==0 for r in out['comparison'])
    assert '미측정' in markdown(out)


def test_required_budget_and_distance_unknown_do_not_become_satisfied():
    row=valid_local(catalog()[0]);s=request()
    s['conditions']['budget']={'currency':'JPY','basis':'per_person','period':'meal','amount_max':'2000'}
    fact(row,'price').update(value=None,status='unknown')
    out=recommend(s,[row],now=NOW)
    assert not out['sections']['local_discovery']['items']
    assert 'PRICE_UNKNOWN' in out['sections']['local_discovery']['needs_confirmation'][0]['reason_codes']
    s['conditions']['radius_m']=100
    out=recommend(s,[row],now=NOW)
    assert 'RADIUS_UNKNOWN' in out['sections']['local_discovery']['needs_confirmation'][0]['reason_codes']


def test_explanations_only_accept_server_numbers_and_order():
    from src.recommendations.explanations import fallback,validate
    row=valid_local(catalog()[0]);card=recommend(request(),[row],now=NOW)['sections']['local_discovery']['items'][0]
    result=fallback(card)
    assert '190건 중 현지어 원문 150건' in result['recommendations'][0]['reason_sentences'][0]['text']
    altered={'recommendations':deepcopy(result['recommendations'])}
    altered['recommendations'][0]['reason_sentences'][0]['text']='현지인 95% 맛집입니다.'
    assert not validate(card,altered)
    assert fallback(card,altered)['explanation_mode']=='server_template'


@pytest.mark.parametrize('reason,state',[('REVIEW_STALE','expired'),('CLASSIFICATION_QUALITY_UNVERIFIED','quality_insufficient'),('REVIEW_PRODUCTION_DISABLED','disabled'),('PROVIDER_FAILED','provider_failed')])
def test_section_absence_keeps_operational_reason_not_invalid_count(reason,state):
    row=catalog()[0];row['review_evidence']={'state':'unavailable','counts':None,'evaluation':{'strict_pass':False,'reason_codes':[reason]}}
    out=recommend(request(),[row],now=NOW)
    assert out['section_status']['local_discovery']['state']==state
    assert reason in out['section_status']['local_discovery']['reason_codes']
    assert out['sections']['landmark']['items']


def test_explicit_editorial_type_beats_legacy_level_field():
    row=catalog()[0]
    for source in row['sources']:source['source_type']='tourism'
    fact(row,'iconic_evidence')['value']={'evidence_type':'editorial_recognition','level':'city'}
    out=recommend(request(),[row],now=NOW)
    assert out['sections']['landmark']['items'][0]['iconic_kind']=='editorial_recognition'


def test_route_shortlist_does_not_apply_local_language_gate_to_iconic():
    from src.recommendations.engine import route_candidates
    row=catalog()[0];s=request(review_language_filter={'required':True,'apply_to':['local_discovery']})
    s['conditions']['origin']={'label':'origin','latitude':35.68,'longitude':139.77}
    assert [p['place_id'] for p in route_candidates(s,[row],NOW,10)]==[row['place_id']]
    fact(row,'closed')['value']=True
    assert route_candidates(s,[row],NOW,10)==[]


@pytest.mark.parametrize('local,korean,unknown,passes',[(200,0,0,True),(199,0,0,False),(199,0,1,False),(199,1,0,False)])
def test_all_observed_local_reviews_never_accept_unknown_or_other_language(local,korean,unknown,passes):
    row=valid_local(catalog()[0],local,korean,unknown)
    s=request();s['review_language_filter']={'min_local_share':1,'max_korean_share':0}
    out=recommend(s,[row],now=NOW)
    assert bool(out['sections']['local_discovery']['items']) is passes


def test_reference_without_positive_evidence_does_not_invent_a_reason():
    from src.recommendations.explanations import render_general
    assert render_general({'source_refs':[{'id':'source'}]})==[]
