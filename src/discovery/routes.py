"""Trip-owned bookmark/conditions endpoints and separate administrator reviews."""
from fastapi import APIRouter,Depends,Request
from src.foundation.auth import require_actor
from .models import ConditionsPatch,BookmarkInput,BookmarkPatch,VersionInput,Selection,PackInput,Approval,SourceReview,FactInput

from .intents import DiscoveryIntent

router=APIRouter(prefix='/api/v2')

@router.post('/trips/{trip_id}/discovery-intents',status_code=202)
def discovery_intent(trip_id:str,body:DiscoveryIntent,request:Request,actor=Depends(require_actor)):
    from .intents import submit
    return submit(request.app.state.recommendations,actor,trip_id,body.model_dump(mode='json'),request.headers.get('Idempotency-Key'))

@router.get('/trips/{trip_id}/discovery-intents/{ident}')
def discovery_intent_get(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):
    from .intents import get
    return get(request.app.state.recommendations,actor,trip_id,ident)

def service(request):return request.app.state.discovery

@router.get('/cities')
def cities(actor=Depends(require_actor)):
    from src.destinations import CATALOG
    return {**CATALOG,'coverage_note':'도시 등록은 추천 후보나 리뷰 품질 검증 완료를 의미하지 않습니다.'}

@router.get('/trips/{trip_id}/discovery-conditions')
def conditions(trip_id:str,request:Request,actor=Depends(require_actor)):
    return service(request).get_conditions(actor,trip_id)
@router.patch('/trips/{trip_id}/discovery-conditions')
def conditions_save(trip_id:str,body:ConditionsPatch,request:Request,actor=Depends(require_actor)):
    return service(request).save_conditions(actor,trip_id,body.model_dump(mode='json'))
@router.get('/trips/{trip_id}/bookmarks')
def bookmarks(trip_id:str,request:Request,actor=Depends(require_actor)):
    return {'items':service(request).list_bookmarks(actor,trip_id)}
@router.post('/trips/{trip_id}/bookmarks',status_code=201)
def bookmark_create(trip_id:str,body:BookmarkInput,request:Request,actor=Depends(require_actor)):
    return service(request).create_bookmark(actor,trip_id,body.model_dump())
@router.get('/trips/{trip_id}/bookmarks/{ident}')
def bookmark(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):
    return service(request).get_bookmark(actor,trip_id,ident)
@router.patch('/trips/{trip_id}/bookmarks/{ident}')
def bookmark_save(trip_id:str,ident:str,body:BookmarkPatch,request:Request,actor=Depends(require_actor)):
    return service(request).patch_bookmark(actor,trip_id,ident,body.model_dump())
@router.delete('/trips/{trip_id}/bookmarks/{ident}')
def bookmark_delete(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):
    return service(request).delete_bookmark(actor,trip_id,ident)
@router.post('/trips/{trip_id}/bookmarks/{ident}/resolve',status_code=202)
def resolve(trip_id:str,ident:str,body:VersionInput,request:Request,actor=Depends(require_actor)):
    return service(request).resolve(actor,trip_id,ident,body.model_dump(),request.headers.get('Idempotency-Key'))
@router.post('/trips/{trip_id}/bookmarks/{ident}/select')
def select(trip_id:str,ident:str,body:Selection,request:Request,actor=Depends(require_actor)):
    return service(request).select(actor,trip_id,ident,body.model_dump())
@router.get('/trips/{trip_id}/discovery-catalog')
def catalog(trip_id:str,request:Request,city:str|None=None,actor=Depends(require_actor)):
    return {'items':service(request).catalog(actor,trip_id,city)}
@router.get('/trips/{trip_id}/places/{place_id}/detail')
def detail(trip_id:str,place_id:str,request:Request,actor=Depends(require_actor)):
    return service(request).detail(actor,trip_id,place_id)
@router.get('/trips/{trip_id}/excluded-places')
def exclusions(trip_id:str,request:Request,actor=Depends(require_actor)):
    return {'items':service(request).list_exclusions(actor,trip_id)}
@router.post('/trips/{trip_id}/excluded-places/{place_id}')
def exclude(trip_id:str,place_id:str,request:Request,actor=Depends(require_actor)):
    return service(request).exclude(actor,trip_id,place_id)
@router.delete('/trips/{trip_id}/excluded-places/{place_id}')
def include(trip_id:str,place_id:str,request:Request,actor=Depends(require_actor)):
    return service(request).exclude(actor,trip_id,place_id,False)
@router.get('/admin/discovery-packs')
def packs(request:Request,actor=Depends(require_actor)):
    return {'items':service(request).list_packs(actor)}
@router.post('/admin/discovery-packs',status_code=201)
def pack_import(body:PackInput,request:Request,actor=Depends(require_actor)):
    return service(request).import_pack(actor,body.model_dump(mode='json'))
@router.patch('/admin/discovery-packs/{ident}')
def pack_review(ident:str,body:Approval,request:Request,actor=Depends(require_actor)):
    return service(request).approve_pack(actor,ident,body.model_dump())
@router.patch('/admin/discovery-sources/{ident}')
def source_review(ident:str,body:SourceReview,request:Request,actor=Depends(require_actor)):
    return service(request).review_source(actor,ident,body.model_dump(mode='json'))
@router.post('/admin/discovery-places/{place_id}/facts',status_code=201)
def add_fact(place_id:str,body:FactInput,request:Request,actor=Depends(require_actor)):
    return service(request).add_fact(actor,place_id,body.model_dump(mode='json'))


from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated

class PhotoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    place_ids: list[Annotated[str, Field(pattern=r'^osm_(?:node|way|relation)_[1-9][0-9]{0,15}$')]] = Field(min_length=1, max_length=6)

@router.post('/trips/{trip_id}/place-photos',status_code=202)
def resolve_photos(trip_id:str,body:PhotoRequest,request:Request,actor=Depends(require_actor)):
    return request.app.state.place_photos.submit(actor,trip_id,body.place_ids,request.headers.get('Idempotency-Key'))
