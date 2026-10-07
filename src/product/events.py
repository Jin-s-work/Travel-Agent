"""Small opt-in events in the existing table. Never store arbitrary payloads."""
from datetime import datetime,timedelta,timezone
import json
import os
from src.foundation.repository import DomainError,dump,utcnow

RETENTION_DAYS=int(os.getenv('PRODUCT_EVENT_RETENTION_DAYS','30'))
if not 1<=RETENTION_DAYS<=90:raise ValueError('PRODUCT_EVENT_RETENTION_DAYS must be 1..90')
NAMES={'recommendation_view','recommendation_save','recommendation_reject','itinerary_add','source_open','booking_task_complete','visit_feedback'}
DETAIL_KEYS={'source_id','item_id','itinerary_id','feedback_id','task_id','action','completion_kind','reason_codes','measurement_version'}

def consented(con,owner):
    row=con.execute('SELECT analytics_enabled FROM product_preferences WHERE owner_id=?',(owner,)).fetchone()
    return bool(row and row[0])

def purge(con,now=None):
    cutoff=((now or datetime.now(timezone.utc))-timedelta(days=RETENTION_DAYS)).isoformat()
    con.execute('DELETE FROM discovery_events WHERE created_at<?',(cutoff,))
    con.execute('DELETE FROM product_run_metrics WHERE created_at<?',(cutoff,))
    # Deleted trip contributions must disappear even before asynchronous shredding.
    for table in ('discovery_events','product_run_metrics'):
        con.execute('DELETE FROM '+table+' WHERE trip_id IN (SELECT id FROM trips WHERE deleted_at IS NOT NULL)')

def validate_refs(con,owner,trip,place,run=None,source=None):
    if not con.execute('SELECT 1 FROM place_identities WHERE id=? AND deleted_at IS NULL',(place,)).fetchone():
        raise DomainError('NOT_FOUND','장소를 찾을 수 없습니다.',404)
    if run:
        row=con.execute('SELECT candidates_json,result_json FROM recommendation_runs WHERE id=? AND owner_id=? AND trip_id=?',(run,owner,trip)).fetchone()
        if not row or not row['result_json']:raise DomainError('NOT_FOUND','사용 가능한 추천 요청을 찾을 수 없습니다.',404)
        candidates=json.loads(row['candidates_json'] or '[]')
        if place not in {p['place_id'] for p in candidates}:raise DomainError('NOT_FOUND','이 요청의 장소가 아닙니다.',404)
    if source and not con.execute("SELECT 1 FROM evidence_sources WHERE id=? AND place_id=? AND status='active' AND display_permitted=1 AND read_confirmed=1",(source,place)).fetchone():
        raise DomainError('NOT_FOUND','사용 가능한 출처가 아닙니다.',404)

def record(con,owner,trip,name,event_id,*,place=None,run=None,detail=None,client_at=None,excluded=None):
    if name not in NAMES or set(detail or {})-DETAIL_KEYS:raise ValueError('Unsupported product event')
    if not consented(con,owner):return {'recorded':False,'reason':'ANALYTICS_DISABLED'}
    safe=dump(detail or {})
    old=con.execute('SELECT * FROM discovery_events WHERE id=?',(event_id,)).fetchone()
    if old:
        same=old['owner_id']==owner and old['trip_id']==trip and old['event']==name and old['place_id']==place and old['run_id']==run and old['detail_json']==safe
        if not same:raise DomainError('EVENT_ID_CONFLICT','같은 event_id에 다른 입력이 있습니다.',409)
        return {'recorded':True,'duplicate':True}
    con.execute('INSERT INTO discovery_events(id,owner_id,trip_id,event,place_id,run_id,created_at,schema_version,detail_json,client_at,exclusion_reason) VALUES(?,?,?,?,?,?,?,1,?,?,?)',
        (event_id,owner,trip,name,place,run,utcnow(),safe,client_at,excluded))
    return {'recorded':True,'duplicate':False}

def capture_run(con,owner,trip,run,snapshot,candidates,result):
    if not consented(con,owner) or not snapshot.get('analytics_opt_in'):return
    kinds=snapshot['conditions']['recommendation_types'];limit=snapshot['limit']
    unsupported=result.get('unsupported_constraints',[])
    summary={'city':snapshot['conditions']['city'],'types':kinds,'ranker_version':result['config_version'],
        'language_requested':('local_discovery' in kinds) if snapshot.get('recommendation_model_version')=='general_v3' else snapshot['review_language_filter']['required'],
        'language_unsupported':any('REVIEW' in x or 'LANGUAGE' in x or 'OBSERVATION' in x for x in unsupported),
        'target_per_section':limit,'shortage':any(len(result['sections'][k]['items'])<limit for k in kinds),
        'candidate_count':len(candidates),'selected_count':sum(len(result['sections'][k]['items']) for k in kinds),
        'synthetic':any(p.get('synthetic') for p in candidates),'reasons':result.get('reason_codes',[])}
    con.execute('INSERT OR IGNORE INTO product_run_metrics VALUES(?,?,?,?,?)',(run,owner,trip,dump(summary),utcnow()))
