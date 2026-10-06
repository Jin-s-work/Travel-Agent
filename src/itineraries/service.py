"""Durable itinerary snapshots, guarded edit previews and append-only revisions.

All authorizations and fact reads are server scoped. The pure engine has no SQL,
credentials, or authority to change bookings. Its result is activated only after
the current booking/evidence manifest and job fencing token are checked again.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
import re
from types import SimpleNamespace
from pydantic import ValidationError

from src.foundation.repository import DomainError,dump,new_id,utcnow
from src.discovery.models import Conditions
from src.discovery.service import city_key
from .models import Generation,Preview


def digest(value):return hashlib.sha256(dump(value).encode()).hexdigest()
def clock():return datetime.now(timezone.utc)
def error(code,message,status=409,details=None):raise DomainError(code,message,status,details)


class Itineraries:
    def __init__(self,db,repo,jobs,discovery,recommendations,travel=None):
        self.db,self.repo,self.jobs,self.discovery,self.recommendations,self.travel=db,repo,jobs,discovery,recommendations,travel

    def _scope(self,con,actor,trip_id):
        return self.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)

    def _get(self,con,actor,trip_id,ident):
        self._scope(con,actor,trip_id)
        row=con.execute('SELECT * FROM itineraries WHERE id=? AND trip_id=? AND owner_id=?',(ident,trip_id,actor.id)).fetchone()
        if not row:error('NOT_FOUND','일정을 찾을 수 없습니다.',404)
        return row

    @staticmethod
    def _version(row,expected):
        if row['version']!=expected:error('VERSION_CONFLICT','일정이 변경되었습니다. 입력을 보존하고 최신 일정을 확인해 주세요.',details={'current_url':f"/api/v2/trips/{row['trip_id']}/itineraries/{row['id']}"})

    def _route_policy(self,con):
        fn=getattr(self.travel,'policy_fingerprint',None)
        return fn(con=con) if fn else {'provider':'none','version':'unknown_routes_v1'}

    def _data(self,con,actor,trip_id,snapshot,extra_ids=()):
        self._scope(con,actor,trip_id)
        ids={p['place_id'] for p in snapshot.get('selected',[])}|set(extra_ids)
        rows=con.execute("SELECT c.*,p.name,p.address,p.city,p.identity_status FROM research_candidates c JOIN candidate_packs k ON k.id=c.pack_id JOIN place_identities p ON p.id=c.place_id WHERE p.deleted_at IS NULL AND c.status='approved' AND k.status='approved' AND p.identity_status='verified' AND p.city=? ORDER BY p.id,c.created_at",(snapshot['city'],)).fetchall()
        excluded={r[0] for r in con.execute('SELECT place_id FROM discovery_exclusions WHERE trip_id=?',(trip_id,))}
        candidates={}
        for row in rows:
            if row['place_id'] not in ids:continue
            value=self.discovery._candidate(con,row)
            value['excluded']=value['place_id'] in excluded
            # These are server-approved structured coordinates, never client
            # supplied provider IDs or parsed reservation location guesses.
            value['coordinate_permitted']=bool(value['latitude'] is not None and value['longitude'] is not None and any(s.get('status')=='active' and s.get('display_permitted') and s.get('read_confirmed') for s in value['sources']))
            candidates[value['place_id']]=value
        bookings=[]
        for row in con.execute('SELECT * FROM bookings WHERE trip_id=? AND deleted_at IS NULL ORDER BY id',(trip_id,)):
            value=json.loads(row['effective_json'])
            events=[dict(r) for r in con.execute('SELECT * FROM booking_events WHERE booking_id=? ORDER BY id',(row['id'],))]
            bookings.append({'booking_id':row['id'],'id':row['id'],'version':row['version'],
                'kind':value.get('kind'),'status':value.get('status'),'name':value.get('provider') or value.get('kind') or '예약',
                'date':value.get('date'),'date_end':value.get('date_end'),'events':events})
        data={'candidates':list(candidates.values()),'bookings':bookings,'missing_place_ids':sorted(ids-candidates.keys()),'route_policy':self._route_policy(con)}
        if 'origin_contexts' in snapshot:
            from src.accommodations.origin import context_in_connection
            contexts={}
            for day,old in snapshot['origin_contexts'].items():
                try:contexts[day]=context_in_connection(con,self.repo,actor,trip_id,old['visit'],old.get('overrides') or {},utcnow())
                except DomainError as exc:
                    if exc.status!=404:raise
                    contexts[day]={'status':'unknown','origin_version':None,'reason_codes':['ORIGIN_DELETED'],'origin':None}
            data['origin_contexts']=contexts
        return data

    @staticmethod
    def _extra(data):return [p['place_id'] for p in data.get('candidates',[])]+data.get('missing_place_ids',[])

    def _route(self,actor,trip_id,ctx=None,key=None):
        if self.travel:return self.travel.lookup(actor,trip_id,job_ctx=ctx,request_key=key)
        def missing(a,b,departure,mode):return {'basis':'unknown','duration_minutes':None,'distance_m':None,'reason_codes':['NO_ROUTE'],'mode':mode,'checked_at':None,'expires_at':None}
        missing.stats={'requests':0,'provider_calls':0,'unknown':0,'cost':None}
        return missing

    def _data_guard(self,con,actor,trip_id,snapshot,data,expected):
        self._origin_guard(con,actor,trip_id,snapshot)
        fresh=self._data(con,actor,trip_id,snapshot,self._extra(data))
        if digest(fresh)!=expected:error('SOURCE_DATA_CHANGED','예약·장소·이동 근거가 변경되었습니다. 새 미리보기를 확인해 주세요.')
        return fresh

    def _origin_guard(self,con,actor,trip_id,snapshot):
        from src.accommodations.origin import snapshot_is_current
        if any(not snapshot_is_current(con,self.repo,actor,trip_id,context,utcnow()) for context in snapshot.get('origin_contexts',{}).values()):
            error('ORIGIN_CONTEXT_CHANGED','숙소·출발점 또는 좌표 확인 기한이 바뀌었습니다. 현재 숙소로 일정을 다시 만들어 주세요.',details={'regenerate_required':True})

    def _attach_origins(self,con,actor,trip_id,snapshot,envelope):
        from src.accommodations.origin import context_in_connection
        from src.accommodations.service import dto
        trip=self.repo._trip_dto(con,self.repo._trip(con,actor.id,trip_id))
        overrides={k:v for k,v in envelope.get('overrides',{}).items() if k in ('origin','origin_selection')}
        # Old full condition envelopes may contain a default automatic selection.
        # A legacy manual point retains its user-entered provenance.
        effective_origin=(envelope.get('origin_context') or {}).get('origin') or {}
        if effective_origin.get('source')=='explicit_origin':
            overrides['origin']={key:effective_origin.get(key) for key in ('label','latitude','longitude')}
            overrides['origin']['place_id']=None  # A client ID is not server-verified identity.
            overrides['origin_selection']={'kind':'manual'}
        elif not trip.get('stops') and (envelope.get('conditions') or {}).get('origin'):
            overrides.setdefault('origin',envelope['conditions']['origin'])
            overrides['origin_selection']={'kind':'manual'}
        contexts={w['date']:context_in_connection(con,self.repo,actor,trip_id,{'date':w['date'],'local_time':w['start'],'stop_id':snapshot['stop_id'],'city':snapshot['city'],'timezone':snapshot['timezone']},overrides,utcnow()) for w in snapshot['activity_windows']}
        stays=[]
        for row in con.execute('SELECT * FROM trip_accommodations WHERE trip_id=? AND owner_id=? ORDER BY id',(trip_id,actor.id)):
            value=dto(row,utcnow());stays.append({k:value.get(k) for k in ('id','version','deleted_at','stop_id','display_name','identity_state','identity','checkin_date','checkout_date','checkin_time','checkout_time','dates_confirmed')})
        snapshot['origin_contexts']=contexts
        snapshot['origin_resolution_input']={'trip':{k:trip[k] for k in ('id','version','start_date','end_date','stops')},'stays':stays,'overrides':overrides}
        snapshot['origin']=deepcopy(contexts[snapshot['start_date']].get('origin'))
        if snapshot['origin'] is None and isinstance(overrides.get('origin'),dict):
            snapshot['origin']={'label':overrides['origin'].get('label'),'latitude':None,'longitude':None,'place_id':None,'coordinate_permitted':False}
        snapshot['snapshot_version']='itinerary_snapshot_v2'

    @staticmethod
    def _leg_guard(result):
        for leg in result.get('legs',[]):
            if leg.get('expires_at') and datetime.fromisoformat(leg['expires_at'].replace('Z','+00:00'))<=clock():
                error('SOURCE_DATA_CHANGED','이동 근거가 만료되었습니다. 새 미리보기를 확인해 주세요.')

    def submit(self,actor,trip_id,body,key):
        body=Generation.model_validate(body).model_dump(mode='json');fingerprint=digest(body)
        existing=self.jobs.lookup(actor.id,actor.session_id,'personal_trip',trip_id,'itinerary_generate',key,fingerprint)
        if existing:
            with self.db.connect() as con:row=con.execute('SELECT id FROM itineraries WHERE job_id=?',(existing['id'],)).fetchone()
            return self._receipt(row['id'],existing)
        envelope=self.discovery.get_conditions(actor,trip_id)
        if envelope['version']!=body['conditions_version']:error('VERSION_CONFLICT','방문 조건이 변경되었습니다. 최신 조건을 확인해 주세요.')
        if envelope.get('city_needs_confirmation'):error('CITY_CONFIRMATION_REQUIRED','여행 도시와 날짜를 확인해 주세요.',422)
        conditions=Conditions.model_validate(envelope['conditions']).model_dump(mode='json')
        trip=envelope['trip_snapshot']
        if not trip['start_date']<=body['start_date']<=body['end_date']<=trip['end_date']:error('DATE_OUTSIDE_TRIP','여행 기간 안의 날짜를 선택해 주세요.',422)
        stops=[s for s in trip.get('stops',[]) if city_key(s['city'])==conditions['city'] and s['timezone']==conditions['visit']['timezone'] and s['start_date']<=body['start_date']<=body['end_date']<=s['end_date']]
        if trip.get('stops') and not stops:error('STOP_BOUNDARY_UNSUPPORTED','이번 일정은 지원하는 한 도시 체류 구간 안에서 만들어 주세요. 다른 도시 예약은 유지됩니다.',422)
        if body['recommendation_run_id']:
            run=self.recommendations.get(actor,trip_id,body['recommendation_run_id'])
            if run['result'] is None:error('RECOMMENDATION_UNAVAILABLE','현재 사용할 수 있는 추천 결과를 확인해 주세요.')
            allowed={p['place_id'] for groups in run['result']['sections'].values() for name in ('items','needs_confirmation','insufficient_data') for p in groups[name]}
            # Saved places are independently selected and need not be part of
            # the particular recommendation that was open in the browser.
            with self.db.connect() as con:
                allowed|={r[0] for r in con.execute('SELECT matched_place_id FROM bookmarks WHERE trip_id=? AND owner_id=? AND deleted_at IS NULL AND matched_place_id IS NOT NULL',(trip_id,actor.id))}
            if any(p['place_id'] not in allowed for p in body['selected']):error('NOT_FOUND','선택한 추천 또는 저장 장소를 찾을 수 없습니다.',404)
        selected=body['selected'];start=date.fromisoformat(body['start_date']);end=date.fromisoformat(body['end_date'])
        origin=deepcopy(conditions.get('origin'))
        if origin:
            # The discovery condition is user input, not proof that an arbitrary
            # catalog ID represents the selected origin. Only explicit user
            # coordinates are used until source-backed origin resolution exists.
            origin['place_id']=None
            origin.update(id='origin',city=conditions['city'],timezone=conditions['visit']['timezone'],
                coordinate_permitted=origin.get('latitude') is not None and origin.get('longitude') is not None)
        snapshot={**body,'trip_id':trip_id,'city':conditions['city'],'stop_id':stops[0]['id'] if stops else None,
            'timezone':conditions['visit']['timezone'],'party':conditions['party'],'conditions':conditions,
            'origin':origin,'transport':conditions['transport'],'density':conditions['density'],
            'meal_time':conditions.get('meal_time'),'locked_items':[],
            'item_ids':{p['place_id']:new_id('item') for p in selected},'max_candidates':30,
            'activity_windows':[{'date':(start+timedelta(days=n)).isoformat(),'start':body['activity_start'],'end':body['activity_end']} for n in range((end-start).days+1)],
            'assumptions':[{'code':'DEFAULT_STAY_DURATION','place_id':p['place_id'],'minutes':p['duration_minutes']} for p in selected if p['duration_origin']=='default'],
            'planning_scope':'single_city_stop','snapshot_version':'itinerary_snapshot_v1','computed_at':utcnow()}
        if not origin or origin.get('latitude') is None:snapshot['assumptions'].append({'code':'ORIGIN_LOCATION_UNCONFIRMED'})
        snapshot['rest_preferences']['duration_minutes']=snapshot['rest_preferences']['minutes']
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');current=self._scope(con,actor,trip_id)
            cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            if current['version']!=body['trip_version'] or (cv['version'] if cv else 0)!=body['conditions_version']:error('VERSION_CONFLICT','여행 또는 방문 조건이 변경되었습니다.')
            self._attach_origins(con,actor,trip_id,snapshot,envelope)
            data=self._data(con,actor,trip_id,snapshot)
            if data['missing_place_ids']:error('NOT_FOUND','사용 가능한 선택 장소를 찾을 수 없습니다.',404)
            ident=new_id('itinerary')
            job=self.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'itinerary_generate',{'itinerary_id':ident},current['version'],key,request_fingerprint=fingerprint,con=con)
            old=con.execute('SELECT id FROM itineraries WHERE job_id=?',(job['id'],)).fetchone()
            if old:return self._receipt(old['id'],job)
            stamp=utcnow()
            con.execute('INSERT INTO itineraries(id,trip_id,owner_id,job_id,trip_version,conditions_version,request_version,snapshot_json,input_data_json,input_manifest,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,trip_id,actor.id,job['id'],current['version'],body['conditions_version'],body['itinerary_request_version'],dump(snapshot),dump(data),digest(data),actor.id,stamp,stamp))
        return self._receipt(ident,job)

    @staticmethod
    def _receipt(ident,job):return {'itinerary_id':ident,'job_id':job['id'],'state':job['state'],'status_url':'/api/v2/jobs/'+job['id'],'events_url':'/api/v2/jobs/'+job['id']+'/events'}

    def execute(self,job,ctx):
        from .scheduler import generate
        actor=SimpleNamespace(id=job['actor_id'],session_id=job['session_id']);ident=job['payload']['itinerary_id'];trip_id=job['trip_id']
        with self.db.connect() as con:row=dict(self._get(con,actor,trip_id,ident))
        ctx.guard()
        if row['active_revision_id']:return {'itinerary_id':ident,'revision_id':row['active_revision_id']}
        snapshot=json.loads(row['snapshot_json']);data=json.loads(row['input_data_json'])
        with self.db.connect() as con:self._data_guard(con,actor,trip_id,snapshot,data,row['input_manifest'])
        ctx.progress('fixed_booking_validation',done=0,total=len(data['bookings']))
        routes=self._route(actor,trip_id,ctx,ident)
        result=generate(snapshot,data['bookings'],data['candidates'],routes,datetime.fromisoformat(snapshot['computed_at']))
        result['route_usage']=deepcopy(routes.stats);result.setdefault('assumptions',deepcopy(snapshot['assumptions']))
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');ctx.guard(con=con,require_version=True)
            current=self._get(con,actor,trip_id,ident)
            cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            if (cv['version'] if cv else 0)!=row['conditions_version']:error('VERSION_CONFLICT','생성 중 방문 조건이 변경되었습니다. 현재 조건으로 다시 만들어 주세요.')
            self._data_guard(con,actor,trip_id,snapshot,data,row['input_manifest']);self._leg_guard(result)
            if current['active_revision_id']:return {'itinerary_id':ident,'revision_id':current['active_revision_id']}
            rid=self._revision(con,current,actor,'generation',[],result,data,version=1)
        ctx.progress('itinerary_saved',done=len(result.get('items',[])),total=len(result.get('items',[])))
        state='partial' if result.get('unplaced') or result.get('conflicts') else 'succeeded'
        return {'state':state,'result':{'itinerary_id':ident,'revision_id':rid,'validation_status':result['validation_status']}}

    def _revision(self,con,row,actor,kind,commands,result,data,version=None,undo_stack=None):
        version=version or row['version']+1;ident=new_id('revision');stamp=utcnow()
        con.execute('INSERT INTO itinerary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident,row['id'],version,row['active_revision_id'],actor.id,kind,row['version'],dump(commands),dump(result),digest(data),dump(data),stamp))
        con.execute('UPDATE itineraries SET version=?,active_revision_id=?,validation_status=?,undo_stack_json=?,updated_at=? WHERE id=?',
            (version,ident,result['validation_status'],dump(undo_stack if undo_stack is not None else json.loads(row['undo_stack_json'])),stamp,row['id']))
        from src.product.events import record
        snap=json.loads(row['snapshot_json']);run_id=snap.get('recommendation_run_id')
        previous=con.execute('SELECT result_json FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone() if row['active_revision_id'] else None
        linked=con.execute('SELECT candidates_json FROM recommendation_runs WHERE id=? AND owner_id=? AND trip_id=?',(run_id,actor.id,row['trip_id'])).fetchone() if run_id else None
        linked_places={p['place_id'] for p in json.loads(linked[0] or '[]')} if linked else set()
        prior={i.get('place_id') for i in json.loads(previous[0]).get('items',[])} if previous else set()
        for item in result.get('items',[]):
            if item.get('place_id') and item['place_id'] not in prior:
                record(con,actor.id,row['trip_id'],'itinerary_add','server:itinerary:'+ident+':'+item['item_id'],place=item['place_id'],run=run_id if item['place_id'] in linked_places else None,detail={'item_id':item['item_id'],'itinerary_id':row['id']})
        return ident

    def list(self,actor,trip_id,limit=20,cursor=0):
        with self.db.connect() as con:
            self._scope(con,actor,trip_id)
            rows=con.execute('SELECT id,version,job_id,validation_status,active_revision_id,created_at,updated_at FROM itineraries WHERE trip_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(trip_id,limit+1,cursor)).fetchall()
        return {'items':[dict(r)|{'itinerary_id':r['id'],'state':self.jobs.get(r['job_id'],actor.id,actor.session_id)['state']} for r in rows[:limit]],'next_cursor':str(cursor+limit) if len(rows)>limit else None}

    @staticmethod
    def _safe_view(result,data):
        """Revoke stale source payloads without deleting the user's arrangement."""
        value=deepcopy(result);valid={p['place_id']:p for p in data['candidates']}
        bookings={b['booking_id']:b for b in data['bookings']}
        for item in value.get('items',[]):
            item.pop('facts',None);item.pop('sources',None);item['source_refs']=[]
            if item.get('place_id') and item['place_id'] not in valid:
                item['verification_status']='unavailable';item['source_status']='withdrawn'
                for name in ('name','native_name','address','canonical_url','location'):item.pop(name,None)
            else:item['verification_status']='stale'
            if item.get('booking_id'):
                booking=bookings.get(item['booking_id']);item['current_booking']=deepcopy(booking)
                item['source_status']='changed' if booking else 'deleted'
                if not booking:
                    for name in ('local_start','local_end','start_instant','end_instant','location','name'):item[name]=None
        for leg in value.get('legs',[]):
            leg.update(basis='unknown',duration_minutes=None,distance_m=None,distance_meters=None,source_refs=[],verification_status='stale')
        value['validation_status']='provisional' if not value.get('conflicts') else 'conflicted'
        value.setdefault('unresolved_conditions',[]).append({'code':'SOURCE_DATA_CHANGED','reason':'예약 또는 근거가 바뀌었습니다. 현재 예약과 새 미리보기를 확인해 주세요.'})
        return value

    def get(self,actor,trip_id,ident):
        with self.db.connect() as con:
            row=dict(self._get(con,actor,trip_id,ident));trip=con.execute('SELECT version FROM trips WHERE id=?',(trip_id,)).fetchone()
            cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            revision=con.execute('SELECT * FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone()
            history=[dict(r) for r in con.execute('SELECT id,version,kind,base_version,created_at FROM itinerary_revisions WHERE itinerary_id=? ORDER BY version DESC LIMIT 100',(ident,))]
            snapshot=json.loads(row['snapshot_json']);result=json.loads(revision['result_json']) if revision else {}
            olddata=json.loads(revision['input_data_json']) if revision else json.loads(row['input_data_json'] or '{}')
            data=self._data(con,actor,trip_id,snapshot,self._extra(olddata))
            stale=bool(revision and digest(data)!=revision['data_manifest'])
            if revision and any(leg.get('expires_at') and leg['expires_at']<=utcnow() for leg in result.get('legs',[])):stale=True
        if stale:result=self._safe_view(result,data)
        job=self.jobs.get(row['job_id'],actor.id,actor.session_id)
        return {'id':ident,'itinerary_id':ident,'version':row['version'],'job_id':row['job_id'],'job':job,'state':job['state'],
            'active_revision_id':row['active_revision_id'],'validation_status':result.get('validation_status',row['validation_status']),
            'input_status':'current' if trip['version']==row['trip_version'] and (cv['version'] if cv else 0)==row['conditions_version'] else 'stale',
            'data_status':'stale' if stale else 'current' if revision else 'pending','snapshot':snapshot,
            'revisions':history,'can_undo':bool(json.loads(row['undo_stack_json'])),'created_at':row['created_at'],'updated_at':row['updated_at'],
            **{name:result.get(name,[]) for name in ('items','legs','conflicts','unplaced','unresolved_conditions','assumptions')},
            'route_usage':result.get('route_usage'),'booking_changes':result.get('booking_changes',[]),'reservation_action':'none'}

    def _preview_base(self,actor,trip_id,ident,expected,extra=()):
        with self.db.connect() as con:
            row=dict(self._get(con,actor,trip_id,ident));self._version(row,expected)
            revision=con.execute('SELECT * FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone()
            if not revision:error('ITINERARY_NOT_READY','일정 생성이 끝난 뒤 편집해 주세요.')
            snapshot=json.loads(row['snapshot_json']);olddata=json.loads(revision['input_data_json'])
            data=self._data(con,actor,trip_id,snapshot,self._extra(olddata)+list(extra))
            trip=self._scope(con,actor,trip_id);cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            if trip['version']!=row['trip_version'] or (cv['version'] if cv else 0)!=row['conditions_version']:
                error('INPUT_SNAPSHOT_STALE','여행·예약 또는 방문 조건이 변경되었습니다. 현재 조건으로 새 일정을 만들어 주세요.',details={'current_url':f'/api/v2/trips/{trip_id}/itineraries/{ident}','regenerate_required':True})
            self._origin_guard(con,actor,trip_id,snapshot)
        return row,snapshot,data,json.loads(revision['result_json']),trip['version'],cv['version'] if cv else 0

    @staticmethod
    def _editable(current,commands):
        by_id={i['item_id']:i for i in current['items']};unlocked=set()
        for command in commands:
            if command['op']=='add':continue
            item=by_id.get(command['item_id'])
            if not item:error('NOT_FOUND','편집할 항목을 찾을 수 없습니다.',404)
            if item.get('booking_id') or item.get('lock_origin')=='booking':error('BOOKING_EDIT_REQUIRED','고정 예약은 예약 확인 화면에서 교정해 주세요. 일정 편집은 외부 예약을 변경하지 않습니다.',422)
            if command['op']=='unlock':unlocked.add(item['item_id'])
            if command['op']=='lock':unlocked.discard(item['item_id'])
            if command['op'] in ('move','remove') and item.get('locked') and item['item_id'] not in unlocked:error('LOCKED_ITEM','이 항목의 잠금을 명시적으로 해제한 뒤 변경해 주세요.',422)

    def preview(self,actor,trip_id,ident,body):
        from .edits import preview
        body=Preview.model_validate(body).model_dump(mode='json',exclude_none=True)
        extra=[c['place_id'] for c in body['commands'] if c['op']=='add']
        row,snapshot,data,current,tv,cv=self._preview_base(actor,trip_id,ident,body['expected_version'],extra)
        if any(pid in data['missing_place_ids'] for pid in extra):error('NOT_FOUND','추가할 장소의 현재 근거를 찾을 수 없습니다.',404)
        self._editable(current,body['commands'])
        for cmd in body['commands']:
            if cmd['op']=='add':cmd['item_id']=new_id('item')
        ident_preview=new_id('preview');routes=self._route(actor,trip_id,key=ident_preview)
        result=preview(current,body['commands'],snapshot,data['bookings'],data['candidates'],routes,clock())
        result['route_usage']=deepcopy(routes.stats)
        return self._store_preview(actor,trip_id,row,snapshot,data,result,body['commands'],ident_preview,'user_edit',tv,cv)

    def _store_preview(self,actor,trip_id,row,snapshot,data,result,commands,ident,kind,tv,cv,steps=0):
        stamp=utcnow();expires=(clock()+timedelta(minutes=10)).isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');current=self._get(con,actor,trip_id,row['id']);self._version(current,row['version'])
            trip=self._scope(con,actor,trip_id);saved=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            if trip['version']!=tv or (saved['version'] if saved else 0)!=cv:error('VERSION_CONFLICT','여행이나 방문 조건이 변경되었습니다. 새 미리보기를 확인해 주세요.')
            self._data_guard(con,actor,trip_id,snapshot,data,digest(data));self._leg_guard(result)
            from .prices import price_delta
            active=con.execute('SELECT result_json FROM itinerary_revisions WHERE id=?',(current['active_revision_id'],)).fetchone()
            overrides={r['item_key']:{**json.loads(r['payload_json']),'version':r['version'],'updated_at':r['updated_at']} for r in con.execute('SELECT * FROM expense_overrides WHERE itinerary_id=? ORDER BY item_key',(row['id'],))}
            result['price_delta']=price_delta(json.loads(active['result_json']),result,snapshot,data['candidates'],overrides,row['id'],clock())
            result['expense_manifest']=digest(overrides)
            con.execute('INSERT INTO itinerary_previews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,row['id'],trip_id,actor.id,row['version'],tv,cv,kind,dump(commands),dump(result),digest(data),dump(data),steps,None,stamp,expires))
        return self._preview_dto(ident,row,result,commands,expires)

    @staticmethod
    def _can_apply(result,snapshot):
        return not result.get('conflicts') and result.get('validation_status')!='conflicted' and (snapshot.get('allow_provisional') or result.get('validation_status')=='validated')

    def _preview_dto(self,ident,row,result,commands,expires):
        return {**deepcopy(result),'preview_id':ident,'itinerary_id':row['id'],'base_version':row['version'],
            'normalized_commands':result.get('normalized_commands',commands),'expires_at':expires,
            'can_apply':self._can_apply(result,json.loads(row['snapshot_json']))}

    def undo_preview(self,actor,trip_id,ident,body):
        from .edits import revalidate
        row,snapshot,data,current,tv,cv=self._preview_base(actor,trip_id,ident,body['expected_version'])
        stack=json.loads(row['undo_stack_json']);steps=body['steps']
        if len(stack)<steps:error('UNDO_UNAVAILABLE','되돌릴 수 있는 사용자 편집 수를 확인해 주세요.',422)
        with self.db.connect() as con:
            old=con.execute('SELECT * FROM itinerary_revisions WHERE id=? AND itinerary_id=?',(stack[-steps],ident)).fetchone()
            if not old:error('UNDO_UNAVAILABLE','이전 일정 자료를 찾을 수 없습니다.',422)
            restored=json.loads(old['result_json']);olddata=json.loads(old['input_data_json'])
            data=self._data(con,actor,trip_id,snapshot,self._extra(olddata)+self._extra(data))
        # Never restore a removed/withdrawn place or deleted source booking.
        available={p['place_id'] for p in data['candidates']}
        if any(i.get('place_id') and i['place_id'] not in available for i in restored.get('items',[])):
            error('UNDO_DATA_UNAVAILABLE','삭제되거나 사용 권한이 철회된 장소는 되살릴 수 없습니다.')
        pid=new_id('preview');routes=self._route(actor,trip_id,key=pid)
        result=revalidate(restored,snapshot,data['bookings'],data['candidates'],routes,clock());result['route_usage']=deepcopy(routes.stats)
        commands=[{'op':'restore_revision','revision_id':old['id'],'steps':steps}]
        result['diff']={'before':current.get('items',[]),'after':result.get('items',[])}
        return self._store_preview(actor,trip_id,row,snapshot,data,result,commands,pid,'undo',tv,cv,steps)

    def apply(self,actor,trip_id,ident,body):
        from .constraints import validate
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');row=dict(self._get(con,actor,trip_id,ident))
            p=con.execute('SELECT * FROM itinerary_previews WHERE id=? AND itinerary_id=? AND trip_id=? AND owner_id=?',(body['preview_id'],ident,trip_id,actor.id)).fetchone()
            if not p:error('NOT_FOUND','변경 미리보기를 찾을 수 없습니다.',404)
            if p['applied_revision_id'] and row['active_revision_id']==p['applied_revision_id']:
                return self._get_after_commit(actor,trip_id,ident,con)
            self._version(row,body['expected_version'])
            if p['base_version']!=row['version']:error('VERSION_CONFLICT','미리보기 이후 일정이 변경되었습니다.')
            if p['expires_at']<=utcnow():error('PREVIEW_EXPIRED','미리보기가 만료되었습니다. 입력을 유지하고 다시 확인해 주세요.')
            trip=self._scope(con,actor,trip_id);cv=con.execute('SELECT version FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
            if trip['version']!=p['trip_version'] or (cv['version'] if cv else 0)!=p['conditions_version']:error('VERSION_CONFLICT','여행이나 방문 조건이 변경되었습니다. 새 미리보기가 필요합니다.')
            if trip['version']!=row['trip_version'] or (cv['version'] if cv else 0)!=row['conditions_version']:error('INPUT_SNAPSHOT_STALE','현재 여행 조건으로 일정을 다시 만들어 주세요.')
            snapshot=json.loads(row['snapshot_json']);data=json.loads(p['input_data_json']);result=json.loads(p['result_json'])
            data=self._data_guard(con,actor,trip_id,snapshot,data,p['data_manifest']);self._leg_guard(result)
            if 'expense_manifest' in result:
                overrides={r['item_key']:{**json.loads(r['payload_json']),'version':r['version'],'updated_at':r['updated_at']} for r in con.execute('SELECT * FROM expense_overrides WHERE itinerary_id=? ORDER BY item_key',(row['id'],))}
                if digest(overrides)!=result['expense_manifest']:error('EXPENSE_DATA_CHANGED','예상 지출 입력이 바뀌었습니다. 새 미리보기를 확인해 주세요.')
            if p['kind']=='plan_b':
                from src.travel_tools.alternatives import check_apply
                active=con.execute('SELECT result_json FROM itinerary_revisions WHERE id=?',(row['active_revision_id'],)).fetchone()
                check_apply(result,json.loads(active[0]),clock())
            # A fresh constraint pass cannot erase command-level rejection
            # (for example duplicate add or an ambiguous clock selection).
            if not self._can_apply(result,snapshot):error('EDIT_CONFLICT','검토가 필요한 미리보기는 적용할 수 없습니다. 원 일정은 유지했습니다.',details={'preview_id':p['id'],'conflicts':result.get('conflicts',[]),'unresolved_conditions':result.get('unresolved_conditions',[])})
            validation=validate(result['items'],data['candidates'],snapshot,result.get('legs',[]),clock(),bookings=data['bookings'])
            result.update(validation)
            if not self._can_apply(result,snapshot):error('EDIT_CONFLICT','변경안에 충돌이나 미확인이 남았습니다. 원 일정은 유지했습니다.',details={'preview_id':p['id'],'validation_status':result.get('validation_status'),'conflicts':result.get('conflicts',[]),'unresolved_conditions':result.get('unresolved_conditions',[])})
            stack=json.loads(row['undo_stack_json'])
            if p['kind']=='undo':stack=stack[:-p['undo_steps']]
            else:stack=(stack+[row['active_revision_id']])[-100:]
            rid=self._revision(con,row,actor,p['kind'],json.loads(p['commands_json']),result,data,undo_stack=stack)
            con.execute('UPDATE itinerary_previews SET applied_revision_id=? WHERE id=?',(rid,p['id']))
        return self.get(actor,trip_id,ident)

    def _get_after_commit(self,actor,trip_id,ident,con):
        con.commit()
        return self.get(actor,trip_id,ident)

    def intent(self,actor,trip_id,ident,body):
        row,_,_,current,_,_=self._preview_base(actor,trip_id,ident,body['expected_version'])
        selected=body.get('item_id')
        if selected and selected not in {i['item_id'] for i in current['items']}:error('NOT_FOUND','선택한 항목을 찾을 수 없습니다.',404)
        text=body['text'].strip();commands=[]
        if not selected:return {'state':'needs_clarification','commands':[],'reason_codes':['SELECT_ITEM_REQUIRED'],'message':'변경할 일정 항목을 먼저 선택해 주세요.'}
        simple={'삭제':'remove','삭제해줘':'remove','빼줘':'remove','remove':'remove','잠금':'lock','잠가줘':'lock','lock':'lock','잠금 해제':'unlock','잠금 풀어줘':'unlock','unlock':'unlock'}
        if text.lower() in simple:commands=[{'op':simple[text.lower()],'item_id':selected}]
        else:
            match=re.fullmatch(r'(?:이동\s*)?(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?:\s*(?:로 이동|로 옮겨줘))?',text)
            if match:commands=[{'op':'move','item_id':selected,'local_start':match[1]+'T'+match[2]}]
        if not commands:return {'state':'needs_clarification','commands':[],'reason_codes':['EXPLICIT_EDIT_REQUIRED'],'message':'선택한 항목의 날짜·시각 또는 삭제·잠금·잠금 해제를 구체적으로 입력해 주세요.'}
        try:normalized=Preview.model_validate({'expected_version':row['version'],'commands':commands}).model_dump(mode='json',exclude_none=True)['commands']
        except ValidationError:return {'state':'needs_clarification','commands':[],'reason_codes':['INVALID_LOCAL_TIME'],'message':'날짜와 현지 시각을 확인해 주세요.'}
        self._editable(current,normalized)
        return {'state':'ready','commands':normalized,'reason_codes':[],'requires_preview':True,'message':'변경 미리보기를 확인한 뒤 적용해 주세요.'}
