from tests.test_foundation_api import service
from tests.test_discovery_foundation import discovery
from tests.test_itinerary_api import planning,prepare,submit,done


def test_expense_owned_versions_and_inclusions(planning):
    trip,pack,place,_=prepare(planning);plan,path=done(planning,trip,submit(planning,trip,place))
    base=f"/api/v2/trips/{trip['id']}"
    initial=planning.client.get(base+'/cost-estimate');assert initial.status_code==200,initial.text
    items=initial.json()['items'];assert items
    item=next(i for i in items if not i['item_key'].startswith('leg:'))
    price={'currency':'JPY','basis':'group','amount_min':'0','amount_max':'0','tax':'included','fees_known':True}
    body={'expected_version':0,'itinerary_version':plan['version'],'itinerary_id':plan['id'],'item_key':item['item_key'],'price':price}
    r=planning.client.put(base+'/cost-estimate',json=body);assert r.status_code==200,r.text
    assert r.json()['totals']['JPY']['known_lower']=='0'
    assert planning.client.put(base+'/cost-estimate',json=body).status_code==409
    b=planning.login('other');assert b.client.put(base+'/cost-estimate',json=body).status_code==404
    assert planning.client.put(base+'/cost-estimate',json={**body,'expected_version':1,'included_by':item['item_key'],'price':None}).status_code==422
    leg=next((i for i in items if i['item_key'].startswith('leg:')),None)
    if leg:
        body2={**body,'item_key':leg['item_key'],'price':None,'included_by':item['item_key']}
        saved=planning.client.put(base+'/cost-estimate',json=body2);assert saved.status_code==200,saved.text
        assert saved.json()['included_item_count']==1
        assert planning.client.put(base+'/cost-estimate',json={**body,'expected_version':1,'price':None,'included_by':leg['item_key']}).status_code==422
