"""UTC half-open, consented cohorts; counts and exclusions before percentages."""
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import json
from src.foundation.repository import DomainError
from .events import purge,consented,RETENTION_DAYS


def metric(n,d):
    return {'numerator':n,'denominator':d,'ratio':n/d if d else None,'display':f'{n}/{d}건' if d else '미측정','emphasize_ratio':d>=5}

def report(db,*,start,end,city=None,ranker=None,synthetic=False,recommendation_type=None,language_required=None):
    a,b=datetime.fromisoformat(start),datetime.fromisoformat(end)
    if not a.tzinfo or not b.tzinfo or a>=b or b-a>timedelta(days=90):raise DomainError('INVALID_PERIOD','UTC offset을 포함한 최대 90일 기간을 입력해 주세요.',422)
    start=a.astimezone(timezone.utc).isoformat();end=b.astimezone(timezone.utc).isoformat()
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE');purge(con)
        all_rows=con.execute('SELECT * FROM product_run_metrics WHERE created_at>=? AND created_at<?',(start,end)).fetchall()
        runs={}
        for row in all_rows:
            summary=json.loads(row['summary_json'])
            if not consented(con,row['owner_id']) or summary['synthetic']!=synthetic or (city and summary['city']!=city) or (ranker and summary['ranker_version']!=ranker):continue
            if recommendation_type and recommendation_type not in summary['types']:continue
            if language_required is not None and summary['language_requested']!=language_required:continue
            runs[row['run_id']]={**dict(row),'summary':summary}
        rows=con.execute('SELECT * FROM discovery_events WHERE created_at>=? AND created_at<?',(start,end)).fetchall()
        excluded=Counter();events=[]
        for row in rows:
            if row['schema_version']!=1:excluded['legacy_semantics']+=1;continue
            if row['exclusion_reason']:excluded[row['exclusion_reason']]+=1;continue
            if row['run_id'] not in runs:excluded['outside_completed_run_cohort']+=1;continue
            events.append({**dict(row),'detail':json.loads(row['detail_json'])})
        views={}
        for e in events:
            if e['event']=='recommendation_view':
                k=(e['run_id'],e['place_id']);views[k]=min(views.get(k,e['created_at']),e['created_at'])
        saved={(e['run_id'],e['place_id']) for e in events if e['event']=='recommendation_save' and (e['run_id'],e['place_id']) in views and views[(e['run_id'],e['place_id'])]<=e['created_at']}
        adopted={e['run_id'] for e in events if e['event']=='itinerary_add' and (e['run_id'],e['place_id']) in views and views[(e['run_id'],e['place_id'])]<=e['created_at']}
        active_saved=0
        for run,place in saved:
            if con.execute('SELECT 1 FROM bookmarks WHERE owner_id=? AND trip_id=? AND matched_place_id=? AND deleted_at IS NULL',(runs[run]['owner_id'],runs[run]['trip_id'],place)).fetchone():active_saved+=1
        language=[r for r in runs.values() if r['summary']['language_requested']]
        reasons=Counter();feedback_status=Counter();reports=Counter();completions=Counter();users=set()
        # Personal notes never enter this projection.
        for r in runs.values():users.add(r['owner_id'])
        feedback_users=set();feedback_without_run=0
        def place_matches(place):
            p=con.execute('SELECT city,provider FROM place_identities WHERE id=?',(place,)).fetchone()
            return bool(p and (p['provider']=='synthetic_editorial')==synthetic and (not city or p['city']==city))
        contributed_feedback={e['id'] for e in rows if e['event']=='visit_feedback' and e['schema_version']==1}
        for f in con.execute('SELECT f.id,f.owner_id,f.run_id,f.place_id,f.version,f.payload_json FROM visit_feedback f JOIN trips t ON t.id=f.trip_id WHERE t.deleted_at IS NULL AND f.withdrawn_at IS NULL AND f.updated_at>=? AND f.updated_at<?',(start,end)):
            if f"server:feedback:{f['id']}:{f['version']}" not in contributed_feedback:continue
            if not consented(con,f['owner_id']) or not place_matches(f['place_id']):continue
            if (ranker or recommendation_type or language_required is not None) and f['run_id'] not in runs:continue
            value=json.loads(f['payload_json']);feedback_status[value['feedback_kind']+':'+value['visit_status']]+=1
            reasons.update(value['reason_codes']);feedback_users.add(f['owner_id'])
            if not f['run_id']:feedback_without_run+=1
        for r in con.execute('SELECT r.owner_id,r.place_id,r.category,r.status FROM fact_reports r JOIN trips t ON t.id=r.trip_id JOIN product_preferences p ON p.owner_id=r.owner_id WHERE t.deleted_at IS NULL AND p.analytics_enabled=1 AND p.updated_at<=r.created_at AND r.created_at>=? AND r.created_at<?',(start,end)):
            if (ranker or recommendation_type or language_required is not None) and r['owner_id'] not in users:continue
            if place_matches(r['place_id']):reports[r['category']+':'+r['status']]+=1
        source_opens=sum(e['event']=='source_open' and e['schema_version']==1 and not e['exclusion_reason'] and consented(con,e['owner_id']) and place_matches(e['place_id']) and (not (ranker or recommendation_type or language_required is not None) or e['run_id'] in runs) for e in rows)
        # Completion events may have no recommendation run. Explicitly separate this cohort.
        for e in rows:
            if e['event']!='booking_task_complete' or e['schema_version']!=1 or e['owner_id'] not in users:continue
            d=json.loads(e['detail_json']);completions[(d.get('task_id'),d.get('completion_kind'))]=1
        completion_counts=Counter(k[1] for k in completions)
        costs=defaultdict(lambda:{'settled_micros':0,'unknown_charge_upper_micros':0,'reserved_upper_micros':0,'calls':0})
        for run,r in runs.items():
            job=con.execute('SELECT job_id FROM recommendation_runs WHERE id=?',(run,)).fetchone()
            for cost in con.execute('SELECT currency,state,actual_cost_micros,estimated_cost_micros FROM usage_reservations WHERE job_id=?',(job[0],)):
                c=costs[cost['currency']];c['calls']+=1
                if cost['state']=='settled':c['settled_micros']+=cost['actual_cost_micros']
                elif cost['state'] in ('unknown','pending_reconciliation','sent'):c['unknown_charge_upper_micros']+=cost['estimated_cost_micros']
                elif cost['state']=='reserved':c['reserved_upper_micros']+=cost['estimated_cost_micros']
        for c in costs.values():c['settled_per_completed_run_micros']=c['settled_micros']/len(runs) if runs else None
        # Failed/cancelled calls have no completed run metric; show separately rather than hide them.
        failed_costs=defaultdict(lambda:{'settled_micros':0,'unknown_charge_upper_micros':0,'reserved_upper_micros':0,'calls':0});failed_requests=set()
        retained_start=max(start,(datetime.now(timezone.utc)-timedelta(days=RETENTION_DAYS)).isoformat())
        for r in con.execute("SELECT r.id AS run_ref,r.owner_id,r.snapshot_json,u.* FROM recommendation_runs r JOIN jobs j ON j.id=r.job_id LEFT JOIN usage_reservations u ON u.job_id=r.job_id JOIN trips t ON t.id=r.trip_id JOIN product_preferences p ON p.owner_id=r.owner_id WHERE t.deleted_at IS NULL AND p.analytics_enabled=1 AND p.updated_at<=r.created_at AND j.state IN ('failed','cancelled') AND j.updated_at>=? AND j.updated_at<?",(retained_start,end)):
            snap=json.loads(r['snapshot_json'])
            if not consented(con,r['owner_id']) or not snap.get('analytics_opt_in') or (city and snap.get('conditions',{}).get('city')!=city):continue
            if recommendation_type and recommendation_type not in snap.get('conditions',{}).get('recommendation_types',[]):continue
            if language_required is not None and snap.get('review_language_filter',{}).get('required')!=language_required:continue
            # No completed ranker version can be verified for these requests.
            if ranker:continue
            failed_requests.add(r['run_ref'])
            if r['currency'] is None:continue
            # No candidate snapshot => cannot establish synthetic/real attribution.
            failed_costs['unattributed:'+r['currency']]['calls']+=1
            if r['state']=='settled':failed_costs['unattributed:'+r['currency']]['settled_micros']+=r['actual_cost_micros']
            elif r['state'] in ('unknown','pending_reconciliation','sent'):failed_costs['unattributed:'+r['currency']]['unknown_charge_upper_micros']+=r['estimated_cost_micros']
            elif r['state']=='reserved':failed_costs['unattributed:'+r['currency']]['reserved_upper_micros']+=r['estimated_cost_micros']
        for c in failed_costs.values():
            c['requests']=len(failed_requests)
            c['settled_per_failed_request_micros']=c['settled_micros']/len(failed_requests) if failed_requests else None
            c['unknown_upper_per_failed_request_micros']=c['unknown_charge_upper_micros']/len(failed_requests) if failed_requests else None
    return {'period':{'start':start,'end':end,'interval':'[start,end)','timezone':'UTC'},'cohort':{'city':city,'ranker_version':ranker,'recommendation_type':recommendation_type,'language_required':language_required,'synthetic':synthetic,'analytics':'explicit_opt_in','retention_days':RETENTION_DAYS,'users':len(users),'completed_runs':len(runs),'exposed_runs':len({k[0] for k in views}),'candidate_count':sum(r['summary']['candidate_count'] for r in runs.values())},
        'metrics':{'candidate_shortage':metric(sum(r['summary']['shortage'] for r in runs.values()),len(runs)),'language_unsupported':metric(sum(r['summary']['language_unsupported'] for r in language),len(language)),
            'exposure_to_save':metric(len(saved),len(views)),'itinerary_adoption':metric(len(adopted),len({k[0] for k in views}))},
        'currently_saved_exposed_pairs':active_saved,'feedback_cohort':{'users':len(feedback_users),'without_recommendation_run':feedback_without_run,'basis':'current_unwithdrawn_updated_in_period'},'feedback_current':dict(feedback_status),'reason_counts':dict(reasons),'fact_report_counts':dict(reports),'source_link_opens':source_opens,'booking_completion_transitions':dict(completion_counts),'operating_cost_by_currency':dict(costs),'failed_cancelled_unattributed_costs':dict(failed_costs),'excluded_events':dict(excluded),
        'measurement_limits':['완료 시각이 기간 안인 참여 run의 기간 내 노출만 집계','오프라인·분석 미참여·보관 기간 밖 자료 제외','브라우저 노출은 best-effort이며 차단/전송 실패는 측정하지 못함','source_open은 링크 열기이며 읽기/예약 완료가 아님','개인 메모는 분석 제외','소수 지인 베타이며 일반 여행자 선호나 인과 효과로 일반화 불가','실패/취소 비용은 미귀속 별도 표시; 공유 장소 조사 비용은 요청당 비용에 배분하지 않음']}
