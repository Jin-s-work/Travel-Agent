from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import pytest
from src.discovery.candidate_pool import ExpandedPublicDiscovery,public_areas
from src.discovery.public_places import FetchedPlaces,normalize,center
from tests.test_public_discovery import public,prepare
from tests.test_foundation_api import service
from tests.test_hybrid_recommender import rows,request
from tests.test_recommendation_engine import NOW,fact
from src.recommendations.service import Recommendations
from src.recommendations.engine import recommend


def run(public,trip,base):
    from tests.test_foundation_api import _job
    receipt=public.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'bounded-public-query'}).json()
    job=_job(public.client,receipt)
    assert job['state']=='succeeded', (job['state'],job.get('error_code'),job.get('stage'))
    return public.client.get(base+'/recommendations/'+receipt['run_id']).json()


def install(public, *, count=60, repeated=False, raw_limit=400):
    calls=[];clock=public.clock;start=clock[0]
    def fetch(city,*,categories=None,area=None):
        calls.append({'city':city,'categories':categories,'area':area})
        idx=0 if repeated else len(calls)-1
        items=FetchedPlaces([dict(external_id=f'node/{80000000+idx*60+i}',name=f'Synthetic venue {idx}-{i}',
            native_name=None,address=f'Public address {i}',address_status='observed',latitude=area['latitude']+.0001*i,
            longitude=area['longitude'],category='restaurant',tags=[],neighborhood=None,
            source_url=f'https://www.openstreetmap.org/node/{80000000+idx*60+i}',observed_tags={},center_distance_m=i)
            for i in range(min(count,area['record_limit']))])
        items.raw_count=len(items);items.deduped_count=0;items.rejected_count=0
        return items,1000
    settings=SimpleNamespace(candidate_pool_limit=200,candidate_raw_record_limit=raw_limit,candidate_query_limit=7,candidate_deadline_seconds=120)
    provider=ExpandedPublicDiscovery(public.app.state.db,settings=settings,fetcher=fetch,clock=lambda:clock[0],
        sleeper=lambda seconds:clock.__setitem__(0,clock[0]+timedelta(seconds=seconds)),monotonic=lambda:(clock[0]-start).total_seconds())
    public.app.state.discovery.public_provider=provider
    return calls


def test_actual_four_bounded_queries_store_evaluate_200_then_get_never_collects(public):
    calls=install(public);trip,base=prepare(public);output=run(public,trip,base)
    assert len(calls)==4
    meta=output['result']['candidate_counts'];assert meta['evaluated_count']==200
    assert meta['fetched_count']==240 and meta['requested_limit']==200
    assert output['result']['public_discovery']['coverage']['private_origin_sent'] is False
    again=public.client.get(base+'/recommendations/'+output['run_id']+'?page_size=12').json()
    assert again['pages']['reference']['total']==200 and len(calls)==4
    cursor=again['pages']['reference']['next_cursor']
    public.client.get(base+'/recommendations/'+output['run_id'],params={'page_size':12,'section':'reference','cursor':cursor})
    assert len(calls)==4


def test_only_37_unique_results_reuse_public_cache_and_preserve_call_cap(public):
    calls=install(public,count=37,repeated=True);trip,base=prepare(public);output=run(public,trip,base)
    assert len(calls)==5 # unchanged per-user day limit, never endpoint rotation
    assert output['result']['candidate_counts']['unique_count']==37
    assert output['result']['candidate_counts']['deduped_count']==148
    assert output['result']['public_discovery']['reason']=='PUBLIC_DISCOVERY_DAILY_LIMIT'
    assert output['result']['public_discovery']['fetched_count']==185


def test_raw_cap_is_applied_before_last_external_request(public):
    calls=install(public,raw_limit=73);trip,base=prepare(public);output=run(public,trip,base)
    assert [c['area']['record_limit'] for c in calls]==[60,13]
    assert output['result']['candidate_counts']['fetched_count']==73
    assert output['result']['public_discovery']['reason']=='raw_record_cap'


def test_private_origin_never_changes_public_query_plan(public):
    calls=install(public,raw_limit=60);trip,base=prepare(public)
    env=public.client.get(base+'/discovery-conditions').json();conditions=env['conditions']
    conditions['origin']={'label':'PRIVATE HOTEL','latitude':36.0,'longitude':139.0}
    public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':conditions})
    # Existing helper fixes condition version 1; use service to run explicit v2.
    from tests.test_foundation_api import _job
    receipt=public.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':2},headers={'Idempotency-Key':'private-origin-test'}).json()
    assert _job(public.client,receipt)['state']=='succeeded'
    assert len(calls)==1 and calls[0]['area']['latitude']==center('paris')['latitude']
    assert 'PRIVATE HOTEL' not in str(calls) and '36.0' not in str(calls)


def test_200th_candidate_is_scored_before_top_k_and_input_order_does_not_matter():
    pool=[];template=rows()[0]
    for i in range(200):
        row=deepcopy(template);row['place_id']=f'p{i:03}'
        for source in row['sources']:source['place_id']=row['place_id']
        for f in row['facts']:f['place_id']=row['place_id']
        row['tags']=['seafood'] if i==199 else ['ramen'];pool.append(row)
    snap=request('hybrid_v5');snap.update(result_contract='discovery_result_v1',candidate_pool_limit=200)
    snap['conditions']['preferred']['tags']=['seafood']
    chosen=Recommendations._select(snap,pool)
    result=recommend(snap,chosen,now=NOW)
    selected=result['sections']['landmark']['items']
    assert selected[0]['place_id']=='p199'
    assert result==recommend(snap,list(reversed(chosen)),now=NOW)


def test_same_name_without_branch_evidence_is_not_merged():
    c=center('tokyo');raw={'elements':[{'id':i+1,'type':'node','lat':c['latitude'],'lon':c['longitude'], 'tags':{'name':'Same name','amenity':'restaurant'}} for i in range(2)]}
    assert len(normalize(raw,'tokyo'))==2
    assert all(a['public_area_id'] for a in public_areas('barcelona'))


def test_candidate_retrieval_checkpoint_reuses_completed_areas_without_external_calls(public):
    calls=install(public,raw_limit=60);trip,base=prepare(public);output=run(public,trip,base)
    provider=public.app.state.discovery.public_provider
    with public.app.state.db.connect() as con:
        import json
        row=con.execute('SELECT checkpoint_json,payload_json FROM jobs WHERE id=?',(output['job_id'],)).fetchone()
        cp=json.loads(row['checkpoint_json'])
    class ReadContext:
        job={'id':output['job_id'],'checkpoint':cp}
        def guard(self,*a,**kw):pass
        def checkpoint(self,*a,**kw):raise AssertionError('Completed cap should need no new checkpoint')
    actor=SimpleNamespace(id=public.user['id'])
    status=provider.ensure(actor,trip['id'],'paris',ReadContext(),categories=['restaurant'])
    assert len(calls)==1 and status['candidate_count']==60 and status['reason']=='raw_record_cap'


def test_retrieval_configuration_change_requires_a_new_request(public):
    from src.discovery.candidate_pool import snapshot_limits,validate_retrieval_snapshot
    from src.foundation.repository import DomainError
    install(public)
    provider=public.app.state.discovery.public_provider;snapshot=snapshot_limits(provider)
    validate_retrieval_snapshot(snapshot,provider)
    provider.query_limit=2
    with pytest.raises(DomainError,match='후보 조회 설정') as error:
        validate_retrieval_snapshot(snapshot,provider)
    assert error.value.status==409
    validate_retrieval_snapshot({'candidate_pool_limit':100},provider) # historical contract


@pytest.mark.parametrize('key,value', [('candidate_raw_record_limit',401),('candidate_pool_limit',201),('candidate_query_limit',8)])
def test_candidate_server_bounds_cannot_be_raised_by_configuration(public,key,value):
    with pytest.raises(ValueError,match='reviewed server bound'):
        ExpandedPublicDiscovery(public.app.state.db,settings=SimpleNamespace(**{key:value}))
