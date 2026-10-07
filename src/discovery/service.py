"""Owned bookmarks and versioned conditions; shared, independently reviewed facts.

No bookmark resolution performs a network request. External URLs are retained as
private user input; resolution uses the approved catalog or reports unsupported.
"""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import sqlite3
import unicodedata
from urllib.parse import urlsplit,parse_qs,urlunsplit,parse_qsl,urlencode
from uuid import uuid4

from src.foundation.repository import DomainError
from .models import Conditions,PackInput,FactInput
from src.destinations import CITIES,city_key
from .schema import scrub_trip


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def new_id(prefix):return prefix+'_'+uuid4().hex
def deny(code,message,status=409):raise DomainError(code,message,status)
def norm(value):return unicodedata.normalize('NFKC',value).strip().casefold()
def now():return datetime.now(timezone.utc).isoformat()
def instant(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
def public_url(value):
    from .safe_fetch import validate_public_url
    try:return validate_public_url(value)
    except (ValueError,TypeError):deny('URL_INVALID','공개 HTTP(S) 주소를 입력해 주세요. 로그인 정보가 든 주소는 저장할 수 없습니다.',422)
def url_identity(value):
    parts=urlsplit(value)
    query=[(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in {'gclid','fbclid','hl'}]
    return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),parts.path.rstrip('/'),urlencode(sorted(query)),''))


class DiscoveryService:
    def __init__(self,db,repo,jobs,reviews,*,allow_synthetic=False):
        self.db,self.repo,self.jobs,self.reviews=db,repo,jobs,reviews
        self.allow_synthetic=allow_synthetic
        self.public_provider=None
    def availability(self,city):
        with self.db.connect() as con:
            real=con.execute("SELECT count(DISTINCT c.place_id) FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id JOIN place_identities p ON p.id=c.place_id WHERE k.synthetic=0 AND k.status='approved' AND c.status='approved' AND p.identity_status='verified' AND p.deleted_at IS NULL AND p.city=? AND EXISTS(SELECT 1 FROM evidence_sources s WHERE s.place_id=p.id AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1)",(city,)).fetchone()[0]
            synthetic=con.execute("SELECT count(DISTINCT c.place_id) FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id WHERE k.synthetic=1 AND k.status='approved' AND k.city=?",(city,)).fetchone()[0] if self.allow_synthetic else 0
        from .public_places import center, ATTRIBUTION
        enabled=bool(self.public_provider and center(city))
        with self.db.connect() as con:
            public_count=len(self._public_catalog(con,city,set()))
        return {'public_discovery_enabled':enabled,'public_candidates':public_count,'public_scope':'city_center','radius_m':3000,'attribution':ATTRIBUTION,
                'city':city,'real_reviewed_candidates':real,'synthetic_test_candidates':synthetic,
                'state':'limited_catalog' if real else 'public_catalog' if public_count else 'public_search_available' if enabled else 'no_live_candidates',
                'visit_qualification':'checked_per_request','review_language':'separate_policy_and_quality_gate'}
    def _admin(self,con,actor):
        self.reviews._admin(con,actor)
    def _audit(self,con,actor,action,target,details):
        con.execute('INSERT INTO discovery_audit VALUES(?,?,?,?,?,?)',(new_id('audit'),actor.id,action,target,encode(details),now()))
    def _owner(self,con,actor,trip_id):return self.repo._trip(con,actor.id,trip_id)
    def _default_conditions(self,trip):
        first=next((s for s in trip['stops'] if city_key(s['city'])),None)
        city=city_key(first['city']) if first else ('barcelona' if 'barcelona' in trip['title'].casefold() or '바르셀로나' in trip['title'] else 'tokyo')
        value={'city':city,'visit':{'date':first['start_date'] if first else trip['start_date'],'local_time':None,'timezone':CITIES[city]['timezone']},'party':trip['party']}
        if first and first.get('base_location'):
            value['origin']={'label':first['base_location'][:300],'latitude':None,'longitude':None,'place_id':None}
        output=Conditions.model_validate(value).model_dump(mode='json')
        if trip['stops'] and first is None:
            output['city']=None
            output['visit']['timezone']=trip['stops'][0]['timezone']
        return output
    def get_conditions(self,actor,trip_id):
        from .context import load, resolve, enrich_origin
        with self.db.connect() as con:
            trip=self.repo._trip_dto(con,self._owner(con,actor,trip_id))
            row=con.execute('SELECT * FROM discovery_conditions WHERE trip_id=? AND owner_id=?',(trip_id,actor.id)).fetchone()
            overrides,stop_id,basis=load(con,trip_id,row)
            resolved=resolve(trip,overrides,stop_id,basis)
            if not resolved['validation']:
                resolved=enrich_origin(con,self.repo,actor,trip_id,resolved,allow_deleted_selection=True)
        # Preserve legacy no-stop defaults for the old condition editor, but require confirmation.
        if not trip['stops'] and not row:
            resolved['conditions']=self._default_conditions(trip)
        stays=[{'stop_id':s['id'],'city':city_key(s['city']),'label':s['city'],'start_date':s['start_date'],'end_date':s['end_date'],'timezone':s['timezone']} for s in trip['stops'] if city_key(s['city'])]
        unsupported=bool(trip['stops'] and not stays)
        issue=resolved['validation']
        confirmed=not issue and bool(row or trip['stops'])
        return {**resolved,'version':row['version'] if row else 0,'trip_version':trip['version'],
            'saved_trip_version':row['trip_version'] if row else trip['version'],'trip_snapshot':trip,
            'city_needs_confirmation':not confirmed,
            'context_state':'unsupported_city' if unsupported else 'selection_required' if not trip['stops'] and not row else 'outdated' if issue else 'ready',
            'stay_options':stays,'catalog_availability':self.availability(resolved['conditions'].get('city')),
            'city_metadata':CITIES.get(resolved['conditions'].get('city')),
            'unsupported_cities':[s['city'] for s in trip['stops'] if not city_key(s['city'])]}

    @staticmethod
    def context_issue(trip,conditions):
        visit=conditions['visit']['date']
        if not trip['start_date']<=visit<=trip['end_date']:
            return {'field':'visit.date','message':'여행 기간 안에서 방문일을 선택해 주세요.'}
        relevant=[s for s in trip['stops'] if city_key(s['city'])==conditions.get('city') and conditions.get('city') and s['timezone']==conditions['visit']['timezone']]
        if trip['stops'] and not relevant:
            return {'field':'city','message':'등록한 여행 도시에서 선택해 주세요. 이 도시의 자동 추천이 미지원이어도 장소 보관함은 사용할 수 있습니다.'}
        if relevant and not any(s['start_date']<=visit<=s['end_date'] for s in relevant):
            ranges=' / '.join(s['start_date']+' ~ '+s['end_date'] for s in relevant)
            return {'field':'visit.date','message':f'선택한 도시의 체류일({ranges}) 중에서 방문일을 선택해 주세요.'}
        return None

    def save_conditions(self,actor,trip_id,body):
        conditions=Conditions.model_validate(body['conditions']).model_dump(mode='json')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._owner(con,actor,trip_id);trip=self.repo._trip_dto(con,row)
            old=con.execute('SELECT * FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            if body['expected_version']!=(old['version'] if old else 0):deny('VERSION_CONFLICT','다른 화면에서 조건이 바뀌었습니다. 입력을 보존한 채 최신 조건을 확인해 주세요.')
            issue=self.context_issue(trip,conditions)
            if issue:raise DomainError('VALIDATION_FAILED',issue['message'],422,[issue])
            from src.accommodations.origin import context_in_connection
            context_in_connection(con,self.repo,actor,trip_id,{**conditions['visit'],'city':conditions['city']},conditions)
            con.execute('DELETE FROM discovery_contexts WHERE trip_id=?',(trip_id,))
            con.execute('INSERT INTO discovery_conditions VALUES(?,?,?,?,?,?,?) ON CONFLICT(trip_id) DO UPDATE SET version=excluded.version,trip_version=excluded.trip_version,snapshot_json=excluded.snapshot_json,conditions_json=excluded.conditions_json,updated_at=excluded.updated_at',
                (trip_id,actor.id,body['expected_version']+1,trip['version'],encode(trip),encode(conditions),now()))
        return self.get_conditions(actor,trip_id)
    def _bookmark(self,con,actor,trip_id,ident):
        self._owner(con,actor,trip_id)
        row=con.execute('SELECT * FROM bookmarks WHERE id=? AND trip_id=? AND owner_id=? AND deleted_at IS NULL',(ident,trip_id,actor.id)).fetchone()
        if not row:deny('NOT_FOUND','저장한 장소를 찾을 수 없습니다.',404)
        return row
    def _bookmark_dto(self,con,row,duplicate=False):
        value={k:row[k] for k in ('id','input_kind','input_value','note','resolve_state','matched_place_id','job_id','version','created_at','updated_at')}
        value.update(candidates=json.loads(row['candidates_json']),reason_codes=json.loads(row['reason_codes_json']),duplicate=duplicate,
            excluded=bool(row['matched_place_id'] and con.execute('SELECT 1 FROM discovery_exclusions WHERE trip_id=? AND place_id=?',(row['trip_id'],row['matched_place_id'])).fetchone()))
        # This DTO is reached only after the owning trip/bookmark was checked.
        # Keep a saved identity legible when an editorial pack is withdrawn;
        # source facts and permissions still go through the separate detail gate.
        place=con.execute('SELECT id,name,address,city FROM place_identities WHERE id=? AND deleted_at IS NULL',
            (row['matched_place_id'],)).fetchone() if row['matched_place_id'] else None
        value['place']=None
        if place:
            native=con.execute('SELECT native_name FROM research_candidates WHERE place_id=? ORDER BY created_at DESC,id LIMIT 1',(place['id'],)).fetchone()
            value['place']={**dict(place),'display_name':place['name'],'native_name':native['native_name'] if native else None}
        return value
    def list_bookmarks(self,actor,trip_id):
        with self.db.connect() as con:
            self._owner(con,actor,trip_id)
            return [self._bookmark_dto(con,row) for row in con.execute('SELECT * FROM bookmarks WHERE trip_id=? AND owner_id=? AND deleted_at IS NULL ORDER BY created_at DESC',(trip_id,actor.id))]
    def get_bookmark(self,actor,trip_id,ident):
        with self.db.connect() as con:return self._bookmark_dto(con,self._bookmark(con,actor,trip_id,ident))
    def create_bookmark(self,actor,trip_id,body):
        kind=body['input_kind'];value=body['input_value'].strip()
        normalized=url_identity(public_url(value)) if kind=='url' else norm(value)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._owner(con,actor,trip_id)
            matched=None
            if kind=='place':
                if not self._visible_place(con,value):deny('NOT_FOUND','장소를 찾을 수 없습니다.',404)
                matched=value
            if body.get('run_id'):
                from src.product.events import validate_refs
                validate_refs(con,actor.id,trip_id,matched,body['run_id'])
            old=con.execute('SELECT * FROM bookmarks WHERE trip_id=? AND deleted_at IS NULL AND ((input_kind=? AND normalized_input=?) OR (CAST(? AS TEXT) IS NOT NULL AND matched_place_id=?))',(trip_id,kind,normalized,matched,matched)).fetchone()
            if old:return self._bookmark_dto(con,old,True)
            ident,stamp=new_id('bookmark'),now()
            con.execute('INSERT INTO bookmarks(id,owner_id,trip_id,input_kind,input_value,normalized_input,note,resolve_state,matched_place_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (ident,actor.id,trip_id,kind,value,normalized,body.get('note',''),'resolved' if matched else 'unresolved',matched,stamp,stamp))
            if matched:
                from src.product.events import record
                record(con,actor.id,trip_id,'recommendation_save','server:bookmark:'+ident,place=matched,run=body.get('run_id'))
            return self._bookmark_dto(con,con.execute('SELECT * FROM bookmarks WHERE id=?',(ident,)).fetchone())
    def patch_bookmark(self,actor,trip_id,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._bookmark(con,actor,trip_id,ident)
            if row['version']!=body['expected_version']:deny('VERSION_CONFLICT','저장 내용이 바뀌었습니다. 입력한 메모를 보존하고 다시 확인해 주세요.')
            con.execute('UPDATE bookmarks SET note=?,version=version+1,updated_at=? WHERE id=?',(body['note'],now(),ident))
        return self.get_bookmark(actor,trip_id,ident)
    def delete_bookmark(self,actor,trip_id,ident):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._bookmark(con,actor,trip_id,ident)
            con.execute("UPDATE bookmarks SET deleted_at=?,note='',input_value='',normalized_input='',candidates_json='[]',reason_codes_json='[]',version=version+1 WHERE id=?",(now(),ident))
            con.execute('INSERT OR IGNORE INTO discovery_tombstones VALUES(?,?,?,?)',('bookmark',ident,'USER_DELETED',now()))
        return {'id':ident,'deleted':True}
    def resolve(self,actor,trip_id,ident,body,key):
        fingerprint=hashlib.sha256(encode({'bookmark_id':ident,'expected_version':body['expected_version']}).encode()).hexdigest()
        existing=self.jobs.lookup(actor.id,actor.session_id,'personal_trip',trip_id,'bookmark_resolve',key,fingerprint)
        if existing:return {'job_id':existing['id'],'status_url':'/api/v2/jobs/'+existing['id']}
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._bookmark(con,actor,trip_id,ident)
            if row['version']!=body['expected_version']:deny('VERSION_CONFLICT','저장 내용이 바뀌었습니다. 다시 확인해 주세요.')
            if row['resolve_state']=='resolving' and row['job_id']:
                job=self.jobs.get(row['job_id'],actor.id,actor.session_id)
                if job['state'] in ('queued','running'):return {'job_id':job['id'],'status_url':'/api/v2/jobs/'+job['id']}
            job=self.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'bookmark_resolve',{'bookmark_id':ident,'bookmark_version':row['version']},self._owner(con,actor,trip_id)['version'],key,request_fingerprint=fingerprint,con=con)
            con.execute("UPDATE bookmarks SET resolve_state='resolving',job_id=?,reason_codes_json='[]',updated_at=? WHERE id=?",(job['id'],now(),ident))
        return {'job_id':job['id'],'bookmark_id':ident,'state':'queued','status_url':'/api/v2/jobs/'+job['id'],'events_url':'/api/v2/jobs/'+job['id']+'/events'}
    def _visible_place(self,con,ident):
        place=con.execute("SELECT p.* FROM place_identities p WHERE p.id=? AND p.deleted_at IS NULL AND p.identity_status='verified' AND (EXISTS(SELECT 1 FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id WHERE c.place_id=p.id AND c.status='approved' AND k.status='approved' AND (?=1 OR k.synthetic=0)) OR NOT EXISTS(SELECT 1 FROM research_candidates c WHERE c.place_id=p.id))",(ident,int(self.allow_synthetic))).fetchone()
        if place:return place
        from .public_places import POLICY, TTL
        return con.execute("SELECT p.* FROM place_identities p JOIN research_candidates c ON c.place_id=p.id JOIN candidate_packs k ON k.id=c.pack_id WHERE p.id=? AND p.deleted_at IS NULL AND p.provider='openstreetmap' AND p.identity_status='needs_confirmation' AND c.status='public_data' AND k.status='public_data' AND k.updated_at>? AND EXISTS(SELECT 1 FROM evidence_sources s WHERE s.place_id=p.id AND s.policy_version=? AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1)",(ident,(datetime.now(timezone.utc)-TTL).isoformat(),POLICY)).fetchone()
    def execute_resolution(self,job,ctx):
        actor=type('Owner',(),{'id':job['actor_id'],'session_id':job['session_id']})()
        with self.db.connect() as con:
            row=self._bookmark(con,actor,job['trip_id'],job['payload']['bookmark_id']);saved=dict(row)
            places=list(con.execute("SELECT p.*,c.native_name,c.canonical_url FROM place_identities p LEFT JOIN research_candidates c ON c.place_id=p.id LEFT JOIN candidate_packs k ON k.id=c.pack_id WHERE p.deleted_at IS NULL AND p.identity_status='verified' AND (c.id IS NULL OR (c.status='approved' AND k.status='approved' AND (?=1 OR k.synthetic=0)))",(int(self.allow_synthetic),)))
        ctx.guard();matches={};exact_matches=set();value=saved['input_value']
        for place in places:
            match=False
            if saved['input_kind']=='place':match=place['id']==value
            elif saved['input_kind']=='name':
                query=norm(value)
                match=any(query==norm(name) or len(query)>=3 and query in norm(name) for name in (place['name'],place['native_name']) if name)
                if any(query==norm(name) for name in (place['name'],place['native_name']) if name):exact_matches.add(place['id'])
            else:
                target=url_identity(value)
                parsed=urlsplit(value);params=parse_qs(parsed.query)
                external=(params.get('query_place_id') or params.get('place_id') or [None])[0] if parsed.hostname in {'google.com','www.google.com','maps.google.com'} and parsed.path.startswith('/maps') else None
                match=bool(external and external==place['external_place_id']) or any(target==url_identity(link) for link in (place['source_url'],place['canonical_url']) if link)
            if match:matches[place['id']]={'place_id':place['id'],'name':place['name'],'address':place['address'],'city':place['city']}
        candidates=sorted(matches.values(),key=lambda p:p['place_id'])[:20]
        automatic=len(candidates)==1 and (saved['input_kind']!='name' or candidates[0]['place_id'] in exact_matches)
        state='resolved' if automatic else 'ambiguous' if candidates else 'unsupported'
        matched=candidates[0]['place_id'] if state=='resolved' else None
        reasons=[] if candidates else ['REMOTE_RESOLUTION_UNAVAILABLE']
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');ctx.guard(con=con)
            current=self._bookmark(con,actor,job['trip_id'],saved['id'])
            if current['version']!=job['payload']['bookmark_version'] or current['job_id']!=job['id']:deny('VERSION_CONFLICT','장소 메모가 바뀌어 이전 확인 결과를 적용하지 않았습니다.')
            if matched and con.execute('SELECT 1 FROM bookmarks WHERE trip_id=? AND matched_place_id=? AND deleted_at IS NULL AND id!=?',(job['trip_id'],matched,saved['id'])).fetchone():
                state,matched,reasons='ambiguous',None,['ALREADY_BOOKMARKED']
            con.execute('UPDATE bookmarks SET resolve_state=?,matched_place_id=?,candidates_json=?,reason_codes_json=?,version=version+1,updated_at=? WHERE id=?',(state,matched,encode(candidates),encode(reasons),now(),saved['id']))
        return {'bookmark_id':saved['id'],'resolve_state':state}
    execute=execute_resolution
    def select(self,actor,trip_id,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._bookmark(con,actor,trip_id,ident)
            if row['version']!=body['expected_version']:deny('VERSION_CONFLICT','확인 후보가 바뀌었습니다.')
            if body['place_id'] not in {c['place_id'] for c in json.loads(row['candidates_json'])} or not self._visible_place(con,body['place_id']):deny('NOT_FOUND','확인 후보를 찾을 수 없습니다.',404)
            if con.execute('SELECT 1 FROM bookmarks WHERE trip_id=? AND matched_place_id=? AND id!=? AND deleted_at IS NULL',(trip_id,body['place_id'],ident)).fetchone():deny('ALREADY_BOOKMARKED','이 여행에 이미 저장한 장소입니다. 기존 메모를 확인해 주세요.')
            con.execute("UPDATE bookmarks SET matched_place_id=?,resolve_state='resolved',version=version+1,updated_at=?,reason_codes_json='[]' WHERE id=?",(body['place_id'],now(),ident))
        return self.get_bookmark(actor,trip_id,ident)
    def exclude(self,actor,trip_id,place_id,excluded=True):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._owner(con,actor,trip_id)
            if not self._visible_place(con,place_id):deny('NOT_FOUND','장소를 찾을 수 없습니다.',404)
            if excluded:con.execute('INSERT OR IGNORE INTO discovery_exclusions VALUES(?,?,?,?)',(actor.id,trip_id,place_id,now()))
            else:con.execute('DELETE FROM discovery_exclusions WHERE trip_id=? AND place_id=?',(trip_id,place_id))
        return {'place_id':place_id,'excluded':excluded}
    def list_exclusions(self,actor,trip_id):
        with self.db.connect() as con:
            self._owner(con,actor,trip_id)
            return [r[0] for r in con.execute('SELECT place_id FROM discovery_exclusions WHERE trip_id=?',(trip_id,))]
    def import_pack(self,actor,payload):
        pack=PackInput.model_validate(payload).model_dump(mode='json')
        payload_hash=hashlib.sha256(encode(pack).encode()).hexdigest()
        for place in pack['places']:
            public_url(place['canonical_url'])
            for source in place['sources']:public_url(source['url'])
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            prior=con.execute('SELECT id,status FROM candidate_packs WHERE version=? AND city=?',(pack['version'],pack['city'])).fetchone()
            if prior:
                audit=con.execute("SELECT details_json FROM discovery_audit WHERE action='candidate_pack_imported' AND target_id=? ORDER BY created_at DESC LIMIT 1",(prior['id'],)).fetchone()
                previous_hash=json.loads(audit['details_json']).get('payload_hash') if audit else None
                if previous_hash!=payload_hash:
                    deny('PACK_VERSION_CONFLICT','같은 후보팩 버전의 내용이 다르거나 이전 원본을 대조할 수 없습니다. 새 버전으로 검토해 주세요.')
                return {'pack_id':prior['id'],'duplicate':True,'status':prior['status']}
            ident,stamp=new_id('pack'),now()
            con.execute('INSERT INTO candidate_packs VALUES(?,?,?,?,?,?,?,?)',(ident,pack['version'],pack['city'],int(pack['synthetic']),'needs_review',actor.id,stamp,stamp))
            provider='synthetic_editorial' if pack['synthetic'] else 'manual_official'
            for sort_order,item in enumerate(pack['places']):
                existing=con.execute('SELECT * FROM place_identities WHERE provider=? AND external_place_id=?',(provider,item['external_id'])).fetchone()
                if existing:
                    if existing['deleted_at']:deny('PLACE_DELETED','삭제한 장소를 후보팩으로 복구할 수 없습니다.')
                    if norm(existing['address'])!=norm(item['address']) or existing['city']!=pack['city']:deny('PLACE_IDENTITY_CONFLICT','같은 외부 ID의 지점 주소가 달라 확인이 필요합니다.')
                    place_id=existing['id']
                else:
                    place_id=new_id('place')
                    con.execute('INSERT INTO place_identities(id,provider,external_place_id,city,name,address,source_url,identity_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (place_id,provider,item['external_id'],pack['city'],item['name'],item['address'],item['canonical_url'],'needs_confirmation',stamp,stamp))
                con.execute('INSERT INTO research_candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (new_id('candidate'),ident,place_id,'needs_review',item['category'],encode(item['recommendation_types']),encode(item['tags']),item['native_name'],item['canonical_url'],item['chain_id'],item['neighborhood'],item['latitude'],item['longitude'],stamp,stamp,sort_order))
                source_ids={}
                for source in item['sources']:
                    source['checked_at']=instant(source['checked_at']).isoformat()
                    if source['published_at']:source['published_at']=instant(source['published_at']).isoformat()
                    old=con.execute('SELECT * FROM evidence_sources WHERE place_id=? AND source_key=?',(place_id,source['key'])).fetchone()
                    if old:
                        # Import cannot silently turn a revoked or pending source on.
                        if old['url']!=source['url'] or old['source_group']!=source['source_group']:deny('SOURCE_IDENTITY_CONFLICT','같은 출처 키의 URL·발행자가 바뀌었습니다.')
                        source_ids[source['key']]=old['id'];continue
                    source_id=new_id('source');source_ids[source['key']]=source_id
                    # Reading a URL alone never activates public use. The review
                    # endpoint records a separate operator permission decision.
                    con.execute('INSERT INTO evidence_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (source_id,place_id,source['key'],source['url'],source['source_type'],source['source_group'],source['checked_at'],source['published_at'],int(source['read_confirmed']),int(source['display_permitted']),'pending',source['evidence_note'],pack['version'],actor.id,1,stamp,stamp))
                for fact in item['facts']:self._insert_fact(con,actor,place_id,fact,source_ids,pack['version'])
            self._audit(con,actor,'candidate_pack_imported',ident,{'version':pack['version'],'city':pack['city'],'synthetic':pack['synthetic'],'places':len(pack['places']),'payload_hash':payload_hash})
        return {'pack_id':ident,'duplicate':False,'status':'needs_review','places':len(pack['places'])}
    def _insert_fact(self,con,actor,place_id,fact,source_ids,policy_version):
        fact=FactInput.model_validate(fact).model_dump(mode='json')
        fact['checked_at']=instant(fact['checked_at']).isoformat()
        fact['expires_at']=instant(fact['expires_at']).isoformat()
        source_id=source_ids.get(fact['source_key'])
        source=con.execute('SELECT * FROM evidence_sources WHERE id=? AND place_id=?',(source_id,place_id)).fetchone() if source_id else None
        status=fact['status']
        if status=='verified' and (not source or not source['read_confirmed']):status='provisional'
        if fact['source_key'] and not source:deny('SOURCE_NOT_FOUND','사실의 출처 키를 찾을 수 없습니다.',422)
        if source and instant(fact['checked_at'])>instant(source['checked_at']):deny('SOURCE_NOT_READ','사실 확인일보다 최근에 출처를 읽은 근거가 필요합니다.',422)
        conflict=[]
        if status in ('verified','conflict'):
            for old in con.execute("SELECT * FROM place_facts WHERE place_id=? AND field=? AND status IN ('verified','conflict')",(place_id,fact['field'])):
                same_scope=(old['valid_for_date']==fact['valid_for_date'] and old['valid_from']==fact['valid_from'] and old['valid_until']==fact['valid_until'])
                if same_scope and old['value_json']!=encode(fact['value']):conflict.append(old['id'])
        if conflict:
            status='conflict'
            for old_id in conflict:con.execute("UPDATE place_facts SET status='conflict' WHERE id=?",(old_id,))
        ident=new_id('fact')
        con.execute('INSERT INTO place_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,place_id,fact['field'],encode(fact['value']),status,source_id,fact['checked_at'],fact['valid_for_date'],fact['valid_from'],fact['valid_until'],fact['expires_at'],policy_version,actor.id,now()))
        return ident
    def add_fact(self,actor,place_id,payload):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            if not con.execute('SELECT 1 FROM place_identities WHERE id=? AND deleted_at IS NULL',(place_id,)).fetchone():deny('NOT_FOUND','장소를 찾을 수 없습니다.',404)
            sources={r['source_key']:r['id'] for r in con.execute('SELECT * FROM evidence_sources WHERE place_id=?',(place_id,))}
            source=con.execute('SELECT policy_version FROM evidence_sources WHERE id=?',(sources.get(payload.get('source_key')),)).fetchone()
            ident=self._insert_fact(con,actor,place_id,payload,sources,source['policy_version'] if source else 'unverified')
            self._audit(con,actor,'fact_recorded',ident,{'place_id':place_id,'field':payload['field']})
        return {'id':ident}
    def list_packs(self,actor):
        with self.db.connect() as con:
            self._admin(con,actor)
            return [{**dict(row),'synthetic':bool(row['synthetic']),'places':[self._candidate(con,c,admin=True) for c in con.execute('SELECT c.*,p.name,p.address,p.city,p.identity_status FROM research_candidates c JOIN place_identities p ON p.id=c.place_id WHERE c.pack_id=? ORDER BY c.sort_order,c.id',(row['id'],))]} for row in con.execute('SELECT * FROM candidate_packs ORDER BY created_at DESC')]
    def approve_pack(self,actor,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            pack=con.execute('SELECT * FROM candidate_packs WHERE id=?',(ident,)).fetchone()
            if not pack:deny('NOT_FOUND','후보팩을 찾을 수 없습니다.',404)
            if body['status']=='approved':
                if pack['synthetic'] and not self.allow_synthetic:deny('SYNTHETIC_PRODUCTION_DISABLED','합성 후보팩은 운영 서비스에서 활성화할 수 없습니다.',422)
                if con.execute("SELECT 1 FROM discovery_tombstones WHERE kind='pack' AND target_id=?",(ident,)).fetchone():deny('PACK_DISABLED','중단한 후보팩은 새 버전으로 다시 검토해 주세요.')
                for candidate in con.execute('SELECT * FROM research_candidates WHERE pack_id=?',(ident,)):
                    source=con.execute("SELECT 1 FROM evidence_sources WHERE place_id=? AND read_confirmed=1 AND status='active' AND display_permitted=1",(candidate['place_id'],)).fetchone()
                    if not source:deny('SOURCE_POLICY_UNVERIFIED','각 지점에 읽기·사용 검토를 마친 출처가 필요합니다.',422)
                    con.execute("UPDATE place_identities SET identity_status='verified',identity_evidence=?,version=version+1,updated_at=? WHERE id=? AND deleted_at IS NULL",(body['evidence'],now(),candidate['place_id']))
            con.execute('UPDATE candidate_packs SET status=?,updated_at=? WHERE id=?',(body['status'],now(),ident))
            con.execute('UPDATE research_candidates SET status=?,updated_at=? WHERE pack_id=?',(body['status'],now(),ident))
            if body['status']=='disabled':con.execute('INSERT OR REPLACE INTO discovery_tombstones VALUES(?,?,?,?)',('pack',ident,'OPERATOR_DISABLED',now()))
            self._audit(con,actor,'candidate_pack_reviewed',ident,body)
        return {'pack_id':ident,'status':body['status']}
    def review_source(self,actor,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._admin(con,actor)
            source=con.execute('SELECT * FROM evidence_sources WHERE id=?',(ident,)).fetchone()
            if not source:deny('NOT_FOUND','출처를 찾을 수 없습니다.',404)
            if source['version']!=body['expected_version']:deny('VERSION_CONFLICT','출처 검토 기록이 바뀌었습니다.')
            if source['status']=='revoked' and body['status']=='active':deny('SOURCE_REVOKED','철회 출처는 새 키와 새 검토 근거로 등록해 주세요.')
            if body['status']=='active' and not body['read_confirmed']:deny('SOURCE_NOT_READ','URL만으로 확인 완료가 될 수 없습니다.',422)
            checked_at=instant(body['checked_at']).isoformat() if body.get('checked_at') else source['checked_at']
            if instant(checked_at)<instant(source['checked_at']):deny('SOURCE_TIME_CONFLICT','출처 확인일을 이전 시점으로 바꿀 수 없습니다.',422)
            if body.get('checked_at') and not body['read_confirmed']:deny('SOURCE_NOT_READ','원문을 확인한 경우에만 확인일을 갱신할 수 있습니다.',422)
            con.execute('UPDATE evidence_sources SET status=?,read_confirmed=?,display_permitted=?,policy_version=?,checked_at=?,version=version+1,updated_at=? WHERE id=?',
                (body['status'],int(body['read_confirmed']),int(body['display_permitted']),body['policy_version'],checked_at,now(),ident))
            if body['status']=='revoked':
                con.execute('INSERT OR REPLACE INTO discovery_tombstones VALUES(?,?,?,?)',('source',ident,'USE_WITHDRAWN',now()))
            self._audit(con,actor,'source_reviewed',ident,body)
        return {'id':ident,'version':body['expected_version']+1,'status':body['status']}
    def _source_dto(self,source,admin=False):
        permitted=source['status']=='active' and source['read_confirmed'] and source['display_permitted']
        if not permitted and not admin:return {'id':source['id'],'status':'unavailable','reason_codes':['SOURCE_POLICY_UNAVAILABLE']}
        return {k:source[k] for k in ('id','source_key','url','source_type','source_group','checked_at','published_at','read_confirmed','display_permitted','status','policy_version','version','evidence_note')}
    def _facts_many(self,con,place_ids,admin=False):
        """Two bounded reads; source/fact combinations never multiply in a join."""
        ids=sorted(set(place_ids)); output={ident:([],[]) for ident in ids}
        if not ids:return output
        marks=','.join('?' for _ in ids)
        sources={}
        for row in con.execute(f"SELECT s.*,EXISTS(SELECT 1 FROM discovery_tombstones t WHERE t.kind='source' AND t.target_id=s.id) AS withdrawn FROM evidence_sources s WHERE s.place_id IN ({marks}) ORDER BY s.source_key,s.id",ids):
            source=dict(row)
            if source['withdrawn']:source.update(status='revoked',display_permitted=0)
            sources[source['id']]=source;output[source['place_id']][1].append(self._source_dto(source,admin))
        clock=now()
        for row in con.execute(f'SELECT * FROM place_facts WHERE place_id IN ({marks}) ORDER BY checked_at DESC,id',ids):
            source=sources.get(row['source_id']); reasons=[];expired=row['expires_at']<=clock
            if not source or source['status']!='active' or not source['read_confirmed'] or not source['display_permitted']:reasons.append('SOURCE_POLICY_UNAVAILABLE')
            elif source['policy_version']!=row['policy_version']:reasons.append('SOURCE_POLICY_CHANGED')
            if expired:reasons.append('FACT_STALE')
            fact={k:row[k] for k in ('id','field','status','source_id','checked_at','valid_for_date','valid_from','valid_until','expires_at','policy_version')}
            fact.update(place_id=row['place_id'],value=json.loads(row['value_json']) if admin or not reasons else None,
                freshness='expired' if expired else 'fresh',usable=not reasons,reason_codes=reasons)
            output[row['place_id']][0].append(fact)
        return output

    def _facts(self,con,place_id,admin=False):
        return self._facts_many(con,[place_id],admin)[place_id]

    def _candidates(self,con,rows,admin=False):
        rows=list(rows)
        if not rows:return []
        facts=self._facts_many(con,[r['place_id'] for r in rows],admin)
        ids=sorted({r['pack_id'] for r in rows});marks=','.join('?' for _ in ids)
        packs={r['id']:r for r in con.execute(f'SELECT * FROM candidate_packs WHERE id IN ({marks})',ids)}
        output=[]
        for row in rows:
            pack=packs[row['pack_id']];values,sources=facts[row['place_id']]
            value={'place_id':row['place_id'],'name':row['name'],'native_name':row['native_name'],'display_name':row['name'],
                'address':row['address'],'city':row['city'],'category':row['category'],'categories':[row['category']],
                'recommendation_types':json.loads(row['recommendation_types_json']),'tags':json.loads(row['tags_json']),
                'chain_id':row['chain_id'],'neighborhood':row['neighborhood'],'latitude':row['latitude'],'longitude':row['longitude'],
                'identity_status':row['identity_status'],'pack_status':pack['status'],'synthetic':bool(pack['synthetic']),
                'canonical_url':row['canonical_url'],'facts':values,'sources':sources,
                'dependencies':{'pack_id':pack['id'],'pack_version':pack['version'],'identity_version':row['identity_version'] if 'identity_version' in row.keys() else None}}
            if pack['status']=='public_data':
                from .public_places import ATTRIBUTION
                value.update(provider='openstreetmap',source_kind='public_map',attribution=ATTRIBUTION)
            output.append(value)
        return output

    def _candidate(self,con,row,admin=False):
        return self._candidates(con,[row],admin)[0]

    def _catalog_rows(self,con,city,*,categories=None,place_ids=None,public=False,include_unapproved=False,available_only=False,origin=None):
        from .public_places import TTL,POLICY
        clauses=['p.deleted_at IS NULL'];args=[]
        if public:
            clauses += ["p.provider='openstreetmap'","p.identity_status='needs_confirmation'","c.status='public_data'","k.status='public_data'",'k.updated_at>?']
            args.append((datetime.now(timezone.utc)-TTL).isoformat())
        elif not include_unapproved:
            clauses += ["c.status='approved'","k.status='approved'","p.identity_status='verified'"]
        if not include_unapproved:clauses.append("NOT EXISTS(SELECT 1 FROM discovery_tombstones t WHERE t.kind='pack' AND t.target_id=k.id)")
        if city:clauses.append('p.city=?');args.append(city)
        for column,values in [('c.category',categories),('p.id',place_ids)]:
            if values is not None:
                values=sorted(set(values))
                if not values:return []
                clauses.append(column+' IN ('+','.join('?' for _ in values)+')');args.extend(values)
        if public or available_only:
            clauses.append("EXISTS(SELECT 1 FROM place_facts f JOIN evidence_sources s ON s.id=f.source_id WHERE f.place_id=p.id AND f.expires_at>? AND s.status='active' AND s.read_confirmed=1 AND s.display_permitted=1 AND s.policy_version=f.policy_version AND NOT EXISTS(SELECT 1 FROM discovery_tombstones t WHERE t.kind='source' AND t.target_id=s.id))")
            args.append(now())
        if not include_unapproved and not self.allow_synthetic:clauses.append('k.synthetic=0')
        order='p.id,c.id'
        if origin and origin.get('latitude') is not None and origin.get('longitude') is not None:
            # Cheap deterministic spatial ordering before the bounded in-memory hard-condition pass.
            import math
            scale=math.cos(math.radians(origin['latitude']))**2
            order='CASE WHEN c.latitude IS NULL OR c.longitude IS NULL THEN 1 ELSE 0 END,((c.latitude-?)*(c.latitude-?)+?*(c.longitude-?)*(c.longitude-?)),p.id,c.id'
            args.extend([origin['latitude'],origin['latitude'],scale,origin['longitude'],origin['longitude']])
        # A request evaluates at most 1000 stored rows; provider collection remains capped at 60.
        return con.execute("SELECT c.*,p.name,p.address,p.city,p.identity_status,p.version AS identity_version FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id JOIN place_identities p ON p.id=c.place_id WHERE "+' AND '.join(clauses)+' ORDER BY '+order+' LIMIT 1000',args).fetchall()

    def _public_catalog(self,con,city,excluded,*,categories=None,place_ids=None,origin=None,limit=None):
        rows=self._catalog_rows(con,city,categories=categories,place_ids=place_ids,origin=origin,public=True)
        values=self._candidates(con,rows);items={}
        for value in values:
            if value['place_id'] not in excluded:
                value['excluded']=False;items.setdefault(value['place_id'],value)
        result=list(items.values())
        return result if limit is None else result[:limit]

    def _catalog_on(self,con,trip_id,city=None,*,include_unapproved=False,include_photos=True,categories=None,place_ids=None,origin=None,available_only=False):
        rows=self._catalog_rows(con,city,categories=categories,place_ids=place_ids,origin=origin,include_unapproved=include_unapproved,available_only=available_only)
        excluded={r['place_id'] for r in con.execute('SELECT place_id FROM discovery_exclusions WHERE trip_id=?',(trip_id,))}
        items={}
        for value in self._candidates(con,rows,include_unapproved):
            value['excluded']=value['place_id'] in excluded
            items.setdefault(value['place_id'],value)
        for value in self._public_catalog(con,city,excluded,categories=categories,place_ids=place_ids,origin=origin):items.setdefault(value['place_id'],value)
        values=sorted(items.values(),key=lambda p:p['place_id'])
        if include_photos:
            from .photos import for_places
            images=for_places(con,[p['place_id'] for p in values])
            for value in values:value.update(images[value['place_id']])
        return values

    def catalog(self,actor,trip_id,city=None,include_unapproved=False,*,include_photos=True,categories=None,place_ids=None,origin=None):
        with self.db.connect() as con:
            self.repo._trip(con,actor.id,trip_id)
            if include_unapproved:self._admin(con,actor)
            return self._catalog_on(con,trip_id,city,include_unapproved=include_unapproved,include_photos=include_photos,categories=categories,place_ids=place_ids,origin=origin)
    def detail(self,actor,trip_id,place_id):
        self.repo.get_trip(actor.id,trip_id)
        with self.db.connect() as con:
            place=self._visible_place(con,place_id)
            retained=con.execute('SELECT 1 FROM bookmarks WHERE trip_id=? AND matched_place_id=? AND deleted_at IS NULL',(trip_id,place_id)).fetchone()
            if not place and retained:place=con.execute('SELECT * FROM place_identities WHERE id=? AND deleted_at IS NULL',(place_id,)).fetchone()
            if not place:deny('NOT_FOUND','장소를 찾을 수 없습니다.',404)
            candidate=con.execute('SELECT c.*,p.name,p.address,p.city,p.identity_status FROM research_candidates c JOIN place_identities p ON p.id=c.place_id WHERE c.place_id=? ORDER BY c.updated_at DESC LIMIT 1',(place_id,)).fetchone()
            facts,sources=self._facts(con,place_id)
            basic={'id':place_id,'name':place['name'],'display_name':place['name'],'native_name':candidate['native_name'] if candidate else None,'city':place['city'],'address':place['address'],'identity_status':place['identity_status'],'categories':[candidate['category']] if candidate else [],'canonical_url':candidate['canonical_url'] if candidate else place['source_url']}
            from .photos import for_place
            basic.update(for_place(con,place_id))
            if place['provider']=='openstreetmap':
                from .public_places import ATTRIBUTION
                basic.update(provider='openstreetmap',source_kind='public_map',attribution=ATTRIBUTION,latitude=candidate['latitude'] if candidate else None,longitude=candidate['longitude'] if candidate else None,tags=json.loads(candidate['tags_json']) if candidate else [])
            excluded=bool(con.execute('SELECT 1 FROM discovery_exclusions WHERE trip_id=? AND place_id=?',(trip_id,place_id)).fetchone())
        try:evidence=self.reviews.evidence(actor,trip_id,place_id)
        except DomainError:evidence={'state':'unavailable','metrics':None,'evaluation':{'decision':'unsupported','strict_pass':False,'reason_codes':['NO_LINKED_REVIEW_EVIDENCE']}}
        return {'place':basic,'facts':facts,'sources':sources,'review_evidence':evidence,'excluded':excluded,'reservation_action':'external_link_only','live_availability':'unknown'}
    def reapply_tombstones(self):
        with self.db.connect() as con:
            con.execute("UPDATE evidence_sources SET status='revoked',display_permitted=0 WHERE id IN (SELECT target_id FROM discovery_tombstones WHERE kind='source')")
            con.execute("UPDATE candidate_packs SET status='disabled' WHERE id IN (SELECT target_id FROM discovery_tombstones WHERE kind='pack')")
            con.execute("UPDATE research_candidates SET status='disabled' WHERE pack_id IN (SELECT target_id FROM discovery_tombstones WHERE kind='pack')")
            con.execute("UPDATE bookmarks SET deleted_at=COALESCE(deleted_at,?),note='',input_value='',normalized_input='',candidates_json='[]' WHERE id IN (SELECT target_id FROM discovery_tombstones WHERE kind='bookmark')",(now(),))
    scrub_trip=staticmethod(scrub_trip)
