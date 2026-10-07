"""SIGKILL recovery of the actual document handler with temporary real Chroma.

Only synthetic parsers/vectors are used. Children do not inherit any valid API
key, and every provider invocation leaves an fsynced synthetic counter record.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import signal
import subprocess
import sys

import pytest

from src.foundation.db import Database
from src.foundation.documents import DocumentService
from src.foundation.repository import Repository, utcnow, DomainError
from src.foundation.settings import Settings
from src.foundation.search import SearchContext, answer
from src.reliability.budget import Budget, BudgetPolicy
from src.reliability.providers import ProviderGateway
from src.reliability.jobs import Jobs
from src.reliability.dispatcher import JobContext
from src.reliability.generations import GenerationManager
from src.reliability.handlers import Operations


def construct(path, fault=None, *, policy=None, timeout_extract=False):
    path=Path(path)
    settings=Settings(database_path=path/'runtime.sqlite3',documents_dir=path/'documents',vectors_dir=path/'vectors')
    db=Database(settings.database_path)
    repo=Repository(db)
    def count(label):
        with (path/'calls.txt').open('a') as output:
            output.write(label+'\n'); output.flush(); os.fsync(output.fileno())
    def parse(raw):
        count('extract')
        if timeout_extract:
            raise TimeoutError('synthetic response lost')
        return [{'kind':'투어','provider':'Synthetic Museum','confirmation_number':'FAKE',
            'date':'2026-11-01','time':'09:00','status':'source_verified','stable_item_key':'same'}]
    def embed(texts):
        count('embed')
        return [[1.0,0.0,0.0] for _ in texts]
    documents=DocumentService(repo,settings,parser=parse,embedder=embed)
    jobs=Jobs(db)
    generations=GenerationManager(db,repo,settings.vectors_dir,jobs=jobs)
    budget=Budget(db,policy or BudgetPolicy.for_tests(rate='1',limit=100))
    gateway=ProviderGateway(budget,settings.artifacts_dir)
    def inject(point,job):
        if point==fault:
            os.kill(os.getpid(),signal.SIGKILL)
    operations=Operations(repo,documents,jobs,generations,gateway,fault_hook=inject)
    documents.operations=operations
    return SimpleNamespace(db=db,repo=repo,documents=documents,jobs=jobs,generations=generations,
        budget=budget,gateway=gateway,operations=operations)


def seed(env):
    now=utcnow()
    with env.db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES(?,?,?,?,?,?)',
            ('A','fake@example.invalid','fake','A',now,now))
        con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES(?,?,?,?,?,?,?)',
            ('session-A','A','hash','csrf','2099-01-01T00:00:00+00:00',0,now))
    trip=env.repo.create_trip('A',{'title':'Synthetic Tokyo','start_date':'2026-11-01','end_date':'2026-11-03'})
    # A preexisting manual reservation must remain readable throughout failure.
    env.repo.create_booking('A',trip['id'],{'provider':'Manual dinner','date':'2026-11-01','time':'19:00'})
    doc=env.documents.save('A',trip['id'],'synthetic.txt',b'synthetic museum booking')
    trip=env.repo.get_trip('A',trip['id'])
    payload={'accepted':[{'document_id':doc['id'],'filename':'synthetic.txt'}],'duplicates':[],'rejected':[]}
    job=env.jobs.enqueue('A','session-A','personal_trip',trip['id'],'documents',payload,trip['version'],'synthetic-request-key')
    return trip['id'],job['id'],doc['id']


def run_one(env,owner='test-worker'):
    job=env.jobs.claim(owner)
    assert job is not None
    ctx=JobContext(env.jobs,job,owner)
    result=env.operations(job,ctx)
    env.jobs.finish(job['id'],job['fencing_token'],state=result['state'],result=result['result'])
    return result


def expire_leases(env):
    with env.db.connect() as con:
        con.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE state='running'")
        con.execute("UPDATE trip_index_writers SET lease_expires_at='2000-01-01T00:00:00+00:00'")
        con.execute("UPDATE dispatcher_leases SET lease_expires_at='2000-01-01T00:00:00+00:00'")


@pytest.mark.parametrize('point',['after_extraction','after_embedding_batch','after_activation'])
def test_sigkill_handler_recovers_checkpoint_without_rebilling(tmp_path,point):
    initial=construct(tmp_path)
    trip_id,job_id,doc_id=seed(initial)
    script='''
import sys
from tests.test_reliability_runtime import construct,run_one
run_one(construct(sys.argv[1],sys.argv[2]),'killed-child')
'''
    child_env={**os.environ,'OPENAI_API_KEY':'test','SEED_ON_EMPTY':'0'}
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path),point],env=child_env,timeout=30,capture_output=True,text=True)
    assert result.returncode==-signal.SIGKILL, result.stderr
    with initial.db.connect() as con:
        job=con.execute('SELECT state,checkpoint_json FROM jobs WHERE id=?',(job_id,)).fetchone()
        assert job['state']=='running'
        assert json.loads(job['checkpoint_json'])['documents'][doc_id].get('generation_id')
    # SQL reservation read is independent of the worker and vector lifecycle.
    assert any(row['provider']=='Manual dinner' for row in initial.repo.list_bookings('A',trip_id))
    restarted=construct(tmp_path)
    expire_leases(restarted)
    result=run_one(restarted,'replacement-child')
    assert result['state'] in ('succeeded','partial')
    calls=(tmp_path/'calls.txt').read_text().splitlines()
    assert calls.count('extract')==1
    assert calls.count('embed')==1
    rows=restarted.repo.list_bookings('A',trip_id)
    assert len(rows)==2
    with restarted.db.connect() as con:
        assert con.execute('SELECT attempt FROM jobs WHERE id=?',(job_id,)).fetchone()[0]==2
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0]==2
        active=con.execute('SELECT active_index_id FROM trips WHERE id=?',(trip_id,)).fetchone()[0]
    assert active
    with restarted.generations.reader('A',trip_id) as reader:
        assert reader.query([1.0,0.0,0.0])[0]['metadata']['document_id']==doc_id


def test_budget_hardstop_preserves_sql_date_answer_without_provider(tmp_path):
    env=construct(tmp_path,policy=BudgetPolicy.for_tests(rate='1',limit=0))
    trip_id,job_id,_=seed(env)
    result=run_one(env)
    assert result['state']=='failed'
    assert result['result']['files'][0]['error_code']=='BUDGET_EXHAUSTED'
    assert not (tmp_path/'calls.txt').exists()
    trip=env.repo.get_trip('A',trip_id)
    response=answer(env.repo,env.documents,SearchContext('A',trip_id,trip['version'],'fake-request'),'1일차 예약 전체',[],generator=lambda *_:pytest.fail('SQL day answer cannot call model'))
    assert 'Manual dinner' in response['answer']
    assert '총 1개' in response['answer']


def test_unknown_provider_job_has_no_partial_activation_or_hidden_retry(tmp_path):
    env=construct(tmp_path,timeout_extract=True)
    trip_id,job_id,doc_id=seed(env)
    result=run_one(env)
    assert result['state']=='failed'
    assert result['result']['files'][0]['error_code']=='PROVIDER_OUTCOME_UNKNOWN'
    assert (tmp_path/'calls.txt').read_text().splitlines()==['extract']
    assert len(env.repo.list_bookings('A',trip_id))==1
    assert env.repo.get_document('A',trip_id,doc_id)['active_generation_id'] is None
    with env.db.connect() as con:
        row=con.execute('SELECT state,estimated_cost_micros FROM usage_reservations').fetchone()
        assert tuple(row)==('unknown',1)
        assert con.execute('SELECT COUNT(*) FROM trip_index_generations').fetchone()[0]==0


def test_legacy_process_is_rejected_before_db_or_file_mutation(tmp_path):
    from src.foundation.cli import import_legacy
    settings=Settings(database_path=tmp_path/'not-created.sqlite3',documents_dir=tmp_path/'not-created-documents')
    with pytest.raises(ValueError,match='retired'):
        import_legacy(settings,tmp_path/'missing-map',tmp_path/'missing-backup',process=True)
    assert not list(tmp_path.iterdir())
    service=DocumentService(None,settings)
    with pytest.raises(DomainError) as error:
        service.process('A','trip','job',[],[])
    assert error.value.code=='LEGACY_PROCESS_DISABLED'


def test_backup_restore_preserves_provider_receipts_and_checkpoint_files(tmp_path):
    from src.foundation.cli import backup,restore
    source=tmp_path/'source'; source.mkdir()
    env=construct(source)
    trip_id,_,_=seed(env)
    run_one(env)
    artifact_root=env.documents.settings.artifacts_dir
    original={str(p.relative_to(artifact_root)):p.read_bytes() for p in artifact_root.rglob('*.json')}
    assert len(original)>=4
    destination=tmp_path/'backup'
    backup(env.documents.settings,destination)
    restored=tmp_path/'restored'
    restore(destination,restored)
    recovered_root=restored/'private-job-artifacts'
    recovered={str(p.relative_to(recovered_root)):p.read_bytes() for p in recovered_root.rglob('*.json')}
    assert recovered==original


def test_document_deleted_during_extract_stops_embedding_and_new_artifacts(tmp_path):
    env=construct(tmp_path)
    trip_id,job_id,document_id=seed(env)
    original=env.documents.parser
    def delete_while_remote(raw):
        result=original(raw)
        env.repo.delete_document('A',trip_id,document_id)
        return result
    env.documents.parser=delete_while_remote
    result=run_one(env)
    assert result['state']=='cancelled'
    assert result['result']['files'][0]['error_code']=='DOCUMENT_DELETED'
    assert (tmp_path/'calls.txt').read_text().splitlines()==['extract']
    assert not list(env.documents.settings.artifacts_dir.rglob('*.json'))
    assert [row['provider'] for row in env.repo.list_bookings('A',trip_id)]==['Manual dinner']
    with env.db.connect() as con:
        assert tuple(con.execute('SELECT state,actual_cost_micros,result_ref FROM usage_reservations').fetchone())==('settled',1,None)
        assert con.execute('SELECT COUNT(*) FROM trip_index_generations').fetchone()[0]==0


def test_one_document_deleted_during_extract_does_not_cancel_sibling(tmp_path):
    env=construct(tmp_path)
    trip_id,job_id,deleted_id=seed(env)
    other=env.documents.save('A',trip_id,'other.txt',b'second synthetic booking')
    with env.db.connect() as con:
        payload=json.loads(con.execute('SELECT payload_json FROM jobs WHERE id=?',(job_id,)).fetchone()[0])
        payload['accepted'].append({'document_id':other['id'],'filename':'other.txt'})
        con.execute('UPDATE jobs SET payload_json=? WHERE id=?',(json.dumps(payload),job_id))
    original=env.documents.parser
    def delete_first(raw):
        result=original(raw)
        if 'second' not in raw:
            env.repo.delete_document('A',trip_id,deleted_id)
        return result
    env.documents.parser=delete_first
    result=run_one(env)
    assert result['state']=='partial'
    assert result['result']['files'][0]['state']=='cancelled'
    assert result['result']['files'][1]['state'] in {'succeeded','needs_review'}
    assert (tmp_path/'calls.txt').read_text().splitlines()==['extract','extract','embed']
    rows=env.repo.list_bookings('A',trip_id)
    assert len(rows)==2 and any(row['provider']=='Manual dinner' for row in rows)
    assert any(row['document_id']==other['id'] for row in rows)
    assert not list(env.documents.settings.artifacts_dir.rglob(deleted_id+'*'))
    with env.db.connect() as con:
        late=con.execute('SELECT state,actual_cost_micros,result_ref FROM usage_reservations WHERE call_key LIKE ?',('%'+deleted_id+':extract',)).fetchone()
        assert tuple(late)==('settled',1,None)


def test_document_artifact_guard_rejects_tombstone_with_live_trip_and_job(tmp_path):
    env=construct(tmp_path)
    trip_id,_,document_id=seed(env)
    job=env.jobs.claim('artifact-worker')
    ctx=JobContext(env.jobs,job,'artifact-worker')
    env.repo.delete_document('A',trip_id,document_id)
    with pytest.raises(DomainError) as error:
        env.operations.artifact(job,'unsafe',{'private':'mail result'},ctx,document_id=document_id)
    assert error.value.code=='DOCUMENT_DELETED'
    assert not list(env.documents.settings.artifacts_dir.rglob('*.json'))


def test_document_deleted_during_repair_embedding_has_no_late_artifact(tmp_path,monkeypatch):
    env=construct(tmp_path)
    trip_id,_,document_id=seed(env)
    run_one(env)
    before={str(p) for p in env.documents.settings.artifacts_dir.rglob('*.json')}
    import src.reliability.handlers as handlers
    monkeypatch.setattr(handlers,'EMBEDDING_MODEL','synthetic-new-model')
    original=env.documents.embedder
    def delete_during_embedding(texts):
        result=original(texts)
        env.repo.delete_document('A',trip_id,document_id)
        return result
    env.documents.embedder=delete_during_embedding
    trip=env.repo.get_trip('A',trip_id)
    job=env.jobs.enqueue('A','session-A','personal_trip',trip_id,'reindex',{},trip['version'],'repair-document-delete')
    result=run_one(env)
    assert result['state']=='succeeded'
    assert {str(p) for p in env.documents.settings.artifacts_dir.rglob('*.json')}==before
    assert [row['provider'] for row in env.repo.list_bookings('A',trip_id)]==['Manual dinner']
    with env.db.connect() as con:
        assert tuple(con.execute('SELECT state,actual_cost_micros,result_ref FROM usage_reservations WHERE job_id=?',(job['id'],)).fetchone())==('settled',1,None)
