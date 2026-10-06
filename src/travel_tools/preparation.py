from copy import deepcopy
from datetime import datetime,timezone
import json
from src.foundation.repository import DomainError,dump,new_id,utcnow
from src.itineraries.service import digest
from .rules import calculate,release_timing
from .exports import calendar,inquiry,validate_draft


def fail(code,message,status=422,details=None): raise DomainError(code,message,status,details)

class Preparation:
    def __init__(self,db,repo,itineraries,discovery):
        self.db,self.repo,self.itineraries,self.discovery=db,repo,itineraries,discovery

    def _scope(self,con,actor,trip): return self.itineraries._scope(con,actor,trip)

    def _inputs(self,con,actor,trip,body):
        t=self.repo._trip_dto(con,self._scope(con,actor,trip));value=deepcopy(body)
        if not t['start_date']<=value['visit_date']<=t['end_date']: fail('DATE_OUTSIDE_TRIP','방문일을 여행 기간 안에서 선택해 주세요.',details={'field':'visit_date'})
        if not value.get('party'): value['party']=t['party'];value['party_origin']='trip'
        else: value['party_origin']='explicit'
        if value.get('place_id'):
            place=self._place(con,value['place_id'])
            value['place_name']=place['name']
            if {'tokyo':'Asia/Tokyo','barcelona':'Europe/Madrid'}.get(place['city'])!=value['timezone']:fail('FACILITY_TIMEZONE_MISMATCH','지점의 시설 시간대를 확인해 주세요.',details={'field':'timezone'})
        if value.get('booking_id'):
            self.repo._booking(con,actor.id,trip,value['booking_id'])
        if value.get('item_id'):
            if not value.get('itinerary_id'): fail('ITINERARY_REQUIRED','연결할 일정을 선택해 주세요.')
            row=self.itineraries._get(con,actor,trip,value['itinerary_id'])
            rev=con.execute('SELECT result_json FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone()
            item=next((i for i in json.loads(rev[0])['items'] if i['item_id']==value['item_id']),None) if rev else None
            if not item or (value.get('place_id') and item.get('place_id')!=value['place_id']): fail('NOT_FOUND','연결할 항목을 찾을 수 없습니다.',404)
        value.setdefault('place_name',None)
        value.update(status='needs_confirmation',reported_at=None,evidence=None,rule=None,calculation=calculate(None,value['visit_date'],value['timezone']))
        return value

    def _place(self,con,ident):
        row=con.execute("SELECT p.* FROM place_identities p WHERE p.id=? AND p.deleted_at IS NULL AND p.identity_status='verified' AND EXISTS(SELECT 1 FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id WHERE c.place_id=p.id AND c.status='approved' AND k.status='approved')",(ident,)).fetchone()
        if not row: fail('NOT_FOUND','검증한 지점을 찾을 수 없습니다.',404)
        return row

    def _manifest(self,con,actor,trip,value):
        t=self.repo._trip_dto(con,self._scope(con,actor,trip));fact=None;reasons=[]
        if value.get('place_id'):
            try:self._place(con,value['place_id'])
            except DomainError: reasons.append('PLACE_UNAVAILABLE')
        if value.get('rule_fact_id') and value.get('place_id'):
            facts,sources=self.discovery._facts(con,value['place_id']);source_by={s['id']:s for s in sources}
            candidate=next((f for f in facts if f['id']==value['rule_fact_id'] and f['field']=='booking_open_rule'),None)
            source=source_by.get(candidate['source_id'],{}) if candidate else {}
            if candidate:
                fact={**candidate,'source':source}
                day=value['visit_date']
                valid=(not candidate['valid_for_date'] or candidate['valid_for_date']==day) and (not candidate['valid_from'] or candidate['valid_from']<=day) and (not candidate['valid_until'] or day<=candidate['valid_until'])
                official=source.get('source_type')=='official' or (self.discovery.allow_synthetic and source.get('source_type')=='synthetic')
                if not candidate['usable'] or candidate['status']!='verified' or not valid or not official: reasons.append('RULE_EVIDENCE_UNAVAILABLE')
            else: reasons.append('RULE_NOT_FOUND')
        else: reasons.append('RULE_UNCONFIRMED')
        if not t['start_date']<=value['visit_date']<=t['end_date']: reasons.append('DATE_OUTSIDE_TRIP')
        if value.get('party_origin')=='trip' and value['party']!=t['party']: reasons.append('PARTY_CHANGED')
        evidence=value.get('evidence');booking=None
        if evidence:
            row=con.execute('SELECT * FROM bookings WHERE id=? AND trip_id=? AND deleted_at IS NULL',(evidence['booking_id'],trip)).fetchone()
            booking=dict(row) if row else None
            if not row or row['version']!=evidence['booking_version']:reasons.append('EVIDENCE_CHANGED')
        # The digest is stored, not the private booking payload.
        return digest({'trip_version':t['version'],'rule':fact,'booking':booking,'reasons':reasons}),fact,reasons

    def _refresh(self,con,actor,trip,row,force=False):
        value=json.loads(row['payload_json']);manifest,fact,reasons=self._manifest(con,actor,trip,value)
        if manifest!=row['manifest'] or force:
            value['rule_version']=digest(fact) if fact else None
            value['rule']=fact
            value['calculation']=calculate(fact['value'] if fact and not reasons else None,value['visit_date'],value['timezone'])
            value['confirmation_reasons']=reasons+([value['calculation']['reason']] if value['calculation']['reason'] else [])
            if row['manifest'] and value['status']!='cancelled':
                value['status']='needs_confirmation';value['evidence']=None
            self._save(con,row,value,manifest,'revalidate')
            row=dict(row);row.update(version=row['version']+1,payload_json=dump(value),manifest=manifest,updated_at=utcnow())
        return self._dto(row)

    @staticmethod
    def _dto(row):
        value=json.loads(row['payload_json'])
        return {**value,'release_timing':release_timing(value['calculation'],value['timezone']),**{k:row[k] for k in ('id','trip_id','version','created_at','updated_at')}}

    def _save(self,con,row,value,manifest,action):
        stamp=utcnow();version=row['version']+1
        con.execute('UPDATE reservation_tasks SET payload_json=?,manifest=?,version=?,updated_at=? WHERE id=?',(dump(value),manifest,version,stamp,row['id']))
        con.execute('INSERT INTO reservation_task_events VALUES(?,?,?,?,?,?)',(new_id('tevent'),row['id'],version,action,dump(value),stamp))

    def _row(self,con,actor,trip,ident):
        self._scope(con,actor,trip)
        row=con.execute('SELECT * FROM reservation_tasks WHERE id=? AND trip_id=? AND owner_id=?',(ident,trip,actor.id)).fetchone()
        if not row: fail('NOT_FOUND','예약 준비 항목을 찾을 수 없습니다.',404)
        return dict(row)

    def create(self,actor,trip,body,key):
        if not key or len(key)>160: fail('IDEMPOTENCY_KEY_REQUIRED','요청 식별자를 확인해 주세요.')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._scope(con,actor,trip)
            old=con.execute('SELECT * FROM reservation_tasks WHERE trip_id=? AND owner_id=? AND idempotency_key=?',(trip,actor.id,key)).fetchone()
            if old:
                if old['input_hash']!=digest(body): fail('IDEMPOTENCY_CONFLICT','같은 요청 식별자의 입력이 다릅니다.',409)
                return self._refresh(con,actor,trip,old)
            value=self._inputs(con,actor,trip,body);manifest,fact,reasons=self._manifest(con,actor,trip,value)
            value['rule_version']=digest(fact) if fact else None;value['rule']=fact;value['calculation']=calculate(fact['value'] if fact and not reasons else None,value['visit_date'],value['timezone'])
            value['confirmation_reasons']=reasons
            if value['calculation']['due_precision']!='unknown': value['status']='waiting_open'
            stamp=utcnow();ident=new_id('task')
            con.execute('INSERT INTO reservation_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,trip,actor.id,1,dump(value),manifest,key,digest(body),stamp,stamp))
            con.execute('INSERT INTO reservation_task_events VALUES(?,?,?,?,?,?)',(new_id('tevent'),ident,1,'create',dump(value),stamp))
            return self._dto(self._row(con,actor,trip,ident))

    def list(self,actor,trip):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self._scope(con,actor,trip)
            return {'items':[self._refresh(con,actor,trip,r) for r in con.execute('SELECT * FROM reservation_tasks WHERE trip_id=? AND owner_id=? ORDER BY created_at,id',(trip,actor.id)).fetchall()]}

    def get(self,actor,trip,ident):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');result=self._refresh(con,actor,trip,self._row(con,actor,trip,ident))
            result['history']=[{'version':r['version'],'action':r['action'],'created_at':r['created_at']} for r in con.execute('SELECT * FROM reservation_task_events WHERE task_id=? ORDER BY version',(ident,))]
            return result

    def command(self,actor,trip,ident,body):
        # Refresh commits first so even a rejected stale command leaves invalid evidence revoked.
        current=self.get(actor,trip,ident)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._row(con,actor,trip,ident)
            if row['version']!=body['expected_version']:fail('VERSION_CONFLICT','준비 항목이 변경되었습니다. 최신 내용을 확인해 주세요.',409)
            value=json.loads(row['payload_json']);action=body['action']
            manifest,_,_=self._manifest(con,actor,trip,value)
            if manifest!=row['manifest']:fail('SOURCE_DATA_CHANGED','근거가 변경되었습니다. 다시 확인해 주세요.',409)
            if action=='verify_evidence':
                if value['status']!='user_completed': fail('COMPLETION_REPORT_REQUIRED','먼저 사용자 완료를 표시하고 증빙을 대조해 주세요.')
                booking=con.execute('SELECT * FROM bookings WHERE id=? AND trip_id=? AND deleted_at IS NULL',(body.get('booking_id'),trip)).fetchone()
                if not booking: fail('NOT_FOUND','예약 증빙을 찾을 수 없습니다.',404)
                effective=json.loads(booking['effective_json'])
                doc=con.execute('SELECT * FROM source_documents WHERE id=? AND trip_id=? AND deleted_at IS NULL',(booking['document_id'],trip)).fetchone() if booking['document_id'] else None
                if not doc: fail('DOCUMENT_EVIDENCE_REQUIRED','직접 입력한 예약은 메일 증빙 확인으로 표시할 수 없습니다.')
                mismatches=[k for k,a,b in [('date',value['visit_date'],effective.get('date')),('place_id',value.get('place_id'),effective.get('place_id')),('party',value['party'],effective.get('party'))] if a is None or a!=b]
                if effective.get('status')=='cancelled':mismatches.append('cancelled')
                if mismatches:fail('EVIDENCE_MISMATCH','예약에서 날짜·검증한 지점·인원을 확인하고 교정해 주세요.',details={'fields':mismatches})
                value.update(status='evidence_verified',evidence={'booking_id':booking['id'],'booking_version':booking['version'],'document_id':doc['id'],'verified_at':utcnow(),'verified_by':actor.id,'method':'user_reviewed_structured_match'})
            elif action=='update':
                if not body.get('changes'):fail('CHANGES_REQUIRED','수정할 입력이 필요합니다.')
                value=self._inputs(con,actor,trip,body['changes'])
            elif action=='revalidate':value['status']='needs_confirmation';value['evidence']=None
            else:
                allowed={'start':({'needs_confirmation','waiting_open'},'in_progress'),'report_complete':({'needs_confirmation','waiting_open','in_progress'},'user_completed'),'cancel':({'needs_confirmation','waiting_open','in_progress','user_completed','evidence_verified'},'cancelled'),'reopen':({'cancelled','user_completed','evidence_verified'},'needs_confirmation')}
                states,target=allowed[action]
                if value['status'] not in states:fail('INVALID_TRANSITION','현재 상태에서 실행할 수 없는 변경입니다.',409)
                value['status']=target
                if action=='report_complete':value['reported_at']=utcnow()
                if action in ('cancel','reopen'):value['evidence']=None
            manifest,fact,reasons=self._manifest(con,actor,trip,value);value['rule_version']=digest(fact) if fact else None;value['rule']=fact;value['confirmation_reasons']=reasons
            value['calculation']=calculate(fact['value'] if fact and not reasons else None,value['visit_date'],value['timezone'])
            self._save(con,row,value,manifest,action)
            if action in ('report_complete','verify_evidence'):
                from src.product.events import record
                record(con,actor.id,trip,'booking_task_complete',f"server:task:{ident}:{row['version']+1}",place=value.get('place_id'),detail={'task_id':ident,'completion_kind':value['status']})
        return self.get(actor,trip,ident)

    def export(self,actor,trip,ident):
        try:return calendar(self.get(actor,trip,ident))
        except ValueError:fail('DATE_UNCONFIRMED','날짜를 확인한 뒤 캘린더 파일을 다운로드해 주세요.')

    def draft(self,actor,trip,ident,body):
        task=self.get(actor,trip,ident)
        if task['version']!=body['expected_version']:fail('VERSION_CONFLICT','최신 준비 항목을 확인해 주세요.',409)
        return validate_draft(task,inquiry(task,body['language']),body['language'])
