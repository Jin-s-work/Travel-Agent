"""Synthetic lease and mocked-storage contracts; also run via postgres_plugin."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest

from src.foundation.db import Database
from src.foundation.repository import DomainError, Repository, utcnow
from src.foundation.settings import Settings
from src.operations.maintenance import Maintenance
from src.reliability.jobs import Jobs
from src.storage.objects import SupabaseObjects


@pytest.fixture
def fixture(tmp_path):
    db = Database(tmp_path/'service.sqlite3')
    with db.connect() as con:
        stamp = utcnow()
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    ('A','synthetic@example.test','fixture','A',stamp,stamp))
        if db.backend == 'sqlite':
            con.execute("CREATE TABLE cloud_objects(key TEXT PRIMARY KEY,trip_id TEXT REFERENCES trips(id),sha256 TEXT,byte_size INTEGER,state TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            con.execute("CREATE TABLE cloud_import_objects(key TEXT PRIMARY KEY,sha256 TEXT,byte_size INTEGER,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    repo = Repository(db)
    trip = repo.create_trip('A',{'title':'Synthetic','start_date':'2026-11-01','end_date':'2026-11-04'})
    state = SimpleNamespace(db=db,repo=repo,trip=trip,objects={},failed=set(),calls=[],post=None)
    def transport(request):
        state.calls.append(request.method)
        if request.url.path.endswith('/bucket/travel-private'):
            return httpx.Response(200,json={'id':'travel-private','public':False})
        if request.method == 'DELETE':
            keys = json.loads(request.content)['prefixes']
            if any(key in state.failed for key in keys):return httpx.Response(503)
            for key in keys:state.objects.pop(key,None)
            return httpx.Response(200,json=[])
        key = request.url.path.split('/object/travel-private/')[1]
        if state.post:state.post(key)
        state.objects[key] = request.content
        return httpx.Response(200,json={})
    settings = Settings(supabase_url='https://fixture.supabase.co',supabase_secret_key='sb_secret_fixture')
    state.storage = SupabaseObjects(settings,db,transport=httpx.MockTransport(transport))
    yield state
    state.storage.close()
    db.close()


def supervisor(fixture, owner, calls, *, interval=30):
    def purge(*,guard):
        guard();calls.append('purge')
    def cleanup(*,guard):
        guard();calls.append('cleanup');return {'items':[]}
    app = SimpleNamespace(state=SimpleNamespace(db=fixture.db,jobs=Jobs(fixture.db),
        dispatcher=SimpleNamespace(owner=owner),documents=SimpleNamespace(objects=None),
        reviews=SimpleNamespace(purge=purge,cleanup_remote=cleanup)))
    maintenance = Maintenance(app,interval=interval)
    maintenance._product = lambda: calls.append('product')
    return maintenance


def test_promoted_follower_runs_and_old_owner_stops(fixture):
    first_calls, second_calls = [], []
    first = supervisor(fixture,'first',first_calls)
    second = supervisor(fixture,'second',second_calls)
    assert first.jobs.acquire_dispatcher('first')
    assert second.run_once() is False and second_calls == []
    assert first.run_once()
    first.jobs.release_dispatcher('first')
    assert second.jobs.acquire_dispatcher('second')
    assert not second.snapshot()['healthy']
    assert second.run_once()
    assert first.run_once() is False
    assert first_calls == second_calls == ['product','purge','cleanup']
    status = first.snapshot()
    assert status['owner'] == status['current_owner'] == 'second'
    assert status['healthy'] and status['last_succeeded_at']


def test_loss_mid_cycle_fences_remaining_effects_and_status(fixture):
    calls = []
    first = supervisor(fixture,'first',calls)
    first.jobs.acquire_dispatcher('first')
    def transfer():
        calls.append('product')
        first.jobs.release_dispatcher('first')
        first.jobs.acquire_dispatcher('second')
    first._product = transfer
    assert first.run_once() is False
    assert calls == ['product']
    assert first.snapshot()['healthy'] is False
    with pytest.raises(DomainError,match='권한'):
        with fixture.db.connect() as con:
            con.execute('BEGIN IMMEDIATE');first.guard(con=con)


def test_supervisor_task_observes_promotion_without_restart(fixture):
    async def scenario():
        calls=[]
        maintenance=supervisor(fixture,'second',calls,interval=.01)
        maintenance.jobs.acquire_dispatcher('first')
        await maintenance.start()
        try:
            await asyncio.sleep(.025)
            assert calls==[]
            maintenance.jobs.release_dispatcher('first')
            maintenance.jobs.acquire_dispatcher('second')
            for _ in range(100):
                if calls:break
                await asyncio.sleep(.01)
            assert calls
        finally:await maintenance.stop()
        with pytest.raises(DomainError):maintenance.guard()
    asyncio.run(scenario())


def test_failed_or_delayed_maintenance_is_observable_without_exception_text(fixture):
    maintenance=supervisor(fixture,'first',[])
    maintenance.jobs.acquire_dispatcher('first')
    def fail():raise RuntimeError('secret original should never be included')
    maintenance._product=fail
    assert maintenance.run_once() is False
    status=maintenance.snapshot()
    assert not status['healthy'] and status['error_code']=='MAINTENANCE_FAILED'
    assert status['last_failed_at'] and 'secret' not in json.dumps(status)
    with fixture.db.connect() as con:
        con.execute("UPDATE maintenance_status SET state='succeeded',last_started_at=?",((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
    assert not maintenance.snapshot()['healthy']


def seed(fixture, count, kind):
    keys=[]
    stamp=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    with fixture.db.connect() as con:
        for i in range(count):
            key=fixture.trip['id']+'/'+f'{i+(1000 if kind=="import" else 0):032x}'+'.txt'
            if kind=='import':
                con.execute('INSERT INTO cloud_import_objects(key,sha256,byte_size,created_at) VALUES(?,?,?,?)',(key,'synthetic',1,stamp))
            else:
                con.execute("INSERT INTO cloud_objects(key,trip_id,sha256,byte_size,state,created_at) VALUES(?,?,?,?,'deleted',?)",(key,fixture.trip['id'],'synthetic',1,stamp))
            fixture.objects[key]=b'x';keys.append(key)
    return keys


@pytest.mark.parametrize('kind',['object','import'])
def test_250_keys_progress_past_deleted_prefix_and_a_failed_key(fixture,kind):
    keys=seed(fixture,250,kind)
    fixture.failed.add(keys[0])
    now=datetime.now(timezone.utc)
    outcomes=[fixture.storage.reconcile(now=now) for _ in range(3)]
    assert sum(row['attempted'] for row in outcomes)==250
    assert sum(row['failed'] for row in outcomes)==1
    assert set(fixture.objects)=={keys[0]}
    later=now+timedelta(days=1,seconds=1)
    for _ in range(3):fixture.storage.reconcile(now=later)
    with fixture.db.connect() as con:
        assert con.execute('SELECT count(*) FROM storage_deletion_receipts WHERE completed_at IS NOT NULL').fetchone()[0]==249
    calls=len(fixture.calls)
    fixture.storage.reconcile(now=later)
    assert len(fixture.calls)==calls  # completed keys do not monopolize later cycles
    fixture.failed.clear()
    fixture.storage.reconcile(now=later+timedelta(days=1))
    fixture.storage.reconcile(now=later+timedelta(days=2,seconds=1))
    with fixture.db.connect() as con:
        assert con.execute('SELECT count(*) FROM storage_deletion_receipts WHERE completed_at IS NOT NULL').fetchone()[0]==250


def test_late_post_after_completed_sweep_cannot_register_and_rearms_tombstone(fixture):
    def late(key):
        now=datetime.now(timezone.utc)
        with fixture.db.connect() as con:
            con.execute('UPDATE cloud_objects SET created_at=? WHERE key=?',((now-timedelta(days=2)).isoformat(),key))
        fixture.storage.reconcile(now=now)
        fixture.storage.reconcile(now=now+timedelta(days=1,seconds=1))
        with fixture.db.connect() as con:
            assert con.execute('SELECT completed_at FROM storage_deletion_receipts WHERE key=?',(key,)).fetchone()[0]
    fixture.post=late
    with pytest.raises(DomainError) as error:
        fixture.storage.save(fixture.repo,'A',fixture.trip['id'],'late.txt',b'synthetic')
    assert error.value.code=='STORAGE_UPLOAD_EXPIRED'
    assert fixture.objects=={}
    with fixture.db.connect() as con:
        assert con.execute('SELECT count(*) FROM source_documents').fetchone()[0]==0
        row=con.execute('SELECT * FROM storage_deletion_receipts').fetchone()
        assert row['completed_at'] is None and row['confirmations']==1


def test_storage_loss_after_delete_fences_ack_and_next_key(fixture):
    seed(fixture,2,'object')
    calls=[]
    maintenance=supervisor(fixture,'first',calls)
    maintenance.jobs.acquire_dispatcher('first')
    original=fixture.storage.request
    def lose(method,path,**kwargs):
        result=original(method,path,**kwargs)
        if method=='DELETE':
            maintenance.jobs.release_dispatcher('first')
            maintenance.jobs.acquire_dispatcher('second')
        return result
    fixture.storage.request=lose
    with pytest.raises(DomainError) as error:fixture.storage.reconcile(guard=maintenance.guard)
    assert error.value.code=='LEASE_LOST'
    assert len(fixture.objects)==1
    with fixture.db.connect() as con:
        assert con.execute('SELECT sum(confirmations) FROM storage_deletion_receipts').fetchone()[0]==0


def test_repeated_immediate_remove_does_not_fake_delayed_confirmation(fixture):
    key=seed(fixture,1,'object')[0]
    fixture.storage.remove(key);fixture.storage.remove(key)
    with fixture.db.connect() as con:
        row=con.execute('SELECT * FROM storage_deletion_receipts').fetchone()
        assert row['completed_at'] is None and row['confirmations']==1


def test_failed_storage_retry_stays_degraded_between_sweeps(fixture):
    key=seed(fixture,1,'object')[0];fixture.failed.add(key)
    maintenance=supervisor(fixture,'first',[])
    maintenance.app.state.documents.objects=fixture.storage
    maintenance.jobs.acquire_dispatcher('first')
    assert maintenance.run_once() is False
    assert maintenance.run_once() is False  # this review cycle skips the sweep
    status=maintenance.snapshot()
    assert not status['healthy'] and status['error_code']=='STORAGE_CLEANUP_INCOMPLETE'


def test_generation_delete_losing_lease_cannot_ack_completion(fixture):
    from src.reliability.generations import GenerationManager
    from tests.test_reliability_generations import make_job
    with fixture.db.connect() as con:
        con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES(?,?,?,?,?,?,?)',
                    ('session-A','A','synthetic-hash','synthetic-csrf','2099-01-01T00:00:00+00:00',0,utcnow()))
    job=make_job(fixture.db,fixture.trip['id'])
    with fixture.db.connect() as con:
        con.execute("INSERT INTO trip_index_generations(id,trip_id,collection_name,state,base_trip_version,source_manifest,embedding_model,embedding_dimension,job_id,fencing_token,created_at) VALUES(?,?,?,'retired',1,'{}','synthetic',1,?,1,?)",
                    ('generation-fixture',fixture.trip['id'],'fixture-collection',job,utcnow()))
    first=supervisor(fixture,'first',[]);first.jobs.acquire_dispatcher('first')
    deleted=[]
    def remove(name):
        deleted.append(name)
        first.jobs.release_dispatcher('first');first.jobs.acquire_dispatcher('second')
    manager=GenerationManager(fixture.db,fixture.repo,client=SimpleNamespace(delete_collection=remove))
    with pytest.raises(DomainError) as error:manager.cleanup(guard=first.guard)
    assert error.value.code=='LEASE_LOST' and deleted==['fixture-collection']
    with fixture.db.connect() as con:
        assert con.execute('SELECT state FROM trip_index_generations').fetchone()[0]=='deleting'
    second=supervisor(fixture,'second',[])
    assert manager.cleanup(guard=second.guard)==['generation-fixture']


def test_unused_budget_release_guard_failure_rolls_back(fixture):
    from src.reliability.budget import Budget,BudgetPolicy,CallContext
    budget=Budget(fixture.db,BudgetPolicy.for_tests())
    context=CallContext('A',fixture.trip['id'],fixture.trip['id'])
    reserved=budget.reserve(context,provider='fake',sku='extract',operation='extract',call_key='unused-cleanup',
                            estimated_units={'calls':1},request_hash='synthetic')
    maintenance=supervisor(fixture,'former-owner',[])
    def lost(*,con):
        # A failing callback and its caller must share the same rollback scope.
        con.execute("UPDATE trips SET title='must roll back' WHERE id=?",(fixture.trip['id'],))
        maintenance.guard(con=con)
    with pytest.raises(DomainError) as error:budget.release_unsent(reserved['call_id'],guard=lost)
    assert error.value.code=='LEASE_LOST'
    with fixture.db.connect() as con:
        assert con.execute('SELECT title FROM trips WHERE id=?',(fixture.trip['id'],)).fetchone()[0]=='Synthetic'
        assert con.execute('SELECT state FROM usage_reservations WHERE call_id=?',(reserved['call_id'],)).fetchone()[0]=='reserved'
        assert con.execute("SELECT count(*) FROM usage_ledger WHERE call_id=? AND entry_kind='released'",(reserved['call_id'],)).fetchone()[0]==0
    budget.release_unsent(reserved['call_id'])
    with fixture.db.connect() as con:
        assert con.execute('SELECT state FROM usage_reservations WHERE call_id=?',(reserved['call_id'],)).fetchone()[0]=='released'
