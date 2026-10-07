from fastapi import APIRouter,Depends,Request,Response,Query
from src.foundation.auth import require_actor
from src.foundation.repository import DomainError
from .models import TaskCreate,TaskCommand,Inquiry,Alternative,OfflineRequest,OfflinePermission

router=APIRouter(prefix='/api/v2')

def enabled(request,key):
    if not getattr(request.app.state.settings,key+'_enabled'):
        raise DomainError('FEATURE_DISABLED','이 기능은 현재 꺼져 있습니다.',404)

@router.get('/travel-tools/features')
def features(request:Request,actor=Depends(require_actor)):
    return {k:getattr(request.app.state.settings,k+'_enabled') for k in ('preparation','plan_b','offline')}

@router.get('/trips/{trip}/reservation-tasks')
def listing(trip:str,request:Request,actor=Depends(require_actor)):
    enabled(request,'preparation');return request.app.state.preparation.list(actor,trip)

@router.post('/trips/{trip}/reservation-tasks',status_code=201)
def create(trip:str,body:TaskCreate,request:Request,actor=Depends(require_actor)):
    enabled(request,'preparation');return request.app.state.preparation.create(actor,trip,body.model_dump(mode='json'),request.headers.get('idempotency-key'))

@router.get('/trips/{trip}/reservation-tasks/{ident}')
def get(trip:str,ident:str,request:Request,actor=Depends(require_actor)):
    enabled(request,'preparation');return request.app.state.preparation.get(actor,trip,ident)

@router.patch('/trips/{trip}/reservation-tasks/{ident}')
@router.post('/trips/{trip}/reservation-tasks/{ident}/revalidate')
def command(trip:str,ident:str,body:TaskCommand,request:Request,actor=Depends(require_actor)):
    enabled(request,'preparation');return request.app.state.preparation.command(actor,trip,ident,body.model_dump(mode='json'))

@router.get('/trips/{trip}/reservation-tasks/{ident}/calendar.ics')
def export(trip:str,ident:str,request:Request,actor=Depends(require_actor)):
    enabled(request,'preparation');return Response(request.app.state.preparation.export(actor,trip,ident),media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="preparation.ics"'})

@router.post('/trips/{trip}/reservation-tasks/{ident}/inquiry-drafts')
def draft(trip:str,ident:str,body:Inquiry,request:Request,actor=Depends(require_actor)):
    enabled(request,'preparation');return request.app.state.preparation.draft(actor,trip,ident,body.model_dump())

@router.post('/trips/{trip}/itineraries/{ident}/alternatives')
def alternatives(trip:str,ident:str,body:Alternative,request:Request,actor=Depends(require_actor)):
    enabled(request,'plan_b');return request.app.state.alternatives.propose(actor,trip,ident,body.model_dump(mode='json'))

@router.get('/trips/{trip}/today')
def today(trip:str,request:Request,itinerary_id:str|None=None,day:str|None=Query(None,max_length=10),actor=Depends(require_actor)):
    return request.app.state.today.today(actor,trip,itinerary_id,day)

@router.post('/trips/{trip}/offline-bundles')
def bundle(trip:str,body:OfflineRequest,request:Request,actor=Depends(require_actor)):
    enabled(request,'offline');return request.app.state.today.bundle(actor,trip,body.include_notes)

@router.post('/admin/offline-source-permissions')
def offline_permission(body:OfflinePermission,request:Request,actor=Depends(require_actor)):
    enabled(request,'offline');return request.app.state.today.grant(actor,body.model_dump(mode='json'))

from .models import OfflineCheck
@router.post('/trips/{trip}/offline-bundles/validate')
def validate_bundle(trip:str,body:OfflineCheck,request:Request,actor=Depends(require_actor)):
    enabled(request,'offline')
    fresh=request.app.state.today.bundle(actor,trip,body.include_notes)
    return {'valid':fresh['manifest']==body.manifest,'namespace':actor.id,'trip_id':trip,'schema_version':1}
