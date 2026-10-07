"""Real disposable PostgreSQL/pgvector, synthetic Supabase HTTP, zero paid calls."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4
import pytest
import httpx
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from src.storage.postgres import PostgresDatabase
from src.foundation.settings import Settings
from src.reliability.budget import BudgetPolicy
from tests.test_foundation_api import _trip,_upload,_job,_fact

DSN=os.getenv('TRAVEL_TEST_POSTGRES_DSN','')
pytestmark=pytest.mark.skipif(not DSN,reason='Requires disposable PostgreSQL/pgvector')

class FakeStorage:
    def __init__(self): self.objects={}; self.public=False; self.failed=False; self.calls=[]
    def __call__(self,request):
        self.calls.append((request.method,request.url.path))
        if self.failed:return httpx.Response(503)
        path=request.url.path.split('/storage/v1/',1)[1]
        if path=='bucket/travel-private':return httpx.Response(200,json={'id':'travel-private','public':self.public})
        if request.method=='DELETE':
            for key in json.loads(request.content)['prefixes']:self.objects.pop(key,None)
            return httpx.Response(200,json=[])
        key=path.split('object/travel-private/',1)[1]
        if request.method=='POST':
            if key in self.objects:return httpx.Response(409)
            self.objects[key]=request.content
            return httpx.Response(200,json={'Key':key})
        return httpx.Response(200,content=self.objects[key]) if key in self.objects else httpx.Response(404)

@pytest.fixture
def cloud(tmp_path,monkeypatch):
    from urllib.parse import urlsplit
    assert urlsplit(DSN).hostname in {'127.0.0.1','localhost'}
    import api
    schema='travel_test_'+uuid4().hex
    storage=FakeStorage();calls=[];databases=[]
    settings=Settings(storage_backend='supabase',database_url=DSN,supabase_url='https://fixture.supabase.co',supabase_secret_key='sb_secret_fixture',
        database_path=tmp_path/'ephemeral/sql/unused.sqlite3',documents_dir=tmp_path/'ephemeral/docs',vectors_dir=tmp_path/'ephemeral/vectors',
        environment='development',public_base_url='http://testserver',oidc_client_id='test',oidc_client_secret='test',session_secret='s'*40,job_poll_seconds=.02)
    def database(path,**kwargs):
        db=PostgresDatabase(DSN,path,schema=schema);databases.append(db);return db
    monkeypatch.setattr(api,'Database',database)
    def parse(raw): calls.append('parse');return [_fact(i,time=f'{8+i:02}:00') for i in range(8)]
    def embed(texts):calls.append('embed');return [[.1]*8 for _ in texts]
    def create():return api.create_app(settings,parser=parse,embedder=embed,answer_generator=lambda q,h:'synthetic',
        budget_policy=BudgetPolicy.for_tests(),storage_transport=httpx.MockTransport(storage))
    @contextmanager
    def running():
        app=create()
        with TestClient(app) as client:yield app,client
    def login(app,client,name='A'):
        invite=app.state.auth.invite(name+'@example.test')
        token=app.state.auth.complete_identity({'iss':'https://fixture.test','sub':name,'email':name+'@example.test','email_verified':True},invite)
        client.cookies.set(settings.cookie_name,token)
        session=client.get('/api/v2/session').json();client.headers.update({'Origin':'http://testserver','X-CSRF-Token':session['csrf_token']})
        return session,token
    yield SimpleNamespace(running=running,create=create,storage=storage,calls=calls,login=login,settings=settings,schema=schema)
    for db in databases:db.close()
    with psycopg.connect(DSN,autocommit=True) as con:con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_ephemeral_loss_keeps_originals_vectors_sessions_jobs_and_bookings(cloud):
    with cloud.running() as (app,c):
        session,token=cloud.login(app,c);trip=_trip(c)
        upload=_upload(c,trip['id']);job=_job(c,upload)
        assert job['state']=='succeeded',job
        docs=app.state.repo.list_documents(session['user']['id'],trip['id'])
        assert len(docs)==1 and docs[0]['opaque_path'].startswith('supabase:')
        assert app.state.db.backend=='postgres'
        assert len(app.state.repo.list_bookings(session['user']['id'],trip['id']))==8
        with app.state.generations.reader(session['user']['id'],trip['id']) as reader:assert reader.query([.1]*8)
        path=f"/api/v2/trips/{trip['id']}/documents/{docs[0]['id']}/content"
        assert c.get(path).content==b'Synthetic reservation'
        csrf=session['csrf_token'];owner=session['user']['id']
    shutil.rmtree(cloud.settings.database_path.parents[1])
    with cloud.running() as (app,c):
        c.cookies.set(cloud.settings.cookie_name,token);c.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        assert c.get('/api/v2/session').json()['authenticated']
        assert len(c.get(f"/api/v2/trips/{trip['id']}/bookings").json()['items'])==8
        assert c.get(path).content==b'Synthetic reservation'
        assert c.get('/api/v2/jobs/'+upload['job_id']).json()['state']=='succeeded'
        with app.state.generations.reader(owner,trip['id']) as reader:assert reader.query([.1]*8)
        assert cloud.calls==['parse','embed']
        assert not cloud.settings.database_path.exists()
        other=TestClient(app)
        try:
            cloud.login(app,other,'B')
            assert other.get(path).status_code==404
        finally: other.close()


def test_storage_outage_and_corruption_do_not_remove_sql(cloud):
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);trip=_trip(c);_upload(c,trip['id'])
        doc=app.state.repo.list_documents(session['user']['id'],trip['id'])[0]
        path=f"/api/v2/trips/{trip['id']}/documents/{doc['id']}/content"
        key=doc['opaque_path'][9:];cloud.storage.objects[key]=b'corrupt'
        assert c.get(path).status_code==503
        cloud.storage.failed=True
        assert c.get(path).status_code==503
        assert c.get(f"/api/v2/trips/{trip['id']}/bookings").status_code==200
        assert c.get('/health/ready').status_code==200


def test_public_bucket_rejected_and_storage_outage_boots_saved_reads(cloud):
    cloud.storage.public=True
    with pytest.raises(ValueError,match='private'):
        with cloud.running():pass
    cloud.storage.public=False;cloud.storage.failed=True
    with cloud.running() as (app,c):
        cloud.login(app,c);assert _trip(c)
        assert c.get('/health/ready').status_code==200


def test_same_filename_and_idempotent_upload_are_scoped(cloud):
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);a=_trip(c);b=_trip(c)
        _upload(c,a['id']);_upload(c,b['id']);_upload(c,a['id'])
        assert len(cloud.storage.objects)==2
        assert cloud.calls==['parse','embed','parse','embed']
        assert len(app.state.repo.list_documents(session['user']['id'],a['id']))==1


def test_pg_receipt_replay_survives_filesystem_loss(cloud):
    from src.reliability.providers import ProviderGateway,ProviderResult
    from src.reliability.budget import CallContext
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);trip=_trip(c);owner=session['user']['id']
        context=CallContext(owner_id=owner,scope_id=trip['id'],trip_id=trip['id'],actor_id=owner)
        args=dict(provider='fake',sku='extract',request_hash='stable')
        result=app.state.gateway.run(context,'extract','test-replay',{'calls':1},lambda:ProviderResult({'ok':1},{'calls':1}),**args)
        assert result=={'ok':1}
        shutil.rmtree(cloud.settings.artifacts_dir)
        gateway=ProviderGateway(app.state.budget,cloud.settings.artifacts_dir)
        result=gateway.run(context,'extract','test-replay',{'calls':1},lambda:pytest.fail('external call repeated'),**args)
        assert result=={'ok':1}


def test_cloud_encrypted_backup_deletion_checkpoint_restore_and_import(cloud,tmp_path):
    from src.storage.transfer import cloud_snapshot,import_sqlite,inspect_source
    from src.operations.backup import write_checkpoint,restore_archive
    from src.foundation.db import Database
    from src.storage.objects import SupabaseObjects
    from src.foundation.repository import Repository,DomainError
    key=os.urandom(32);archive=tmp_path/'snapshot.enc';checkpoint=tmp_path/'latest.enc'
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);owner=session['user']['id'];keep=_trip(c);doomed=_trip(c)
        _upload(c,keep['id']);_upload(c,doomed['id'],key='doomed')
        cloud_snapshot(cloud.settings,app.state.db,app.state.documents.objects,archive,key)
        assert c.delete('/api/v2/trips/'+doomed['id']).status_code==202
        write_checkpoint(app.state.db,checkpoint,key)
    restored=tmp_path/'restored'
    result=restore_archive(archive,checkpoint,restored,key)
    assert result['state']=='restored_closed_for_validation'
    local=Database(restored/'database.sqlite3')
    assert len(Repository(local).list_bookings(owner,keep['id']))==8
    with pytest.raises(DomainError):Repository(local).get_trip(owner,doomed['id'])
    before={p:p.read_bytes() for p in (restored/'documents').rglob('*') if p.is_file()}
    report,_=inspect_source(local.path,restored/'documents')
    assert report['owned_documents']==1
    target_schema='travel_test_'+uuid4().hex
    target=PostgresDatabase(DSN,tmp_path/'import-cache',schema=target_schema)
    obj=__import__('src.storage.objects',fromlist=['SupabaseObjects']).SupabaseObjects(cloud.settings,target,transport=httpx.MockTransport(cloud.storage))
    try:
        assert import_sqlite(local.path,restored/'documents',target,obj)['state']=='dry_run'
        imported=import_sqlite(local.path,restored/'documents',target,obj,apply=True)
        assert imported['state']=='imported_read_only'
        assert len(Repository(target).list_bookings(owner,keep['id']))==8
        with pytest.raises(DomainError):Repository(target).get_trip(owner,doomed['id'])
        with target.connect() as con:
            assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
            assert con.execute('SELECT mode FROM operations_controls').fetchone()[0]=='read_only'
        assert all(p.read_bytes()==data for p,data in before.items())
    finally:
        obj.close();target.close()
        with psycopg.connect(DSN,autocommit=True) as con:con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(target_schema)))


def test_pg_last_budget_two_requests_only_one_reservation(cloud):
    from concurrent.futures import ThreadPoolExecutor
    from copy import deepcopy
    from src.reliability.budget import Budget,BudgetPolicy,CallContext
    from src.foundation.repository import DomainError
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);trip=_trip(c);owner=session['user']['id']
        config=deepcopy(BudgetPolicy.for_tests().config)
        config['prices']['fake/extract']['rates_per_million']={'calls':'1000000'}
        config['limits']['USD']={key:1000000 for key in ('user_daily','user_monthly','global_daily','global_monthly')}
        budget=Budget(app.state.db,BudgetPolicy(config));context=CallContext(owner_id=owner,scope_id=trip['id'],trip_id=trip['id'],actor_id=owner)
        def reserve(number):
            try:budget.reserve(context,provider='fake',sku='extract',operation='extract',call_key=str(number),estimated_units={'calls':1},request_hash=str(number));return 'ok'
            except DomainError as exc:return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
        assert results.count('ok')==1,results
        with app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]==1


def test_pg_schema_is_denied_to_browser_role(cloud):
    with cloud.running() as (app,c):
        cloud.login(app,c);_trip(c)
        with psycopg.connect(DSN,autocommit=True) as con:
            con.execute("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='travel_test_anon') THEN CREATE ROLE travel_test_anon; END IF; END $$")
            con.execute('SET ROLE travel_test_anon')
            with pytest.raises(psycopg.errors.InsufficientPrivilege):con.execute(sql.SQL('SELECT * FROM {}.trips').format(sql.Identifier(cloud.schema)))


def test_shared_generation_pin_blocks_other_instance_reclaim(cloud):
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);trip=_trip(c);_upload(c,trip['id'])
        manager=app.state.generations
        with manager.reader(session['user']['id'],trip['id']) as reader:
            ident=reader.generation['id']
            with app.state.db.connect() as con:
                con.execute('BEGIN IMMEDIATE');assert not app.state.db.can_reclaim(con,ident)
        with app.state.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');assert app.state.db.can_reclaim(con,ident)


def test_upload_finishing_after_deletion_cannot_register_original(cloud):
    from src.foundation.repository import DomainError
    with cloud.running() as (app,c):
        session,_=cloud.login(app,c);trip=_trip(c)
        objects=app.state.documents.objects;original=objects.request
        def late(method,path,**kwargs):
            result=original(method,path,**kwargs)
            if method=='POST':app.state.repo.delete_trip(session['user']['id'],trip['id'])
            return result
        objects.request=late
        with pytest.raises(DomainError):app.state.documents.save(session['user']['id'],trip['id'],'late.txt',b'late synthetic data')
        with app.state.db.connect() as con:
            assert con.execute('SELECT count(*) FROM source_documents').fetchone()[0]==0
            assert con.execute('SELECT count(*) FROM cloud_objects').fetchone()[0]==1
            con.execute("UPDATE cloud_objects SET created_at=now()-interval '2 days'")
        objects.reconcile();assert cloud.storage.objects=={}


def test_postgres_sigkill_recovers_receipt_and_checkpoint(cloud,tmp_path):
    import subprocess,sys,time
    from src.reliability.jobs import Jobs
    from src.reliability.providers import ProviderGateway
    from src.reliability.budget import CallContext
    app=cloud.create();c=TestClient(app)
    try:
        session,_=cloud.login(app,c);trip=_trip(c);owner=session['user']['id']
        with app.state.db.connect() as con:sid=con.execute('SELECT id FROM sessions WHERE user_id=?',(owner,)).fetchone()[0]
        jobs=Jobs(app.state.db,lease_seconds=.5)
        queued=jobs.enqueue(owner,sid,'personal_trip',trip['id'],'documents',{},trip['version'],'crash-recovery')
        marker=tmp_path/'checkpoint-ready'
        script=r'''
import asyncio,os,sys,time
from pathlib import Path
from src.storage.postgres import PostgresDatabase
from src.reliability.jobs import Jobs
from src.reliability.dispatcher import Dispatcher
from src.reliability.budget import Budget,BudgetPolicy,CallContext
from src.reliability.providers import ProviderGateway,ProviderResult
db=PostgresDatabase(os.environ['TRAVEL_TEST_POSTGRES_DSN'],sys.argv[2],schema=sys.argv[1])
gateway=ProviderGateway(Budget(db,BudgetPolicy.for_tests()),Path(sys.argv[2]).parent/'receipts')
def handler(job,ctx):
    context=CallContext(owner_id=job['actor_id'],scope_id=job['trip_id'],trip_id=job['trip_id'],actor_id=job['actor_id'],job_id=job['id'])
    value=gateway.run(context,'extract','crash-receipt',{'calls':1},lambda:ProviderResult({'extracted':True},{'calls':1}),provider='fake',sku='extract',request_hash='crash',guard=ctx.guard)
    ctx.checkpoint({'extracted':value},stage='extracted',done=1,total=2)
    Path(sys.argv[3]).write_text('ready')
    time.sleep(30)
async def main():
    dispatcher=Dispatcher(Jobs(db,lease_seconds=.5),handler,poll_seconds=.02,heartbeat_seconds=.1)
    await dispatcher.start();await asyncio.sleep(60)
asyncio.run(main())
'''
        child=subprocess.Popen([sys.executable,'-c',script,cloud.schema,str(tmp_path/'child/cache'),str(marker)],cwd=Path(__file__).parents[1],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            until=time.monotonic()+12
            while not marker.exists() and child.poll() is None and time.monotonic()<until:time.sleep(.02)
            assert marker.exists(),child.stderr.read().decode() if child.poll() is not None else 'checkpoint timeout'
            child.kill();child.wait(timeout=5);assert child.returncode<0
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
            child.stderr.close()
        shutil.rmtree(tmp_path/'child');time.sleep(.6)
        recovered=jobs.claim('replacement-process')
        assert recovered['id']==queued['id'] and recovered['attempt']==2
        assert recovered['checkpoint']['extracted']=={'extracted':True}
        context=CallContext(owner_id=owner,scope_id=trip['id'],trip_id=trip['id'],actor_id=owner,job_id=queued['id'])
        value=app.state.gateway.run(context,'extract','crash-receipt',{'calls':1},lambda:pytest.fail('paid work repeated'),provider='fake',sku='extract',request_hash='crash')
        assert value=={'extracted':True}
        jobs.finish(recovered['id'],recovered['fencing_token'])
        with app.state.db.connect() as con:assert con.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]==1
    finally:c.close();app.state.db.close();app.state.documents.objects.close()
