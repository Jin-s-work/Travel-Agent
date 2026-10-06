"""Authenticated private accommodation endpoints. Mutations reuse global Origin/CSRF middleware."""
from fastapi import APIRouter,Depends,Query,Request
from src.foundation.auth import require_actor
from src.foundation.models import valid_date
from src.foundation.repository import DomainError
from .models import AccommodationInput,AccommodationPatch,ResolutionInput,CandidateSelection
router=APIRouter(prefix='/api/v2')
def service(request):return request.app.state.accommodations
@router.get('/trips/{trip_id}/accommodations')
def stays(trip_id:str,request:Request,actor=Depends(require_actor)):return service(request).list(actor,trip_id)
@router.post('/trips/{trip_id}/accommodations',status_code=201)
def create(trip_id:str,body:AccommodationInput,request:Request,actor=Depends(require_actor)):return service(request).create(actor,trip_id,body.model_dump(mode='json'))
@router.get('/trips/{trip_id}/accommodations/{ident}')
def detail(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):return service(request).get(actor,trip_id,ident)
@router.patch('/trips/{trip_id}/accommodations/{ident}')
def patch(trip_id:str,ident:str,body:AccommodationPatch,request:Request,actor=Depends(require_actor)):return service(request).patch(actor,trip_id,ident,body.model_dump(mode='json'))
@router.delete('/trips/{trip_id}/accommodations/{ident}')
def delete(trip_id:str,ident:str,request:Request,expected_version:int=Query(ge=1),actor=Depends(require_actor)):return service(request).delete(actor,trip_id,ident,expected_version)
@router.post('/trips/{trip_id}/accommodations/{ident}/resolve',status_code=202)
def resolve(trip_id:str,ident:str,body:ResolutionInput,request:Request,actor=Depends(require_actor)):return service(request).resolve(actor,trip_id,ident,body.model_dump(mode='json'),request.headers.get('Idempotency-Key'))
@router.post('/trips/{trip_id}/accommodations/{ident}/select')
def select(trip_id:str,ident:str,body:CandidateSelection,request:Request,actor=Depends(require_actor)):return service(request).select(actor,trip_id,ident,body.model_dump(mode='json'))
@router.get('/trips/{trip_id}/origin-context')
def origin(trip_id:str,request:Request,visit_date:str,stop_id:str|None=None,local_time:str|None=None,accommodation_id:str|None=None,expected_version:int|None=Query(default=None,ge=1),actor=Depends(require_actor)):
    try:valid_date(visit_date)
    except ValueError:raise DomainError('VALIDATION_FAILED','방문일을 YYYY-MM-DD 형식으로 입력해 주세요.',422)
    if expected_version is not None and not accommodation_id:raise DomainError('VALIDATION_FAILED','숙소 선택과 버전을 함께 보내 주세요.',422)
    overrides={'origin_selection':{'kind':'accommodation','accommodation_id':accommodation_id,'expected_version':expected_version}} if accommodation_id else {}
    return service(request).origin_context(actor,trip_id,{'date':visit_date,'stop_id':stop_id,'local_time':local_time},overrides)
