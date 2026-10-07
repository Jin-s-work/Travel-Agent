"""Normal admin/API canonical review flow. Fake data cannot enable production.

DB access sets fixture admin role and inspects assertions only; canonical identity,
links, policies, collection, recommendations and itinerary use HTTP contracts.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import copy
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from tests.test_foundation_api import service,_trip,_job
from tests.test_review_integration import reviews,rows,FixtureDetector,submit
from tests.test_discovery_foundation import import_pack
from tests.discovery_synthetic import pack,conditions


def canonical(reviews):
    stored=import_pack(reviews)
    response=reviews.client.get('/api/v2/admin/review-external-links')
    assert response.status_code==200,response.text
    pid=stored['places'][0]['place_id']
    p=next(p for p in response.json()['canonical_places'] if p['id']==pid)
    return p,stored

def preview_body(p,**changes):
    return {'canonical_place_id':p['id'],'provider':'fake','external_place_id':'canonical-synthetic-1',
        'name':p['name'],'address':p['address'],'source_url':'https://www.google.com/maps/place/synthetic-canonical',
        'source_id':p['sources'][0]['id'],'latitude':p['latitude'],'longitude':p['longitude'],
        'expected_identity_version':p['version'],'expires_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),
        'evidence':'Synthetic official branch and provider address checked separately',**changes}

def preview(reviews,p,**changes):
    r=reviews.client.post('/api/v2/admin/review-external-links/preview',json=preview_body(p,**changes))
    assert r.status_code==201,r.text
    return r.json()

def approve(reviews,p,value,**changes):
    return reviews.client.post('/api/v2/admin/review-external-links/'+value['id']+'/approve',json={
        'expected_identity_version':p['version'],'decision':'approved','confirm_name':True,'confirm_address':True,
        'confirm_coordinates':True,'confirm_branch':True,**changes})

def contract_body(reviews,**changes):
    now=datetime.now(timezone.utc)
    return {'provider':'fake','build':'synthetic-v1','adapter_version':'fake-review-v1','status':'approved',
        'checked_at':now.isoformat(),'expires_at':(now+timedelta(days=1)).isoformat(),'report_sha256':'a'*64,
        'documentation_urls':['https://example.org/synthetic-contract'],'schema_verified':True,
        'original_separation_verified':True,'original_language_verified':True,
        'language_meaning':'Synthetic original language protocol behavior','sort_basis':'published_at',
        'source_pagination_visible':True,'continuity_verified':True,'internal_limits_verified':True,
        'pagination_scope':'Synthetic source pages have known generated ordering','rights_policy_id':reviews.policy['id'],
        'sample_count':200,'failure_types':[],'reviewer_note':'Synthetic contract only, no real provider validation',
        'synthetic':True,'pricing_checked_at':now.isoformat(),'billing_unit':'synthetic zero cost calls',
        'execution_cap_usd':'0','additional_cost_scope':'No paid calls or actual external collection',**changes}


def test_normal_canonical_link_collect_card_and_itinerary_flow(reviews):
    p,_=canonical(reviews)
    missing=submit(reviews,'before-link',place_id=p['id'])
    assert missing.status_code==409 and missing.json()['error']['code']=='CANONICAL_REVIEW_LINK_REQUIRED'
    approved=approve(reviews,p,preview(reviews,p));assert approved.status_code==200,approved.text
    contract=reviews.client.post('/api/v2/admin/review-provider-contracts',json=contract_body(reviews))
    assert contract.status_code==201 and not contract.json()['strict_ready']
    result=submit(reviews,'canonical-review',place_id=p['id']);assert result.status_code==202,result.text
    assert _job(reviews.client,result.json())['state']=='succeeded'
    run=reviews.client.get(result.json()['status_url']).json()
    assert run['counts']['text_count']==200 and run['counts']['classified_count']==190
    assert run['evaluation']['metrics']['local_share_lower_bound']==.75
    with reviews.app.state.db.connect() as con:
        row=con.execute('SELECT place_id FROM review_collection_runs WHERE id=?',(result.json()['run_id'],)).fetchone()
        assert row['place_id']==p['id']
    trip=_trip(reviews.client);base=f"/api/v2/trips/{trip['id']}"
    saved=reviews.client.patch(base+'/discovery-conditions',json={'expected_version':0,'conditions':conditions()})
    assert saved.status_code==200,saved.text
    job=reviews.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'canonical-recommendation'})
    assert job.status_code==202,job.text
    assert _job(reviews.client,job.json())['state']=='succeeded'
    recommendation=reviews.client.get(base+'/recommendations/'+job.json()['run_id']).json()['result']
    cards=[c for section in recommendation['sections'].values() for key in ('items','needs_confirmation','insufficient_data') for c in section.get(key,[])]
    assert any(c['place_id']==p['id'] for c in cards)
    detail=reviews.client.get(base+'/places/'+p['id']+'/detail');assert detail.status_code==200,detail.text
    assert detail.json()['place']['id']==p['id']
    evidence=reviews.client.get(base+'/places/'+p['id']+'/review-evidence').json()
    assert evidence['dependencies']['external_link_id']==approved.json()['id']
    assert evidence['synthetic'] and not evidence['evaluation']['strict_pass'] and evidence['metrics'] is None
    # The same canonical ID flows into first itinerary preview/apply without ID rewrite.
    generated=reviews.client.post(base+'/itineraries/generation-previews',json={'trip_version':trip['version'],'conditions_version':1,
        'start_date':'2026-11-06','end_date':'2026-11-06','selected':[{'place_id':p['id'],'duration_minutes':60}],
        'allow_provisional':True},headers={'Idempotency-Key':'canonical-itinerary'})
    assert generated.status_code==202,generated.text
    assert _job(reviews.client,generated.json())['state']=='succeeded'
    path=base+'/itineraries/'+generated.json()['itinerary_id']
    proposed=reviews.client.get(path+'/generation-preview').json()
    assert any(i.get('place_id')==p['id'] for i in proposed['result']['items'])
    applied=reviews.client.post(path+'/generation-preview/apply',json={'expected_version':0,'preview_id':proposed['preview_id']})
    assert applied.status_code==200,applied.text
    calls=len(reviews.app.state.reviews.provider.calls)
    for _ in range(3):reviews.client.get(base+'/places/'+p['id']+'/review-evidence')
    assert len(reviews.app.state.reviews.provider.calls)==calls
    controls=reviews.client.get('/api/v2/admin/review-controls').json()
    assert not controls['production_enabled']

@pytest.mark.parametrize('changes,reason',[
    ({'address':'Other branch 99 Tokyo'},'ADDRESS_MISMATCH'),
    ({'latitude':35.0},'COORDINATE_MISMATCH'),
    ({'latitude':None,'longitude':None},'COORDINATES_UNCONFIRMED'),
])
def test_same_name_never_overrides_branch_coordinate_or_address_conflict(reviews,changes,reason):
    p,_=canonical(reviews);value=preview(reviews,p,**changes)
    assert reason in value['evidence']['reason_codes']
    assert approve(reviews,p,value).status_code==409

def test_concurrent_approval_one_winner_and_member_cannot_approve(reviews):
    p,_=canonical(reviews);values=[preview(reviews,p) for _ in range(2)]
    member=reviews.login('member-no-admin')
    assert member.client.post('/api/v2/admin/review-external-links/preview',json=preview_body(p)).status_code==404
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda v:approve(reviews,p,v),values))
    assert sorted(r.status_code for r in responses)==[200,409]

def test_revoke_fences_running_collection_and_only_affected_aggregate(reviews):
    p,stored=canonical(reviews);link=approve(reviews,p,preview(reviews,p)).json()
    entered,release=threading.Event(),threading.Event()
    provider=reviews.app.state.reviews.provider;original=provider.start
    def blocked(request):
        entered.set();assert release.wait(5)
        return original(request)
    provider.start=blocked
    submission=submit(reviews,'fenced-link',place_id=p['id']);assert submission.status_code==202
    assert entered.wait(5)
    revoke=reviews.client.post('/api/v2/admin/review-external-links/'+link['id']+'/revoke');release.set();assert revoke.status_code==200
    assert _job(reviews.client,submission.json())['state']=='failed'
    # Jobs remain durable but cannot activate after their external connection changes.
    svc=reviews.app.state.reviews
    with svc.db.connect() as con:
        row=dict(con.execute('SELECT * FROM review_collection_runs WHERE id=?',(submission.json()['run_id'],)).fetchone())
        with pytest.raises(Exception) as error:svc._run_dependency_guard(con,row)
        assert getattr(error.value,'code',None)=='EXTERNAL_LINK_REVOKED'
        assert con.execute('SELECT active_aggregate_id FROM place_identities WHERE id=?',(p['id'],)).fetchone()[0] is None
        assert con.execute('SELECT status FROM candidate_packs WHERE id=?',(stored['id'],)).fetchone()[0]=='approved'

def test_shared_review_request_is_deduped_without_paid_work(reviews):
    p,_=canonical(reviews);member=reviews.login('request-member')
    trips=[_trip(reviews.client),_trip(member.client)]
    before=len(reviews.app.state.reviews.provider.calls)
    ids=[]
    for c,t in zip((reviews.client,member.client),trips):
        assert c.post(f"/api/v2/trips/{t['id']}/places",json={'place_id':p['id']}).status_code==201
        res=c.post(f"/api/v2/trips/{t['id']}/places/{p['id']}/review-request")
        assert res.status_code==202 and not res.json()['paid_collection_started'];ids.append(res.json()['id'])
    assert ids[0]==ids[1] and len(reviews.app.state.reviews.provider.calls)==before
    assert member.client.get('/api/v2/admin/place-review-requests').status_code==404


def test_contract_runtime_exact_build_expiry_revoke_and_original_flags(reviews):
    from src.providers.apify_reviews import ApifyReviewCollectionProvider
    # Fake contract proves same normal API shape, never Apify runtime inheritance.
    response=reviews.client.post('/api/v2/admin/review-provider-contracts',json=contract_body(reviews));assert response.status_code==201,response.text
    ident=response.json()['id'];svc=reviews.app.state.reviews
    with svc.db.connect() as con:assert svc._runtime_contract(con)['id']==ident
    svc.provider=ApifyReviewCollectionProvider(token='',build='0.0.528')
    with svc.db.connect() as con:assert svc._runtime_contract(con) is None
    assert not svc.provider.contract_verified and svc.provider.capabilities.sort_basis=='unknown'
    assert reviews.client.post('/api/v2/admin/review-provider-contracts/'+ident+'/revoke').status_code==200
    assert reviews.client.get('/api/v2/admin/review-provider-contracts').json()['items'][0]['status']=='revoked'


def test_all_city_capabilities_have_independent_profile_and_off_state(reviews):
    response=reviews.client.get('/api/v2/review-capabilities');assert response.status_code==200
    if os.getenv('GOING_STAGE2_REPORT_DIR'):
        target=Path(os.environ['GOING_STAGE2_REPORT_DIR']);target.mkdir(parents=True,exist_ok=True)
        (target/'stage2-city-language-capabilities.json').write_text(json.dumps({'scope':'normal API synthetic fixture; no live collection; all product gates OFF','response':response.json()},ensure_ascii=False,indent=2)+'\n')
    items={p['city']:p for p in response.json()['items']}
    assert len(items)>=100
    assert items['tokyo']['language_profile']['local_languages']==['ja']
    assert items['barcelona']['language_profile']['local_languages']==['es','ca']
    assert 'OVERLAPPING_LANGUAGE_SETS' in items['seoul']['language_profile']['reason_codes']
    assert all(p['observed_local']['status']=='unavailable' for p in items.values())
    assert all('REVIEW_PRODUCTION_DISABLED' in p['observed_local']['reason_codes'] for p in items.values())

@pytest.mark.parametrize('support',[0,1,49,50])
def test_quality_denominator_policy_is_separate_from_accuracy(reviews,support):
    now=datetime.now(timezone.utc)
    response=reviews.client.post('/api/v2/admin/review-quality-evaluations',json={
        'city':'tokyo','detector_version':'synthetic-detector-v1','domain':'restaurant_reviews','synthetic':True,
        'heldout_disjoint':True,'label_count':100,'original_checks':20,'original_translation_errors':0,
        'local':{'tp':50,'fp':0,'fn':0},'korean':{'tp':support,'fp':0,'fn':0},'report_sha256':'b'*64,
        'evidence_url':'https://example.org/synthetic-quality','expires_at':(now+timedelta(days=1)).isoformat(),
        'provider':'fake','provider_build':'synthetic-v1','adapter_version':'fake-review-v1','category':'restaurant',
        'policy_version':reviews.policy['version'],'quality_policy_version':'review-language-quality-v2',
        'language_profile_version':'city-languages-v1','duplicate_text_count':0,'shared_place_count':0})
    assert response.status_code==201,response.text
    value=response.json();assert not value['passed']
    assert value['support_sufficient']==(support==50)
    assert value['korean_recall']==(1 if support else None)


def test_offline_evaluation_detects_text_and_same_place_leakage_without_persisting_text():
    from src.research.evaluation import evaluate_dataset
    data={'city':'tokyo','synthetic':False,'domain':'restaurant_reviews','development':[{'text':'ja private development original','place_id':'same-place'}],
          'heldout':[{'text':f'{language} private original number {i}','expected':language,'place_id':'same-place' if i==0 else f'p{i}',
                      'original_checked':i<20} for language in ('ja','ko') for i in range(50)]}
    result=evaluate_dataset(data,detector=FixtureDetector())
    assert result['local_precision']==result['korean_recall']==1
    assert result['shared_place_count']==1 and not result['production_strict_gate_supported']
    assert 'private original' not in json.dumps(result)
    data['development']=[{'text':data['heldout'][0]['text'],'place_id':'development-other'}]
    result=evaluate_dataset(data,detector=FixtureDetector())
    assert result['duplicate_text_count']==1 and not result['production_strict_gate_supported']


def test_readonly_budget_preview_and_visible_unsaved_review_request(reviews):
    p,_=canonical(reviews);approve(reviews,p,preview(reviews,p))
    before=len(reviews.app.state.reviews.provider.calls)
    response=reviews.client.post('/api/v2/admin/review-collection-preview',json={'place_id':p['id'],'policy_id':reviews.policy['id']})
    assert response.status_code==200,response.text
    assert not response.json()['reservation_created'] and not response.json()['is_price_quote']
    trip=_trip(reviews.client)
    requested=reviews.client.post(f"/api/v2/trips/{trip['id']}/places/{p['id']}/review-request")
    assert requested.status_code==202,requested.text
    assert not requested.json()['duplicate']
    again=reviews.client.post(f"/api/v2/trips/{trip['id']}/places/{p['id']}/review-request")
    assert again.json()['duplicate']
    assert len(reviews.app.state.reviews.provider.calls)==before


def test_real_runtime_contract_injection_and_build_expiry_do_not_inherit(reviews):
    from src.providers.apify_reviews import ApifyReviewCollectionProvider
    from src.research.service import stamp
    svc=reviews.app.state.reviews
    policy=reviews.client.post('/api/v2/admin/review-policies',json={**reviews.policy_body,'provider':'apify','version':'apify-fixture-contract-policy'})
    assert policy.status_code==201,policy.text
    body=contract_body(reviews,provider='apify',build='0.0.528',adapter_version='apify-compass-v1',synthetic=False,
        rights_policy_id=policy.json()['id'],report_sha256='c'*64)
    # Fixture attestation exercises the runtime handshake, not actual field quality.
    response=reviews.client.post('/api/v2/admin/review-provider-contracts',json=body)
    assert response.status_code==201,response.text
    svc.provider=ApifyReviewCollectionProvider(token='',build='0.0.528')
    with svc.db.connect() as con:assert svc._runtime_contract(con)['id']==response.json()['id']
    assert svc.provider.contract_verified and svc.provider.capabilities.sort_basis=='published_at'
    assert not svc.provider.capabilities.source_pagination_visible and not svc.provider.capabilities.continuity_verified
    assert 'ADAPTER_SOURCE_PAGINATION_UNOBSERVABLE' in response.json()['reason_codes']
    assert not response.json()['strict_ready']
    svc.provider.build='0.0.529'
    with svc.db.connect() as con:assert svc._runtime_contract(con) is None
    assert not svc.provider.contract_verified and not svc.provider.capabilities.continuity_verified
    svc.provider.build='0.0.528';svc.now=lambda:stamp(body['expires_at'])+timedelta(seconds=1)
    with svc.db.connect() as con:assert svc._runtime_contract(con) is None
    assert not svc.provider.contract_verified


def test_mapping_and_contract_tombstones_survive_old_backup_restore(reviews):
    from src.research.maintenance import merge_tombstones
    p,_=canonical(reviews);link=approve(reviews,p,preview(reviews,p)).json()
    contract=reviews.client.post('/api/v2/admin/review-provider-contracts',json=contract_body(reviews)).json()
    submission=submit(reviews,'before-restore',place_id=p['id']);assert _job(reviews.client,submission.json())['state']=='succeeded'
    now=datetime.now(timezone.utc).isoformat()
    result=merge_tombstones(reviews.app.state.db,[{'target_type':kind,'target_id':ident,'reason':'WITHDRAWN_AFTER_BACKUP','requested_at':now,'completed_at':None} for kind,ident in [('external_link',link['id']),('contract',contract['id'])]])
    assert result['applied']==2
    with reviews.app.state.db.connect() as con:
        assert con.execute('SELECT status FROM place_external_links WHERE id=?',(link['id'],)).fetchone()[0]=='revoked'
        assert con.execute('SELECT status FROM review_provider_contracts WHERE id=?',(contract['id'],)).fetchone()[0]=='revoked'
        assert con.execute('SELECT invalidation_reason FROM review_aggregates WHERE run_id=?',(submission.json()['run_id'],)).fetchone()[0]=='RESTORE_TOMBSTONE'


def test_current_quality_checks_model_expiry_city_category_policy_and_support(reviews):
    # Unit-test the release predicate separately from synthetic end-to-end proof.
    # A future provider adapter could expose source pages; current Apify v1 cannot.
    svc=reviews.app.state.reviews
    svc.provider=SimpleNamespace(name='apify',build='0.0.999',adapter_version='test-observable-v2')
    now=svc.now();later=(now+timedelta(days=1)).isoformat()
    evidence=contract_body(reviews,provider='apify',build='0.0.999',adapter_version='test-observable-v2',synthetic=False)
    contract={'id':'contract-unit','provider':'apify','build':'0.0.999','adapter_version':'test-observable-v2','status':'approved',
        'checked_at':now.isoformat(),'expires_at':later,'report_sha256':'d'*64,'evidence_json':json.dumps(evidence)}
    report={'local':{'tp':50,'fp':0,'fn':0},'korean':{'tp':50,'fp':0,'fn':0},'label_count':100,'original_checks':20,
        'quality_policy_version':'review-language-quality-v2','language_profile_version':'city-languages-v1',
        'provider':'apify','provider_build':'0.0.999','adapter_version':'test-observable-v2','category':'restaurant',
        'policy_version':'reviewed-policy','duplicate_text_count':0,'shared_place_count':0}
    row={'detector_version':svc._detector_version(),'expires_at':later,'report_json':json.dumps(report)}
    context={'_contract':contract,'_policy_version':'reviewed-policy','_category':'restaurant'}
    assert svc._quality_matches(row,context)
    assert not svc._quality_matches({**row,'detector_version':'old-model'},context)
    assert not svc._quality_matches({**row,'expires_at':(now-timedelta(seconds=1)).isoformat()},context)
    assert not svc._quality_matches(row,{**context,'_category':'cafe'})
    assert not svc._quality_matches(row,{**context,'_policy_version':'another-policy'})
    assert not svc._quality_matches({**row,'report_json':json.dumps({**report,'korean':{'tp':1,'fp':0,'fn':0}})},context)
