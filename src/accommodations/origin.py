"""Pure departure-point decisions. No geocoding, mutation, current-clock reads, or 0,0 fallback."""
from __future__ import annotations
import hashlib
import json
import math
from datetime import datetime, timezone
from src.destinations import city_key
from src.foundation.models import valid_date, local_to_instant

VERSION='stay_origin_v1'

def coordinates(value):
    lat,lon=value.get('latitude'),value.get('longitude')
    if isinstance(lat,bool) or isinstance(lon,bool): return None
    if not isinstance(lat,(int,float)) or not isinstance(lon,(int,float)): return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180: return None
    return {'latitude':lat,'longitude':lon}

def _utc(value):
    if isinstance(value,datetime): return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)

def resolve_origin(trip,stays,visit,overrides,clock):
    visit=dict(visit);overrides=overrides or {};selection=overrides.get('origin_selection') or {}
    day=visit.get('date');valid_date(day)
    if not day: raise ValueError('방문일이 필요합니다.')
    local=visit.get('local_time');stop_id=visit.get('stop_id');city=visit.get('city')
    stops=trip.get('stops',[])
    selected=[s for s in stops if (s['id']==stop_id if stop_id else (not city or city_key(s['city'])==city_key(city)) and s['start_date']<=day<=s['end_date'])]
    stop=selected[0] if len(selected)==1 else None
    relevant=sorted([s for s in stays if not s.get('deleted_at') and stop and s.get('stop_id')==stop['id']],key=lambda s:s['id'])
    warnings=[];result={'status':'missing','reason_codes':['ACCOMMODATION_NOT_ADDED'],'origin':None,'candidates':[],
        'resolver_version':VERSION,'visit':visit,'overrides':{'origin_selection':selection,**({'origin':overrides['origin']} if 'origin' in overrides else {})},
        'booking_status':'not_inferred','luggage_storage':'unknown','early_checkin':'unknown','hotel_arrival':'unknown'}
    def finish():
        basis={'version':VERSION,'trip_id':trip.get('id'),'trip_version':trip.get('version'),'stop':stop,'visit':visit,
               'selection':selection,'manual':overrides.get('origin'),'stays':[(s['id'],s.get('version'),s.get('deleted_at')) for s in sorted(stays,key=lambda s:s['id'])],
               'status':result['status'],'origin':result['origin'],'reasons':result['reason_codes']}
        result['origin_version']=hashlib.sha256(json.dumps(basis,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()[:32]
        result['warnings']=warnings;return result
    if not trip['start_date']<=day<=trip['end_date']:
        result.update(status='unknown',reason_codes=['VISIT_OUTSIDE_TRIP']);return finish()
    zone=visit.get('timezone') or (stop or {}).get('timezone')
    if local and zone:
        try:local_to_instant(day+'T'+local,zone)
        except ValueError:
            result.update(status='unknown',reason_codes=['VISIT_TIME_AMBIGUOUS']);return finish()
    kind=selection.get('kind')
    if kind=='none':result.update(reason_codes=['ORIGIN_EXPLICITLY_UNSET']);return finish()
    manual=overrides.get('origin')
    if kind=='manual' or not kind and isinstance(manual,dict) and coordinates(manual):
        point=coordinates(manual or {})
        if point:
            result.update(status='ready',reason_codes=['USER_SELECTED_ORIGIN'],origin={**point,'label':(manual or {}).get('label') or '직접 선택한 출발점','place_id':(manual or {}).get('place_id'),'accommodation_id':None,'version':None,'provenance':'user_entered','checked_at':None,'precision':'user_entered','source':'explicit_origin'})
        else:result.update(status='unresolved',reason_codes=['ORIGIN_COORDINATES_UNKNOWN'])
        return finish()
    if not stop or not stop['start_date']<=day<=stop['end_date']:
        result.update(status='unknown',reason_codes=['CITY_STAY_SELECTION_REQUIRED']);return finish()
    if local:
        try: local_to_instant(day+'T'+local,visit.get('timezone') or stop['timezone'])
        except ValueError:
            result.update(status='unknown',reason_codes=['VISIT_TIME_AMBIGUOUS']);return finish()
    candidate_stays=[]
    for stay in relevant:
        start,end=stay.get('checkin_date'),stay.get('checkout_date')
        if start and (start<trip['start_date'] or end and end>trip['end_date']): warnings.append({'code':'STAY_OUTSIDE_TRIP','accommodation_id':stay['id']})
        if not start or not end:
            continue
        temporal='during_stay' if start<=day<end else 'checkout_before' if day==end else None
        if temporal=='checkout_before' and local and stay.get('checkout_time') and local>=stay['checkout_time']:continue
        if temporal=='during_stay' and day==start and local and stay.get('checkin_time') and local<stay['checkin_time']:
            continue
        if temporal:
            item={**stay,'origin_role':temporal}
            if temporal=='checkout_before' and not (local and stay.get('checkout_time')):item['temporal_unknown']=True
            candidate_stays.append(item)
    result['candidates']=[{'accommodation_id':s['id'],'version':s['version'],'display_name':s['display_name'],'identity_state':s['identity_state'],'origin_role':s['origin_role'],'dates_confirmed':bool(s.get('dates_confirmed'))} for s in candidate_stays]
    if kind=='accommodation':
        matching=[s for s in candidate_stays if s['id']==selection.get('accommodation_id')]
        if not matching:
            result.update(status='unknown',reason_codes=['SELECTED_ACCOMMODATION_NOT_APPLICABLE']);return finish()
        chosen=matching[0]
        if selection.get('expected_version') is not None and selection['expected_version']!=chosen['version']:
            result.update(status='unknown',reason_codes=['ORIGIN_VERSION_CHANGED']);return finish()
        # An explicit visit departure choice is not a claim of an all-day hotel stay.
        chosen={**chosen,'temporal_unknown':False}
    elif len(candidate_stays)>1:
        result.update(status='ambiguous',reason_codes=['ACCOMMODATION_OVERLAP']);return finish()
    elif candidate_stays: chosen=candidate_stays[0]
    elif relevant:
        result.update(status='unknown',reason_codes=['STAY_DATES_UNKNOWN' if any(not s.get('checkin_date') or not s.get('checkout_date') for s in relevant) else 'ACCOMMODATION_GAP']);return finish()
    else:return finish()
    result['label']=chosen['display_name'];result['selected_accommodation']={'id':chosen['id'],'version':chosen['version']}
    identity=chosen.get('identity') or {};point=coordinates(identity)
    if chosen['identity_state']!='confirmed' or not point:
        result.update(status='unresolved',reason_codes=['ACCOMMODATION_LOCATION_UNKNOWN']);return finish()
    if identity.get('expires_at') and _utc(identity['expires_at'])<=_utc(clock):
        result.update(status='unknown',reason_codes=['ACCOMMODATION_COORDINATES_EXPIRED']);return finish()
    if chosen.get('temporal_unknown'):
        result.update(status='unknown',reason_codes=['CHECKOUT_TIME_OR_SELECTION_REQUIRED']);return finish()
    result.update(status='ready',reason_codes=['USER_SELECTED_ACCOMMODATION' if kind=='accommodation' else 'SINGLE_CONFIRMED_ACCOMMODATION'],origin={**point,
        'accommodation_id':chosen['id'],'version':chosen['version'],'label':chosen['display_name'],'place_id':identity.get('provider_place_id'),
        'source':identity.get('provider'),'provenance':identity.get('provenance','provider_candidate_selected'),
        'checked_at':identity.get('checked_at'),'expires_at':identity.get('expires_at'),'precision':identity.get('precision'),
        'origin_role':chosen['origin_role'],'dates_status':'user_confirmed' if chosen.get('dates_confirmed') else 'suggested',
        'usage_permission':identity.get('usage_permission'),'coordinate_version':identity.get('coordinate_version'),'coordinate_permitted':identity.get('coordinate_permitted',False)})
    return finish()


def context_in_connection(con,repo,actor,trip_id,visit,overrides=None,clock=None,*,allow_deleted_selection=False):
    """Owner-scoped read suitable for the recommendation submission transaction."""
    from .service import dto
    trip=repo._trip_dto(con,repo._trip(con,actor.id,trip_id))
    clock=clock or datetime.now(timezone.utc).isoformat()
    if visit.get('stop_id') and not any(s['id']==visit['stop_id'] for s in trip['stops']):
        from src.foundation.repository import DomainError
        raise DomainError('NOT_FOUND','도시 구간을 찾을 수 없습니다.',404)
    selection=(overrides or {}).get('origin_selection') or {}
    if selection.get('accommodation_id'):
        found=con.execute('SELECT 1 FROM trip_accommodations WHERE id=? AND owner_id=? AND trip_id=? AND deleted_at IS NULL',(selection['accommodation_id'],actor.id,trip_id)).fetchone()
        if not found and allow_deleted_selection:
            found=con.execute('SELECT 1 FROM trip_accommodations WHERE id=? AND owner_id=? AND trip_id=?',(selection['accommodation_id'],actor.id,trip_id)).fetchone()
        if not found:
            from src.foundation.repository import DomainError
            raise DomainError('NOT_FOUND','숙소를 찾을 수 없습니다.',404)
    stays=[dto(r,clock) for r in con.execute('SELECT * FROM trip_accommodations WHERE trip_id=? AND owner_id=? ORDER BY id',(trip_id,actor.id))]
    return resolve_origin(trip,stays,visit,overrides or {},clock)


def snapshot_is_current(con,repo,actor,trip_id,snapshot,clock=None):
    from src.foundation.repository import DomainError
    try:
        current=context_in_connection(con,repo,actor,trip_id,snapshot['visit'],snapshot.get('overrides') or {},clock)
        return current['origin_version']==snapshot.get('origin_version')
    except (DomainError,KeyError,ValueError):return False
