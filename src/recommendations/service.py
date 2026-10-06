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

VERSIONS = ['local_editorial_v1','iconic_v1','local_observed_v1']


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


class Recommendations:
    def __init__(self, db, repo, jobs, discovery, reviews):
        self.db,self.repo,self.jobs,self.discovery,self.reviews = db,repo,jobs,discovery,reviews

    @staticmethod
    def _review_guard(con):
        return digest({
            'controls':[dict(r) for r in con.execute('SELECT * FROM review_controls')],
            'policies':[dict(r) for r in con.execute('SELECT id,status,version,rights_json,expires_at FROM provider_policies ORDER BY id')],
            'aggregates':[dict(r) for r in con.execute('SELECT id,invalidated_at,expires_at FROM review_aggregates ORDER BY id')],
            'pointers':[tuple(r) for r in con.execute('SELECT id,active_aggregate_id FROM place_identities ORDER BY id')],
        })

    def _guard_catalog(self,con,actor,trip_id,city,candidates):
        rows=con.execute("SELECT c.*,p.name,p.address,p.city,p.identity_status FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id JOIN place_identities p ON p.id=c.place_id WHERE p.deleted_at IS NULL AND c.status='approved' AND k.status='approved' AND p.identity_status='verified' AND p.city=? ORDER BY p.id",(city,)).fetchall()
        excluded={r[0] for r in con.execute('SELECT place_id FROM discovery_exclusions WHERE trip_id=?',(trip_id,))}
        current={}
        for row in rows:
            p=self.discovery._candidate(con,row)
            p['excluded']=row['place_id'] in excluded;current[row['place_id']]=p
        core=[{k:v for k,v in p.items() if k not in {'review_evidence','review_guard_token'}} for p in candidates]
        if digest(list(current.values())[:100])!=digest(core) or any(p['review_guard_token']!=self._review_guard(con) for p in candidates):
            raise DomainError('SOURCE_DATA_CHANGED','출처나 장소 자료가 바뀌었습니다. 다시 확인해 주세요.',409)
        for p in candidates:
            review=p.get('review_evidence') or {}
            if review.get('expires_at') and review['expires_at']<=utcnow():
                raise DomainError('SOURCE_DATA_CHANGED','리뷰 근거가 만료되었습니다.',409)

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
        if envelope['conditions'].get('city') is None or envelope['version']==0 and envelope['city_needs_confirmation']:
            raise DomainError('CITY_CONFIRMATION_REQUIRED','먼저 지원 도시와 방문 조건을 저장해 주세요.',422)
        conditions=Conditions.model_validate(envelope['conditions']).model_dump(mode='json')
        trip=envelope['trip_snapshot']
        if not trip['start_date']<=conditions['visit']['date']<=trip['end_date']:
            raise DomainError('CONDITIONS_OUTDATED','현재 여행 기간에 맞게 방문 조건을 다시 저장해 주세요.',409)
        snapshot={**body,'conditions':conditions,'trip':{k:trip[k] for k in ('id','version','start_date','end_date','stops')},
                  'pipeline_version':'discovery_pipeline_v1','ranker_versions':VERSIONS,
                  'explanation_version':'server_templates_v1','evaluation_at':utcnow()}
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current=self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            saved=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            cv=saved['version'] if saved else 0
            if current['version']!=body['trip_version'] or cv!=body['conditions_version'] or cv!=envelope['version']:
                raise DomainError('VERSION_CONFLICT','여행 또는 방문 조건이 변경되었습니다. 최신 내용을 확인해 주세요.',409)
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

    def _catalog(self,actor,trip_id,city):
        candidates=self.discovery.catalog(actor,trip_id,city)
        if len(candidates)>100: candidates=candidates[:100]
        # Link only server-verified candidates within this owned trip so the
        # existing consumer evidence policy remains the sole review gate.
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
            for p in candidates:
                con.execute('INSERT OR IGNORE INTO trip_places VALUES(?,?,?)',(trip_id,p['place_id'],utcnow()))
        self.reviews.purge()
        with self.db.connect() as con: review_guard=self._review_guard(con)
        for candidate in candidates:
            try: candidate['review_evidence']=self.reviews.evidence(actor,trip_id,candidate['place_id'])
            except DomainError as exc:
                if exc.status in (401,): raise
                candidate['review_evidence']={'state':'unavailable','counts':None,'metrics':None,
                    'evaluation':{'strict_pass':False,'reason_codes':['NO_REVIEW_OBSERVATION']}}
            candidate['review_guard_token']=review_guard
        with self.db.connect() as con:
            if self._review_guard(con)!=review_guard:
                raise DomainError('SOURCE_DATA_CHANGED','검토 상태가 바뀌었습니다. 다시 확인해 주세요.',409)
        return candidates

    def execute(self,job,ctx):
        from src.recommendations.engine import recommend
        actor=SimpleNamespace(id=job['actor_id'],session_id=job['session_id'])
        ident=job['payload']['run_id']; trip_id=job['trip_id']; ctx.guard()
        with self.db.connect() as con: row=dict(self._get(con,actor,trip_id,ident))
        if row['result_json']:
            return {'run_id':ident}
        snapshot=json.loads(row['snapshot_json'])
        ctx.progress('candidate_snapshot',done=0,total=1)
        current=self._catalog(actor,trip_id,snapshot['conditions']['city'])
        if row['candidates_json']:
            candidates=json.loads(row['candidates_json'])
            if digest(current)!=row['manifest_hash']:
                raise DomainError('SOURCE_DATA_CHANGED','조사 자료가 변경되었습니다. 현재 근거로 다시 추천해 주세요.',409)
        else:
            candidates=current
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
                con.execute('UPDATE recommendation_runs SET candidates_json=?,manifest_hash=?,data_status=? WHERE id=?',
                    (dump(candidates),digest(candidates),'captured',ident))
        ctx.checkpoint({'candidate_snapshot':ident},stage='constraints_and_scoring',done=0,total=len(candidates))
        result=recommend(snapshot,candidates,now=datetime.fromisoformat(snapshot['evaluation_at']))
        result['external_discovery']={'state':'unavailable','reason':'EXTERNAL_DISCOVERY_NOT_CONFIGURED','calls':0,'cost':None}
        result['requested_constraints']={k:snapshot[k] for k in ('conditions','review_language_filter','rating_filter')}
        result.setdefault('applied_constraints',deepcopy(result['requested_constraints']))
        result.setdefault('unsupported_constraints',[])
        # No external work was performed, but both deletion and evidence may
        # still change concurrently with a CPU-bound calculation.
        if digest(self._catalog(actor,trip_id,snapshot['conditions']['city']))!=digest(candidates):
            raise DomainError('SOURCE_DATA_CHANGED','자료가 변경되어 이전 결과를 적용하지 않았습니다.',409)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
            self._guard_catalog(con,actor,trip_id,snapshot['conditions']['city'],candidates)
            con.execute("UPDATE recommendation_runs SET result_json=?,data_status='current',completed_at=? WHERE id=?",(dump(result),utcnow(),ident))
            from src.product.events import capture_run
            capture_run(con,actor.id,trip_id,ident,snapshot,candidates,result)
        ctx.progress('recommendation_complete',done=len(candidates),total=len(candidates))
        return {'run_id':ident}

    def get(self,actor,trip_id,ident,*,check_data=True):
        with self.db.connect() as con:
            row=dict(self._get(con,actor,trip_id,ident))
            cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            trip=con.execute('SELECT version FROM trips WHERE id=?',(trip_id,)).fetchone()
        snapshot=json.loads(row['snapshot_json']); result=json.loads(row['result_json']) if row['result_json'] else None
        if check_data and row['candidates_json'] and digest(self._catalog(actor,trip_id,snapshot['conditions']['city']))!=row['manifest_hash']:
            # Do not continue serving revoked/expired facts out of an old run.
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE');self._get(con,actor,trip_id,ident)
                con.execute("UPDATE recommendation_runs SET result_json=NULL,candidates_json=NULL,data_status='stale' WHERE id=?",(ident,))
            row['data_status']='stale';result=None
        job=self.jobs.get(row['job_id'],actor.id,actor.session_id)
        return {'run_id':ident,'job_id':row['job_id'],'state':job['state'],'job':job,
            'trip_version':row['trip_version'],'conditions_version':row['conditions_version'],
            'request':{k:snapshot[k] for k in ('trip_version','conditions_version','review_language_filter','rating_filter','limit') if k in snapshot},
            'error_code':job.get('error_code'),
            'conditions_snapshot':snapshot.get('conditions'),'snapshot':snapshot,
            'input_status':'current' if trip['version']==row['trip_version'] and (cv['version'] if cv else 0)==row['conditions_version'] else 'stale',
            'data_status':row['data_status'],'result':result,'created_at':row['created_at'],
            'completed_at':row['completed_at'],'reason_codes':['SOURCE_DATA_CHANGED'] if row['data_status']=='stale' else [],
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
            'input_status':run['input_status'],'data_status':run['data_status'],
            'items':[by_id[p] for p in json.loads(row['place_ids_json']) if p in by_id],
            'reason_codes':run['reason_codes']}

    def event(self,actor,trip_id,body):
        with self.db.connect() as con:
            self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
        return {'recorded':False,'reason':'LEGACY_EVENT_CONTRACT_DISABLED'}
