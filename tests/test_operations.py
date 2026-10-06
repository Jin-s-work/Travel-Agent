"""Phase06 production gates, online encrypted backup and disaster restoration."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import time
import pytest
from tests.test_foundation_api import service,_trip,_upload,_job
from tests.test_discovery_foundation import discovery
from tests.test_itinerary_api import planning,prepare,submit,done
from src.foundation.db import Database,SCHEMA_VERSION
from src.operations.backup import create_archive,write_checkpoint,restore_archive,decrypt,encrypt,key_bytes
from src.operations.controls import update
from src.operations.preflight import validate,StartupError,InstanceLock


def test_health_no_paid_dependencies_and_correct_schema(service):
    client=service.login('health').client
    assert service.lifetime.get('/health/live').json()=={'ok':True}
    response=service.lifetime.get('/health/ready')
    assert response.status_code==200,response.text
    assert all(response.json()['checks'].values())
    service.app.state.generations._client=object() # health must not depend on Chroma
    update(service.app.state.db,external_enabled=False,mode='read_only')
    assert client.get('/api/v2/trips').status_code==200
    assert client.post('/api/v2/trips',json={}).status_code==503
    assert client.get('/api/v2/admin/operations').status_code==404
    assert service.lifetime.get('/health/ready').status_code==200
    assert service.calls==[]
    (service.settings.database_path.parent/'RESTORE_PENDING.json').write_text('{}')
    assert service.lifetime.get('/health/ready').status_code==503
    assert client.get('/api/v2/trips').status_code==503
    (service.settings.database_path.parent/'RESTORE_PENDING.json').unlink()


def test_startup_rejects_unmounted_root_seed_workers_and_identity(service,monkeypatch):
    s=replace(service.settings,environment='production',public_base_url='https://beta.example.test')
    monkeypatch.setenv('PERSISTENT_STORAGE_ROOT',str(s.database_path.parent));monkeypatch.setenv('SEED_ON_EMPTY','0')
    monkeypatch.setattr(os,'geteuid',lambda:1000)
    assert validate(s,require_mount=False)['storage_writable']
    with pytest.raises(StartupError,match='mount'):validate(s)
    for key,value in [('SEED_ON_EMPTY','1'),('WEB_CONCURRENCY','2'),('UVICORN_WORKERS','2')]:
        monkeypatch.setenv(key,value)
        with pytest.raises(StartupError):validate(s,require_mount=False)
        monkeypatch.setenv(key,'0' if key=='SEED_ON_EMPTY' else '1')
    with pytest.raises(StartupError,match='identity'):validate(replace(s,oidc_client_id=''),require_mount=False)
    with pytest.raises(StartupError,match='separate'):validate(replace(s,vectors_dir=s.documents_dir/'nested'),require_mount=False)


def test_process_lock_blocks_second_instance(tmp_path):
    with InstanceLock(tmp_path/'runtime.lock'):
        with pytest.raises(StartupError):
            with InstanceLock(tmp_path/'runtime.lock'):pass
    with InstanceLock(tmp_path/'runtime.lock'):pass


def test_encryption_tamper_and_wrong_key_rejected_before_plaintext(tmp_path):
    source=tmp_path/'source';source.write_bytes(b'private synthetic data'*10000)
    key=os.urandom(32);archive=tmp_path/'archive';encrypt(source,archive,key)
    assert b'private synthetic' not in archive.read_bytes()
    decrypt(archive,tmp_path/'good',key);assert (tmp_path/'good').read_bytes()==source.read_bytes()
    with pytest.raises(ValueError):decrypt(archive,tmp_path/'bad',os.urandom(32))
    assert not (tmp_path/'bad').exists()
    data=bytearray(archive.read_bytes());data[70]^=1;archive.write_bytes(data)
    with pytest.raises(ValueError):decrypt(archive,tmp_path/'bad',key)
    with pytest.raises(ValueError):key_bytes('invalid')
    existing=tmp_path/'existing';existing.write_bytes(b'preserve existing file')
    with pytest.raises(ValueError):decrypt(archive,existing,key)
    assert existing.read_bytes()==b'preserve existing file'


def test_online_snapshot_later_deletion_checkpoint_and_restore_keeps_corrections_itinerary(planning,tmp_path):
    trip,pack,selected,reserved=prepare(planning,booking=True)
    itinerary,path=done(planning,trip,submit(planning,trip,selected,allow_provisional=True))
    original=_upload(planning.client,trip['id'])
    imported=next(b for b in planning.client.get(f"/api/v2/trips/{trip['id']}/bookings").json()['items'] if b['document_id'])
    correction=planning.client.patch(f"/api/v2/trips/{trip['id']}/bookings/{imported['id']}",json={'expected_version':imported['version'],'changes':[{'field_path':'time','value':'11:30'}]})
    assert correction.status_code==200,correction.text
    other=planning.login('restore-B');doomed=_trip(other.client,'deleted after backup')
    upload=_upload(other.client,doomed['id'],key='doomed')
    key=os.urandom(32);archive=tmp_path/'full.enc';checkpoint=tmp_path/'later.enc'
    result=create_archive(planning.settings,archive,key)
    assert result['raw_bytes']>0
    deleted=other.client.delete('/api/v2/trips/'+doomed['id'])
    assert deleted.status_code in (200,202),deleted.text
    write_checkpoint(planning.app.state.db,checkpoint,key)
    restored=tmp_path/'restored'
    report=restore_archive(archive,checkpoint,restored,key)
    assert report['state']=='restored_closed_for_validation'
    db=Database(restored/'database.sqlite3')
    with db.connect() as con:
        assert con.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
        assert con.execute('SELECT active_index_id FROM trips WHERE id=?',(trip['id'],)).fetchone()[0] is None
        assert con.execute('SELECT deleted_at FROM trips WHERE id=?',(doomed['id'],)).fetchone()[0]
        assert con.execute('SELECT COUNT(*) FROM booking_events WHERE trip_id=?',(doomed['id'],)).fetchone()[0]==0
        assert con.execute('SELECT version FROM itineraries WHERE id=?',(itinerary['id'],)).fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0]>0
        assert con.execute('SELECT external_enabled FROM operations_controls').fetchone()[0]==0
        assert con.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert not (restored/'documents'/doomed['id']).exists()
    assert list((restored/'documents'/trip['id']).iterdir())
    assert (restored/'RESTORE_PENDING.json').exists()
    assert not list((restored/'vectors').iterdir())
    # Latest deletion evidence is mandatory, and stale or foreign evidence fails.
    with pytest.raises(FileNotFoundError):restore_archive(archive,tmp_path/'missing',tmp_path/'missing-restore',key)
    with pytest.raises(ValueError,match='stale'):restore_archive(archive,checkpoint,tmp_path/'stale-restore',key,max_checkpoint_age_seconds=0)
    # Rebuild real Chroma only from surviving SQL under an isolated fake budget.
    from api import create_app
    from fastapi.testclient import TestClient
    from src.reliability.budget import BudgetPolicy
    from src.foundation.auth import digest
    (restored/'RESTORE_PENDING.json').unlink() # test's offline review gate
    update(db,mode='normal',external_enabled=True)
    with db.connect() as con:con.execute('UPDATE cost_controls SET halted=0')
    rebuild_calls=[]
    def embed(texts):rebuild_calls.append(len(texts));return [[.1]*8 for _ in texts]
    rebuilt=create_app(replace(planning.settings,database_path=restored/'database.sqlite3',documents_dir=restored/'documents',vectors_dir=restored/'vectors'),embedder=embed,budget_policy=BudgetPolicy.for_tests())
    started=time.monotonic()
    token=rebuilt.state.auth.complete_identity({'iss':'https://fixture.example.test','sub':'discovery-admin','email':'discovery-admin@example.test','email_verified':True},None)
    with TestClient(rebuilt) as client:
        client.cookies.set(planning.settings.cookie_name,token)
        session=client.get('/api/v2/session').json()
        with db.connect() as con:sid=con.execute('SELECT id FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()[0]
        current=rebuilt.state.repo.get_trip(session['user']['id'],trip['id'])
        queued=rebuilt.state.jobs.enqueue(session['user']['id'],sid,'personal_trip',trip['id'],'reindex',{},current['version'],'restore-reindex')
        job=_job(client,queued);assert job['state']=='succeeded',job
        assert client.get('/api/v2/trips/'+doomed['id']).status_code==404
        assert client.get(path).json()['version']==1
        assert client.get(f"/api/v2/trips/{trip['id']}/bookings/{imported['id']}").json()['time']=='11:30'
        with rebuilt.state.generations.reader(session['user']['id'],trip['id']) as reader:
            records=reader.collection.get(include=['metadatas'])
            assert records['ids']
            assert all(m['trip_id']==trip['id'] for m in records['metadatas'])
    report.update(reindex_seconds=round(time.monotonic()-started,3),reindex_fake_calls=len(rebuild_calls),paid_calls=0,correction_preserved=True,itinerary_preserved=True,deleted_trip_absent_from_chroma=True)
    if os.getenv('OPS_RESTORE_REPORT'):
        Path(os.environ['OPS_RESTORE_REPORT']).write_text(json.dumps({k:v for k,v in report.items() if k not in ('database_path','documents_dir','vectors_dir')},indent=2)+'\n')


def test_remote_transport_only_encrypted_roundtrip(tmp_path):
    from src.operations.remote import ObjectStore
    from io import BytesIO
    class FakeS3:
        def put_object(self,**kw):self.value=kw['Body'].read();self.meta=kw['Metadata']
        def head_object(self,**kw):return {'Metadata':self.meta,'ContentLength':len(self.value)}
        def get_object(self,**kw):return {**self.head_object(),'Body':BytesIO(self.value)}
    s3=FakeS3();store=ObjectStore(s3,'separate-fixture-bucket','private-beta')
    raw=tmp_path/'raw';raw.write_bytes(b'synthetic mail');encrypted=tmp_path/'archive';encrypt(raw,encrypted,os.urandom(32))
    store.upload(encrypted,'snapshot-test.enc');assert b'synthetic mail' not in s3.value
    store.download('snapshot-test.enc',tmp_path/'downloaded');assert encrypted.read_bytes()==(tmp_path/'downloaded').read_bytes()
    with pytest.raises(ValueError):store.download('../secret',tmp_path/'bad')


def test_production_filters_preexisting_synthetic_catalog_detail_and_resolution(discovery):
    from tests.test_discovery_foundation import import_pack,bookmark,resolve
    data=import_pack(discovery);trip=_trip(discovery.client);place=data['places'][0]
    discovery.app.state.discovery.allow_synthetic=False
    response=discovery.client.get(f"/api/v2/trips/{trip['id']}/places/{place['place_id']}/detail")
    assert response.status_code==404,response.text
    with discovery.app.state.db.connect() as con:
        assert discovery.app.state.discovery._visible_place(con,place['place_id']) is None
    value=bookmark(discovery.client,trip,'name',place['name'])
    result,_=resolve(discovery.client,trip,value)
    assert result['resolve_state']=='unsupported' and not result['candidates']
    availability=discovery.app.state.discovery.availability('tokyo')
    assert availability['real_reviewed_candidates']==0
    assert availability['synthetic_test_candidates']==0
    response=discovery.client.patch('/api/v2/admin/discovery-packs/'+data['id'],json={'status':'approved','evidence':'Attempt synthetic activation'})
    assert response.status_code==422,response.text
