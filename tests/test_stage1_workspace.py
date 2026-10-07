"""Real auth/SQL draft isolation; no paid providers or personal fixtures."""
from concurrent.futures import ThreadPoolExecutor
from tests.test_foundation_api import service, _trip


def test_draft_survives_trip_switch_and_refresh_without_read_writes(service):
    user=service.login('draft-A');a=_trip(user.client);b=_trip(user.client,'B')
    path=f"/api/v2/trips/{a['id']}/workspace-draft"
    assert user.client.get(path).json()['version']==0
    body={'expected_version':0,'context':{'tab':'explore','visit_date':'2026-11-07','scroll_by_tab':{'explore':700}},'conditions_draft':{'filters':{'budget':'invalid preserved'}}}
    saved=user.client.put(path,json=body)
    assert saved.status_code==200,saved.text
    assert saved.json()['version']==1
    assert user.client.get(f"/api/v2/trips/{b['id']}/workspace-draft").json()['version']==0
    assert user.client.get(path).json()==saved.json()
    other=service.login('draft-B')
    for method in ('get','put','delete'):
        response=getattr(other.client,method)(path,**({'json':body} if method=='put' else {}))
        assert response.status_code==404,response.text
    assert user.client.get(path).headers['cache-control']=='private, no-store'


def test_draft_concurrent_cas_expiry_clear_logout_and_deleted_trip(service):
    user=service.login('draft-A');trip=_trip(user.client)
    path=f"/api/v2/trips/{trip['id']}/workspace-draft"
    body={'expected_version':0,'context':{'tab':'itinerary'}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:user.client.put(path,json=body),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]
    with service.app.state.db.connect() as con:
        con.execute("UPDATE workspace_drafts SET expires_at='2000-01-01T00:00:00+00:00'")
    expired=user.client.get(path).json()
    assert expired['expired'] and expired['version']==1 and expired['context']=={}
    assert user.client.put(path,json=body).status_code==409
    assert user.client.put(path,json={**body,'expected_version':1}).status_code==200
    assert user.client.delete(path).status_code==204
    assert user.client.put(path,json={**body,'expected_version':2}).status_code==409
    assert user.client.post('/api/v2/auth/logout').status_code==204
    with service.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM workspace_drafts').fetchone()[0]==0
    new=service.login('draft-A');assert new.client.get(path).json()['version']==0
    assert new.client.put(path,json=body).status_code==200
    assert new.client.delete(f"/api/v2/trips/{trip['id']}").status_code==202
    assert new.client.put(path,json={**body,'expected_version':1}).status_code==404
    with service.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM workspace_drafts').fetchone()[0]==0


def test_draft_rejects_unknown_refs_secrets_and_excessive_payload(service):
    user=service.login('draft-A');trip=_trip(user.client)
    path=f"/api/v2/trips/{trip['id']}/workspace-draft"
    for body,status in [
        ({'selected_places':[{'place_id':'unknown'}]},404),
        ({'owner_id':'client-owned'},422),
        ({'conditions_draft':{'filters':{'token':'secret'}}},422),
        ({'context':{'visit_date':'000000-11-07'}},422),
        ({'context':{'visit_date':'0000-11-07'}},422),
        ({'conditions_draft':{'filters':{'memo':'a'*24001}}},422),
    ]:
        response=user.client.put(path,json={'expected_version':0,**body})
        assert response.status_code==status,response.text


def test_clearing_absent_draft_fences_a_delayed_first_save(service):
    user=service.login('draft-A');trip=_trip(user.client)
    path=f"/api/v2/trips/{trip['id']}/workspace-draft"
    delayed={'expected_version':0,'context':{'tab':'explore'}}
    assert user.client.delete(path).status_code==204
    assert user.client.put(path,json=delayed).status_code==409
    cleared=user.client.get(path).json()
    assert cleared['version']==1 and cleared['expired'] and not cleared['selected_places']


def test_later_deletion_checkpoint_scrubs_a_draft_from_an_older_snapshot(service,tmp_path):
    from src.foundation.repository import utcnow
    from src.operations.backup import _sanitize_deleted
    user=service.login('draft-A');trip=_trip(user.client)
    path=f"/api/v2/trips/{trip['id']}/workspace-draft"
    assert user.client.put(path,json={'expected_version':0,'conditions_draft':{'filters':{'budget':'synthetic private draft'}}}).status_code==200
    # Simulate applying a newer deletion marker to an older snapshot: the live
    # delete API's immediate draft cleanup did not run in this restored copy.
    with service.app.state.db.connect() as con:
        con.execute('UPDATE trips SET deleted_at=? WHERE id=?',(utcnow(),trip['id']))
    _sanitize_deleted(service.app.state.db,tmp_path)
    with service.app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM workspace_drafts WHERE trip_id=?',(trip['id'],)).fetchone()[0]==0
    assert user.client.get(path).status_code==404


def test_discovery_modes_survive_without_marking_conditions_dirty(service):
    user=service.login('mode-owner');trip=_trip(user.client)
    path=f"/api/v2/trips/{trip['id']}/workspace-draft"
    saved=user.client.put(path,json={'expected_version':0,'context':{'tab':'explore','discovery_mode':'landmark'},'conditions_draft':None})
    assert saved.status_code==200,saved.text
    restored=user.client.get(path).json()
    assert restored['context']['discovery_mode']=='landmark'
    assert restored['conditions_draft'] is None
    invalid=user.client.put(path,json={'expected_version':1,'context':{'discovery_mode':'unverified_local'}})
    assert invalid.status_code==422
    assert user.client.get(path).json()['context']['discovery_mode']=='landmark'
