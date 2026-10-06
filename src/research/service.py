"""Scoped review research: audited rights, durable jobs and minimized receipts.

No review body, translation, reviewer identity or body hash is persisted here.
Only permitted IDs and classified observations survive a page checkpoint.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

from src.foundation.repository import DomainError
from src.providers.reviews import CollectionRequest, ProviderPage, ReviewProviderError
from src.reliability.budget import CallContext
from src.reliability.providers import ProviderResult
from src.reliability.dispatcher import RetryableJobError

SCOPE = 'review_catalog_v1'
RIGHTS = ('access','collect','calculate','raw_store','aggregate_store','id_store','llm','display')
REQUIRED = ('access','collect','calculate','aggregate_store','id_store')
CITIES = {'tokyo': ('ja',), 'barcelona': ('es','ca')}

def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',',':'), allow_nan=False)

def new_id(prefix):
    return prefix+'_'+uuid4().hex

def deny(code, message, status=409):
    raise DomainError(code,message,status)

def stamp(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None: raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        deny('VALIDATION_FAILED','시간에는 UTC offset이 필요합니다.',422)

def validate_place_url(url):
    """Identity evidence only. Never fetch/resolve URLs or follow short links."""
    try:
        parsed=urlsplit(url)
        port=parsed.port
    except ValueError: deny('PLACE_URL_INVALID','지점 URL을 확인해 주세요.',422)
    if parsed.scheme!='https' or parsed.hostname not in {'www.google.com','maps.google.com','google.com'} or port not in {None,443} or parsed.username or parsed.password or not parsed.path.startswith('/maps'):
        deny('PLACE_URL_INVALID','https Google Maps 지점 URL을 사용해 주세요. 단축 링크는 직접 확인한 뒤 넣어 주세요.',422)
    if len(url)>2000 or parsed.fragment: deny('PLACE_URL_INVALID','지점 URL을 확인해 주세요.',422)
    return url

class ReviewService:
    def __init__(self, db, repo, jobs, gateway, *, provider=None, detector=None, fault_hook=None):
        self.db,self.repo,self.jobs,self.gateway=db,repo,jobs,gateway
        self.jobs.admin_scopes=frozenset((*self.jobs.admin_scopes,SCOPE))
        if provider is None:
            from src.providers.apify_reviews import ApifyReviewCollectionProvider
            provider=ApifyReviewCollectionProvider(token=os.getenv('APIFY_TOKEN') or os.getenv('APIFY_API_TOKEN',''),build=os.getenv('APIFY_REVIEW_BUILD','0.0.527'))
        self.provider=provider
        self.detector=detector
        self.fault_hook=fault_hook

    def now(self): return self.jobs.clock().astimezone(timezone.utc)
    def _admin(self,con,actor):
        user=self.jobs._user(con,actor.id,actor.session_id)
        if user['role']!='admin': deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
    def _audit(self,con,actor,action,target,details):
        con.execute('INSERT INTO research_audit VALUES(?,?,?,?,?,?)',(new_id('audit'),actor.id,action,target,encoded(details),self.now().isoformat()))
    def controls(self,actor=None):
        with self.db.connect() as con:
            row=dict(con.execute('SELECT * FROM review_controls WHERE singleton=1').fetchone())
        return {**row,'research_enabled':bool(row['research_enabled']),'production_enabled':bool(row['production_enabled']),
                'provider':self.provider.name,'adapter_version':self.provider.adapter_version,
                'provider_configured':self.provider.name=='fake' or bool(getattr(self.provider,'configured',False)),
                'pricing_configured':self.gateway.budget.policy.valid,'pricing_checked_at':self.gateway.budget.policy.config.get('confirmed_at'),'price_version':self.gateway.budget.policy.config.get('version'),
                'budget_status':self.budget_status(actor.id) if actor else [],
                'strict_description':'최근 관측 범위만 설명합니다. 주민 비율·전체 리뷰 비율이 아닙니다.'}
    def set_controls(self,actor,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            old=con.execute('SELECT * FROM review_controls').fetchone()
            if old['version']!=body['expected_version']: deny('VERSION_CONFLICT','설정을 새로 불러와 주세요.')
            if body['production_enabled']:
                if not body['research_enabled'] or self.provider.name=='fake': deny('PRODUCTION_QUALITY_UNVERIFIED','합성 공급자 또는 미검증 데이터는 운영으로 활성화할 수 없습니다.')
                for city in CITIES:
                    if not con.execute('SELECT 1 FROM review_quality_evaluations WHERE city=? AND passed=1 AND synthetic=0 AND expires_at>?',(city,self.now().isoformat())).fetchone():
                        deny('CLASSIFICATION_QUALITY_UNVERIFIED','도시별 독립 리뷰 평가가 먼저 필요합니다.')
                if not con.execute("SELECT 1 FROM review_aggregates a JOIN provider_policies p ON p.id=a.policy_id WHERE a.invalidated_at IS NULL AND a.expires_at>? AND p.status='active' AND json_extract(a.evaluation_json,'$.strict_pass')=1",(self.now().isoformat(),)).fetchone():
                    deny('PRODUCTION_QUALITY_UNVERIFIED','사용 권한과 품질을 통과한 실제 수집 결과가 필요합니다.')
            con.execute('UPDATE review_controls SET research_enabled=?,production_enabled=?,version=version+1',(body['research_enabled'],body['production_enabled']))
            self._audit(con,actor,'controls_changed',SCOPE,body)
        return self.controls(actor)
    def add_policy(self,actor,body):
        if set(body['rights'])!=set(RIGHTS) or any(type(v) is not bool for v in body['rights'].values()): deny('POLICY_INVALID','이용 범위를 항목별로 확인해 주세요.',422)
        now=self.now(); reviewed=stamp(body['reviewed_at']); expires=stamp(body['expires_at'])
        if not reviewed<=now<expires or expires>now+timedelta(days=366): deny('POLICY_INVALID','정책 검토일과 유효기간을 확인해 주세요.',422)
        ident=new_id('policy')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            if con.execute('SELECT 1 FROM provider_policies WHERE provider=? AND version=?',(body['provider'],body['version'])).fetchone(): deny('VERSION_CONFLICT','이미 등록한 정책 버전입니다.')
            con.execute('INSERT INTO provider_policies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,body['provider'],body['version'],'active',encoded(body['rights']),encoded(body['evidence']),body['purpose'],reviewed.isoformat(),expires.isoformat(),body['aggregate_ttl_seconds'],body['id_ttl_seconds'],body.get('remote_raw_ttl_seconds',0),actor.id,now.isoformat()))
            self._audit(con,actor,'policy_reviewed',ident,{'version':body['version'],'rights':body['rights'],'evidence':body['evidence']})
        return {'id':ident,**body,'status':'active'}
    def policies(self,actor):
        with self.db.connect() as con:
            self._admin(con,actor); rows=con.execute('SELECT * FROM provider_policies ORDER BY created_at DESC').fetchall()
        return [{**dict(r),'rights':json.loads(r['rights_json']),'evidence':json.loads(r['evidence_json'])} for r in rows]
    def add_place(self,actor,body):
        validate_place_url(body['source_url'])
        ident=new_id('place'); now=self.now().isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            old=con.execute('SELECT * FROM place_identities WHERE provider=? AND external_place_id=?',(body['provider'],body['external_place_id'])).fetchone()
            if old:
                if old['deleted_at']: deny('PLACE_DELETED','삭제한 지점은 새 지점 확인 절차가 필요합니다.')
                return self._place_dto(old)
            con.execute('INSERT INTO place_identities(id,provider,external_place_id,city,name,address,source_url,rating,total_rating_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (ident,body['provider'],body['external_place_id'],body['city'],body['name'],body['address'],body['source_url'],body.get('rating'),body.get('total_rating_count'),now,now))
            self._audit(con,actor,'place_discovered',ident,{'method':'manual_verified_candidate','source_url':body['source_url']})
            return self._place_dto(con.execute('SELECT * FROM place_identities WHERE id=?',(ident,)).fetchone())
    @staticmethod
    def _place_dto(row):
        keys=('id','provider','external_place_id','city','name','address','source_url','identity_status','version','rating','total_rating_count')
        return {**{k:row[k] for k in keys},'status':row['identity_status']}
    def verify_place(self,actor,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            row=con.execute('SELECT * FROM place_identities WHERE id=? AND deleted_at IS NULL',(ident,)).fetchone()
            if not row: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            if row['version']!=body['expected_version']: deny('VERSION_CONFLICT','지점 정보를 다시 확인해 주세요.')
            con.execute('UPDATE place_identities SET identity_status=?,identity_evidence=?,version=version+1,updated_at=? WHERE id=?',(body['status'],body['evidence'],self.now().isoformat(),ident))
            con.execute("UPDATE review_aggregates SET invalidated_at=?,invalidation_reason='PLACE_IDENTITY_CHANGED' WHERE place_id=?",(self.now().isoformat(),ident))
            self._audit(con,actor,'place_identity_reviewed',ident,body)
            return self._place_dto(con.execute('SELECT * FROM place_identities WHERE id=?',(ident,)).fetchone())
    def places(self,actor,trip_id=None):
        if trip_id: self.repo.get_trip(actor.id,trip_id)
        with self.db.connect() as con:
            if trip_id:
                rows=con.execute('SELECT p.* FROM place_identities p JOIN trip_places t ON t.place_id=p.id WHERE t.trip_id=? AND p.deleted_at IS NULL ORDER BY p.name',(trip_id,)).fetchall()
            else:
                self._admin(con,actor); rows=con.execute('SELECT * FROM place_identities WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT 200').fetchall()
            return [self._place_dto(r) for r in rows]
    def link(self,actor,trip_id,place_id):
        self.repo.get_trip(actor.id,trip_id)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if not con.execute('SELECT 1 FROM trips WHERE id=? AND owner_id=? AND deleted_at IS NULL',(trip_id,actor.id)).fetchone() or not con.execute("SELECT 1 FROM place_identities WHERE id=? AND identity_status='verified' AND deleted_at IS NULL",(place_id,)).fetchone(): deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            con.execute('INSERT OR IGNORE INTO trip_places VALUES(?,?,?)',(trip_id,place_id,self.now().isoformat()))
        return {'place_id':place_id,'trip_id':trip_id}
    def _eligible(self,con,place_id,policy_id):
        place=con.execute('SELECT * FROM place_identities WHERE id=? AND deleted_at IS NULL',(place_id,)).fetchone()
        if not place: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
        if place['identity_status']!='verified': deny('PLACE_IDENTITY_UNCONFIRMED','지점·주소·외부 ID를 먼저 확인해 주세요.')
        policy=con.execute("SELECT * FROM provider_policies WHERE id=? AND status='active' AND expires_at>?",(policy_id,self.now().isoformat())).fetchone()
        if not policy or policy['provider']!=place['provider'] or policy['provider']!=self.provider.name: deny('REVIEW_POLICY_UNAVAILABLE','현재 공급자의 이용 범위 검토가 필요합니다.')
        rights=json.loads(policy['rights_json'])
        if any(not rights.get(k) for k in REQUIRED): deny('REVIEW_POLICY_UNAVAILABLE','수집·계산·집계·ID 보관 권한을 각각 확인해 주세요.')
        if self.provider.name=='apify' and not re.fullmatch(r'(?:ChIJ|GhIJ)[A-Za-z0-9_-]{23}',place['external_place_id']): deny('PLACE_IDENTITY_UNCONFIRMED','현재 Apify 계약에 맞는 Google 지점 ID를 확인해 주세요.')
        if self.provider.name=='apify' and not getattr(self.provider,'configured',False): deny('PROVIDER_NOT_CONFIGURED','Apify 공급자 키가 설정되지 않았습니다.',503)
        if self.provider.name=='apify' and (not rights.get('raw_store') or policy['remote_raw_ttl_seconds']<=0): deny('REMOTE_RETENTION_UNREVIEWED','공급자 측 원문 보관과 삭제 기한을 먼저 검토해 주세요.')
        if not con.execute('SELECT research_enabled FROM review_controls').fetchone()[0]: deny('REVIEW_RESEARCH_DISABLED','관리자 연구 수집이 꺼져 있습니다.')
        return place,policy
    def submit(self,actor,body,key):
        fingerprint=hashlib.sha256(encoded(body).encode()).hexdigest()
        existing=self.jobs.lookup(actor.id,actor.session_id,'admin_research',SCOPE,'review_collection',key,fingerprint)
        if existing: return self._submission(existing)
        ident=new_id('review'); now=self.now()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            place,policy=self._eligible(con,body['place_id'],body['policy_id'])
            job=self.jobs.enqueue(actor.id,actor.session_id,'admin_research',SCOPE,'review_collection',{'run_id':ident,'detector_version':self._detector_version()},place['version'],key,request_fingerprint=fingerprint,deadline_seconds=body['max_elapsed_seconds']+60,con=con)
            if con.execute('SELECT 1 FROM review_collection_runs WHERE job_id=?',(job['id'],)).fetchone(): return self._submission(job,con)
            request=CollectionRequest(place_identity_id=place['id'],external_place_id=place['external_place_id'],provider=self.provider.name,adapter_version=self.provider.adapter_version,
                requested_start=(now-timedelta(days=180)).isoformat(),requested_end=now.isoformat(),policy_version=policy['version'],actor_id=actor.id,job_id=job['id'],idempotency_key=hashlib.sha256(key.encode()).hexdigest(),verified_place_url=place['source_url'],max_review_records=body['max_review_records'],max_pages=body['max_pages'],max_elapsed_seconds=body['max_elapsed_seconds'],max_total_charge_usd=body['max_total_charge_usd'])
            expiry=min(now+timedelta(seconds=min(policy['id_ttl_seconds'],policy['aggregate_ttl_seconds'])),stamp(policy['expires_at']))
            con.execute('INSERT INTO review_collection_runs(id,job_id,actor_id,place_id,place_version,policy_id,provider,adapter_version,request_json,input_hash,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,job['id'],actor.id,place['id'],place['version'],policy['id'],self.provider.name,self.provider.adapter_version,encoded(request.to_dict()),request.request_hash,now.isoformat(),expiry.isoformat()))
            self._audit(con,actor,'collection_requested',ident,{'max_review_records':request.max_review_records,'max_total_charge_usd':request.max_total_charge_usd,'policy_id':policy['id']})
        return self._submission(job)
    def _submission(self,job,con=None):
        if con is None:
            with self.db.connect() as own: return self._submission(job,own)
        row=con.execute('SELECT id FROM review_collection_runs WHERE job_id=?',(job['id'],)).fetchone()
        return {'job_id':job['id'],'run_id':row['id'],'state':job['state'],'status_url':'/api/v2/admin/review-collection-runs/'+row['id'],'events_url':'/api/v2/jobs/'+job['id']+'/events'}
    def _guard(self,run,ctx,con=None):
        if con is None:
            with self.db.connect() as own: return self._guard(run,ctx,own)
        ctx.guard(con=con)
        current=con.execute('SELECT * FROM review_collection_runs WHERE id=? AND deleted_at IS NULL',(run['id'],)).fetchone()
        if not current: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
        if current['expires_at']<=self.now().isoformat(): deny('REVIEW_RETENTION_EXPIRED','보관 기간이 만료되어 수집을 중지했습니다.')
        place,policy=self._eligible(con,run['place_id'],run['policy_id'])
        if place['version']!=run['place_version']: deny('VERSION_CONFLICT','지점 검증 정보가 바뀌었습니다.')
        if self.provider.adapter_version!=run['adapter_version']: deny('ADAPTER_VERSION_CHANGED','수집 어댑터가 변경되어 새 검증이 필요합니다.')
        return place,policy

    def execute(self,job,ctx):
        from src.research.calls import ResearchCalls
        from src.research.collection import collect_reviews
        from src.research.normalization import normalize_review
        from src.research.metrics import aggregate_observations, evaluate_review_signal
        with self.db.connect() as con:
            row=con.execute('SELECT * FROM review_collection_runs WHERE job_id=?',(job['id'],)).fetchone()
        if not row: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
        run=dict(row)
        if run['deleted_at']: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
        request=CollectionRequest(**json.loads(run['request_json']))
        def guard(con=None): return self._guard(run,ctx,con)
        place,policy=guard()
        if job['payload'].get('detector_version')!=self._detector_version(): deny('DETECTOR_VERSION_CHANGED','언어 판별기 버전이 바뀌어 새 관측 실행이 필요합니다.')
        with self.db.connect() as con:
            committed=con.execute('SELECT id,evaluation_json,coverage_json FROM review_aggregates WHERE run_id=? AND invalidated_at IS NULL',(run['id'],)).fetchone()
        if committed:
            coverage=json.loads(committed['coverage_json']); evaluation=json.loads(committed['evaluation_json'])
            return {'state':'partial' if coverage.get('partial') else 'succeeded','result':{'run_id':run['id'],'aggregate_id':committed['id'],'decision':evaluation['decision'],'stop_reason':coverage.get('stop_reason')}}
        context=CallContext(owner_id=job['actor_id'],actor_id=job['actor_id'],scope_kind='admin_research',scope_id=SCOPE,job_id=job['id'])
        calls=ResearchCalls(self,run,context,guard)
        if self.provider.name!='fake':
            calls.reserve_cleanup()
        detector=self.detector
        if detector is None:
            from src.research.language import LocalLanguageDetector
            self.detector=detector=LocalLanguageDetector()
        def call(operation,key,fn,project=lambda x:x,attempt=1):
            if (self.now()-stamp(run['created_at'])).total_seconds()>=request.max_elapsed_seconds: deny('REVIEW_TIME_CAP','전체 수집 실행 시간 상한에 도달했습니다.')
            digest=hashlib.sha256((request.request_hash+':'+key).encode()).hexdigest()
            return calls.run_call(operation,key,digest,fn,project=project,attempt=attempt)
        def metadata(value):
            # Reject arbitrary provider metadata: never persist tokens or responses.
            return {k:value[k] for k in ('run_id','dataset_id','status','usage','cost_actual_usd','usage_usd','usage_final','charged_event_counts','finished_at','started_at','limitations') if k in value}
        checkpoint=ctx.job['checkpoint']
        remote=checkpoint.get('remote')
        if not remote:
            ctx.progress('review_start',done=0,total=request.max_review_records)
            from dataclasses import replace
            remote_request=replace(request,max_total_charge_usd=calls.actor_charge_ceiling()) if self.provider.name=='apify' else request
            def start_metadata(value):
                minimal=metadata(value)
                if self.provider.name!='fake': self._schedule_remote_cleanup(run,minimal,policy)
                return minimal
            remote=call('review_start','start',lambda:self.provider.start(remote_request),start_metadata)
            ctx.checkpoint({'remote':remote},stage='provider_running')
        if self.provider.name!='fake':
            self._schedule_remote_cleanup(run,remote,policy)
        poll_index=ctx.job['checkpoint'].get('poll_index',0)
        while remote.get('status') not in {'SUCCEEDED','succeeded'}:
            guard()
            if (self.now()-stamp(run['created_at'])).total_seconds()>=request.max_elapsed_seconds or poll_index>=150:
                deny('REVIEW_TIME_CAP','공급자 실행 시간 상한에 도달했습니다.')
            remote=call('review_poll',f'poll:{poll_index}',lambda:self.provider.poll(remote,request),metadata)
            poll_index+=1
            if self.provider.name!='fake': self._schedule_remote_cleanup(run,remote,policy)
            ctx.checkpoint({'remote':remote,'poll_index':poll_index},stage='provider_running')
            if remote.get('status') in {'READY','RUNNING','PENDING','running','queued'}:
                import time
                time.sleep(1)
            elif remote.get('status') not in {'SUCCEEDED','succeeded'}:
                deny('REVIEW_PROVIDER_FAILED','공급자 수집이 완료되지 않았습니다.')
        if self.provider.name!='fake' and remote.get('usage_final') is True and remote.get('cost_actual_usd') is not None:
            self._settle_remote_run(run,remote['cost_actual_usd'])
        def transform(record):
            result=normalize_review(record,run_id=run['id'],place_id=run['place_id'],provider=self.provider.name,fetched_at=self.now().isoformat(),detector=detector,policy_version=policy['version'])
            # Never persist body/translation or body fingerprint, even if raw_store is allowed.
            return {k:v for k,v in result.items() if k not in {'original_text','translated_text','dedupe_key','original_text_ref'}}
        def fetch(cursor,attempt):
            key='page:'+str(cursor or 'first')
            def project(page):
                data=page.to_dict()
                try: data['records']=[transform(r) for r in page.records]
                except (ValueError,TypeError): raise ReviewProviderError('parse_error') from None
                return data
            try: value=call('review_page',key,lambda:self.provider.fetch_page(remote,cursor,request),project,attempt)
            except DomainError as exc:
                if exc.code=='REVIEW_TIME_CAP': raise ReviewProviderError('time_cap') from None
                if exc.code in {'BUDGET_EXHAUSTED','GLOBAL_BUDGET_EXHAUSTED','GLOBAL_BUDGET_STOPPED'}: raise ReviewProviderError('budget_cap') from None
                raise
            return ProviderPage(**value)
        def save(state):
            guard(); ctx.checkpoint({'collection':state},stage='review_pages',done=len(state.get('records',[])),total=request.max_review_records)
        result=collect_reviews(request,fetch,transform=lambda r:r,checkpoint=save,initial=ctx.job['checkpoint'].get('collection'),guard=guard)
        expected=job['payload']['detector_version']
        if any(r.get('language') and r.get('detector_version')!=expected for r in result.records): deny('DETECTOR_VERSION_CHANGED','서로 다른 판별 버전의 관측을 합칠 수 없습니다.')
        stats=aggregate_observations(result.records,CITIES[place['city']])
        now=self.now(); expiry=min(now+timedelta(seconds=policy['aggregate_ttl_seconds']),stamp(policy['expires_at']),stamp(run['expires_at']))
        detector_version=getattr(detector,'version',None) or next((r.get('detector_version') for r in result.records if r.get('detector_version')),'unverified')
        with self.db.connect() as con:
            quality=con.execute('SELECT * FROM review_quality_evaluations WHERE city=? AND detector_version=? AND expires_at>? ORDER BY checked_at DESC LIMIT 1',(place['city'],detector_version,now.isoformat())).fetchone()
        if quality and (not quality['passed'] or quality['synthetic']): quality=None
        if quality: expiry=min(expiry,stamp(quality['expires_at']))
        coverage=result.coverage
        coverage.update(fetched_count=coverage['received_count'],pages_count=coverage['pages'],ordering_semantics=coverage['sort_basis'],boundary_time_field=coverage['sort_basis'],continuity_status='verified_supplier_window' if coverage['continuity_verified'] else 'unverified',observed_oldest_at=coverage['observed_start'],observed_newest_at=coverage['observed_end'])
        payload={'collection_mode':'observed_window','place_identity_confirmed':True,'local_languages':list(CITIES[place['city']]),**stats,
            'rights':{'access_confirmed':True,'compute_confirmed':True,'display_confirmed':json.loads(policy['rights_json'])['display']},
            'freshness':{'state':'valid'},'quality':{k:'passed' if quality else 'unverified' for k in ('language_evaluation','korean_recall_validation','local_precision_validation')},
            'window':{'sort':'newest','ordering_semantics':coverage.get('ordering_semantics',coverage.get('sort_basis','unknown')),
                'boundary_time_field':coverage.get('sort_basis','unknown'),
                'continuity_status':'verified_supplier_window' if coverage.get('continuity_verified') else 'unverified','stop_reason':coverage.get('stop_reason'),
                'locale_filter':None,'keyword_filter':None,'rating_filter':None}}
        evaluation=evaluate_review_signal(payload)
        evaluation['reason_codes']=list(dict.fromkeys(evaluation['reason_codes']+result.reason_codes))
        ident=new_id('aggregate')
        summary={'coverage':coverage,**stats,'evaluation':evaluation,'detector_version':detector_version,'synthetic':self.provider.name=='fake'}
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); guard(con=con)
            con.execute('INSERT INTO review_aggregates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,run['id'],place['id'],policy['id'],encoded(evaluation),encoded(coverage),encoded(stats),detector_version,evaluation['config_version'],now.isoformat(),expiry.isoformat(),None,None,now.isoformat()))
            con.execute('UPDATE review_collection_runs SET finished_at=?,summary_json=? WHERE id=?',(now.isoformat(),encoded(summary),run['id']))
            # A failed/partial refresh never replaces an older eligible result.
            if result.state=='succeeded' and evaluation['decision'] in {'pass','fail'}:
                con.execute('UPDATE place_identities SET active_aggregate_id=? WHERE id=?',(ident,place['id']))
            elif not place['active_aggregate_id'] and result.state=='succeeded':
                con.execute('UPDATE place_identities SET active_aggregate_id=? WHERE id=?',(ident,place['id']))
        if self.fault_hook: self.fault_hook('review_aggregate_committed',run['id'])
        if self.provider.name!='fake':
            self.cleanup_remote(run['id'])
        return {'state':result.state,'result':{'run_id':run['id'],'aggregate_id':ident,'decision':evaluation['decision'],'stop_reason':coverage.get('stop_reason')}}

    def _settle_remote_run(self,run,cost):
        amount=Decimal(str(cost))
        if not amount.is_finite() or amount<0: return
        with self.db.connect() as con:
            row=con.execute("SELECT call_id,price_rates_json FROM usage_reservations WHERE job_id=? AND operation='review_start'",(run['job_id'],)).fetchone()
        if row and set(json.loads(row['price_rates_json']))=={'usd_micros'}:
            self.gateway.budget.settle(row['call_id'],{'usd_micros':int((amount*1000000).to_integral_value(rounding='ROUND_CEILING'))},result_ref='review:'+run['id'])

    def usage(self,job_id):
        with self.db.connect() as con:
            rows=con.execute('SELECT currency,state,estimated_cost_micros,actual_cost_micros,price_version,price_confirmed_at FROM usage_reservations WHERE job_id=?',(job_id,)).fetchall()
        return {'currencies':[{ 'currency':cur,'reserved_micros':sum(r['estimated_cost_micros'] for r in rows if r['currency']==cur and r['state'] not in {'settled','released'}),
                    'actual_micros':sum(r['actual_cost_micros'] or 0 for r in rows if r['currency']==cur and r['state']=='settled'),
                    'unknown':any(r['state'] in {'sent','unknown','pending_reconciliation'} for r in rows if r['currency']==cur)} for cur in sorted({r['currency'] for r in rows})],
                'calls':len(rows),'price_checked_at':max((r['price_confirmed_at'] for r in rows),default=None)}
    def get_run(self,actor,ident):
        self.purge()
        with self.db.connect() as con:
            self._admin(con,actor)
            row=con.execute('SELECT * FROM review_collection_runs WHERE id=? AND actor_id=?',(ident,actor.id)).fetchone()
            if not row: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            place=con.execute('SELECT * FROM place_identities WHERE id=?',(row['place_id'],)).fetchone()
        if row['deleted_at']:
            with self.db.connect() as con:
                item=con.execute('SELECT id,state,error_code,cancel_requested_at FROM jobs WHERE id=?',(row['job_id'],)).fetchone()
                job=dict(item)
        else: job=self.jobs.get(row['job_id'],actor.id,actor.session_id)
        summary=json.loads(row['summary_json']) if row['summary_json'] else {}
        with self.db.connect() as con:
            cleanup=con.execute('SELECT state,deadline_at,error_code,dataset_deleted,run_deleted,attempts FROM review_remote_cleanup WHERE run_id=?',(ident,)).fetchone()
        result={'run_id':ident,'place':self._place_dto(place),'state':job['state'],'job':job,'request':json.loads(row['request_json']),
            'remote_cleanup':dict(cleanup) if cleanup else None,'checked_at':row['finished_at'],'expires_at':row['expires_at'],'usage':self.usage(row['job_id']),'product_status':'sampled' if summary else 'access_reviewed',
            'synthetic':row['provider']=='fake','coverage':None,'counts':None,'evaluation':None,**summary,
            'provenance':{'provider':row['provider'],'adapter_version':row['adapter_version'],'input_hash':row['input_hash'],'policy_id':row['policy_id']},
            'freshness':{'checked_at':row['finished_at'],'expires_at':row['expires_at']}}
        if summary and summary.get('evaluation',{}).get('decision') in {'pass','fail'}:
            result['product_status']='quality_reviewed'
            if self.controls()['production_enabled'] and not row['deleted_at']: result['product_status']='production_enabled'
        if row['deleted_at']:
            result.update(coverage=None,counts=None,evaluation={'decision':'unsupported','strict_pass':False,'reason_codes':['REVIEW_RETENTION_OR_POLICY_WITHDRAWN']},request={},product_status='unavailable')
        return result
    def list_runs(self,actor):
        with self.db.connect() as con:
            self._admin(con,actor); ids=[r['id'] for r in con.execute('SELECT id FROM review_collection_runs WHERE actor_id=? ORDER BY created_at DESC LIMIT 100',(actor.id,))]
        return [self.get_run(actor,i) for i in ids]
    def evidence(self,actor,trip_id,place_id):
        self.repo.get_trip(actor.id,trip_id); self.purge()
        with self.db.connect() as con:
            place=con.execute('SELECT p.* FROM place_identities p JOIN trip_places t ON t.place_id=p.id WHERE t.trip_id=? AND p.id=? AND p.deleted_at IS NULL',(trip_id,place_id)).fetchone()
            if not place: deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            aggregate=con.execute('SELECT * FROM review_aggregates WHERE id=?',(place['active_aggregate_id'],)).fetchone()
            controls=con.execute('SELECT * FROM review_controls').fetchone()
            policy=con.execute('SELECT * FROM provider_policies WHERE id=?',(aggregate['policy_id'],)).fetchone() if aggregate else None
            run=con.execute('SELECT provider FROM review_collection_runs WHERE id=?',(aggregate['run_id'],)).fetchone() if aggregate else None
        reasons=[]
        if not aggregate: reasons.append('NO_REVIEW_OBSERVATION')
        if not controls['production_enabled']: reasons.append('REVIEW_PRODUCTION_DISABLED')
        if place['identity_status']!='verified': reasons.append('PLACE_IDENTITY_UNCONFIRMED')
        if policy and (policy['status']!='active' or policy['expires_at']<=self.now().isoformat() or not json.loads(policy['rights_json']).get('display')): reasons.append('DISPLAY_RIGHTS_UNAVAILABLE')
        if aggregate and (aggregate['invalidated_at'] or aggregate['expires_at']<=self.now().isoformat()): reasons.append(aggregate['invalidation_reason'] or 'REVIEW_STALE')
        available=bool(aggregate and not reasons)
        ev=json.loads(aggregate['evaluation_json']) if available else {'decision':'unsupported','strict_pass':False,'reason_codes':reasons,'metrics':None}
        if available and ev['decision'] not in {'pass','fail'}: reasons.extend(ev['reason_codes'])
        # Counts can explain an eligible observation even when classification is unverified.
        return {'place':self._place_dto(place),'state':'available' if available and not reasons else ('empty' if not aggregate else 'stale' if 'REVIEW_STALE' in reasons else 'unavailable'),
            'counts':json.loads(aggregate['counts_json'])['counts'] if available else None,
            'metrics':ev.get('metrics') if available and ev['decision'] in {'pass','fail'} else None,
            'evaluation':{**ev,'reason_codes':list(dict.fromkeys(ev['reason_codes']+reasons))},
            'coverage':json.loads(aggregate['coverage_json']) if available else None,'checked_at':aggregate['computed_at'] if available else None,
            'expires_at':aggregate['expires_at'] if available else None,'synthetic':bool(run and run['provider']=='fake'),
            'aggregate_id':aggregate['id'] if available else None,
            'platform':'google_maps' if run and run['provider']=='apify' else 'synthetic' if run and run['provider']=='fake' else None,
            'attribution':{'label':'Google Maps · 지점 정보 확인','url':place['source_url']},
            'scope_mode':'observed_window','population_inference_allowed':False,'residency_inference_allowed':False}

    def revoke_policy(self,actor,ident):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            if not con.execute('SELECT 1 FROM provider_policies WHERE id=?',(ident,)).fetchone(): deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            con.execute("UPDATE provider_policies SET status='revoked' WHERE id=?",(ident,))
            con.execute('INSERT OR REPLACE INTO research_tombstones VALUES(?,?,?,?,NULL)',('policy',ident,'POLICY_REVOKED',self.now().isoformat()))
            self._audit(con,actor,'policy_revoked',ident,{})
        self.purge()
        return {'id':ident,'status':'revoked'}
    def delete_place(self,actor,ident):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            if not con.execute('SELECT 1 FROM place_identities WHERE id=?',(ident,)).fetchone(): deny('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            con.execute('UPDATE place_identities SET deleted_at=?,identity_status=\'blocked\',version=version+1 WHERE id=?',(self.now().isoformat(),ident))
            con.execute('INSERT OR REPLACE INTO research_tombstones VALUES(?,?,?,?,NULL)',('place',ident,'PLACE_DELETED',self.now().isoformat()))
            self._audit(con,actor,'place_deleted',ident,{})
        self.purge()
        return {'id':ident,'deleted':True}
    def purge(self):
        """Idempotent deletion with persistent markers; also enforced after restore.

        SQLite backups contain no raw text. Old snapshot restores must merge this
        tombstone manifest before serving; startup alone cannot invent lost markers.
        """
        now=self.now().isoformat(); count=0
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            # Reapply authoritative markers even if restored rows still say active.
            con.execute("UPDATE provider_policies SET status='revoked' WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='policy')")
            con.execute("UPDATE place_identities SET deleted_at=COALESCE(deleted_at,?),identity_status='blocked' WHERE id IN (SELECT target_id FROM research_tombstones WHERE target_type='place')",(now,))
            rows=con.execute("SELECT r.* FROM review_collection_runs r JOIN provider_policies p ON p.id=r.policy_id JOIN place_identities i ON i.id=r.place_id WHERE r.expires_at<=? OR p.expires_at<=? OR p.status!='active' OR i.deleted_at IS NOT NULL OR i.identity_status!='verified' OR i.version!=r.place_version OR r.id IN (SELECT target_id FROM research_tombstones WHERE target_type='run') OR r.id IN (SELECT run_id FROM review_aggregates WHERE expires_at<=?)",(now,now,now)).fetchall()
            for row in rows:
                reason='REVIEW_RETENTION_OR_POLICY_WITHDRAWN'
                con.execute('INSERT OR IGNORE INTO research_tombstones VALUES(?,?,?,?,?)',('run',row['id'],reason,now,now))
                con.execute("DELETE FROM review_call_receipts WHERE run_id=? AND call_id NOT IN (SELECT call_id FROM usage_reservations WHERE operation IN ('review_delete_dataset','review_delete_run','review_abort'))",(row['id'],))
                con.execute("UPDATE jobs SET checkpoint_json='{}' WHERE id=?",(row['job_id'],))
                con.execute("UPDATE review_collection_runs SET deleted_at=COALESCE(deleted_at,?),summary_json=NULL,request_json='{}' WHERE id=?",(now,row['id']))
                con.execute("UPDATE review_aggregates SET invalidated_at=COALESCE(invalidated_at,?),invalidation_reason=?,counts_json='{}',coverage_json='{}',evaluation_json='{}' WHERE run_id=?",(now,reason,row['id']))
                count+=1
            con.execute("UPDATE review_aggregates SET invalidated_at=COALESCE(invalidated_at,?),invalidation_reason=COALESCE(invalidation_reason,'REVIEW_STALE') WHERE expires_at<=?",(now,now))
            con.execute('UPDATE research_tombstones SET completed_at=COALESCE(completed_at,?)',(now,))
        return {'expired_or_withdrawn_runs':count,'raw_records_stored':0}

    def record_quality(self,actor,body):
        # Admin attests an immutable independently labelled restaurant-review report.
        # Generic multilingual corpus diagnostics and synthetic fixtures never pass.
        cities=body['city']; local=body['local']; korean=body['korean']
        def valid_confusion(v):
            return all(type(v.get(k)) is int and v[k]>=0 for k in ('tp','fp','fn'))
        if not valid_confusion(local) or not valid_confusion(korean): deny('VALIDATION_FAILED','평가 TP/FP/FN을 확인해 주세요.',422)
        if any(v['tp']+v['fn']>body['label_count'] or v['tp']+v['fp']>body['label_count'] for v in (local,korean)) or body['original_translation_errors']>body['original_checks']: deny('VALIDATION_FAILED','평가 합계와 라벨 수가 맞지 않습니다.',422)
        precision=local['tp']/(local['tp']+local['fp']) if local['tp']+local['fp'] else None
        recall=korean['tp']/(korean['tp']+korean['fn']) if korean['tp']+korean['fn'] else None
        passed=bool(not body['synthetic'] and body['domain']=='restaurant_reviews' and body['heldout_disjoint'] and body['label_count']>=100 and body['original_checks']>=20 and body['original_translation_errors']==0 and precision is not None and precision>=.95 and recall is not None and recall>=.95)
        ident=new_id('quality'); now=self.now(); expiry=stamp(body['expires_at'])
        if not now<expiry<=now+timedelta(days=90): deny('VALIDATION_FAILED','평가 유효기간은 최대 90일입니다.',422)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self._admin(con,actor)
            con.execute('INSERT INTO review_quality_evaluations VALUES(?,?,?,?,?,?,?,?,?)',(ident,cities,body['detector_version'],encoded(body),passed,body['synthetic'],actor.id,now.isoformat(),expiry.isoformat()))
            if not passed:
                con.execute("UPDATE review_aggregates SET invalidated_at=?,invalidation_reason='CLASSIFICATION_QUALITY_UNVERIFIED' WHERE detector_version=? AND place_id IN (SELECT id FROM place_identities WHERE city=?)",(now.isoformat(),body['detector_version'],cities))
            self._audit(con,actor,'quality_reviewed',ident,{'report_sha256':body['report_sha256'],'passed':passed})
        return {'id':ident,'passed':passed,'local_precision':precision,'korean_recall':recall,'reason_codes':[] if passed else ['CLASSIFICATION_QUALITY_UNVERIFIED']}
    def _schedule_remote_cleanup(self,run,remote,policy):
        minimal={k:remote[k] for k in ('run_id','dataset_id','status') if remote.get(k)}
        deadline=min(stamp(run['created_at'])+timedelta(seconds=policy['remote_raw_ttl_seconds']),stamp(policy['expires_at']))
        with self.db.connect() as con:
            con.execute("INSERT INTO review_remote_cleanup(run_id,remote_json,deadline_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET remote_json=excluded.remote_json,updated_at=excluded.updated_at WHERE review_remote_cleanup.state!='succeeded'",(run['id'],encoded(minimal),deadline.isoformat(),self.now().isoformat()))

    def cleanup_remote(self,run_id=None):
        """Only deletion rights survive a withdrawal; no read/collect can run here.

        Both DELETE call costs are reserved before starting the remote actor. A
        durable outbox keeps the minimal remote references until deletion is
        confirmed; failure remains visible, including inactive billing actors.
        """
        from src.research.calls import ResearchCalls
        now=self.now(); results=[]
        with self.db.connect() as con:
            ids=[r[0] for r in con.execute("SELECT c.run_id FROM review_remote_cleanup c JOIN review_collection_runs r ON r.id=c.run_id JOIN jobs j ON j.id=r.job_id WHERE c.state!='succeeded' AND c.attempts<3 AND (c.lease_until IS NULL OR c.lease_until<?) AND (CAST(? AS TEXT) IS NOT NULL AND c.run_id=? OR CAST(? AS TEXT) IS NULL AND (c.deadline_at<=? OR j.state IN ('succeeded','partial','failed','cancelled')))",(now.isoformat(),run_id,run_id,run_id,now.isoformat()))]
        for ident in ids:
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                changed=con.execute("UPDATE review_remote_cleanup SET state='running',attempts=attempts+1,lease_until=?,updated_at=? WHERE run_id=? AND state!='succeeded' AND attempts<3 AND (lease_until IS NULL OR lease_until<?)",((now+timedelta(seconds=90)).isoformat(),now.isoformat(),ident,now.isoformat())).rowcount
                if not changed: continue
                row=con.execute('SELECT * FROM review_collection_runs WHERE id=?',(ident,)).fetchone(); run=dict(row)
                pending=dict(con.execute('SELECT * FROM review_remote_cleanup WHERE run_id=?',(ident,)).fetchone())
            def deletion_guard(con=None):
                if con is None:
                    with self.db.connect() as own: return deletion_guard(own)
                # Scope authorizes the original admin and only this recorded outbox.
                self.gateway.budget._scope(con,context)
                if not con.execute('SELECT 1 FROM review_remote_cleanup WHERE run_id=? AND state=\'running\' AND lease_until>?',(ident,self.now().isoformat())).fetchone(): deny('LEASE_LOST','삭제 실행 권한이 만료되었습니다.')
            context=CallContext(owner_id=run['actor_id'],actor_id=run['actor_id'],scope_kind='admin_research',scope_id=SCOPE,job_id=run['job_id'])
            calls=ResearchCalls(self,run,context,deletion_guard)
            try:
                # Purged request contains no review IDs; only finite transport bounds are required for DELETE.
                body=json.loads(run['request_json'])
                if not body:
                    body=dict(place_identity_id=run['place_id'],external_place_id='deleted',provider=run['provider'],adapter_version=run['adapter_version'],requested_start=(now-timedelta(days=180)).isoformat(),requested_end=now.isoformat(),policy_version='withdrawn',actor_id=run['actor_id'],job_id=run['job_id'],idempotency_key='deletion-only')
                request=CollectionRequest(**body); remote=json.loads(pending['remote_json'])
                if remote.get('status') not in {'SUCCEEDED','FAILED','ABORTED','TIMED-OUT','succeeded'}:
                    aborted=calls.run_call('review_abort','review_abort',run['input_hash']+':review_abort',lambda:self.provider.abort(remote,request),project=lambda v:{'status':v.get('status','UNKNOWN')})
                    if aborted.get('status') not in {'SUCCEEDED','FAILED','ABORTED','TIMED-OUT'}:
                        # No deletion while an actor can still write to the dataset.
                        version=str(pending['attempts'])
                        checked=calls.run_call('review_poll','cleanup_poll:'+version,run['input_hash']+':cleanup_poll:'+version,lambda:self.provider.poll(remote,request),project=lambda v:{'status':v.get('status','UNKNOWN')})
                        if checked.get('status') not in {'SUCCEEDED','FAILED','ABORTED','TIMED-OUT'}: deny('REMOTE_ABORT_PENDING','공급자의 실제 중단 확인이 필요합니다.')
                for operation,flag,method in [('review_delete_dataset','dataset_deleted',self.provider.delete_dataset),('review_delete_run','run_deleted',self.provider.delete_run)]:
                    if pending[flag]: continue
                    result=calls.run_call(operation,operation,run['input_hash']+':'+operation,lambda:method(remote,request),project=lambda v:{'deleted':v.get('deleted') is True})
                    if not result.get('deleted'): deny('REMOTE_DELETE_UNCONFIRMED','공급자 삭제 결과를 확인할 수 없습니다.')
                    with self.db.connect() as con: con.execute(f'UPDATE review_remote_cleanup SET {flag}=1 WHERE run_id=?',(ident,))
                with self.db.connect() as con:
                    con.execute("UPDATE review_remote_cleanup SET state='succeeded',remote_json='{}',lease_until=NULL,error_code=NULL,updated_at=? WHERE run_id=?",(self.now().isoformat(),ident))
                self._release_unused_cleanup(run)
                results.append({'run_id':ident,'state':'succeeded'})
            except Exception as exc:
                code=exc.code if isinstance(exc,(DomainError,ReviewProviderError)) else 'REMOTE_DELETE_UNCONFIRMED'
                with self.db.connect() as con:
                    con.execute("UPDATE review_remote_cleanup SET state='failed',lease_until=NULL,error_code=?,updated_at=? WHERE run_id=?",(code,self.now().isoformat(),ident))
                results.append({'run_id':ident,'state':'failed','error_code':code,'requires_operator':True})
        return {'items':results}
    def _detector_version(self):
        if self.detector is None:
            from src.research.language import LocalLanguageDetector
            self.detector=LocalLanguageDetector()
        return getattr(self.detector,'version',None) or type(self.detector).__module__+'.'+type(self.detector).__qualname__

    def budget_status(self,actor_id):
        policy=self.gateway.budget.policy
        if not policy.valid:return []
        now=self.now(); output=[]
        with self.db.connect() as con:
            for currency,limits in policy.config['limits'].items():
                item={'currency':currency}
                for field,col,period,personal in [('user_daily','period_day',now.date().isoformat(),True),('user_monthly','period_month',now.strftime('%Y-%m'),True),('global_daily','period_day',now.date().isoformat(),False),('global_monthly','period_month',now.strftime('%Y-%m'),False)]:
                    query=f"SELECT COALESCE(SUM(CASE WHEN state='released' THEN 0 WHEN state='settled' THEN actual_cost_micros ELSE estimated_cost_micros END),0) FROM usage_reservations WHERE currency=? AND {col}=?"
                    args=[currency,period]
                    if personal:query+=' AND owner_id=?';args.append(actor_id)
                    spent=con.execute(query,args).fetchone()[0]
                    item[field+'_remaining_micros']=max(0,limits[field]-spent)
                output.append(item)
        return output
    def _release_unused_cleanup(self,run):
        with self.db.connect() as con:
            ids=[r[0] for r in con.execute("SELECT call_id FROM usage_reservations WHERE job_id=? AND state='reserved' AND (operation='review_abort' OR call_key LIKE '%:cleanup_poll:%')",(run['job_id'],))]
        for call_id in ids:
            self.gateway.budget.release_unsent(call_id,reason_code='REMOTE_CLEANUP_NOT_NEEDED')
