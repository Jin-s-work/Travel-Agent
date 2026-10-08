from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import secrets
from typing import Literal
from fastapi import APIRouter, Depends, File, Request, UploadFile, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from authlib.integrations.base_client.errors import MismatchingStateError, OAuthError
from httpx import TransportError
from .auth import Actor, require_actor
from .repository import DomainError
from .models import TripCreate, TripPatch, BookingCreate, BookingPatch
from .search import SearchContext, answer
from . import workspaces

router = APIRouter(prefix='/api/v2')

class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user','assistant']
    content: str = Field(min_length=1,max_length=4000)

class AskRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1,max_length=1000)
    history: list[Message] = Field(default_factory=list,max_length=20)

    @model_validator(mode='after')
    def total(self):
        if not self.question.strip():
            raise ValueError('질문을 입력해 주세요.')
        if sum(len(m.content) for m in self.history)>20000:
            raise ValueError('대화 기록 전체는 20,000자 이내여야 합니다.')
        return self


def page(items, cursor, limit):
    if cursor:
        try:
            offset = int(cursor)
        except ValueError:
            raise DomainError('VALIDATION_FAILED','잘못된 페이지입니다.')
        if offset<0:
            raise DomainError('VALIDATION_FAILED','잘못된 페이지입니다.')
    else:
        offset=0
    return {'items':items[offset:offset+limit],'count':len(items),'next_cursor':str(offset+limit) if offset+limit<len(items) else None}


def document_dto(doc):
    return {key:value for key,value in doc.items() if key not in {'opaque_path','content_hash'}}


@router.get('/session')
def session(request:Request):
    configured=request.app.state.settings.auth_configured
    try:
        actor=request.app.state.auth.actor(request)
    except DomainError:
        return {'authenticated':False,'auth_configured':configured,'user':None,'csrf_token':None,'login_url':'/api/v2/auth/login'}
    return {'authenticated':True,'auth_configured':configured,'user':{'id':actor.id,'display_name':actor.display_name,'role':actor.role},'expires_at':actor.expires_at,'csrf_token':actor.csrf_token,'login_url':'/api/v2/auth/login'}


@router.get('/mail-capabilities')
def mail_capabilities(request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.operations.mail_capabilities()


@router.get('/auth/login')
async def login(request:Request):
    return await start_login(request)


@router.post('/auth/login')
async def login_post(request:Request):
    if request.headers.get('origin') != request.app.state.settings.public_base_url:
        raise DomainError('ORIGIN_REJECTED','요청 출처를 확인할 수 없습니다.',403)
    return await start_login(request)


async def start_login(request):
    auth=request.app.state.auth
    if not auth.settings.auth_configured:
        raise DomainError('AUTH_NOT_CONFIGURED','운영자가 로그인 제공자를 설정해야 합니다.',503)
    # A fresh provider flow owns the transient state cookie.
    request.session.clear()
    callback=auth.settings.public_base_url+'/api/v2/auth/callback'
    try:
        return await auth.oauth.identity.authorize_redirect(request,callback)
    except Exception as exc:
        return login_failure(request,exc,stage='start')


def login_failure(request,exc,*,stage='callback'):
    """Expose fixed recovery codes; never log tokens, claims, query strings or errors."""
    code='login_failed'
    if isinstance(exc,MismatchingStateError):
        cookie='__Host-oidc' if request.app.state.settings.secure_cookie else 'travel_dev_oidc'
        code='state_expired' if request.cookies.get(cookie) else 'cookie_missing'
    elif isinstance(exc,DomainError):
        code={'IDENTITY_UNVERIFIED':'identity_unverified','ACCESS_REVOKED':'access_revoked'}.get(exc.code,'login_failed')
    elif isinstance(exc,OAuthError):
        code={'access_denied':'login_cancelled','invalid_client':'provider_configuration',
              'unauthorized_client':'provider_configuration','invalid_grant':'state_expired'}.get(exc.error,'identity_unverified')
    elif isinstance(exc,TransportError):
        code='provider_unavailable'
    from src.operations.failures import diagnostic
    logging.getLogger(__name__).warning('login_failed stage=%s code=%s request_id=%s diagnostic=%s',
        stage,code,request.state.request_id,diagnostic(exc)['errors'])
    request.session.clear()
    return RedirectResponse('/?auth_error='+code+'&auth_request='+request.state.request_id,status_code=303)


@router.get('/auth/callback')
async def callback(request:Request):
    auth=request.app.state.auth
    if not auth.settings.auth_configured:
        raise DomainError('AUTH_NOT_CONFIGURED','로그인 설정이 필요합니다.',503)
    try:
        token=await auth.oauth.identity.authorize_access_token(request)
        claims=token.get('userinfo')
        if not claims:
            raise ValueError('Missing verified ID token')
        session_token=auth.complete_identity(claims)
    except Exception as exc:
        return login_failure(request,exc)
    request.session.clear()
    response=RedirectResponse('/',status_code=303)
    response.set_cookie(auth.settings.cookie_name,session_token,max_age=auth.settings.session_hours*3600,secure=auth.settings.secure_cookie,httponly=True,samesite='lax',path='/')
    return response


@router.post('/auth/logout')
def logout(request:Request,actor:Actor=Depends(require_actor)):
    with request.app.state.db.connect() as con:
        con.execute('DELETE FROM sessions WHERE id=?',(actor.session_id,))
    response=Response(status_code=204)
    response.delete_cookie(request.app.state.settings.cookie_name,path='/',secure=request.app.state.settings.secure_cookie,httponly=True,samesite='lax')
    return response


@router.get('/trips')
def trips(request:Request,actor:Actor=Depends(require_actor),cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
    return page(request.app.state.repo.list_trips(actor.id),cursor,limit)

@router.post('/trips',status_code=201)
def create_trip(body:TripCreate,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.repo.create_trip(actor.id,body.model_dump())

@router.get('/trips/{trip_id}')
def trip(trip_id:str,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.repo.get_trip(actor.id,trip_id)

@router.patch('/trips/{trip_id}')
def update_trip(trip_id:str,body:TripPatch,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.repo.update_trip(actor.id,trip_id,body.model_dump(exclude_unset=True))

@router.delete('/trips/{trip_id}',status_code=202)
def delete_trip(trip_id:str,request:Request,actor:Actor=Depends(require_actor)):
    request.app.state.repo.delete_trip(actor.id,trip_id)
    receipt=request.app.state.jobs.enqueue_cleanup(actor.id,actor.session_id,trip_id)
    return {'state':'deletion_requested',**receipt,'receipt_url':'/api/v2/deletions/'+receipt['receipt_id']}

@router.get('/trips/{trip_id}/bookings')
def bookings(trip_id:str,request:Request,actor:Actor=Depends(require_actor),date_from:str|None=None,date_to:str|None=None,kind:str|None=None,status:str|None=None,cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
    return page(request.app.state.repo.list_bookings(actor.id,trip_id,date_from,date_to,kind,status),cursor,limit)

@router.get('/trips/{trip_id}/workspace-draft')
def workspace_draft(trip_id:str,request:Request,actor:Actor=Depends(require_actor)):
    return workspaces.read(request,actor,trip_id)

@router.put('/trips/{trip_id}/workspace-draft')
def save_workspace_draft(trip_id:str,body:workspaces.Draft,request:Request,actor:Actor=Depends(require_actor)):
    return workspaces.save(request,actor,trip_id,body)

@router.delete('/trips/{trip_id}/workspace-draft',status_code=204)
def clear_workspace_draft(trip_id:str,request:Request,actor:Actor=Depends(require_actor)):
    workspaces.clear(request,actor,trip_id)
    return Response(status_code=204)

@router.post('/trips/{trip_id}/bookings',status_code=201)
def create_booking(trip_id:str,body:BookingCreate,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.repo.create_booking(actor.id,trip_id,body.model_dump())

@router.get('/trips/{trip_id}/bookings/{booking_id}')
def booking(trip_id:str,booking_id:str,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.repo.get_booking(actor.id,trip_id,booking_id)

@router.patch('/trips/{trip_id}/bookings/{booking_id}')
def update_booking(trip_id:str,booking_id:str,body:BookingPatch,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.repo.update_booking(actor.id,trip_id,booking_id,body.model_dump(exclude_unset=True))

@router.delete('/trips/{trip_id}/bookings/{booking_id}',status_code=202)
def delete_booking(trip_id:str,booking_id:str,request:Request,actor:Actor=Depends(require_actor)):
    request.app.state.repo.delete_booking(actor.id,trip_id,booking_id)
    return {'state':'deletion_requested'}

@router.get('/trips/{trip_id}/documents')
def documents(trip_id:str,request:Request,actor:Actor=Depends(require_actor),cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
    return page([document_dto(d) for d in request.app.state.repo.list_documents(actor.id,trip_id)],cursor,limit)

@router.post('/trips/{trip_id}/documents',status_code=202)
async def upload(trip_id:str,request:Request,files:list[UploadFile]=File(...),actor:Actor=Depends(require_actor)):
    repo,service=request.app.state.repo,request.app.state.documents
    repo.get_trip(actor.id,trip_id)
    if len(files)>service.settings.max_upload_files:
        raise DomainError('VALIDATION_FAILED','한 번에 파일 10개까지 업로드할 수 있습니다.')
    staged,rejected=[],[]
    total=0
    for file in files:
        data=await file.read(service.settings.max_upload_bytes+1)
        await file.close()
        total+=len(data)
        name=(file.filename or 'unnamed')[:255]
        error=service.validate(name,file.content_type or '',data)
        if error:
            rejected.append({'filename':name,'reason':error})
        else:
            staged.append((name,data))
    if total>service.settings.max_upload_bytes*service.settings.max_upload_files:
        raise DomainError('REQUEST_TOO_LARGE','업로드 전체 크기를 초과했습니다.',413)
    if not staged:
        raise DomainError('VALIDATION_FAILED','업로드 가능한 파일이 없습니다.',422,{'rejected':rejected})
    key=request.headers.get('idempotency-key')
    if not key or not 8<=len(key)<=128:
        raise DomainError('VALIDATION_FAILED','Idempotency-Key가 필요합니다.')
    payload_hash=hashlib.sha256(json.dumps([(name,hashlib.sha256(data).hexdigest()) for name,data in staged]+rejected,sort_keys=True).encode()).hexdigest()
    existing=request.app.state.jobs.lookup(actor.id,actor.session_id,'personal_trip',trip_id,'documents',key,payload_hash)
    if existing:
        return job_dto(existing)
    accepted,duplicates=[],[]
    for name,data in staged:
        try:
            doc=await asyncio.to_thread(service.save,actor.id,trip_id,name,data)
        except Exception:
            rejected.append({'filename':name,'reason':'STORAGE_FAILED'})
            continue
        entry={'document_id':doc['id'],'filename':name}
        if doc.get('active_generation_id') or any(a['document_id']==doc['id'] for a in accepted):
            duplicates.append(entry)
        else:
            accepted.append(entry)
    payload={'accepted':accepted,'duplicates':duplicates,'rejected':rejected}
    trip=repo.get_trip(actor.id,trip_id)
    queued=request.app.state.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'documents',payload,
        trip['version'],key,request_fingerprint=payload_hash,deadline_seconds=service.settings.job_deadline_seconds)
    return job_dto(queued)


def job_dto(job):
    return {**job,**job.get('submission',{}),**job.get('result',{}),'status_url':'/api/v2/jobs/'+job['job_id'],
            'events_url':'/api/v2/jobs/'+job['job_id']+'/events',
            'progress':{'done':job.get('done_count',0),'total':job.get('total_count'),'unit':'document'},
            'can_cancel':job['state'] in {'queued','running'} and not job.get('cancel_requested_at')}


@router.get('/jobs/{job_id}')
def job(job_id:str,request:Request,actor:Actor=Depends(require_actor)):
    return job_dto(request.app.state.jobs.get(job_id,actor.id,actor.session_id))


@router.get('/trips/{trip_id}/jobs')
def trip_jobs(trip_id:str,request:Request,actor:Actor=Depends(require_actor),cursor:str|None=None,limit:int=Query(50,ge=1,le=100)):
    # The list is always scoped to the authenticated owner and one live trip.
    try:
        offset=int(cursor or '0')
        if offset<0: raise ValueError()
    except ValueError:
        raise DomainError('VALIDATION_FAILED','잘못된 페이지입니다.')
    jobs=request.app.state.jobs.list_for_trip(trip_id,actor.id,actor.session_id,limit=limit,offset=offset)
    count=request.app.state.jobs.count_for_trip(trip_id,actor.id,actor.session_id)
    return {'items':[job_dto(j) for j in jobs],'count':count,'next_cursor':str(offset+limit) if offset+limit<count else None}


@router.post('/jobs/{job_id}/cancel',status_code=202)
def cancel_job(job_id:str,request:Request,actor:Actor=Depends(require_actor)):
    return job_dto(request.app.state.jobs.cancel(job_id,actor.id,actor.session_id))


@router.post('/jobs/{job_id}/retry',status_code=202)
def retry_job(job_id:str,request:Request,actor:Actor=Depends(require_actor)):
    jobs=request.app.state.jobs
    old=jobs.get(job_id,actor.id,actor.session_id)
    if old['operation']=='review_collection':
        raise DomainError('REVIEW_NEW_COLLECTION_REQUIRED','리뷰 조사 화면에서 범위와 최대 비용을 확인한 뒤 새 관측을 요청해 주세요.',409)
    if old['operation'] in {'bookmark_resolve', 'recommendations', 'itinerary_generate'}:
        raise DomainError('DISCOVERY_NEW_REQUEST_REQUIRED','탐색 화면에서 현재 조건을 확인하고 다시 요청해 주세요.',409)
    if old['state'] not in {'failed','partial','cancelled'}:
        raise DomainError('JOB_NOT_RETRYABLE','실패한 작업에서 재시도할 항목을 확인해 주세요.',409)
    with request.app.state.db.connect() as con:
        row=con.execute('SELECT payload_json,checkpoint_json FROM jobs WHERE id=?',(job_id,)).fetchone()
    payload=json.loads(row['payload_json']); checkpoint=json.loads(row['checkpoint_json'])
    result=old.get('result',{})
    units=result.get('files',checkpoint.get('files',[]))
    blocked={'BUDGET_EXHAUSTED','BUDGET_NOT_CONFIGURED','GLOBAL_BUDGET_EXHAUSTED','PROVIDER_OUTCOME_UNKNOWN','BUDGET_GLOBAL_STOP','USAGE_PENDING_RECONCILIATION','CALL_OUTCOME_UNKNOWN','GLOBAL_BUDGET_STOPPED','RECONCILIATION_REQUIRED'}
    failed={f['document_id'] for f in units if f.get('state')=='failed' and f.get('error_code') not in blocked}
    if old['operation']=='documents':
        completed={f['document_id'] for f in units if f.get('state') in {'succeeded','needs_review'}}
        accepted=[f for f in payload.get('accepted',[]) if f['document_id'] in failed]
        if not accepted:
            raise DomainError('JOB_NOT_RETRYABLE','예산·사용량 확인이 필요한 항목 또는 이미 완료된 파일은 자동 재시도하지 않습니다.',409)
        payload={'accepted':accepted,'rejected':[],'duplicates':[],'resume_from':job_id}
    elif old.get('error_code') in blocked:
        raise DomainError('JOB_NOT_RETRYABLE','운영자가 예산과 사용량을 확인해야 합니다.',409)
    payload['resume_from']=job_id
    trip=request.app.state.repo.get_trip(actor.id,old['trip_id'])
    key=request.headers.get('idempotency-key')
    queued=jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip['id'],old['operation'],payload,trip['version'],key,
        request_fingerprint=hashlib.sha256((job_id+json.dumps(payload,sort_keys=True)).encode()).hexdigest())
    return job_dto(queued)


@router.get('/jobs/{job_id}/events')
async def job_events(job_id:str,request:Request,actor:Actor=Depends(require_actor)):
    jobs=request.app.state.jobs
    snapshot=await asyncio.to_thread(jobs.get,job_id,actor.id,actor.session_id)
    try:
        cursor=int(request.headers.get('last-event-id',request.query_params.get('after','0')))
        if cursor<0 or cursor>snapshot['last_event_id']: raise ValueError()
    except ValueError:
        raise DomainError('EVENT_CURSOR_INVALID','현재 작업 상태를 다시 불러와 주세요.',409)
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            try:
                await asyncio.to_thread(request.app.state.auth.actor,request)
                snapshot=await asyncio.to_thread(jobs.get,job_id,actor.id,actor.session_id)
                events=await asyncio.to_thread(jobs.events,job_id,actor.id,actor.session_id,after=cursor)
            except DomainError:
                yield 'event: access_revoked\ndata: {}\n\n'
                return
            for event in events:
                cursor=event['id']
                yield f"id: {cursor}\nevent: {event['event']}\ndata: "+json.dumps(event['data'],ensure_ascii=False)+'\n\n'
            if snapshot['state'] in {'succeeded','partial','failed','cancelled'} and cursor>=snapshot['last_event_id']:
                yield 'event: snapshot\ndata: '+json.dumps({'job_id':job_id,'state':snapshot['state']})+'\n\n'
                return
            yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'private, no-store'})


@router.get('/deletions/{receipt_id}')
def deletion_status(receipt_id:str,request:Request,actor:Actor=Depends(require_actor)):
    return request.app.state.jobs.deletion_receipt(receipt_id,actor.id,actor.session_id)


@router.get('/usage')
def usage(request:Request,actor:Actor=Depends(require_actor)):
    return {'configured':request.app.state.budget.policy.valid,**request.app.state.budget.summary(actor.id)}


@router.get('/readiness')
def readiness(request:Request,actor:Actor=Depends(require_actor)):
    import os
    settings=request.app.state.settings
    version=request.app.state.db.schema_version()
    paths=[settings.database_path.parent,settings.documents_dir,settings.vectors_dir,settings.artifacts_dir]
    writable=all(os.access(path if path.exists() else path.parent,os.W_OK) for path in paths)
    search={'ready':0,'rebuilding':0,'structured':0}
    structured=request.app.state.operations.structured_only()
    for trip in request.app.state.repo.list_trips(actor.id):
        active=[d for d in request.app.state.repo.list_documents(actor.id,trip['id']) if d.get('active_generation_id')]
        if not active: continue
        if structured:
            search['structured']+=1
            continue
        try:
            with request.app.state.generations.reader(actor.id,trip['id']): pass
            search['ready']+=1
        except DomainError:
            search['rebuilding']+=1
    health=request.app.state.jobs.health()
    if actor.role!='admin': health.pop('jobs',None)
    from src.foundation.db import SCHEMA_VERSION
    return {'ready':writable and version==SCHEMA_VERSION and health['dispatcher_alive'],'schema_version':version,'storage_writable':writable,'dispatcher':health,'search':search,
            'external_calls_configured':request.app.state.budget.policy.valid,'auth_configured':settings.auth_configured}


@router.post('/trips/{trip_id}/documents/{document_id}/reprocess',status_code=202)
def reprocess(trip_id:str,document_id:str,request:Request,actor:Actor=Depends(require_actor)):
    repo=request.app.state.repo
    doc=repo.get_document(actor.id,trip_id,document_id)
    trip=repo.get_trip(actor.id,trip_id)
    payload={'accepted':[{'document_id':document_id,'filename':doc['display_filename']}],'rejected':[],'duplicates':[]}
    queued=request.app.state.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'documents',payload,
        trip['version'],request.headers.get('idempotency-key'),request_fingerprint=hashlib.sha256(('reprocess:'+document_id).encode()).hexdigest())
    return job_dto(queued)

@router.get('/trips/{trip_id}/documents/{document_id}/content')
def download(trip_id:str,document_id:str,request:Request,actor:Actor=Depends(require_actor)):
    doc=request.app.state.repo.get_document(actor.id,trip_id,document_id)
    content=request.app.state.documents.read(doc)
    request.app.state.auth.actor(request)
    request.app.state.repo.get_document(actor.id,trip_id,document_id)
    from urllib.parse import quote
    filename=quote(doc['display_filename'].replace('\\','_').replace('/','_'),safe='')
    return Response(content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=utf-8''"+filename,'Content-Security-Policy':"sandbox; default-src 'none'",'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@router.delete('/trips/{trip_id}/documents/{document_id}',status_code=202)
def delete_document(trip_id:str,document_id:str,request:Request,actor:Actor=Depends(require_actor)):
    request.app.state.repo.delete_document(actor.id,trip_id,document_id)
    trip=request.app.state.repo.get_trip(actor.id,trip_id)
    queued=request.app.state.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'reindex',{},trip['version'],
        'delete-document:'+document_id+':'+str(trip['version']))
    return {'state':'deletion_requested','job_id':queued['job_id']}


def perform_ask(trip_id,body,request,actor):
    trip=request.app.state.repo.get_trip(actor.id,trip_id)
    before={b['id']:b['version'] for b in request.app.state.repo.list_bookings(actor.id,trip_id)}
    context=SearchContext(actor.id,trip_id,trip['version'],request.state.request_id,actor.session_id)
    try:
        result=answer(request.app.state.repo,request.app.state.documents,context,body.question,[m.model_dump() for m in body.history],request.app.state.answer_generator)
    except DomainError:
        request.app.state.auth.actor(request)
        raise
    except Exception as exc:
        raise DomainError('ANSWER_UNAVAILABLE','답변 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.',503) from exc
    request.app.state.auth.actor(request)
    current=request.app.state.repo.get_trip(actor.id,trip_id)
    after={b['id']:b['version'] for b in request.app.state.repo.list_bookings(actor.id,trip_id)}
    if current['version'] != context.trip_version or before != after:
        raise DomainError('VERSION_CONFLICT','답변 중 예약이 변경되었습니다. 다시 질문해 주세요.',409)
    return result

@router.post('/trips/{trip_id}/ask')
def ask(trip_id:str,body:AskRequest,request:Request,actor:Actor=Depends(require_actor)):
    return perform_ask(trip_id,body,request,actor)

@router.post('/trips/{trip_id}/ask/stream')
def ask_stream(trip_id:str,body:AskRequest,request:Request,actor:Actor=Depends(require_actor)):
    # Same final DTO, checked immediately before output. No durable replay promised.
    result=perform_ask(trip_id,body,request,actor)
    def events():
        request.app.state.auth.actor(request)
        request.app.state.repo.get_trip(actor.id,trip_id)
        yield 'data: '+json.dumps({'event':'done',**result},ensure_ascii=False)+'\n\n'
    return StreamingResponse(events(),media_type='text/event-stream')
