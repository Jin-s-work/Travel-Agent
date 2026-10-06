from fastapi import APIRouter,Depends,Query,Request
from src.foundation.auth import require_actor
from .models import Generation,Preview,Apply,Undo,Intent

router=APIRouter(prefix='/api/v2/trips/{trip_id}/itineraries')

@router.post('',status_code=202)
def generate(trip_id:str,body:Generation,request:Request,actor=Depends(require_actor)):
    return request.app.state.itineraries.submit(actor,trip_id,body.model_dump(mode='json'),request.headers.get('idempotency-key'))

@router.get('')
def listing(trip_id:str,request:Request,actor=Depends(require_actor),limit:int=Query(20,ge=1,le=50),cursor:int=Query(0,ge=0)):
    return request.app.state.itineraries.list(actor,trip_id,limit,cursor)

@router.get('/{ident}')
def detail(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):
    return request.app.state.itineraries.get(actor,trip_id,ident)

@router.post('/{ident}/edit-previews')
def preview(trip_id:str,ident:str,body:Preview,request:Request,actor=Depends(require_actor)):
    return request.app.state.itineraries.preview(actor,trip_id,ident,body.model_dump(mode='json',exclude_none=True))

@router.patch('/{ident}')
def apply(trip_id:str,ident:str,body:Apply,request:Request,actor=Depends(require_actor)):
    return request.app.state.itineraries.apply(actor,trip_id,ident,body.model_dump())

@router.post('/{ident}/undo-previews')
def undo(trip_id:str,ident:str,body:Undo,request:Request,actor=Depends(require_actor)):
    return request.app.state.itineraries.undo_preview(actor,trip_id,ident,body.model_dump())

@router.post('/{ident}/edit-intents')
def intent(trip_id:str,ident:str,body:Intent,request:Request,actor=Depends(require_actor)):
    return request.app.state.itineraries.intent(actor,trip_id,ident,body.model_dump())
