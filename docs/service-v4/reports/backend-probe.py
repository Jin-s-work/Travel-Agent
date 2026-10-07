import os, sys, tempfile, json, threading
from pathlib import Path
os.environ['PYTHON_DOTENV_DISABLED']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import tests.conftest
from contextlib import contextmanager
from types import SimpleNamespace
from pytest import MonkeyPatch
from tests.test_foundation_api import service, _trip, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.discovery_synthetic import conditions, pack
from src.foundation.auth import digest

out=[]
with tempfile.TemporaryDirectory(prefix='going-audit-') as tmp:
 mp=MonkeyPatch()
 generator=service.__wrapped__(Path(tmp),mp)
 svc=next(generator)
 try:
  d=discovery.__wrapped__(svc)
  trip=_trip(d.client)
  p=import_pack(d)
  db=d.app.state.db
  with db.connect() as con:
   session=con.execute('SELECT id,user_id FROM sessions WHERE token_hash=?',(digest(d.admin.token),)).fetchone()
  actor=SimpleNamespace(id=session['user_id'],session_id=session['id'])
  original=db.connect
  counts=threading.local()
  @contextmanager
  def counted():
   with original() as con:
    active=getattr(counts,'active',None)
    if active is not None:
     active['checkouts']+=1
     def trace(sql):
      keyword=sql.split()[0].upper()
      active['statements']+=1
      active[keyword]=active.get(keyword,0)+1
     con.set_trace_callback(trace)
    yield con
  db.connect=counted
  def measure(label,fn):
   counts.active={'checkouts':0,'statements':0}
   try: result=fn()
   finally:
    out.append({'label':label,**counts.active})
    counts.active=None
   return result
  rec=d.app.state.recommendations
  original_execute=rec.execute
  rec.execute=lambda job,ctx:measure('job_execute_12',lambda:original_execute(job,ctx))
  measure('catalog_6',lambda:rec._catalog(actor,trip['id'],'tokyo',categories=['restaurant']))
  new=pack();new['version']='synthetic-second';
  for place in new['places']:
   place['external_id']+='-second';place['canonical_url']+='-second'
  import_pack(d,new)
  measure('catalog_12',lambda:rec._catalog(actor,trip['id'],'tokyo',categories=['restaurant']))
  path='/api/v2/trips/'+trip['id']
  assert d.client.patch(path+'/discovery-conditions',json={'expected_version':0,'conditions':conditions()}).status_code==200
  r=d.client.post(path+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1,'rating_filter':{'enabled':False}},headers={'Idempotency-Key':'audit-recommendation'})
  assert r.status_code==202,r.text
  assert _job(d.client,r.json())['state']=='succeeded'
  rid=r.json()['run_id']
  before=measure('saved_run_get_12',lambda:rec.get(actor,trip['id'],rid))
  with db.connect() as con:
   con.execute('INSERT INTO provider_policies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('unrelated-policy','unrelated-provider','unrelated-v1','active','{}','{}','synthetic audit','2026-01-01T00:00:00+00:00','2099-01-01T00:00:00+00:00',3600,3600,0,actor.id,'2026-01-01T00:00:00+00:00'))
  after=rec.get(actor,trip['id'],rid)
  out.append({'label':'unrelated_policy_invalidates_saved_run','before':before['data_status'],'before_result':before['result'] is not None,'after':after['data_status'],'after_result':after['result'] is not None,'reason_codes':after['reason_codes']})
  import asyncio
  from api import create_app
  from src.foundation.settings import Settings
  async def failover_probe():
   app=create_app(Settings(database_path=Path(tmp)/'failover.sqlite',documents_dir=Path(tmp)/'failover-docs',vectors_dir=Path(tmp)/'failover-vectors',environment='development',job_poll_seconds=.01))
   original_acquire=app.state.jobs.acquire_dispatcher
   attempts=[0]
   def acquire(owner):
    attempts[0]+=1
    return False if attempts[0]==1 else original_acquire(owner)
   app.state.jobs.acquire_dispatcher=acquire
   async with app.router.lifespan_context(app):
    await asyncio.sleep(.15)
    maintenance=[t.get_coro().__qualname__ for t in asyncio.all_tasks() if 'review_maintenance' in t.get_coro().__qualname__]
    out.append({'label':'follower_promoted_without_maintenance','acquisition_calls':attempts[0],'dispatcher_alive':app.state.jobs.health()['dispatcher_alive'],'review_maintenance_tasks':len(maintenance)})
  asyncio.run(failover_probe())
  import sqlite3
  from src.storage.objects import SupabaseObjects
  raw=sqlite3.connect(':memory:');raw.row_factory=sqlite3.Row
  raw.executescript('CREATE TABLE trips(id TEXT,deleted_at TEXT); CREATE TABLE cloud_objects(key TEXT,trip_id TEXT,state TEXT,created_at TEXT); CREATE TABLE source_documents(opaque_path TEXT,deleted_at TEXT); CREATE TABLE cloud_import_objects(key TEXT,created_at TEXT);')
  tidy_trip='trip_'+'a'*32
  raw.execute('INSERT INTO trips VALUES(?,NULL)',(tidy_trip,))
  raw.executemany('INSERT INTO cloud_objects VALUES(?,?,?,?)',[(tidy_trip+'/'+format(i,'032x')+'.txt',tidy_trip,'orphan','2020-01-01') for i in range(101)])
  class SweepConnection:
   def execute(self,statement,params=()):
    return raw.execute(statement.replace("now()-interval '1 day'","'2026-10-06'"),params)
  class SweepDatabase:
   @contextmanager
   def connect(self):
    yield SweepConnection()
  objects=SupabaseObjects.__new__(SupabaseObjects)
  objects.db=SweepDatabase();objects.bucket='synthetic-private';deletes=[]
  objects.request=lambda method,path,**kwargs:deletes.extend(kwargs['json']['prefixes'])
  objects.reconcile();objects.reconcile()
  out.append({'label':'orphan_sweep_does_not_advance_101_objects_sqlite_compatible_query','delete_calls':len(deletes),'unique_keys_deleted':len(set(deletes)),'orphan_rows_remaining':raw.execute("SELECT count(*) FROM cloud_objects WHERE state='orphan'").fetchone()[0]})
  raw.close()
  out.append({'label':'paid_provider_calls','calls':len(d.calls)})
  print(json.dumps(out,ensure_ascii=False,indent=2))
  # Results go to stdout; redirect to a new file to preserve the historical evidence.
 finally:
  try: next(generator)
  except StopIteration: pass
  mp.undo()
