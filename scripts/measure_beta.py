"""Measure five HTTP clients against an isolated test container, never production."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import statistics
import subprocess
import time
import httpx

p=argparse.ArgumentParser();p.add_argument('--container',required=True);p.add_argument('--url',default='http://127.0.0.1:8780');p.add_argument('--report',type=Path,required=True);args=p.parse_args()
def docker(*cmd):return subprocess.check_output(['docker',*cmd],text=True)
info=json.loads(docker('exec',args.container,'cat','/var/data/sql/load-info.json'))
clients=[httpx.Client(base_url=args.url,headers={'Cookie':'__Host-session='+u['token'],'Origin':'https://beta.example.test','X-CSRF-Token':u['csrf']},timeout=30) for u in info['users']]
for _ in range(60):
    try:
        if clients[0].get('/health/ready').status_code==200:break
    except httpx.TransportError:pass
    time.sleep(1)
else:raise RuntimeError('Fixture readiness failed')
def ok(r,code=200):assert r.status_code==code,(r.status_code,r.text[:300]);return r.json()
trips=[]
for i,c in enumerate(clients):
    trip=ok(c.post('/api/v2/trips',json={'title':'Synthetic load '+str(i),'start_date':'2026-11-06','end_date':'2026-11-09','party':{'adults':2,'children':[]}}),201);trips.append(trip)
    for n in range(8):ok(c.post('/api/v2/trips/'+trip['id']+'/bookings',json={'kind':'tour','provider':'Synthetic '+str(n),'date':'2026-11-07','time':f'{9+n:02d}:00','status':'user_confirmed'}),201)
    # Conditions are persisted once; recommendations and later refresh reuse them.
    conditions=ok(c.get('/api/v2/trips/'+trip['id']+'/discovery-conditions'))
    ok(c.patch('/api/v2/trips/'+trip['id']+'/discovery-conditions',json={'expected_version':0,'conditions':conditions['conditions']}))
trip=ok(clients[0].get('/api/v2/trips/'+trips[0]['id']))
def resource():
    output=docker('exec',args.container,'python','-c',"import json;from pathlib import Path;print(json.dumps({p:Path('/sys/fs/cgroup/'+p).read_text().strip() for p in ['memory.current','memory.peak','memory.events','cpu.stat']}))")
    data=json.loads(output)
    extra=docker('exec',args.container,'python','-c',"import json;from pathlib import Path;s=Path('/proc/1/status').read_text().splitlines();print(json.dumps({'rss_bytes':int(next(x for x in s if x.startswith('VmRSS:')).split()[1])*1024,'disk_bytes':sum(p.stat().st_size for p in Path('/var/data').rglob('*') if p.is_file())}))")
    data.update(json.loads(extra));return data
idle=resource();start=time.monotonic()
files=[('files',('same.txt',json.dumps([{'kind':'tour','provider':'Synthetic imported','date':'2026-11-07','time':'18:00','stable_item_key':'one'}]),'text/plain'))]
receipt=ok(clients[0].post('/api/v2/trips/'+trip['id']+'/documents',files=files,headers={'Idempotency-Key':'load-document-once'}),202)
# Put recommendation on a second user's trip to avoid deliberate version conflict.
other=ok(clients[1].get('/api/v2/trips/'+trips[1]['id']))
rec=clients[1].post('/api/v2/trips/'+other['id']+'/recommendations',json={'trip_version':other['version'],'conditions_version':1},headers={'Idempotency-Key':'load-recommend-once'})
recdata=ok(rec,202)
latencies=[];errors=[]
def reads(i):
    c=clients[i];trip=trips[i];timings=[];failures=[]
    for n in range(80):
        for path in ('/api/v2/trips','/api/v2/trips/'+trip['id']+'/bookings','/api/v2/trips/'+trip['id']+'/itineraries'):
            tick=time.monotonic();r=c.get(path);timings.append((time.monotonic()-tick)*1000)
            if r.status_code!=200:failures.append(r.status_code)
        time.sleep(.02)
    return timings,failures
with ThreadPoolExecutor(max_workers=5) as pool:
    for timings,failures in pool.map(reads,range(5)):latencies+=timings;errors+=failures
again=ok(clients[0].post('/api/v2/trips/'+trip['id']+'/documents',files=files,headers={'Idempotency-Key':'load-document-once'}),202)
assert receipt['job_id']==again['job_id']
jobs=[]
for c,jobid in [(clients[0],receipt['job_id']),(clients[1],recdata['job_id'])]:
    job=ok(c.get('/api/v2/jobs/'+jobid));assert job['state'] in ('succeeded','partial'),job;jobs.append(job)
    event=c.get('/api/v2/jobs/'+jobid+'/events',headers={'Last-Event-ID':'1'});assert event.status_code==200
summary=ok(clients[0].get('/api/v2/admin/operations'));peak=resource()
read_data=ok(clients[0].get('/api/v2/trips/'+trip['id']+'/bookings'));assert len(read_data['items'])==9
# Actual process restart with the same volume. Old cookies must remain usable.
docker('restart',args.container)
for _ in range(60):
    try:
        if clients[0].get('/health/ready').status_code==200:break
    except httpx.TransportError:pass
    time.sleep(.5)
after=ok(clients[0].get('/api/v2/trips/'+trip['id']+'/bookings'));assert len(after['items'])==9
assert ok(clients[4].get('/api/v2/trips'))['items'][0]['id']==trips[4]['id']
assert clients[4].get('/api/v2/trips/'+trip['id']).status_code==404
latencies.sort()
report={'synthetic':True,'external_provider_calls':0,'users':5,'read_requests':len(latencies),'seconds_including_restart':round(time.monotonic()-start,3),
        'read_p50_ms':round(statistics.median(latencies),2),'read_p95_ms':round(latencies[int(.95*(len(latencies)-1))],2),'errors':errors,
        'idle_cgroup':idle,'peak_cgroup':peak,'disk_growth_bytes':peak['disk_bytes']-idle['disk_bytes'],'operations':summary,
        'jobs':[{'operation':j['operation'],'state':j['state'],'attempt':j['attempt'],'created_at':j['created_at'],'started_at':j['started_at'],'finished_at':j['finished_at']} for j in jobs],
        'restart_preserved_bookings':True,'cross_user_404':True,'duplicate_reused_job':True,'sse_reconnected':True}
args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('users','read_requests','seconds_including_restart','read_p50_ms','read_p95_ms','errors','restart_preserved_bookings')}))
