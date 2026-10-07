"""Synthetic frozen route/ranker contracts, not real map or user-quality results."""
from copy import deepcopy
from datetime import timedelta
import json
import subprocess
import sys
import pytest
from pydantic import ValidationError
from src.discovery.models import Conditions
from src.recommendations.engine import recommend, straight_line_distance, endpoint_version, route_candidates, WEIGHTS
from tests.test_recommendation_engine import catalog, snapshot, NOW, all_items, fact, review


def request():
    value=snapshot(movement_version='v2',evaluation_at=NOW.isoformat())
    value['origin_context']={'origin_version':'origin-fixture-1','origin':{'label':'Synthetic hotel','accommodation_id':'stay_fixture','version':1}}
    value['conditions']['prefer_nearby']=True
    return value


def route(row,seconds=1500,**overrides):
    return {'origin_index':0,'destination_index':0,'origin_identity':{'id':'stay_fixture','version':'origin-fixture-1'},
        'destination_identity':{'id':row['place_id'],'version':endpoint_version(row)},'mode':'walking','status':'ok',
        'provider':'synthetic_location','adapter_version':'synthetic_routes_v1','distance_m':1900,'duration_seconds':seconds,
        'checked_at':(NOW-timedelta(minutes=2)).isoformat(),'expires_at':(NOW+timedelta(hours=2)).isoformat(),
        'usage_permission':{'display':True,'durable_storage':True},'accessibility_status':'unknown',**overrides}


@pytest.mark.parametrize('a,b,valid',[(0,0,True),(35,0,True),(90,180,True),(-90,-180,True),(91,0,False),(0,181,False),(float('nan'),0,False),(0,float('inf'),False),(True,0,False),(None,0,False)])
def test_geometry_validates_pairs_preserves_zero(a,b,valid):
    actual=straight_line_distance({'latitude':a,'longitude':b},{'latitude':0,'longitude':0})
    assert (actual is not None)==valid
    if a==0 and b==0:assert actual==0


def test_unrounded_boundary_and_versioned_models():
    row=catalog()[0];value=request();origin=value['conditions']['origin']
    distance=straight_line_distance(origin,row)
    value['conditions']['distance_filter']={'kind':'straight_line','max_distance_m':distance-.000001}
    item=all_items(recommend(value,[row],now=NOW))[row['place_id']]
    assert item['eligibility']=='ineligible'
    assert 'STRAIGHT_LINE_LIMIT_EXCEEDED' in item['reason_codes']
    value['conditions']['distance_filter']['max_distance_m']=distance
    item=all_items(recommend(value,[row],now=NOW))[row['place_id']]
    assert item['eligibility']=='eligible' and item['ranker_version']=='local_editorial_v2'
    assert WEIGHTS['local_editorial_v1']==WEIGHTS['local_editorial_v2']
    assert item['movement']['straight_line_m']==distance
    old=all_items(recommend(snapshot(),[row],now=NOW))[row['place_id']]
    assert old['ranker_version']=='local_editorial_v1'
    assert old['movement']['distance_m']==round(old['movement']['distance_m'],1)


def test_river_short_straight_line_is_not_fifteen_minute_walk():
    row=catalog()[0];value=request();row.update(latitude=35.684,longitude=139.77)
    value['route_evidence']={row['place_id']:route(row)}
    value['conditions']['distance_filter']={'kind':'walking','max_duration_minutes':15}
    result=recommend(value,[row],now=NOW);item=all_items(result)[row['place_id']]
    assert item['movement']['straight_line_m']<500
    assert item['movement']['duration_minutes']==25
    assert item['eligibility']=='ineligible'
    assert result['distance_exclusions']['WALKING_TIME_LIMIT_EXCEEDED']==2
    # A new explicit request may relax the time; no automatic relaxation.
    value['conditions']['distance_filter']['max_duration_minutes']=25
    assert all_items(recommend(value,[row],now=NOW))[row['place_id']]['eligibility']=='eligible'


@pytest.mark.parametrize('changes',[{'status':'unknown','duration_seconds':None,'distance_m':None},
    {'mode':'car'}, {'expires_at':(NOW-timedelta(seconds=1)).isoformat()},
    {'checked_at':(NOW+timedelta(seconds=1)).isoformat()}, {'duration_seconds':float('nan')},
    {'duration_seconds':-1}, {'duration_seconds':0,'distance_m':0}, {'usage_permission':{'display':False}},
    {'origin_identity':{'id':'other-origin','version':'origin-fixture-1'}}])
def test_missing_or_wrong_route_never_zero_and_keeps_straight_line(changes):
    row=catalog()[0];value=request();value['conditions']['distance_filter']={'kind':'walking','max_duration_minutes':15}
    value['route_evidence']={row['place_id']:route(row,**changes)}
    item=all_items(recommend(value,[row],now=NOW))[row['place_id']]
    assert item['movement']['duration_minutes'] is None
    assert item['movement']['straight_line_m']>0
    assert item['eligibility']=='needs_confirmation'
    assert 'WALKING_TIME_UNKNOWN' in item['reason_codes']


def test_accessibility_unknown_route_keeps_place_constraint_separate():
    row=catalog()[0];value=request();value['conditions']['required']['accessibility']=['step_free']
    value['conditions']['distance_filter']={'kind':'walking','max_duration_minutes':30}
    value['route_evidence']={row['place_id']:route(row)}
    item=all_items(recommend(value,[row],now=NOW))[row['place_id']]
    assert 'ROUTE_ACCESSIBILITY_UNKNOWN' in item['movement']['route']['reason_codes']
    assert item['eligibility']=='needs_confirmation'
    value['route_evidence'][row['place_id']]['accessibility_status']='satisfied'
    assert all_items(recommend(value,[row],now=NOW))[row['place_id']]['eligibility']=='eligible'


def test_near_preference_does_not_reward_unknown_or_reweight_missing():
    row=catalog()[0];value=request();value['conditions']['origin']=None
    item=all_items(recommend(value,[row],now=NOW))[row['place_id']]
    assert item['score_components']['movement']['value'] is None and item['score'] is None
    assert sum(x['weight'] for x in item['score_components'].values())==1


def test_prefilter_drops_closed_capacity_and_wrong_category_before_cap():
    rows=catalog();value=request()
    eligible=route_candidates(value,rows,NOW,1)
    assert len(eligible)==1 and eligible[0]['place_id']==rows[0]['place_id']
    filtered=route_candidates(value,list(reversed(rows)),NOW,20)
    assert [r['place_id'] for r in filtered]==[rows[0]['place_id'],rows[1]['place_id']]


def test_strict_review_gate_unchanged_and_no_fake_support():
    rows=catalog();value=request();value['review_language_filter']={'required':True,'apply_to':['local_discovery']}
    for row in rows[:2]:review(row)
    result=recommend(value,rows,now=NOW)
    assert len(result['sections']['local_discovery']['items'])==2
    value['conditions']['city']='seoul';value['conditions']['visit']['timezone']='Asia/Seoul'
    assert not recommend(value,rows,now=NOW)['sections']['local_discovery']['items']


@pytest.mark.parametrize('filters',[{'kind':'walking','max_distance_m':1000},{'kind':'straight_line','max_duration_minutes':15},{'kind':'walking','max_duration_minutes':float('inf')},{'kind':'straight_line','max_distance_m':-1}])
def test_distance_filter_units_never_interchangeable(filters):
    value=request()['conditions'];value['distance_filter']=filters
    with pytest.raises(ValidationError):Conditions.model_validate(value)


def test_frozen_comparison_and_cli_do_not_retrieve(tmp_path):
    from src.product.evaluation import compare
    rows=catalog();value=request();value['route_evidence']={rows[0]['place_id']:route(rows[0],seconds=60)}
    before=deepcopy(value);data=compare(value,rows,'movement-v2')
    assert data['retrieval_calls']==0 and data['language_gate_unchanged'] and value==before
    assert all(item['hard_violations']=={'before':0,'after':0} for item in data['comparison'])
    fixture=tmp_path/'fixture.json';output=tmp_path/'evaluation.json'
    fixture.write_text(json.dumps({'snapshot':value,'candidates':rows}))
    completed=subprocess.run([sys.executable,'-m','src.product.evaluation','--fixture',str(fixture),'--output',str(output)],check=True,capture_output=True,text=True)
    result=json.loads(output.read_text());assert result['observed_user_outcome']=='unmeasured'
    assert result['input_hash']==data['input_hash']
    assert 'actual_micros' not in completed.stdout

# These exercise the real authenticated SQL/job API in an isolated test database.
from tests.test_foundation_api import service, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.test_discovery_intents import trip_for, body, submit
from tests.discovery_synthetic import conditions


def create_stay(discovery,trip):
    payload={'stop_id':trip['stops'][0]['id'],'input_kind':'name','input_value':'Synthetic unknown hotel','checkin_date':trip['start_date'],'checkout_date':trip['end_date']}
    response=discovery.client.post(f"/api/v2/trips/{trip['id']}/accommodations",json=payload)
    assert response.status_code==201,response.text
    return response.json(),payload


def finish(discovery,trip,response):
    assert response.status_code==202,response.text
    assert _job(discovery.client,response.json())['state']=='succeeded'
    value=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{response.json()['run_id']}")
    assert value.status_code==200,value.text
    return value.json()


def test_origin_changes_mark_old_run_stale_without_snapshot_rewrite(discovery):
    trip=trip_for(discovery.client);stay,payload=create_stay(discovery,trip)
    response=submit(discovery.client,trip)
    run=finish(discovery,trip,response);assert run['origin_status']=='current'
    before=deepcopy(run['snapshot']);url=f"/api/v2/trips/{trip['id']}"
    assert run['origin_context']['status']=='unresolved'
    updated=discovery.client.patch(url+'/accommodations/'+stay['id'],json={**payload,'expected_version':1,'display_name':'Synthetic second name'})
    assert updated.status_code==200,updated.text
    read=discovery.client.get(url+'/recommendations/'+run['run_id']).json()
    assert read['snapshot']==before and read['origin_status']=='stale' and read['input_status']=='stale'
    assert read['result']==run['result']
    assert 'ORIGIN_CHANGED' in read['reason_codes']
    assert discovery.client.delete(url+'/accommodations/'+stay['id']+'?expected_version=2').status_code==200
    assert discovery.client.get(url+'/recommendations/'+run['run_id']).json()['origin_status']=='stale'


def test_explicit_other_user_stay_is_404_without_conditions_write(discovery):
    mine=trip_for(discovery.client)
    outsider=discovery.login('stage2-origin-B');foreign=trip_for(outsider.client)
    response=outsider.client.post(f"/api/v2/trips/{foreign['id']}/accommodations",json={'stop_id':foreign['stops'][0]['id'],'input_value':'Other private hotel'})
    assert response.status_code==201,response.text
    supplied=body(mine,overrides={'origin_selection':{'kind':'accommodation','accommodation_id':response.json()['id'],'expected_version':1}})
    assert submit(discovery.client,mine,supplied).status_code==404
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM discovery_conditions WHERE trip_id=?',(mine['id'],)).fetchone()[0]==0


def test_recommendation_matrix_bounded_metered_and_receipt_survives_read(discovery):
    from src.location.providers import FakeRouteProvider
    from src.location.service import MatrixService
    from src.reliability.budget import BudgetPolicy
    stored=import_pack(discovery);trip=trip_for(discovery.client)
    # Explicit dependency injection: synthetic routes are never a production fallback.
    routes={('manual-origin',p['place_id'],'walking'):{'distance_m':700,'duration_seconds':720} for p in stored['places']}
    provider=FakeRouteProvider(routes=routes)
    cfg=deepcopy(BudgetPolicy.for_tests().config)
    cfg['prices']['synthetic_location/matrix']={'currency':'USD','rates_per_million':{'matrix_elements':'1'},'max_units':{'matrix_elements':30}}
    discovery.app.state.budget.policy=BudgetPolicy(cfg)
    discovery.app.state.recommendations.matrix=MatrixService(discovery.app.state.gateway,discovery.app.state.jobs,provider,max_candidates=1)
    overrides=conditions();overrides['distance_filter']={'kind':'walking','max_duration_minutes':15};overrides['prefer_nearby']=True
    response=submit(discovery.client,trip,body(trip,overrides=overrides))
    run=finish(discovery,trip,response)
    assert provider.calls==1
    assert run['snapshot']['route_stats']['candidates']==1
    assert run['snapshot']['route_stats']['matrix_elements']==1
    assert run['snapshot']['route_stats']['provider_calls']==1
    assert len(run['snapshot']['route_evidence'])==1
    one=list(run['snapshot']['route_evidence'])[0]
    row=all_items(run['result'],'reference')[one]
    assert row['movement']['duration_minutes']==12
    assert row['movement']['straight_line_m'] is not None
    assert 'VERIFIED_WALKING_ROUTE' in row['score_components']['movement']['reason_codes']
    assert discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{run['run_id']}").status_code==200
    assert provider.calls==1
    with discovery.app.state.db.connect() as con:
        reservations=con.execute("SELECT state,actual_cost_micros FROM usage_reservations WHERE operation='route_matrix'").fetchall()
        assert len(reservations)==1 and reservations[0]['state']=='settled' and reservations[0]['actual_cost_micros']==1


def test_zero_budget_real_adapter_entry_never_called_and_unknown_keeps_distance(discovery):
    from src.location.providers import FakeRouteProvider
    from src.location.service import MatrixService
    from src.reliability.budget import BudgetPolicy
    import_pack(discovery);trip=trip_for(discovery.client);provider=FakeRouteProvider()
    cfg=deepcopy(BudgetPolicy.for_tests().config)
    cfg['prices']['synthetic_location/matrix']={'currency':'USD','rates_per_million':{'matrix_elements':'1'},'max_units':{'matrix_elements':30}}
    cfg['limits']['USD']={key:0 for key in cfg['limits']['USD']}
    discovery.app.state.budget.policy=BudgetPolicy(cfg)
    discovery.app.state.recommendations.matrix=MatrixService(discovery.app.state.gateway,discovery.app.state.jobs,provider)
    overrides=conditions();overrides['distance_filter']={'kind':'walking','max_duration_minutes':15}
    run=finish(discovery,trip,submit(discovery.client,trip,body(trip,overrides=overrides)))
    assert provider.calls==0 and run['snapshot']['route_stats']['provider_calls']==0
    assert not run['result']['sections']['local_discovery']['items']
    assert any(row['movement']['straight_line_m'] is not None for row in all_items(run['result'],'reference').values())
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]==0


def test_deleted_selected_stay_read_recovers_but_new_write_still_404(discovery):
    trip=trip_for(discovery.client);stay,payload=create_stay(discovery,trip)
    overrides={'origin_selection':{'kind':'accommodation','accommodation_id':stay['id'],'expected_version':1}}
    run=finish(discovery,trip,submit(discovery.client,trip,body(trip,overrides=overrides)))
    root=f"/api/v2/trips/{trip['id']}"
    assert discovery.client.delete(root+'/accommodations/'+stay['id']+'?expected_version=1').status_code==200
    result=discovery.client.get(root+'/discovery-conditions')
    assert result.status_code==200,result.text
    assert result.json()['origin_context']['status']=='unknown'
    assert discovery.client.get(root+'/recommendations/'+run['run_id']).json()['origin_status']=='stale'
    assert submit(discovery.client,trip,body(trip,cv=1,overrides=overrides),key='deleted-stay-new-intent').status_code==404


def test_legacy_conditions_foreign_stay_rejected_before_commit(discovery):
    mine=trip_for(discovery.client);other=discovery.login('legacy-stay-B');foreign=trip_for(other.client)
    created=other.client.post(f"/api/v2/trips/{foreign['id']}/accommodations",json={'stop_id':foreign['stops'][0]['id'],'input_value':'Private other stay'})
    value=conditions();value['origin_selection']={'kind':'accommodation','accommodation_id':created.json()['id']}
    response=discovery.client.patch(f"/api/v2/trips/{mine['id']}/discovery-conditions",json={'expected_version':0,'conditions':value})
    assert response.status_code==404,response.text
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM discovery_conditions WHERE trip_id=?',(mine['id'],)).fetchone()[0]==0


def test_expired_route_dto_hides_time_preserves_sql_frozen_evidence(discovery):
    trip=trip_for(discovery.client);import_pack(discovery)
    value=conditions();response=submit(discovery.client,trip,body(trip,overrides=value));run=finish(discovery,trip,response)
    with discovery.app.state.db.connect() as con:
        row=con.execute('SELECT snapshot_json,result_json FROM recommendation_runs WHERE id=?',(run['run_id'],)).fetchone()
        stored=json.loads(row['result_json']);one=stored['sections']['reference']['items'][0]
        one['movement']['route']={'status':'ok','mode':'walking','distance_m':1000,'duration_seconds':720,'expires_at':'2020-01-01T00:00:00+00:00','checked_at':'2019-12-31T23:00:00+00:00'}
        one['movement'].update(duration_minutes=12,kind='provider',distance_m=1000)
        frozen=json.dumps(stored);con.execute('UPDATE recommendation_runs SET result_json=? WHERE id=?',(frozen,run['run_id']))
    actual=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{run['run_id']}").json()
    assert actual['route_status']=='stale'
    rendered=all_items(actual['result'],'reference')[one['place_id']]['movement']
    assert rendered['duration_minutes'] is None and rendered['route']['duration_seconds'] is None
    assert rendered['straight_line_m'] is not None
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT result_json FROM recommendation_runs WHERE id=?',(run['run_id'],)).fetchone()[0]==frozen


def test_stay_change_while_job_calculates_cannot_activate_old_origin(discovery,monkeypatch):
    from tests.test_recommendation_api import block_engine
    trip=trip_for(discovery.client);stay,payload=create_stay(discovery,trip)
    started,release=block_engine(monkeypatch)
    response=submit(discovery.client,trip)
    try:
        assert response.status_code==202,response.text
        assert started.wait(8)
        changed=discovery.client.patch(f"/api/v2/trips/{trip['id']}/accommodations/{stay['id']}",json={**payload,'expected_version':1,'display_name':'Changed while calculating'})
        assert changed.status_code==200,changed.text
    finally:release.set()
    job=_job(discovery.client,response.json());assert job['state']=='failed' and job['error_code']=='ORIGIN_CHANGED'
    read=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{response.json()['run_id']}").json()
    assert read['result'] is None and read['origin_status']=='stale'
