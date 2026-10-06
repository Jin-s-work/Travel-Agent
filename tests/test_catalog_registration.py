from copy import deepcopy
from types import SimpleNamespace
import pytest
from src.discovery.catalog_tools import inspect_pack,register_reviewed_pack
from src.foundation.repository import DomainError
from tests.test_foundation_api import service,_trip
from tests.test_discovery_foundation import discovery
from tests.discovery_synthetic import pack


def actual_shape():
    value=pack();value['synthetic']=False
    # Contract exercise only: names and example.org remain visibly synthetic.
    value['version']='test-operator-contract';value['places']=value['places'][:2]
    for place in value['places']:
        for source in place['sources']:source['source_type']='official'
    return value


def actor(discovery):
    with discovery.app.state.db.connect() as con:
        session=con.execute('SELECT id FROM sessions WHERE user_id=?',(discovery.admin.user['id'],)).fetchone()
    return SimpleNamespace(id=discovery.admin.user['id'],session_id=session['id'])


def test_registration_requires_review_then_is_idempotent_without_new_versions(discovery):
    svc=discovery.app.state.discovery;admin=actor(discovery);data=actual_shape()
    first=register_reviewed_pack(svc,admin,data)
    assert first['status']=='needs_review' and svc.availability('tokyo')['real_reviewed_candidates']==0
    approved=register_reviewed_pack(svc,admin,data,review_evidence='Contract fixture source and branch review, no live evidence claim.')
    before=svc.list_packs(admin)
    replay=register_reviewed_pack(svc,admin,data,review_evidence='Contract fixture source and branch review, no live evidence claim.')
    assert replay['duplicate'] and replay['status']=='approved' and replay['approved_sources']==0
    assert before==svc.list_packs(admin)
    assert approved['pack_id']==first['pack_id'] and svc.availability('tokyo')['real_reviewed_candidates']==2
    changed=deepcopy(data);changed['places'][0]['name']='Changed payload same version'
    with pytest.raises(DomainError) as error:register_reviewed_pack(svc,admin,changed)
    assert error.value.code=='PACK_VERSION_CONFLICT'
    with discovery.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM usage_ledger').fetchone()[0]==0


def test_revoked_source_and_disabled_pack_cannot_be_reactivated(discovery):
    svc=discovery.app.state.discovery;admin=actor(discovery);data=actual_shape()
    first=register_reviewed_pack(svc,admin,data,review_evidence='Contract review of public identity and limited factual snippets.')
    source=svc.list_packs(admin)[0]['places'][0]['sources'][0]
    svc.review_source(admin,source['id'],{'expected_version':source['version'],'status':'revoked','read_confirmed':True,'display_permitted':False,'policy_version':data['version'],'evidence':'Test removal requested for source.'})
    with pytest.raises(DomainError) as error:register_reviewed_pack(svc,admin,data,review_evidence='Contract review of public identity and limited factual snippets.')
    assert error.value.code=='SOURCE_REVOKED'
    svc.approve_pack(admin,first['pack_id'],{'status':'disabled','evidence':'Contract test intentionally disables this catalog.'})
    with pytest.raises(DomainError) as error:register_reviewed_pack(svc,admin,data,review_evidence='Contract review of public identity and limited factual snippets.')
    assert error.value.code=='PACK_DISABLED'


def test_import_tool_rejects_fake_duplicate_and_expired_input():
    with pytest.raises(DomainError):inspect_pack(pack())
    value=actual_shape();value['places'].append(deepcopy(value['places'][0]))
    with pytest.raises(DomainError) as error:inspect_pack(value)
    assert error.value.code=='DUPLICATE_BRANCH'
    value=actual_shape();value['places'][0]['facts'][0].update(checked_at='2020-01-01T00:00:00+00:00',expires_at='2021-01-01T00:00:00+00:00')
    with pytest.raises(DomainError) as error:inspect_pack(value)
    assert error.value.code=='FACT_STALE'


def test_non_admin_existing_session_cannot_import(discovery):
    other=discovery.login('catalog-nonadmin')
    with discovery.app.state.db.connect() as con:session=con.execute('SELECT id FROM sessions WHERE user_id=?',(other.user['id'],)).fetchone()
    with pytest.raises(DomainError) as error:register_reviewed_pack(discovery.app.state.discovery,SimpleNamespace(id=other.user['id'],session_id=session['id']),actual_shape())
    assert error.value.status==404


@pytest.mark.parametrize('city,zone',[('tokyo','Asia/Tokyo'),('barcelona','Europe/Madrid'),('madrid','Europe/Madrid')])
def test_official_minimal_facts_produce_honest_confirmation_cards_without_paid_calls(discovery,city,zone):
    import json
    from pathlib import Path
    from tests.test_foundation_api import _job
    collection=json.loads((Path(__file__).parents[1]/'docs/service-v3/data/official-restaurants-2026-10-06.json').read_text())
    # Time-shifted contract fixture, not evidence of current real-world freshness.
    # Change only the in-memory copy; the dated, operator-reviewed JSON and its
    # expiry remain immutable. This keeps future CI runs independent of its TTL.
    from datetime import datetime,timedelta,timezone
    data=deepcopy(next(p for p in collection['packs'] if p['city']==city))
    test_now=datetime.now(timezone.utc)
    for place in data['places']:
        for source in place['sources']:source['checked_at']=test_now.isoformat()
        for fact in place['facts']:
            fact['checked_at']=test_now.isoformat()
            fact['expires_at']=(test_now+timedelta(days=7)).isoformat()
    svc=discovery.app.state.discovery
    register_reviewed_pack(svc,actor(discovery),data,review_evidence='Time-shifted contract fixture only; does not verify current source freshness or live venue conditions.')
    response=discovery.client.post('/api/v2/trips',json={'title':'Catalog contract '+city,'start_date':'2026-11-06','end_date':'2026-11-09','stops':[{'sequence':1,'city':city,'start_date':'2026-11-06','end_date':'2026-11-09','timezone':zone}]})
    assert response.status_code==201,response.text
    trip=response.json();base='/api/v2/trips/'+trip['id']
    receipt=discovery.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':0},headers={'Idempotency-Key':'minimal-real-catalog-'+city})
    assert receipt.status_code==202,receipt.text
    job=_job(discovery.client,receipt.json());assert job['state']=='succeeded',job
    run=discovery.client.get(base+'/recommendations/'+receipt.json()['run_id']).json()
    summary=run['result']['summary']
    assert summary['catalog_count']==summary['displayable_count']==3
    assert summary['qualified_count']==0 and summary['state']=='needs_confirmation'
    for section in run['result']['sections'].values():
        assert section['items']==[]
        for item in section['needs_confirmation']:
            assert item['score'] is None and item['synthetic'] is False
            assert item['movement']['distance_m'] is None
            assert 'LIVE_AVAILABILITY_NOT_CONFIRMED' in item['important_unknowns']
    assert run['result']['applied_constraints']['rating_filter']['enabled'] is True
    sse=discovery.client.get('/api/v2/jobs/'+job['id']+'/events').text
    progress=[json.loads(line[6:]) for line in sse.splitlines() if line.startswith('data: ') and '"stage"' in line]
    stages=list(dict.fromkeys(p['stage'] for p in progress if p.get('stage') in {'candidate_snapshot','route_snapshot','constraints_and_scoring','source_revalidation','recommendation_complete'}))
    assert stages==['candidate_snapshot','route_snapshot','constraints_and_scoring','source_revalidation','recommendation_complete']
    assert any(p.get('stage')=='constraints_and_scoring' and p.get('done')==p.get('total')==3 for p in progress)
    assert discovery.calls==[]
    with discovery.app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM usage_ledger').fetchone()[0]==0
