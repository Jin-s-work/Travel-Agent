"""Administrator-reviewed identity links and runtime evidence; no URL fetching.

The mixin keeps all approvals in normal scoped API transactions. It never starts
paid work from a read or from a user's review request.
"""
from datetime import timedelta
from dataclasses import replace
import hashlib
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit

from src.foundation.repository import DomainError
from src.research.profiles import city_profile, PROFILE_VERSION, QUALITY_POLICY_VERSION


def reject(code,message,status=409): raise DomainError(code,message,status)
def enc(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def norm(value): return re.sub(r'[^\w]','',unicodedata.normalize('NFKC',value).casefold())
def public_url(value):
    try:
        parsed=urlsplit(value)
        valid=parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None,443)
    except ValueError:valid=False
    if not valid:reject('VALIDATION_FAILED','근거는 HTTPS 공개 출처 주소로 기록해 주세요.',422)


class ReviewContracts:
    def _contract_on(self,con,*,ident=None):
        build=getattr(self.provider,'build','synthetic-v1')
        sql="SELECT * FROM review_provider_contracts WHERE provider=? AND build=? AND adapter_version=? AND status='approved' AND expires_at>?"
        args=[self.provider.name,build,self.provider.adapter_version,self.now().isoformat()]
        if ident:sql+=' AND id=?';args.append(ident)
        row=con.execute(sql+' ORDER BY checked_at DESC,created_at DESC LIMIT 1',args).fetchone()
        if row:
            policy_id=json.loads(row['evidence_json']).get('rights_policy_id')
            policy=con.execute("SELECT id FROM provider_policies WHERE id=? AND provider=? AND status='active' AND expires_at>?",(policy_id,self.provider.name,self.now().isoformat())).fetchone()
            if not policy:return None
        return row

    def _runtime_contract(self,con,*,ident=None):
        row=self._contract_on(con,ident=ident)
        # Fake capabilities are synthetic protocol behavior, never production proof.
        if self.provider.name=='apify':
            body=json.loads(row['evidence_json']) if row else {}
            self.provider.contract_verified=bool(body.get('original_separation_verified') and body.get('original_language_verified'))
            self.provider.capabilities=replace(self.provider.capabilities,
                sort_basis=body.get('sort_basis','unknown'),
                continuity_verified=bool(body.get('continuity_verified') and body.get('source_pagination_visible') and body.get('internal_limits_verified') and getattr(self.provider,'source_pagination_observable',False)),
                source_pagination_visible=bool(body.get('source_pagination_visible') and getattr(self.provider,'source_pagination_observable',False)))
        return row

    def provider_contracts(self,actor):
        with self.db.connect() as con:
            self._admin(con,actor)
            rows=con.execute('SELECT * FROM review_provider_contracts ORDER BY created_at DESC LIMIT 100').fetchall()
            current=self._contract_on(con)
        return {'items':[self._contract_dto(r) for r in rows],'runtime_contract_id':current['id'] if current else None,
                'runtime_provider':self.provider.name,'runtime_build':getattr(self.provider,'build','synthetic-v1'),
                'runtime_adapter_version':self.provider.adapter_version}

    def _contract_dto(self,row):
        result={k:row[k] for k in ('id','provider','build','adapter_version','status','checked_at','expires_at','report_sha256')}
        result['evidence']=json.loads(row['evidence_json']);result['expired']=row['expires_at']<=self.now().isoformat()
        body=result['evidence']
        reasons=[]
        if row['status']!='approved' or result['expired']:reasons.append('PROVIDER_CONTRACT_UNVERIFIED')
        if body.get('sort_basis')=='unknown':reasons.append('ORDERING_SEMANTICS_UNVERIFIED')
        for key in ('original_separation_verified','original_language_verified','source_pagination_visible','continuity_verified','internal_limits_verified'):
            if not body.get(key):reasons.append(key.upper()+'_UNVERIFIED')
        if row['provider']=='apify' and row['adapter_version']=='apify-compass-v1':reasons.append('ADAPTER_SOURCE_PAGINATION_UNOBSERVABLE')
        if body.get('synthetic'):reasons.append('SYNTHETIC_EVIDENCE')
        result['strict_ready']=not reasons;result['reason_codes']=reasons
        return result

    def add_contract(self,actor,body):
        from src.research.service import stamp,new_id
        now=self.now();checked=stamp(body['checked_at']);expiry=stamp(body['expires_at'])
        if not checked<=now<expiry<=now+timedelta(days=90):reject('VALIDATION_FAILED','계약 검토일과 최대 90일 유효기간을 확인해 주세요.',422)
        for url in body['documentation_urls']:public_url(url)
        if body['status']=='approved' and (not body['sample_count'] or not body['schema_verified'] or not body['language_meaning'] or not body['pagination_scope']):
            reject('PROVIDER_CONTRACT_UNVERIFIED','스키마·표본·원문 언어 의미·페이지 범위를 각각 기록해 주세요.')
        if body['provider']=='fake' and not body['synthetic']:reject('VALIDATION_FAILED','합성 공급자는 실제 검증으로 기록할 수 없습니다.',422)
        if body['provider']=='apify' and body['synthetic'] and body['status']=='approved':reject('PROVIDER_CONTRACT_UNVERIFIED','합성 표본은 실제 공급자 계약을 승인하지 않습니다.')
        ident=new_id('contract')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            policy=con.execute("SELECT * FROM provider_policies WHERE id=? AND provider=? AND status='active' AND expires_at>?",(body['rights_policy_id'],body['provider'],now.isoformat())).fetchone()
            if not policy:reject('REVIEW_POLICY_UNAVAILABLE','현재 공급자의 유효한 이용 범위 기록을 선택해 주세요.')
            if stamp(body['pricing_checked_at'])>now:reject('VALIDATION_FAILED','가격 확인일은 미래일 수 없습니다.',422)
            old=con.execute('SELECT * FROM review_provider_contracts WHERE provider=? AND build=? AND adapter_version=? AND report_sha256=?',(body['provider'],body['build'],body['adapter_version'],body['report_sha256'])).fetchone()
            if old:return self._contract_dto(old)
            con.execute('INSERT INTO review_provider_contracts VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,body['provider'],body['build'],body['adapter_version'],body['status'],checked.isoformat(),expiry.isoformat(),body['report_sha256'],enc(body),actor.id,now.isoformat()))
            self._audit(con,actor,'provider_contract_recorded',ident,{'status':body['status'],'report_sha256':body['report_sha256']})
            return self._contract_dto(con.execute('SELECT * FROM review_provider_contracts WHERE id=?',(ident,)).fetchone())

    def revoke_contract(self,actor,ident):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            if not con.execute('SELECT 1 FROM review_provider_contracts WHERE id=?',(ident,)).fetchone():reject('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            con.execute("UPDATE review_provider_contracts SET status='revoked' WHERE id=?",(ident,))
            con.execute("UPDATE review_aggregates SET invalidated_at=?,invalidation_reason='PROVIDER_CONTRACT_REVOKED' WHERE run_id IN (SELECT run_id FROM review_run_dependencies WHERE contract_id=?)",(self.now().isoformat(),ident))
            con.execute('INSERT OR REPLACE INTO research_tombstones VALUES(?,?,?,?,NULL)',('contract',ident,'PROVIDER_CONTRACT_REVOKED',self.now().isoformat()))
            self._audit(con,actor,'provider_contract_revoked',ident,{})
        return {'id':ident,'status':'revoked'}

    def external_links(self,actor,canonical_place_id=None):
        with self.db.connect() as con:
            self._admin(con,actor)
            rows=con.execute('SELECT * FROM place_external_links'+(' WHERE canonical_place_id=?' if canonical_place_id else '')+' ORDER BY updated_at DESC LIMIT 200',(canonical_place_id,) if canonical_place_id else ()).fetchall()
            places=[dict(r) for r in con.execute("SELECT p.id,p.name,p.address,p.city,p.version,p.identity_status,c.latitude,c.longitude FROM place_identities p LEFT JOIN research_candidates c ON c.place_id=p.id WHERE p.deleted_at IS NULL AND p.identity_status='verified' ORDER BY p.name LIMIT 300")]
            sources=[dict(r) for r in con.execute("SELECT id,place_id,url,source_type,checked_at FROM evidence_sources WHERE status='active' AND read_confirmed=1 AND display_permitted=1")]
        for p in places:p['sources']=[s for s in sources if s['place_id']==p['id']]
        unique={p['id']:p for p in places}
        return {'items':[self._link_dto(r) for r in rows],'canonical_places':list(unique.values())}

    @staticmethod
    def _link_dto(row):
        return {**{k:row[k] for k in ('id','canonical_place_id','provider','external_place_id','status','source_id','checked_at','expires_at','identity_version','version')},'evidence':json.loads(row['evidence_json'])}

    def preview_link(self,actor,body):
        from src.research.service import new_id,stamp,validate_place_url
        validate_place_url(body['source_url']);now=self.now();expiry=stamp(body['expires_at'])
        if not now<expiry<=now+timedelta(days=90):reject('VALIDATION_FAILED','연결 유효기간은 최대 90일입니다.',422)
        if body['provider']=='apify' and not re.fullmatch(r'(?:ChIJ|GhIJ)[A-Za-z0-9_-]{23}',body['external_place_id']):reject('PLACE_IDENTITY_UNCONFIRMED','Google 지점 ID 형식을 확인해 주세요.',422)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            p=con.execute('SELECT * FROM place_identities WHERE id=? AND deleted_at IS NULL',(body['canonical_place_id'],)).fetchone()
            if not p:reject('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            if p['identity_status']!='verified' or p['version']!=body['expected_identity_version']:reject('VERSION_CONFLICT','공식 지점 정보를 먼저 다시 확인해 주세요.')
            source=con.execute("SELECT * FROM evidence_sources WHERE id=? AND place_id=? AND status='active' AND read_confirmed=1 AND display_permitted=1",(body['source_id'],p['id'])).fetchone()
            if not source:reject('IDENTITY_SOURCE_UNAVAILABLE','이 지점의 확인된 공식 근거를 선택해 주세요.')
            coordinate=con.execute('SELECT latitude,longitude FROM research_candidates WHERE place_id=? AND latitude IS NOT NULL AND longitude IS NOT NULL LIMIT 1',(p['id'],)).fetchone()
            issues=[];distance=None
            if norm(body['address'])!=norm(p['address']):issues.append('ADDRESS_MISMATCH')
            if coordinate and body.get('latitude') is not None and body.get('longitude') is not None:
                lat1,lon1=map(math.radians,(coordinate['latitude'],coordinate['longitude']));lat2,lon2=map(math.radians,(body['latitude'],body['longitude']))
                distance=6371000*2*math.asin(min(1,math.sqrt(math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2)))
                if distance>250:issues.append('COORDINATE_MISMATCH')
            else:issues.append('COORDINATES_UNCONFIRMED')
            existing=con.execute("SELECT canonical_place_id FROM place_external_links WHERE provider=? AND external_place_id=? AND status='approved'",(body['provider'],body['external_place_id'])).fetchone()
            if existing and existing['canonical_place_id']!=p['id']:issues.append('EXTERNAL_ID_ALREADY_LINKED')
            legacy=con.execute('SELECT id FROM place_identities WHERE provider=? AND external_place_id=? AND deleted_at IS NULL',(body['provider'],body['external_place_id'])).fetchone()
            if legacy and legacy['id']!=p['id']:issues.append('LEGACY_IDENTITY_CONFLICT')
            evidence={**body,'canonical':{'name':p['name'],'address':p['address'],'city':p['city']},'distance_meters':round(distance,1) if distance is not None else None,'reason_codes':issues,'name_matches':norm(body['name'])==norm(p['name'])}
            ident=new_id('extlink');con.execute('INSERT INTO place_external_links VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,p['id'],body['provider'],body['external_place_id'],'pending',source['id'],now.isoformat(),expiry.isoformat(),p['version'],1,enc(evidence),actor.id,now.isoformat(),now.isoformat()))
            self._audit(con,actor,'external_link_previewed',ident,{'reason_codes':issues})
            return self._link_dto(con.execute('SELECT * FROM place_external_links WHERE id=?',(ident,)).fetchone())

    def decide_link(self,actor,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            row=con.execute('SELECT * FROM place_external_links WHERE id=?',(ident,)).fetchone()
            if not row:reject('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            p=con.execute('SELECT * FROM place_identities WHERE id=? AND deleted_at IS NULL',(row['canonical_place_id'],)).fetchone()
            if not p or p['version']!=body['expected_identity_version'] or p['version']!=row['identity_version']:reject('VERSION_CONFLICT','공식 지점 정보가 변경되어 새 미리보기가 필요합니다.')
            if row['status']!='pending' or row['expires_at']<=self.now().isoformat():reject('VERSION_CONFLICT','이미 처리했거나 만료된 연결입니다.')
            evidence=json.loads(row['evidence_json']);reasons=evidence['reason_codes']
            if body['decision']=='approved':
                if reasons or not all(body.get(k) for k in ('confirm_name','confirm_address','confirm_coordinates','confirm_branch')):reject('PLACE_MAPPING_UNCONFIRMED','이름·주소·좌표·지점의 불일치와 확인 항목을 해결해 주세요.')
                if p['identity_status']!='verified' or not con.execute("SELECT 1 FROM evidence_sources WHERE id=? AND status='active' AND read_confirmed=1 AND display_permitted=1",(row['source_id'],)).fetchone():reject('IDENTITY_SOURCE_UNAVAILABLE','현재 공식 근거를 확인해 주세요.')
                conflict=con.execute("SELECT id FROM place_external_links WHERE provider=? AND status='approved' AND (external_place_id=? OR canonical_place_id=?)",(row['provider'],row['external_place_id'],row['canonical_place_id'])).fetchone()
                if conflict:reject('EXTERNAL_LINK_CONFLICT','이미 활성 연결이 있습니다. 이전 연결을 철회한 뒤 검토해 주세요.')
            con.execute('UPDATE place_external_links SET status=?,version=version+1,updated_at=? WHERE id=?',(body['decision'],self.now().isoformat(),ident))
            self._audit(con,actor,'external_link_decided',ident,body)
            return self._link_dto(con.execute('SELECT * FROM place_external_links WHERE id=?',(ident,)).fetchone())

    def revoke_link(self,actor,ident):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            row=con.execute('SELECT * FROM place_external_links WHERE id=?',(ident,)).fetchone()
            if not row:reject('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            con.execute("UPDATE place_external_links SET status='revoked',version=version+1,updated_at=? WHERE id=?",(self.now().isoformat(),ident))
            con.execute("UPDATE review_aggregates SET invalidated_at=?,invalidation_reason='EXTERNAL_LINK_REVOKED' WHERE run_id IN (SELECT run_id FROM review_run_dependencies WHERE external_link_id=?)",(self.now().isoformat(),ident))
            con.execute('INSERT OR REPLACE INTO research_tombstones VALUES(?,?,?,?,NULL)',('external_link',ident,'EXTERNAL_LINK_REVOKED',self.now().isoformat()))
            self._audit(con,actor,'external_link_revoked',ident,{})
        return {'id':ident,'status':'revoked'}

    def _resolve_external(self,con,place):
        row=con.execute("SELECT l.* FROM place_external_links l JOIN evidence_sources s ON s.id=l.source_id WHERE l.canonical_place_id=? AND l.provider=? AND l.status='approved' AND l.expires_at>? AND l.identity_version=? AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1",(place['id'],self.provider.name,self.now().isoformat(),place['version'])).fetchone()
        if row:return dict(row)
        # Preserve existing directly registered review IDs; never infer a merge.
        if place['provider']==self.provider.name:
            other=con.execute("SELECT canonical_place_id FROM place_external_links WHERE provider=? AND external_place_id=? AND status='approved'",(self.provider.name,place['external_place_id'])).fetchone()
            if other and other['canonical_place_id']!=place['id']:reject('EXTERNAL_LINK_CONFLICT','이 외부 지점은 다른 canonical 지점에 연결되어 있습니다.')
            return None
        reject('CANONICAL_REVIEW_LINK_REQUIRED','공식 지점과 리뷰 공급자 지점의 연결을 먼저 승인해 주세요.')

    def _run_dependency_guard(self,con,run):
        row=con.execute('SELECT * FROM review_run_dependencies WHERE run_id=?',(run['id'],)).fetchone()
        if not row:return # legacy observations retain existing quality gates
        if row['external_link_id']:
            link=con.execute("SELECT l.* FROM place_external_links l JOIN evidence_sources s ON s.id=l.source_id WHERE l.id=? AND l.status='approved' AND l.expires_at>? AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1",(row['external_link_id'],self.now().isoformat())).fetchone()
            if not link or link['version']!=row['external_link_version']:reject('EXTERNAL_LINK_REVOKED','지점 연결이 변경되어 수집을 중지했습니다.')
        if row['provider_build']!=getattr(self.provider,'build','synthetic-v1'):reject('PROVIDER_BUILD_CHANGED','공급자 build가 변경되어 새 계약 검토가 필요합니다.')
        if row['contract_id'] and not self._contract_on(con,ident=row['contract_id']):reject('PROVIDER_CONTRACT_UNVERIFIED','실행 당시 계약이 만료되거나 철회되었습니다.')
        if row['contract_id']:self._runtime_contract(con,ident=row['contract_id'])
        elif self.provider.name=='apify':
            self.provider.contract_verified=False
            self.provider.capabilities=replace(self.provider.capabilities,sort_basis='unknown',continuity_verified=False,source_pagination_visible=False)

    def request_review(self,actor,trip_id,place_id,*,visibility=None):
        from src.research.service import new_id
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.repo._trip(con,actor.id,trip_id)
            p=con.execute("SELECT p.id FROM place_identities p JOIN trip_places t ON t.place_id=p.id WHERE t.trip_id=? AND p.id=? AND p.deleted_at IS NULL",(trip_id,place_id)).fetchone()
            if not p and (visibility is None or not visibility(con,place_id)):reject('NOT_FOUND','자료를 찾을 수 없습니다.',404)
            old=con.execute("SELECT * FROM place_review_requests WHERE canonical_place_id=? AND status='queued'",(place_id,)).fetchone()
            duplicate=bool(old)
            if not old:
                ident=new_id('reviewrequest');clock=self.now().isoformat()
                con.execute('INSERT INTO place_review_requests VALUES(?,?,?,?,?)',(ident,place_id,'queued',clock,clock))
                old=con.execute('SELECT * FROM place_review_requests WHERE id=?',(ident,)).fetchone()
        return {'id':old['id'],'place_id':place_id,'status':'queued','paid_collection_started':False,'duplicate':duplicate}

    def review_requests(self,actor):
        with self.db.connect() as con:
            self._admin(con,actor)
            return {'items':[dict(r) for r in con.execute('SELECT r.*,p.name,p.city FROM place_review_requests r JOIN place_identities p ON p.id=r.canonical_place_id WHERE p.deleted_at IS NULL ORDER BY r.created_at LIMIT 200')]}

    def capabilities(self):
        from src.destinations import CITIES
        from src.research.profiles import quality_support
        now=self.now().isoformat()
        with self.db.connect() as con:
            controls=con.execute('SELECT production_enabled FROM review_controls').fetchone()
            contract=self._contract_on(con)
            qualities=con.execute('SELECT * FROM review_quality_evaluations WHERE expires_at>? ORDER BY checked_at DESC',(now,)).fetchall()
            counts={r['city']:r['n'] for r in con.execute("SELECT p.city,count(*) AS n FROM place_identities p JOIN review_aggregates a ON a.id=p.active_aggregate_id JOIN provider_policies y ON y.id=a.policy_id JOIN review_collection_runs r ON r.id=a.run_id WHERE p.deleted_at IS NULL AND p.identity_status='verified' AND a.invalidated_at IS NULL AND a.expires_at>? AND y.status='active' AND y.expires_at>? AND r.deleted_at IS NULL AND r.expires_at>? AND r.place_version=p.version AND r.provider!='fake' AND json_extract(a.evaluation_json,'$.strict_pass')=1 GROUP BY p.city",(now,now,now))}
        output=[]
        for city in CITIES:
            profile=city_profile(city);reasons=list(profile['reason_codes'])
            if not controls['production_enabled']:reasons.append('REVIEW_PRODUCTION_DISABLED')
            if self.provider.name=='fake':reasons.append('SYNTHETIC_PROVIDER')
            contract_ready=bool(contract and self._contract_dto(contract)['strict_ready'])
            if not contract_ready:reasons.append('PROVIDER_CONTRACT_UNVERIFIED')
            q=next((q for q in qualities if q['city']==city and q['detector_version']==self._detector_version()),None)
            if not q or not q['passed'] or q['synthetic']:reasons.append('CLASSIFICATION_QUALITY_UNVERIFIED')
            if not counts.get(city):reasons.append('NO_REVIEW_OBSERVATION')
            output.append({'city':city,'language_profile':profile,'observed_local':{'status':'available' if not reasons else 'unavailable','reason_codes':reasons,'valid_aggregate_count':counts.get(city,0)},'quality_policy_version':QUALITY_POLICY_VERSION,'synthetic':self.provider.name=='fake'})
        return {'items':output,'profile_version':PROFILE_VERSION,'population_inference_allowed':False,'residency_inference_allowed':False}

    def _quality_matches(self,row,place):
        body=json.loads(row['report_json'])
        from src.research.profiles import quality_support
        _,support=quality_support(body)
        contract=place.get('_contract')
        return bool(row['detector_version']==self._detector_version() and row['expires_at']>self.now().isoformat() and support and body.get('quality_policy_version')==QUALITY_POLICY_VERSION
            and body.get('language_profile_version')==PROFILE_VERSION
            and body.get('provider')==self.provider.name
            and body.get('provider_build')==getattr(self.provider,'build','synthetic-v1')
            and body.get('adapter_version')==self.provider.adapter_version
            and body.get('policy_version')==place.get('_policy_version')
            and body.get('category')==place.get('_category')
            and body.get('duplicate_text_count')==0 and body.get('shared_place_count')==0
            and contract and self._contract_dto(contract)['strict_ready'])

    def quality_evaluations(self,actor):
        with self.db.connect() as con:
            self._admin(con,actor)
            return {'items':[{**{k:r[k] for k in ('id','city','detector_version','passed','synthetic','checked_at','expires_at')},'report':json.loads(r['report_json'])} for r in con.execute('SELECT * FROM review_quality_evaluations ORDER BY checked_at DESC LIMIT 100')], 'quality_policy_version':QUALITY_POLICY_VERSION}

    def collection_preview(self,actor,body):
        with self.db.connect() as con:
            self._admin(con,actor)
            place,policy=self._eligible(con,body['place_id'],body['policy_id'])
            link=place['_external_link'];contract=place['_contract']
        return {'canonical_place_id':place['id'],'external_place_id':place['_external_id'],
            'external_link_id':link['id'] if link else None,'contract_id':contract['id'] if contract else None,
            'strict_contract_ready':bool(contract and self._contract_dto(contract)['strict_ready']),
            'provider':self.provider.name,'build':getattr(self.provider,'build','synthetic-v1'),
            'limits':{'max_review_records':body['max_review_records'],'max_pages':body['max_pages'],
                      'max_attempts_per_page':2,'max_elapsed_seconds':body['max_elapsed_seconds'],'lookback_days':180},
            'maximum_charge_usd':body['max_total_charge_usd'],'is_price_quote':False,
            'pricing_configured':self.gateway.budget.policy.valid,'budget_status':self.budget_status(actor.id),
            'reservation_created':False,'provider_calls':0,'synthetic':self.provider.name=='fake',
            'notes':['수집 시작 시 현재 예산을 다시 원자적으로 확인합니다.','이 상한은 실제 비용 견적이나 과금 취소 보장이 아닙니다.']}
