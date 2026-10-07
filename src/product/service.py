from src.destinations import CITIES
from datetime import datetime,timezone
import json
from src.foundation.repository import DomainError,dump,new_id,utcnow
from src.recommendations.service import digest
from src.recommendations.engine import Facts
from . import events
from .models import Feedback,Price
from .prices import estimate


def fail(code,message,status=422):raise DomainError(code,message,status)

class Product:
    def __init__(self,db,repo,discovery,recommendations,itineraries):
        self.db,self.repo,self.discovery,self.recommendations,self.itineraries=db,repo,discovery,recommendations,itineraries
    def scope(self,con,actor,trip):return self.itineraries._scope(con,actor,trip)
    def preferences(self,actor):
        with self.db.connect() as con:
            row=con.execute('SELECT * FROM product_preferences WHERE owner_id=?',(actor.id,)).fetchone()
            return {'analytics_enabled':bool(row and row['analytics_enabled']),'version':row['version'] if row else 0,'retention_days':events.RETENTION_DAYS,'offline_measurement':False}
    def consent(self,actor,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM product_preferences WHERE owner_id=?',(actor.id,)).fetchone()
            if (row['version'] if row else 0)!=body['expected_version']:fail('VERSION_CONFLICT','분석 설정이 변경되었습니다.',409)
            con.execute('INSERT INTO product_preferences VALUES(?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET analytics_enabled=excluded.analytics_enabled,version=excluded.version,updated_at=excluded.updated_at',(actor.id,int(body['analytics_enabled']),body['expected_version']+1,utcnow()))
            if not body['analytics_enabled']:
                con.execute("INSERT INTO discovery_tombstones VALUES('analytics_owner',?,'ANALYTICS_REVOKED',?) ON CONFLICT(kind,target_id) DO UPDATE SET created_at=excluded.created_at",(actor.id,utcnow()))
                con.execute('DELETE FROM discovery_events WHERE owner_id=?',(actor.id,));con.execute('DELETE FROM product_run_metrics WHERE owner_id=?',(actor.id,))
            events.purge(con)
        return self.preferences(actor)
    def client_event(self,actor,trip,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.scope(con,actor,trip)
            events.validate_refs(con,actor.id,trip,body['place_id'],body['run_id'],body['source_id'])
            if not self.discovery._visible_place(con,body['place_id']):fail('NOT_FOUND','장소를 찾을 수 없습니다.',404)
            skew=abs((datetime.fromisoformat(body['client_at'])-datetime.now(timezone.utc)).total_seconds())
            detail={'measurement_version':'visible_50pct_1000ms_v1'} if body['event_name']=='recommendation_view' else {'source_id':body['source_id']}
            events.purge(con)
            return events.record(con,actor.id,trip,body['event_name'],'client:'+body['event_id'],place=body['place_id'],run=body['run_id'],detail=detail,client_at=body['client_at'],excluded='CLOCK_SKEW' if skew>300 else None)
    def _context(self,con,actor,trip,place,body,*,historical=False):
        self.scope(con,actor,trip)
        if not historical:
            if not self.discovery._visible_place(con,place):fail('NOT_FOUND','장소를 찾을 수 없습니다.',404)
            events.validate_refs(con,actor.id,trip,place,body.get('run_id'))
        if body.get('itinerary_id') and not historical:
            row=self.itineraries._get(con,actor,trip,body['itinerary_id'])
            rev=con.execute('SELECT result_json FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone()
            if not rev or not any(i['item_id']==body['item_id'] and i.get('place_id')==place for i in json.loads(rev[0])['items']):fail('NOT_FOUND','이 여행의 일정 항목을 찾을 수 없습니다.',404)
        if body.get('visit_date'):
            triprow=self.repo._trip_dto(con,self.scope(con,actor,trip))
            if not triprow['start_date']<=body['visit_date']<=triprow['end_date']:fail('VISIT_DATE_OUTSIDE_TRIP','방문일을 여행 기간 안에서 확인해 주세요.')
            if body['visit_status']=='visited' and body['visit_date']>datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo(CITIES[con.execute('SELECT city FROM place_identities WHERE id=?',(place,)).fetchone()[0]]['timezone'])).date().isoformat():fail('VISIT_IN_FUTURE','미래 날짜를 방문 완료로 기록할 수 없습니다.')
        return digest({'kind':body['feedback_kind'],'run':body.get('run_id'),'item':body.get('item_id'),'itinerary':body.get('itinerary_id')})
    def _feedback(self,con,actor,trip,ident):
        self.scope(con,actor,trip);row=con.execute('SELECT * FROM visit_feedback WHERE id=? AND owner_id=? AND trip_id=?',(ident,actor.id,trip)).fetchone()
        if not row:fail('NOT_FOUND','피드백을 찾을 수 없습니다.',404)
        return dict(row)
    @staticmethod
    def dto(row):
        return {**json.loads(row['payload_json']),**{k:row[k] for k in ('id','place_id','run_id','itinerary_id','item_id','version','created_at','updated_at','withdrawn_at')},'private_note':row['private_note'],'source':'explicit_user_report'}
    def feedback_list(self,actor,trip):
        with self.db.connect() as con:
            self.scope(con,actor,trip)
            items=[]
            for row in con.execute('SELECT * FROM visit_feedback WHERE trip_id=? AND owner_id=? AND withdrawn_at IS NULL ORDER BY updated_at DESC',(trip,actor.id)):
                place=self.discovery._visible_place(con,row['place_id'])
                items.append({**self.dto(row),'place_name':place['name'] if place else '현재 제공되지 않는 장소'})
            return {'items':items}
    def get_feedback(self,actor,trip,ident):
        with self.db.connect() as con:return self.dto(self._feedback(con,actor,trip,ident))
    def create_feedback(self,actor,trip,place,body,key):
        if not key or len(key)>160:fail('IDEMPOTENCY_KEY_REQUIRED','요청 식별자를 확인해 주세요.')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.scope(con,actor,trip)
            old=con.execute('SELECT * FROM visit_feedback WHERE owner_id=? AND trip_id=? AND idempotency_key=?',(actor.id,trip,key)).fetchone()
            hashed=digest({'place':place,'body':body})
            if old:
                if old['input_hash']!=hashed:fail('IDEMPOTENCY_CONFLICT','같은 요청의 내용이 다릅니다.',409)
                return self.dto(old)
            context=self._context(con,actor,trip,place,body)
            old=con.execute('SELECT id FROM visit_feedback WHERE trip_id=? AND place_id=? AND context_key=? AND withdrawn_at IS NULL',(trip,place,context)).fetchone()
            if old:fail('FEEDBACK_EXISTS','같은 방문 맥락의 기록이 있습니다. 기록 목록에서 수정해 주세요.',409)
            ident=new_id('feedback');stamp=utcnow();data={k:v for k,v in body.items() if k not in ('private_note','run_id','item_id','itinerary_id')}
            con.execute('INSERT INTO visit_feedback VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',(ident,actor.id,trip,place,context,body.get('run_id'),body.get('itinerary_id'),body.get('item_id'),dump(data),body['private_note'],1,key,hashed,stamp,stamp))
            self._feedback_event(con,actor,trip,ident,place,body.get('run_id'),body,1,'created')
            return self.dto(self._feedback(con,actor,trip,ident))
    def _feedback_event(self,con,actor,trip,ident,place,run,body,version,action):
        con.execute('INSERT INTO feedback_changes VALUES(?,?,?,?,?)',(new_id('fchange'),ident,action,version,utcnow()))
        events.record(con,actor.id,trip,'visit_feedback',f'server:feedback:{ident}:{version}',place=place,run=run,detail={'feedback_id':ident,'action':action})
        if action=='created' and body.get('feedback_kind')=='preference':events.record(con,actor.id,trip,'recommendation_reject','server:reject:'+ident,place=place,run=run,detail={'feedback_id':ident,'reason_codes':body['reason_codes']})
    def update_feedback(self,actor,trip,ident,version,body=None):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=self._feedback(con,actor,trip,ident)
            if row['withdrawn_at']:fail('FEEDBACK_WITHDRAWN','철회한 기록입니다.',409)
            if row['version']!=version:fail('VERSION_CONFLICT','기록이 변경되었습니다. 입력을 보존하고 최신 기록을 확인해 주세요.',409)
            if body:
                # The persisted owned context remains editable after a run is stale or an item removed.
                # Only creation may attach new references; the digest below forbids replacing them.
                context=self._context(con,actor,trip,row['place_id'],body,historical=True)
                if context!=row['context_key']:fail('CONTEXT_IMMUTABLE','방문 맥락은 새 기록으로 구분해 주세요.')
                data={k:v for k,v in body.items() if k not in ('private_note','run_id','item_id','itinerary_id')}
                con.execute('UPDATE visit_feedback SET payload_json=?,private_note=?,version=version+1,updated_at=? WHERE id=?',(dump(data),body['private_note'],utcnow(),ident));action='updated'
            else:
                con.execute("UPDATE visit_feedback SET payload_json='{}',private_note='',withdrawn_at=?,updated_at=?,version=version+1 WHERE id=?",(utcnow(),utcnow(),ident));action='withdrawn'
                con.execute("INSERT OR IGNORE INTO discovery_tombstones VALUES('feedback',?,'USER_WITHDRAWN',?)",(ident,utcnow()))
                # Erase contributed reason/action data before recording the minimal withdrawal.
                for e in con.execute('SELECT id,detail_json FROM discovery_events WHERE trip_id=? AND owner_id=?',(trip,actor.id)).fetchall():
                    if json.loads(e['detail_json']).get('feedback_id')==ident:con.execute('DELETE FROM discovery_events WHERE id=?',(e['id'],))
            self._feedback_event(con,actor,trip,ident,row['place_id'],row['run_id'],body or {},version+1,action)
            return self.dto(self._feedback(con,actor,trip,ident))
    def report_fact(self,actor,trip,place,body,key):
        if not key or len(key)>160:fail('IDEMPOTENCY_KEY_REQUIRED','요청 식별자가 필요합니다.')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.scope(con,actor,trip)
            if not self.discovery._visible_place(con,place):fail('NOT_FOUND','장소를 찾을 수 없습니다.',404)
            fact=con.execute('SELECT * FROM place_facts WHERE id=? AND place_id=?',(body['fact_id'],place)).fetchone()
            if not fact:fail('NOT_FOUND','사실을 찾을 수 없습니다.',404)
            old=con.execute('SELECT * FROM fact_reports WHERE owner_id=? AND trip_id=? AND idempotency_key=?',(actor.id,trip,key)).fetchone()
            hashed=digest({'place':place,'body':body})
            if old:
                if old['input_hash']!=hashed:fail('IDEMPOTENCY_CONFLICT','요청 내용이 다릅니다.',409)
                return dict(old)
            ident=new_id('report');stamp=utcnow()
            con.execute('INSERT INTO fact_reports VALUES(?,?,?,?,?,?,?,?,?,?,1,NULL,NULL,NULL,?,?,?,?)',(ident,actor.id,trip,place,body['fact_id'],fact['source_id'],body['category'],body['observed_date'],body['description'],'reported',stamp,stamp,key,hashed))
            return dict(con.execute('SELECT * FROM fact_reports WHERE id=?',(ident,)).fetchone())
    def report_list(self,actor,trip=None):
        with self.db.connect() as con:
            if trip:self.scope(con,actor,trip)
            else:self.discovery._admin(con,actor)
            query='SELECT r.* FROM fact_reports r JOIN trips t ON t.id=r.trip_id WHERE t.deleted_at IS NULL'
            rows=con.execute(query+(' AND r.trip_id=? AND r.owner_id=?' if trip else '')+' ORDER BY r.created_at DESC LIMIT 200',(trip,actor.id) if trip else ()).fetchall()
            values=[]
            for row in rows:
                value={k:row[k] for k in ('id','place_id','fact_id','source_id','category','observed_date','description','status','version','resolution_code','replacement_fact_id','created_at','updated_at')}
                facts,sources=self.discovery._facts(con,row['place_id'])
                value['fact']=next((f for f in facts if f['id']==row['fact_id']),None)
                value['sources']=sources;values.append(value)
            return {'items':values}
    def review_report(self,actor,ident,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.discovery._admin(con,actor)
            row=con.execute('SELECT r.* FROM fact_reports r JOIN trips t ON t.id=r.trip_id WHERE r.id=? AND t.deleted_at IS NULL',(ident,)).fetchone()
            if not row:fail('NOT_FOUND','신고를 찾을 수 없습니다.',404)
            if row['version']!=body['expected_version']:fail('VERSION_CONFLICT','검토 상태가 변경되었습니다.',409)
            if row['status'] in ('resolved','dismissed'):fail('REPORT_CLOSED','처리한 신고입니다.',409)
            replacement=None
            if body['status']=='resolved':
                correction=body['correction'];old=con.execute('SELECT * FROM place_facts WHERE id=?',(row['fact_id'],)).fetchone()
                if correction['field']!=old['field'] or correction['status']!='verified':fail('CORRECTION_MISMATCH','같은 필드의 검증한 수정 사실을 입력해 주세요.')
                source=con.execute("SELECT * FROM evidence_sources WHERE place_id=? AND source_key=? AND source_type='official' AND status='active' AND display_permitted=1 AND read_confirmed=1",(row['place_id'],correction['source_key'])).fetchone()
                if not source or datetime.fromisoformat(correction['expires_at'])<=datetime.now(timezone.utc):fail('OFFICIAL_EVIDENCE_REQUIRED','사용 가능한 공식 출처와 새 확인일이 필요합니다.')
                if any(correction.get(k)!=old[k] for k in ('valid_for_date','valid_from','valid_until')):fail('CORRECTION_SCOPE_MISMATCH','신고한 사실과 같은 적용 날짜·기간으로 정정해 주세요.')
                if datetime.fromisoformat(correction['checked_at'])<max(datetime.fromisoformat(old['checked_at']),datetime.fromisoformat(source['checked_at'])):fail('OLDER_CORRECTION','이전 사실·출처보다 오래된 근거로 정정할 수 없습니다.')
                # Explicit operator attestation refreshes only the read timestamp, not permissions.
                con.execute('UPDATE evidence_sources SET checked_at=?,version=version+1,updated_at=? WHERE id=?',(correction['checked_at'],utcnow(),source['id']))
                con.execute("UPDATE place_facts SET status='unknown',expires_at=? WHERE id=?",(utcnow(),old['id']))
                replacement=self.discovery._insert_fact(con,actor,row['place_id'],correction,{source['source_key']:source['id']},source['policy_version'])
                inserted=con.execute('SELECT status FROM place_facts WHERE id=?',(replacement,)).fetchone()
                if inserted[0]!='verified':fail('CORRECTION_CONFLICT','다른 근거와 충돌합니다. 공식 자료를 비교해 주세요.',409)
                # Remove obsolete recommendation payloads; itinerary revisions and bookings remain intact.
                for run in con.execute('SELECT id,candidates_json FROM recommendation_runs WHERE candidates_json IS NOT NULL').fetchall():
                    if any(p['place_id']==row['place_id'] for p in json.loads(run['candidates_json'])):
                        con.execute("UPDATE recommendation_runs SET data_status='stale',result_json=NULL,candidates_json=NULL WHERE id=?",(run['id'],))
            con.execute('UPDATE fact_reports SET status=?,version=version+1,resolution_code=?,reviewer_id=?,replacement_fact_id=?,updated_at=? WHERE id=?',(body['status'],body['reason_code'],actor.id,replacement,utcnow(),ident))
            con.execute('INSERT INTO fact_report_actions VALUES(?,?,?,?,?,?)',(new_id('report_action'),ident,body['status'],body['reason_code'],actor.id,utcnow()))
            return {'id':ident,'status':body['status'],'version':row['version']+1,'replacement_fact_id':replacement,'bookings_changed':False}

    def _expense_entries(self,con,actor,trip):
        self.scope(con,actor,trip);rows=con.execute('SELECT * FROM itineraries WHERE trip_id=? AND active_revision_id IS NOT NULL ORDER BY created_at DESC,id DESC',(trip,)).fetchall();seen=set();entries=[];versions={}
        for row in rows:
            snap=json.loads(row['snapshot_json']);city=snap['city']
            if city in seen:continue
            seen.add(city);versions[row['id']]=row['version']
            revision=con.execute('SELECT result_json FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone();result=json.loads(revision[0])
            data=self.itineraries._data(con,actor,trip,snap,[i['place_id'] for i in result['items'] if i.get('place_id')])
            candidates={p['place_id']:p for p in data['candidates']}
            overrides={r['item_key']:r for r in con.execute('SELECT * FROM expense_overrides WHERE itinerary_id=?',(row['id'],))}
            items=[(i['item_id'],i) for i in result['items']]
            items += [('leg:'+(l.get('from_item_id') or 'origin')+':'+(l.get('to_item_id') or 'end'),{'name':'이동 비용','local_start':l.get('departure_local')}) for l in result.get('legs',[]) if not l.get('filter_only')]
            for key,item in items:
                entry={'key':row['id']+':'+key,'item_key':key,'itinerary_id':row['id'],'label':'예약 비용' if item.get('booking_id') else item.get('name') or '방문 비용','party':snap['conditions']['party'],'price':None,'source':'unknown','source_id':None,'checked_at':None,'override_version':0,'included_by':None,'covers_days':None}
                candidate=candidates.get(item.get('place_id'))
                if candidate:
                    visit={**snap['conditions']['visit'],'date':(item.get('local_start') or snap['conditions']['visit']['date'])[:10]}
                    price,facts,_=Facts(candidate,visit,datetime.now(timezone.utc)).get('price')
                    if price:
                        # Older source contracts lack age/fee evidence: keep them unknown.
                        normalized={k:v for k,v in price.items() if k in Price.model_fields}
                        normalized['tax']=price.get('tax_status',price.get('tax','unknown'))
                        try:entry.update(price=Price.model_validate(normalized).model_dump(mode='json'),source='verified_fact',source_id=facts[0]['source_id'],checked_at=facts[0]['checked_at'])
                        except ValueError:pass
                if key in overrides:
                    override=overrides[key];value=json.loads(override['payload_json']);entry.update(covers_days=value.get('days'),price=value['price'],party=value['party'] or entry['party'],included_by=(row['id']+':'+value['included_by']) if value['included_by'] else None,source='user_entered',source_id=None,checked_at=override['updated_at'],override_version=override['version'])
                entries.append(entry)
        return entries,versions
    def costs(self,actor,trip):
        with self.db.connect() as con:
            entries,versions=self._expense_entries(con,actor,trip)
        result=estimate(entries);result.update(itinerary_versions=versions)
        for result_item,entry in zip(result['items'],entries):result_item.update(item_key=entry['item_key'],input_price=entry['price'],input_party=entry['party'],input_days=entry['covers_days'],input_included_by=entry['included_by'].split(':',1)[1] if entry['included_by'] else None)
        return result
    def expense(self,actor,trip,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');entries,versions=self._expense_entries(con,actor,trip)
            iid=body['itinerary_id'];key=iid+':'+body['item_key'];entry=next((e for e in entries if e['key']==key),None)
            if not entry:fail('NOT_FOUND','현재 일정의 비용 항목을 찾을 수 없습니다.',404)
            if versions[iid]!=body['itinerary_version'] or entry['override_version']!=body['expected_version']:fail('VERSION_CONFLICT','일정 또는 가격 입력이 바뀌었습니다.',409)
            if body['party']:
                triprow=self.repo._trip_dto(con,self.scope(con,actor,trip));party=triprow['party']
                if body['party']['adults']>party['adults'] or len(body['party']['children'])>len(party['children']):fail('PARTY_EXCEEDS_TRIP','실제 참여 인원을 확인해 주세요.')
            if body['included_by']:
                parent=next((e for e in entries if e['key']==iid+':'+body['included_by']),None)
                if not parent:fail('NOT_FOUND','포함하는 비용 항목을 찾을 수 없습니다.',404)
                current=parent;visited={key}
                while current:
                    if current['key'] in visited:fail('INCLUSION_CYCLE','포함 관계가 순환합니다.')
                    visited.add(current['key']);current=next((e for e in entries if e['key']==current.get('included_by')),None)
            payload={k:body[k] for k in ('price','party','included_by','days')}
            con.execute('INSERT INTO expense_overrides VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(trip_id,itinerary_id,item_key) DO UPDATE SET payload_json=excluded.payload_json,version=excluded.version,updated_at=excluded.updated_at',(new_id('expense'),actor.id,trip,iid,body['item_key'],dump(payload),body['expected_version']+1,utcnow()))
        return self.costs(actor,trip)
