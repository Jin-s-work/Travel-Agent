"""Private single-instance service: authenticated SQL ownership is authoritative."""
from __future__ import annotations
import json
import os
import mimetypes
import secrets
import uuid
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException
from src.config import PROJECT_ROOT
from src.foundation.settings import Settings
from src.foundation.db import Database
from src.foundation.repository import Repository, DomainError
from src.foundation.auth import Auth, require_actor
from src.foundation.documents import DocumentService
from src.foundation.routes import router, AskRequest, Message
from src.foundation.legacy_helpers import _policy_lines, _to_booking, _can_answer_directly

mimetypes.add_type('application/javascript','.js')

class BoundedBody:
    """Enforce the complete transport cap even without Content-Length."""
    def __init__(self, app, maximum):
        self.app,self.maximum=app,maximum
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method') not in {'POST','PUT','PATCH'}:
            return await self.app(scope,receive,send)
        chunks=[]; size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':
                return
            size+=len(message.get('body',b''))
            if size>self.maximum:
                response=JSONResponse({'error':{'code':'REQUEST_TOO_LARGE','message':'요청 크기 제한을 초과했습니다.','request_id':str(uuid.uuid4()),'retryable':False,'details':None}},status_code=413,headers={'Cache-Control':'private, no-store'})
                return await response(scope,receive,send)
            chunks.append(message.get('body',b''))
            if not message.get('more_body',False):
                break
        done=False
        async def replay():
            nonlocal done
            if done:
                return await receive()
            done=True
            return {'type':'http.request','body':b''.join(chunks),'more_body':False}
        await self.app(scope,replay,send)


def create_app(settings=None, *, parser=None, embedder=None, vector_factory=None, answer_generator=None,
               budget_policy=None, chroma_client=None, fault_hook=None, review_provider=None, review_detector=None,
               route_provider=None, storage_transport=None):
    settings=settings or Settings()
    if settings.storage_backend not in {'local','supabase'}:
        raise ValueError('Unsupported STORAGE_BACKEND')
    if settings.storage_backend=='supabase' and not settings.database_url:
        raise ValueError('Supabase mode requires DATABASE_URL; local fallback is forbidden')
    if settings.storage_backend=='local' and settings.database_url:
        raise ValueError('DATABASE_URL requires STORAGE_BACKEND=supabase')
    for private_path in (settings.database_path,settings.documents_dir,settings.vectors_dir):
        if private_path.resolve().is_relative_to((PROJECT_ROOT/'web').resolve()):
            raise ValueError('Private storage must be outside the public web directory')
    @asynccontextmanager
    async def lifespan(app):
        from src.reliability.maintenance import reconcile_storage
        leader=await asyncio.to_thread(app.state.jobs.acquire_dispatcher,app.state.dispatcher.owner)
        if app.state.documents.objects:
            try:
                await asyncio.to_thread(app.state.documents.objects.verify_private)
                if leader: await asyncio.to_thread(app.state.documents.objects.reconcile)
            except DomainError:
                logging.getLogger(__name__).warning('private_storage_degraded')
        app.state.storage_reconciliation=await asyncio.to_thread(reconcile_storage,app.state.db,settings,
            dispatcher_owner=app.state.dispatcher.owner if leader else None)
        if leader:
            await asyncio.to_thread(app.state.generations.cleanup)
            await asyncio.to_thread(app.state.reviews.purge)
        def purge_product():
            from src.product.events import purge
            with app.state.db.connect() as con:purge(con)
        await asyncio.to_thread(purge_product)
        await app.state.dispatcher.start()
        async def review_maintenance():
            cloud_swept=0
            while True:
                await asyncio.sleep(30)
                try:
                    await asyncio.to_thread(purge_product)
                    await asyncio.to_thread(app.state.reviews.purge)
                    await asyncio.to_thread(app.state.reviews.cleanup_remote)
                    if app.state.documents.objects and time.monotonic()-cloud_swept>1800:
                        await asyncio.to_thread(app.state.documents.objects.reconcile)
                        cloud_swept=time.monotonic()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning('review_maintenance_failed')
        review_task=asyncio.create_task(review_maintenance()) if leader else None
        backup_task=None
        if leader and os.getenv('BACKUP_ENABLED','0')=='1':
            from src.operations.remote import loop as backup_loop
            backup_task=asyncio.create_task(backup_loop(app))
        try:
            yield
        finally:
            if backup_task:
                backup_task.cancel()
                try:await backup_task
                except asyncio.CancelledError:pass
            if review_task:
                review_task.cancel()
                try: await review_task
                except asyncio.CancelledError: pass
            await app.state.dispatcher.stop()
            if app.state.documents.objects: app.state.documents.objects.close()
            app.state.db.close()
    app=FastAPI(title='Travel Agent',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.settings=settings
    from src.operations.health import Metrics
    app.state.metrics=Metrics()
    app.state.db=Database(settings.database_path,url=settings.database_url or None)
    app.state.repo=Repository(app.state.db)
    app.state.auth=Auth(app.state.db,settings)
    app.state.documents=DocumentService(app.state.repo,settings,parser,embedder,vector_factory)
    if settings.storage_backend=='supabase':
        from src.storage.objects import SupabaseObjects
        app.state.documents.objects=SupabaseObjects(settings,app.state.db,transport=storage_transport)
    app.state.answer_generator=answer_generator
    from src.reliability.jobs import Jobs
    from src.reliability.dispatcher import Dispatcher
    from src.reliability.generations import GenerationManager
    from src.reliability.budget import Budget, BudgetPolicy
    from src.reliability.providers import ProviderGateway
    from src.reliability.handlers import Operations
    app.state.jobs=Jobs(app.state.db,lease_seconds=settings.job_lease_seconds,max_attempts=settings.job_max_attempts)
    app.state.budget=Budget(app.state.db,budget_policy or BudgetPolicy.from_file(settings.pricing_config))
    app.state.gateway=ProviderGateway(app.state.budget,settings.artifacts_dir)
    app.state.generations=GenerationManager(app.state.db,app.state.repo,settings.vectors_dir,client=chroma_client,jobs=app.state.jobs)
    app.state.operations=Operations(app.state.repo,app.state.documents,app.state.jobs,app.state.generations,app.state.gateway,
                                    answer_generator=answer_generator,fault_hook=fault_hook)
    app.state.documents.operations=app.state.operations
    from src.research.service import ReviewService
    app.state.reviews=ReviewService(app.state.db,app.state.repo,app.state.jobs,app.state.gateway,provider=review_provider,detector=review_detector,fault_hook=fault_hook)
    from src.discovery.service import DiscoveryService
    app.state.discovery=DiscoveryService(app.state.db,app.state.repo,app.state.jobs,app.state.reviews,allow_synthetic=settings.environment=='development')
    from src.recommendations.service import Recommendations
    app.state.recommendations=Recommendations(app.state.db,app.state.repo,app.state.jobs,app.state.discovery,app.state.reviews)
    from src.itineraries.travel_time import TravelTime
    from src.itineraries.service import Itineraries
    app.state.travel_time=TravelTime(app.state.gateway,app.state.jobs,provider=route_provider)
    app.state.itineraries=Itineraries(app.state.db,app.state.repo,app.state.jobs,app.state.discovery,
                                    app.state.recommendations,app.state.travel_time)
    from src.travel_tools.alternatives import Alternatives
    app.state.alternatives=Alternatives(app.state.itineraries,app.state.discovery)
    from src.travel_tools.preparation import Preparation
    app.state.preparation=Preparation(app.state.db,app.state.repo,app.state.itineraries,app.state.discovery)
    from src.product.service import Product
    app.state.product=Product(app.state.db,app.state.repo,app.state.discovery,app.state.recommendations,app.state.itineraries)
    from src.travel_tools.today import Today
    app.state.today=Today(app.state.db,app.state.repo,app.state.itineraries,app.state.discovery,app.state.preparation)
    def dispatch(job,ctx):
        if job['operation']=='review_collection':
            return app.state.reviews.execute(job,ctx)
        if job['operation']=='bookmark_resolve':
            return app.state.discovery.execute(job,ctx)
        if job['operation']=='recommendations':
            return app.state.recommendations.execute(job,ctx)
        if job['operation']=='itinerary_generate':
            return app.state.itineraries.execute(job,ctx)
        return app.state.operations(job,ctx)
    app.state.dispatcher=Dispatcher(app.state.jobs,dispatch,poll_seconds=settings.job_poll_seconds,
                                   heartbeat_seconds=settings.job_heartbeat_seconds,shutdown_seconds=settings.job_shutdown_seconds)
    app.add_middleware(SessionMiddleware,secret_key=settings.session_secret or secrets.token_urlsafe(48),session_cookie='__Host-oidc' if settings.secure_cookie else 'travel_dev_oidc',max_age=600,same_site='lax',https_only=settings.secure_cookie)
    app.add_middleware(BoundedBody,maximum=settings.max_request_bytes)

    @app.middleware('http')
    async def headers(request,call_next):
        request.state.request_id=str(uuid.uuid4())
        started=time.monotonic()
        try:
            if request.url.path.startswith('/api/'):
                from src.operations.controls import read as read_controls
                def current_mode():
                    with app.state.db.connect() as con:return read_controls(con)['mode']
                mode=await asyncio.to_thread(current_mode)
                restoring=any((settings.database_path.parent/p).exists() for p in ('RESTORE_PENDING.json','RESTORE_INCOMPLETE.json'))
                blocked=restoring or mode=='maintenance' or (mode=='read_only' and request.method not in {'GET','HEAD','OPTIONS'} and request.url.path not in {'/api/v2/auth/login','/api/v2/auth/logout'})
                if blocked and request.url.path!='/api/health':
                    response=JSONResponse({'error':{'code':'RESTORE_VALIDATION_REQUIRED' if restoring else 'SERVICE_READ_ONLY','message':'운영 점검 중입니다. 잠시 후 다시 시도해 주세요.','request_id':request.state.request_id,'retryable':True,'details':None}},status_code=503)
                else:response=await call_next(request)
            else:response=await call_next(request)
        except Exception:
            logging.getLogger(__name__).error('request_failed request_id=%s code=INTERNAL_ERROR',request.state.request_id)
            response=JSONResponse({'error':{'code':'INTERNAL_ERROR','message':'요청을 처리하지 못했습니다.','request_id':request.state.request_id,'retryable':False,'details':None}},status_code=500)
        if request.url.path not in {'/api/health','/health/live','/health/ready'}:
            app.state.metrics.record(response.status_code,time.monotonic()-started)
        response.headers['X-Request-ID']=request.state.request_id
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        response.headers['X-Frame-Options']='DENY'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='private, no-store'
            response.headers['Vary']='Cookie'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request,exc):
        return JSONResponse({'error':{'code':exc.code,'message':exc.message,'request_id':request.state.request_id,'retryable':exc.code in {'PROVIDER_UNAVAILABLE','SEARCH_REBUILDING','SERVICE_STOPPING','VERSION_CONFLICT'},'details':exc.details}},status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        details=[{'field':'.'.join(str(p) for p in e['loc'] if p!='body'),'message':e['msg']} for e in exc.errors()]
        return JSONResponse({'error':{'code':'VALIDATION_FAILED','message':'입력값을 확인해 주세요.','request_id':request.state.request_id,'retryable':False,'details':details}},status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return JSONResponse({'error':{'code':'NOT_FOUND' if exc.status_code==404 else 'REQUEST_FAILED','message':'요청을 처리할 수 없습니다.','request_id':request.state.request_id,'retryable':False,'details':None}},status_code=exc.status_code)

    @app.get('/api/health')
    @app.get('/health/live')
    def health():
        return {'ok':True}

    @app.get('/health/ready')
    def health_ready():
        from src.operations.health import ready
        result=ready(app)
        return JSONResponse(result,status_code=200 if result['ready'] else 503,headers={'Cache-Control':'no-store'})

    @app.get('/api/v2/admin/operations')
    def operations_status(actor=Depends(require_actor)):
        if actor.role!='admin':raise DomainError('NOT_FOUND','자료를 찾을 수 없습니다.',404)
        from src.operations.health import summary,ready
        return {'health':ready(app),'observations':summary(app)}

    @app.get('/api/v2/openapi.json')
    def private_openapi(actor=Depends(require_actor)):
        return app.openapi()

    app.include_router(router)
    from src.research.routes import router as review_router
    app.include_router(review_router)
    from src.discovery.routes import router as discovery_router
    app.include_router(discovery_router)
    from src.http.recommendations import router as recommendation_router
    app.include_router(recommendation_router)
    from src.product.routes import router as product_router
    app.include_router(product_router)
    from src.itineraries.routes import router as itinerary_router
    app.include_router(itinerary_router)
    from src.travel_tools.routes import router as travel_tools_router
    app.include_router(travel_tools_router)

    @app.api_route('/api/{legacy_path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
    def retired(legacy_path:str,request:Request,actor=Depends(require_actor)):
        # Old global index/download/delete paths must never access shared private files.
        raise DomainError('LEGACY_API_RETIRED','여행을 선택한 뒤 v2 API를 사용해 주세요.',410)

    @app.get('/sw.js')
    def service_worker():
        return FileResponse(PROJECT_ROOT/'web/sw.js',media_type='application/javascript',headers={'Cache-Control':'no-cache','Service-Worker-Allowed':'/'})

    app.mount('/',StaticFiles(directory=PROJECT_ROOT/'web',html=True),name='web')
    return app

app=create_app()
