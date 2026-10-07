"""Admin research and scoped consumer evidence; existing CSRF/session checks apply."""
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ConfigDict
from src.foundation.auth import require_actor

router=APIRouter(prefix='/api/v2')
class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Controls(Input):
    expected_version:int=Field(ge=1)
    research_enabled:bool
    production_enabled:bool
class Policy(Input):
    provider:Literal['apify','fake']
    version:str=Field(min_length=1,max_length=100)
    purpose:str=Field(min_length=8,max_length=1000)
    evidence:list[str]=Field(min_length=1,max_length=10)
    rights:dict[str,bool]
    reviewed_at:str
    expires_at:str
    aggregate_ttl_seconds:int=Field(default=604800,ge=1,le=2592000)
    id_ttl_seconds:int=Field(default=86400,ge=1,le=86400)
    remote_raw_ttl_seconds:int=Field(default=0,ge=0,le=86400)
class Place(Input):
    provider:Literal['apify','fake']
    external_place_id:str=Field(min_length=5,max_length=256,pattern=r'^[A-Za-z0-9_:\-]+$')
    city:Literal['tokyo','barcelona']
    name:str=Field(min_length=1,max_length=200)
    address:str=Field(min_length=4,max_length=400)
    source_url:str=Field(max_length=2000)
    rating:float|None=Field(default=None,ge=0,le=5)
    total_rating_count:int|None=Field(default=None,ge=0,le=100000000)
class Verify(Input):
    expected_version:int=Field(ge=1)
    status:Literal['verified','needs_confirmation','blocked']
    evidence:str=Field(min_length=10,max_length=1000)
class Link(Input):
    place_id:str=Field(min_length=1,max_length=100)
class Collection(Input):
    place_id:str=Field(max_length=100)
    policy_id:str=Field(max_length=100)
    max_review_records:int=Field(default=200,ge=1,le=200)
    max_pages:int=Field(default=20,ge=1,le=20)
    max_elapsed_seconds:int=Field(default=300,ge=1,le=900)
    max_total_charge_usd:str=Field(default='0',pattern=r'^(?:[0-2](?:\.\d{1,6})?|3(?:\.0{1,6})?)$')
class Quality(Input):
    city:Literal['tokyo','barcelona']
    detector_version:str=Field(min_length=1,max_length=200)
    domain:Literal['restaurant_reviews','general_corpus']
    synthetic:bool
    heldout_disjoint:bool
    label_count:int=Field(ge=0)
    original_checks:int=Field(ge=0)
    original_translation_errors:int=Field(ge=0)
    local:dict[str,int]
    korean:dict[str,int]
    report_sha256:str=Field(pattern='^[a-f0-9]{64}$')
    evidence_url:str=Field(min_length=8,max_length=2000)
    expires_at:str

def admin_service(request,actor):
    service=request.app.state.reviews
    with service.db.connect() as con: service._admin(con,actor)
    return service

@router.get('/admin/review-controls')
def controls(request:Request,actor=Depends(require_actor)): return admin_service(request,actor).controls(actor)
@router.patch('/admin/review-controls')
def patch_controls(body:Controls,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).set_controls(actor,body.model_dump())
@router.get('/admin/review-policies')
def policies(request:Request,actor=Depends(require_actor)): return {'items':admin_service(request,actor).policies(actor)}
@router.post('/admin/review-policies',status_code=201)
def policy(body:Policy,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).add_policy(actor,body.model_dump())
@router.delete('/admin/review-policies/{ident}')
def revoke(ident:str,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).revoke_policy(actor,ident)
@router.get('/admin/review-places')
def places(request:Request,actor=Depends(require_actor)): return {'items':admin_service(request,actor).places(actor)}
@router.post('/admin/review-places',status_code=201)
def place(body:Place,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).add_place(actor,body.model_dump())
@router.patch('/admin/review-places/{ident}')
def verify(ident:str,body:Verify,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).verify_place(actor,ident,body.model_dump())
@router.delete('/admin/review-places/{ident}')
def delete(ident:str,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).delete_place(actor,ident)
@router.post('/trips/{trip_id}/places',status_code=201)
def link(trip_id:str,body:Link,request:Request,actor=Depends(require_actor)): return request.app.state.reviews.link(actor,trip_id,body.place_id)
@router.get('/trips/{trip_id}/places')
def trip_places(trip_id:str,request:Request,actor=Depends(require_actor)): return {'items':request.app.state.reviews.places(actor,trip_id)}
@router.get('/trips/{trip_id}/places/{place_id}/review-evidence')
def evidence(trip_id:str,place_id:str,request:Request,actor=Depends(require_actor)): return request.app.state.reviews.evidence(actor,trip_id,place_id)
@router.post('/admin/review-collection-runs',status_code=202)
def collect(body:Collection,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).submit(actor,body.model_dump(),request.headers.get('idempotency-key'))
@router.get('/admin/review-collection-runs')
def runs(request:Request,actor=Depends(require_actor)): return {'items':admin_service(request,actor).list_runs(actor)}
@router.get('/admin/review-collection-runs/{ident}')
def run(ident:str,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).get_run(actor,ident)
@router.post('/admin/review-quality-evaluations',status_code=201)
def quality(body:Quality,request:Request,actor=Depends(require_actor)): return admin_service(request,actor).record_quality(actor,body.model_dump())

@router.post('/admin/review-collection-runs/{ident}/remote-cleanup')
def cleanup(ident:str,request:Request,actor=Depends(require_actor)):
    service=admin_service(request,actor)
    current=service.get_run(actor,ident)
    if current['state'] not in {'succeeded','partial','failed','cancelled'} and (not current.get('remote_cleanup') or current['remote_cleanup']['deadline_at']>service.now().isoformat()):
        from src.foundation.repository import DomainError
        raise DomainError('COLLECTION_STILL_RUNNING','수집 취소 또는 보관 기한 이후에 원격 삭제를 실행해 주세요.',409)
    return service.cleanup_remote(ident)
