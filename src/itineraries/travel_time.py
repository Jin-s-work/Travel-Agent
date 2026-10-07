"""Scoped bounded route lookups. No live route provider is enabled by default.

Straight-line walking estimates are planning assumptions, never verified routes.
Provider execution goes through the durable receipt/budget wrapper; an unknown
charge is not retried. Session-local caches do not cross users or trips.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
import time

from src.foundation.repository import DomainError, dump
from src.reliability.budget import CallContext
from src.reliability.providers import ProviderResult
from src.location.geometry import coordinates, haversine_straight_line, same_identity

ESTIMATE_VERSION = 'walking_distance_assumption_v1'


def _coordinates(endpoint):
    return coordinates(endpoint)


def _distance(a,b):
    return haversine_straight_line(a,b)


class TravelTime:
    def __init__(self,gateway,jobs,provider=None,*,max_elements=120,max_seconds=20,clock=None):
        if not 1 <= max_elements <= 500 or not 0 < max_seconds <= 60:
            raise ValueError('Invalid route search bounds')
        self.gateway,self.jobs,self.provider=gateway,jobs,provider
        self.max_elements,self.max_seconds=max_elements,max_seconds
        self.clock=clock or (lambda:datetime.now(timezone.utc))

    def lookup(self,actor,trip_id,job_ctx=None,request_key=None):
        if self.provider is not None and getattr(self.provider,'enabled',True) and not request_key:
            raise DomainError('ROUTE_REQUEST_KEY_REQUIRED','경로 요청의 복구 식별자가 필요합니다.',422)
        return LookupSession(self,actor,trip_id,job_ctx,request_key)

    def policy_fingerprint(self,con=None):
        """Local-only commit guard; provider adapters own their policy registry."""
        p=self.provider
        extra=p.policy_fingerprint(con) if p is not None and hasattr(p,'policy_fingerprint') else None
        return hashlib.sha256(dump({'provider':getattr(p,'name',None),
            'version':getattr(p,'policy_version',None),'adapter':getattr(p,'adapter_version',None),
            'usage_permitted':getattr(p,'usage_permitted',False),'registry':extra,
            'enabled':getattr(p,'enabled',True),
            'estimate':ESTIMATE_VERSION}).encode()).hexdigest()


class LookupSession:
    def __init__(self,travel,actor,trip_id,job_ctx,request_key):
        self.travel,self.actor,self.trip_id,self.job_ctx=travel,actor,trip_id,job_ctx
        self.request_key=request_key or 'local'
        self.policy=travel.policy_fingerprint()
        self.cache={};self.started=time.monotonic();self.call_keys=set()
        self.stats={'requests':0,'matrix_elements':0,'provider_calls':0,'receipt_replays':0,
                    'cache_hits':0,'estimates':0,'unknown':0,'element_limit':travel.max_elements,
                    'time_limit_seconds':travel.max_seconds,'cost':{'currencies':[],'actual_micros':0,'pending_micros':0}}

    def guard(self,con=None):
        if self.travel.policy_fingerprint(con=con)!=self.policy:
            raise DomainError('SOURCE_DATA_CHANGED','이동 자료 사용 범위가 변경되었습니다.',409)
        if self.job_ctx is not None:
            return self.job_ctx.guard(con=con) if con is not None else self.job_ctx.guard()
        if con is not None:
            return self.travel.jobs._scope(con,self.actor.id,self.actor.session_id,'personal_trip',self.trip_id)
        with self.travel.jobs.db.connect() as db:
            return self.travel.jobs._scope(db,self.actor.id,self.actor.session_id,'personal_trip',self.trip_id)

    def _unknown(self,reason,stamp):
        self.stats['unknown']+=1
        return {'duration_minutes':None,'distance_meters':None,'basis':'unknown','checked_at':stamp,
                'expires_at':None,'source_refs':[],'policy_version':None,'reason_codes':[reason]}

    def _cost(self):
        if not self.call_keys:return
        with self.travel.gateway.budget.db.connect() as con:
            rows=con.execute('SELECT currency,state,actual_cost_micros,estimated_cost_micros FROM usage_reservations WHERE owner_id=? AND trip_id=? AND operation=? AND call_key IN ('+','.join('?' for _ in self.call_keys)+')',
                (self.actor.id,self.trip_id,'route_matrix',*sorted(self.call_keys))).fetchall()
        currencies=sorted({r['currency'] for r in rows})
        pending=sum(r['estimated_cost_micros'] for r in rows if r['state'] not in ('settled','released'))
        known=all(r['state'] in ('settled','released') for r in rows)
        self.stats['cost']={'currencies':currencies,'actual_micros':sum(r['actual_cost_micros'] or 0 for r in rows) if known and len(currencies)<=1 else None,
                            'pending_micros':pending if len(currencies)<=1 else None}

    def __call__(self,origin,destination,departure_instant,mode):
        self.guard();self.stats['requests']+=1
        now=self.travel.clock().astimezone(timezone.utc);stamp=now.isoformat()
        def unknown(reason, at):
            return self._with_geometry(self._unknown(reason,at),origin,destination)
        try:
            departure=datetime.fromisoformat(departure_instant)
            if departure.tzinfo is None:raise ValueError()
        except (ValueError,TypeError):return unknown('TRAVEL_DEPARTURE_UNKNOWN',stamp)
        if mode not in {'walking','transit','car'}:return unknown('TRAVEL_MODE_UNSUPPORTED',stamp)
        # Bind exact departure, coordinate evidence permission and provider policy.
        policy=getattr(self.travel.provider,'policy_version',None)
        request={'origin':{k:origin.get(k) for k in ('id','version','coordinate_version','identity_confirmed','place_id','latitude','longitude','coordinate_permitted','city','accessibility','accessibility_constraints')},
                 'destination':{k:destination.get(k) for k in ('id','version','coordinate_version','identity_confirmed','place_id','latitude','longitude','coordinate_permitted','city','accessibility','accessibility_constraints')},
                 'departure':departure.astimezone(timezone.utc).isoformat(),'mode':mode,'policy_version':policy,
                 'policy_fingerprint':self.travel.policy_fingerprint(),
                 'adapter_version':getattr(self.travel.provider,'adapter_version',ESTIMATE_VERSION)}
        key=hashlib.sha256(dump(request).encode()).hexdigest()
        if key in self.cache and (not self.cache[key].get('expires_at') or self.cache[key]['expires_at']>stamp):
            self.stats['cache_hits']+=1;return deepcopy(self.cache[key])
        if time.monotonic()-self.started>=self.travel.max_seconds:return unknown('ROUTE_TIME_LIMIT',stamp)
        if self.stats['matrix_elements']>=self.travel.max_elements:return unknown('ROUTE_ELEMENT_LIMIT',stamp)
        self.stats['matrix_elements']+=1
        a,b=_coordinates(origin),_coordinates(destination)
        same=same_identity(origin,destination)
        if same:
            result={'duration_minutes':0,'distance_meters':0,'basis':'provider','provider':'identity',
                    'checked_at':stamp,'expires_at':(now+timedelta(minutes=15)).isoformat(),'source_refs':[],
                    'policy_version':'same_place_identity_v1','reason_codes':['SAME_PLACE_NO_TRAVEL']}
        elif self.travel.provider is not None and getattr(self.travel.provider,'enabled',True):
            provider=self.travel.provider
            if not getattr(provider,'enabled',True) or not getattr(provider,'usage_permitted',False) or not policy:
                return unknown('ROUTE_POLICY_UNAVAILABLE',stamp)
            if a is None or b is None:return unknown('ROUTE_COORDINATES_UNKNOWN',stamp)
            if os.getenv('ZERO_SPEND','0')=='1':return unknown('ZERO_SPEND',stamp)
            price=self.travel.gateway.budget.policy.config.get('prices',{}).get(provider.name+'/'+provider.sku)
            if price and any(value==0 for value in self.travel.gateway.budget.policy.config['limits'][price['currency']].values()):
                return unknown('BUDGET_EXHAUSTED',stamp)
            context=CallContext(self.actor.id,self.trip_id,trip_id=self.trip_id,
                job_id=getattr(self.job_ctx,'job',{}).get('id') if self.job_ctx else None,actor_id=self.actor.id)
            call_key='route:'+self.request_key+':'+key
            self.call_keys.add(('job:'+context.job_id+':' if context.job_id else 'request:')+call_key)
            before=self.stats['provider_calls']
            def execute():
                self.stats['provider_calls']+=1
                # An adapter must bound its own transport, disable SDK retries,
                # and return observed billing units even for a no-route result.
                if hasattr(provider,'matrix'):
                    from src.location.service import MatrixService
                    value=provider.matrix([deepcopy(request['origin'])],[deepcopy(request['destination'])],mode,
                        request['departure'],{'max_elements':1,'timeout_seconds':min(8,self.travel.max_seconds),
                         'accessibility':deepcopy(origin.get('accessibility_constraints') or origin.get('accessibility') or [])})
                    if not isinstance(value,ProviderResult):raise TypeError('Missing measured provider usage')
                    normalized=MatrixService(self.travel.gateway,self.travel.jobs,provider,clock=self.travel.clock)._validated(
                        value.value,[request['origin']],[request['destination']],mode,self.travel.clock().astimezone(timezone.utc))['elements'][0]
                    mapped={'duration_minutes':normalized['duration_seconds']/60 if normalized['duration_seconds'] is not None else None,
                        'distance_meters':normalized['distance_m'],'checked_at':normalized['checked_at'],
                        'expires_at':normalized['expires_at'],
                        'accessibility_status':normalized.get('accessibility_status','unknown'),
                        'usage_permission':normalized.get('usage_permission')}
                    value=ProviderResult(mapped,value.units)
                else:
                    value=provider.lookup(deepcopy(request),timeout_seconds=min(8,self.travel.max_seconds))
                if not isinstance(value,ProviderResult):raise TypeError('Missing measured provider usage')
                # Persist only our normalized route contract, never an entire
                # HTTP response or additional provider account/profile fields.
                return ProviderResult(self._validated(value.value,self.travel.clock().astimezone(timezone.utc).isoformat(),policy),value.units)
            try:
                result=self.travel.gateway.run(context,'route_matrix',call_key,{'matrix_elements':1},execute,
                    request_hash=key,provider=provider.name,sku=provider.sku,guard=self.guard,max_attempts=1,
                    deadline=time.time()+max(0,self.travel.max_seconds-(time.monotonic()-self.started)))
                if self.stats['provider_calls']==before:self.stats['receipt_replays']+=1
                if result.get('basis')!='unknown':
                    result=self._validated(result,self.travel.clock().astimezone(timezone.utc).isoformat(),policy)
            except DomainError as exc:
                if exc.status in (401,403,404) or exc.code in {'LEASE_LOST','JOB_DEADLINE','JOB_CANCELLED','VERSION_CONFLICT'}:raise
                result=unknown(exc.code,stamp)
            finally:self._cost()
        elif self.travel.provider is not None:
            result=unknown('ROUTES_DISABLED',stamp)
        elif mode=='walking' and a is not None and b is not None and origin.get('city')==destination.get('city'):
            meters=_distance(a,b)
            if meters>3000:return unknown('ROUTE_DISTANCE_REQUIRES_PROVIDER',stamp)
            self.stats['estimates']+=1
            result={'duration_minutes':max(1,math.ceil(meters*1.4/80)),'distance_meters':meters,
                'basis':'estimate','provider':None,'checked_at':stamp,'expires_at':(now+timedelta(minutes=15)).isoformat(),
                'source_refs':[],'policy_version':ESTIMATE_VERSION,'reason_codes':['WALKING_ROUTE_UNVERIFIED'],
                'assumption':{'distance':'straight_line','detour_factor':1.4,'meters_per_minute':80,'barriers_verified':False}}
        else:result=unknown('NO_ROUTE',stamp)
        result=self._with_geometry(result,origin,destination)
        result.setdefault('mode',mode)
        result.setdefault('departure',request['departure'])
        result.setdefault('origin_identity',{k:origin.get(k) for k in ('id','version','place_id','coordinate_version')})
        result.setdefault('destination_identity',{k:destination.get(k) for k in ('id','version','place_id','coordinate_version')})
        result.setdefault('observed_at',self.travel.clock().astimezone(timezone.utc).isoformat())
        self.guard();self.cache[key]=deepcopy(result);return deepcopy(result)

    @staticmethod
    def _with_geometry(result,origin,destination):
        a,b=_coordinates(origin),_coordinates(destination)
        distance=_distance(a,b) if a is not None and b is not None else None
        return {**result,'straight_line_meters':distance,
            'straight_line_method':'haversine_straight_line' if distance is not None else None}

    def _validated(self,result,stamp,policy):
        if not isinstance(result,dict):return self._unknown('ROUTE_RESPONSE_INVALID',stamp)
        minutes=result.get('duration_minutes');distance=result.get('distance_meters')
        if isinstance(minutes,bool) or not isinstance(minutes,(int,float)) or not math.isfinite(minutes) or not 0<minutes<=1440:
            return self._unknown('NO_ROUTE' if minutes is None else 'ROUTE_RESPONSE_INVALID',stamp)
        if distance is not None and (isinstance(distance,bool) or not isinstance(distance,(int,float)) or not math.isfinite(distance) or distance<=0):
            return self._unknown('ROUTE_RESPONSE_INVALID',stamp)
        try:
            checked=datetime.fromisoformat(result['checked_at']);expires=datetime.fromisoformat(result['expires_at'])
            current=datetime.fromisoformat(stamp)
            if checked.tzinfo is None or expires.tzinfo is None or not checked<=current<expires:raise ValueError()
        except (KeyError,TypeError,ValueError):return self._unknown('ROUTE_RESPONSE_STALE',stamp)
        # Never retain arbitrary provider payload (authors, URLs, raw body, etc.).
        return {'duration_minutes':minutes,'distance_meters':distance,'basis':'provider','provider':self.travel.provider.name,
                'checked_at':checked.astimezone(timezone.utc).isoformat(),'expires_at':expires.astimezone(timezone.utc).isoformat(),'source_refs':[],
                'policy_version':policy,'adapter_version':self.travel.provider.adapter_version,'reason_codes':[],
                'accessibility_status':result.get('accessibility_status') if result.get('accessibility_status') in ('satisfied','violated') else 'unknown',
                'usage_permission':self.travel.provider.permission() if hasattr(self.travel.provider,'permission') else None}
