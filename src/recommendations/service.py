"""Durable recommendation snapshots; decisions are made only by the pure engine.

No external provider is called by default. Existing approved catalog and review
aggregates are reused. Lack of an authorized search provider is explicit in the
result; it never triggers an unmetered crawler or fabricated candidate seed.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from src.foundation.repository import DomainError, dump, new_id, utcnow
from src.discovery.models import Conditions
from src.recommendations.models import RecommendationInput

VERSIONS = ['local_observed_hybrid_v4','iconic_hybrid_v4','reference_hybrid_v4']


class CandidateBatch(list):
    def __init__(self,values,selection):
        super().__init__(values)
        self.selection=selection



def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


class Recommendations:
    def __init__(self, db, repo, jobs, discovery, reviews, matrix=None):
        self.db,self.repo,self.jobs,self.discovery,self.reviews = db,repo,jobs,discovery,reviews
        self.matrix=matrix

    @staticmethod
    def _same_candidate(captured,current):
        if current is None:return False
        legacy='review_guard_token' in captured
        def comparable(value):
            value=deepcopy(value);value.pop('review_guard_token',None)
            value['sources']=sorted(value.get('sources',[]),key=lambda row:row['id'])
            value['facts']=sorted(value.get('facts',[]),key=lambda row:row['id'])
            review=value.get('review_evidence') or {}
            if legacy:
                review.pop('dependencies',None);value.pop('dependencies',None)
            if review.get('state')!='available' and review.get('place'):
                review['place'].pop('rating',None);review['place'].pop('total_rating_count',None)
            return value
        return digest(comparable(captured))==digest(comparable(current))

    def _guard_catalog(self,con,actor,trip_id,city,candidates,*,categories=None,snapshot=None):
        current=self._catalog_on(con,trip_id,city,categories=categories,snapshot=snapshot,place_ids=[p['place_id'] for p in candidates])
        by_id={p['place_id']:p for p in current}
        if len(current)!=len(candidates) or any(not self._same_candidate(p,by_id.get(p['place_id'])) for p in candidates):
            raise DomainError('SOURCE_DATA_CHANGED','출처나 장소 자료가 바뀌었습니다. 다시 확인해 주세요.',409)

    def _get(self, con, actor, trip_id, ident):
        self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
        row=con.execute('SELECT * FROM recommendation_runs WHERE id=? AND trip_id=? AND owner_id=?',
                        (ident,trip_id,actor.id)).fetchone()
        if not row: raise DomainError('NOT_FOUND','추천 결과를 찾을 수 없습니다.',404)
        return row

    def submit(self, actor, trip_id, body, key):
        body=RecommendationInput.model_validate(body).model_dump(mode='json')
        fingerprint=digest(body)
        existing=self.jobs.lookup(actor.id,actor.session_id,'personal_trip',trip_id,'recommendations',key,fingerprint)
        if existing:
            with self.db.connect() as con:
                row=con.execute('SELECT id FROM recommendation_runs WHERE job_id=?',(existing['id'],)).fetchone()
            return self._receipt(row['id'],existing)
        envelope=self.discovery.get_conditions(actor,trip_id)
        if envelope.get('context_state')=='unsupported_city':
            raise DomainError('CITY_UNSUPPORTED','이 도시의 자동 추천은 준비 중입니다. 장소 링크나 이름은 보관함에 저장할 수 있습니다.',422)
        if envelope['conditions'].get('city') is None or envelope['version']==0 and envelope['city_needs_confirmation']:
            raise DomainError('CITY_CONFIRMATION_REQUIRED','먼저 지원 도시와 방문 조건을 저장해 주세요.',422)
        conditions=Conditions.model_validate(envelope['conditions']).model_dump(mode='json')
        trip=envelope['trip_snapshot']
        issue=self.discovery.context_issue(trip,conditions)
        if issue:
            raise DomainError('CONDITIONS_OUTDATED','여행 정보가 바뀌었습니다. '+issue['message'],409,[issue])
        snapshot={**body,'conditions':conditions,'trip':{k:trip[k] for k in ('id','version','start_date','end_date','stops')},
                  'pipeline_version':'discovery_pipeline_v2','movement_version':'v2','origin_context':envelope.get('origin_context'),'ranker_versions':VERSIONS,
                  'explanation_version':'server_templates_v1','evaluation_at':utcnow()}
        from .registry import model_snapshot
        snapshot.update(model_snapshot(snapshot))
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current=self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            saved=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            cv=saved['version'] if saved else 0
            if current['version']!=trip['version'] or current['version']!=body['trip_version'] or cv!=body['conditions_version'] or cv!=envelope['version']:
                raise DomainError('VERSION_CONFLICT','여행 또는 방문 조건이 변경되었습니다. 최신 내용을 확인해 주세요.',409)
            from src.accommodations.origin import snapshot_is_current
            if snapshot.get('origin_context') and not snapshot_is_current(con,self.repo,actor,trip_id,snapshot['origin_context']):
                raise DomainError('ORIGIN_CHANGED','출발점이 바뀌었습니다. 최신 숙소를 확인해 주세요.',409)
            from src.product.events import consented
            snapshot['analytics_opt_in']=consented(con,actor.id)
            snapshot['feedback_policy_version']='soft_avoid_half_v1'
            snapshot['soft_avoid_place_ids']=[r['place_id'] for r in con.execute('SELECT place_id,payload_json FROM visit_feedback WHERE owner_id=? AND trip_id=? AND withdrawn_at IS NULL',(actor.id,trip_id)) if json.loads(r['payload_json']).get('reflect_preference')]
            ident=new_id('rec')
            job=self.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'recommendations',{'run_id':ident},
                current['version'],key,request_fingerprint=fingerprint,con=con)
            # Another request with the same key may have won before the lock.
            old=con.execute('SELECT id FROM recommendation_runs WHERE job_id=?',(job['id'],)).fetchone()
            if old:return self._receipt(old['id'],job)
            con.execute('INSERT INTO recommendation_runs(id,trip_id,owner_id,job_id,trip_version,conditions_version,snapshot_json,ranker_versions_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (ident,trip_id,actor.id,job['id'],current['version'],cv,dump(snapshot),dump(VERSIONS),utcnow()))
        return self._receipt(ident,job)

    @staticmethod
    def _receipt(ident,job):
        return {'run_id':ident,'job_id':job['id'],'state':job['state'],
                'status_url':'/api/v2/jobs/'+job['id'],'events_url':'/api/v2/jobs/'+job['id']+'/events'}

    @staticmethod
    def _select(snapshot,candidates,limit=100):
        """Known failures cannot displace fitting candidates; unknowns remain explicit."""
        if not snapshot:return sorted(candidates,key=lambda p:p['place_id'])[:limit]
        from .engine import _candidate,RankerConfig,straight_line_distance
        config=RankerConfig(movement_version=snapshot.get('movement_version','v1'))
        from collections import Counter
        current=datetime.fromisoformat(snapshot['evaluation_at']);ranked=[];reasons=Counter()
        for candidate in candidates:
            kinds=set(candidate.get('recommendation_types',[]))&set(snapshot['conditions']['recommendation_types'])
            if snapshot.get('recommendation_model_version') in ('general_v3', 'hybrid_v4'):
                from .general import candidate as evaluate_general
                evaluations=[evaluate_general(snapshot,candidate,kind,current,config) for kind in sorted(kinds|{'reference'})]
            else:evaluations=[_candidate(snapshot,candidate,kind,current,config) for kind in sorted(kinds)]
            rejected=not evaluations or all(item['eligibility']=='ineligible' for item in evaluations)
            if rejected:reasons.update({code for item in evaluations for code in item['reason_codes']})
            distance=straight_line_distance(snapshot['conditions'].get('origin') or {},candidate)
            ranked.append((rejected,distance if distance is not None else float('inf'),candidate['place_id'],candidate))
        ranked.sort(key=lambda row:row[:3])
        return CandidateBatch(sorted([row[3] for row in ranked[:limit]],key=lambda p:p['place_id']),{'stored_candidates_considered':len(candidates),'preselection_excluded_by_reason':dict(sorted(reasons.items())),'selection_truncated':len(ranked)>limit})

    def _catalog_on(self,con,trip_id,city,*,categories=None,snapshot=None,place_ids=None):
        candidates=self.discovery._catalog_on(con,trip_id,city,include_photos=False,categories=categories,place_ids=place_ids,
            origin=(snapshot or {}).get('conditions',{}).get('origin'),available_only=True)
        reviewed=[p['place_id'] for p in candidates if p.get('provider')!='openstreetmap']
        evidence=self.reviews._evidence_on(con,reviewed)
        for candidate in candidates:
            candidate['review_evidence']=evidence.get(candidate['place_id'],{'state':'unavailable','counts':None,'metrics':None,
                'evaluation':{'strict_pass':False,'reason_codes':['NO_REVIEW_OBSERVATION']}})
        return self._select(snapshot,candidates) if place_ids is None else sorted(candidates,key=lambda p:p['place_id'])

    def _catalog(self,actor,trip_id,city,*,categories=None,snapshot=None,place_ids=None):
        with self.db.connect() as con:
            self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            return self._catalog_on(con,trip_id,city,categories=categories,snapshot=snapshot,place_ids=place_ids)

    def execute(self,job,ctx):
        from src.recommendations.engine import recommend
        actor=SimpleNamespace(id=job['actor_id'],session_id=job['session_id'])
        ident=job['payload']['run_id']; trip_id=job['trip_id']; ctx.guard()
        with self.db.connect() as con: row=dict(self._get(con,actor,trip_id,ident))
        if row['result_json']:
            return {'run_id':ident}
        snapshot=json.loads(row['snapshot_json'])
        if not row['candidates_json'] and self.discovery.public_provider and set(snapshot['conditions']['categories']) & {'restaurant','cafe'} and 'local_discovery' in snapshot['conditions']['recommendation_types']:
            city=snapshot['conditions']['city']
            pool=self._catalog(actor,trip_id,city,categories=snapshot['conditions']['categories'],snapshot=snapshot)
            from .engine import _candidate,RankerConfig
            config=RankerConfig(movement_version=snapshot.get('movement_version','v1'))
            reviewed=[p for p in pool if not p.get('synthetic') and p.get('provider')!='openstreetmap'
                and 'local_discovery' in p.get('recommendation_types',[]) and _candidate(snapshot,p,'local_discovery',datetime.fromisoformat(snapshot['evaluation_at']),config)['eligibility']=='eligible']
            language=snapshot.get('review_language_filter') or {}
            strict=language.get('required') and 'local_discovery' in language.get('apply_to',['local_discovery'])
            ctx.progress('public_discovery',done=0,total=1)
            if len(reviewed)>=snapshot.get('limit',6):
                external=self.discovery.public_provider._status(city,'not_needed',reason='REVIEWED_CATALOG_AVAILABLE')
            elif strict and snapshot.get('recommendation_model_version') not in ('general_v3','hybrid_v4'):
                external=self.discovery.public_provider._status(city,'unavailable',reason='PUBLIC_REVIEW_FILTER_UNSUPPORTED')
            else:
                external=self.discovery.public_provider.ensure(actor,trip_id,city,ctx,origin=snapshot['conditions'].get('origin'),categories=snapshot['conditions']['categories'],radius_m=snapshot['conditions'].get('radius_m'))
            snapshot['public_discovery']=external
            snapshot['evaluation_at']=utcnow()
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
                con.execute('UPDATE recommendation_runs SET snapshot_json=? WHERE id=?',(dump(snapshot),ident))
            ctx.progress('public_discovery',done=1,total=1)
        ctx.progress('candidate_snapshot',done=0,total=1)
        current=self._catalog(actor,trip_id,snapshot['conditions']['city'],categories=snapshot['conditions']['categories'],snapshot=snapshot,place_ids=[p['place_id'] for p in json.loads(row['candidates_json'])] if row['candidates_json'] else None)
        if row['candidates_json']:
            candidates=json.loads(row['candidates_json'])
            if digest(current)!=row['manifest_hash']:
                raise DomainError('SOURCE_DATA_CHANGED','조사 자료가 변경되었습니다. 현재 근거로 다시 추천해 주세요.',409)
        else:
            candidates=current
            snapshot['candidate_selection']=getattr(current,'selection',{})
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
                con.execute('UPDATE recommendation_runs SET snapshot_json=? WHERE id=?',(dump(snapshot),ident))
                con.executemany('INSERT OR IGNORE INTO trip_places VALUES(?,?,?)',[(trip_id,p['place_id'],utcnow()) for p in candidates])
                con.execute('UPDATE recommendation_runs SET candidates_json=?,manifest_hash=?,data_status=? WHERE id=?',
                    (dump(candidates),digest(candidates),'captured',ident))
        ctx.checkpoint({'candidate_snapshot':ident},stage='candidate_snapshot',done=1,total=1)
        snapshot=self._routes(actor,trip_id,ident,snapshot,candidates,ctx)
        ctx.progress('constraints_and_scoring',done=0,total=len(candidates))
        result=recommend(snapshot,candidates,now=datetime.fromisoformat(snapshot['evaluation_at']))
        ctx.progress('constraints_and_scoring',done=len(candidates),total=len(candidates))
        from .presentation import summarize
        result['summary']=summarize(snapshot,candidates,result)
        result['route_status']=snapshot.get('route_stats',{'provider_calls':0,'reason':'ROUTE_PROVIDER_DISABLED'})
        result['origin_context']=deepcopy(snapshot.get('origin_context'))
        result['external_discovery']=snapshot.get('public_discovery',{'state':'unavailable','reason':'EXTERNAL_DISCOVERY_NOT_CONFIGURED','calls':0,'cost':None})
        result['public_discovery']=result['external_discovery']
        origin=snapshot['conditions'].get('origin') or {}
        external=result['external_discovery']
        if external.get('scope')=='city_center' and origin.get('latitude') is not None and origin.get('longitude') is not None:
            from src.discovery.public_places import center,_distance
            c=center(snapshot['conditions']['city'])
            inside=bool(c and _distance(c['latitude'],c['longitude'],origin['latitude'],origin['longitude'])<=external.get('radius_m',3000))
            external['coverage']='origin_in_city_center_scope' if inside else 'origin_outside_city_center_scope'
            external['origin_scope_supported']=False
            if not inside and not any(groups[name] for groups in result['sections'].values() for name in ('items','needs_confirmation','insufficient_data')):
                result['summary']['empty_state']={'code':'PUBLIC_DISCOVERY_OUTSIDE_COVERAGE','title':'현재 자료는 도심 주변만 포함해요','description':'선택한 출발점 주변을 수집한 결과가 아니에요. 도심 기준으로 바꾸거나 가고 싶은 장소를 저장해 주세요.','actions':['edit_conditions','save_place']}

        if not candidates and snapshot.get('public_discovery'):
            status=snapshot['public_discovery']
            from src.discovery.public_places import empty_state
            result['summary']['empty_state']=empty_state(status)
        if not candidates and result['external_discovery']['state']=='ready':
            result['summary']['empty_state']={'code':'PUBLIC_DISCOVERY_NO_MATCHES','title':'조건에 맞는 공개지도 장소가 없어요','description':'현재 표시할 수 있는 공개지도 후보 중 선택한 종류와 제외 조건에 맞는 장소가 없어요. 조건을 바꾸거나 가고 싶은 장소를 저장할 수 있어요.','actions':['edit_conditions','save_place']}
        if not candidates and result['external_discovery'].get('reason')=='PUBLIC_REVIEW_FILTER_UNSUPPORTED':
            result['summary']['empty_state']={'code':'PUBLIC_REVIEW_FILTER_UNSUPPORTED','title':'리뷰 조건을 확인할 자료가 없어요','description':'공개지도는 리뷰 언어를 제공하지 않아요. 필터를 직접 바꾸거나 가고 싶은 장소를 저장할 수 있어요.','actions':['edit_conditions','save_place']}
        if result['external_discovery'].get('coverage')=='origin_outside_city_center_scope' and not any(groups[name] for groups in result['sections'].values() for name in ('items','needs_confirmation','insufficient_data')):
            result['summary']['empty_state']={'code':'PUBLIC_DISCOVERY_OUTSIDE_COVERAGE','title':'현재 자료는 도심 주변만 포함해요','description':'선택한 출발점 주변을 수집한 결과가 아니에요. 도심 기준으로 바꾸거나 가고 싶은 장소를 저장해 주세요.','actions':['edit_conditions','save_place']}
        from collections import Counter
        reasons=Counter(code for groups in result['sections'].values() for item in groups['excluded'] for code in set(item['reason_codes']))
        result['candidate_selection']={**snapshot.get('candidate_selection',{}),'provider_limit':60,'catalog_scan_limit_per_source':1000,'catalog_scan_limit':2000,'evaluation_limit':100,'evaluated':len(candidates),
            'excluded_by_reason':dict(sorted(reasons.items())),'display_limit':snapshot.get('limit',6),'reference_display_limit':12}
        for groups in result['sections'].values():
            for group in (() if snapshot.get('recommendation_model_version') in ('general_v3', 'hybrid_v4') else ('needs_confirmation','insufficient_data')):
                groups[group]=sorted(groups[group],key=lambda item:((item.get('movement') or {}).get('straight_line_m') if (item.get('movement') or {}).get('straight_line_m') is not None else float('inf'),item['place_id']))[:12]
        result['candidate_selection']['displayed']=sum(len(groups[group]) for groups in result['sections'].values() for group in ('items','needs_confirmation','insufficient_data'))
        result['requested_constraints']={k:snapshot[k] for k in ('conditions','review_language_filter','rating_filter','ordering_profile') if k in snapshot}
        result.setdefault('applied_constraints',deepcopy(result['requested_constraints']))
        result.setdefault('unsupported_constraints',[])
        # No external work was performed, but both deletion and evidence may
        # still change concurrently with a CPU-bound calculation.
        ctx.progress('source_revalidation',done=0,total=1)
        if digest(self._catalog(actor,trip_id,snapshot['conditions']['city'],categories=snapshot['conditions']['categories'],snapshot=snapshot,place_ids=[p['place_id'] for p in candidates]))!=digest(candidates):
            raise DomainError('SOURCE_DATA_CHANGED','자료가 변경되어 이전 결과를 적용하지 않았습니다.',409)
        ctx.progress('source_revalidation',done=1,total=1)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
            self._guard_catalog(con,actor,trip_id,snapshot['conditions']['city'],candidates,categories=snapshot['conditions']['categories'],snapshot=snapshot)
            from src.accommodations.origin import snapshot_is_current
            if snapshot.get('origin_context') and not snapshot_is_current(con,self.repo,actor,trip_id,snapshot['origin_context']):
                raise DomainError('ORIGIN_CHANGED','숙소가 바뀌어 이전 위치의 추천을 활성화하지 않았습니다.',409)
            con.execute("UPDATE recommendation_runs SET result_json=?,data_status='current',completed_at=? WHERE id=?",(dump(result),utcnow(),ident))
            from src.product.events import capture_run
            capture_run(con,actor.id,trip_id,ident,snapshot,candidates,result)
        ctx.progress('recommendation_complete',done=len(candidates),total=len(candidates))
        return {'run_id':ident}

    def _routes(self,actor,trip_id,ident,snapshot,candidates,ctx):
        """Collect a bounded matrix only inside the explicit recommendation job."""
        if 'route_evidence' in snapshot or snapshot.get('movement_version')!='v2':
            elements=len(snapshot.get('route_evidence') or {})
            ctx.progress('route_snapshot',done=elements,total=elements)
            return snapshot
        ctx.progress('route_snapshot',done=0,total=0)
        from .engine import route_candidates,endpoint_version
        origin=(snapshot.get('origin_context') or {}).get('origin') or snapshot['conditions'].get('origin')
        conditions=snapshot['conditions'];now=datetime.fromisoformat(snapshot['evaluation_at'])
        current=deepcopy(snapshot)
        stats={'provider_calls':0,'matrix_elements':0,'candidates':0,'reason':'ROUTE_PROVIDER_DISABLED' if not self.matrix else 'ORIGIN_COORDINATES_UNKNOWN'}
        evidence={}
        if self.matrix and origin and origin.get('latitude') is not None and origin.get('longitude') is not None:
            maximum=min(100,self.matrix.max_candidates)
            filtered=route_candidates(snapshot,candidates,now,maximum)
            endpoints=[{'id':p['place_id'],'version':endpoint_version(p),'latitude':p['latitude'],'longitude':p['longitude'],'coordinate_permitted':True} for p in filtered]
            source={'id':origin.get('accommodation_id') or origin.get('place_id') or 'manual-origin','version':(snapshot.get('origin_context') or {}).get('origin_version') or 'manual-v1','latitude':origin['latitude'],'longitude':origin['longitude'],'coordinate_permitted':True}
            if endpoints:
                ctx.progress('route_snapshot',done=0,total=len(endpoints))
                try:
                    from src.foundation.models import local_to_instant
                    visit=conditions['visit']
                    try:departure=local_to_instant(visit['date']+'T'+visit['local_time'],visit['timezone']) if visit.get('local_time') else None
                    except ValueError:departure=None
                    output=self.matrix.collect(actor,trip_id,[source],endpoints,conditions.get('transport','walking'),departure,job_ctx=ctx,request_key='recommendation:'+ident,accessibility=(conditions.get('required') or {}).get('accessibility',[]))
                    stats=output['stats']
                    for element in output['elements']:
                        index=element.get('destination_index')
                        if element.get('origin_index')==0 and type(index) is int and 0<=index<len(filtered):evidence[filtered[index]['place_id']]=element
                except DomainError as exc:
                    if exc.code in {'NOT_FOUND','LEASE_LOST','JOB_CANCELLED','JOB_DEADLINE','VERSION_CONFLICT','TRIP_DELETED','SOURCE_DATA_CHANGED'}:raise
                    stats={**stats,'reason':exc.code}
            else:stats={**stats,'reason':'NO_ROUTE_ELIGIBLE_CANDIDATES'}
        current.update(route_evidence=evidence,route_stats=stats,route_evaluation_at=utcnow(),route_policy_fingerprint=self.matrix.policy_fingerprint() if self.matrix else None)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
            con.execute('UPDATE recommendation_runs SET snapshot_json=? WHERE id=?',(dump(current),ident))
        ctx.checkpoint({'route_snapshot':ident},stage='route_snapshot',done=len(evidence),total=stats.get('matrix_elements',0))
        return current

    def get(self,actor,trip_id,ident,*,check_data=True):
        with self.db.connect() as con:
            row=dict(self._get(con,actor,trip_id,ident))
            cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            trip=con.execute('SELECT version FROM trips WHERE id=?',(trip_id,)).fetchone()
            origin_snapshot=json.loads(row['snapshot_json']).get('origin_context')
            from src.accommodations.origin import snapshot_is_current
            origin_current=not origin_snapshot or snapshot_is_current(con,self.repo,actor,trip_id,origin_snapshot)
        snapshot=json.loads(row['snapshot_json']); result=json.loads(row['result_json']) if row['result_json'] else None
        changed=[];review_only=[]
        if check_data and row['candidates_json']:
            captured=json.loads(row['candidates_json'])
            current=self._catalog(actor,trip_id,snapshot['conditions']['city'],categories=snapshot['conditions']['categories'],snapshot=snapshot,place_ids=[p['place_id'] for p in captured])
            current_by_id={p['place_id']:p for p in current}
            changed=[p['place_id'] for p in captured if not self._same_candidate(p,current_by_id.get(p['place_id']))]
            if snapshot.get('recommendation_model_version') in ('general_v3', 'hybrid_v4'):
                def without_review(value):
                    value=deepcopy(value);value.pop('review_evidence',None);value.pop('review_guard_token',None);return value
                review_only=[p['place_id'] for p in captured if p['place_id'] in changed and current_by_id.get(p['place_id']) and self._same_candidate(without_review(p),without_review(current_by_id[p['place_id']]))]
            if changed:
                # GET never destroys the durable snapshot. Changed evidence is withheld
                # from the response, including score/summary claims derived from it.
                row['data_status']='stale'
                if result:
                    for kind,groups in result.get('sections',{}).items():
                        hidden=set(changed) if kind=='local_discovery' or not review_only else set(changed)-set(review_only)
                        for name,items in groups.items():
                            groups[name]=[item for item in items if item['place_id'] not in hidden]
                            for item in groups[name]:
                                if item['place_id'] in review_only:
                                    item['review_evidence']={'state':'unavailable','counts':None,'metrics':None,'evaluation':{'strict_pass':False,'reason_codes':['REVIEW_DATA_CHANGED']}}
                        if kind in result.get('section_status',{}):
                            state=result['section_status'][kind];state['displayable_count']=len(groups['items'])+len(groups['needs_confirmation'])
                            if hidden:state.update(state='stale',reason_codes=list(dict.fromkeys(state.get('reason_codes',[])+['SOURCE_DATA_CHANGED'])))
                    result['counters']={kind:{name:len(items) for name,items in groups.items()} for kind,groups in result['sections'].items()}
                    from .presentation import summarize
                    result['summary']=summarize(snapshot,[p for p in current if p['place_id'] not in set(changed)-set(review_only)],result)
                    result['withheld_place_ids']=[p for p in changed if p not in review_only]
                    result['withheld_review_place_ids']=review_only
                    result['previous_result']=True
                    if snapshot.get('recommendation_model_version') == 'hybrid_v4':
                        # TF-IDF and the rating prior depend on the whole frozen
                        # cohort. Removing one source invalidates derived scores
                        # for the remaining cards too; GET must not rerank/write.
                        from .explanations import render
                        result['ranking_status'] = 'stale'
                        for groups in result['sections'].values():
                            for items in groups.values():
                                for item in items:
                                    item.pop('ranking_diagnostics', None)
                                    item.update(score=None, score_complete=False, ranking_status='stale')
                                    item['supported_reasons'] = render(item)
                                    item['reason_sentences'] = [{k:v for k,v in r.items() if k!='code'} for r in item['supported_reasons']]
        route_status='captured'
        route_policy=snapshot.get('route_policy_fingerprint')
        route_policy_current=not route_policy or self.matrix is not None and route_policy==self.matrix.policy_fingerprint()
        now=datetime.now(timezone.utc)
        if result:
            for section in result.get('sections',{}).values():
                for group in section.values():
                    for item in group:
                        travel=item.get('movement') or {}; route=travel.get('route') or {}
                        if route.get('status')!='ok':continue
                        try:fresh=datetime.fromisoformat(route['expires_at']).astimezone(timezone.utc)>now
                        except (ValueError,TypeError,KeyError):fresh=False
                        if not fresh or not route_policy_current:
                            route_status='stale' if route_policy_current else 'unavailable'
                            # Preserve immutable SQL evidence, but never display expired durations as current.
                            travel['route']={**route,'status':'unknown','distance_m':None,'duration_seconds':None,'reason_codes':['ROUTE_EXPIRED' if route_policy_current else 'ROUTE_POLICY_CHANGED']}
                            travel.update(duration_minutes=None,provider=None,distance_m=travel.get('straight_line_m'),kind='estimate' if travel.get('straight_line_m') is not None else 'unknown',method='haversine_straight_line' if travel.get('straight_line_m') is not None else None)
        if result:
            # Presentation-only photos are always current. They never enter the
            # immutable ranking snapshot, Chroma, or its manifest fingerprint.
            from src.discovery.photos import for_places
            with self.db.connect() as con:
                self._get(con,actor,trip_id,ident)
                entries=[item for section in result.get('sections',{}).values() for group in section.values() for item in group]
                images=for_places(con,[item['place_id'] for item in entries])
                for item in entries:item.update(images[item['place_id']])
        job=self.jobs.get(row['job_id'],actor.id,actor.session_id)
        return {'run_id':ident,'job_id':row['job_id'],'state':job['state'],'job':job,
            'trip_version':row['trip_version'],'conditions_version':row['conditions_version'],
            'request':{k:snapshot[k] for k in ('trip_version','conditions_version','review_language_filter','rating_filter','ordering_profile','limit') if k in snapshot},
            'error_code':job.get('error_code'),
            'conditions_snapshot':snapshot.get('conditions'),'snapshot':snapshot,
            'input_status':'current' if trip['version']==row['trip_version'] and (cv['version'] if cv else 0)==row['conditions_version'] and origin_current else 'stale',
            'origin_status':'current' if origin_current else 'stale','origin_context':snapshot.get('origin_context'),
            'route_status':route_status,
            'changed_dependencies':[{'place_id':ident,'reason':'SOURCE_DATA_CHANGED'} for ident in changed],
            'data_status':row['data_status'],'result':result,'created_at':row['created_at'],
            'completed_at':row['completed_at'],'reason_codes':(['SOURCE_DATA_CHANGED'] if row['data_status']=='stale' else [])+([] if origin_current else ['ORIGIN_CHANGED']),
            'ranker_versions':json.loads(row['ranker_versions_json'])}

    def list(self,actor,trip_id,limit=20,offset=0):
        with self.db.connect() as con:
            self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            rows=con.execute('SELECT id,job_id,created_at,data_status FROM recommendation_runs WHERE trip_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                             (trip_id,limit+1,offset)).fetchall()
        return {'items':[dict(r)|{'run_id':r['id'],'state':self.jobs.get(r['job_id'],actor.id,actor.session_id)['state']} for r in rows[:limit]],'next_cursor':str(offset+limit) if len(rows)>limit else None}

    def compare(self,actor,trip_id,body):
        run=self.get(actor,trip_id,body['run_id'])
        if not run['result']: raise DomainError('RESULT_UNAVAILABLE','사용 가능한 추천 결과에서 장소를 선택해 주세요.',409)
        allowed={p['place_id'] for section in run['result']['sections'].values() for name in ('items','needs_confirmation','insufficient_data') for p in section.get(name,[])}
        if not set(body['place_ids'])<=allowed: raise DomainError('NOT_FOUND','같은 추천 결과의 장소를 선택해 주세요.',404)
        ident=new_id('comparison')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._get(con,actor,trip_id,body['run_id'])
            con.execute('INSERT INTO comparison_sets VALUES(?,?,?,?,?,?,?)',(ident,trip_id,actor.id,body['run_id'],dump(body['place_ids']),dump(run['conditions_snapshot']),utcnow()))
        return self.get_comparison(actor,trip_id,ident)

    def get_comparison(self,actor,trip_id,ident):
        with self.db.connect() as con:
            self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            row=con.execute('SELECT * FROM comparison_sets WHERE id=? AND trip_id=? AND owner_id=?',(ident,trip_id,actor.id)).fetchone()
            if not row:raise DomainError('NOT_FOUND','비교 자료를 찾을 수 없습니다.',404)
            row=dict(row)
        run=self.get(actor,trip_id,row['run_id'])
        by_id={p['place_id']:p for s in (run.get('result') or {}).get('sections',{}).values() for name in ('items','needs_confirmation','insufficient_data') for p in s.get(name,[])}
        return {'comparison_id':ident,'run_id':row['run_id'],'conditions':json.loads(row['context_json']),
            'comparison_basis':{'visit':run['conditions_snapshot']['visit'],'party':run['conditions_snapshot']['party'],'origin_version':(run['origin_context'] or {}).get('origin_version'),'transport':run['conditions_snapshot'].get('transport','walking')},
            'input_status':run['input_status'],'data_status':run['data_status'],'origin_status':run['origin_status'],'origin_context':run['origin_context'],'route_status':run['route_status'],
            'items':[by_id[p] for p in json.loads(row['place_ids_json']) if p in by_id],
            'reason_codes':run['reason_codes']}

    def event(self,actor,trip_id,body):
        with self.db.connect() as con:
            self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
        return {'recorded':False,'reason':'LEGACY_EVENT_CONTRACT_DISABLED'}
