"""Pure daily/departure-specific accommodation origin selection for one itinerary snapshot."""
from copy import deepcopy
from src.accommodations.origin import resolve_origin

def origin_context(snapshot,day,local_time=None):
    source=snapshot.get('origin_resolution_input')
    if source:
        visit={'date':day,'local_time':local_time,'stop_id':snapshot.get('stop_id'),'city':snapshot['city'],'timezone':snapshot['timezone']}
        return resolve_origin(source['trip'],source['stays'],visit,source['overrides'],snapshot['computed_at'])
    return deepcopy((snapshot.get('origin_contexts') or {}).get(day))

def origin_for(snapshot,day,local_time=None):
    context=origin_context(snapshot,day,local_time)
    if context is not None:
        value=deepcopy(context.get('origin') or {})
        value.setdefault('label',context.get('label') or '출발점 미확인')
        value.setdefault('latitude',None);value.setdefault('longitude',None)
        value['id']=value.get('accommodation_id') or 'origin'
        value['origin_version']=context['origin_version']
        value['coordinate_permitted']=context['status']=='ready' and value.get('latitude') is not None and value.get('longitude') is not None
        value['origin_status']=context['status'];value['origin_reason_codes']=context['reason_codes']
        return value
    return deepcopy(snapshot.get('origin') or (snapshot.get('conditions') or {}).get('origin') or {})
