from datetime import datetime,timedelta,timezone
import json
from fastapi import APIRouter,Depends,Request,Query
from src.foundation.auth import require_actor
from src.foundation.repository import DomainError
from .models import Feedback,FeedbackPatch,Version,Consent,ClientEvent,FactReport,ReviewReport,Expense,Evaluation
from .reporting import report
from .evaluation import compare
router=APIRouter(prefix='/api/v2')
def service(r):return r.app.state.product

@router.get('/product-preferences')
def preferences(r:Request,actor=Depends(require_actor)):return service(r).preferences(actor)
@router.patch('/product-preferences')
def preferences_save(body:Consent,r:Request,actor=Depends(require_actor)):return service(r).consent(actor,body.model_dump())
@router.post('/trips/{trip}/events')
def event(trip:str,body:ClientEvent,r:Request,actor=Depends(require_actor)):return service(r).client_event(actor,trip,body.model_dump(mode='json'))
@router.get('/trips/{trip}/feedback')
def feedbacks(trip:str,r:Request,actor=Depends(require_actor)):return service(r).feedback_list(actor,trip)
@router.post('/trips/{trip}/places/{place}/feedback',status_code=201)
def feedback_create(trip:str,place:str,body:Feedback,r:Request,actor=Depends(require_actor)):return service(r).create_feedback(actor,trip,place,body.model_dump(mode='json'),r.headers.get('idempotency-key'))
@router.get('/trips/{trip}/feedback/{ident}')
def feedback_get(trip:str,ident:str,r:Request,actor=Depends(require_actor)):return service(r).get_feedback(actor,trip,ident)
@router.patch('/trips/{trip}/feedback/{ident}')
def feedback_patch(trip:str,ident:str,body:FeedbackPatch,r:Request,actor=Depends(require_actor)):return service(r).update_feedback(actor,trip,ident,body.expected_version,body.feedback.model_dump(mode='json'))
@router.delete('/trips/{trip}/feedback/{ident}')
def feedback_delete(trip:str,ident:str,body:Version,r:Request,actor=Depends(require_actor)):return service(r).update_feedback(actor,trip,ident,body.expected_version)
@router.post('/trips/{trip}/places/{place}/fact-reports',status_code=201)
def report_create(trip:str,place:str,body:FactReport,r:Request,actor=Depends(require_actor)):return service(r).report_fact(actor,trip,place,body.model_dump(mode='json'),r.headers.get('idempotency-key'))
@router.get('/trips/{trip}/fact-reports')
def own_reports(trip:str,r:Request,actor=Depends(require_actor)):return service(r).report_list(actor,trip)
@router.get('/admin/fact-reports')
def admin_reports(r:Request,actor=Depends(require_actor)):return service(r).report_list(actor)
@router.patch('/admin/fact-reports/{ident}')
def report_review(ident:str,body:ReviewReport,r:Request,actor=Depends(require_actor)):return service(r).review_report(actor,ident,body.model_dump(mode='json'))
@router.get('/trips/{trip}/cost-estimate')
def costs(trip:str,r:Request,actor=Depends(require_actor)):return service(r).costs(actor,trip)
@router.put('/trips/{trip}/cost-estimate')
def cost_update(trip:str,body:Expense,r:Request,actor=Depends(require_actor)):return service(r).expense(actor,trip,body.model_dump(mode='json'))
@router.get('/admin/product-report')
def product_report(r:Request,start:datetime|None=None,end:datetime|None=None,city:str|None=Query(None,pattern='^(tokyo|barcelona)$'),ranker:str|None=Query(None,max_length=100),synthetic:bool=False,recommendation_type:str|None=Query(None,pattern='^(local_discovery|landmark)$'),language_required:bool|None=None,actor=Depends(require_actor)):
    with r.app.state.db.connect() as con:r.app.state.discovery._admin(con,actor)
    end=end or datetime.now(timezone.utc);start=start or end-timedelta(days=30)
    return report(r.app.state.db,start=start.isoformat(),end=end.isoformat(),city=city,ranker=ranker,synthetic=synthetic,recommendation_type=recommendation_type,language_required=language_required)
@router.post('/trips/{trip}/recommendations/{ident}/evaluation')
def evaluation(trip:str,ident:str,body:Evaluation,r:Request,actor=Depends(require_actor)):
    run=r.app.state.recommendations.get(actor,trip,ident)
    if not run['result'] or run['data_status']!='current':raise DomainError('SOURCE_DATA_CHANGED','현재 사용 가능한 근거로만 비교할 수 있습니다.',409)
    with r.app.state.db.connect() as con:
        row=r.app.state.recommendations._get(con,actor,trip,ident)
        candidates=json.loads(row['candidates_json'] or '[]');snapshot=json.loads(row['snapshot_json'])
        r.app.state.recommendations._guard_catalog(con,actor,trip,snapshot['conditions']['city'],candidates)
        return compare(snapshot,candidates,body.candidate_version)
