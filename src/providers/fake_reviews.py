"""Deterministic synthetic provider. Never asserts live collection or accuracy."""
from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
import hashlib

from .reviews import ProviderCapabilities,ProviderPage,ReviewCollectionProvider,ReviewProviderError,parse_timestamp


class FakeReviewCollectionProvider(ReviewCollectionProvider):
    name='fake'
    adapter_version='fake-review-v1'
    capabilities=ProviderCapabilities(sort_basis='published_at',source_pagination_visible=True,continuity_verified=True,edited_at=True)

    def __init__(self,pages: list[ProviderPage] | None=None, *, failures: dict | None=None,
                 records: list[dict] | None=None,page_size=50,polls_before_ready=0):
        self.pages=deepcopy(pages)
        self.records=deepcopy(records)
        self.page_size=page_size
        self.failures=failures or {}
        self.polls_before_ready=polls_before_ready
        self.calls=[]
        self._attempts={}
        self._polls={}

    def start(self,request):
        self.calls.append(('start',request.place_identity_id))
        digest=hashlib.sha256(request.request_hash.encode()).hexdigest()[:20]
        return {'run_id':'fake'+digest,'dataset_id':'fakeDataset'+digest,
                'status':'RUNNING' if self.polls_before_ready else 'SUCCEEDED',
                'usage_usd':'0','usage_final':True,'synthetic':True}

    def poll(self,remote,request):
        self.calls.append(('poll',remote['run_id']))
        count=self._polls.get(remote['run_id'],0)+1
        self._polls[remote['run_id']]=count
        return dict(remote,status='SUCCEEDED' if count>=self.polls_before_ready else 'RUNNING')

    def abort(self,remote,request):
        self.calls.append(('abort',remote['run_id']))
        return dict(remote,status='ABORTED',cancellation_requested=True,charges_cancelled=False)

    def delete_dataset(self,remote,request):
        self.calls.append(('delete_dataset',remote['dataset_id']))
        return {'deleted':True,'synthetic':True}

    def delete_run(self,remote,request):
        self.calls.append(('delete_run',remote['run_id']))
        return {'deleted':True,'synthetic':True}

    def fetch_page(self,remote,cursor,request):
        self.calls.append(('page',cursor))
        self._attempts[cursor]=self._attempts.get(cursor,0)+1
        failure=self.failures.get((cursor,self._attempts[cursor]))
        if failure:
            raise failure
        index=0 if cursor is None else int(cursor.split(':')[-1])
        if self.pages is not None:
            if index>=len(self.pages):
                return ProviderPage([],None,True,sort_basis='published_at',continuity_verified=True)
            return deepcopy(self.pages[index])
        records=self.records
        if records is None:
            end=parse_timestamp(request.requested_end)
            records=[{'provider_review_id':f'fake-review-{i}','original_text':'合成レビューです。料理が美味しく、サービスも親切でした。',
                'original_language':'ja','original_language_verified':True,'original_separation_verified':True,
                'text_presence':'present','published_at':(end-timedelta(hours=i+1)).isoformat(),
                'edited_at':None,'date_precision':'exact','rating':5,'rating_scale':5,'synthetic':True}
                for i in range(request.max_review_records)]
        offset=index*self.page_size
        batch=deepcopy(records[offset:offset+self.page_size])
        exhausted=offset+len(batch)>=len(records)
        return ProviderPage(batch,None if exhausted else f'page:{index+1}',exhausted,
            sort_basis='published_at',continuity_verified=True,remote_ref=remote['run_id'],
            capabilities=self.capabilities.to_dict(),usage={'synthetic':True,'records':len(batch)})
