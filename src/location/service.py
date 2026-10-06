"""Bounded private route matrix orchestration using existing cost/receipt ledger."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import os
import time

from src.foundation.repository import DomainError, dump
from src.reliability.budget import CallContext
from src.reliability.providers import ProviderResult
from .geometry import coordinates, same_identity
from .providers import DisabledRouteProvider, empty_element, identity, stamp


class MatrixService:
    def __init__(self, gateway, jobs, provider=None, *, max_elements=30, max_candidates=10, max_seconds=20, clock=None):
        if not 1 <= max_elements <= 100 or not 1 <= max_candidates <= 20 or not 0 < max_seconds <= 60:
            raise ValueError('Invalid location search bounds')
        self.gateway,self.jobs,self.provider = gateway,jobs,provider or DisabledRouteProvider()
        self.max_elements,self.max_candidates,self.max_seconds = max_elements,max_candidates,max_seconds
        self.clock = clock; self.cache = {}

    def policy_fingerprint(self, con=None):
        p = self.provider
        permission = p.permission() if hasattr(p,'permission') else {}
        extra = p.policy_fingerprint(con) if hasattr(p,'policy_fingerprint') else None
        return hashlib.sha256(dump({'provider':p.name,'adapter':p.adapter_version,'policy':p.policy_version,
            'enabled':p.enabled,'permitted':p.usage_permitted,'permission':permission,'registry':extra}).encode()).hexdigest()

    def collect(self, actor, trip_id, origins, destinations, mode, departure, *, job_ctx=None,
                request_key=None, accessibility=None, limits=None):
        limits = limits or {}; started = time.monotonic(); now = stamp(self.clock)
        policy = self.policy_fingerprint()
        stats = {'candidates':len(destinations),'origins':len(origins),'matrix_elements':len(origins)*len(destinations),
            'attempted_elements':0,'provider_calls':0,'receipt_replays':0,'cache_hits':0,'max_attempts':1,
            'element_limit':self.max_elements,'candidate_limit':self.max_candidates,'time_limit_seconds':self.max_seconds,
            'cost':{'currency':None,'actual_micros':0,'pending_micros':0}}
        def guard(con=None):
            if time.monotonic()-started >= self.max_seconds:
                raise DomainError('ROUTE_TIME_LIMIT','경로 조회 실행 시간이 초과되었습니다.',409)
            if policy != self.policy_fingerprint(con):
                raise DomainError('SOURCE_DATA_CHANGED','이동 자료의 사용 범위가 변경되었습니다.',409)
            if job_ctx:
                return job_ctx.guard(con=con) if con is not None else job_ctx.guard()
            if con is not None:
                return self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            with self.jobs.db.connect() as db:
                return self.jobs._scope(db,actor.id,actor.session_id,'personal_trip',trip_id)
        guard()
        if not isinstance(origins,list) or not isinstance(destinations,list) or not origins or not destinations:
            raise DomainError('ROUTE_INPUT_INVALID','경로의 출발점과 도착점을 확인해 주세요.',422)
        p=self.provider
        def unknown(reason):
            return {'provider':p.name,'adapter_version':p.adapter_version,
                'elements':[empty_element(a,b,i,j,mode,p,reason,now) for i,a in enumerate(origins) for j,b in enumerate(destinations)],
                'stats':deepcopy(stats)}
        if stats['matrix_elements'] > self.max_elements or len(destinations)>self.max_candidates:
            return unknown('ROUTE_ELEMENT_LIMIT')
        if mode not in ('walking','car','transit'): return unknown('ROUTE_MODE_UNSUPPORTED')
        try:
            when=datetime.fromisoformat(departure)
            if when.tzinfo is None: raise ValueError()
        except (TypeError,ValueError): return unknown('ROUTE_DEPARTURE_UNKNOWN')
        if any(coordinates(x) is None for x in origins+destinations): return unknown('ROUTE_COORDINATES_UNKNOWN')
        if not p.enabled or not p.usage_permitted: return unknown('ROUTES_DISABLED' if not p.enabled else 'LOCATION_POLICY_UNAVAILABLE')
        if limits.get('zero_spend') is True or os.getenv('ZERO_SPEND','0')=='1': return unknown('ZERO_SPEND')
        price=self.gateway.budget.policy.config.get('prices',{}).get(p.name+'/'+p.sku)
        if price and any(value==0 for value in self.gateway.budget.policy.config['limits'][price['currency']].values()):
            return unknown('BUDGET_EXHAUSTED')
        if not request_key: raise DomainError('ROUTE_REQUEST_KEY_REQUIRED','경로 작업의 복구 식별자가 필요합니다.',422)
        # Cache retains direction, exact departure plus an explicit minute bucket,
        # versions and accessibility. Exact time prevents bucket-boundary ambiguity.
        def point(x):
            return {**identity(x),'latitude':x.get('latitude'),'longitude':x.get('longitude'),
                'coordinate_permitted':x.get('coordinate_permitted'),'identity_confirmed':x.get('identity_confirmed')}
        request={'scope_kind':'private_trip','owner_id':actor.id,'trip_id':trip_id,
            'origins':[point(x) for x in origins],'destinations':[point(x) for x in destinations],
            'mode':mode,'departure':when.astimezone(timezone.utc).isoformat(),
            'departure_bucket':when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M'),
            'accessibility':deepcopy(accessibility or {}),'provider':p.name,'adapter_version':p.adapter_version,'policy':policy}
        key=hashlib.sha256(dump(request).encode()).hexdigest()
        if key in self.cache:
            result=self._validated(self.cache[key],origins,destinations,mode,now)
            if all(x['status']=='ok' for x in result['elements']):
                stats['cache_hits']=1;result['stats']=stats;guard();return deepcopy(result)
            self.cache.pop(key,None)
        deadline=time.time()+self.max_seconds
        context=CallContext(actor.id,trip_id,trip_id=trip_id,
            job_id=getattr(job_ctx,'job',{}).get('id') if job_ctx else None,actor_id=actor.id)
        call_key='location-matrix:'+request_key+':'+key
        full_call_key=('job:'+context.job_id+':' if context.job_id else 'request:')+call_key
        def execute():
            guard()
            remaining=self.max_seconds-(time.monotonic()-started)
            if remaining <= 0: raise DomainError('ROUTE_TIME_LIMIT','경로 조회 시간이 초과되었습니다.',409)
            stats['provider_calls']+=1;stats['attempted_elements']=stats['matrix_elements']
            response=p.matrix(deepcopy(origins),deepcopy(destinations),mode,request['departure'],
                {'max_elements':self.max_elements,'max_candidates':self.max_candidates,
                 'timeout_seconds':min(8,remaining),'accessibility':deepcopy(accessibility or {})})
            if not isinstance(response,ProviderResult): raise ValueError('Missing observed matrix usage')
            return ProviderResult(self._validated(response.value,origins,destinations,mode,stamp(self.clock)),response.units)
        try:
            result=self.gateway.run(context,'route_matrix',call_key,{'matrix_elements':stats['matrix_elements']},execute,
                request_hash=key,provider=p.name,sku=p.sku,guard=guard,max_attempts=1,deadline=deadline)
            if stats['provider_calls']==0: stats['receipt_replays']=1
            result=self._validated(result,origins,destinations,mode,stamp(self.clock))
        except DomainError as exc:
            if exc.status in (401,403,404) or exc.code in ('LEASE_LOST','JOB_CANCELLED','JOB_DEADLINE','VERSION_CONFLICT','SOURCE_DATA_CHANGED'):
                raise
            result=unknown(exc.code)
        finally:
            with self.gateway.budget.db.connect() as con:
                rows=con.execute('SELECT currency,state,estimated_cost_micros,actual_cost_micros FROM usage_reservations WHERE owner_id=? AND trip_id=? AND operation=? AND call_key=?',
                    (actor.id,trip_id,'route_matrix',full_call_key)).fetchall()
            if rows:
                settled=all(r['state'] in ('settled','released') for r in rows)
                stats['cost']={'currency':rows[0]['currency'],
                    'actual_micros':sum(r['actual_cost_micros'] or 0 for r in rows) if settled else None,
                    'pending_micros':sum(r['estimated_cost_micros'] for r in rows if r['state'] not in ('settled','released'))}
        result['stats']=deepcopy(stats);guard()
        if all(e['status']=='ok' and e.get('usage_permission',{}).get('cache') is True for e in result['elements']):
            if len(self.cache)>=256: self.cache.pop(next(iter(self.cache)))
            self.cache[key]=deepcopy(result)
        return result

    def _validated(self, raw, origins, destinations, mode, now):
        p=self.provider; raw=raw if isinstance(raw,dict) else {}; indexed={}; duplicates=set()
        for e in raw.get('elements',[]):
            if not isinstance(e,dict):continue
            i,j=e.get('origin_index'),e.get('destination_index')
            if type(i) is not int or type(j) is not int or not 0<=i<len(origins) or not 0<=j<len(destinations):continue
            if (i,j) in indexed:duplicates.add((i,j))
            indexed[i,j]=e
        result=[]
        for i,a in enumerate(origins):
            for j,b in enumerate(destinations):
                e=empty_element(a,b,i,j,mode,p,'ROUTE_RESPONSE_INVALID',now);r=indexed.get((i,j))
                if r and (i,j) not in duplicates:
                    d,t=r.get('distance_m'),r.get('duration_seconds')
                    if r.get('status')!='ok':
                        e['reason_codes']=[x for x in r.get('reason_codes',[]) if isinstance(x,str) and x.isupper()][:5] or ['NO_ROUTE']
                    elif r.get('mode')!=mode or r.get('origin_identity')!=identity(a) or r.get('destination_identity')!=identity(b):
                        e['reason_codes']=['ROUTE_CONTEXT_MISMATCH']
                    elif all(not isinstance(x,bool) and isinstance(x,(float,int)) and math.isfinite(x) and x>=0 for x in (d,t)) and t<=86400 and (d>0 and t>0 or same_identity(a,b)):
                        try:
                            checked=datetime.fromisoformat(r['checked_at']);expires=datetime.fromisoformat(r['expires_at'])
                            if checked.tzinfo is None or expires.tzinfo is None or not checked<=now<expires:raise ValueError()
                            permission=r.get('usage_permission') or {}
                            ttl=permission.get('ttl_seconds')
                            if permission.get('display') is not True or permission.get('durable_storage') is not True:raise ValueError()
                            if type(ttl) is not int or ttl<=0 or (expires-checked).total_seconds()>ttl:raise ValueError()
                            e.update({'status':'ok','distance_m':d,'duration_seconds':t,
                                'checked_at':checked.isoformat(),'expires_at':expires.isoformat(),'reason_codes':[],
                                'usage_permission':p.permission(),'traffic_live':False,
                                'accessibility_status':r.get('accessibility_status') if r.get('accessibility_status') in ('satisfied','violated') else 'unknown'})
                            if r.get('synthetic') is True:e['synthetic']=True
                        except (KeyError,TypeError,ValueError):e['reason_codes']=['ROUTE_RESPONSE_STALE']
                result.append(e)
        return {'provider':p.name,'adapter_version':p.adapter_version,'elements':result}
