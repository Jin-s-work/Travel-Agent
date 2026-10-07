"""Immutable trip indexes with SQL activation and reference-safe reclamation.

Chroma writes deliberately happen outside SQL transactions. A generation is
invisible until verification succeeds and the SQL pointer commits. All external
embeddings must be supplied by the budgeted provider layer; copying an unchanged
chunk does not invoke an embedding function.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
import math
import os
from pathlib import Path

from src.foundation.repository import DomainError, dump, new_id, utcnow


def _error(code, message, status=409):
    raise DomainError(code, message, status)


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _missing_collection(exc):
    return isinstance(exc, KeyError) or type(exc).__name__ in ('NotFoundError', 'InvalidCollectionException')


def _vector(value, dimension):
    if value is None:
        _error('EMBEDDING_REQUIRED', '변경된 검색 자료의 임베딩이 필요합니다.')
    result = [float(number) for number in value]
    if len(result) != dimension or not all(math.isfinite(number) for number in result):
        _error('INDEX_VERIFICATION_FAILED', '검색 벡터의 차원이 잘못되었습니다.')
    return result


class IndexReader:
    def __init__(self, manager, user_id, trip_id, generation, collection):
        self.manager, self.user_id, self.trip_id = manager, user_id, trip_id
        self.generation, self.collection = generation, collection

    def query(self, embedding, top_k=5):
        """Only this acquired generation can occupy semantic result slots."""
        vector = _vector(embedding, self.generation['embedding_dimension'])
        total = self.collection.count()
        if not total:
            return []
        try:
            result = self.collection.query(query_embeddings=[vector], n_results=min(top_k, total),
                                           include=['metadatas', 'documents', 'distances'])
        except Exception as exc:
            raise DomainError('SEARCH_REBUILDING', '검색 자료를 복구하고 있습니다. 날짜별 예약은 계속 확인할 수 있습니다.', 503) from exc
        hits = []
        # Recheck source liveness after Chroma, never return raw stale facts as
        # answer authority. The caller hydrates hits with current SQL overlays.
        with self.manager.db.connect() as con:
            self.manager.repo._trip(con, self.user_id, self.trip_id)
            active = {row['id']: row['active_generation_id'] for row in con.execute(
                'SELECT id,active_generation_id FROM source_documents WHERE trip_id=? AND deleted_at IS NULL', (self.trip_id,))}
            for ident, metadata, document, distance in zip(result['ids'][0], result['metadatas'][0], result['documents'][0], result['distances'][0]):
                if metadata.get('trip_id') != self.trip_id or active.get(metadata.get('document_id')) != metadata.get('generation_id'):
                    continue
                hits.append({'id': ident, 'metadata': metadata, 'document': document,
                             'distance': distance, 'similarity': max(0.0, 1.0 - float(distance))})
        return hits


class GenerationManager:
    def __init__(self, db, repo, chroma_path=None, *, client=None, jobs=None):
        self.db, self.repo, self.jobs = db, repo, jobs
        self._client, self.chroma_path = client, Path(chroma_path) if chroma_path else None
        if client is None and db.backend=='postgres':
            from src.storage.vectors import PostgresVectors
            self._client=PostgresVectors(db)

    @property
    def client(self):
        if self._client is None:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(path=str(self.chroma_path),
                                                     settings=Settings(anonymized_telemetry=False))
        return self._client

    def _guard(self, con, user_id, trip_id, job_id, fencing_token):
        trip = self.repo._trip(con, user_id, trip_id)
        if con.execute('SELECT 1 FROM deletion_tombstones WHERE target_type=? AND target_id=?', ('trip', trip_id)).fetchone():
            _error('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
        if self.jobs is None:
            # Production callers must supply the durable job owner. This fallback
            # still checks the actual SQL lease and is useful in isolated tests.
            row = con.execute('SELECT j.* FROM jobs j JOIN trip_index_writers w ON w.holder_job_id=j.id '
                              'JOIN sessions s ON s.id=j.session_id AND s.user_id=j.actor_id '
                              'JOIN users u ON u.id=j.actor_id '
                              'WHERE j.id=? AND j.trip_id=? AND j.owner_id=? AND j.state=? '
                              'AND j.fencing_token=? AND j.lease_expires_at>? '
                              'AND j.cancel_requested_at IS NULL AND w.trip_id=? '
                              'AND w.fencing_token=? AND w.lease_expires_at>? '
                              'AND s.epoch=u.session_epoch AND s.expires_at>? AND u.status=?',
                              (job_id, trip_id, user_id, 'running', fencing_token, utcnow(), trip_id, fencing_token, utcnow(), utcnow(), 'active')).fetchone()
            if row is None:
                _error('LEASE_LOST', '작업 실행 권한이 만료되었습니다.')
        else:
            row = self.jobs.guard(job_id, fencing_token, con=con)
            if row['trip_id'] != trip_id or row['owner_id'] != user_id:
                _error('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
        return trip

    @staticmethod
    def _dto(row):
        result = dict(row)
        result['manifest'] = json.loads(result['source_manifest'])
        return result

    def get(self, user_id, trip_id, generation_id):
        with self.db.connect() as con:
            self.repo._trip(con, user_id, trip_id)
            row = con.execute('SELECT * FROM trip_index_generations WHERE id=? AND trip_id=?', (generation_id, trip_id)).fetchone()
            if row is None:
                _error('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)
            return self._dto(row)

    def _get_collection(self, name):
        # Never use get_or_create for an active pointer: absence is corruption.
        try:
            return self.client.get_collection(name=name, embedding_function=None)
        except Exception as exc:
            raise DomainError('SEARCH_REBUILDING', '검색 자료를 복구하고 있습니다. 날짜별 예약은 계속 확인할 수 있습니다.', 503) from exc

    def _records(self, collection):
        result = collection.get(include=['metadatas', 'documents', 'embeddings'])
        embeddings = result.get('embeddings')
        if embeddings is None:
            embeddings = [None] * len(result['ids'])
        return {ident: {'id': ident, 'metadata': metadata, 'text': text, 'embedding': embedding}
                for ident, metadata, text, embedding in zip(result['ids'], result['metadatas'], result['documents'], embeddings)}

    def verify(self, generation, collection=None):
        collection = collection or self._get_collection(generation['collection_name'])
        records = self._records(collection)
        if generation['manifest'].get('embedding_model') != generation['embedding_model'] or generation['manifest'].get('embedding_dimension') != generation['embedding_dimension']:
            _error('INDEX_VERIFICATION_FAILED', '검색 모델의 설정이 일치하지 않습니다.')
        if any(document['chunk_count'] != len(document['chunks']) for document in generation['manifest']['documents']):
            _error('INDEX_VERIFICATION_FAILED', '문서의 검색 청크 수가 일치하지 않습니다.')
        expected = {chunk['id']: (document, chunk) for document in generation['manifest']['documents'] for chunk in document['chunks']}
        if len(expected) != sum(len(document['chunks']) for document in generation['manifest']['documents']) or set(records) != set(expected) or collection.count() != len(expected):
            _error('INDEX_VERIFICATION_FAILED', '검색 자료의 개수가 일치하지 않습니다.')
        for ident, record in records.items():
            document, chunk = expected[ident]
            metadata = record['metadata'] or {}
            if any(metadata.get(key) != value for key, value in {
                'trip_id': generation['trip_id'], 'document_id': document['document_id'],
                'generation_id': document['generation_id'], 'content_hash': document['content_hash'],
                'text_hash': chunk['text_hash'], 'embedding_model': generation['embedding_model'],
                'embedding_dimension': generation['embedding_dimension']}.items()) or _hash(record['text']) != chunk['text_hash']:
                _error('INDEX_VERIFICATION_FAILED', '검색 자료의 범위 또는 내용이 일치하지 않습니다.')
            _vector(record['embedding'], generation['embedding_dimension'])
        return records

    @contextmanager
    def reader(self, user_id, trip_id):
        with ExitStack() as pins:
            with self._reader(user_id,trip_id,pins) as reader:
                yield reader

    @contextmanager
    def _reader(self, user_id, trip_id, pins):
        reference_id = new_id('reader')
        with self.db.connect() as con:
            # Pointer read and reference registration cannot race a retirement.
            con.execute('BEGIN IMMEDIATE')
            trip = self.repo._trip(con, user_id, trip_id)
            row = con.execute('SELECT * FROM trip_index_generations WHERE id=? AND trip_id=? AND state=?',
                              (trip['active_index_id'], trip_id, 'active')).fetchone()
            if row is None:
                _error('SEARCH_REBUILDING', '검색 자료를 준비하고 있습니다. 날짜별 예약은 계속 확인할 수 있습니다.', 503)
            if self.db.backend=='postgres': pins.enter_context(self.db.pin_generation(row['id']))
            con.execute('INSERT INTO trip_index_readers(id,generation_id,process_id,created_at) VALUES (?,?,?,?)',
                        (reference_id, row['id'], os.getpid(), utcnow()))
            generation = self._dto(row)
        try:
            collection = self._get_collection(generation['collection_name'])
            try:
                self.verify(generation, collection)
            except Exception as exc:
                raise DomainError('SEARCH_REBUILDING', '검색 자료를 복구하고 있습니다. 날짜별 예약은 계속 확인할 수 있습니다.', 503) from exc
            yield IndexReader(self, user_id, trip_id, generation, collection)
        finally:
            with self.db.connect() as con:
                con.execute('DELETE FROM trip_index_readers WHERE id=?', (reference_id,))
            pins.close()
            # Releasing the last reader can unblock an earlier retirement.
            # Reclamation is best effort here; durable cleanup jobs retry I/O
            # failures without converting an otherwise valid read to an error.
            try:
                self.cleanup(trip_id)
            except Exception:
                pass

    def build(self, user_id, trip_id, job_id, fencing_token, documents, *, embedding_model,
              embedding_dimension, base_trip_version=None):
        """Merge changed documents with unchanged vectors; resume staged writes.

        documents contains document_id/generation_id/content_hash and chunks of
        {text, embedding}. An empty list rebuilds by copying live active chunks.
        A missing embedding is accepted only when the identical chunk exists in
        a verified active generation (same model and dimension).
        """
        if not embedding_model or not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            _error('INDEX_VERIFICATION_FAILED', '임베딩 모델과 차원이 필요합니다.')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            trip = self._guard(con, user_id, trip_id, job_id, fencing_token)
            base = trip['version'] if base_trip_version is None else base_trip_version
            if base != trip['version']:
                _error('VERSION_CONFLICT', '예약이 변경되어 검색 자료를 다시 준비해야 합니다.')
            live = {row['id']: dict(row) for row in con.execute('SELECT * FROM source_documents WHERE trip_id=? AND deleted_at IS NULL', (trip_id,))}
            for document in documents:
                stored = live.get(document['document_id'])
                generation = con.execute('SELECT * FROM document_generations WHERE id=? AND document_id=?', (document['generation_id'], document['document_id'])).fetchone()
                if stored is None or generation is None or stored['content_hash'] != document['content_hash'] or generation['status'] not in ('processing', 'staged', 'active'):
                    _error('VERSION_CONFLICT', '검색 대상 원문이 변경되었습니다.')
        old_records, old_documents = {}, []
        if trip['active_index_id']:
            try:
                with self.reader(user_id, trip_id) as reader:
                    if reader.generation['embedding_model'] == embedding_model and reader.generation['embedding_dimension'] == embedding_dimension:
                        old_records = self.verify(reader.generation, reader.collection)
                        old_documents = reader.generation['manifest']['documents']
            except DomainError as exc:
                if exc.code != 'SEARCH_REBUILDING':
                    raise
                # Recovery must supply every active document's chunks. A broken
                # collection cannot silently become a valid empty replacement.
        incoming_ids = [document['document_id'] for document in documents]
        if len(set(incoming_ids)) != len(incoming_ids):
            _error('INDEX_VERIFICATION_FAILED', '문서가 중복되었습니다.')
        manifest_docs, planned = [], {}
        for document in old_documents:
            current = live.get(document['document_id'])
            if document['document_id'] not in incoming_ids and current and current['active_generation_id'] == document['generation_id']:
                manifest_docs.append(document)
                planned.update({chunk['id']: old_records[chunk['id']] for chunk in document['chunks']})
        for document in documents:
            chunks = []
            for ordinal, chunk in enumerate(document['chunks']):
                text_hash = _hash(chunk['text'])
                ident = 'chunk_' + _hash(dump([document['document_id'], document['generation_id'], ordinal, text_hash]))
                previous = old_records.get(ident)
                embedding = chunk.get('embedding')
                if embedding is None and previous:
                    embedding = previous['embedding']
                metadata = {'trip_id': trip_id, 'document_id': document['document_id'], 'generation_id': document['generation_id'],
                            'content_hash': document['content_hash'], 'text_hash': text_hash,
                            'embedding_model': embedding_model, 'embedding_dimension': embedding_dimension}
                planned[ident] = {'id': ident, 'text': chunk['text'], 'metadata': metadata, 'embedding': embedding}
                chunks.append({'id': ident, 'text_hash': text_hash})
            manifest_docs.append({'document_id': document['document_id'], 'generation_id': document['generation_id'],
                                  'content_hash': document['content_hash'], 'parse_version': document.get('parse_version', 'v1'),
                                  'chunk_count': len(chunks), 'chunks': chunks})
        represented = {document['document_id'] for document in manifest_docs}
        if any(document['active_generation_id'] and ident not in represented for ident, document in live.items()):
            _error('INDEX_REBUILD_INPUT_REQUIRED', '복구할 활성 원문의 검색 자료가 필요합니다.')
        manifest_docs.sort(key=lambda document: document['document_id'])
        manifest = {'documents': manifest_docs, 'embedding_model': embedding_model, 'embedding_dimension': embedding_dimension}
        encoded = dump(manifest)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = self._guard(con, user_id, trip_id, job_id, fencing_token)
            if current['version'] != base:
                _error('VERSION_CONFLICT', '예약이 변경되어 검색 자료를 다시 준비해야 합니다.')
            existing = con.execute('SELECT * FROM trip_index_generations WHERE job_id=? AND trip_id=? AND base_trip_version=? AND source_manifest=? AND state IN (?,?) ORDER BY created_at DESC LIMIT 1',
                                   (job_id, trip_id, base, encoded, 'staged', 'ready')).fetchone()
            # A stale worker may still be inside Chroma after losing its lease.
            # Never let a new fence publish that worker's writable collection.
            # Ready collections are immutable and can safely be adopted.
            previous_staged = self._dto(existing) if existing and existing['state'] == 'staged' and existing['fencing_token'] != fencing_token else None
            if existing and previous_staged is None:
                ident, collection_name = existing['id'], existing['collection_name']
                con.execute('UPDATE trip_index_generations SET fencing_token=? WHERE id=?', (fencing_token, ident))
            else:
                ident = new_id('idx')
                collection_name = 'ti_' + new_id('snapshot')
                con.execute('INSERT INTO trip_index_generations(id,trip_id,collection_name,state,base_trip_version,source_manifest,embedding_model,embedding_dimension,job_id,fencing_token,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (ident, trip_id, collection_name, 'staged', base, encoded, embedding_model, embedding_dimension, job_id, fencing_token, utcnow()))
            generation = self._dto(con.execute('SELECT * FROM trip_index_generations WHERE id=?', (ident,)).fetchone())
        try:
            collection = self.client.get_collection(name=collection_name, embedding_function=None)
        except Exception:
            collection = self.client.create_collection(name=collection_name, embedding_function=None,
                                                       metadata={'trip_id': trip_id, 'index_generation_id': ident, 'hnsw:space': 'cosine'})
        # A delete can race create_collection itself. Do not leave that late
        # collection visible or orphaned merely because no chunks were written.
        try:
            with self.db.connect() as con:
                self._guard(con, user_id, trip_id, job_id, fencing_token)
        except DomainError:
            with self.db.connect() as con:
                deleted = con.execute('SELECT deleted_at FROM trips WHERE id=?', (trip_id,)).fetchone()
            if deleted and deleted['deleted_at']:
                try:
                    self.client.delete_collection(name=collection_name)
                except Exception:
                    pass  # Cleanup job retries the persisted generation row.
            raise
        if generation['state'] == 'ready':
            self.verify(generation, collection)
            return generation
        present = self._records(collection)
        reusable_stage = {}
        if previous_staged:
            try:
                reusable_stage = self._records(self._get_collection(previous_staged['collection_name']))
            except DomainError:
                pass
        if set(present) - set(planned):
            _error('INDEX_VERIFICATION_FAILED', '준비 중 검색 자료에 예상하지 않은 청크가 있습니다.')
        missing = []
        for ident, record in planned.items():
            cached = present.get(ident)
            if cached is not None:
                if cached['metadata'] != record['metadata'] or cached['text'] != record['text']:
                    _error('INDEX_VERIFICATION_FAILED', '준비 중 검색 자료가 일치하지 않습니다.')
                _vector(cached['embedding'], embedding_dimension)
                continue
            cached_stage = reusable_stage.get(ident)
            if record['embedding'] is None and cached_stage and cached_stage['metadata'] == record['metadata'] and cached_stage['text'] == record['text']:
                record['embedding'] = cached_stage['embedding']
            record['embedding'] = _vector(record['embedding'], embedding_dimension)
            missing.append(record)
        for offset in range(0, len(missing), 100):
            with self.db.connect() as con:
                self._guard(con, user_id, trip_id, job_id, fencing_token)
            batch = missing[offset:offset + 100]
            collection.upsert(ids=[r['id'] for r in batch], embeddings=[r['embedding'] for r in batch],
                              documents=[r['text'] for r in batch], metadatas=[r['metadata'] for r in batch])
        self.verify(generation, collection)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = self._guard(con, user_id, trip_id, job_id, fencing_token)
            if current['version'] != base:
                _error('VERSION_CONFLICT', '예약이 변경되어 검색 자료를 다시 준비해야 합니다.')
            con.execute('UPDATE trip_index_generations SET state=?,verified_at=? WHERE id=? AND state=? AND fencing_token=?', ('ready', utcnow(), generation['id'], 'staged', fencing_token))
            return self._dto(con.execute('SELECT * FROM trip_index_generations WHERE id=?', (generation['id'],)).fetchone())

    def activate(self, user_id, trip_id, generation_id, job_id, fencing_token, *, staged_documents=(), session_id=None):
        """No Chroma calls while SQL writer is held. Ready collections immutable."""
        generation = self.get(user_id, trip_id, generation_id)
        self.verify(generation)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            trip = self._guard(con, user_id, trip_id, job_id, fencing_token)
            current = con.execute('SELECT * FROM trip_index_generations WHERE id=? AND trip_id=?', (generation_id, trip_id)).fetchone()
            if trip['active_index_id'] == generation_id and current['state'] == 'active':
                return self._dto(current)
            if current['state'] != 'ready' or current['job_id'] != job_id or current['fencing_token'] != fencing_token or trip['version'] != current['base_trip_version']:
                _error('VERSION_CONFLICT', '예약 또는 작업 권한이 변경되어 결과를 다시 준비해야 합니다.')
            staged = {item['document_id']: item for item in staged_documents}
            expected = {item['document_id']: item for item in generation['manifest']['documents']}
            for ident, item in staged.items():
                if ident not in expected or item['generation_id'] != expected[ident]['generation_id']:
                    _error('INDEX_VERIFICATION_FAILED', '추출 결과와 검색 세대가 다릅니다.')
                self.repo.activate_generation(user_id, trip_id, ident, item['generation_id'], item['bookings'],
                                              session_id=session_id, connection=con, bump_trip_version=False)
                active = con.execute('SELECT active_generation_id FROM source_documents WHERE id=?', (ident,)).fetchone()
                if active['active_generation_id'] != item['generation_id']:
                    _error('AMBIGUOUS_MATCH', '이전 예약과 새 추출 결과의 대응을 확인해 주세요.')
            actual = {row['id']: row for row in con.execute('SELECT id,active_generation_id,content_hash FROM source_documents WHERE trip_id=? AND deleted_at IS NULL AND active_generation_id IS NOT NULL', (trip_id,))}
            if set(actual) != set(expected) or any(actual[ident]['active_generation_id'] != item['generation_id'] or actual[ident]['content_hash'] != item['content_hash'] for ident, item in expected.items()):
                _error('VERSION_CONFLICT', '원문 활성 세대가 변경되었습니다.')
            now = utcnow()
            if trip['active_index_id']:
                con.execute('UPDATE trip_index_generations SET state=?,retired_at=? WHERE id=? AND state=?', ('retired', now, trip['active_index_id'], 'active'))
            con.execute('UPDATE trip_index_generations SET state=?,activated_at=? WHERE id=?', ('active', now, generation_id))
            con.execute('UPDATE trips SET active_index_id=?,version=version+1,updated_at=? WHERE id=?', (generation_id, now, trip_id))
            return self._dto(con.execute('SELECT * FROM trip_index_generations WHERE id=?', (generation_id,)).fetchone())

    def retire_deleted_trip(self, trip_id):
        """Cleanup worker entry point, only after a durable trip tombstone."""
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT t.id FROM trips t JOIN deletion_tombstones d ON d.target_id=t.id AND d.target_type='trip' WHERE t.id=? AND t.deleted_at IS NOT NULL", (trip_id,)).fetchone()
            if row is None:
                _error('DELETE_NOT_REQUESTED', '삭제 요청이 확인되지 않았습니다.')
            con.execute('UPDATE trips SET active_index_id=NULL WHERE id=?', (trip_id,))
            con.execute("UPDATE trip_index_generations SET state='retired',retired_at=? WHERE trip_id=? AND state IN ('staged','ready','active','failed')", (utcnow(), trip_id))
        return self.cleanup(trip_id)

    def cleanup(self, trip_id=None, *, guard=None):
        """Claim reclaimable rows, delete outside SQL, then record completion.

        A generation in staged/ready state remains a resumable writer checkpoint.
        Expired wall time alone never revokes an in-flight reader reference.
        """
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if guard: guard(con=con)
            # Terminal jobs will never resume a staged/ready checkpoint. Keep
            # unfinished jobs' artifacts even after an arbitrary wall timeout.
            con.execute("UPDATE trip_index_generations SET state='failed' WHERE state IN ('staged','ready') "
                        "AND EXISTS (SELECT 1 FROM jobs j WHERE j.id=trip_index_generations.job_id "
                        "AND j.state IN ('succeeded','partial','failed','cancelled')) "
                        "AND NOT EXISTS (SELECT 1 FROM trips t WHERE t.active_index_id=trip_index_generations.id)")
            for row in ([] if self.db.backend=='postgres' else con.execute('SELECT DISTINCT process_id FROM trip_index_readers').fetchall()):
                try:
                    os.kill(row['process_id'], 0)
                except ProcessLookupError:
                    con.execute('DELETE FROM trip_index_readers WHERE process_id=?', (row['process_id'],))
                except PermissionError:
                    pass
            clauses = ["g.state IN ('retired','deleting','failed')", 'NOT EXISTS (SELECT 1 FROM trips t WHERE t.active_index_id=g.id)',
                       'NOT EXISTS (SELECT 1 FROM trip_index_readers r WHERE r.generation_id=g.id)']
            if self.db.backend=='postgres': clauses.pop()
            args = []
            if trip_id:
                clauses.append('g.trip_id=?')
                args.append(trip_id)
            rows = con.execute('SELECT g.* FROM trip_index_generations g WHERE ' + ' AND '.join(clauses), args).fetchall()
            rows=[row for row in rows if self.db.backend!='postgres' or self.db.can_reclaim(con,row['id'])]
            for row in rows:
                if self.db.backend=='postgres': con.execute('DELETE FROM trip_index_readers WHERE generation_id=?',(row['id'],))
                con.execute('UPDATE trip_index_generations SET state=? WHERE id=?', ('deleting', row['id']))
        deleted = []
        for row in rows:
            if guard: guard()
            try:
                self.client.delete_collection(name=row['collection_name'])
            except Exception as exc:
                if _missing_collection(exc):
                    pass
                else:
                    # A storage/network failure is not evidence of deletion.
                    continue
            with self.db.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                if guard: guard(con=con)
                con.execute('UPDATE trip_index_generations SET state=?,retired_at=? WHERE id=? AND state=?', ('deleted', utcnow(), row['id'], 'deleting'))
            deleted.append(row['id'])
        return deleted
