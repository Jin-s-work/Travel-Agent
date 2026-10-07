from datetime import datetime,timedelta,timezone
import json
import httpx
import pytest

from src.providers.reviews import CollectionRequest,ProviderPage,ReviewProviderError
from src.providers.apify_reviews import ApifyReviewCollectionProvider
from src.providers.fake_reviews import FakeReviewCollectionProvider
from src.research.collection import collect_reviews


NOW=datetime(2026,10,1,tzinfo=timezone.utc)


def request(**changes):
    values=dict(place_identity_id='place_test',external_place_id='ChIJ'+('a'*23),provider='fake',
        adapter_version='fake-review-v1',requested_start=(NOW-timedelta(days=180)).isoformat(),
        requested_end=NOW.isoformat(),policy_version='test-v1',actor_id='admin',job_id='job_test',
        idempotency_key='key',max_total_charge_usd='0.5')
    values.update(changes)
    return CollectionRequest(**values)


def record(i, **changes):
    value={'provider_review_id':f'r{i}','original_text':f'Original synthetic text {i}',
           'text_presence':'present','published_at':(NOW-timedelta(hours=i+1)).isoformat(),
           'date_precision':'exact'}
    value.update(changes)
    return value


def page(records, cursor=None, exhausted=True, **changes):
    return ProviderPage(records,cursor,exhausted,sort_basis=changes.pop('sort_basis','published_at'),
                        continuity_verified=changes.pop('continuity_verified',True),**changes)


def collect(pages, **kw):
    req=kw.pop('request',request())
    provider=FakeReviewCollectionProvider(pages=pages)
    remote=provider.start(req)
    return collect_reviews(req,lambda cursor,attempt:provider.fetch_page(remote,cursor,req),
        transform=kw.pop('transform',lambda r:{k:v for k,v in r.items() if k!='original_text'}),**kw)


@pytest.mark.parametrize('field,value',[('max_review_records',201),('max_pages',21),('max_attempts_per_page',3),
    ('max_elapsed_seconds',901),('max_response_bytes',4000001),('max_review_records',True),('lookback_days',181),
    ('locale_filter','ja'),('keyword_filter','food'),('rating_filter','5'),('sort','mostRelevant'),
    ('max_total_charge_usd','NaN'),('max_total_charge_usd','Infinity'),('max_total_charge_usd','-1')])
def test_request_server_caps(field,value):
    with pytest.raises(ValueError):request(**{field:value})


def test_record_cap_counts_rating_only_and_overshoot_received():
    rows=[record(i,text_presence='rating_only' if i%2==0 else 'present') for i in range(210)]
    result=collect([page(rows)])
    assert len(result.records)==200
    assert result.coverage['received_count']==210
    assert result.coverage['overshoot_count']==10
    assert result.coverage['stop_reason']=='record_cap'
    assert sum(r['text_presence']=='rating_only' for r in result.records)==100
    assert result.coverage['platform_census'] is False


def test_duplicates_by_id_only_and_same_text_different_id_kept():
    result=collect([page([record(0),record(0),record(1,original_text=record(0)['original_text'])])])
    assert len(result.records)==2 and result.coverage['duplicate_count']==1


def test_missing_ids_not_silently_deduplicated():
    result=collect([page([record(0,provider_review_id=None),record(0,provider_review_id=None)])])
    assert len(result.records)==2
    assert result.coverage['continuity_verified'] is False
    assert 'MISSING_STABLE_ID' in result.reason_codes


def test_exact_date_boundary_and_edited_sort_uses_edited_field():
    rows=[record(0,published_at=(NOW-timedelta(days=300)).isoformat(),edited_at=(NOW-timedelta(days=1)).isoformat()),
          record(1,published_at=(NOW-timedelta(days=301)).isoformat(),edited_at=(NOW-timedelta(days=181)).isoformat())]
    result=collect([page(rows,sort_basis='edited_at')])
    assert len(result.records)==1
    assert result.coverage['sort_basis']=='edited_at'
    assert result.coverage['stop_reason']=='date_boundary'


def test_approximate_date_does_not_make_boundary():
    result=collect([page([record(0,published_at=(NOW-timedelta(days=181)).isoformat(),date_precision='approximate')])])
    assert result.coverage['stop_reason']=='exhausted'
    assert 'AMBIGUOUS_DATE' in result.reason_codes
    assert not result.coverage['continuity_verified']


def test_unknown_sort_does_not_claim_date_boundary():
    result=collect([page([record(0,published_at=(NOW-timedelta(days=181)).isoformat())],sort_basis='unknown')])
    assert len(result.records)==1
    assert 'SORT_BASIS_UNKNOWN' in result.reason_codes


def test_sort_inversion_is_partial():
    result=collect([page([record(2),record(1)])])
    assert result.state=='partial' and 'SORT_INVERSION' in result.reason_codes


def test_repeated_cursor_stops_before_recalling_page():
    result=collect([page([record(0)],'page:1',False),page([record(1)],'page:1',False)])
    assert result.coverage['pages']==2
    assert result.coverage['stop_reason']=='cursor_expired'
    assert 'REPEATED_CURSOR' in result.reason_codes


def test_repeated_page_detected_even_new_cursor():
    result=collect([page([record(0)],'page:1',False),page([record(0)],'page:2',False)])
    assert 'REPEATED_PAGE' in result.reason_codes
    assert result.state=='partial'


def test_page_cap_is_partial_and_received_count_preserved():
    result=collect([page([record(0)],'page:1',False)],request=request(max_pages=1))
    assert result.coverage['received_count']==1
    assert result.coverage['stop_reason']=='page_cap'


def test_two_attempts_include_first_and_retry_after():
    req=request()
    attempts=[]; sleeps=[]
    def fetch(cursor,attempt):
        attempts.append(attempt)
        if attempt==1:raise ReviewProviderError('provider_error',retryable=True,retry_after=2)
        return page([record(0)])
    result=collect_reviews(req,fetch,transform=lambda r:{'provider_review_id':r['provider_review_id']},sleeper=sleeps.append)
    assert attempts==[1,2] and sleeps==[2]
    assert result.coverage['attempts']==2 and result.state=='succeeded'


def test_midpage_provider_blocked_does_not_retry():
    req=request(); provider=FakeReviewCollectionProvider(pages=[page([record(0)],'page:1',False)],
        failures={('page:1',1):ReviewProviderError('provider_blocked')})
    remote=provider.start(req)
    result=collect_reviews(req,lambda cursor,attempt:provider.fetch_page(remote,cursor,req),transform=lambda r:{'id':r['provider_review_id']})
    assert result.state=='partial' and result.coverage['stop_reason']=='provider_blocked'
    assert len(result.records)==1 and result.coverage['attempts']==2


def test_checkpoint_resume_never_repeats_committed_page_or_stores_body():
    checkpoints=[]
    class Crash(Exception):pass
    def checkpoint(value):
        checkpoints.append(value)
        raise Crash()
    with pytest.raises(Crash):
        collect([page([record(0)],'page:1',False)],checkpoint=checkpoint)
    assert 'original_text' not in json.dumps(checkpoints)
    req=request(); calls=[]
    def fetch(cursor,attempt):
        calls.append(cursor)
        return page([record(1)])
    result=collect_reviews(req,fetch,transform=lambda r:{'provider_review_id':r['provider_review_id']},initial=checkpoints[-1])
    assert calls==['page:1'] and len(result.records)==2


def test_time_cap_and_guard():
    ticks=iter([0,1,2,3])
    result=collect([],request=request(max_elapsed_seconds=1),clock=lambda:next(ticks))
    assert result.coverage['stop_reason']=='time_cap'


def test_zero_denominator_window_is_not_a_platform_census():
    result=collect([page([])])
    assert result.records==[] and result.coverage['stop_reason']=='exhausted'
    assert result.coverage['exhaustion_scope']=='provider_returned_window'


def apify_request(**changes):
    return request(provider='apify',adapter_version='apify-compass-v1',**changes)


def row(**changes):
    value={'reviewId':'review1','text':'Texto original para la prueba.','textTranslated':'테스트 번역문',
        'originalLanguage':'es','language':'ko','publishedAtDate':NOW.isoformat(),
        'scrapedAt':NOW.isoformat(),'stars':5,'reviewOrigin':'Google','placeId':'ChIJ'+('a'*23),
        'name':'PRIVATE AUTHOR','reviewerUrl':'PRIVATE URL','reviewerPhotoUrl':'PRIVATE PHOTO'}
    value.update(changes)
    return value


def test_apify_start_pins_build_caps_original_fields_no_author():
    calls=[]
    def handler(req):
        calls.append(req)
        if req.method=='POST':
            return httpx.Response(201,json={'data':{'id':'run1','status':'SUCCEEDED','defaultDatasetId':'dataset1'}})
        return httpx.Response(200,json=[row()],headers={'x-apify-pagination-total':'1'})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    provider=ApifyReviewCollectionProvider('secret',build='0.0.527',client=client)
    req=apify_request(); remote=provider.start(req); result=provider.fetch_page(remote,None,req)
    body=json.loads(calls[0].content)
    assert body['personalData'] is False and body['reviewsOrigin']=='google' and body['reviewsSort']=='newest'
    assert body['maxReviews']==200 and body['reviewsFilterString']==''
    assert calls[0].url.params['build']=='0.0.527' and calls[0].url.params['maxTotalChargeUsd']=='0.5'
    assert 'secret' not in str(calls[0].url)
    assert result.records[0]['original_language']=='es'
    assert result.records[0]['original_language_verified'] is False
    assert not any('PRIVATE' in str(v) for v in result.records)
    assert 'reviewer' not in calls[1].url.params['fields']
    assert result.sort_basis=='unknown' and not result.continuity_verified
    assert result.exhausted is True


@pytest.mark.parametrize('changes,presence',[
    ({'text':None,'textTranslated':'번역만 있습니다'},'present'),
    ({'text':None,'textTranslated':None},'rating_only'),
    ({'text':None},'present')])
def test_apify_text_structure(changes,presence):
    p=ApifyReviewCollectionProvider('secret',build='0.0.527')
    assert p.canonical_record(row(**changes),apify_request())['text_presence']==presence
    unknown=row(text=None,textTranslated=None);del unknown['textTranslated']
    assert p.canonical_record(unknown,apify_request())['text_presence']=='unextractable'


def test_apify_unknown_fields_never_replaced_by_hl_or_language():
    p=ApifyReviewCollectionProvider('secret',build='0.0.527')
    record=row();del record['originalLanguage']
    assert p.canonical_record(record,apify_request())['original_language'] is None


@pytest.mark.parametrize('status',[301,302,401,403])
def test_redirects_and_protected_origins_stop(status):
    p=ApifyReviewCollectionProvider('secret',build='0.0.527',client=httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(status,headers={'Location':'http://127.0.0.1/private'}))))
    with pytest.raises(ReviewProviderError,match='provider_blocked'):p.start(apify_request())


def test_response_size_and_shape_are_bounded():
    p=ApifyReviewCollectionProvider('secret',build='0.0.527',client=httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,content=b' '*200))))
    with pytest.raises(ReviewProviderError,match='parse_error'):p.start(apify_request(max_response_bytes=100))


def test_branch_and_origin_mismatch_rejected():
    p=ApifyReviewCollectionProvider('secret',build='0.0.527')
    with pytest.raises(ReviewProviderError,match='place_identity_mismatch'):
        p.canonical_record(row(placeId='wrong'),apify_request())
    with pytest.raises(ReviewProviderError,match='parse_error'):
        p.canonical_record(row(reviewOrigin='Tripadvisor'),apify_request())


def test_apify_timeout_does_not_retry_start():
    calls=[]
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout('PRIVATE response context')
    p=ApifyReviewCollectionProvider('secret',build='0.0.527',client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ReviewProviderError,match='provider_outcome_unknown'):p.start(apify_request())
    assert len(calls)==1


def test_terminal_costs_are_preliminary_and_author_metadata_is_discarded():
    p=ApifyReviewCollectionProvider('secret',build='0.0.527')
    result=p._metadata({'data':{'id':'run1','status':'SUCCEEDED','usageTotalUsd':.12,'log':'PRIVATE',
        'chargedEventCounts':{'review-scraped':200,'apify-actor-start':1,'userSecret':4}}})
    assert result['usage_usd']=='0.12' and result['usage_final'] is False
    assert 'PRIVATE' not in str(result) and 'userSecret' not in str(result)


@pytest.mark.parametrize('code',[204,404])
def test_remote_deletion_idempotent_dataset_then_run(code):
    paths=[]
    def handler(req):
        paths.append((req.method,req.url.path))
        return httpx.Response(code)
    p=ApifyReviewCollectionProvider('secret',build='0.0.527',client=httpx.Client(transport=httpx.MockTransport(handler)))
    remote={'run_id':'run1','dataset_id':'dataset1'}
    assert p.delete_dataset(remote,apify_request())['deleted']
    assert p.delete_run(remote,apify_request())['deleted']
    assert paths==[('DELETE','/v2/datasets/dataset1'),('DELETE','/v2/actor-runs/run1')]


def test_malformed_record_is_sanitized_partial():
    result=collect([page([123])])
    assert result.state=='partial' and 'MALFORMED_RECORD' in result.reason_codes


def test_keyless_adapter_keeps_app_constructible_but_never_calls_remote():
    calls=[]
    p=ApifyReviewCollectionProvider('',build='0.0.527',client=httpx.Client(transport=httpx.MockTransport(lambda req:calls.append(req))))
    assert p.configured is False
    with pytest.raises(ReviewProviderError,match='provider_not_configured'):p.start(apify_request())
    assert calls==[]


def test_budget_cap_is_partial_without_zeroing_existing_records():
    req=request();calls=[]
    def fetch(cursor,attempt):
        calls.append((cursor,attempt))
        if cursor is None:return page([record(0)],'next',False)
        raise ReviewProviderError('budget_cap')
    result=collect_reviews(req,fetch,transform=lambda r:{'provider_review_id':r['provider_review_id']})
    assert result.state=='partial' and result.coverage['stop_reason']=='budget_cap'
    assert result.coverage['unique_count']==1
    assert calls==[(None,1),('next',1)]


def test_retry_cap_is_two_total_attempts_not_three():
    attempts=[]
    def fetch(cursor,attempt):
        attempts.append(attempt)
        raise ReviewProviderError('provider_error',retryable=True)
    result=collect_reviews(request(),fetch,transform=lambda r:r)
    assert attempts==[1,2] and result.state=='partial'
