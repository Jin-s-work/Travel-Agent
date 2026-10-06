"""Crash reconciliation touches only temporary, synthetic private storage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest

from src.foundation.db import Database
from src.foundation.documents import DocumentService
from src.foundation.repository import Repository, utcnow
from src.foundation.settings import Settings
from src.reliability.maintenance import reconcile_storage


def _settings(root):
    return Settings(database_path=root/'private.sqlite3', documents_dir=root/'documents', vectors_dir=root/'vectors')


def _setup(root):
    settings=_settings(root); db=Database(settings.database_path); repo=Repository(db)
    now=utcnow()
    with db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                    ('A','synthetic@example.invalid','test','A',now,now))
        con.execute('INSERT INTO dispatcher_leases(name,owner,lease_expires_at,heartbeat_at) VALUES (?,?,?,?)',
                    ('main','maintenance-test','2099-01-01T00:00:00+00:00',now))
    trip=repo.create_trip('A',{'title':'Synthetic','start_date':'2026-11-01','end_date':'2026-11-03'})
    service=DocumentService(repo,settings)
    return settings,db,repo,trip['id'],service


def _age(path, days=3):
    timestamp=(datetime.now(timezone.utc)-timedelta(days=days)).timestamp()
    os.utime(path,(timestamp,timestamp))


def _file(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('synthetic',encoding='utf-8')
    _age(path)
    return path


def test_only_dispatcher_leader_collects_server_named_aged_orphans(tmp_path):
    settings,db,repo,tid,service=_setup(tmp_path)
    doc=service.save('A',tid,'synthetic.txt',b'Synthetic original')
    referenced=Path(doc['opaque_path']);_age(referenced)
    orphan=_file(referenced.parent/(str(uuid4())+'.eml'))
    temporary=_file(referenced.parent/('.'+str(uuid4())+'.tmp'))
    recent=referenced.parent/(str(uuid4())+'.txt');recent.write_text('new upload')
    unrelated=_file(referenced.parent/'user-notes.txt')
    arbitrary_dir=_file(settings.documents_dir/'personal'/(str(uuid4())+'.txt'))
    outside=_file(tmp_path/'outside'/(str(uuid4())+'.txt'))
    symlink=referenced.parent/(str(uuid4())+'.txt');symlink.symlink_to(outside)
    rejected=reconcile_storage(db,settings,dispatcher_owner='other-worker')
    assert rejected['skipped']=='not_dispatcher_leader' and orphan.exists()
    result=reconcile_storage(db,settings,dispatcher_owner='maintenance-test')
    assert result['raw_removed']==1 and result['temporary_removed']==1
    assert result['protected_references']==1 and result['recent_skipped']==1
    assert referenced.exists() and not orphan.exists() and not temporary.exists()
    assert recent.exists() and unrelated.exists() and arbitrary_dir.exists() and outside.exists() and symlink.exists()
    # Even deleted SQL documents remain protected here; deletion jobs own their
    # explicit physical cleanup and permission checks.
    repo.delete_document('A',tid,doc['id'])
    assert reconcile_storage(db,settings,dispatcher_owner='maintenance-test')['protected_references']==1
    assert referenced.exists()


def test_aged_artifact_temporaries_wait_for_running_worker_and_json_always_remains(tmp_path):
    settings,db,repo,tid,service=_setup(tmp_path)
    job_id='job_'+uuid4().hex;scope=settings.artifacts_dir/tid
    partial=_file(scope/job_id/(str(uuid4())+'.tmp'))
    provider_partial=_file(scope/('call_'+uuid4().hex+'.'+uuid4().hex+'.tmp'))
    checkpoint=_file(scope/job_id/'doc_checkpoint.json')
    provider_json=_file(scope/('call_'+uuid4().hex+'.json'))
    unknown=_file(scope/'unexpected.tmp')
    values={'id':job_id,'actor_id':'A','session_id':'synthetic-session','scope_kind':'personal_trip',
            'scope_id':tid,'owner_id':'A','trip_id':tid,'operation':'documents','state':'running',
            'payload_json':'{}','payload_hash':'test','input_version':1,'max_attempts':3,'available_at':utcnow(),
            'deadline_at':'2099-01-01T00:00:00+00:00','lease_expires_at':'2099-01-01T00:00:00+00:00',
            'created_at':utcnow(),'updated_at':utcnow()}
    with db.connect() as con:
        con.execute('INSERT INTO jobs('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
    first=reconcile_storage(db,settings,dispatcher_owner='maintenance-test')
    assert first['active_writer_skipped']==2 and partial.exists() and provider_partial.exists()
    with db.connect() as con:
        con.execute("UPDATE jobs SET state='failed',lease_expires_at=NULL WHERE id=?",(job_id,))
    second=reconcile_storage(db,settings,dispatcher_owner='maintenance-test')
    assert second['temporary_removed']==2 and not partial.exists() and not provider_partial.exists()
    assert checkpoint.exists() and provider_json.exists() and unknown.exists()


def test_concurrent_same_content_upload_is_atomic_and_does_not_deadlock(tmp_path):
    settings,db,repo,tid,service=_setup(tmp_path)
    barrier=threading.Barrier(2)
    def save(_):
        barrier.wait(timeout=5)
        return service.save('A',tid,'same.txt',b'Same synthetic content')
    with ThreadPoolExecutor(2) as pool:
        saved=list(pool.map(save,range(2)))
    assert saved[0]['id']==saved[1]['id']
    assert sorted(doc['duplicate'] for doc in saved)==[False,True]
    files=list((settings.documents_dir/tid).iterdir())
    assert len(files)==1 and files[0].read_bytes()==b'Same synthetic content'
    assert files[0].stat().st_mode & 0o777 == 0o600


def test_pending_raw_rename_is_protected_from_reconciliation_by_sql_writer(tmp_path):
    settings,db,repo,tid,service=_setup(tmp_path)
    entered,release=threading.Event(),threading.Event()
    def pause(point,path):
        _age(path)
        entered.set()
        assert release.wait(5)
    service.raw_save_fault_hook=pause
    with ThreadPoolExecutor(2) as pool:
        save=pool.submit(service.save,'A',tid,'safe.txt',b'Pending synthetic upload')
        assert entered.wait(5)
        maintenance=pool.submit(reconcile_storage,db,settings,dispatcher_owner='maintenance-test')
        # Reconciliation waits for the writer; once it acquires SQL, the newly
        # committed document reference protects even an artificially aged file.
        release.set()
        doc=save.result(timeout=5)
        result=maintenance.result(timeout=5)
    assert result['raw_removed']==0 and Path(doc['opaque_path']).exists()


def test_sigkill_after_raw_rename_before_sql_preserves_old_active_then_collects_orphan(tmp_path):
    settings,db,repo,tid,service=_setup(tmp_path)
    original=service.save('A',tid,'old.txt',b'Old synthetic booking')
    generation=repo.create_generation('A',tid,original['id'])
    repo.activate_generation('A',tid,original['id'],generation['id'],[{'provider':'Existing tour','date':'2026-11-02'}])
    marker=tmp_path/'raw-crash.json'
    script=tmp_path/'raw_crash_worker.py'
    script.write_text('''from pathlib import Path
import json,sys,threading
from src.foundation.db import Database
from src.foundation.repository import Repository
from src.foundation.settings import Settings
from src.foundation.documents import DocumentService
root=Path(sys.argv[1]);tid=sys.argv[2]
settings=Settings(database_path=root/'private.sqlite3',documents_dir=root/'documents',vectors_dir=root/'vectors')
service=DocumentService(Repository(Database(settings.database_path)),settings)
def pause(point,path):
    (root/'raw-crash.json').write_text(json.dumps({'path':str(path),'point':point}))
    threading.Event().wait()
service.raw_save_fault_hook=pause
service.save('A',tid,'new.txt',b'New synthetic orphan')
''')
    environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'OPENAI_API_KEY':'test','SEED_ON_EMPTY':'0'}
    process=subprocess.Popen([sys.executable,str(script),str(tmp_path),tid],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
    try:
        deadline=time.monotonic()+15
        while not marker.exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(0.025)
        assert marker.exists(), process.communicate(timeout=1) if process.poll() is not None else 'crash boundary not reached'
        process.kill();process.wait(timeout=10)
        assert process.returncode<0
    finally:
        if process.poll() is None:
            process.kill();process.wait(timeout=10)
        process.stdout.close();process.stderr.close()
    orphan=Path(json.loads(marker.read_text())['path'])
    reopened=Database(settings.database_path);current=Repository(reopened)
    assert len(current.list_documents('A',tid))==1
    assert current.get_document('A',tid,original['id'])['active_generation_id']==generation['id']
    assert current.list_bookings('A',tid)[0]['provider']=='Existing tour'
    assert orphan.exists() and Path(original['opaque_path']).exists()
    young=reconcile_storage(reopened,settings,dispatcher_owner='maintenance-test')
    assert young['raw_removed']==0 and orphan.exists()
    future=datetime.now(timezone.utc)+timedelta(days=2)
    collected=reconcile_storage(reopened,settings,dispatcher_owner='maintenance-test',now=future)
    assert collected['raw_removed']==1 and not orphan.exists()
    assert Path(original['opaque_path']).exists()
    assert current.list_bookings('A',tid)[0]['provider']=='Existing tour'
