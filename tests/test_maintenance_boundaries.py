"""No-network regression tests for backup side-effect fences."""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.foundation.repository import DomainError
from src.operations.remote import ObjectStore


def test_backup_upload_losing_lease_cannot_start_verification(tmp_path):
    state={'leader':True,'calls':[]}
    def guard():
        if not state['leader']:raise DomainError('LEASE_LOST','fixture',409)
    class Storage:
        def put_object(self,**kwargs):
            state['calls'].append('put');state['leader']=False
        def head_object(self,**kwargs):state['calls'].append('head')
    path=tmp_path/'fixture.enc';path.write_bytes(b'synthetic encrypted fixture')
    store=ObjectStore(Storage(),'fixture','test')
    with pytest.raises(DomainError):store.upload(path,'snapshot-fixture.enc',guard=guard)
    assert state['calls']==['put']


def test_backup_prune_fences_each_object_after_loss():
    state={'leader':True,'deleted':[]}
    def guard():
        if not state['leader']:raise DomainError('LEASE_LOST','fixture',409)
    class Storage:
        def get_paginator(self,name):return self
        def paginate(self,**kwargs):
            return [{'Contents':[{'Key':'test/snapshot-'+str(i)+'.enc','LastModified':datetime.now(timezone.utc)-timedelta(days=20)} for i in range(2)]}]
        def delete_object(self,**kwargs):
            state['deleted'].append(kwargs['Key']);state['leader']=False
    with pytest.raises(DomainError):ObjectStore(Storage(),'fixture','test').prune(7,guard=guard)
    assert len(state['deleted'])==1


def test_backup_loop_initial_follower_takes_over_without_restart(monkeypatch):
    import src.operations.remote as remote
    state={'leader':False,'waits':0,'created':0,'cycles':0}
    def guard():
        if not state['leader']:raise DomainError('LEASE_LOST','fixture',409)
    def store():
        assert state['leader'];state['created']+=1;return object()
    def cycle(*args,**kwargs):
        kwargs['guard']();state['cycles']+=1
    async def sleep(_):
        state['waits']+=1
        if state['waits']==1:state['leader']=True
        elif state['waits']==2:state['leader']=False
        else:raise asyncio.CancelledError
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',base64.b64encode(b'x'*32).decode())
    monkeypatch.setenv('BACKUP_CHECKPOINT_SECONDS','300')
    monkeypatch.setenv('BACKUP_INTERVAL_SECONDS','86400')
    monkeypatch.setenv('BACKUP_RETENTION_DAYS','7')
    monkeypatch.setattr(remote.ObjectStore,'from_env',store)
    monkeypatch.setattr(remote,'cycle',cycle)
    monkeypatch.setattr(remote.asyncio,'sleep',sleep)
    with pytest.raises(asyncio.CancelledError):asyncio.run(remote.loop(SimpleNamespace(),guard=guard))
    assert state['created']==state['cycles']==1
