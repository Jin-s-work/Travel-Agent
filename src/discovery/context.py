"""Sparse personal overrides and inherited travel context. No network or writes."""
from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import hashlib
from pydantic import ValidationError
from src.destinations import CITIES, city_key
from src.foundation.repository import DomainError
from .models import Conditions

VERSION = 'inherited_context_v2'


def merge(base, overrides):
    result = deepcopy(base)
    for key, value in overrides.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


def load(con, trip_id, saved):
    row = con.execute('SELECT * FROM discovery_contexts WHERE trip_id=?', (trip_id,)).fetchone()
    if row:
        return json.loads(row['overrides_json']), row['stop_id'], json.loads(row['basis_json'])
    # Explicit legacy values remain explicit. Never infer that an old value was a default.
    if saved:
        return json.loads(saved['conditions_json']), None, {'legacy_trip': json.loads(saved['snapshot_json'])}
    return {}, None, {}


def flatten(value, prefix=''):
    result = {}
    for key, item in value.items():
        path = prefix + key
        if isinstance(item, dict): result.update(flatten(item, path + '.'))
        else: result[path] = item
    return result


def selection(trip, overrides, stop_id=None, basis=None, clock=None):
    """Shared inclusive stop-date contract for reads, previews and submission."""
    current = clock or datetime.now(timezone.utc)
    if current.tzinfo is None: raise ValueError('Context clock must include a timezone')
    stops = trip['stops']
    explicit = (overrides.get('visit') or {}).get('date')
    if explicit is not None:
        from src.foundation.models import valid_date
        try: valid_date(explicit)
        except (ValueError, TypeError):
            raise DomainError('VALIDATION_FAILED','유효한 방문일을 YYYY-MM-DD로 입력해 주세요.',422,
                [{'field':'visit.date','message':'네 자리 연도와 실제 달력 날짜를 입력해 주세요.'}])
    desired = overrides.get('city')
    chosen = next((s for s in stops if s['id'] == stop_id), None)
    errors = []
    if stop_id and not chosen:
        errors.append({'field':'stop_id','code':'STOP_REMOVED','message':'선택한 도시 구간이 삭제되었어요. 도시 구간을 다시 골라 주세요.'})
    local_days = [(s, current.astimezone(ZoneInfo(s['timezone'])).date().isoformat()) for s in stops]
    ongoing = [(s, day) for s, day in local_days if s['start_date'] <= day <= s['end_date']]
    if explicit:
        day, reason = explicit, 'user_selected'
    elif ongoing:
        _, day = next(((s,d) for s,d in ongoing if s['id']==stop_id),ongoing[0])
        reason = 'city_local_today'
    else:
        today = local_days[0][1] if local_days else current.date().isoformat()
        previous = (basis or {}).get('visit_date')
        ended = today > trip['end_date']
        day = previous if ended and previous and trip['start_date']<=previous<=trip['end_date'] else trip['start_date']
        reason = 'completed_previous' if ended and day==previous else 'completed_start' if ended else 'future_start'
    matching = [s for s in stops if s['start_date']<=day<=s['end_date']]
    if chosen and chosen not in matching: chosen = None
    options = [s for s in matching if not desired or city_key(s['city'])==desired]
    if not chosen and not errors and len(options)==1: chosen = options[0]
    if stops and not chosen:
        errors.append({'field':'visit.date' if not matching else 'stop_id',
            'code':'STOP_DATE_GAP' if not matching else 'STOP_CHOICE_REQUIRED',
            'message':'이 날짜에 머무는 도시가 없어요. 방문일을 바꾸거나 여행의 도시 구간을 수정해 주세요.' if not matching else '이 날짜에 방문할 도시를 골라 주세요.'})
    return {'date':day,'date_reason':reason,'stop':chosen,'matching_stops':matching,
            'validation':errors,'evaluated_at':current.isoformat()}


def resolve(trip, overrides, stop_id=None, basis=None, clock=None):
    basis = basis or {}
    legacy_explicit_origin = bool("legacy_trip" in basis and (overrides.get("origin") or {}).get("latitude") is not None)
    stops = trip['stops']
    chosen = selection(trip, overrides, stop_id, basis, clock)
    stop = chosen['stop']
    errors = list(chosen['validation'])
    city = city_key(stop['city']) if stop else overrides.get('city') or (city_key(chosen['matching_stops'][0]['city']) if chosen['matching_stops'] else city_key(stops[0]['city']) if stops else None)
    # Legacy trips without stops need an explicit city; no inference from their title.
    base = Conditions.model_validate({'city': city if isinstance(city,str) and city in CITIES else 'tokyo', 'visit': {'date': chosen['date'], 'timezone': CITIES[city]['timezone'] if isinstance(city,str) and city in CITIES else 'Asia/Tokyo'}, 'party': trip['party']}).model_dump(mode='json')
    base['city'] = city
    if stop: base['visit']['timezone'] = stop['timezone']
    if stop and stop.get('base_location'):
        base['origin'] = {'label': stop['base_location'][:300], 'latitude': None, 'longitude': None, 'place_id': None}
    value = merge(base, overrides)
    if stop and value.get('city') != city:
        errors.append({'field': 'city', 'message': '직접 선택한 도시와 현재 체류 구간이 다릅니다. 여행 기본값으로 되돌리거나 구간을 선택해 주세요.'})
    if not value.get('city'):
        errors.append({'field': 'city', 'message': '등록된 도시를 선택하면 추천을 사용할 수 있습니다. 장소 보관함은 지금 사용할 수 있어요.'})
    else:
        try: value = Conditions.model_validate(value).model_dump(mode='json')
        except ValidationError as exc:
            errors.extend({'field': '.'.join(map(str, e['loc'])), 'message': e['msg']} for e in exc.errors(include_input=False))
    visit = value.get('visit') or {}
    day = visit.get('date') or ''
    if not trip['start_date'] <= day <= trip['end_date']:
        errors.append({'field': 'visit.date', 'message': '여행 기간 안에서 방문일을 선택해 주세요.'})
    if stop and not stop['start_date'] <= day <= stop['end_date']:
        errors.append({'field': 'visit.date', 'message': f"이 도시 구간의 체류일({stop['start_date']} ~ {stop['end_date']})에서 선택해 주세요."})
    if stop and visit.get('timezone') != stop['timezone']:
        errors.append({'field': 'visit.timezone', 'message': '도시의 현지 시간대를 다시 확인해 주세요.'})
    current_basis = {'stop_id': stop['id'] if stop else stop_id if errors and stop_id else None, 'city': value.get('city'), 'visit_date': day, 'base_location': stop.get('base_location') if stop else None}
    if 'legacy_trip' in basis:
        old = basis['legacy_trip']; oldstop = next((s for s in old.get('stops', []) if s['id'] == current_basis['stop_id']), None)
        basis = {'stop_id': oldstop['id'] if oldstop else None, 'city': city_key(oldstop['city']) if oldstop else overrides.get('city'), 'visit_date': (overrides.get('visit') or {}).get('date'), 'base_location': oldstop.get('base_location') if oldstop else None}
    warnings = []
    origin = value.get('origin')
    if origin and basis and (basis.get('origin_invalidated') or any(basis.get(k) != v for k,v in current_basis.items())):
        # Keep the label and the raw explicit input for comparison, but do not use stale coordinates.
        value['origin'] = {**origin, 'latitude': None, 'longitude': None, 'place_id': None}
        current_basis['origin_invalidated'] = True
        current_basis['previous_origin'] = basis.get('previous_origin', deepcopy(origin))
        warnings.append({'field': 'origin', 'code': 'ORIGIN_RECONFIRMATION_REQUIRED', 'message': '도시·방문일·출발점이 바뀌어 위치를 다시 확인해야 합니다. 이전 좌표는 추천에 사용하지 않습니다.'})
    overridden = flatten(overrides)
    provenance = {}
    for key, val in flatten(value).items():
        origin_type = 'user_override' if key in overridden or any(key.startswith(k+'.') for k,v in overridden.items() if v is None) else 'trip_default' if key.startswith(('party.', 'visit.')) or key in ('city',) or key.startswith('origin.') and base.get('origin') else 'planning_default'
        provenance[key] = {'origin': origin_type if val is not None or origin_type=='user_override' else 'unknown', 'validation': 'needs_confirmation' if any(e['field'] == key or key.startswith(e['field']+'.') for e in errors+warnings) else 'valid'}
    return {'legacy_explicit_origin':legacy_explicit_origin, 'conditions': value, 'overrides': deepcopy(overrides), 'selection': {'date_reason':chosen['date_reason'], 'evaluated_at':chosen['evaluated_at'], 'matching_stop_ids':[s['id'] for s in chosen['matching_stops']], 'state':'needs_selection' if errors else 'ready'}, 'trip_context': {'owner_id':trip.get('owner_id'), 'trip_id': trip['id'], 'trip_version': trip['version'], 'stop_id': current_basis['stop_id'], 'city_id': city, 'visit_date': day, 'party': trip['party'], 'timezone': visit.get('timezone')}, 'provenance': provenance, 'validation': errors, 'warnings': warnings, 'basis': current_basis, 'resolver_version': VERSION, 'origin_version': hashlib.sha256(json.dumps({'origin':value.get('origin'),'basis':current_basis},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]}


def enrich_origin(con, repo, actor, trip_id, resolved, clock=None, *, allow_deleted_selection=False):
    """Resolve only owned stay references; preserve sparse user input separately."""
    from src.accommodations.origin import context_in_connection
    visit={**resolved['conditions']['visit'], 'stop_id':resolved['trip_context']['stop_id'], 'city':resolved['conditions']['city']}
    overrides=deepcopy(resolved.get('overrides') or {})
    # Previously invalidated manually entered coordinates must not reappear.
    if resolved.get('basis',{}).get('origin_invalidated'):
        overrides['origin']=deepcopy(resolved['conditions'].get('origin'))
    if resolved.get('legacy_explicit_origin') and (overrides.get('origin_selection') or {}).get('kind')=='automatic':
        overrides.pop('origin_selection',None)
    context=context_in_connection(con,repo,actor,trip_id,visit,overrides,clock,allow_deleted_selection=allow_deleted_selection)
    resolved['origin_context']=context
    resolved['origin_version']=context['origin_version']
    origin=context.get('origin')
    if origin and origin.get('source')=='explicit_origin':
        resolved['conditions']['origin_selection']={'kind':'manual','accommodation_id':None,'expected_version':None}
    if origin:
        resolved['conditions']['origin']={key:origin.get(key) for key in ('label','latitude','longitude','place_id')}
    else:
        prior=resolved['conditions'].get('origin') or {}
        label=context.get('label') or prior.get('label')
        resolved['conditions']['origin']={'label':label,'latitude':None,'longitude':None,'place_id':None} if label else None
    for key in ('label','latitude','longitude','place_id'):
        resolved['provenance']['origin.'+key]={'origin':'accommodation' if origin and origin.get('accommodation_id') else 'user_override' if origin else 'unknown','validation':'valid' if context['status']=='ready' else 'needs_confirmation'}
    return resolved
