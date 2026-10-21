"""Bounded replacement of one optional visit; all other reservations and times survive."""
from copy import deepcopy,copy
from datetime import timedelta
import time
from math import isfinite
from src.foundation.repository import new_id,DomainError
from src.itineraries.service import clock,digest
from src.itineraries.intervals import utc,bounds
from src.itineraries.edits import preview
from src.itineraries.constraints import validate
from src.recommendations.engine import movement,Facts
from .preparation import fail

MAX_CANDIDATES=12
MAX_SECONDS=15
# Category default for rain: a restaurant/cafe is usually indoors, but this is
# not verified evidence. It is surfaced as its own status, never as confirmed.
# Known limit: no current data source writes 'indoor'/'outdoor' (OSM food tags
# are cuisine values, OSM sights carry the tourism value). Outdoor exclusion
# therefore only fires once curated tags or verified 'tags' facts carry it; a
# restaurant/cafe with outdoor-only seating still passes as indoor_likely.
INDOOR_LIKELY_CATEGORIES=frozenset({'restaurant','cafe'})


def _labels(value):
    if isinstance(value,str):value=[value]
    if isinstance(value,dict):value=[k for k,v in value.items() if v is True]
    if not isinstance(value,(list,tuple,set)):return set()
    return {str(x).strip().lower() for x in value if isinstance(x,str)}


def indoor_status(candidate,visit,now):
    """Rain suitability from stored evidence only; no live weather is read."""
    tags=_labels(candidate.get('tags'))
    verified,_,_=Facts(candidate,visit,now).get('tags')
    if 'outdoor' in tags:return {'status':'outdoor_confirmed','basis':'tag'}
    if 'outdoor' in _labels(verified):return {'status':'outdoor_confirmed','basis':'verified_fact'}
    if 'indoor' in tags:return {'status':'indoor_confirmed','basis':'tag'}
    if 'indoor' in _labels(verified):return {'status':'indoor_confirmed','basis':'verified_fact'}
    if candidate.get('category') in INDOOR_LIKELY_CATEGORIES:return {'status':'indoor_likely','basis':'category_default'}
    return {'status':'unconfirmed','basis':None}


def run_scores(recommendations,actor,trip,snapshot,replaced_place_id):
    """score_lower per place from the linked recommendation run, if it is still valid.

    Reads through Recommendations.get (owner-scoped, read-only), the same path
    itinerary creation uses, so stale/withheld handling is shared. Scores are
    dropped entirely when the run is no longer current: changed source data
    invalidates every derived score of the cohort, and score_lower carries a
    proximity term measured from the run's origin (ORIGIN_CHANGED). Only the
    section that ranked the replaced place is used, because sections are scored
    with different weights and their numbers are not on one scale."""
    run_id=snapshot.get('recommendation_run_id')
    if not run_id or recommendations is None or not replaced_place_id:return {}
    try:run=recommendations.get(actor,trip,run_id)
    except DomainError:return {}
    result=run.get('result')
    if not isinstance(result,dict) or run.get('data_status')!='current' or run.get('origin_status')!='current' or result.get('ranking_status')=='stale':return {}
    def ranked(groups):return [item for name in ('items','needs_confirmation') for item in (groups.get(name) or ()) if isinstance(item,dict)]
    section=next((ranked(groups) for groups in (result.get('sections') or {}).values() if isinstance(groups,dict) and any(item.get('place_id')==replaced_place_id for item in ranked(groups))),None)
    scores={}
    for item in section or ():
        value=(item.get('ranking_diagnostics') or {}).get('score_lower')
        if type(value) in (int,float) and isfinite(value) and item.get('place_id'):
            scores[item['place_id']]=max(value,scores.get(item['place_id'],value))
    return scores


def order_pool(pool,origin,scores):
    """(candidate, distance) pairs: score_lower desc (only when a run is linked) -> straight-line distance asc (unknown last) -> place_id.
    Distances are computed for the whole pool before any cap is applied."""
    rows=[(p,movement(p,{'origin':origin})['distance_m']) for p in pool]
    def key(row):
        p,distance=row;head=()
        if scores:score=scores.get(p['place_id']);head=(score is None,-(score or 0))
        return (*head,distance is None,distance or 0,p['place_id'])
    return sorted(rows,key=key)


def check_apply(result,current,now):
    guard=result.get('plan_b')
    if not guard:return
    if utc(guard['start_instant'])<=now:fail('PLAN_B_WINDOW_PASSED','대체할 일정이 이미 시작되었습니다. 현재 시각으로 다시 확인해 주세요.',409)
    prior={i['item_id']:i for i in current['items']};after={i['item_id']:i for i in result['items']}
    for ident,old in prior.items():
        if ident==guard['replaced_item_id']:continue
        new=after.get(ident)
        keys=('local_start','local_end','start_instant','end_instant','booking_id','locked','place_id')
        if not new or any(old.get(k)!=new.get(k) for k in keys):fail('PLAN_B_SCOPE_CHANGED','영향 구간 밖의 일정은 변경할 수 없습니다.',409)
    incoming=next((l for l in result['legs'] if l.get('to_item_id')==guard['added_item_id']),None)
    if incoming and guard.get('origin'):
        minutes=incoming.get('duration_minutes')
        if minutes is None:fail('PLAN_B_ROUTE_UNKNOWN','출발점에서 이동 시간을 확인해 주세요.',409)
        if max(now,utc(incoming['departure_instant']))+timedelta(minutes=minutes+incoming.get('buffer_minutes',0))>utc(guard['start_instant']):fail('PLAN_B_WINDOW_PASSED','현재 위치에서 이동할 시간이 부족합니다. 새 대체안을 확인해 주세요.',409)

class Alternatives:
    def __init__(self,itineraries,discovery):self.engine,self.discovery=itineraries,discovery

    def propose(self,actor,trip,ident,body):
        started=time.monotonic();engine=self.engine;now=clock()
        row,snapshot,data,current,tv,cv=engine._preview_base(actor,trip,ident,body['expected_version'])
        old=next((i for i in current['items'] if i['item_id']==body['item_id']),None)
        if not old:fail('NOT_FOUND','일정 항목을 찾을 수 없습니다.',404)
        if body['basis']=='provider_verified':
            candidate=next((c for c in data['candidates'] if c['place_id']==old.get('place_id')),None)
            facts=Facts(candidate or {},{'date':str(old.get('local_start',''))[:10],'timezone':snapshot['timezone']},now)
            fact=next((f for f in facts.rows if f['id']==body.get('evidence_fact_id')),None)
            if body['reason']!='closed' or not fact or not facts.permitted(fact) or fact['status']!='verified' or fact['field']!='closed' or fact['value'] is not True:
                fail('SITUATION_EVIDENCE_REQUIRED','공급자 확인으로 표시하려면 해당 날짜·지점의 검증된 휴무 근거가 필요합니다.')
        if old.get('booking_id') or old.get('lock_origin')=='booking':
            return {'alternatives':[],'reason_codes':['FIXED_BOOKING_REQUIRES_SEPARATE_CANCEL_REVIEW'],'next_action':'예약 준비에 취소 확인 업무를 만들고 예약 원본을 먼저 확인해 주세요. 외부 예약은 취소되지 않습니다.'}
        if old.get('locked'):fail('LOCKED_ITEM','일정 편집에서 잠금 해제 미리보기를 먼저 적용해 주세요.',409)
        if not bounds(old) or utc(old['start_instant'])<=now:fail('PLAN_B_WINDOW_PASSED','시작 전의 선택 일정을 지정해 주세요.',409)
        radius=body.get('radius_m');saved_radius=snapshot['conditions'].get('radius_m')
        if radius and saved_radius and radius>saved_radius:fail('HARD_CONDITION_RELAXATION','저장한 반경보다 넓힐 수 없습니다. 방문 조건을 별도 수정해 주세요.')
        origin=body.get('origin') or snapshot.get('origin')
        if not origin or origin.get('latitude') is None:fail('ORIGIN_REQUIRED','위치 권한 없이도 출발점의 좌표를 직접 선택할 수 있습니다.',details={'field':'origin'})
        before=sorted([i for i in current['items'] if bounds(i) and utc(i['end_instant'])<=utc(old['start_instant'])],key=lambda i:i['end_instant'])
        if body.get('origin') and before and utc(before[-1]['end_instant'])>now:fail('ORIGIN_CONFLICT','앞 일정이 끝나기 전에는 현재 위치로 이동 출발점을 덮어쓸 수 없습니다.')
        origin={**origin,'coordinate_permitted':True,'id':'plan_b_origin','place_id':None}
        # origin makes the stored-row LIMIT use the local spatial ordering, so the
        # nearest candidates are not cut by place_id before order_pool ranks them.
        category=old.get('category')
        catalog=self.discovery.catalog(actor,trip,snapshot['city'],categories=[category] if category else None,origin=origin);used={i.get('place_id') for i in current['items']}
        pool=[p for p in catalog if p['place_id'] not in used and not p['excluded'] and p['category']==old.get('category')]
        alternatives=[];rejected=[];processed=0;route_totals={'requests':0,'provider_calls':0};route_costs=[];stop='exhausted'
        scores=run_scores(getattr(engine,'recommendations',None),actor,trip,snapshot,old.get('place_id'))
        visit={'date':str(old.get('local_start',''))[:10],'timezone':snapshot['timezone']}
        for candidate,distance in order_pool(pool,origin,scores)[:MAX_CANDIDATES]:
            if time.monotonic()-started>=MAX_SECONDS:stop='time_cap';break
            processed+=1;pid=candidate['place_id'];reasons=[]
            indoor=indoor_status(candidate,visit,now)
            if body['reason']=='rain':
                if indoor['status']=='outdoor_confirmed':reasons.append('OUTDOOR_CONFIRMED')
                elif indoor['status']=='unconfirmed':reasons.append('INDOOR_UNCONFIRMED')
            limit=radius or saved_radius
            if limit and (distance is None or distance>limit):reasons.append('RADIUS_UNCONFIRMED' if distance is None else 'RADIUS_EXCEEDED')
            if reasons:rejected.append({'place_id':pid,'reason_codes':reasons});continue
            row,snapshot,data,current,tv,cv=engine._preview_base(actor,trip,ident,body['expected_version'],[pid])
            duration=body.get('duration_minutes') or old['duration_minutes']
            if body['reason']=='fatigue' and duration>old['duration_minutes']:rejected.append({'place_id':pid,'reason_codes':['LONGER_STAY_FOR_FATIGUE']});continue
            commands=[{'op':'remove','item_id':old['item_id']},{'op':'add','place_id':pid,'local_start':old['local_start'],'duration_minutes':duration,'item_id':new_id('item')}]
            preview_id=new_id('preview');route=engine._route(actor,trip,key=preview_id)
            if hasattr(route,'travel'):
                route.travel=copy(route.travel)
                route.travel.max_seconds=max(.01,MAX_SECONDS-(time.monotonic()-started))
                route.travel.max_elements=24
                route.stats['time_limit_seconds']=route.travel.max_seconds
                route.stats['element_limit']=24
            def scoped_route(a,b,departure,mode):
                # Unchanged legs reuse the currently valid route evidence. Only
                # incoming/outgoing legs of the replacement may invoke a provider.
                for leg in current.get('legs',[]):
                    if leg.get('from_endpoint')==a and leg.get('to_endpoint')==b and leg.get('departure_instant')==departure and leg.get('mode')==mode:
                        if not leg.get('expires_at') or utc(leg['expires_at'])>now:return deepcopy(leg)
                return route(a,b,departure,mode)
            result=preview(current,commands,snapshot,data['bookings'],data['candidates'],scoped_route,now)
            new_id_item=commands[1]['item_id']
            if body.get('origin'):
                incoming=next((l for l in result['legs'] if l.get('to_item_id')==new_id_item),None)
                if incoming:
                    departure=max(now,utc(incoming['departure_instant']));travel=route(origin,incoming['to_endpoint'],departure.isoformat(),snapshot['transport'])
                    incoming.update(travel,from_endpoint=origin,departure_instant=departure.isoformat())
                    result.update(validate(result['items'],data['candidates'],snapshot,result['legs'],now,bookings=data['bookings']))
            for k in route_totals:route_totals[k]+=route.stats.get(k,0)
            route_costs.append(deepcopy(route.stats.get('cost')))
            # Hard unknowns cannot be turned into permissive Plan B results even
            # when the parent schedule accepts a provisional opening-time draft.
            hard={'max_party','min_party','budget','radius_m','dietary','accessibility','children_rule'}
            unknowns=[x for x in result.get('unresolved_conditions',[]) if new_id_item in x['item_ids'] and (x.get('constraint') in hard or x['code'] in {'UNKNOWN_REQUIRED_DISTANCE','UNKNOWN_CHILD_RULE'})]
            if unknowns:rejected.append({'place_id':pid,'reason_codes':[x['code'] for x in unknowns]});continue
            if body['reason']=='fatigue':
                old_minutes=sum(l['duration_minutes'] for l in current['legs'] if l.get('duration_minutes') is not None and old['item_id'] in (l.get('from_item_id'),l.get('to_item_id')))
                new_legs=[l for l in result['legs'] if new_id_item in (l.get('from_item_id'),l.get('to_item_id'))]
                if any(l.get('duration_minutes') is None for l in new_legs) or sum(l['duration_minutes'] for l in new_legs)>old_minutes:rejected.append({'place_id':pid,'reason_codes':['FATIGUE_TRAVEL_NOT_REDUCED']});continue
            if not engine._can_apply(result,snapshot):
                rejected.append({'place_id':pid,'reason_codes':sorted({x['code'] for x in result['conflicts']+result['unresolved_conditions']})});continue
            result['plan_b']={'reason':body['reason'],'basis':body['basis'],'reported_at':now.isoformat(),'replaced_item_id':old['item_id'],'added_item_id':new_id_item,'start_instant':old['start_instant'],'origin':body.get('origin'),'radius_m':limit,'external_cancellation':False}
            check_apply(result,current,now)
            facts=Facts(candidate,{'date':old['local_start'][:10],'timezone':snapshot['timezone']},now)
            price,refs,_=facts.get('price')
            original_candidate=next((c for c in data['candidates'] if c['place_id']==old.get('place_id')),None)
            original_price=Facts(original_candidate or {},{'date':old['local_start'][:10],'timezone':snapshot['timezone']},now).get('price')[0]
            next_fixed=next(iter(sorted((i for i in current['items'] if i.get('booking_id') and i.get('start_instant') and utc(i['start_instant'])>=utc(old['end_instant'])),key=lambda i:i['start_instant'])),None)
            result['comparison']={'preserved_fixed_item_ids':[i['item_id'] for i in current['items'] if i.get('booking_id')],'next_fixed_start':next_fixed.get('local_start') if next_fixed else None,'price_before':original_price,'before':old,'after':next(i for i in result['items'] if i['item_id']==new_id_item),'price':price,'price_source_refs':facts.refs(refs),'cost_delta':None,'cost_delta_reason':'Same-basis total cost not confirmed','distance_from_origin_m':distance,'indoor':indoor}
            result['route_usage']=deepcopy(route.stats)
            alternatives.append(engine._store_preview(actor,trip,row,snapshot,data,result,commands,preview_id,'plan_b',tv,cv))
            if len(alternatives)==3:stop='result_cap';break
        if processed>=MAX_CANDIDATES and len(pool)>MAX_CANDIDATES:stop='candidate_cap'
        return {'alternatives':alternatives,'rejected':rejected,'reason_codes':[] if alternatives else ['NO_FEASIBLE_REPLACEMENT'],
            'situation':{'reason':body['reason'],'basis':body['basis'],'live_weather':None,'live_queue_minutes':None,'live_seats':None},
            'limits':{'max_candidates':MAX_CANDIDATES,'max_seconds':MAX_SECONDS,'ordering':(['score_lower'] if scores else [])+['straight_line_distance','place_id'],'examined':processed,'termination':stop,'elapsed_seconds':round(time.monotonic()-started,3)},'route_usage':{**route_totals,'attempt_costs':route_costs},'external_action':'none'}
