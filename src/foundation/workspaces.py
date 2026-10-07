"""Small, session-scoped drafts. GET never writes or refreshes the TTL."""
from datetime import date, datetime, timedelta, timezone
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .repository import DomainError, dump, utcnow


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Context(Input):
    tab: Literal['trip','explore','itinerary','mail','today','preparation','product','ask','reviews','settings'] = 'trip'
    explore_view: Literal['discover','saved','local_discovery','landmark','recommendations','all'] = 'discover'
    discovery_mode: Literal['local_discovery','landmark','reference'] = 'local_discovery'
    visit_date: date | None = None
    categories: list[Literal['restaurant','cafe','attraction']] = Field(default_factory=list, max_length=3)
    recommendation_types: list[Literal['local_discovery','landmark']] = Field(default_factory=list, max_length=2)
    scroll_by_tab: dict[str, int] = Field(default_factory=dict)
    return_context: dict[str, str | None] = Field(default_factory=dict)

    @model_validator(mode='after')
    def small_context(self):
        if len(self.scroll_by_tab)>10 or any(k not in {'trip','explore','itinerary','mail','today','preparation','product','ask','reviews','settings'} or not 0<=v<=1000000 for k,v in self.scroll_by_tab.items()):
            raise ValueError('잘못된 화면 위치입니다.')
        if set(self.return_context)-{'insertion_date','insertion_time','itinerary_day','generation_preview_id','generation_job_id'} or any(v is not None and len(v)>150 for v in self.return_context.values()):
            raise ValueError('잘못된 돌아갈 화면입니다.')
        return self


class Selection(Input):
    place_id: str = Field(min_length=1,max_length=100)
    duration_minutes: int = Field(default=60,ge=5,le=720)
    duration_origin: Literal['default','user'] = 'default'
    selection_run_id: str | None = Field(default=None,max_length=100)


class Draft(Input):
    expected_version: int = Field(ge=0)
    context: Context = Field(default_factory=Context)
    selected_places: list[Selection] = Field(default_factory=list,max_length=30)
    # Incomplete/invalid form values must survive validation failures. These
    # are never promoted to live conditions without the ordinary condition API.
    conditions_draft: dict | None = None

    @model_validator(mode='after')
    def bounded(self):
        if len({v.place_id for v in self.selected_places})!=len(self.selected_places):
            raise ValueError('같은 장소를 두 번 저장할 수 없습니다.')
        value=self.conditions_draft
        if value is not None:
            if set(value)-{'conditions','overrides','stop_id','base_conditions_version','base_trip_version','filters'}:
                raise ValueError('지원하지 않는 조건 초안입니다.')
            def check(node,depth=0):
                if depth>8:raise ValueError('조건 초안 구조가 너무 깊습니다.')
                if isinstance(node,dict):
                    if len(node)>40:raise ValueError('조건 초안이 너무 큽니다.')
                    for k,v in node.items():
                        if not isinstance(k,str) or len(k)>60 or any(secret in k.lower() for secret in ('token','password','secret','email','confirmation_number','raw_mail')):
                            raise ValueError('초안에 저장할 수 없는 값입니다.')
                        check(v,depth+1)
                elif isinstance(node,list):
                    if len(node)>40:raise ValueError('조건 초안이 너무 큽니다.')
                    for v in node:check(v,depth+1)
                elif isinstance(node,str) and len(node)>2000:raise ValueError('입력은 2,000자 이내여야 합니다.')
            check(value)
        if len(json.dumps(self.model_dump(mode='json'),ensure_ascii=False,allow_nan=False).encode())>24000:
            raise ValueError('초안은 24KB 이내여야 합니다.')
        return self


def scope(request,actor,trip_id,con):
    return request.app.state.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)


def dto(row):
    expired=bool(row and row['expires_at']<=utcnow())
    payload=json.loads(row['payload_json']) if row and not expired else {'context':{},'selected_places':[],'conditions_draft':None}
    return {'version':row['version'] if row else 0,**payload,'expires_at':row['expires_at'] if row else None,'expired':expired}


def read(request,actor,trip_id):
    with request.app.state.db.connect() as con:
        scope(request,actor,trip_id,con)
        row=con.execute('SELECT * FROM workspace_drafts WHERE owner_id=? AND trip_id=? AND session_id=?',(actor.id,trip_id,actor.session_id)).fetchone()
        return dto(row)


def save(request,actor,trip_id,body):
    with request.app.state.db.connect() as con:
        con.execute('BEGIN IMMEDIATE');scope(request,actor,trip_id,con)
        args=(actor.id,trip_id,actor.session_id)
        row=con.execute('SELECT * FROM workspace_drafts WHERE owner_id=? AND trip_id=? AND session_id=?',args).fetchone()
        version=row['version'] if row else 0
        if version!=body.expected_version:
            raise DomainError('VERSION_CONFLICT','다른 화면에서 초안이 변경되었습니다. 현재 입력을 보존하고 저장된 초안과 비교해 주세요.',409,{'current_version':version})
        for selection in body.selected_places:
            if not con.execute('SELECT id FROM place_identities WHERE id=? AND deleted_at IS NULL',(selection.place_id,)).fetchone():
                raise DomainError('NOT_FOUND','선택한 장소를 찾을 수 없습니다.',404)
            if selection.selection_run_id:
                run=con.execute('SELECT candidates_json FROM recommendation_runs WHERE id=? AND owner_id=? AND trip_id=?',(selection.selection_run_id,actor.id,trip_id)).fetchone()
                if not run or selection.place_id not in {p['place_id'] for p in json.loads(run[0] or '[]')}:
                    raise DomainError('NOT_FOUND','선택한 추천을 찾을 수 없습니다.',404)
        payload=body.model_dump(mode='json',exclude={'expected_version'})
        stop_id=(payload.get('conditions_draft') or {}).get('stop_id')
        if stop_id and not con.execute('SELECT id FROM trip_stops WHERE id=? AND trip_id=?',(stop_id,trip_id)).fetchone():
            raise DomainError('NOT_FOUND','여행의 도시 구간을 찾을 수 없습니다.',404)
        context=payload['context']['return_context']
        if context.get('generation_preview_id') and not con.execute('SELECT d.itinerary_id FROM itinerary_generation_drafts d JOIN itineraries i ON i.id=d.itinerary_id WHERE i.id=? AND i.trip_id=? AND i.owner_id=?',(context['generation_preview_id'],trip_id,actor.id)).fetchone():
            raise DomainError('NOT_FOUND','일정 미리보기를 찾을 수 없습니다.',404)
        if context.get('generation_job_id') and not con.execute("SELECT id FROM jobs WHERE id=? AND actor_id=? AND trip_id=? AND operation='itinerary_generate'",(context['generation_job_id'],actor.id,trip_id)).fetchone():
            raise DomainError('NOT_FOUND','일정 작업을 찾을 수 없습니다.',404)
        stamp=utcnow();expires=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()
        con.execute('INSERT INTO workspace_drafts(owner_id,trip_id,session_id,version,payload_json,expires_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_id,trip_id,session_id) DO UPDATE SET version=excluded.version,payload_json=excluded.payload_json,expires_at=excluded.expires_at,updated_at=excluded.updated_at',(*args,version+1,dump(payload),expires,stamp))
        return {'version':version+1,**payload,'expires_at':expires,'expired':False}


def clear(request,actor,trip_id):
    # Keep a version tombstone so delayed autosaves cannot resurrect cleared data.
    with request.app.state.db.connect() as con:
        con.execute('BEGIN IMMEDIATE');scope(request,actor,trip_id,con)
        stamp=utcnow()
        con.execute("INSERT INTO workspace_drafts(owner_id,trip_id,session_id,version,payload_json,expires_at,updated_at) VALUES(?,?,?,1,'{}',?,?) "
                    "ON CONFLICT(owner_id,trip_id,session_id) DO UPDATE SET version=workspace_drafts.version+1,payload_json='{}',expires_at=excluded.expires_at,updated_at=excluded.updated_at",
                    (actor.id,trip_id,actor.session_id,stamp,stamp))
