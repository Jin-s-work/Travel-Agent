"""Bounded ingestion; durable production execution lives in reliability.handlers."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid
from .repository import DomainError


class DocumentService:
    def __init__(self, repository, settings, parser=None, embedder=None, vector_factory=None):
        self.repo, self.settings = repository, settings
        self.parser, self.embedder, self.vector_factory = parser, embedder, vector_factory
        self.objects = None
        self._vector_lock = threading.Lock()
        self._vectors = {}
        self._processing_lock = threading.Lock()  # phase 01: one process, no durable dispatcher

    def vector(self, trip_id):
        with self._vector_lock:
            if trip_id not in self._vectors:
                if self.vector_factory:
                    self._vectors[trip_id] = self.vector_factory(trip_id)
                else:
                    from src.store import VectorStore
                    self._vectors[trip_id] = VectorStore(self.settings.vectors_dir, 'trip-' + trip_id)
            return self._vectors[trip_id]

    def validate(self, filename, mime, data):
        if any(char in filename for char in ('/', '\\')) or any(ord(char)<32 or ord(char)==127 for char in filename):
            return 'UNSAFE_FILENAME'
        ext = Path(filename).suffix.lower()
        if ext not in {'.txt','.eml'}:
            return 'UNSUPPORTED_EXTENSION'
        allowed = {'text/plain','application/octet-stream'} if ext == '.txt' else {'message/rfc822','text/plain','application/octet-stream'}
        if mime.split(';',1)[0].lower() not in allowed:
            return 'MIME_MISMATCH'
        if not data:
            return 'EMPTY_FILE'
        if len(data) > self.settings.max_upload_bytes:
            return 'FILE_TOO_LARGE'
        if b'\x00' in data:
            return 'INVALID_CONTENT'
        if ext == '.txt':
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                return 'INVALID_ENCODING'
            if text.lstrip().lower().startswith(('<!doctype html','<html','<script','%pdf')):
                return 'INVALID_CONTENT'
        else:
            from email import policy
            from email.parser import BytesParser
            msg = BytesParser(policy=policy.default).parsebytes(data)
            if not any(msg.get(key) for key in ('From','To','Subject','Date','MIME-Version')):
                return 'INVALID_EMAIL'
        return None

    def save(self, user_id, trip_id, filename, data):
        if self.objects:
            return self.objects.save(self.repo,user_id,trip_id,filename,data)
        content_hash = hashlib.sha256(data).hexdigest()
        path = temporary = None
        try:
            # Bounded local I/O only. The shared SQL writer protects the gap
            # between rename and registration against deletion/maintenance.
            # A crash can leave an unreferenced file, never a partial referenced
            # original; startup reconciliation removes aged server-ID orphans.
            with self.repo.db.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                self.repo._trip(con,user_id,trip_id)
                root = self.settings.documents_dir.resolve()
                root.mkdir(parents=True,exist_ok=True,mode=0o700)
                directory = root / trip_id
                directory.mkdir(exist_ok=True,mode=0o700)
                path = directory / (str(uuid.uuid4()) + Path(filename).suffix.lower())
                temporary = directory / ('.' + str(uuid.uuid4()) + '.tmp')
                descriptor = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'wb') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary,path)
                directory_fd = os.open(directory,os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                # Explicit test injection only; no public route/env bypass.
                hook = getattr(self,'raw_save_fault_hook',None)
                if hook:
                    hook('after_raw_rename',path)
                doc = self.repo.create_document(user_id,trip_id,filename,str(path),content_hash,connection=con)
                if doc['opaque_path'] != str(path):
                    path.unlink(missing_ok=True)
                return doc
        except Exception:
            if path is not None:
                # A storage/commit exception may leave the commit outcome
                # uncertain. Never remove an original that SQL now references.
                try:
                    with self.repo.db.connect() as con:
                        referenced=con.execute('SELECT 1 FROM source_documents WHERE opaque_path=?',(str(path),)).fetchone()
                    if referenced is None:
                        path.unlink(missing_ok=True)
                except Exception:
                    pass  # Conservatively retain; startup reconciliation checks.
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def process(self, user_id, trip_id, job_id, accepted, rejected, session_id=None):
        # Kept only for the previous phase's fully injected migration fixtures.
        # Real imports must enqueue through an authenticated durable API; this
        # method has no lease, cost reservation, or generation activation fence.
        if not (self.parser and self.embedder and self.vector_factory):
            raise DomainError('LEGACY_PROCESS_DISABLED','직접 메일 처리는 중단되었습니다. 로그인한 여행 화면에서 재분석 작업을 실행해 주세요.',409)
        results = []
        base = self.repo.get_receipt(user_id,trip_id,job_id).get('result',{})
        with self._processing_lock:
            try:
                self.repo.update_receipt(user_id,trip_id,job_id,'running',{**base,'files':results,'rejected':rejected})
                for entry in accepted:
                    item = {**entry,'state':'running'}
                    results.append(item)
                    generation = None
                    try:
                        self.ensure_active_user(user_id,session_id)
                        doc = self.repo.get_document(user_id,trip_id,entry['document_id'])
                        generation = self.repo.create_generation(user_id,trip_id,doc['id'],parse_version='foundation-v1')
                        from src.loader import read_email_file
                        parsed = self.parser(read_email_file(Path(doc['opaque_path'])))
                        if not isinstance(parsed,list) or not parsed:
                            raise ValueError('No booking facts')
                        # Validate before any activation; repository revalidates within transaction.
                        from .models import BookingCreate
                        cleaned = []
                        for record in parsed:
                            record = dict(record)
                            if 'type' in record:
                                record.setdefault('kind',record.pop('type'))
                            cleaned.append(BookingCreate.model_validate(record).model_dump())
                        from src.indexer import _chunk
                        texts = _chunk(json.dumps(cleaned,ensure_ascii=False))
                        vectors = self.embedder(texts)
                        if len(vectors) != len(texts) or any(not vector for vector in vectors):
                            raise ValueError('Incomplete embedding')
                        self.vector(trip_id).add(ids=[f"{generation['id']}:{index}" for index in range(len(texts))], documents=texts, embeddings=vectors,
                            metadatas=[{'document_id':doc['id'],'generation_id':generation['id']} for _ in texts])
                        self.ensure_active_user(user_id,session_id)
                        bookings = self.repo.activate_generation(user_id,trip_id,doc['id'],generation['id'],cleaned,session_id=session_id)
                        state='needs_review' if self.repo.get_document(user_id,trip_id,doc['id'])['status']=='needs_review' else 'succeeded'
                        item.update(state=state,bookings_count=len(bookings))
                    except Exception as exc:
                        code = exc.code if isinstance(exc,DomainError) else 'PROCESSING_FAILED'
                        item.update(state='failed',error_code=code)
                        if generation:
                            try:
                                self.repo.fail_generation(user_id,trip_id,entry['document_id'],generation['id'],code)
                            except DomainError:
                                pass
                    self.repo.update_receipt(user_id,trip_id,job_id,'running',{**base,'files':results,'rejected':rejected})
                succeeded = sum(item['state'] in {'succeeded','needs_review'} for item in results)
                state = 'succeeded' if succeeded == len(results) and not rejected and all(item['state']=='succeeded' for item in results) else ('partial' if succeeded else 'failed')
                self.repo.update_receipt(user_id,trip_id,job_id,state,{**base,'files':results,'rejected':rejected})
            except DomainError:
                # Deleted trips/users cannot receive late activation or be exposed by polling.
                return

    def ensure_active_user(self, user_id, session_id=None):
        with self.repo.db.connect() as con:
            row = con.execute("SELECT id FROM users WHERE id=? AND status='active'",(user_id,)).fetchone()
            if not row:
                raise DomainError('ACCESS_REVOKED','서비스 이용 권한이 회수되었습니다.',403)
            if session_id:
                from .auth import now
                session=con.execute('SELECT s.id FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.user_id=? AND s.epoch=u.session_epoch AND s.expires_at>?', (session_id,user_id,now().isoformat())).fetchone()
                if not session:
                    raise DomainError('AUTH_REQUIRED','처리 중 세션이 만료되거나 회수되었습니다.',401)

    def safe_path(self, document):
        path = Path(document['opaque_path']).resolve()
        if not path.is_relative_to(self.settings.documents_dir.resolve()) or not path.is_file():
            raise DomainError('NOT_FOUND','원문을 찾을 수 없습니다.',404)
        return path

    def read(self, document):
        return self.objects.read(document) if self.objects else self.safe_path(document).read_bytes()

    def raw_text(self, document):
        from src.loader import read_email_bytes
        return read_email_bytes(self.read(document),document['display_filename'])
