"""Encrypted S3-compatible backup transport; no plaintext/credentials in logs."""
import asyncio
from datetime import datetime,timezone,timedelta
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit
from src.foundation.repository import DomainError
from .backup import create_archive,write_checkpoint,key_bytes,file_hash,decrypt

class ObjectStore:
    def __init__(self,client,bucket,prefix):
        if not bucket or not prefix or '..' in prefix or prefix.startswith('/'):
            raise ValueError('Backup bucket and safe prefix are required')
        self.client,self.bucket,self.prefix=client,bucket,prefix.rstrip('/')+'/'
    @classmethod
    def from_env(cls):
        import boto3
        from botocore.config import Config
        endpoint=os.getenv('BACKUP_S3_ENDPOINT','');url=urlsplit(endpoint)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query:raise ValueError('BACKUP_S3_ENDPOINT must be HTTPS')
        if not os.getenv('AWS_ACCESS_KEY_ID') or not os.getenv('AWS_SECRET_ACCESS_KEY'):raise ValueError('Backup credentials are missing')
        client=boto3.client('s3',endpoint_url=endpoint,region_name=os.getenv('BACKUP_S3_REGION','auto'),
            config=Config(connect_timeout=5,read_timeout=30,retries={'total_max_attempts':1}))
        return cls(client,os.getenv('BACKUP_S3_BUCKET',''),os.getenv('BACKUP_S3_PREFIX','travel-agent'))
    def upload(self,path,name,*,guard=None):
        digest=file_hash(path)
        with open(path,'rb') as stream:
            if guard:guard()
            self.client.put_object(Bucket=self.bucket,Key=self.prefix+name,Body=stream,ContentType='application/octet-stream',Metadata={'sha256':digest})
        if guard:guard()
        head=self.client.head_object(Bucket=self.bucket,Key=self.prefix+name)
        if head.get('Metadata',{}).get('sha256')!=digest or head['ContentLength']!=Path(path).stat().st_size:raise ValueError('Remote backup verification failed')
        return {'object':name,'sha256':digest,'bytes':head['ContentLength']}
    def download(self,name,target):
        if not name or '/' in name or '..' in name:raise ValueError('Use an exact archive object name')
        response=self.client.get_object(Bucket=self.bucket,Key=self.prefix+name)
        target=Path(target);created=False;size=0
        try:
            with target.open('xb') as stream:
                created=True;target.chmod(0o600)
                while chunk:=response['Body'].read(65536):
                    size+=len(chunk)
                    if size>2*1024**3:raise ValueError('Remote archive exceeds limit')
                    stream.write(chunk)
            if size!=response['ContentLength'] or file_hash(target)!=response.get('Metadata',{}).get('sha256'):
                raise ValueError('Remote checksum mismatch')
        except Exception:
            if created:target.unlink(missing_ok=True)
            raise
        finally:response['Body'].close()
    def recent(self,limit=30):
        # Bounded memory even if the retained bucket has many checkpoints.
        import heapq
        def entries():
            for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,Prefix=self.prefix):
                for item in page.get('Contents',[]):
                    name=item['Key'][len(self.prefix):]
                    if name.startswith(('snapshot-','checkpoint-')) and name.endswith('.enc'):
                        yield {'object':name,'bytes':item['Size'],'modified_at':item['LastModified'].isoformat()}
        return heapq.nlargest(limit,entries(),key=lambda item:item['modified_at'])
    def prune(self,days,*,guard=None):
        if not 7<=days<=90:raise ValueError('Backup retention must be 7–90 days')
        cutoff=datetime.now(timezone.utc)-timedelta(days=days)
        # Preserve checkpoints one extra day to cover the oldest daily snapshot.
        count=0
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,Prefix=self.prefix):
            for item in page.get('Contents',[]):
                name=item['Key'][len(self.prefix):]
                if name.startswith(('snapshot-','checkpoint-')) and name.endswith('.enc') and item['LastModified']<cutoff-timedelta(days=1):
                    if guard:guard()
                    self.client.delete_object(Bucket=self.bucket,Key=item['Key']);count+=1
        return count


def cycle(app,store,key,*,full=False,retention=7,guard=None):
    if guard:guard()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    result={'at':stamp,'full':full}
    with tempfile.TemporaryDirectory(prefix='travel-offhost-') as temp:
        root=Path(temp)
        if full:
            result['snapshot']=create_archive(app.state.settings,root/'snapshot.enc',key)
            result['snapshot_remote']=store.upload(root/'snapshot.enc','snapshot-'+stamp+'.enc',guard=guard)
        if guard:guard()
        result['checkpoint']=write_checkpoint(app.state.db,root/'checkpoint.enc',key)
        result['checkpoint_remote']=store.upload(root/'checkpoint.enc','checkpoint-'+stamp+'.enc',guard=guard)
        if full:result['pruned']=store.prune(retention,guard=guard)
    # Counts and hashes only; never archive contents, location secrets or user IDs.
    with app.state.db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if guard:guard(con=con)
        con.execute('INSERT INTO operations_audit(action,created_at,details_json) VALUES(?,?,?)',('offhost_backup',datetime.now(timezone.utc).isoformat(),json.dumps(result)))
    return result


async def loop(app,*,guard=None):
    store=None
    full_interval=int(os.getenv('BACKUP_INTERVAL_SECONDS','86400'));checkpoint_interval=int(os.getenv('BACKUP_CHECKPOINT_SECONDS','300'))
    retention=int(os.getenv('BACKUP_RETENTION_DAYS','7'))
    if not 300<=checkpoint_interval<=3600 or not checkpoint_interval<=full_interval<=86400 or not 7<=retention<=90:raise ValueError('Backup intervals/retention invalid')
    next_full=next_checkpoint=0
    while True:
        try:
            if guard:await asyncio.to_thread(guard)
            if time.monotonic()<next_checkpoint:
                await asyncio.sleep(min(30,next_checkpoint-time.monotonic()))
                continue
            next_checkpoint=time.monotonic()+checkpoint_interval
            if store is None:store=ObjectStore.from_env()
            key=key_bytes(os.environ['BACKUP_ENCRYPTION_KEY'])
            full=time.monotonic()>=next_full
            await asyncio.to_thread(cycle,app,store,key,full=full,retention=retention,guard=guard)
            if full:next_full=time.monotonic()+full_interval
        except asyncio.CancelledError:raise
        except Exception as exc:
            if not isinstance(exc,DomainError) or exc.code!='LEASE_LOST':
                logging.getLogger(__name__).error('offhost_backup_failed code=BACKUP_FAILED')
                try:
                    with app.state.db.connect() as con:
                        con.execute('BEGIN IMMEDIATE')
                        if guard:guard(con=con)
                        con.execute('INSERT INTO operations_audit(action,created_at,details_json) VALUES(?,?,?)',('backup_failed',datetime.now(timezone.utc).isoformat(),'{}'))
                except DomainError as lost:
                    if lost.code!='LEASE_LOST':raise
        # Every process can take over, but only a live lease may start a cycle.
        await asyncio.sleep(30)
