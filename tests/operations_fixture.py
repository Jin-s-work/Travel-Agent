"""Synthetic five-user container harness. Mounted only by the test command.

Production image contains neither this module nor an authentication bypass.
Uses production startup/secure-cookie settings; verified claims are injected only
at this offline fixture boundary. Every external call is a deterministic fake.
"""
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
sys.path.insert(0,'/app');sys.path.insert(0,str(Path(__file__).parent))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['OPENAI_API_KEY']='test-placeholder'
from src.foundation.settings import Settings
from src.foundation.auth import digest
from src.reliability.budget import BudgetPolicy
from discovery_synthetic import pack
import api

settings=Settings()
def parse(raw):
    time.sleep(2)
    return json.loads(raw)
def embed(texts):return [[.1]*8 for _ in texts]
app=api.create_app(settings,parser=parse,embedder=embed,answer_generator=lambda q,h:'합성 답변',budget_policy=BudgetPolicy.for_tests())
# Explicitly allowed for this mounted test harness only.
app.state.discovery.allow_synthetic=True
info=settings.database_path.parent/'load-info.json'
if not info.exists():
    users=[]
    for index in range(5):
        name='load'+str(index);email=name+'@example.test'
        token=app.state.auth.complete_identity({'iss':'https://fixture.example.test','sub':name,'email':email,'email_verified':True},app.state.auth.invite(email))
        with app.state.db.connect() as con:
            user=con.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
            if index==0:con.execute("UPDATE users SET role='admin' WHERE id=?",(user['id'],))
            session=con.execute('SELECT * FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()
        users.append({'user_id':user['id'],'session_id':session['id'],'token':token,'csrf':session['csrf_token']})
    admin=SimpleNamespace(id=users[0]['user_id'],session_id=users[0]['session_id'],role='admin')
    result=app.state.discovery.import_pack(admin,pack('tokyo'))
    imported=app.state.discovery.list_packs(admin)[0]
    for place in imported['places']:
        for source in place['sources']:
            app.state.discovery.review_source(admin,source['id'],{'expected_version':source['version'],'status':'active','read_confirmed':True,'display_permitted':True,'policy_version':imported['version'],'evidence':'Author-created synthetic capacity fixture.'})
    app.state.discovery.approve_pack(admin,result['pack_id'],{'status':'approved','evidence':'Synthetic load test, no operational data.'})
    info.write_text(json.dumps({'users':users,'synthetic':True}));info.chmod(0o600)
api.app=app
from src.operations.launch import main
main()
