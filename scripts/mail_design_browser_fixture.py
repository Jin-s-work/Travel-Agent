"""Loopback mail/design check: synthetic accounts and real local mail parser.
No paid provider or production database is used. No injected parser, no artificial delay, no production data.
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/mail_design_browser_fixture.py
"""
import json,os,secrets,sys,threading,time
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
os.environ.update(PYTHON_DOTENV_DISABLED='1',STORAGE_BACKEND='local',ZERO_SPEND='true',MAIL_ANALYSIS_MODE='local',GOOGLE_MAPS_API_KEY='',APIFY_TOKEN='')
import browser_fixture as base
base.BASE='http://127.0.0.1:8766';base.PROVIDER='http://127.0.0.1:8767'
from api import create_app
from src.foundation.settings import Settings
from src.foundation.auth import digest
from src.discovery.catalog_tools import register_reviewed_pack
settings=Settings(database_path=base.SANDBOX/'mail-design.sqlite3',documents_dir=base.SANDBOX/'documents',vectors_dir=base.SANDBOX/'vectors',environment='development',public_base_url=base.BASE,oidc_client_id='browser-fixture',oidc_client_secret='fixture-secret',oidc_metadata_url=base.PROVIDER+'/.well-known/openid-configuration',session_secret=secrets.token_urlsafe(48),job_poll_seconds=.2)
app=create_app(settings)
invite=app.state.auth.invite('a@example.test')
token=app.state.auth.complete_identity({'iss':base.PROVIDER,'sub':'a','email':'a@example.test','email_verified':True,'name':'화면 검증 사용자'},invite)
with app.state.db.connect() as con:
    user=con.execute("SELECT id FROM users WHERE email='a@example.test'").fetchone()
    con.execute("UPDATE users SET role='admin' WHERE id=?",(user['id'],))
    session=con.execute('SELECT id FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()
actor=SimpleNamespace(id=user['id'],session_id=session['id'],role='admin')
collection=json.loads((ROOT/'docs/service-v3/data/official-restaurants-2026-10-06.json').read_text())
for pack in collection['packs']:
    register_reviewed_pack(app.state.discovery,actor,pack,review_evidence='2026-10-06 official branch address and minimal factual summary review. No review bodies, images or full web pages. See source audit report.')
if not app.state.repo.list_trips(actor.id):
    for city,label,zone in [('tokyo','도쿄','Asia/Tokyo'),('barcelona','바르셀로나','Europe/Madrid'),('madrid','마드리드','Europe/Madrid')]:
        app.state.repo.create_trip(actor.id,{'title':'화면 검증 · '+label,'start_date':'2026-11-06','end_date':'2026-11-09','party':{'adults':2,'children':[],'children_status':'none'},'stops':[{'sequence':1,'city':city,'start_date':'2026-11-06','end_date':'2026-11-09','timezone':zone}]})
if __name__=='__main__':
    import uvicorn
    print(json.dumps({'url':base.BASE,'fixture_dir':str(base.SANDBOX),'accounts_trips':'synthetic','restaurant_facts':'reviewed public sources','paid_calls':0}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8767,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning',access_log=False)
