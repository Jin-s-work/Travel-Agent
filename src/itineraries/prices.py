"""Preview-only expense deltas using the same Decimal/inclusion/deposit estimator."""
from decimal import Decimal
from src.product.models import Price
from src.product.prices import estimate
from src.recommendations.engine import Facts

def entries(result,snapshot,candidates,overrides,itinerary_id,now):
    catalog={p['place_id']:p for p in candidates};output=[]
    items=[(i['item_id'],i) for i in result.get('items',[])]
    items += [('leg:'+(leg.get('from_item_id') or 'origin')+':'+(leg.get('to_item_id') or 'end'),{'name':'이동 비용','local_start':leg.get('departure_local')}) for leg in result.get('legs',[]) if not leg.get('filter_only')]
    for key,item in items:
        entry={'key':itinerary_id+':'+key,'item_key':key,'itinerary_id':itinerary_id,'label':'예약 비용' if item.get('booking_id') else item.get('name') or '방문 비용',
            'party':snapshot['conditions']['party'],'price':None,'source':'unknown','source_id':None,'checked_at':None,'override_version':0,'included_by':None,'covers_days':None}
        candidate=catalog.get(item.get('place_id'))
        if candidate:
            visit={**snapshot['conditions']['visit'],'date':(item.get('local_start') or snapshot['conditions']['visit']['date'])[:10]}
            price,facts,_=Facts(candidate,visit,now).get('price')
            if price:
                normalized={k:v for k,v in price.items() if k in Price.model_fields};normalized['tax']=price.get('tax_status',price.get('tax','unknown'))
                try:entry.update(price=Price.model_validate(normalized).model_dump(mode='json'),source='verified_fact',source_id=facts[0]['source_id'],checked_at=facts[0]['checked_at'])
                except ValueError:pass
        if key in overrides:
            value=overrides[key]
            entry.update(covers_days=value.get('days'),price=value['price'],party=value['party'] or entry['party'],included_by=(itinerary_id+':'+value['included_by']) if value['included_by'] else None,
                source='user_entered',source_id=None,checked_at=value.get('updated_at'),override_version=value.get('version',0))
        output.append(entry)
    return output

def price_delta(before,after,snapshot,candidates,overrides,itinerary_id,now):
    left=estimate(entries(before,snapshot,candidates,overrides,itinerary_id,now));right=estimate(entries(after,snapshot,candidates,overrides,itinerary_id,now));by={}
    for currency in sorted(set(left['totals'])|set(right['totals'])):
        a=left['totals'].get(currency);b=right['totals'].get(currency)
        def field(total,name):return total.get(name) if total else '0'
        # Complete ranges allow conservative delta bounds; missing bounds remain null.
        al,au=field(a,'known_lower'),field(a,'upper');bl,bu=field(b,'known_lower'),field(b,'upper')
        by[currency]={'before_known_lower':al,'before_upper':au,'after_known_lower':bl,'after_upper':bu,
            'delta_lower':str(Decimal(bl)-Decimal(au)) if bl is not None and au is not None else None,
            'delta_upper':str(Decimal(bu)-Decimal(al)) if bu is not None and al is not None else None,
            'before_refundable_deposits':field(a,'refundable_deposits'),'after_refundable_deposits':field(b,'refundable_deposits')}
    return {'currencies':by,'before_unknown_items':left['unknown_item_count'],'after_unknown_items':right['unknown_item_count'],
        'before':left,'after':right,'krw_total':None,'scope':'known_components_only','version':'itinerary_price_delta_v1','external_cancellation':False}
