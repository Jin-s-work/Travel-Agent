"""Explicit server-side allowlists; offline permission is independent of display permission."""
from datetime import datetime,timedelta,timezone,date
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import json
from src.foundation.repository import dump,utcnow
from src.itineraries.service import digest
from src.itineraries.intervals import utc
from .preparation import fail

MAX_BUNDLE_BYTES=512000

class Today:
    def __init__(self,db,repo,itineraries,discovery,preparation):
        self.db,self.repo,self.itineraries,self.discovery,self.preparation=db,repo,itineraries,discovery,preparation

    def grant(self,actor,body):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');self.discovery._admin(con,actor)
            s=con.execute('SELECT * FROM evidence_sources WHERE id=?',(body['source_id'],)).fetchone()
            if not s:fail('NOT_FOUND','출처를 찾을 수 없습니다.',404)
            if s['version']!=body['source_version']:fail('VERSION_CONFLICT','출처가 변경되었습니다.',409)
            expires=utc(body['expires_at']);now=datetime.now(timezone.utc)
            if not now<expires<=now+timedelta(days=365):fail('INVALID_EXPIRY','허용 기간은 현재부터 1년 이내로 지정해 주세요.')
            if body['policy_version']!=s['policy_version'] or not s['read_confirmed'] or not s['display_permitted'] or s['status']!='active':fail('SOURCE_POLICY_UNAVAILABLE','출처 사용 범위를 먼저 검토해 주세요.')
            con.execute('DELETE FROM offline_source_permissions WHERE source_id=?',(s['id'],))
            con.execute('INSERT INTO offline_source_permissions VALUES(?,?,?,?,?,?,?)',(s['id'],s['version'],dump(sorted(set(body['fields']))),expires.isoformat(),body['policy_version'],actor.id,utcnow()))
        return {'source_id':s['id'],'fields':body['fields'],'expires_at':expires.isoformat()}

    def _places(self,con,actor,trip,offline=False):
        # catalog() opens another read-only SQL connection; no mutation occurs here.
        catalog=self.discovery.catalog(actor,trip);out={};permissions=[]
        for place in catalog:
            sources=[s for s in place['sources'] if s.get('status')=='active' and s.get('read_confirmed') and s.get('display_permitted')]
            fields=set() if offline or not sources else {'name','native_name','address','map_url'}
            if offline:
                for s in sources:
                    r=con.execute('SELECT * FROM offline_source_permissions WHERE source_id=?',(s['id'],)).fetchone()
                    if r and r['source_version']==s['version'] and r['policy_version']==s['policy_version'] and utc(r['expires_at'])>datetime.now(timezone.utc):
                        fields.update(json.loads(r['fields_json']));permissions.append(dict(r))
            value={'place_id':place['place_id'],'name':'저장된 방문 장소','native_name':None,'address':None,'map_url':None,'withheld_fields':sorted({'name','native_name','address','map_url'}-fields)}
            for f in fields-{'map_url'}:value[f]=place.get(f)
            url=place.get('canonical_url','');parts=urlsplit(url)
            if 'map_url' in fields and parts.scheme=='https' and not parts.username and (parts.hostname in {'maps.google.com','maps.app.goo.gl'} or parts.hostname in {'www.google.com','www.google.co.jp','www.google.es'} and parts.path.startswith('/maps')):value['map_url']=url
            out[place['place_id']]=value
        return out,permissions

    def content(self,actor,trip,offline=False,include_notes=False):
        t=self.repo.get_trip(actor.id,trip)
        with self.db.connect() as con:
            self.itineraries._scope(con,actor,trip)
            places,permissions=self._places(con,actor,trip,offline)
            rows=con.execute('SELECT id FROM itineraries WHERE trip_id=? AND owner_id=? AND active_revision_id IS NOT NULL ORDER BY updated_at DESC,id',(trip,actor.id)).fetchall()
        schedules=[];offline_cities=set()
        for row in rows[:20]:
            schedule=self.itineraries.get(actor,trip,row['id'])
            if offline and schedule['snapshot']['city'] in offline_cities:continue
            offline_cities.add(schedule['snapshot']['city'])
            items=[]
            for i in schedule['items']:
                # No mail body, confirmation number, ticket, payment, session,
                # raw fact/review, author, photograph, or map tile enters this DTO.
                item={k:i.get(k) for k in ('item_id','item_type','local_start','local_end','start_timezone','end_timezone','start_instant','end_instant','locked','verification_status')}
                item.update(places.get(i.get('place_id'),{'name':'예약 일정' if i.get('booking_id') else '휴식','native_name':None,'address':None,'map_url':None,'withheld_fields':[]}))
                item['confirmed_booking']=bool(i.get('booking_id'));items.append(item)
            legs=[{k:l.get(k) for k in ('from_item_id','to_item_id','duration_minutes','mode','basis','buffer_minutes','checked_at','expires_at')} for l in schedule['legs']]
            schedules.append({'id':schedule['id'],'version':schedule['version'],'timezone':schedule['snapshot']['timezone'],'city':schedule['snapshot']['city'],'validation_status':schedule['validation_status'],'data_status':schedule['data_status'],'items':items,'legs':legs})
        prepared=self.preparation.list(actor,trip)['items']
        tasks=[{'id':t['id'],'status':t['status'],'visit_date':t['visit_date'],'timezone':t['timezone'],'task_kind':t['task_kind'],'due_date':t['calculation']['due_date'],'due_at':t['calculation']['due_at'],'due_precision':t['calculation']['due_precision'],'title':'예약 준비 확인' if offline else t['title']} for t in prepared]
        notes=[]
        if include_notes:
            with self.db.connect() as con:
                self.itineraries._scope(con,actor,trip)
                notes=[{'note':r['note']} for r in con.execute('SELECT note FROM bookmarks WHERE trip_id=? AND owner_id=? AND deleted_at IS NULL AND note!=?',(trip,actor.id,''))]
        return {'trip':{'id':trip,'title':t['title'],'start_date':t['start_date'],'end_date':t['end_date'],'version':t['version']},'schedules':schedules,'tasks':tasks,'notes':notes},permissions

    def today(self,actor,trip,ident=None,day=None):
        content,_=self.content(actor,trip);now=datetime.now(timezone.utc)
        schedules=content['schedules'];selected=next((s for s in schedules if s['id']==ident),None) if ident else next(iter(schedules),None)
        if ident and not selected:fail('NOT_FOUND','일정을 찾을 수 없습니다.',404)
        zone=selected['timezone'] if selected else self.discovery.get_conditions(actor,trip)['conditions']['visit']['timezone'];local_day=day or now.astimezone(ZoneInfo(zone)).date().isoformat()
        try:date.fromisoformat(local_day)
        except ValueError:fail('INVALID_DATE','날짜 형식을 확인해 주세요.')
        items=[i for i in selected['items'] if str(i.get('local_start') or '')[:10]==local_day] if selected else []
        items.sort(key=lambda i:(i.get('start_instant') or '9999',i['item_id']))
        for item in items:
            leg=next((l for l in selected['legs'] if l['to_item_id']==item['item_id']),None)
            item['travel']=leg;item['recommended_departure']=None
            if leg and leg['duration_minutes'] is not None and leg['basis'] in ('provider','estimate') and item['start_instant']:
                if not leg['expires_at'] or utc(leg['expires_at'])>now:
                    item['recommended_departure']=(utc(item['start_instant'])-timedelta(minutes=leg['duration_minutes']+(leg['buffer_minutes'] or 0))).isoformat()
        next_item=next((i['item_id'] for i in items if i.get('end_instant') and utc(i['end_instant'])>now),None)
        return {**content,'schedules':[{'id':s['id'],'city':s['city'],'timezone':s['timezone'],'version':s['version']} for s in schedules],
            'selected_itinerary':selected['id'] if selected else None,'selected_version':selected['version'] if selected else None,'date':local_day,'timezone':zone,'now':now.isoformat(),'items':items,'next_item_id':next_item,'location_required':False}

    def _guard(self,actor,trip):
        with self.db.connect() as con:
            t=self.itineraries._scope(con,actor,trip)
            return digest({'trip':dict(t),
                'itineraries':[dict(r) for r in con.execute('SELECT id,version,active_revision_id FROM itineraries WHERE trip_id=? ORDER BY id',(trip,))],
                'sources':[dict(r) for r in con.execute('SELECT id,version,status,policy_version FROM evidence_sources ORDER BY id')],
                'permissions':[dict(r) for r in con.execute('SELECT * FROM offline_source_permissions ORDER BY source_id')],
                'tasks':[dict(r) for r in con.execute('SELECT id,version FROM reservation_tasks WHERE trip_id=? ORDER BY id',(trip,))],
                'bookmarks':[dict(r) for r in con.execute('SELECT id,version,deleted_at FROM bookmarks WHERE trip_id=? ORDER BY id',(trip,))]})

    def bundle(self,actor,trip,include_notes):
        self.preparation.list(actor,trip)  # commit stale-task invalidation before the snapshot fence
        initial=self._guard(actor,trip)
        content,permissions=self.content(actor,trip,offline=True,include_notes=include_notes)
        stamp=datetime.now(timezone.utc);expires=min([stamp+timedelta(hours=24)]+[utc(p['expires_at']) for p in permissions])
        # Permission version changes invalidate reconnect validation, even when
        # a name happened to stay identical. No source URLs/actor details copied.
        manifest=digest({'content':content,'permissions':permissions})
        value={'allowed_fields':['trip.id','trip.title','trip.start_date','trip.end_date','trip.version','schedule.version','local_time','timezone','instant','lock','verification_status','permitted_place_name','permitted_address','permitted_map_link','travel_duration_basis','task_status_due','notes_if_selected'],'schema_version':1,'namespace':actor.id,'generated_at':stamp.isoformat(),'expires_at':expires.isoformat(),'manifest':manifest,'include_notes':include_notes,'read_only':True,'live_verification':False,**content}
        if len(dump(value).encode())>MAX_BUNDLE_BYTES:fail('OFFLINE_TOO_LARGE','오프라인 저장 범위가 너무 큽니다. 메모를 제외하거나 여행을 나눠 주세요.',413)
        if self._guard(actor,trip)!=initial:fail('OFFLINE_SOURCE_CHANGED','저장 준비 중 자료가 바뀌었습니다. 이전 저장본을 유지하고 다시 다운로드해 주세요.',409)
        return value
