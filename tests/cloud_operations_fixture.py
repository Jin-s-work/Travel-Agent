"""Mounted synthetic harness only; never copied into the production image.

Production launcher and 512 MiB container, actual disposable pgvector SQL,
HTTP Storage fake and pre-verified synthetic identities. No Supabase credentials.
"""
import os
import sys
import json
from urllib.parse import urlsplit
if urlsplit(os.environ['TRAVEL_TEST_POSTGRES_DSN']).hostname not in {'travel-supabase-test','127.0.0.1','localhost'}:
    raise ValueError('Disposable local PostgreSQL is required')
sys.path.insert(0,'/app')
import httpx
cloud_url=os.environ['DATABASE_URL']
os.environ['STORAGE_BACKEND']='local';os.environ['DATABASE_URL']=''
import api
from src.storage.postgres import PostgresDatabase
class FakeStorage:
    def __init__(self):self.objects={}
    def __call__(self,request):
        path=request.url.path
        if '/bucket/' in path:return httpx.Response(200,json={'id':'travel-private','public':False})
        prefix='/storage/v1/object/travel-private/'
        if request.method=='DELETE':
            for key in json.loads(request.content)['prefixes']:self.objects.pop(key,None)
            return httpx.Response(200,json=[])
        key=path.split(prefix,1)[1]
        if request.method=='POST':self.objects[key]=request.content;return httpx.Response(200,json={'Key':key})
        return httpx.Response(200,content=self.objects[key]) if key in self.objects else httpx.Response(404)
factory=api.create_app
storage=FakeStorage()
api.Database=lambda path,**kwargs:PostgresDatabase(os.environ['TRAVEL_TEST_POSTGRES_DSN'],path,schema='travel_load')
api.create_app=lambda *args,**kwargs:factory(*args,**kwargs,storage_transport=httpx.MockTransport(storage))
os.environ['STORAGE_BACKEND']='supabase';os.environ['DATABASE_URL']=cloud_url
# Existing five-user harness injects fake extraction, embedding and test budget.
from tests import operations_fixture
