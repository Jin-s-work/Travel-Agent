"""No external keys: real SQLite, deterministic vectors and temporary Chroma."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from src.foundation.db import Database
from src.foundation.repository import DomainError, Repository, dump, utcnow
from src.reliability.generations import GenerationManager


class FakeCollection:
    def __init__(self):
        self.records = {}
        self.upserts = 0

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upserts += 1
        for ident, vector, text, metadata in zip(ids, embeddings, documents, metadatas):
            self.records[ident] = (deepcopy(metadata), text, list(vector))

    def get(self, include):
        return {'ids': list(self.records), 'metadatas': [r[0] for r in self.records.values()],
                'documents': [r[1] for r in self.records.values()], 'embeddings': [r[2] for r in self.records.values()]}

    def count(self):
        return len(self.records)

    def query(self, query_embeddings, n_results, include):
        result = self.get(include)
        return {key: [values[:n_results]] for key, values in result.items() if key != 'embeddings'} | {'distances': [[0.0] * min(n_results, self.count())]}


class FakeClient:
    def __init__(self):
        self.collections = {}

    def get_collection(self, name, embedding_function=None):
        return self.collections[name]

    def create_collection(self, name, embedding_function=None, metadata=None):
        assert name not in self.collections
        self.collections[name] = FakeCollection()
        return self.collections[name]

    def delete_collection(self, name):
        del self.collections[name]


def setup_storage(path):
    db = Database(path / 'test.sqlite3')
    now = utcnow()
    with db.connect() as con:
        for user in ('A', 'B'):
            con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                        (user, user + '@example.invalid', 'test', user, now, now))
            con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES (?,?,?,?,?,?,?)',
                        ('session-' + user, user, 'hash-' + user, 'csrf', '2099-01-01T00:00:00+00:00', 0, now))
        if con.execute('PRAGMA user_version').fetchone()[0] == 1:
            from src.reliability.schema import SCHEMA
            con.executescript(SCHEMA)
    repo = Repository(db)
    trip = repo.create_trip('A', {'title': 'Synthetic', 'start_date': '2026-11-01', 'end_date': '2026-11-04'})
    return db, repo, trip['id']


def make_job(db, trip_id, job_id='job-one', fence=1):
    now, expiry = utcnow(), '2099-01-01T00:00:00+00:00'
    with db.connect() as con:
        version = con.execute('SELECT version FROM trips WHERE id=?', (trip_id,)).fetchone()[0]
        values = {'id': job_id, 'actor_id': 'A', 'session_id': 'session-A', 'scope_kind': 'personal_trip',
                  'scope_id': trip_id, 'owner_id': 'A', 'trip_id': trip_id, 'operation': 'index', 'state': 'running',
                  'payload_json': '{}', 'payload_hash': 'test', 'input_version': version, 'max_attempts': 3,
                  'available_at': now, 'deadline_at': expiry, 'lease_expires_at': expiry, 'fencing_token': fence,
                  'created_at': now, 'updated_at': now}
        con.execute('INSERT INTO jobs(' + ','.join(values) + ') VALUES (' + ','.join('?' for _ in values) + ')', tuple(values.values()))
        con.execute('INSERT INTO trip_index_writers(trip_id,holder_job_id,fencing_token,lease_expires_at) VALUES (?,?,?,?) ON CONFLICT(trip_id) DO UPDATE SET holder_job_id=excluded.holder_job_id,fencing_token=excluded.fencing_token,lease_expires_at=excluded.lease_expires_at',
                    (trip_id, job_id, fence, expiry))
    return job_id


def next_fence(db, trip_id, job_id, fence):
    with db.connect() as con:
        con.execute('UPDATE jobs SET fencing_token=? WHERE id=?', (fence, job_id))
        con.execute('UPDATE trip_index_writers SET fencing_token=? WHERE trip_id=?', (fence, trip_id))


def prepare(repo, trip_id, label='one', doc=None):
    doc = doc or repo.create_document('A', trip_id, 'same.eml', '/synthetic/' + label, label)
    parsed = [{'kind': '투어', 'provider': label, 'stable_item_key': label,
               'confirmation_number': label, 'date': '2026-11-02', 'time': '09:00'}]
    generation = repo.create_generation('A', trip_id, doc['id'])
    changed = {'document_id': doc['id'], 'generation_id': generation['id'], 'content_hash': doc['content_hash'],
               'chunks': [{'text': label + ' facts', 'embedding': [1.0, 0.0, 0.0]}]}
    staged = {'document_id': doc['id'], 'generation_id': generation['id'], 'bookings': parsed}
    return doc, changed, staged


def build(manager, tid, job, documents, fence=1):
    return manager.build('A', tid, job, fence, documents, embedding_model='fake-3', embedding_dimension=3)


def activate(manager, tid, job, generation, staged=(), fence=1):
    return manager.activate('A', tid, generation['id'], job, fence, staged_documents=staged, session_id='session-A')


@pytest.fixture
def environment(tmp_path):
    db, repo, tid = setup_storage(tmp_path)
    client = FakeClient()
    manager = GenerationManager(db, repo, client=client)
    job = make_job(db, tid)
    return db, repo, tid, client, manager, job


def test_uncommitted_generation_invisible_and_atomic_sql_activation(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    first = build(manager, tid, job, [changed])
    assert first['state'] == 'ready' and repo.list_bookings('A', tid) == []
    with pytest.raises(DomainError, match='검색 자료'):
        with manager.reader('A', tid):
            pass
    active = activate(manager, tid, job, first, [staged])
    assert active['state'] == 'active'
    assert len(repo.list_bookings('A', tid)) == 1
    with db.connect() as con:
        assert con.execute('SELECT active_index_id FROM trips WHERE id=?', (tid,)).fetchone()[0] == first['id']
        assert con.execute('SELECT active_generation_id FROM source_documents').fetchone()[0] == staged['generation_id']
    # SQL commit happened before a completion event: replay activation is safe.
    assert activate(manager, tid, job, first, [staged])['id'] == first['id']


def test_previous_reader_survives_pointer_swap_until_reference_released(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    old = build(manager, tid, job, [changed])
    activate(manager, tid, job, old, [staged])
    job2 = make_job(db, tid, 'job-two')
    _, changed2, staged2 = prepare(repo, tid, 'two')
    with manager.reader('A', tid) as reader:
        new = build(manager, tid, job2, [changed2])
        assert sum(doc['chunk_count'] for doc in new['manifest']['documents']) == 2
        activate(manager, tid, job2, new, [staged2])
        assert manager.cleanup() == []
        assert len(reader.query([1, 0, 0])) == 1
        assert old['collection_name'] in client.collections
    assert manager.get('A', tid, old['id'])['state'] == 'deleted'
    assert manager.cleanup() == []
    assert old['collection_name'] not in client.collections
    with manager.reader('A', tid) as reader:
        assert len(reader.query([1, 0, 0])) == 2


def test_latest_user_correction_blocks_stale_build_and_survives(environment):
    db, repo, tid, client, manager, job = environment
    doc, changed, staged = prepare(repo, tid)
    old = build(manager, tid, job, [changed])
    activate(manager, tid, job, old, [staged])
    job2 = make_job(db, tid, 'job-two')
    _, new_changed, new_staged = prepare(repo, tid, doc=doc)
    pending = build(manager, tid, job2, [new_changed])
    booking = repo.list_bookings('A', tid)[0]
    repo.update_booking('A', tid, booking['id'], {'expected_version': booking['version'], 'changes': [{'field_path': 'time', 'value': '10:30'}]})
    with pytest.raises(DomainError) as error:
        activate(manager, tid, job2, pending, [new_staged])
    assert error.value.code == 'VERSION_CONFLICT'
    assert repo.list_bookings('A', tid)[0]['time'] == '10:30'
    with manager.reader('A', tid) as reader:
        assert reader.generation['id'] == old['id']


def test_fence_expired_worker_cannot_activate_ready_generation(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    ready = build(manager, tid, job, [changed])
    next_fence(db, tid, job, 2)
    with pytest.raises(DomainError) as error:
        activate(manager, tid, job, ready, [staged])
    assert error.value.code == 'LEASE_LOST'
    resumed = build(manager, tid, job, [changed], fence=2)
    assert resumed['id'] == ready['id']
    activate(manager, tid, job, resumed, [staged], fence=2)


def test_crash_after_chroma_write_resumes_without_reembedding_but_new_collection_on_new_fence(environment, monkeypatch):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    verify = manager.verify
    monkeypatch.setattr(manager, 'verify', lambda *args: (_ for _ in ()).throw(SystemExit('simulated process death before ready')))
    with pytest.raises(SystemExit):
        build(manager, tid, job, [changed])
    old_name = next(iter(client.collections))
    monkeypatch.setattr(manager, 'verify', verify)
    next_fence(db, tid, job, 2)
    changed['chunks'][0].pop('embedding')
    resumed = build(manager, tid, job, [changed], fence=2)
    assert resumed['collection_name'] != old_name
    activate(manager, tid, job, resumed, [staged], fence=2)
    # A late stale collection mutation cannot touch the new active snapshot.
    client.collections[old_name].records.clear()
    with manager.reader('A', tid) as reader:
        assert len(reader.query([1, 0, 0])) == 1


def test_missing_or_corrupt_active_collection_never_becomes_empty_success(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    old = build(manager, tid, job, [changed])
    activate(manager, tid, job, old, [staged])
    client.collections[old['collection_name']].records.clear()
    with pytest.raises(DomainError) as error:
        with manager.reader('A', tid):
            pass
    assert error.value.code == 'SEARCH_REBUILDING'
    del client.collections[old['collection_name']]
    with pytest.raises(DomainError) as error:
        with manager.reader('A', tid):
            pass
    assert error.value.code == 'SEARCH_REBUILDING'
    assert old['collection_name'] not in client.collections
    assert len(repo.list_bookings('A', tid, date_from='2026-11-02', date_to='2026-11-02')) == 1
    with pytest.raises(DomainError) as error:
        build(manager, tid, job, [])
    assert error.value.code == 'INDEX_REBUILD_INPUT_REQUIRED'


def test_trip_delete_and_revocation_reject_late_activation(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    ready = build(manager, tid, job, [changed])
    with db.connect() as con:
        con.execute('UPDATE users SET session_epoch=session_epoch+1 WHERE id=?', ('A',))
    with pytest.raises(DomainError):
        activate(manager, tid, job, ready, [staged])
    repo.delete_trip('A', tid)
    with pytest.raises(DomainError) as error:
        activate(manager, tid, job, ready, [staged])
    assert error.value.status == 404
    assert manager.retire_deleted_trip(tid) == [ready['id']]
    assert client.collections == {}


def test_foreign_trip_and_wrong_dimensions_rejected(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    changed['chunks'][0]['embedding'] = [1, 0]
    with pytest.raises(DomainError) as error:
        build(manager, tid, job, [changed])
    assert error.value.code == 'INDEX_VERIFICATION_FAILED'
    with pytest.raises(DomainError) as error:
        manager.build('B', tid, job, 1, [], embedding_model='fake-3', embedding_dimension=3)
    assert error.value.status == 404


def test_expired_lease_and_incomplete_manifest_cannot_activate(environment):
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    ready = build(manager, tid, job, [changed])
    with db.connect() as con:
        con.execute('UPDATE jobs SET lease_expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', job))
    with pytest.raises(DomainError) as error:
        activate(manager, tid, job, ready, [staged])
    assert error.value.code == 'LEASE_LOST'
    assert repo.list_bookings('A', tid) == []


def test_scope_corruption_and_activation_rollback_preserve_old_facts(environment):
    db, repo, tid, client, manager, job = environment
    doc, changed, staged = prepare(repo, tid)
    old = build(manager, tid, job, [changed])
    activate(manager, tid, job, old, [staged])
    job2 = make_job(db, tid, 'job-two')
    _, replacement, replacement_staged = prepare(repo, tid, doc=doc)
    ready = build(manager, tid, job2, [replacement])
    collection = client.collections[ready['collection_name']]
    row = next(iter(collection.records.values()))
    row[0]['trip_id'] = 'foreign-trip'
    with pytest.raises(DomainError) as error:
        activate(manager, tid, job2, ready, [replacement_staged])
    assert error.value.code == 'INDEX_VERIFICATION_FAILED'
    row[0]['trip_id'] = tid
    # A staged fact list with ambiguous identities must roll back fact writes,
    # document pointer and index pointer together.
    replacement_staged['bookings'] *= 2
    with pytest.raises(DomainError) as error:
        activate(manager, tid, job2, ready, [replacement_staged])
    assert error.value.code == 'AMBIGUOUS_MATCH'
    assert repo.get_document('A', tid, doc['id'])['active_generation_id'] == staged['generation_id']
    assert len(repo.list_bookings('A', tid)) == 1
    with manager.reader('A', tid) as reader:
        assert reader.generation['id'] == old['id']


def test_reindex_handler_recovers_committed_pointer_without_new_collection_or_embedding(environment, tmp_path, monkeypatch):
    from src.reliability.dispatcher import JobContext
    from src.reliability.handlers import Operations
    from src.reliability.jobs import Jobs
    monkeypatch.setattr('src.reliability.handlers.EMBEDDING_MODEL','fake-3')
    db, repo, tid, client, manager, job = environment
    _, changed, staged = prepare(repo, tid)
    old = build(manager, tid, job, [changed])
    activate(manager, tid, job, old, [staged])
    job2 = make_job(db, tid, 'job-reindex')
    jobs = Jobs(db)
    manager.jobs = jobs
    documents = SimpleNamespace(settings=SimpleNamespace(artifacts_dir=tmp_path/'artifacts', documents_dir=tmp_path/'documents'))
    def die(point, value):
        if point == 'after_activation':
            raise SystemExit('process died after pointer commit')
    operations = Operations(repo, documents, jobs, manager, object(), fault_hook=die)
    claimed = jobs.guard(job2, 1)
    with pytest.raises(SystemExit):
        operations.reindex(claimed, JobContext(jobs, claimed, 'test-owner'))
    current = repo.get_trip('A', tid)
    count = len(client.collections)
    next_fence(db, tid, job2, 2)
    operations.fault_hook = None
    reclaimed = jobs.guard(job2, 2)
    result = operations.reindex(reclaimed, JobContext(jobs, reclaimed, 'test-owner'))
    assert result['result']['index_id'] == current['active_index_id']
    assert repo.get_trip('A', tid)['version'] == current['version']
    assert len(client.collections) == count


def test_reindex_checkpoint_model_change_requires_new_job(environment, tmp_path, monkeypatch):
    from src.reliability.dispatcher import JobContext
    from src.reliability.handlers import Operations, stable_hash
    from src.reliability.jobs import Jobs
    db, repo, tid, client, manager, job = environment
    jobs=Jobs(db);manager.jobs=jobs
    documents=SimpleNamespace(settings=SimpleNamespace(artifacts_dir=tmp_path/'artifacts',documents_dir=tmp_path/'documents'))
    operations=Operations(repo,documents,jobs,manager,object())
    jobs.checkpoint(job,1,{'pipeline_signature':stable_hash({'embedding_model':'old-model','chunker':'v1'}),
                         'reindex_refs':{'generation':{'ref':'checkpoint.json','model':'old-model','dimension':3}}})
    monkeypatch.setattr('src.reliability.handlers.EMBEDDING_MODEL','new-model')
    claimed=jobs.guard(job,1)
    with pytest.raises(DomainError) as error:
        operations.reindex(claimed,JobContext(jobs,claimed,'test-owner'))
    assert error.value.code=='CHECKPOINT_INCOMPATIBLE'
    assert client.collections=={}


@pytest.mark.parametrize('boundary', ['ready', 'active'])
def test_real_process_sigkill_before_or_after_sql_pointer_commit(tmp_path, boundary):
    """Actual kill, real Chroma files, then a fresh interpreter-created store."""
    marker = tmp_path / 'boundary.json'
    script = tmp_path / 'crash_worker.py'
    script.write_text('''import json, runpy, sys, threading
from pathlib import Path
helper = runpy.run_path(sys.argv[1])
root = Path(sys.argv[2])
db, repo, tid = helper['setup_storage'](root)
manager = helper['GenerationManager'](db, repo, root / 'chroma')
job = helper['make_job'](db, tid)
_, changed, staged = helper['prepare'](repo, tid)
ready = helper['build'](manager, tid, job, [changed])
if sys.argv[3] == 'active':
    helper['activate'](manager, tid, job, ready, [staged])
(root / 'boundary.json').write_text(json.dumps({'tid':tid,'job':job,'ready':ready,'changed':changed,'staged':staged}))
threading.Event().wait()
''')
    environment = {**os.environ, 'OPENAI_API_KEY': 'test', 'SEED_ON_EMPTY': '0',
                   'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    process = subprocess.Popen([sys.executable, str(script), str(Path(__file__).resolve()), str(tmp_path), boundary],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert marker.exists(), process.communicate(timeout=1) if process.poll() is not None else 'worker did not reach crash boundary'
        process.kill()
        process.wait(timeout=10)
        assert process.returncode < 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()
    state = json.loads(marker.read_text())
    db = Database(tmp_path / 'test.sqlite3')
    repo = Repository(db)
    manager = GenerationManager(db, repo, tmp_path / 'chroma')
    next_fence(db, state['tid'], state['job'], 2)
    if boundary == 'ready':
        assert repo.list_bookings('A', state['tid']) == []
        # Recovery has no embedding in input; completed Chroma snapshot is used.
        state['changed']['chunks'][0].pop('embedding')
        ready = build(manager, state['tid'], state['job'], [state['changed']], fence=2)
        assert ready['id'] == state['ready']['id']
        activate(manager, state['tid'], state['job'], ready, [state['staged']], fence=2)
    else:
        assert len(repo.list_bookings('A', state['tid'])) == 1
        activate(manager, state['tid'], state['job'], state['ready'], [state['staged']], fence=2)
    with manager.reader('A', state['tid']) as reader:
        assert len(reader.query([1, 0, 0])) == 1
        assert reader.generation['id'] == state['ready']['id']


def test_real_chroma_immutable_collection_switch_and_dimension_manifest(tmp_path):
    db, repo, tid = setup_storage(tmp_path)
    manager = GenerationManager(db, repo, tmp_path / 'chroma')
    job = make_job(db, tid)
    _, changed, staged = prepare(repo, tid)
    old = build(manager, tid, job, [changed])
    activate(manager, tid, job, old, [staged])
    job2 = make_job(db, tid, 'job-two')
    _, changed2, staged2 = prepare(repo, tid, 'two')
    second = build(manager, tid, job2, [changed2])
    with manager.reader('A', tid) as reader:
        assert len(reader.query([1, 0, 0])) == 1
        activate(manager, tid, job2, second, [staged2])
        assert manager.cleanup() == []
    assert manager.get('A', tid, old['id'])['state'] == 'deleted'
    assert manager.cleanup() == []
    with manager.reader('A', tid) as reader:
        assert len(reader.query([1, 0, 0])) == 2
        assert reader.generation['embedding_dimension'] == 3
