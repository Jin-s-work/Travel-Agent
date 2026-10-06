"""Synthetic review browser fixture; no external calls, keys, or production data.

Run .venv/bin/python tests/review_browser_fixture.py . Authlib still verifies
loopback OIDC login state/PKCE/JWT using the foundation browser fixture.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent))
import browser_fixture as base
from api import create_app
from src.foundation.auth import Actor, digest
from src.providers.fake_reviews import FakeReviewCollectionProvider
from src.reliability.budget import BudgetPolicy

class SyntheticDetector:
    version='synthetic-review-browser-v1'
    def detect(self,text):
        return {'language':text.split(':',1)[0], 'detector_version':self.version,
                'model_confidence':1.0,'disagreement_reason':None}

now=datetime.now(timezone.utc)
texts={'ja':'合成レビューです。料理とサービスについてのテスト用文章です。',
       'ko':'실제 음식점과 무관한 합성 언어 집계 검증용 리뷰 문장입니다.',
       'en':'This is a synthetic review used only to verify the application interface.'}
records=[]
for index in range(200):
    language='ja' if index<150 else 'ko' if index<154 else 'en' if index<190 else None
    records.append({'provider_review_id':f'browser-synthetic-{index}',
        'original_text':f'{language}:{texts[language]}' if language else None,
        'translated_text':None if language else '원문을 확보하지 못한 합성 번역문',
        'original_language':language,'original_language_verified':bool(language),
        'original_separation_verified':True,'text_presence':'present',
        'published_at':(now-timedelta(hours=index+1)).isoformat(),'date_precision':'exact',
        'rating':5,'rating_scale':5})

policy_config=deepcopy(BudgetPolicy.for_tests().config)
for operation in ('review_start','review_poll','review_page','review_abort','review_delete_dataset','review_delete_run'):
    policy_config['prices']['fake/'+operation]={'currency':'USD','rates_per_million':{'calls':'0'},'max_units':{'calls':1}}
provider=FakeReviewCollectionProvider(records=records,page_size=50,polls_before_ready=8)
app=create_app(base.settings,parser=base.parse,embedder=base.embed,answer_generator=base.generate,
    budget_policy=BudgetPolicy(policy_config),review_provider=provider,review_detector=SyntheticDetector())
actors={}
for who in ('a','b'):
    token=app.state.auth.complete_identity({'iss':base.PROVIDER,'sub':who,'email':who+'@example.test',
        'email_verified':True,'name':'합성 사용자 '+who.upper()},base.invites[who])
    with app.state.db.connect() as con:
        if who=='a':con.execute("UPDATE users SET role='admin' WHERE email='a@example.test'")
        row=con.execute('SELECT u.id,u.email,u.display_name,u.role,s.id AS session_id,s.csrf_token,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?',(digest(token),)).fetchone()
    actors[who]=Actor(**dict(row))
trips={}
for who in actors:
    existing=app.state.repo.list_trips(actors[who].id)
    trips[who]=existing[0] if existing else app.state.repo.create_trip(actors[who].id,{
        'title':f'합성 {who.upper()} 도쿄 리뷰 검증','start_date':'2026-11-06','end_date':'2026-11-09',
        'stops':[{'city':'Tokyo','sequence':1,'start_date':'2026-11-06','end_date':'2026-11-09','timezone':'Asia/Tokyo'}]})
service=app.state.reviews
policies=service.policies(actors['a'])
policy=policies[0] if policies else service.add_policy(actors['a'],{
    'provider':'fake','version':'synthetic-browser-policy-v1','purpose':'합성 UI 검증에만 사용하는 자료의 계산·표시',
    'evidence':['https://example.test/synthetic-fixture-only'],
    'rights':{'access':True,'collect':True,'calculate':True,'raw_store':False,'aggregate_store':True,'id_store':True,'llm':False,'display':True},
    'reviewed_at':(now-timedelta(minutes=1)).isoformat(),'expires_at':(now+timedelta(days=1)).isoformat(),
    'aggregate_ttl_seconds':86400,'id_ttl_seconds':86400})
places=service.places(actors['a'])
place=places[0] if places else service.add_place(actors['a'],{'provider':'fake','external_place_id':'SYNTHETIC-TOKYO-REVIEW-001','city':'tokyo',
    'name':'合成食堂 · 실제 식당 아님','address':'東京都 · 합성 검증 전용 주소',
    'source_url':'https://www.google.com/maps?cid=SYNTHETIC-TOKYO-REVIEW-001','rating':4.5,'total_rating_count':1200})
if place['identity_status']!='verified':place=service.verify_place(actors['a'],place['id'],{'expected_version':place['version'],'status':'verified','evidence':'합성 fixture의 이름·주소·지점 ID를 대조함. 실제 지점 검증이 아님.'})
for who in actors:service.link(actors[who],trips[who]['id'],place['id'])
controls=service.controls()
if not controls['research_enabled']:service.set_controls(actors['a'],{'expected_version':controls['version'],'research_enabled':True,'production_enabled':False})
info={'base_url':base.BASE,'invites':base.invites,'fixture_dir':str(base.SANDBOX),
      'synthetic':True,'admin_account':'a@example.test','user_account':'b@example.test',
      'place_id':place['id'],'policy_id':policy['id'],'trip_ids':{who:trip['id'] for who,trip in trips.items()}}
(base.SANDBOX/'review-browser-info.json').write_text(json.dumps(info,ensure_ascii=False))
if __name__=='__main__':
    print(json.dumps(info,ensure_ascii=False),flush=True)
    threading.Thread(target=lambda:base.uvicorn.run(base.idp,host='127.0.0.1',port=8766,log_level='warning',access_log=False),daemon=True).start()
    base.uvicorn.run(app,host='127.0.0.1',port=8765,log_level='warning',access_log=False)
