"""Private stays, durable provider identification and explicit branch selection."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import uuid4
from src.foundation.models import local_to_instant
from src.foundation.repository import DomainError
from src.destinations import city_key
from src.discovery.safe_fetch import validate_public_url
from src.reliability.budget import CallContext
from .models import AccommodationInput
from .origin import coordinates

HOTEL_KINDS={'숙소','호텔','hotel','lodging','accommodation'}
def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def stamp():return datetime.now(timezone.utc).isoformat()
def fail(code,message,status=409):raise DomainError(code,message,status)
def expired(value,clock):return bool(value and datetime.fromisoformat(value.replace('Z','+00:00'))<=datetime.fromisoformat(clock.replace('Z','+00:00')))
def synthetic(value):return any(word in value.casefold() for word in ('synthetic','[test]','fixture','가상','테스트용','example.com','example.test','example.invalid'))

def dto(row,clock=None):
    value=dict(row)
    for key in ('identity','candidates','reason_codes','source'):value[key]=json.loads(value.pop(key+'_json'))
    value.pop('legacy_stop_id',None)
    value['dates_confirmed']=bool(value['dates_confirmed'])
    now=clock or stamp()
    had_candidates=bool(value['candidates'])
    value['candidates']=[c for c in value['candidates'] if not expired(c.get('expires_at'),now)]
    if had_candidates and not value['candidates'] and value['identity_state']=='candidates':
        value['identity_state']='unavailable';value['reason_codes']=[*value['reason_codes'],'CANDIDATES_EXPIRED']
    if value['identity'] and expired(value['identity'].get('expires_at'),now):
        value['identity_state']='needs_reconfirmation';value['reason_codes']=[*value['reason_codes'],'COORDINATES_EXPIRED']
        # Storage is private, but expired provider display permissions are not extended by a GET.
        value['identity']={key:value['identity'].get(key) for key in ('provider','provider_place_id','adapter_version','expires_at','provenance')}
    return value

class Accommodations:
    def __init__(self,db,repo,jobs,geocoder=None,gateway=None,*,allow_synthetic=False):
        self.db,self.repo,self.jobs,self.gateway=db,repo,jobs,gateway
        if geocoder is None:
            from src.location.providers import DisabledGeocodingProvider
            geocoder=DisabledGeocodingProvider()
        self.geocoder,self.allow_synthetic=geocoder,allow_synthetic
    def _scope(self,con,actor,trip_id):
        self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
        return self.repo._trip_dto(con,self.repo._trip(con,actor.id,trip_id))
    def _get(self,con,actor,trip_id,ident):
        self._scope(con,actor,trip_id)
        row=con.execute('SELECT * FROM trip_accommodations WHERE id=? AND owner_id=? AND trip_id=? AND deleted_at IS NULL',(ident,actor.id,trip_id)).fetchone()
        if not row:fail('NOT_FOUND','숙소를 찾을 수 없습니다.',404)
        return row
    def _references(self,con,actor,trip,body):
        stop=next((s for s in trip['stops'] if s['id']==body['stop_id']),None)
        if not stop:fail('NOT_FOUND','도시 구간을 찾을 수 없습니다.',404)
        booking=None
        if body.get('booking_id'):
            row=self.repo._booking(con,actor.id,trip['id'],body['booking_id'])
            booking=self.repo._booking_dto(con,row)
            if (booking.get('kind') or '').casefold() not in HOTEL_KINDS:fail('BOOKING_KIND_MISMATCH','숙박 예약만 연결할 수 있습니다.',422)
        return stop,booking
    def _prepare(self,con,actor,trip,body):
        body={**body};body.pop('expected_version',None)
        stop,booking=self._references(con,actor,trip,body)
        body['facility_timezone']=body.get('facility_timezone') or stop['timezone']
        source={'kind':'user_input','version':'accommodation_v1','permission':'private_user_input','retention':'until_user_deletion','dates_status':'user_confirmed' if body.get('dates_confirmed') else 'suggested'}
        if booking:
            label=booking.get('provider') or booking.get('location') or '숙박 예약'
            if body['input_kind']=='booking':body['input_value']=label
            source.update(kind='booking_suggestion',booking_id=booking['id'],booking_version=booking['version'],booking_status=booking['status'],synthetic=synthetic(encode(booking)))
            # Values remain suggestions in a separate model. Original extraction/overrides stay untouched.
            body['checkin_date']=body.get('checkin_date') or booking.get('date')
            body['checkout_date']=body.get('checkout_date') or booking.get('date_end')
        if not body.get('checkin_date') and not body.get('checkout_date') and stop['end_date']>stop['start_date']:
            body.update(checkin_date=stop['start_date'],checkout_date=stop['end_date'],dates_confirmed=False)
            source['dates_status']='suggested'
        try:body=AccommodationInput.model_validate(body).model_dump(mode='json')
        except ValueError:fail('VALIDATION_FAILED','숙박 날짜·시각과 시설 시간대를 확인해 주세요. 서머타임으로 중복되거나 없는 시각은 확정할 수 없습니다.',422)
        if body['input_kind']=='map_url':
            try:validate_public_url(body['input_value'])
            except (ValueError,TypeError):fail('URL_INVALID','공개 지도 링크를 입력해 주세요.',422)
        for boundary in ('checkin','checkout'):
            if body.get(boundary+'_date') and body.get(boundary+'_time'):
                local_to_instant(body[boundary+'_date']+'T'+body[boundary+'_time'],body['facility_timezone'])
        body['display_name']=body.get('display_name') or ('지도 링크로 저장한 숙소' if body['input_kind']=='map_url' else body['input_value'][:300])
        body['city']=city_key(stop['city']) or stop['city'];body['source']=source
        return body
    def provider_status(self):
        return {'enabled':bool(getattr(self.geocoder,'enabled',False) and getattr(self.geocoder,'usage_permitted',False)),
            'name':getattr(self.geocoder,'name','disabled'),'adapter_version':getattr(self.geocoder,'adapter_version','disabled_v1'),
            'disclosure':'지점 확인을 요청하면 입력한 숙소 이름 또는 지도 링크와 도시를 선택된 지도 제공자에게 보냅니다. 메일 원문·예약번호·개인 메모는 보내지 않습니다.'}
    def list(self,actor,trip_id):
        with self.db.connect() as con:
            trip=self._scope(con,actor,trip_id)
            items=[dto(row,self.jobs.now()) for row in con.execute('SELECT * FROM trip_accommodations WHERE owner_id=? AND trip_id=? AND deleted_at IS NULL ORDER BY created_at,id',(actor.id,trip_id))]
            suggestions=[]
            for row in con.execute('SELECT * FROM bookings WHERE trip_id=? AND deleted_at IS NULL ORDER BY created_at,id',(trip_id,)):
                booking=self.repo._booking_dto(con,row)
                if (booking.get('kind') or '').casefold() not in HOTEL_KINDS or booking.get('status')=='cancelled':continue
                suggestions.append({'booking_id':booking['id'],'booking_version':booking['version'],'display_name':booking.get('provider') or booking.get('location') or '숙박 예약',
                    'checkin_date':booking.get('date'),'checkout_date':booking.get('date_end'),'booking_status':booking['status'],
                    'linked':any(i['booking_id']==booking['id'] for i in items),'synthetic':synthetic(encode(booking)),
                    'stop_candidates':[s['id'] for s in trip['stops'] if booking.get('date') and s['start_date']<=booking['date']<=s['end_date']]})
        return {'items':items,'booking_suggestions':suggestions,'provider':self.provider_status()}
    def get(self,actor,trip_id,ident):
        with self.db.connect() as con:return dto(self._get(con,actor,trip_id,ident),self.jobs.now())
    def create(self,actor,trip_id,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');trip=self._scope(con,actor,trip_id);data=self._prepare(con,actor,trip,body)
            ident='stay_'+uuid4().hex;now=self.jobs.now()
            fields=('stop_id','display_name','input_kind','input_value','private_note','booking_id','city','facility_timezone','checkin_date','checkout_date','checkin_time','checkout_time','dates_confirmed')
            con.execute('INSERT INTO trip_accommodations(id,owner_id,trip_id,'+','.join(fields)+',source_json,created_at,updated_at) VALUES('+','.join('?' for _ in range(len(fields)+6))+')',
                (ident,actor.id,trip_id,*(int(data[k]) if k=='dates_confirmed' else data[k] for k in fields),encode(data['source']),now,now))
            return dto(con.execute('SELECT * FROM trip_accommodations WHERE id=?',(ident,)).fetchone(),now)
    def patch(self,actor,trip_id,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');old=self._get(con,actor,trip_id,ident)
            if old['version']!=body['expected_version']:fail('VERSION_CONFLICT','다른 화면에서 숙소가 변경되었습니다. 입력을 보존하고 다시 확인해 주세요.')
            data=self._prepare(con,actor,self._scope(con,actor,trip_id),body)
            fields=('stop_id','display_name','input_kind','input_value','private_note','booking_id','city','facility_timezone','checkin_date','checkout_date','checkin_time','checkout_time','dates_confirmed')
            identity_changed=any(old[k]!=data[k] for k in ('stop_id','input_kind','input_value','booking_id'))
            con.execute('UPDATE trip_accommodations SET '+','.join(k+'=?' for k in fields)+',source_json=?,version=version+1,updated_at=?,identity_state=?,identity_json=?,candidates_json=?,reason_codes_json=? WHERE id=?',
                (*(int(data[k]) if k=='dates_confirmed' else data[k] for k in fields),encode(data['source']),self.jobs.now(),'needs_reconfirmation' if identity_changed else old['identity_state'],'{}' if identity_changed else old['identity_json'],'[]' if identity_changed else old['candidates_json'],encode(['INPUT_CHANGED']) if identity_changed else old['reason_codes_json'],ident))
        return self.get(actor,trip_id,ident)
    def delete(self,actor,trip_id,ident,version):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._get(con,actor,trip_id,ident)
            if row['version']!=version:fail('VERSION_CONFLICT','숙소가 변경되었습니다. 최신 내용을 확인해 주세요.')
            self.repo._tombstone(con,'accommodation',ident,trip_id)
            self.scrub_one(con,ident,self.jobs.now(),self.gateway.result_dir if self.gateway else None)
        return {'id':ident,'deleted':True}
    @staticmethod
    def scrub_one(con,ident,now,result_dir=None):
        # Normalized geocoding receipts are private too; preserve charge ledger amounts,
        # erase their payloads/references. Fenced writers cannot recreate these after deletion.
        jobs=[r['id'] for r in con.execute("SELECT id,payload_json FROM jobs WHERE operation='accommodation_resolve' AND trip_id=(SELECT trip_id FROM trip_accommodations WHERE id=?)",(ident,)) if json.loads(r['payload_json']).get('accommodation_id')==ident]
        from pathlib import Path
        for job_id in jobs:
            for record in con.execute('SELECT call_id,trip_id,result_ref FROM usage_reservations WHERE job_id=?',(job_id,)).fetchall():
                ref=record['result_ref'] or (record['trip_id']+'/'+record['call_id']+'.json')
                if hasattr(con,'raw'):con.execute('DELETE FROM durable_artifacts WHERE ref=?',(ref,))
                if result_dir:
                    root=Path(result_dir).resolve();path=(root/ref).resolve()
                    if path.is_relative_to(root):path.unlink(missing_ok=True)
                con.execute('UPDATE usage_reservations SET result_ref=NULL WHERE call_id=?',(record['call_id'],))
            con.execute("UPDATE jobs SET checkpoint_json='{}',result_json='{}' WHERE id=?",(job_id,))
        con.execute('DELETE FROM accommodation_resolutions WHERE accommodation_id=?',(ident,))
        con.execute("UPDATE trip_accommodations SET display_name='',input_value='',private_note='',identity_json='{}',candidates_json='[]',reason_codes_json='[]',source_json='{}',checkin_date=NULL,checkout_date=NULL,checkin_time=NULL,checkout_time=NULL,booking_id=NULL,identity_state='unavailable',version=version+1,deleted_at=? WHERE id=?",(now,ident))
    def resolve(self,actor,trip_id,ident,body,key):
        fingerprint=hashlib.sha256(encode({'accommodation_id':ident,**body}).encode()).hexdigest()
        existing=self.jobs.lookup(actor.id,actor.session_id,'personal_trip',trip_id,'accommodation_resolve',key,fingerprint)
        if existing:
            self.get(actor,trip_id,ident)
            return self._receipt(existing['id'],ident)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._get(con,actor,trip_id,ident)
            if row['version']!=body['expected_version']:fail('VERSION_CONFLICT','숙소가 변경되었습니다. 최신 내용을 확인해 주세요.')
            if row['job_id']:
                pending=con.execute("SELECT * FROM jobs WHERE id=? AND state IN ('queued','running')",(row['job_id'],)).fetchone()
                if pending:
                    if pending['payload_hash']!=fingerprint:fail('RESOLUTION_IN_PROGRESS','다른 확인 조건의 작업이 진행 중입니다. 완료 후 다시 확인해 주세요.')
                    # Bind additional client keys to the same in-flight intent too.
                    # Otherwise a retry key could later be reused with different input.
                    con.execute('INSERT INTO idempotency_keys VALUES(?,?,?,?,?,?,?,?,?)',(actor.id,'personal_trip',trip_id,'accommodation_resolve',hashlib.sha256(key.encode()).hexdigest(),fingerprint,pending['id'],self.jobs.later(30*86400),self.jobs.now()))
                    return self._receipt(pending['id'],ident)
            if self.provider_status()['enabled'] and getattr(self.geocoder,'external',True) and not body.get('consent_to_provider'):
                fail('PROVIDER_CONSENT_REQUIRED','숙소 이름·도시를 지도 제공자에게 보내는 지점 확인에 동의해 주세요.',422)
            job=self.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'accommodation_resolve',
                {'accommodation_id':ident,'accommodation_version':row['version'],'max_candidates':body.get('max_candidates',5),'consent_to_provider':body.get('consent_to_provider',False)},
                self._scope(con,actor,trip_id)['version'],key,request_fingerprint=fingerprint,deadline_seconds=60,max_attempts=2,con=con)
            con.execute('UPDATE trip_accommodations SET job_id=?,updated_at=? WHERE id=?',(job['id'],self.jobs.now(),ident))
        return self._receipt(job['id'],ident)
    @staticmethod
    def _receipt(job_id,ident):return {'job_id':job_id,'accommodation_id':ident,'status_url':'/api/v2/jobs/'+job_id,'events_url':'/api/v2/jobs/'+job_id+'/events','state':'queued'}
    def execute_resolution(self,job,ctx):
        actor=SimpleNamespace(id=job['actor_id'],session_id=job['session_id']);ident=job['payload']['accommodation_id']
        def guard(*,con=None):
            def check(connection):
                ctx.guard(con=connection);row=self._get(connection,actor,job['trip_id'],ident)
                if row['version']!=job['payload']['accommodation_version'] or row['job_id']!=job['id']:fail('VERSION_CONFLICT','숙소가 바뀌어 이전 지점 결과를 적용하지 않았습니다.')
                return dict(row)
            if con is not None:return check(con)
            with self.db.connect() as connection:return check(connection)
        # A worker may die after activating candidates but before recording job success.
        # The same job/version activation is itself a durable completion checkpoint.
        with self.db.connect() as con:
            ctx.guard(con=con);current=self._get(con,actor,job['trip_id'],ident)
            receipt=con.execute('SELECT * FROM accommodation_resolutions WHERE job_id=?',(job['id'],)).fetchone()
            if receipt and current['job_id']==job['id'] and current['version']==job['payload']['accommodation_version']+1 and current['identity_state'] in ('candidates','unavailable'):
                return {'accommodation_id':ident,'identity_state':current['identity_state'],'candidate_count':len(json.loads(current['candidates_json'])),'checkpoint_reused':True}
        saved=guard()
        if receipt: result=json.loads(receipt['result_json'])
        else:
            source=json.loads(saved['source_json']);provider=self.geocoder
            blocked_synthetic=(source.get('synthetic') or synthetic(saved['input_value'])) and getattr(provider,'external',True)
            if blocked_synthetic:
                result={'status':'unavailable','candidates':[],'reason_codes':['SYNTHETIC_INPUT_NO_LIVE_LOOKUP']}
            elif os.getenv('ZERO_SPEND')=='1' and getattr(provider,'external',True):
                result={'status':'unavailable','candidates':[],'reason_codes':['ZERO_SPEND']}
            elif not self.provider_status()['enabled']:
                result={'status':'unavailable','candidates':[],'reason_codes':['GEOCODING_DISABLED']}
            elif not self.gateway:
                fail('PROVIDER_GATEWAY_UNAVAILABLE','지도 제공자의 비용 관리가 준비되지 않았습니다.',503)
            else:
                if getattr(provider,'external',True) and not job['payload'].get('consent_to_provider'):fail('PROVIDER_CONSENT_REQUIRED','지도 제공자 전송 동의가 필요합니다.',422)
                ctx.progress('identifying_accommodation',done=0,total=1)
                limits={'max_candidates':job['payload']['max_candidates'],'timeout_seconds':8,'max_attempts':1,'scope_kind':'private_accommodation'}
                request_hash=hashlib.sha256(encode({'query':saved['input_value'],'city':saved['city'],'limits':limits,'provider':provider.name,'adapter_version':provider.adapter_version}).encode()).hexdigest()
                result=self.gateway.run(CallContext(owner_id=actor.id,scope_id=job['trip_id'],trip_id=job['trip_id'],job_id=job['id'],actor_id=actor.id),
                    'geocoding',job['id']+':identify',{'requests':1},lambda:provider.resolve(saved['input_value'],saved['city'],limits),
                    request_hash=request_hash,provider=provider.name,sku=provider.sku,guard=guard,max_attempts=1,
                    deadline=datetime.fromisoformat(job['deadline_at']).timestamp())
            result=self._normalize(result,job['payload']['max_candidates'],saved['city'])
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE');guard(con=con)
                receipt_id='stay_resolution_'+uuid4().hex
                con.execute('INSERT INTO accommodation_resolutions VALUES(?,?,?,?,?,?,?,?,?)',(receipt_id,ident,actor.id,job['trip_id'],job['id'],saved['version'],encode(result),self.jobs.now(),result.get('expires_at')))
            ctx.checkpoint({'resolution_id':receipt_id},stage='identity_received',done=len(result['candidates']),total=len(result['candidates']))
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');guard(con=con)
            state='candidates' if result['candidates'] else 'unavailable'
            con.execute('UPDATE trip_accommodations SET identity_state=?,identity_json=\'{}\',candidates_json=?,reason_codes_json=?,version=version+1,updated_at=? WHERE id=?',(state,encode(result['candidates']),encode(result.get('reason_codes',[])),self.jobs.now(),ident))
        return {'accommodation_id':ident,'identity_state':state,'candidate_count':len(result['candidates']),'provider_calls':0 if result.get('provider') in (None,'disabled') else 1}
    def _normalize(self,result,cap,city):
        # Persist only the supported fields; raw provider records/profiles are never retained.
        allowed={'provider_place_id','name','original_name','address','city','latitude','longitude','timezone','precision','map_url','official_url','source_id','checked_at','expires_at','coordinate_permitted','retention_policy','usage_permission','provider','adapter_version','synthetic'}
        normalized=[]
        for raw in result.get('candidates',[])[:cap]:
            value={k:v for k,v in raw.items() if k in allowed}
            if not value.get('provider_place_id') or not value.get('name') or not value.get('address'):continue
            if city_key(value.get('city') or '')!=city_key(city) and (value.get('city') or '').casefold()!=city.casefold():continue
            if not coordinates(value) or value.get('coordinate_permitted') is not True:continue
            for key in ('map_url','official_url'):
                if value.get(key):
                    try:validate_public_url(value[key])
                    except (ValueError,TypeError):value[key]=None
            value.update(candidate_id='stay_candidate_'+uuid4().hex,provider=result.get('provider',getattr(self.geocoder,'name','unknown')),adapter_version=result.get('adapter_version',getattr(self.geocoder,'adapter_version','unknown')),provenance='provider_candidate_selected')
            value.setdefault('usage_permission',result.get('usage_permission'))
            value['coordinate_version']=hashlib.sha256(encode({key:value.get(key) for key in ('provider_place_id','latitude','longitude','checked_at','adapter_version')}).encode()).hexdigest()[:24]
            value.setdefault('checked_at',result.get('checked_at'))
            value.setdefault('expires_at',result.get('expires_at'))
            permission=value.get('usage_permission') or {}
            if not isinstance(permission,dict) or permission.get('display') is not True or permission.get('durable_storage') is not True:continue
            try:
                checked=datetime.fromisoformat(value['checked_at'].replace('Z','+00:00'));expiry=datetime.fromisoformat(value['expires_at'].replace('Z','+00:00'))
                if checked.tzinfo is None or expiry.tzinfo is None or expiry<=checked:continue
            except (ValueError,TypeError,AttributeError,KeyError):continue
            normalized.append(value)
        return {k:result.get(k) for k in ('status','provider','adapter_version','checked_at','expires_at','usage_permission','reason_codes')}|{'candidates':normalized,'reason_codes':result.get('reason_codes') or ([] if normalized else ['NO_VERIFIED_BRANCH_CANDIDATES'])}
    def select(self,actor,trip_id,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._get(con,actor,trip_id,ident)
            if row['version']!=body['expected_version']:fail('VERSION_CONFLICT','지점 후보가 변경되었습니다. 최신 목록을 확인해 주세요.')
            candidate=next((c for c in json.loads(row['candidates_json']) if c['candidate_id']==body['candidate_id']),None)
            if not candidate:fail('NOT_FOUND','이 숙소의 지점 후보를 찾을 수 없습니다.',404)
            if expired(candidate.get('expires_at'),self.jobs.now()):fail('CANDIDATE_EXPIRED','지점 확인 자료가 만료되었습니다. 다시 확인해 주세요.')
            if not coordinates(candidate) or candidate.get('coordinate_permitted') is not True:fail('COORDINATES_UNAVAILABLE','사용 가능한 지점 좌표가 없습니다.')
            con.execute("UPDATE trip_accommodations SET identity_state='confirmed',identity_json=?,reason_codes_json='[]',version=version+1,updated_at=? WHERE id=?",(encode(candidate),self.jobs.now(),ident))
        return self.get(actor,trip_id,ident)
    def origin_context(self,actor,trip_id,visit,overrides=None):
        from .origin import context_in_connection
        with self.db.connect() as con:
            self._scope(con,actor,trip_id)
            return context_in_connection(con,self.repo,actor,trip_id,visit,overrides, self.jobs.now())
