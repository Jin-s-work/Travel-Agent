"""Pure Decimal estimates. Unknown components never become a zero-priced item."""
from decimal import Decimal,ROUND_HALF_UP
from .models import Price


def estimate(entries):
    by={e['key']:e for e in entries};results=[];totals={}
    for entry in entries:
        result={k:entry.get(k) for k in ('key','label','source','source_id','checked_at','itinerary_id','override_version')}
        result.update(lower=None,upper=None,refundable_deposit=None,prepaid=None,currency=None,reasons=[],included_by=entry.get('included_by'))
        parent=entry.get('included_by')
        if parent:
            visited={entry['key']};current=entry;valid=True
            while current.get('included_by'):
                key=current['included_by']
                if key in visited or key not in by:valid=False;break
                visited.add(key);current=by[key]
            if valid and current.get('price'):
                result.update(state='included',currency=current['price'].get('currency'));results.append(result);continue
            result['reasons'].append('INCLUSION_UNCONFIRMED')
        try:p=Price.model_validate(entry['price']) if entry.get('price') and not parent else None
        except ValueError:p=None;result['reasons'].append('PRICE_STRUCTURE_UNCONFIRMED')
        if p is None:
            result.update(state='unknown');result['reasons'].append('PRICE_UNKNOWN');results.append(result);continue
        unit=Decimal('1') if p.currency=='JPY' else Decimal('.01')
        def money(v):return str(v.quantize(unit,rounding=ROUND_HALF_UP)) if v is not None else None
        result['currency']=p.currency;party=entry.get('party')
        lo,hi=p.amount_min,p.amount_max
        if not party:lo=hi=None;result['reasons'].append('PARTY_UNKNOWN')
        elif (p.min_party and party['adults']+len(party['children'])<p.min_party) or (p.max_party and party['adults']+len(party['children'])>p.max_party):
            lo=hi=None;result['reasons'].append('PARTY_OUTSIDE_PRICE_RANGE')
        elif p.basis=='per_person':
            lo=lo*party['adults'] if lo is not None else None;hi=hi*party['adults'] if hi is not None else None
            for child in party['children']:
                if p.children_same_price is True:a,b=p.amount_min,p.amount_max
                else:
                    rate=next((r for r in p.child_rates if child.get('age') is not None and r.min_age<=child['age']<=r.max_age),None)
                    a,b=(rate.amount_min,rate.amount_max) if rate else (None,None)
                    if not rate:result['reasons'].append('CHILD_PRICE_UNKNOWN')
                # A missing child rate does not erase a known adult subtotal. It is a
                # conservative lower bound only, never a claim that the child is free.
                lo=lo+(a or Decimal(0)) if lo is not None else None;hi=hi+b if hi is not None and b is not None else None
        if p.period=='day' and entry.get('covers_days') is None:
            lo=hi=None;result['reasons'].append('DURATION_UNKNOWN')
        elif p.period=='day':
            lo=lo*entry['covers_days'] if lo is not None else None;hi=hi*entry['covers_days'] if hi is not None else None
        if p.tax!='included':
            if p.tax=='excluded' and p.tax_rate is not None:
                lo=lo*(1+p.tax_rate) if lo is not None else None;hi=hi*(1+p.tax_rate) if hi is not None else None
            else:hi=None;result['reasons'].append('TAX_UNKNOWN')
        if p.fees_known:
            lo=lo+(p.fee_min or Decimal(0)) if lo is not None else None
            hi=hi+(p.fee_max or Decimal(0)) if hi is not None else None
            if (p.fee_min is None)!=(p.fee_max is None):hi=None;result['reasons'].append('FEE_RANGE_UNKNOWN')
        else:hi=None;result['reasons'].append('FEES_UNKNOWN')
        refundable=Decimal(0)
        if p.deposit:
            if p.deposit.kind=='additional_fee':
                lo=lo+p.deposit.amount if lo is not None else None;hi=hi+p.deposit.amount if hi is not None else None
            elif p.deposit.kind=='refundable':refundable=p.deposit.amount
            # part_payment is a payment schedule, not an additional charge.
        result.update(state='known' if lo is not None and hi is not None else 'partial' if lo is not None else 'unknown',lower=money(lo),upper=money(hi),refundable_deposit=money(refundable),prepaid=money(p.prepaid),
            remaining_cash_lower=money(max(Decimal(0),lo-p.prepaid)+refundable) if lo is not None else None,
            remaining_cash_upper=money(max(Decimal(0),hi-p.prepaid)+refundable) if hi is not None else None,
            cancellation_possible=money(p.cancellation_fee),basis=p.basis,tax=p.tax,deposit_kind=p.deposit.kind if p.deposit else None)
        t=totals.setdefault(p.currency,{'known_lower':Decimal(0),'known_upper':Decimal(0),'upper_complete':True,'known_items':0,'unknown_items':0,'refundable_deposits':Decimal(0),'prepaid':Decimal(0),'remaining_cash_lower':Decimal(0),'remaining_cash_upper':Decimal(0)})
        if lo is not None:t['known_lower']+=Decimal(result['lower']);t['known_items']+=1
        else:t['unknown_items']+=1
        if hi is None:t['upper_complete']=False
        else:t['known_upper']+=Decimal(result['upper'])
        if lo is not None and hi is None:t['unknown_items']+=1
        t['refundable_deposits']+=refundable;t['prepaid']+=p.prepaid
        if result['remaining_cash_lower'] is not None:t['remaining_cash_lower']+=Decimal(result['remaining_cash_lower'])
        if result['remaining_cash_upper'] is not None:t['remaining_cash_upper']+=Decimal(result['remaining_cash_upper'])
        results.append(result)
    for currency,t in totals.items():
        t['upper']=str(t['known_upper']) if t['upper_complete'] else None
        t['remaining_cash_upper']=str(t['remaining_cash_upper']) if t['upper_complete'] else None
        if t['known_items']==0:
            t['known_lower']=None;t['remaining_cash_lower']=None
        for key,value in list(t.items()):
            if isinstance(value,Decimal):t[key]=str(value)
    return {'items':results,'totals':totals,'unknown_item_count':sum(r['state'] in ('unknown','partial') for r in results),'included_item_count':sum(r['state']=='included' for r in results),
        'krw_total':None,'exchange_rate':None,'scope':'known_components_only','version':'expense_decimal_v1','cancellation_fees_in_total':False}
