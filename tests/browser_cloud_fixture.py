"""Browser fixture on disposable PostgreSQL; fake object HTTP and OIDC only."""
import os
import sys
from pathlib import Path
import tempfile
import json
import threading
import httpx
import uvicorn
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
root=Path(os.environ.get('BROWSER_FIXTURE_DIR',tempfile.mkdtemp(prefix='travel-cloud-browser-'))).resolve()
root.mkdir(parents=True,exist_ok=True)
os.environ['BROWSER_FIXTURE_DIR']=str(root)
os.environ['DATABASE_PATH']=str(root/'module.sqlite3')
os.environ['STORAGE_BACKEND']='local';os.environ['DATABASE_URL']=''
os.environ['OPENAI_API_KEY']='fixture';os.environ['TAVILY_API_KEY']='';os.environ['SEED_ON_EMPTY']='0'
import api
from src.storage.postgres import PostgresDatabase
from tests.test_cloud_storage import FakeStorage
class PersistentFake(FakeStorage):
    def __init__(self):
        super().__init__();self.store=root/'objects.json'
        if self.store.exists():self.objects={k:bytes.fromhex(v) for k,v in json.loads(self.store.read_text()).items()}
    def __call__(self,request):
        response=super().__call__(request)
        if request.method in {'POST','DELETE'}:self.store.write_text(json.dumps({k:v.hex() for k,v in self.objects.items()}));self.store.chmod(0o600)
        return response
storage=PersistentFake(); original=api.create_app
api.Database=lambda path,**kw:PostgresDatabase(os.environ['TRAVEL_TEST_POSTGRES_DSN'],path,schema='travel_browser')
api.create_app=lambda *a,**kw:original(*a,**kw,storage_transport=httpx.MockTransport(storage))
os.environ.update(STORAGE_BACKEND='supabase',DATABASE_URL=os.environ['TRAVEL_TEST_POSTGRES_DSN'],SUPABASE_URL='https://fixture.supabase.co',SUPABASE_SECRET_KEY='sb_secret_fixture')
import browser_itinerary_fixture as fixture
if __name__=='__main__':
    print(json.dumps({'url':fixture.base.BASE,'fixture_dir':str(root),'synthetic_only':True}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(fixture.base.idp,host='127.0.0.1',port=8766,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(fixture.app,host='127.0.0.1',port=8765,log_level='warning',access_log=False)
