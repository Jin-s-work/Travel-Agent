from copy import deepcopy
from tests.test_foundation_api import service
from tests.test_discovery_foundation import discovery
from tests.test_itinerary_api import planning,prepare,submit,done,apply


def test_bounded_plan_b_preview_apply_undo_and_fixed_preservation(planning):
    trip,pack,pid,reserved=prepare(planning)
    original,path=done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    item=next(i for i in original['items'] if i.get('place_id'))
    response=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'closed','basis':'user_report'})
    assert response.status_code==200,response.text
    result=response.json();assert result['alternatives'],result
    assert len(result['alternatives'])<=3
    assert result['situation']['live_queue_minutes'] is None
    assert planning.client.get(path).json()['items']==original['items']
    chosen=result['alternatives'][0]
    changed=apply(planning,path,chosen);assert changed.status_code==200,changed.text
    assert changed.json()['version']==2
    conflict=apply(planning,path,{**chosen,'preview_id':result['alternatives'][-1]['preview_id']})
    if len(result['alternatives'])>1: assert conflict.status_code==409
    undo=planning.client.post(path+'/undo-previews',json={'expected_version':2,'steps':1})
    assert undo.status_code==200,undo.text
    restored=apply(planning,path,undo.json());assert restored.status_code==200,restored.text
    assert next(i for i in restored.json()['items'] if i.get('place_id'))['place_id']==pid


def test_plan_b_cannot_change_booking_lock_past_or_claim_provider(planning):
    trip,pack,pid,reserved=prepare(planning,booking=True)
    result,path=done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    fixed=next(i for i in result['items'] if i.get('booking_id'))
    payload={'expected_version':1,'item_id':fixed['item_id'],'reason':'reservation_failed'}
    r=planning.client.post(path+'/alternatives',json=payload)
    assert r.status_code==200 and not r.json()['alternatives']
    assert 'CANCEL_REVIEW' in r.json()['reason_codes'][0]
    assert planning.client.post(path+'/alternatives',json={**payload,'basis':'provider_verified'}).status_code==422
    item=next(i for i in result['items'] if i.get('place_id'))
    r=planning.client.post(path+'/edit-previews',json={'expected_version':1,'commands':[{'op':'lock','item_id':item['item_id']}]})
    assert r.status_code==200,r.text
    assert apply(planning,path,r.json()).status_code==200
    assert planning.client.post(path+'/alternatives',json={**payload,'expected_version':2,'item_id':item['item_id']}).status_code==409


def test_rain_without_indoor_evidence_returns_empty(planning):
    trip,pack,pid,reserved=prepare(planning)
    result,path=done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    item=next(i for i in result['items'] if i.get('place_id'))
    r=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'rain'})
    assert r.status_code==200,r.text
    assert not r.json()['alternatives'] and r.json()['rejected']


def test_apply_after_start_and_trip_deletion_preserves_original(planning,monkeypatch):
    from datetime import timedelta
    from src.itineraries.intervals import utc
    trip,pack,pid,_=prepare(planning)
    original,path=done(planning,trip,submit(planning,trip,pid,allow_provisional=True))
    item=next(i for i in original['items'] if i.get('place_id'))
    proposed=planning.client.post(path+'/alternatives',json={'expected_version':1,'item_id':item['item_id'],'reason':'long_queue'}).json()['alternatives'][0]
    # Keep the source fact clock stable: exercise the Plan B time fence directly.
    from src.travel_tools.alternatives import check_apply
    from src.foundation.repository import DomainError
    import pytest
    with pytest.raises(DomainError,match='이미 시작'):check_apply(proposed,original,utc(item['start_instant'])+timedelta(seconds=1))
    assert planning.client.get(path).json()['version']==1
    assert planning.client.delete(f"/api/v2/trips/{trip['id']}").status_code==202
    assert apply(planning,path,proposed).status_code==404
