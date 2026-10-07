"""Sparse personal overrides and inherited travel context. No network or writes."""
from copy import deepcopy
import json
import hashlib
from pydantic import ValidationError
from src.destinations import CITIES, city_key
from src.foundation.repository import DomainError
from .models import Conditions

VERSION = 'inherited_context_v1'


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


def resolve(trip, overrides, stop_id=None, basis=None):
    basis = basis or {}
    legacy_explicit_origin = bool("legacy_trip" in basis and (overrides.get("origin") or {}).get("latitude") is not None)
    stops = trip['stops']
    stop = next((s for s in stops if s['id'] == stop_id), None)
    errors = []
    if stop_id and not stop:
        errors.append({'field': 'stop_id', 'message': '선택한 도시 구간이 삭제되었습니다. 현재 여행의 구간을 골라 주세요.'})
    if not stop_id:
        desired = overrides.get('city')
        day = (overrides.get('visit') or {}).get('date')
        stop = next((s for s in stops if city_key(s['city']) == desired and (not day or s['start_date'] <= day <= s['end_date'])), None) if desired else None
        if not stop: stop = next((s for s in stops if city_key(s['city'])), stops[0] if stops else None)
    city = city_key(stop['city']) if stop else overrides.get('city')
    # Legacy trips without stops need an explicit city; no inference from their title.
    base = Conditions.model_validate({'city': city if isinstance(city,str) and city in CITIES else 'tokyo', 'visit': {'date': stop['start_date'] if stop else trip['start_date'], 'timezone': CITIES[city]['timezone'] if isinstance(city,str) and city in CITIES else 'Asia/Tokyo'}, 'party': trip['party']}).model_dump(mode='json')
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
    current_basis = {'stop_id': stop['id'] if stop else stop_id, 'city': value.get('city'), 'visit_date': day, 'base_location': stop.get('base_location') if stop else None}
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
    return {'legacy_explicit_origin':legacy_explicit_origin, 'conditions': value, 'overrides': deepcopy(overrides), 'trip_context': {'trip_id': trip['id'], 'trip_version': trip['version'], 'stop_id': current_basis['stop_id'], 'city_id': city, 'visit_date': day, 'party': trip['party'], 'timezone': visit.get('timezone')}, 'provenance': provenance, 'validation': errors, 'warnings': warnings, 'basis': current_basis, 'resolver_version': VERSION, 'origin_version': hashlib.sha256(json.dumps({'origin':value.get('origin'),'basis':current_basis},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]}


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
