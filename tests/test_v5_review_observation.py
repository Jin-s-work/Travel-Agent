from dataclasses import replace
from tests.test_review_provider import request,record,page,collect,NOW
from src.research.observation import evaluate_bounded,contract_reasons
from src.research.language import wilson95,evaluate_predictions
from datetime import timedelta
import pytest


def test_bounded_unknown_dates_excluded_but_consume_record_cap():
    rows=[record(0,published_at=None),record(1),record(2,text_presence='rating_only')]
    req=request(provider='apify',observation_profile='bounded_observation_v1',max_review_records=2)
    result=collect([page(rows,sort_basis='unknown',continuity_verified=False)],request=req)
    assert len(result.records)==1 and result.coverage['considered_count']==2
    assert result.coverage['ambiguous_date_count']==1 and result.coverage['stop_reason']=='record_cap'
    assert result.coverage['source_page_count'] is None and result.coverage['dataset_read_page_count']==1
    assert result.coverage['source_attempt_limit'] is None and result.coverage['latest_records_claim'] is False


def test_bounded_publication_membership_does_not_invent_sorted_boundary():
    req=request(observation_profile='bounded_observation_v1')
    rows=[record(0,published_at=(NOW-timedelta(days=190)).isoformat()),record(1),record(2)]
    result=collect([page(rows,sort_basis='unknown',continuity_verified=False)],request=req)
    assert len(result.records)==2 and result.coverage['out_of_window_count']==1
    assert result.coverage['stop_reason']=='exhausted'
    assert result.coverage['source_coverage']=='provider_observation'


def test_bounded_never_reuses_strict_badge_or_automatically_enables_production():
    stats={'counts':dict(observed_records=200,text_count=200,classified_count=190,unknown_count=10,
        local_count=150,korean_count=4,rating_only_count=0,extraction_unknown_count=0)}
    ev=evaluate_bounded(stats,{'observation_profile':'bounded_observation_v1','collection_complete':True,'partial':False})
    assert ev['metrics']['local_share_lower_bound']==.75 and ev['metrics']['korean_share_upper_bound']==.07
    assert ev['strict_pass'] is False and ev['production_enabled'] is False and not ev['research_qualified']
    assert 'BOUNDED_POLICY_UNAPPROVED' in ev['reason_codes']
    assert contract_reasons({'observation_profile':'strict_source_v1'},status='approved',expired=False)


def test_intervals_do_not_pass_absent_korean_support():
    ev=evaluate_predictions([{'expected':'ja','predicted':'ja'}]*100,domain='independently_labeled_restaurant_reviews')
    assert ev['korean_recall'] is None and ev['korean_recall_interval95'] is None
    assert 0<ev['local_precision_interval95'][0]<1
    assert wilson95(0,0) is None
    with pytest.raises(ValueError):wilson95(2,1)


from tests.test_foundation_api import service
from tests.test_review_integration import reviews,submit
from tests.test_stage2_review_contracts import contract_body


def test_bounded_contract_does_not_replace_strict_or_historical_dependency(reviews):
    svc=reviews.app.state.reviews
    strict=reviews.client.post('/api/v2/admin/review-provider-contracts',json=contract_body(reviews)).json()
    bounded=reviews.client.post('/api/v2/admin/review-provider-contracts',json=contract_body(reviews,
        observation_profile='bounded_observation_v1',report_sha256='b'*64)).json()
    with svc.db.connect() as con:
        assert svc._contract_on(con)['id']==strict['id']
        assert svc._contract_on(con,profile='bounded_observation_v1')['id']==bounded['id']
        assert svc._contract_on(con,ident=strict['id'])['id']==strict['id']
        assert svc._contract_on(con,ident=bounded['id'])['id']==bounded['id']
    assert not bounded['strict_ready'] and not bounded['bounded_contract_ready']


def test_bounded_research_flag_controls_collection_without_consumer_promotion(reviews):
    from tests.test_foundation_api import _job
    response=submit(reviews,'bounded-disabled',observation_profile='bounded_observation_v1')
    assert response.status_code==409 and response.json()['error']['code']=='BOUNDED_OBSERVATION_DISABLED'
    reviews.app.state.reviews.bounded_enabled=True
    response=submit(reviews,'bounded-enabled',observation_profile='bounded_observation_v1')
    assert response.status_code==202,response.text
    assert _job(reviews.client,response.json())['state']=='succeeded'
    run=reviews.client.get(response.json()['status_url']).json()
    assert run['coverage']['observation_profile']=='bounded_observation_v1'
    assert run['evaluation']['bounded_observation']['production_enabled'] is False
    assert run['evaluation']['strict_pass'] is False
