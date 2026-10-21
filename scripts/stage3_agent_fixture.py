"""V6 stage 3 browser sandbox (E06): AI mode + trip agent with a scripted model.

PYTHON_DOTENV_DISABLED=1 <app venv python> scripts/stage3_agent_fixture.py
Application :8776 / synthetic IdP :8777 (STAGE3_APP_PORT / STAGE3_IDP_PORT override;
the E2E suite uses free ports). Temporary SQL, synthetic OIDC/data, no network
provider and no paid call: the parser and embedder are local fakes (so the mail
analysis mode is "ai" and the question router reaches the agent branch), and the
model is `app.state.agent_adapter`, a deterministic script that reads, previews
and answers. It can never apply: applying stays the user's click on the existing
PATCH. Weather is off. Never used by the deployed application, and the E01-E05
fixture (scripts/stage1_browser_fixture.py) is untouched.

Users A and B are ordinary members (role 'member'); a separate operator account only
approves the synthetic place pack. Outbound sockets from this process are limited
to loopback: any other connect or name lookup is refused and logged to stderr as
BLOCKED_EXTERNAL, which the E2E suite checks in the server log.
"""
import ipaddress
import os
import socket
import sys
from pathlib import Path


def _loopback(host):
    if host in (None, '', 'localhost', '0.0.0.0', '::') or str(host).endswith('.localhost'):
        return True
    try:
        return ipaddress.ip_address(str(host).split('%')[0]).is_loopback
    except ValueError:
        return False


def _blocked(host):
    print(f'BLOCKED_EXTERNAL {host}', file=sys.stderr, flush=True)
    return OSError(f'stage3 fixture: outbound connection to {host} refused (loopback only)')


def _guard_network():
    """Loopback-only process: the fixture must make zero external provider calls."""
    connect, connect_ex, lookup = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def target(sock, address):
        return address[0] if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) else None

    def guarded_connect(sock, address):
        host = target(sock, address)
        if host is not None and not _loopback(host):
            raise _blocked(host)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        host = target(sock, address)
        if host is not None and not _loopback(host):
            raise _blocked(host)
        return connect_ex(sock, address)

    def guarded_lookup(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if not _loopback(name):
            raise _blocked(name)
        return lookup(host, *args, **kwargs)
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = guarded_connect, guarded_connect_ex, guarded_lookup


_guard_network()
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
os.environ.update(PYTHON_DOTENV_DISABLED='1',STORAGE_BACKEND='local',ZERO_SPEND='true',MAIL_ANALYSIS_MODE='ai',
                  GOOGLE_MAPS_API_KEY='',APIFY_TOKEN='',DATABASE_URL='',OPENAI_API_KEY='',ANTHROPIC_API_KEY='',
                  TRIP_AGENT_ENABLED='1',WEATHER_ENABLED='0',PERSONAL_TOKENS_ENABLED='0',PUBLIC_DISCOVERY_ENABLED='0')
import browser_fixture as base
APP_PORT=int(os.getenv('STAGE3_APP_PORT','8776'));IDP_PORT=int(os.getenv('STAGE3_IDP_PORT','8777'))
base.BASE=f'http://127.0.0.1:{APP_PORT}';base.PROVIDER=f'http://127.0.0.1:{IDP_PORT}'
from copy import deepcopy
from datetime import datetime, timedelta
import json
import secrets
import threading
from types import SimpleNamespace
from api import create_app
from src.config import AGENT_MODEL
from src.foundation.auth import digest
from src.foundation.settings import Settings
from src.llm import ChatResult, ToolCall, Usage
from src.reliability.budget import BudgetPolicy
from src.reliability.providers import ProviderResult
from tests.discovery_synthetic import pack

FINAL_ANSWER='일정을 확인하고 변경 미리보기를 만들었어요. 아직 적용하지 않았어요. 아래 카드에서 검증 결과를 확인한 뒤 직접 적용해 주세요.'
NO_PLAN_ANSWER='적용된 일정이 아직 없어 미리보기를 만들지 않았어요. 일정 탭에서 먼저 일정을 만들어 주세요.'


def parse(text):
    """Synthetic extraction (JSON list in, list out); E06 uploads no mail."""
    return json.loads(text)


def embed(texts):
    return [[1.,0.,0.] for _ in texts]


def _tool_json(message):
    content=message.get('content') or ''
    start,end=content.find('{'),content.rfind('}')
    try:
        return json.loads(content[start:end+1]) if start>=0 and end>start else {}
    except ValueError:
        return {}


def _later(local_start):
    """Same day, two hours later (or 09:00 when that would pass 19:00)."""
    try:
        start=datetime.fromisoformat(str(local_start)[:16])
    except ValueError:
        return None
    moved=start+timedelta(hours=2)
    if moved.date()!=start.date() or moved.hour>=19:
        moved=start.replace(hour=9,minute=0)
    return moved.strftime('%Y-%m-%dT%H:%M')


class ScriptedAgentModel:
    """Deterministic stand-in for the agent model (adapter contract of src/llm).

    Turn 1: read bookings, saved places and the latest itinerary.
    Turn 2: preview moving the first unlocked, non-booking place item.
    Turn 3: final answer without checkable atoms (grounding stays verified).
    It only sees what the tools returned in this request; it has no write tool.
    """
    name='replay'
    needs_client=False
    supports_embeddings=False

    def chat_request(self,*,model,messages,schema=None,tools=None,tool_choice=None,reasoning_effort=None,max_output_tokens=None):
        from src.llm.openai_adapter import OpenAIAdapter
        return OpenAIAdapter().chat_request(model=model,messages=messages,schema=schema,tools=tools,max_output_tokens=max_output_tokens)

    def make_client(self,client_factory=None):
        return None

    def preflight(self,kind,kwargs):
        return None

    def output_cap(self,kwargs,price):
        bound=price['max_units']['output_tokens']
        return min(int(kwargs.get('max_completion_tokens',bound)),bound)

    def apply_cap(self,kwargs,cap):
        kwargs['max_completion_tokens']=cap

    def estimate_chat(self,kwargs,cap):
        return {'input_tokens':10,'output_tokens':cap}

    def send(self,client,kind,kwargs):
        return ProviderResult({'turn':self._turn(kwargs['messages'])},{'input_tokens':1,'output_tokens':1})

    def parse_chat(self,value):
        # The gateway stores the raw value as a receipt, so it stays plain JSON.
        turn=value['turn']
        if turn.get('text') is not None:
            return ChatResult(text=turn['text'],finish='stop',usage=Usage(1,1))
        return ChatResult(text=None,finish='tool_calls',usage=Usage(1,1),
                          tool_calls=tuple(ToolCall(id=ident,name=name,arguments=args) for ident,name,args in turn['calls']))

    @staticmethod
    def _calls(*calls):
        return {'text':None,'calls':[[f'{name}-{index}',name,json.dumps(args)] for index,(name,args) in enumerate(calls)]}

    def _turn(self,messages):
        rounds=sum(1 for m in messages if m.get('role')=='assistant' and m.get('tool_calls'))
        if rounds==0:
            return self._calls(('list_bookings',{'date_from':None,'date_to':None}),('list_saved_places',{}),
                               ('get_itinerary',{'itinerary_id':None}))
        if rounds==1:
            plan=next((_tool_json(m) for m in messages if m.get('role')=='tool' and str(m.get('tool_call_id','')).startswith('get_itinerary')),{})
            item=next((i for i in plan.get('items') or [] if i.get('place_id') and not i.get('booking_id') and not i.get('locked')),None)
            target=_later(item.get('local_start')) if item else None
            if not (plan.get('itinerary_id') and plan.get('version') and target):
                return {'text':NO_PLAN_ANSWER}
            command={'op':'move','item_id':item['item_id'],'place_id':None,'local_start':target,'duration_minutes':None}
            return self._calls(('preview_itinerary_change',{'itinerary_id':plan['itinerary_id'],'expected_version':plan['version'],'commands':[command]}))
        return {'text':FINAL_ANSWER}


def policy():
    config=deepcopy(BudgetPolicy.for_tests().config)
    config['version']='synthetic-stage3-agent-v1'
    config['prices'][f'replay/{AGENT_MODEL}']={'currency':'USD','rates_per_million':{'input_tokens':'0','output_tokens':'0'},
                                               'max_units':{'input_tokens':200000,'output_tokens':1024}}
    return BudgetPolicy(config)


settings=Settings(database_path=base.SANDBOX/'stage3-agent.sqlite3',documents_dir=base.SANDBOX/'agent-documents',vectors_dir=base.SANDBOX/'agent-vectors',
                  environment='development',public_base_url=base.BASE,oidc_client_id='browser-fixture',oidc_client_secret='fixture-secret',
                  oidc_metadata_url=base.PROVIDER+'/.well-known/openid-configuration',session_secret=secrets.token_urlsafe(48),job_poll_seconds=.2)
app=create_app(settings,parser=parse,embedder=embed,budget_policy=policy())
app.state.agent_adapter=ScriptedAgentModel()
app.state.discovery.allow_synthetic=True


def account(who,role):
    invitation=app.state.auth.invite(who+'@example.test')
    token=app.state.auth.complete_identity({'iss':base.PROVIDER,'sub':who,'email':who+'@example.test','email_verified':True,'name':'합성 사용자 '+who.upper()},invitation)
    with app.state.db.connect() as con:
        user=con.execute('SELECT id FROM users WHERE email=?',(who+'@example.test',)).fetchone()
        if role=='admin':
            con.execute("UPDATE users SET role='admin' WHERE id=?",(user['id'],))
        session=con.execute('SELECT id FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()
    return SimpleNamespace(id=user['id'],session_id=session['id'],role=role)


# Only the operator approves the synthetic pack; the users who ask and apply stay ordinary.
operator=account('ops','admin')
imported=app.state.discovery.import_pack(operator,pack('barcelona'))
stored=next(p for p in app.state.discovery.list_packs(operator) if p['id']==imported['pack_id'])
for place in stored['places']:
    for source in place['sources']:
        app.state.discovery.review_source(operator,source['id'],{'expected_version':source['version'],'status':'active','read_confirmed':True,'display_permitted':True,'policy_version':stored['version'],'evidence':'Author-created synthetic browser test only'})
app.state.discovery.approve_pack(operator,imported['pack_id'],{'status':'approved','evidence':'Synthetic only; no live place or quality claim'})
for who in ('a','b'):
    actor=account(who,'member')
    trip=app.state.repo.create_trip(actor.id,{'title':who.upper()+' · 바르셀로나 에이전트','start_date':'2026-11-06','end_date':'2026-11-09','party':{'adults':2,'children':[],'children_status':'none'},'stops':[{'sequence':1,'city':'barcelona','start_date':'2026-11-06','end_date':'2026-11-09','timezone':'Europe/Madrid'}]})
    app.state.repo.create_booking(actor.id,trip['id'],{'kind':'restaurant','provider':'합성 점심 예약','date':'2026-11-07','time':'12:00','time_end':'13:00','status':'user_confirmed','events':[{'event_type':'visit','start_local':'2026-11-07T12:00:00','end_local':'2026-11-07T13:00:00','start_timezone':'Europe/Madrid','end_timezone':'Europe/Madrid','location':'합성 식당'}]})

if __name__=='__main__':
    import uvicorn
    print(json.dumps({'url':base.BASE,'fixture_dir':str(base.SANDBOX),'data':'synthetic only','agent':'scripted','external_provider_calls':0}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=IDP_PORT,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=APP_PORT,log_level='warning',access_log=False)
