"""Local-only manual browser fixture. Never imported by the production application.

Run: OPENAI_API_KEY=test .venv/bin/python tests/browser_fixture.py
Authlib performs real state/nonce/PKCE/JWT checks against a synthetic loopback IdP.
The extractor, embedder and vector store are deterministic fixtures, no paid calls.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SANDBOX=Path(os.environ['BROWSER_FIXTURE_DIR']) if os.getenv('BROWSER_FIXTURE_DIR') else Path(tempfile.mkdtemp(prefix='travel-foundation-browser-'))
os.environ['DATABASE_PATH']=str(SANDBOX/'unused.sqlite3')
os.environ['OPENAI_API_KEY']='test-placeholder'
os.environ['TAVILY_API_KEY']=''
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from authlib.jose import JsonWebKey, jwt
import uvicorn
from api import create_app
from src.foundation.settings import Settings

PROVIDER='http://127.0.0.1:8766'
BASE='http://127.0.0.1:8765'
key=JsonWebKey.generate_key('RSA',2048,is_private=True,options={'kid':'fixture-key'})
codes={}
idp=FastAPI()

@idp.get('/.well-known/openid-configuration')
def config():
    return {'issuer':PROVIDER,'authorization_endpoint':PROVIDER+'/authorize','token_endpoint':PROVIDER+'/token','jwks_uri':PROVIDER+'/jwks','response_types_supported':['code'],'subject_types_supported':['public'],'id_token_signing_alg_values_supported':['RS256'],'token_endpoint_auth_methods_supported':['client_secret_basic'],'code_challenge_methods_supported':['S256']}

@idp.get('/jwks')
def jwks():
    return {'keys':[key.as_dict(is_private=False)]}

@idp.get('/authorize')
def authorize(request:Request):
    p=dict(request.query_params)
    if p.get('redirect_uri')!=BASE+'/api/v2/auth/callback' or p.get('client_id')!='browser-fixture' or p.get('code_challenge_method')!='S256':
        return HTMLResponse('Invalid authorization request',400)
    if 'fixture_account' not in p:
        import html
        fields=''.join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v,quote=True)}">' for k,v in p.items())
        return HTMLResponse(f'<html lang="ko"><title>합성 로그인 제공자</title><body><h1>테스트용 OIDC 로그인</h1><p>실제 Google 계정이 아닌 합성 사용자입니다.</p><form method="get">{fields}<button name="fixture_account" value="a">합성 사용자 A 로그인</button><button name="fixture_account" value="b">합성 사용자 B 로그인</button></form></body></html>')
    code=secrets.token_urlsafe(24)
    codes[code]={**p,'issued':time.time()}
    return RedirectResponse(p['redirect_uri']+'?'+urlencode({'state':p['state'],'code':code}),303)

@idp.post('/token')
async def token(request:Request):
    p=dict(await request.form()); c=codes.pop(p.get('code',''),None)
    from fastapi.responses import JSONResponse
    valid_auth='Basic '+base64.b64encode(b'browser-fixture:fixture-secret').decode()
    if not c or request.headers.get('authorization')!=valid_auth or p.get('redirect_uri')!=c['redirect_uri'] or time.time()-c['issued']>120:
        return JSONResponse({'error':'invalid_grant'},400)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(p.get('code_verifier','').encode()).digest()).decode().rstrip('=')
    if challenge!=c['code_challenge']:
        return JSONResponse({'error':'invalid_grant'},400)
    who=c['fixture_account']; stamp=int(time.time())
    claims={'iss':PROVIDER,'aud':'browser-fixture','sub':who,'iat':stamp,'exp':stamp+300,'nonce':c['nonce'],'email':who+'@example.test','email_verified':True,'name':'합성 사용자 '+who.upper()}
    encoded=jwt.encode({'alg':'RS256','kid':'fixture-key'},claims,key).decode()
    return {'access_token':'fixture-access','token_type':'Bearer','expires_in':300,'id_token':encoded}

class Vectors:
    def __init__(self): self.items={}
    def add(self,ids,documents,embeddings,metadatas):
        for ident,doc,meta in zip(ids,documents,metadatas):self.items[ident]={'document':doc,'metadata':meta,'similarity':1.0}
    def search(self,query,top_k=5,where=None):
        active=where['generation_id']['$in']
        return [value for key,value in self.items.items() if value['metadata']['generation_id'] in active][:top_k]

def parse(text):
    # Explicit synthetic controls exist only in this fixture, never production.
    if (SANDBOX/'pause-extraction').exists():
        time.sleep(8)
    value=json.loads(text)
    (SANDBOX/'calls.jsonl').open('a').write(json.dumps({'operation':'extract','provider':value[0].get('provider')})+'\n')
    return value

failed_once=(SANDBOX/'failure-used').exists()
def embed(texts):
    global failed_once
    if (SANDBOX/'embedding-offline').exists():
        from src.reliability.providers import DefinitelyNotSent
        raise DefinitelyNotSent()
    if any('RETRY-FIXTURE' in text for text in texts) and not failed_once:
        failed_once=True
        (SANDBOX/'failure-used').touch()
        from src.reliability.providers import DefinitelyNotSent
        raise DefinitelyNotSent()
    (SANDBOX/'calls.jsonl').open('a').write(json.dumps({'operation':'embed','count':len(texts)})+'\n')
    return [[1.,0.,0.] for _ in texts]

def generate(question,hits):
    return '\n'.join(f"{h['metadata'].get('provider')}: {h['metadata'].get('time') or '시각 미확인'} / {h['metadata'].get('refund_policy') or '환불 규정 미확인'}" for h in hits)

settings=Settings(database_path=SANDBOX/'service.sqlite3',documents_dir=SANDBOX/'documents',vectors_dir=SANDBOX/'vectors',environment='development',public_base_url=BASE,oidc_client_id='browser-fixture',oidc_client_secret='fixture-secret',oidc_metadata_url=PROVIDER+'/.well-known/openid-configuration',session_secret=secrets.token_urlsafe(48))
from src.reliability.budget import BudgetPolicy
app=create_app(settings,parser=parse,embedder=embed,answer_generator=generate,budget_policy=BudgetPolicy.for_tests())
invites={who:app.state.auth.invite(who+'@example.test') for who in ('a','b')}
fixtures=[{'kind':'항공','provider':'合成航空 東京往復','confirmation_number':'SYNTH-ROUND','date':'2026-11-06','time':'09:00','refund_policy':'출발 7일 전까지 무료 취소','raw_snippet':'합성 왕복 항공 자료','stable_item_key':'round','events':[{'event_type':'outbound','start_local':'2026-11-06T09:00:00','end_local':'2026-11-06T11:20:00','start_timezone':'Asia/Seoul','end_timezone':'Asia/Tokyo','location':'ICN → NRT'},{'event_type':'return','start_local':'2026-11-09T18:00:00','end_local':'2026-11-09T20:30:00','start_timezone':'Asia/Tokyo','end_timezone':'Asia/Seoul','location':'NRT → ICN'}]}, {'kind':'투어','provider':'東京の長い名前の街歩き・合成ツアー','confirmation_number':'SYNTH-TOUR','date':'2026-11-06','time':'15:00','location':'新宿駅','refund_policy':'전날까지 취소 가능','raw_snippet':'합성 투어 자료','stable_item_key':'tour'}]
(SANDBOX/'round-trip.txt').write_text(json.dumps(fixtures,ensure_ascii=False))
(SANDBOX/'retry.txt').write_text(json.dumps([{'kind':'투어','provider':'RETRY-FIXTURE 미술관','date':'2026-11-06','time':'17:00','stable_item_key':'retry'}],ensure_ascii=False))
(SANDBOX/'pause-extraction').touch()
(SANDBOX/'bad.pdf').write_bytes(b'%PDF-1.4 synthetic only')
(SANDBOX/'browser-info.json').write_text(json.dumps({'base_url':BASE,'invites':invites,'fixture_dir':str(SANDBOX)}))

if __name__=='__main__':
    print(json.dumps({'base_url':BASE,'invites':invites,'fixture_dir':str(SANDBOX)},ensure_ascii=False),flush=True)
    threading.Thread(target=lambda:uvicorn.run(idp,host='127.0.0.1',port=8766,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8765,log_level='warning',access_log=False)
