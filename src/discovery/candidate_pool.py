"""Bounded public-area retrieval. A private origin never chooses external queries.

Photon has no documented page cursor. Seven *public*, deterministic geographic
windows reuse the same server, cache, call ledger, refusal backoff and daily caps.
The windows are a sampling plan, not an administrative-city census.
"""
from collections import Counter
import math
import time
from .public_places import PublicDiscovery, center, MAX_CANDIDATES, COOLDOWN

VERSION = 'public_area_pool_v1'
CANDIDATE_POOL_LIMIT = 200
RAW_RECORD_LIMIT = 400
QUERY_LIMIT = 7

def public_areas(city):
    c = center(city)
    if not c:return []
    # No address, device location or hotel-derived cell is sent to the provider.
    offsets = [('center',0,0),('north',2400,0),('east',0,2400),('south',-2400,0),
               ('west',0,-2400),('northeast',1700,1700),('southwest',-1700,-1700)]
    return [dict(c, public_area_id=key,
                 latitude=c['latitude']+north/111320,
                 longitude=c['longitude']+east/(111320*math.cos(math.radians(c['latitude']))))
            for key,north,east in offsets]

class ExpandedPublicDiscovery(PublicDiscovery):
    def __init__(self,db,*,settings=None,sleeper=time.sleep,monotonic=time.monotonic,**kwargs):
        super().__init__(db,**kwargs)
        self.pool_limit=getattr(settings,'candidate_pool_limit',200)
        self.raw_limit=getattr(settings,'candidate_raw_record_limit',400)
        self.query_limit=getattr(settings,'candidate_query_limit',7)
        self.deadline=getattr(settings,'candidate_deadline_seconds',120)
        for value,maximum in ((self.pool_limit,200),(self.raw_limit,400),(self.query_limit,7),(self.deadline,180)):
            if type(value) is not int or not 1<=value<=maximum:raise ValueError('Candidate retrieval setting exceeds reviewed server bound')
        self.sleeper,self.monotonic=sleeper,monotonic

    def ensure(self,actor,trip_id,city,ctx,*,origin=None,categories=None,radius_m=None,existing_count=0):
        plan=public_areas(city)[:self.query_limit]
        if not plan:return self._status(city,'unavailable',reason='CITY_UNSUPPORTED')
        checkpoint=dict((ctx.job.get('checkpoint') or {}).get('candidate_retrieval') or {})
        if checkpoint and (checkpoint.get('city')!=city or checkpoint.get('categories')!=sorted(categories or [])
                           or checkpoint.get('version')!=VERSION
                           or checkpoint.get('limits',snapshot_limits(self))!=snapshot_limits(self)):
            from src.foundation.repository import DomainError
            raise DomainError('VERSION_CONFLICT','후보 조회 조건이 변경되었습니다.',409)
        done=list(checkpoint.get('areas') or [])
        began=self.monotonic(); prior=checkpoint.get('elapsed_seconds',0)
        raw=checkpoint.get('raw_records',0);calls=checkpoint.get('calls',0)
        reason=None; unique=set(); category_counts=Counter()
        def observed():
            unique.clear();category_counts.clear()
            packs=[r['pack_id'] for r in done if r.get('pack_id')]
            if packs:
                with self.db.connect() as con:
                    for row in con.execute('SELECT DISTINCT place_id,category FROM research_candidates WHERE status=? AND pack_id IN ('+','.join('?' for _ in packs)+')',('public_data',*packs)):
                        if row['place_id'] not in unique:
                            unique.add(row['place_id']);category_counts[row['category']]+=1
        observed()
        for area in plan[len(done):]:
            if existing_count+len(unique)>=self.pool_limit:reason='candidate_cap';break
            if raw>=self.raw_limit:reason='raw_record_cap';break
            if prior+self.monotonic()-began>=self.deadline:reason='deadline';break
            ctx.guard()
            # Each fresh query consumes the same global/user quota as legacy queries.
            area=dict(area,record_limit=min(MAX_CANDIDATES,self.raw_limit-raw))
            status=super().ensure(actor,trip_id,city,ctx,categories=categories,_area=area)
            if status.get('reason')=='PUBLIC_DISCOVERY_COOLDOWN' and not status.get('last_error') and status.get('retry_after_seconds',999)<=COOLDOWN.total_seconds():
                delay=status['retry_after_seconds']
                if prior+self.monotonic()-began+delay+15>=self.deadline:reason='deadline';break
                for _ in range(delay):ctx.guard();self.sleeper(1)
                status=super().ensure(actor,trip_id,city,ctx,categories=categories,_area=area)
            calls+=status.get('calls',0)
            if status['state'] not in ('ready','empty'):
                reason=status.get('reason','provider_failed');break
            # Unobservable normalization counts are bounded by the entire requested
            # response size, never undercounted to make another request possible.
            received=status.get('fetched_count')
            raw+=received if type(received) is int else area['record_limit'] if status.get('calls') else 0
            done.append({**status,'public_area_id':area['public_area_id'],'query_center':{k:area[k] for k in ('latitude','longitude','radius_m')}})
            observed()
            ctx.checkpoint({'candidate_retrieval':{'version':VERSION,'city':city,'categories':sorted(categories or []),
                'limits':snapshot_limits(self),'areas':done,'raw_records':raw,'calls':calls,'elapsed_seconds':prior+self.monotonic()-began}},stage='public_discovery',done=len(done),total=len(plan))
        if reason is None:reason='public_plan_exhausted'
        fetched_known=all(r.get('fetched_count') is not None for r in done)
        actual_fetched=sum(r.get('fetched_count') or 0 for r in done) if fetched_known else None
        return self._status(city,'ready' if unique or existing_count else 'empty' if done else 'unavailable',
            reason=reason,calls=calls,cache_hit=bool(done and not calls),candidate_count=len(unique),
            fetched_count=actual_fetched,
            deduped_count=sum(r.get('candidate_count',0) for r in done)-len(unique)+sum(r.get('deduped_count') or 0 for r in done),
            rejected_count=sum(r['rejected_count'] for r in done) if all(r.get('rejected_count') is not None for r in done) else None,
            raw_record_budget_consumed=raw,requested_limit=self.pool_limit,
            provider_limit_per_query=MAX_CANDIDATES,provider_limit=self.raw_limit,
            query_plan_version=VERSION,query_count=len(done),planned_queries=len(plan),
            scope='public_city_areas',origin_scope_supported=False,scope_reason='PRIVATE_ORIGIN_INTERNAL_ONLY',
            categories=dict(category_counts),coverage={'kind':'public_geographic_samples','areas':[
                {'id':r['public_area_id'],**r['query_center'],'count':r.get('candidate_count'),
                 'fetched_at':r.get('fetched_at'),'cache_hit':r.get('cache_hit')} for r in done],
                'complete_city':False,'source_cursor_supported':False,'private_origin_sent':False},
            truncated=reason not in ('public_plan_exhausted',),truncation_reason=reason,
            notice='도심과 인접 공개 지역에서 확인한 지도 자료입니다. 선택한 숙소와의 거리는 별도로 계산합니다.')


def snapshot_limits(provider):
    return {'candidate_pool_limit':getattr(provider,'pool_limit',200),
            'raw_record_limit':getattr(provider,'raw_limit',400),
            'candidate_query_limit':getattr(provider,'query_limit',1),
            'candidate_deadline_seconds':getattr(provider,'deadline',None),
            'initial_visible_limit':12,'result_page_size':12,
            'review_record_limit_per_place':200,'review_window_days':180,
            'candidate_preselection_version':'scope_distance_evidence_v2',
            'candidate_retrieval_version':VERSION if isinstance(provider,ExpandedPublicDiscovery) else 'legacy_catalog_v1'}


def validate_retrieval_snapshot(snapshot,provider):
    # Historical runs without these fields retain their old contract. New runs
    # cannot silently acquire a different pool when deployment flags change.
    current=snapshot_limits(provider)
    if snapshot.get('candidate_retrieval_version') and any(snapshot.get(k)!=v for k,v in current.items() if k in snapshot):
        from src.foundation.repository import DomainError
        raise DomainError('VERSION_CONFLICT','후보 조회 설정이 변경되었습니다. 새 조건으로 다시 찾아 주세요.',409)
