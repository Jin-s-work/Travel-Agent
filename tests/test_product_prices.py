from copy import deepcopy
from decimal import Decimal
import pytest
from src.product.prices import estimate
from src.product.models import Price

P={'currency':'JPY','basis':'per_person','amount_min':'1000','amount_max':'1500','tax':'included','fees_known':True}
PARTY={'adults':2,'children':[]}
def entry(key='a',price=P,**kw):return {'key':key,'label':key,'price':deepcopy(price),'party':deepcopy(PARTY),**kw}

def test_zero_unknown_currency_and_partial_range():
    r=estimate([entry('free',{**P,'amount_min':'0','amount_max':'0'}),entry('unknown',None),entry('euro',{**P,'currency':'EUR','amount_min':'2.20','amount_max':'3.10'})])
    assert r['totals']['JPY']['known_lower']=='0' and r['items'][0]['state']=='known'
    assert r['unknown_item_count']==1 and r['krw_total'] is None
    assert Decimal(r['totals']['EUR']['upper'])==Decimal('6.20')
    assert estimate([entry(price={**P,'amount_max':None})])['totals']['JPY']['upper'] is None
    missing=estimate([entry(price={**P,'amount_min':None,'amount_max':None})])
    assert missing['totals']['JPY']['known_lower'] is None and missing['totals']['JPY']['upper'] is None

@pytest.mark.parametrize('kind,total,cash',[('part_payment','2000','2000'),('additional_fee','2300','2300'),('refundable','2000','2300')])
def test_deposit_not_double_counted(kind,total,cash):
    r=estimate([entry(price={**P,'amount_max':'1000','deposit':{'kind':kind,'amount':'300'}})])
    assert r['items'][0]['lower']==total and r['items'][0]['remaining_cash_lower']==cash

def test_children_included_tax_prepaid_and_fee():
    adult=entry(price={**P,'amount_max':'1000','children_same_price':False,'child_rates':[{'min_age':0,'max_age':10,'amount_min':'500','amount_max':'500'}],'tax':'excluded','tax_rate':'.10','fee_min':'100','fee_max':'100','prepaid':'800','cancellation_fee':'500'},party={'adults':2,'children':[{'age':8}]})
    r=estimate([adult,entry('meal',included_by='a')]);assert r['items'][0]['lower']=='2850'
    assert r['items'][0]['remaining_cash_lower']=='2050' and r['included_item_count']==1
    assert Decimal(r['totals']['JPY']['known_lower'])==2850
    adult['party']['children'][0]['age']=None
    unknown=estimate([adult])['items'][0]
    assert unknown['state']=='partial' and unknown['lower']=='2300' and unknown['upper'] is None

def test_unknown_tax_fee_and_invalid_currency_precision():
    r=estimate([entry(price={**P,'tax':'unknown'})]);assert r['totals']['JPY']['upper'] is None
    assert 'TAX_UNKNOWN' in r['items'][0]['reasons']
    with pytest.raises(ValueError):Price.model_validate({**P,'amount_min':'1.10'})
    r=estimate([entry('a',included_by='b'),entry('b',included_by='a')]);assert r['unknown_item_count']==2

def test_group_age_and_currency_rounding():
    r=estimate([entry(price={**P,'currency':'EUR','basis':'group','amount_min':'10.01','amount_max':'10.01','tax':'excluded','tax_rate':'.05'},party={'adults':2,'children':[{'age':None}]})])
    assert r['items'][0]['lower']=='10.51'
