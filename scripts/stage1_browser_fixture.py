"""V4 stage1 browser sandbox: temporary SQL, synthetic OIDC/data, no network providers.

PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/stage1_browser_fixture.py
Application :8766 / synthetic IdP :8767. Never used by the deployed application.
"""
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
os.environ.update(PYTHON_DOTENV_DISABLED='1',STORAGE_BACKEND='local',ZERO_SPEND='true',MAIL_ANALYSIS_MODE='local',GOOGLE_MAPS_API_KEY='',APIFY_TOKEN='',DATABASE_URL='')
import browser_fixture as base
base.BASE='http://127.0.0.1:8766';base.PROVIDER='http://127.0.0.1:8767'
import json
import secrets
import threading
from types import SimpleNamespace
from api import create_app
from src.foundation.settings import Settings
from src.foundation.auth import digest
from src.discovery.public_places import PublicDiscovery, normalize, center
from tests.discovery_synthetic import pack

settings=Settings(database_path=base.SANDBOX/'stage1.sqlite3',documents_dir=base.SANDBOX/'documents',vectors_dir=base.SANDBOX/'vectors',environment='development',public_base_url=base.BASE,oidc_client_id='browser-fixture',oidc_client_secret='fixture-secret',oidc_metadata_url=base.PROVIDER+'/.well-known/openid-configuration',session_secret=secrets.token_urlsafe(48),job_poll_seconds=.2)
app=create_app(settings)
app.state.discovery.allow_synthetic=True
def public_places(city):
    point=center(city)
    raw={'elements':[{'type':'node','id':800000000+n,'lat':point['latitude']+.0002*n,'lon':point['longitude'],
        'tags':{'name':f'합성 지도 카페 {n+1}','amenity':'cafe','addr:street':'Synthetic street'}} for n in range(3)]}
    return normalize(raw,city),500
app.state.discovery.public_provider=PublicDiscovery(app.state.db,fetcher=public_places)
for who in ('a','b'):
    invitation=app.state.auth.invite(who+'@example.test')
    token=app.state.auth.complete_identity({'iss':base.PROVIDER,'sub':who,'email':who+'@example.test','email_verified':True,'name':'합성 사용자 '+who.upper()},invitation)
    with app.state.db.connect() as con:
        user=con.execute('SELECT id FROM users WHERE email=?',(who+'@example.test',)).fetchone()
        con.execute("UPDATE users SET role='admin' WHERE id=?",(user['id'],))
        session=con.execute('SELECT id FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()
    actor=SimpleNamespace(id=user['id'],session_id=session['id'],role='admin')
    if who=='a':
        for city in ('tokyo','barcelona'):
            imported=app.state.discovery.import_pack(actor,pack(city))
            stored=next(p for p in app.state.discovery.list_packs(actor) if p['id']==imported['pack_id'])
            for place in stored['places']:
                for source in place['sources']:
                    app.state.discovery.review_source(actor,source['id'],{'expected_version':source['version'],'status':'active','read_confirmed':True,'display_permitted':True,'policy_version':stored['version'],'evidence':'Author-created synthetic browser test only'})
            app.state.discovery.approve_pack(actor,imported['pack_id'],{'status':'approved','evidence':'Synthetic only; no live place or quality claim'})
    for city,label,zone in [('tokyo','도쿄','Asia/Tokyo'),('barcelona','바르셀로나','Europe/Madrid')]:
        trip=app.state.repo.create_trip(actor.id,{'title':who.upper()+' · '+label+' 테스트','start_date':'2026-11-06','end_date':'2026-11-09','party':{'adults':2,'children':[],'children_status':'none'},'stops':[{'sequence':1,'city':city,'start_date':'2026-11-06','end_date':'2026-11-09','timezone':zone}]})
        app.state.repo.create_booking(actor.id,trip['id'],{'kind':'restaurant','provider':'합성 점심 예약','date':'2026-11-07','time':'12:00','time_end':'13:00','status':'user_confirmed','events':[{'event_type':'visit','start_local':'2026-11-07T12:00:00','end_local':'2026-11-07T13:00:00','start_timezone':zone,'end_timezone':zone,'location':'합성 식당'}]})

if __name__=='__main__':
    import uvicorn
    print(json.dumps({'url':base.BASE,'fixture_dir':str(base.SANDBOX),'data':'synthetic only','external_provider_calls':0}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8767,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning',access_log=False)
