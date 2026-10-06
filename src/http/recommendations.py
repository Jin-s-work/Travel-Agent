from fastapi import APIRouter, Depends, Request, Query
from src.foundation.auth import require_actor
from src.recommendations.models import RecommendationInput,ComparisonInput,EventInput

router=APIRouter(prefix='/api/v2')

@router.post('/trips/{trip_id}/recommendations',status_code=202)
def submit(trip_id:str,body:RecommendationInput,request:Request,actor=Depends(require_actor)):
    return request.app.state.recommendations.submit(actor,trip_id,body.model_dump(mode='json'),request.headers.get('idempotency-key'))

@router.get('/trips/{trip_id}/recommendations')
def runs(trip_id:str,request:Request,actor=Depends(require_actor),limit:int=Query(20,ge=1,le=50),cursor:int=Query(0,ge=0)):
    return request.app.state.recommendations.list(actor,trip_id,limit,cursor)

@router.get('/trips/{trip_id}/recommendations/{ident}')
def run(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):
    return request.app.state.recommendations.get(actor,trip_id,ident)

@router.post('/trips/{trip_id}/comparisons',status_code=201)
def compare(trip_id:str,body:ComparisonInput,request:Request,actor=Depends(require_actor)):
    return request.app.state.recommendations.compare(actor,trip_id,body.model_dump())

@router.get('/trips/{trip_id}/comparisons/{ident}')
def comparison(trip_id:str,ident:str,request:Request,actor=Depends(require_actor)):
    return request.app.state.recommendations.get_comparison(actor,trip_id,ident)

@router.post('/trips/{trip_id}/discovery-events',status_code=201)
def event(trip_id:str,body:EventInput,request:Request,actor=Depends(require_actor)):
    return request.app.state.recommendations.event(actor,trip_id,body.model_dump())
