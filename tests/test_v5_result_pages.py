"""200 candidate places are independent of review-record limits; no live calls."""
from copy import deepcopy
import json
import pytest
from src.recommendations.pages import page,counts,PAGE_SIZE
from src.recommendations.general import _state
from src.recommendations.engine import recommend
from src.recommendations.service import Recommendations
from src.foundation.repository import DomainError
from tests.test_hybrid_recommender import rows,request
from tests.test_recommendation_engine import NOW
from tests.test_recommendation_persistence import setup
from tests.test_discovery_foundation import discovery
from tests.test_foundation_api import service,_job


def result200():
    snapshot={**request('hybrid_v5'),'result_contract':'discovery_result_v1','candidate_pool_limit':200,'trip_version':1,'conditions_version':1}
    template=rows()[0];candidates=[]
    for n in range(200):
        item=deepcopy(template);item.update(place_id=f'p{n:03}',latitude=35.68+n/100000,neighborhood=f'n{n%8}',chain_id=None)
        candidates.append(item)
    candidates=Recommendations._select(snapshot,candidates)
    assert len(candidates)==200
    result=recommend(snapshot,candidates,now=NOW)
    result['candidate_counts']=counts(snapshot,candidates,result)
    return {'run_id':'run-a','state':'succeeded','snapshot':snapshot,'conditions_version':1,'result':result,'input_status':'current','data_status':'current'}


def test_200_unique_stable_pages_no_duplicates_or_reranking():
    run=result200();before=deepcopy(run);seen=[];cursor=None
    for index in range(17):
        response=page(run,'actor-a','trip-a','test-secret',section='landmark',cursor=cursor)
        chunk=[i['place_id'] for g in ('items','needs_confirmation') for i in response['result']['sections']['landmark'][g]]
        assert len(chunk)==(12 if index<16 else 8)
        seen+=chunk;cursor=response['pages']['landmark']['next_cursor']
    assert cursor is None
    assert len(set(seen))==200
    assert run==before
    assert response['counts']['requested_limit']==200
    assert response['counts']['unique_count']==200
    assert response['counts']['fetched_count'] is None  # Do not invent provider acquisition counts.


@pytest.mark.parametrize('change',['actor','trip','run','snapshot','conditions','section','tamper','withdrawal'])
def test_cursor_bound_to_actor_trip_snapshot_and_visibility(change):
    run=result200();cursor=page(run,'a','t','secret',section='landmark')['pages']['landmark']['next_cursor'];actor='a';trip='t';section='landmark'
    if change=='actor':actor='b'
    elif change=='trip':trip='t2'
    elif change=='run':run['run_id']='different'
    elif change=='snapshot':run['snapshot']['conditions']['party']['adults']=5
    elif change=='conditions':run['conditions_version']=2
    elif change=='section':section='reference'
    elif change=='tamper':cursor=cursor[:-1]+('1' if cursor[-1]!='1' else '2')
    else:run['result']['sections']['landmark']['items'].pop()
    with pytest.raises(DomainError) as exc:page(run,actor,trip,'secret',section=section,cursor=cursor)
    assert exc.value.status==409


def test_off_and_unknown_is_preparing_not_quality_failure():
    assert _state(0,['REVIEW_PRODUCTION_DISABLED','PLACE_IDENTITY_UNKNOWN'])=='disabled'
    snapshot={**request('hybrid_v5'),'local_feature_enabled':False}
    assert recommend(snapshot,[],now=NOW)['section_status']['local_discovery']['state']=='disabled'


def test_duplicate_ids_and_known_violations_never_count_as_more_results():
    snapshot={**request('hybrid_v5'),'result_contract':'discovery_result_v1'};places=rows();places.append(deepcopy(places[0]))
    for f in places[1]['facts']:
        if f['field']=='closed':f['value']=True
    result=recommend(snapshot,places,now=NOW)
    visible=[i['place_id'] for g in ('items','needs_confirmation') for i in result['sections']['landmark'][g]]
    assert len(set(visible))==len(visible)
    assert 'p1' not in visible


def test_page_api_owned_deleted_and_read_only(discovery,monkeypatch):
    _,trip,path,payload=setup(discovery)
    created=discovery.client.post(path+'/recommendations',json=payload,headers={'Idempotency-Key':'v5-page-api'}).json();assert _job(discovery.client,created)['state']=='succeeded'
    url=path+'/recommendations/'+created['run_id']+'?page_size=12'
    def fail(*args,**kwargs):raise AssertionError('Read created a job')
    monkeypatch.setattr(discovery.app.state.jobs,'enqueue',fail)
    first=discovery.client.get(url);assert first.status_code==200,first.text
    assert first.json()['job_state']=='succeeded'
    other=discovery.login('v5-cursor-other')
    assert other.client.get(url).status_code==404
    assert discovery.client.get(url+'&cursor=invalid&section=landmark').status_code==409
    with discovery.app.state.db.connect() as con:
        con.execute("UPDATE trips SET deleted_at='2026-10-08' WHERE id=?",(trip['id'],))
    assert discovery.client.get(url).status_code==404


def test_persisted_200_fixture_pages_survive_new_reader_without_jobs(discovery,monkeypatch):
    """Synthetic engine result stored in the real SQL run; not live-source evidence."""
    _,trip,path,payload=setup(discovery)
    receipt=discovery.client.post(path+'/recommendations',json=payload,headers={'Idempotency-Key':'v5-persisted-200'}).json();assert _job(discovery.client,receipt)['state']=='succeeded'
    fixture=result200()
    with discovery.app.state.db.connect() as con:
        con.execute('UPDATE recommendation_runs SET result_json=?,candidates_json=NULL,snapshot_json=? WHERE id=?',
                    (json.dumps(fixture['result']),json.dumps(fixture['snapshot']),receipt['run_id']))
        jobs_before=con.execute('SELECT count(*) FROM jobs').fetchone()[0]
    # Each request creates a fresh read scope/connection; no process-local paging state.
    def forbidden(*args,**kwargs):raise AssertionError('Browsing must not collect or enqueue')
    monkeypatch.setattr(discovery.app.state.jobs,'enqueue',forbidden)
    url=path+'/recommendations/'+receipt['run_id'];ids=[];cursor=None
    for _ in range(17):
        response=discovery.client.get(url,params={'page_size':12,'section':'landmark',**({'cursor':cursor} if cursor else {})})
        assert response.status_code==200,response.text
        output=response.json();ids.extend(i['place_id'] for g in ('items','needs_confirmation') for i in output['result']['sections']['landmark'][g])
        cursor=output['pages']['landmark']['next_cursor']
    assert len(ids)==len(set(ids))==200 and cursor is None
    assert len(discovery.client.get(url).json()['result']['sections']['landmark']['items'])==6
    with discovery.app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM jobs').fetchone()[0]==jobs_before


def test_job_failure_and_budget_are_separate_from_availability_and_freshness():
    result=page({'run_id':'r','state':'failed','snapshot':{},'conditions_version':1,'result':None,'error_code':'BUDGET_EXCEEDED'},'a','t','secret')
    assert result['job_state']=='failed'
    assert result['availability_state']=='budget_exhausted'
    assert result['freshness_state']=='unknown'
    assert result['result_count']==0
    assert result['next_actions']==['read_saved_results','save_place']
