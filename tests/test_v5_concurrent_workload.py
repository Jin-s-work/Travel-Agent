"""Five synthetic users, one dispatcher, actual SQL/HTTP timings, fake paid calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from statistics import median
from collections import Counter
import json,os,platform,resource,time
from pathlib import Path
from tests.test_foundation_api import service,_job
from tests.test_public_discovery import public,prepare
from tests.test_v5_candidate_retrieval import install


def percentiles(values):
    ordered=sorted(values)
    return {'samples':len(values),'p50':median(values),'p95':ordered[max(0,(95*len(values)+99)//100-1)]}


def test_five_users_overlap_documents_and_recommendations(public):
    calls=install(public)
    users=[public.login('load-synthetic-'+str(i)) for i in range(5)]
    inputs=[]
    for u in users:
        trip,base=prepare(SimpleNamespace(client=u.client))
        inputs.append((u,trip,base))
    def submit(row):
        u,trip,base=row;started=time.perf_counter()
        # Recommendation first: a finished document job bumps trips.version, so a
        # recommendation sent after it with the earlier trip_version is a correct
        # 409 VERSION_CONFLICT (the old intermittent failure). Running jobs do not
        # re-check the version, so the two jobs still overlap in the queue.
        recommendation=u.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'load-recommend'})
        assert recommendation.status_code==202,recommendation.text
        document=u.client.post(base+'/documents',files=[('files',('synthetic.txt',b'Synthetic reservation only','text/plain'))],headers={'Idempotency-Key':'load-mail'})
        assert document.status_code==202,document.text
        return row,document.json(),recommendation.json(),(time.perf_counter()-started)*1000
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=5) as pool:receipts=list(pool.map(submit,inputs))
    final=[]
    for row,mail,rec,latency in receipts:
        for receipt in (mail,rec):
            j=_job(row[0].client,receipt);assert j['state']=='succeeded',(j['state'],j.get('error_code'))
            final.append(j)
    elapsed=time.perf_counter()-start
    warm=[]
    def read(row):
        (u,trip,base),_,receipt,_=row
        result=[]
        for i in range(5):
            began=time.perf_counter();resp=u.client.get(base+'/recommendations/'+receipt['run_id']+'?page_size=12');result.append((time.perf_counter()-began)*1000)
            assert resp.status_code==200 and resp.json()['counts']['evaluated_count']==200
        return result
    with ThreadPoolExecutor(max_workers=5) as pool:
        for times in pool.map(read,receipts):warm.extend(times)
    with public.app.state.db.connect() as con:
        jobs=[dict(r) for r in con.execute("SELECT created_at,started_at,finished_at,state,attempt,operation FROM jobs WHERE operation IN ('documents','recommendations')")]
        costs=[dict(r) for r in con.execute('SELECT provider,state,count(*) AS calls,COALESCE(sum(actual_cost_micros),0) AS cost FROM usage_reservations GROUP BY provider,state')]
    def millis(a,b):return (datetime.fromisoformat(a)-datetime.fromisoformat(b)).total_seconds()*1000
    report={'environment':{'os':platform.system(),'machine':platform.machine(),'python':platform.python_version(),'storage':'temporary SQLite','clients':'FastAPI TestClient / in process','dispatcher':1},
        'synthetic_users':5,'overlapping_jobs':len(jobs),'elapsed_seconds':round(elapsed,3),
        'submit_pair_ms':percentiles([r[3] for r in receipts]),'warm_result_get_ms':percentiles(warm),
        'queue_wait_ms':percentiles([millis(j['started_at'],j['created_at']) for j in jobs]),
        'job_end_to_end_ms':percentiles([millis(j['finished_at'],j['created_at']) for j in jobs]),
        'memory_peak_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if platform.system()=='Darwin' else 1024),
        'errors':sum(j['state']!='succeeded' for j in jobs),'retries':sum(max(0,j['attempt']-1) for j in jobs),
        'fake_provider_calls':dict(Counter([name for name,*_ in public.calls],public_map=len(calls))),
        'real_provider_calls':0,'real_external_cost_usd':'0','synthetic_cost_ledger':costs,
        'render_cold_start':'unmeasured','network_latency':'not simulated; free provider cooldown clock accelerated',
        'provider_responses_reused':len(calls)==4}
    assert len(jobs)==10 and len(calls)==4
    if os.getenv('GOING_LOAD_REPORT'):
        Path(os.environ['GOING_LOAD_REPORT']).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
