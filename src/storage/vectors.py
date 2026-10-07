"""pgvector collection interface used by the existing fenced generation manager.

Exact cosine search is appropriate for this bounded private beta; collections
remain immutable after publication. No local Chroma cache is authoritative.
"""
import json
import math
from src.foundation.repository import DomainError


def vector(value):
    value = [float(v) for v in value]
    if not value or len(value) > 4096 or not all(math.isfinite(v) for v in value): raise ValueError('Invalid vector')
    return '[' + ','.join(str(v) for v in value) + ']'


class PostgresVectors:
    def __init__(self, db): self.db = db

    def get_collection(self, name, **kwargs):
        with self.db.connect() as con:
            if not con.execute('SELECT 1 FROM vector_collections WHERE name=?', (name,)).fetchone(): raise KeyError(name)
        return Collection(self.db, name)

    def create_collection(self, name, metadata=None, **kwargs):
        with self.db.connect() as con:
            con.execute('INSERT INTO vector_collections VALUES(?,?)', (name, json.dumps(metadata or {})))
        return Collection(self.db, name)

    def delete_collection(self, name):
        with self.db.connect() as con: con.execute('DELETE FROM vector_collections WHERE name=?', (name,))

    def list_collections(self):
        with self.db.connect() as con: return [Collection(self.db, r['name']) for r in con.execute('SELECT name FROM vector_collections')]


class Collection:
    def __init__(self, db, name): self.db, self.name = db, name

    def count(self):
        with self.db.connect() as con: return con.execute('SELECT count(*) FROM vector_records WHERE collection=?', (self.name,)).fetchone()[0]

    def get(self, include=None, ids=None, **kwargs):
        query = 'SELECT id,metadata_json,document,embedding::text FROM vector_records WHERE collection=?'
        params = [self.name]
        if ids is not None:
            query += ' AND id = ANY(?)'; params.append(ids)
        with self.db.connect() as con: rows = con.execute(query + ' ORDER BY id', params).fetchall()
        return {'ids': [r['id'] for r in rows], 'metadatas': [json.loads(r['metadata_json']) for r in rows],
                'documents': [r['document'] for r in rows], 'embeddings': [json.loads(r['embedding']) for r in rows]}

    def upsert(self, ids, documents, embeddings, metadatas):
        if not len(ids) == len(documents) == len(embeddings) == len(metadatas): raise ValueError('Vector batch lengths differ')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            generation = con.execute('SELECT g.state,t.deleted_at,j.state job_state,j.fencing_token,j.lease_expires_at,g.fencing_token generation_fence '
                'FROM trip_index_generations g JOIN trips t ON t.id=g.trip_id JOIN jobs j ON j.id=g.job_id WHERE g.collection_name=?', (self.name,)).fetchone()
            if generation:
                from src.foundation.repository import utcnow
                if generation['state'] != 'staged' or generation['deleted_at'] or generation['job_state'] != 'running' or generation['fencing_token'] != generation['generation_fence'] or generation['lease_expires_at'] <= utcnow():
                    raise DomainError('LEASE_LOST', '검색 쓰기 권한이 만료되었습니다.', 409)
            for ident, document, embedding, metadata in zip(ids, documents, embeddings, metadatas):
                con.execute('INSERT INTO vector_records VALUES(?,?,?,?,?::extensions.vector) ON CONFLICT(collection,id) '
                    'DO UPDATE SET metadata_json=excluded.metadata_json,document=excluded.document,embedding=excluded.embedding',
                    (self.name, ident, json.dumps(metadata, ensure_ascii=False), document, vector(embedding)))
    add = upsert

    def query(self, query_embeddings, n_results=5, **kwargs):
        results = {'ids': [], 'metadatas': [], 'documents': [], 'distances': []}
        with self.db.connect() as con:
            for embedding in query_embeddings:
                rows = con.execute('SELECT id,metadata_json,document,embedding <=> ?::extensions.vector AS distance '
                    'FROM vector_records WHERE collection=? ORDER BY distance,id LIMIT ?', (vector(embedding), self.name, n_results)).fetchall()
                results['ids'].append([r['id'] for r in rows]); results['metadatas'].append([json.loads(r['metadata_json']) for r in rows])
                results['documents'].append([r['document'] for r in rows]); results['distances'].append([r['distance'] for r in rows])
        return results
