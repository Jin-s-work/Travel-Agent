"""Resumable application operations. Only opaque references live in job checkpoints."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from src.foundation.repository import DomainError, utcnow
from src.foundation.models import BookingCreate
from src.config import EMBEDDING_MODEL, EXTRACTION_MODEL
from .budget import CallContext
from .providers import ProviderResult, provider_scope


def stable_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class Operations:
    def __init__(self, repo, documents, jobs, generations, gateway, *, answer_generator=None, fault_hook=None):
        self.repo, self.documents, self.jobs = repo, documents, jobs
        self.generations, self.gateway = generations, gateway
        self.answer_generator, self.fault_hook = answer_generator, fault_hook
        self.root = documents.settings.artifacts_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        from src.storage.artifacts import Artifacts
        self.artifacts = Artifacts(repo.db) if repo.db.backend=='postgres' else None

    def fault(self, point, job):
        if self.fault_hook:
            self.fault_hook(point, job)

    def context(self, user, trip, job_id=None):
        return CallContext(owner_id=user,scope_id=trip,trip_id=trip,job_id=job_id,actor_id=user)

    def fake_call(self, context, operation, key, value, fn, guard):
        return self.gateway.run(context,operation,key,{'calls':1},
            lambda: ProviderResult(fn(),{'calls':1}),request_hash=stable_hash(value),guard=guard,
            provider='fake',sku=operation)

    def mail_capabilities(self):
        """Availability is descriptive; the paid gateway still checks every call."""
        from src.config import OPENAI_API_KEY, ANSWER_MODEL
        requested = getattr(self.documents.settings, 'mail_analysis_mode', 'auto')
        if requested not in {'auto', 'local', 'ai'}:
            requested = 'local'  # Invalid configuration must never enable billing.
        available, reason = True, None
        if not self.documents.parser:
            if not OPENAI_API_KEY:
                available, reason = False, 'AI_KEY_NOT_CONFIGURED'
            else:
                try:
                    policy = self.gateway.budget.policy
                    prices = [policy.price('openai', model) for model in {EXTRACTION_MODEL, EMBEDDING_MODEL, ANSWER_MODEL}]
                    with self.repo.db.connect() as con:
                        currencies = {price['currency'] for price in prices}
                        controls = con.execute('SELECT currency,halted FROM cost_controls').fetchall()
                    if policy.config.get('halted') or any(row['halted'] and row['currency'] in currencies for row in controls):
                        available, reason = False, 'AI_BUDGET_PAUSED'
                    elif any(any(policy.config['limits'][currency][key] <= 0 for key in ('user_daily','user_monthly','global_daily','global_monthly')) for currency in currencies):
                        available, reason = False, 'AI_BUDGET_NOT_CONFIGURED'
                    from src.operations.controls import external_guard
                    external_guard(self.repo.db, 'openai')
                except DomainError as error:
                    available, reason = False, error.code
        mode = 'ai' if requested != 'local' and available else 'local'
        return {'analysis_mode': mode, 'requested_mode': requested,
                'label': 'AI 상세 분석' if mode == 'ai' else '기본 분석',
                'semantic_search': mode == 'ai', 'ai_available': available,
                'reason_code': reason if requested != 'local' else 'LOCAL_MODE_SELECTED',
                'external_calls': mode == 'ai', 'review_required': True,
                'supported_formats': ['.txt', '.eml'],
                'supported_questions': ['날짜별 예약', '예약 시간', '예약번호', '주소', '원문 취소 규정'],
                'limitations': [] if mode == 'ai' else ['명시된 날짜·장소와 예약 항목을 기본 규칙으로 추출합니다.', '복잡한 형식은 직접 확인이 필요하며 모든 문장을 이해하는 AI 분석은 아닙니다.']}

    def structured_only(self):
        # Index-only injected maintenance fixtures do not provide a gateway.
        return hasattr(self.gateway, 'budget') and self.mail_capabilities()['analysis_mode'] == 'local'

    def activate_structured(self, job, ctx, document_id, generation_id, bookings):
        """Commit facts under the same job/deletion fence without invented vectors."""
        user, trip = job['actor_id'], job['trip_id']
        with self.repo.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.document_guard(ctx, user, trip, document_id)(con=con)
            self.repo.activate_generation(user, trip, document_id, generation_id, bookings,
                session_id=job['session_id'], connection=con)
            active = con.execute('SELECT active_generation_id FROM source_documents WHERE id=?', (document_id,)).fetchone()
            if active['active_generation_id'] != generation_id:
                raise DomainError('AMBIGUOUS_MATCH', '이전 예약과 기본 분석 결과의 대응을 확인해 주세요.', 409)
            con.execute("UPDATE source_documents SET status='needs_review' WHERE id=?", (document_id,))
            self._retire_search_pointer(con, trip)

    def _retire_search_pointer(self, con, trip):
        row = con.execute('SELECT active_index_id FROM trips WHERE id=?', (trip,)).fetchone()
        if row and row['active_index_id']:
            con.execute("UPDATE trip_index_generations SET state='retired',retired_at=? WHERE id=? AND state='active'", (utcnow(), row['active_index_id']))
            con.execute('UPDATE trips SET active_index_id=NULL WHERE id=?', (trip,))

    def extract(self, call_context, key, raw, guard):
        if self.documents.parser:
            return self.fake_call(call_context,'extract',key,raw,lambda:self.documents.parser(raw),guard)
        from src.parser import parse_document_reservations
        with provider_scope(self.gateway,call_context,guard=guard,call_prefix=key):
            return parse_document_reservations(raw)

    def embed(self, call_context, key, texts, guard, *, query=False):
        if self.documents.embedder:
            return self.fake_call(call_context,'query_embed' if query else 'embed',key,texts,lambda:self.documents.embedder(texts),guard)
        from src.embedder import embed_texts
        with provider_scope(self.gateway,call_context,guard=guard,call_prefix=key):
            return embed_texts(texts,show_progress=False)

    def generate(self, user, trip, request_id, question, hits, guard):
        context=self.context(user,trip)
        if self.answer_generator:
            return self.fake_call(context,'generate',request_id,[question,hits],lambda:self.answer_generator(question,hits),guard)
        from src.rag import generate
        with provider_scope(self.gateway,context,guard=guard,call_prefix=request_id):
            return generate(question,hits)

    def document_guard(self, ctx, user, trip, document_id):
        """A live job/trip does not authorize a deleted document's next call."""
        def guard(*, con=None):
            def check(connection):
                ctx.guard(con=connection)
                # Distinguish a removed file from revoked access/deleted trip,
                # so only this file is cancelled in a multi-file submission.
                try:
                    return dict(self.repo._document(connection,user,trip,document_id))
                except DomainError as exc:
                    if exc.code != 'NOT_FOUND':
                        raise
                    self.repo._trip(connection,user,trip)
                    raise DomainError('DOCUMENT_DELETED','삭제된 메일의 처리를 중단했습니다.',409) from None
            if con is not None:
                return check(con)
            with self.repo.db.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return check(connection)
        return guard

    def artifact(self, job, key, value, ctx, *, document_id=None):
        # Serialize before taking SQLite's short write lock. Prevent a worker
        # returning after deletion from re-creating a private artifact directory.
        data=json.dumps(value,ensure_ascii=False).encode()
        with self.repo.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self.jobs.guard(job['id'],job['fencing_token'],con=con)
            if document_id:
                self.document_guard(ctx,job['actor_id'],job['trip_id'],document_id)(con=con)
            if self.artifacts:
                return self.artifacts.put(con,job['trip_id']+'/'+job['id']+'/'+key+'.json',job['trip_id'],data)
            directory=self.root/job['trip_id']/job['id']
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            path=directory/(key+'.json'); temporary=directory/(str(uuid.uuid4())+'.tmp')
            with temporary.open('wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            temporary.chmod(0o600); os.replace(temporary,path)
        return str(path.relative_to(self.root))

    def read_artifact(self, ref):
        if self.artifacts:
            data=self.artifacts.read(ref)
            if data is None: raise DomainError('CHECKPOINT_INVALID','저장된 작업 참조를 확인할 수 없습니다.',409)
            return json.loads(data)
        path=(self.root/ref).resolve()
        if not path.is_relative_to(self.root):
            raise DomainError('CHECKPOINT_INVALID','저장된 작업 참조를 확인할 수 없습니다.',409)
        return json.loads(path.read_text())

    def __call__(self, job, ctx):
        if job['operation']=='documents':
            return self.process_documents(job,ctx)
        if job['operation']=='reindex':
            return self.reindex(job,ctx)
        if job['operation']=='cleanup_trip':
            return self.cleanup_trip(job,ctx)
        raise DomainError('OPERATION_DISABLED','이 작업은 아직 제공하지 않습니다.',409)

    def process_documents(self, job, ctx):
        user,trip=job['actor_id'],job['trip_id']
        payload=job['payload']; cp=dict(job['checkpoint']); units=dict(cp.get('documents',{}))
        mode = cp.get('analysis_mode') or self.mail_capabilities()['analysis_mode']
        parse_version = 'local-mail-v1' if mode == 'local' else 'foundation-v2'
        signature=stable_hash({'parser':parse_version,'chunker':'v1','extraction_model':EXTRACTION_MODEL if mode == 'ai' else None,'embedding_model':EMBEDDING_MODEL if mode == 'ai' else None})
        if cp.get('pipeline_signature') and cp['pipeline_signature']!=signature:
            raise DomainError('CHECKPOINT_INCOMPATIBLE','모델·추출 규칙이 변경되었습니다. 새 분석 작업으로 실행해 주세요.',409)
        ctx.checkpoint({'pipeline_signature':signature, 'analysis_mode':mode})
        accepted=payload.get('accepted',[])
        if not units and payload.get('resume_from'):
            previous=self.jobs.get(payload['resume_from'],user,job['session_id'])
            if previous['trip_id']!=trip: raise DomainError('NOT_FOUND','작업을 찾을 수 없습니다.',404)
            with self.repo.db.connect() as con:
                old=con.execute('SELECT checkpoint_json FROM jobs WHERE id=?',(payload['resume_from'],)).fetchone()
            previous_cp=json.loads(old['checkpoint_json'])
            previous_units=previous_cp.get('documents',{}) if previous_cp.get('pipeline_signature')==signature else {}
            for entry in accepted:
                old_unit=previous_units.get(entry['document_id'],{})
                units[entry['document_id']]={key:old_unit[key] for key in ('extracted_ref','vectors_ref','review_reasons') if key in old_unit}
            ctx.checkpoint({'documents':units},stage='resuming_files',done=0,total=len(accepted))
        results=[]
        for index, entry in enumerate(accepted):
            ctx.guard(); did=entry['document_id']; saved=dict(units.get(did,{}))
            item={**entry,'state':'running','analysis_mode':mode,'search_mode':'structured' if mode == 'local' else 'semantic'}
            doc_guard=self.document_guard(ctx,user,trip,did)
            # The pointer is authoritative if a crash happened after SQL commit.
            try:
                doc=doc_guard()
            except DomainError as exc:
                if exc.code!='DOCUMENT_DELETED': raise
                item.update(state='cancelled',error_code='DOCUMENT_DELETED',activated=False)
                saved.update(state='cancelled',result=item); units[did]=saved; results.append(item)
                ctx.checkpoint({'documents':units,'files':results},stage='document_complete',done=index+1,total=len(accepted))
                continue
            if saved.get('generation_id') and doc['active_generation_id']==saved['generation_id']:
                item.update(state='needs_review' if doc['status']=='needs_review' else 'succeeded',bookings_count=len([b for b in self.repo.list_bookings(user,trip) if b['document_id']==did]))
                saved['state']=item['state']
            elif saved.get('state') in {'failed','succeeded','needs_review','cancelled'}:
                item.update(saved.get('result',{'state':saved['state']}))
            else:
                try:
                    if not saved.get('generation_id'):
                        with self.repo.db.connect() as con:
                            con.execute('BEGIN IMMEDIATE'); self.jobs.guard(job['id'],job['fencing_token'],con=con)
                            generation=self.repo.create_generation(user,trip,did,parse_version=parse_version,connection=con)
                            saved['generation_id']=generation['id']; units[did]=saved
                            con.execute('UPDATE document_generations SET job_id=? WHERE id=?',(job['id'],generation['id']))
                            self.jobs.checkpoint(job['id'],job['fencing_token'],{'documents':units},stage='extracting',done=index,total=len(accepted),con=con)
                    if not saved.get('extracted_ref'):
                        raw=self.documents.raw_text(doc)
                        if mode == 'local':
                            from src.foundation.local_mail import parse_local_document
                            doc_guard()
                            parsed, reasons = parse_local_document(raw)
                            saved['review_reasons'] = reasons
                        else:
                            parsed=self.extract(self.context(user,trip,job['id']),did+':extract',raw,doc_guard)
                        if not isinstance(parsed,list) or not parsed:
                            raise DomainError('EXTRACTION_INVALID','예약 정보를 확인할 수 없습니다.')
                        cleaned=[]
                        for value in parsed:
                            value=dict(value)
                            if 'type' in value:
                                value.setdefault('kind',value.pop('type'))
                            cleaned.append(BookingCreate.model_validate(value).model_dump())
                        saved['extracted_ref']=self.artifact(job,did+'-facts',cleaned,ctx,document_id=did)
                        units[did]=saved; ctx.checkpoint({'documents':units},stage='reviewing_facts' if mode == 'local' else 'embedding',done=index,total=len(accepted))
                        self.fault('after_extraction',job)
                    cleaned=self.read_artifact(saved['extracted_ref'])
                    if mode == 'local':
                        ctx.checkpoint({'documents':units},stage='activating',done=index,total=len(accepted))
                        self.fault('after_ready',job)
                        self.activate_structured(job,ctx,did,saved['generation_id'],cleaned)
                        item['review_reasons'] = saved.get('review_reasons', ['BASIC_EXTRACTION_REVIEW'])
                    else:
                        if not saved.get('vectors_ref'):
                            from src.indexer import _chunk
                            texts=_chunk(json.dumps(cleaned,ensure_ascii=False))
                            # Each batch has its own result artifact and billing identity.
                            vectors=[]
                            for batch_no,start in enumerate(range(0,len(texts),32)):
                                vectors.extend(self.embed(self.context(user,trip,job['id']),did+':embed:'+str(batch_no),texts[start:start+32],doc_guard))
                                self.fault('after_embedding_batch',job)
                            if len(vectors)!=len(texts) or not vectors or any(not v for v in vectors):
                                raise DomainError('EMBEDDING_INVALID','검색 자료를 완성하지 못했습니다.')
                            saved['vectors_ref']=self.artifact(job,did+'-vectors',{'texts':texts,'vectors':vectors,'model':EMBEDDING_MODEL},ctx,document_id=did)
                            units[did]=saved; ctx.checkpoint({'documents':units},stage='building_index',done=index,total=len(accepted))
                        material=self.read_artifact(saved['vectors_ref'])
                        generation=self.generations.build(user,trip,job['id'],job['fencing_token'],[
                            {'document_id':did,'generation_id':saved['generation_id'],'content_hash':doc['content_hash'],
                             'parse_version':'foundation-v2','chunks':[{'text':t,'embedding':v} for t,v in zip(material['texts'],material['vectors'])]}],
                             embedding_model=material['model'],embedding_dimension=len(material['vectors'][0]))
                        saved['index_id']=generation['id']; units[did]=saved
                        ctx.checkpoint({'documents':units},stage='activating',done=index,total=len(accepted))
                        self.fault('after_ready',job)
                        self.generations.activate(user,trip,generation['id'],job['id'],job['fencing_token'],staged_documents=[
                            {'document_id':did,'generation_id':saved['generation_id'],'bookings':cleaned}],session_id=job['session_id'])
                    self.fault('after_activation',job)
                    doc=doc_guard()
                    item.update(state='needs_review' if doc['status']=='needs_review' else 'succeeded',bookings_count=len(cleaned))
                except Exception as exc:
                    code=exc.code if isinstance(exc,DomainError) else 'PROCESSING_FAILED'
                    if code=='NOT_FOUND':
                        # A SQL build/activation may observe the deletion before
                        # the provider/artifact guard does.
                        try: doc_guard()
                        except DomainError as scoped: code=scoped.code
                    if code in {'LEASE_LOST','JOB_CANCELLED','SESSION_EXPIRED','NOT_FOUND','UNAUTHORIZED','AUTH_REQUIRED','ACCESS_REVOKED'}:
                        raise
                    item.update(state='cancelled' if code=='DOCUMENT_DELETED' else 'failed',error_code=code)
                    if code=='DOCUMENT_DELETED': item['activated']=False
                    if saved.get('generation_id') and code!='DOCUMENT_DELETED':
                        if code=='AMBIGUOUS_MATCH':
                            with self.repo.db.connect() as con:
                                con.execute('BEGIN IMMEDIATE'); self.jobs.guard(job['id'],job['fencing_token'],con=con)
                                con.execute('UPDATE document_generations SET status=?,extracted_json=?,error_code=?,completed_at=? WHERE id=?',
                                    ('needs_review',json.dumps(cleaned,ensure_ascii=False),code,utcnow(),saved['generation_id']))
                                con.execute('UPDATE source_documents SET status=?,updated_at=? WHERE id=?',('needs_review',utcnow(),did))
                            item.update(state='needs_review',activated=False,bookings_count=0)
                        else:
                            with self.repo.db.connect() as con:
                                con.execute('BEGIN IMMEDIATE'); self.jobs.guard(job['id'],job['fencing_token'],con=con)
                                self.repo.fail_generation(user,trip,did,saved['generation_id'],code,connection=con)
                saved['state']=item['state']
            saved['result']=item; units[did]=saved; results.append(item)
            ctx.checkpoint({'documents':units,'files':results},stage='document_complete',done=index+1,total=len(accepted))
        successes=sum(item['state'] in {'succeeded','needs_review'} and item.get('activated',True) for item in results)
        rejected=payload.get('rejected',[])
        duplicates=payload.get('duplicates',[])
        usable_duplicates=0
        for duplicate in duplicates:
            try:
                usable_duplicates+=bool(self.repo.get_document(user,trip,duplicate['document_id']).get('active_generation_id'))
            except DomainError:
                pass
        state=('succeeded' if successes==len(results) and not rejected and all(x['state']=='succeeded' or mode=='local' and x['state']=='needs_review' for x in results)
               else 'partial' if successes or usable_duplicates else 'failed')
        if not results: state='succeeded' if duplicates and usable_duplicates==len(duplicates) and not rejected else 'partial' if usable_duplicates else 'failed'
        if results and all(item['state']=='cancelled' for item in results) and not rejected: state='cancelled'
        try:
            self.generations.cleanup(trip)
        except Exception:
            pass  # Reclamation is retried on startup/read completion; activation already committed.
        return {'state':state,'result':{**payload,'files':results,'analysis_mode':mode,'review_required':any(item['state']=='needs_review' for item in results)}}

    def reindex(self, job, ctx):
        user,trip=job['actor_id'],job['trip_id']; documents=[]
        cp=dict(job['checkpoint'])
        signature=stable_hash({'embedding_model':EMBEDDING_MODEL,'chunker':'v1'})
        if cp.get('pipeline_signature') and cp['pipeline_signature']!=signature:
            raise DomainError('CHECKPOINT_INCOMPATIBLE','검색 모델·분할 규칙이 변경되었습니다. 새 복구 작업으로 실행해 주세요.',409)
        if cp.get('reindex_refs') and not cp.get('pipeline_signature'):
            raise DomainError('CHECKPOINT_INCOMPATIBLE','이전 검색 체크포인트의 모델을 확인할 수 없습니다. 새 복구 작업으로 실행해 주세요.',409)
        ctx.checkpoint({'pipeline_signature':signature})
        self.cleanup_documents(job,ctx)
        # An explicitly injected adapter can rebuild an offline/test index.
        # Without one, local mode must never fall through to paid embedding.
        if self.structured_only() and self.documents.embedder is None:
            with self.repo.db.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                self.jobs.guard(job['id'],job['fencing_token'],con=con)
                self.repo._trip(con,user,trip)
                self._retire_search_pointer(con,trip)
            self.generations.cleanup(trip)
            return {'state':'succeeded','result':{'index_id':None,'search_mode':'structured'}}
        # Healthy source vectors can be copied without any external call.
        trip_row=self.repo.get_trip(user,trip)
        previous=self.generations.get(user,trip,trip_row['active_index_id']) if trip_row.get('active_index_id') else None
        if previous and previous['embedding_model']==EMBEDDING_MODEL:
            try:
                if previous['job_id']==job['id'] and trip_row['version']==previous['base_trip_version']+1:
                    # A process may die after the pointer commit but before the
                    # final job event. The active manifest is the checkpoint.
                    self.generations.verify(previous)
                    ctx.guard()
                    return {'state':'succeeded','result':{'index_id':previous['id']}}
                generation=self.generations.build(user,trip,job['id'],job['fencing_token'],[],
                    embedding_model=previous['embedding_model'],embedding_dimension=previous['embedding_dimension'])
                ctx.checkpoint({'index_id':generation['id']},stage='activating',done=0,total=0)
                self.fault('after_ready',job)
                self.generations.activate(user,trip,generation['id'],job['id'],job['fencing_token'],session_id=job['session_id'])
                self.fault('after_activation',job)
                return {'state':'succeeded','result':{'index_id':generation['id']}}
            except DomainError as exc:
                if exc.code not in {'SEARCH_REBUILDING','INDEX_REBUILD_INPUT_REQUIRED','INDEX_VERIFICATION_FAILED'}: raise
        from src.indexer import _chunk
        docs=[d for d in self.repo.list_documents(user,trip) if d.get('active_generation_id')]
        refs=dict(cp.get('reindex_refs',{})); dimension=0
        for index,doc in enumerate(docs):
            ctx.guard(); did=doc['id']; gid=doc['active_generation_id']
            doc_guard=self.document_guard(ctx,user,trip,did)
            try:
                doc_guard()
                with self.repo.db.connect() as con:
                    row=con.execute('SELECT extracted_json FROM document_generations WHERE id=?',(gid,)).fetchone()
                if not row or not row['extracted_json']:
                    raise DomainError('REEXTRACTION_REQUIRED','원문의 재분석이 필요합니다.',409)
                texts=_chunk(row['extracted_json'])
                cached=refs.get(gid)
                if cached and (not isinstance(cached,dict) or cached.get('model')!=EMBEDDING_MODEL):
                    raise DomainError('CHECKPOINT_INCOMPATIBLE','저장된 임베딩 모델이 현재 설정과 다릅니다. 새 복구 작업으로 실행해 주세요.',409)
                if isinstance(cached,dict) and cached.get('model')==EMBEDDING_MODEL:
                    vectors=self.read_artifact(cached['ref'])
                    if not vectors or len(vectors[0])!=cached.get('dimension'):
                        raise DomainError('EMBEDDING_INVALID','저장된 임베딩 차원을 확인할 수 없습니다.')
                else:
                    vectors=self.embed(self.context(user,trip,job['id']),gid+':repair:'+EMBEDDING_MODEL,texts,doc_guard)
                    if not vectors or len(vectors)!=len(texts):
                        raise DomainError('EMBEDDING_INVALID','검색 자료를 완성하지 못했습니다.')
                    ref=self.artifact(job,gid+'-'+stable_hash(EMBEDDING_MODEL)[:12]+'-repair',vectors,ctx,document_id=did)
                    refs[gid]={'ref':ref,'model':EMBEDDING_MODEL,'dimension':len(vectors[0])}
                    ctx.checkpoint({'reindex_refs':refs},stage='rebuilding_index',done=index+1,total=len(docs))
                if len(vectors)!=len(texts):
                    raise DomainError('EMBEDDING_INVALID','검색 청크 수가 일치하지 않습니다.')
                dimension=len(vectors[0])
                documents.append({'document_id':did,'generation_id':gid,'content_hash':doc['content_hash'],'chunks':[{'text':t,'embedding':v} for t,v in zip(texts,vectors)]})
            except DomainError as exc:
                if exc.code!='DOCUMENT_DELETED': raise
                continue
        if not dimension:
            # An empty collection has no billable embedding; dimension is a
            # manifest property, not a fabricated booking or vector.
            with self.repo.db.connect() as con:
                previous=con.execute('SELECT embedding_dimension FROM trip_index_generations WHERE trip_id=? ORDER BY created_at DESC LIMIT 1',(trip,)).fetchone()
            dimension=previous['embedding_dimension'] if previous else 1
        generation=self.generations.build(user,trip,job['id'],job['fencing_token'],documents,embedding_model=EMBEDDING_MODEL,embedding_dimension=dimension)
        ctx.checkpoint({'index_id':generation['id']},stage='activating',done=len(docs),total=len(docs))
        self.fault('after_ready',job)
        self.generations.activate(user,trip,generation['id'],job['id'],job['fencing_token'],session_id=job['session_id'])
        self.fault('after_activation',job)
        return {'state':'succeeded','result':{'index_id':generation['id']}}

    def cleanup_documents(self, job, ctx):
        if getattr(self.documents,'objects',None):
            ctx.guard()
            with self.repo.db.connect() as con:
                docs=con.execute('SELECT opaque_path FROM source_documents WHERE trip_id=? AND deleted_at IS NOT NULL',(job['trip_id'],)).fetchall()
            for doc in docs:
                if doc['opaque_path'].startswith('supabase:'): self.documents.objects.remove(doc['opaque_path'][9:])
        with self.repo.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self.jobs.guard(job['id'],job['fencing_token'],con=con)
            for doc in con.execute('SELECT id,opaque_path FROM source_documents WHERE trip_id=? AND deleted_at IS NOT NULL',(job['trip_id'],)).fetchall():
                path=Path(doc['opaque_path']).resolve()
                if path.is_relative_to(self.documents.settings.documents_dir.resolve()): path.unlink(missing_ok=True)
                con.execute('UPDATE deletion_tombstones SET completed_at=? WHERE target_type=? AND target_id=?',(utcnow(),'document',doc['id']))

    def cleanup_trip(self, job, ctx):
        ctx.guard(); trip=job['trip_id']
        self.generations.retire_deleted_trip(trip)
        with self.repo.db.connect() as con:
            remaining=con.execute("SELECT 1 FROM trip_index_generations WHERE trip_id=? AND state!='deleted' LIMIT 1",(trip,)).fetchone()
        if remaining:
            from .dispatcher import RetryableJobError
            raise RetryableJobError('WAITING_FOR_READERS',retry_after=2)
        if getattr(self.documents,'objects',None):
            with self.repo.db.connect() as con:
                keys=con.execute("SELECT key FROM cloud_objects WHERE trip_id=? AND state!='deleted'",(trip,)).fetchall()
            for row in keys:
                ctx.guard(); self.documents.objects.remove(row['key'])
        # Cleanup is serialized after writers; late external returns cannot
        # regain their SQL fence or artifact write authorization.
        with self.repo.db.connect() as con:
            con.execute('BEGIN IMMEDIATE'); self.jobs.guard(job['id'],job['fencing_token'],con=con)
            if self.artifacts: self.artifacts.delete_scope(con,trip)
            for root in (self.documents.settings.documents_dir.resolve(),self.root):
                target=(root/trip).resolve()
                if target.is_relative_to(root): shutil.rmtree(target,ignore_errors=False) if target.exists() else None
            con.execute('UPDATE document_generations SET extracted_json=NULL WHERE document_id IN (SELECT id FROM source_documents WHERE trip_id=?)',(trip,))
            con.execute("UPDATE bookings SET extracted_json='{}', effective_json='{}', conflicts_json='[]' WHERE trip_id=?",(trip,))
            con.execute('DELETE FROM booking_events WHERE trip_id=?',(trip,))
            con.execute('DELETE FROM trip_stops WHERE trip_id=?',(trip,))
            con.execute("UPDATE trips SET title='',conditions_json='{}',start_date='',end_date='' WHERE id=?",(trip,))
            con.execute("UPDATE source_documents SET display_filename='',content_hash='',opaque_path='' WHERE trip_id=?",(trip,))
            con.execute("UPDATE trip_index_generations SET source_manifest='{}' WHERE trip_id=? AND state='deleted'",(trip,))
            con.execute("UPDATE bookings SET kind=NULL,date_start=NULL,date_end=NULL,stable_item_key=NULL WHERE trip_id=?",(trip,))
            con.execute('DELETE FROM booking_overrides WHERE booking_id IN (SELECT id FROM bookings WHERE trip_id=?)',(trip,))
            from src.discovery.schema import scrub_trip
            scrub_trip(con,trip)
            from src.recommendations.schema import scrub_trip as scrub_recommendations
            scrub_recommendations(con,trip)
            from src.itineraries.schema import scrub_trip as scrub_itineraries
            scrub_itineraries(con,trip)
            from src.travel_tools.schema import scrub_trip as scrub_tools
            scrub_tools(con,trip)
            from src.product.schema import scrub_trip as scrub_product
            scrub_product(con,trip)
            con.execute("UPDATE jobs SET checkpoint_json='{}',payload_json='{}',result_json='{}' WHERE trip_id=? AND id!=?",(trip,job['id']))
            con.execute('UPDATE deletion_tombstones SET completed_at=? WHERE trip_id=?',(utcnow(),trip))
        return {'state':'succeeded','result':{}}
