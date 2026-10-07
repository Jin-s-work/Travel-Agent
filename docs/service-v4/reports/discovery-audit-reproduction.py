"""Read-only audit reproduction: temporary fixtures only, no live network."""
import tests.conftest  # Configure isolated paths before importing the app.
from types import SimpleNamespace
from tests.test_foundation_api import service, _job
from tests.test_public_discovery import public, prepare, run, payload
from src.discovery.public_places import normalize, center


def test_public_card_accepted_but_not_scheduled(public):
    trip, base = prepare(public)
    output = run(public, trip, base)
    place = output['result']['sections']['local_discovery']['needs_confirmation'][0]['place_id']
    response = public.client.post(base+'/itineraries', json={
        'trip_version':trip['version'], 'conditions_version':1,
        'recommendation_run_id':output['run_id'],
        'start_date':'2026-11-06', 'end_date':'2026-11-06',
        'selected':[{'place_id':place}], 'allow_provisional':True,
    }, headers={'Idempotency-Key':'audit-public-itinerary'})
    assert response.status_code == 404, response.text
    assert response.json()['error']['code'] == 'NOT_FOUND'
    print('PUBLIC_ITINERARY', {'http_status':response.status_code, 'error':response.json()['error']['code'], 'message':response.json()['error']['message']})


def test_thirteenth_closest_to_hotel_is_lost_before_distance_filter(public):
    trip,base=prepare(public)
    def fake(city):
        public.calls_public.append(city)
        return normalize(payload(city,13), city), 1000
    public.app.state.discovery.public_provider.fetcher=fake
    c=center('paris')
    conditions=public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions['origin']={'label':'Synthetic origin near candidate 13','latitude':c['latitude']+.024,'longitude':c['longitude']}
    conditions['origin_selection']={'kind':'manual'}
    conditions['distance_filter']={'kind':'straight_line','max_distance_m':100}
    response=public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':conditions})
    assert response.status_code==200,response.text
    output=run(public,trip,base,'audit-thirteenth-candidate',conditions_version=2)
    result=output['result']
    with public.app.state.db.connect() as con:
        stored=con.execute("SELECT count(*) FROM research_candidates WHERE status='public_data'").fetchone()[0]
    assert stored==13
    assert result['summary']['displayable_count']==0
    assert len(result['sections']['local_discovery']['excluded'])==12
    print('CAP_BEFORE_DISTANCE', {'stored':stored,'summary':result['summary'],'excluded':len(result['sections']['local_discovery']['excluded'])})


def test_curated_restaurant_blocks_public_cafe_fetch(public):
    from tests.discovery_synthetic import pack
    from tests.test_discovery_foundation import import_pack
    public.admin=SimpleNamespace(id=public.user['id'])
    with public.app.state.db.connect() as con:
        con.execute("UPDATE users SET role='admin' WHERE id=?",(public.user['id'],))
    data=pack('tokyo')
    for p in data['places']:p['category']='restaurant'
    imported=import_pack(public,data)
    with public.app.state.db.connect() as con:
        con.execute('UPDATE candidate_packs SET synthetic=0 WHERE id=?',(imported['id'],))
    trip,base=prepare(public,'tokyo')
    conditions=public.client.get(base+'/discovery-conditions').json()['conditions']
    conditions['categories']=['cafe']
    response=public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':conditions})
    assert response.status_code==200,response.text
    result=run(public,trip,base,'audit-curated-blocks-cafe',conditions_version=2)['result']
    assert public.calls_public==[]
    assert result['summary']['displayable_count']==0
    assert result['public_discovery']['reason']=='REVIEWED_CATALOG_AVAILABLE'
    print('CURATED_CATEGORY_GATE', {'summary':result['summary'],'public_status':result['public_discovery']['reason'],'calls':public.calls_public})


def test_review_identity_not_connectable_to_editorial_catalog(public):
    from datetime import datetime, timedelta, timezone
    from tests.discovery_synthetic import pack
    from tests.test_discovery_foundation import import_pack
    public.admin=SimpleNamespace(id=public.user['id'])
    with public.app.state.db.connect() as con:
        con.execute("UPDATE users SET role='admin' WHERE id=?",(public.user['id'],))
    data=pack('tokyo')
    data['synthetic']=False
    imported=import_pack(public,data)
    editorial=imported['places'][0]['place_id']
    raw=data['places'][0]
    response=public.client.post('/api/v2/admin/review-places', json={
        'provider':'apify','external_place_id':raw['external_id'],'city':'tokyo',
        'name':raw['name'],'address':raw['address'],'source_url':'https://www.google.com/maps/place/synthetic-audit'
    })
    assert response.status_code==201,response.text
    review=response.json()['id']
    assert editorial!=review
    stamp=datetime.now(timezone.utc)
    response=public.client.post('/api/v2/admin/review-policies',json={
        'provider':'apify','version':'audit-only-permission-v1','purpose':'Synthetic isolated audit only',
        'rights':{k:True for k in ('access','collect','calculate','raw_store','aggregate_store','id_store','llm','display')},
        'evidence':['Synthetic audit policy, no actual provider rights claimed'],
        'reviewed_at':(stamp-timedelta(minutes=1)).isoformat(),'expires_at':(stamp+timedelta(days=1)).isoformat(),
        'remote_raw_ttl_seconds':3600,
    })
    assert response.status_code==201,response.text
    response=public.client.post('/api/v2/admin/review-collection-runs',json={
        'place_id':editorial,'policy_id':response.json()['id']},headers={'Idempotency-Key':'audit-editorial-review'})
    assert response.status_code==409,response.text
    assert response.json()['error']['code']=='REVIEW_POLICY_UNAVAILABLE'
    provider=public.app.state.reviews.provider
    assert provider.name=='apify'
    assert provider.contract_verified is False
    assert provider.capabilities.sort_basis=='unknown'
    assert provider.capabilities.continuity_verified is False
    with public.app.state.db.connect() as con:
        count=con.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]
        review_candidate_count=con.execute('SELECT count(*) FROM research_candidates WHERE place_id=?',(review,)).fetchone()[0]
    assert count==0 and review_candidate_count==0
    print('REVIEW_IDENTITY_GATE', {'same_name_address':True,'ids_differ':editorial!=review,
        'editorial_collect_error':response.json()['error']['code'], 'review_id_catalog_membership':review_candidate_count,
        'default_contract_verified':provider.contract_verified,'default_sort_basis':provider.capabilities.sort_basis,
        'default_continuity':provider.capabilities.continuity_verified,'external_calls':count})


def test_default_optional_preferences_and_budget_prevent_ranked_results():
    from tests.test_recommendation_engine import snapshot,catalog,NOW
    from src.recommendations.engine import recommend
    before=snapshot()
    assert all(recommend(before,catalog(),now=NOW)['sections'][kind]['items'] for kind in ('local_discovery','landmark'))
    before['conditions']['budget']=None
    before['conditions']['preferred']['tags']=[]
    out=recommend(before,catalog(),now=NOW)
    for kind in ('local_discovery','landmark'):
        assert out['sections'][kind]['items']==[]
        assert out['sections'][kind]['insufficient_data']
        assert all('price' in i['missing_components'] and 'preference' in i['missing_components'] for i in out['sections'][kind]['insufficient_data'])
    print('OPTIONAL_INPUT_SCORE_GATE', {kind:{'ranked':len(out['sections'][kind]['items']), 'reference':len(out['sections'][kind]['insufficient_data'])} for kind in out['sections']})


def test_one_expired_fact_invalidates_entire_saved_run(public):
    trip,base=prepare(public)
    output=run(public,trip,base)
    assert output['result']['summary']['displayable_count']==2
    with public.app.state.db.connect() as con:
        con.execute("UPDATE place_facts SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=(SELECT id FROM place_facts ORDER BY id LIMIT 1)")
    stale=public.client.get(base+'/recommendations/'+output['run_id']).json()
    assert stale['result'] is None and stale['data_status']=='stale'
    print('SINGLE_FACT_EXPIRY', {'previous_displayable':2,'result':stale['result'],'data_status':stale['data_status'],'public_fetch_calls':len(public.calls_public)})
