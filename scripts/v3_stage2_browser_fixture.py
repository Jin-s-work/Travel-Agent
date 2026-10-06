"""Synthetic loopback-only V3 stage 2 browser fixture, never production seed.

Run: PYTHON_DOTENV_DISABLED=1 STORAGE_BACKEND=local .venv/bin/python scripts/v3_stage2_browser_fixture.py
Resume: BROWSER_FIXTURE_DIR=/absolute/fixture/directory <same command>
Inspect counters: python3 scripts/v3_stage2_browser_fixture.py --report /absolute/fixture/directory
No real OAuth account, key, map lookup or paid provider is used.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))


def report(directory):
    path=Path(directory)/'stage2.sqlite3'
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(r) for r in con.execute('SELECT provider,operation,state,COUNT(*) calls,SUM(estimated_cost_micros) reserved_micros,SUM(actual_cost_micros) actual_micros FROM usage_reservations GROUP BY provider,operation,state')]
        jobs=[dict(r) for r in con.execute('SELECT operation,state,COUNT(*) jobs FROM jobs GROUP BY operation,state')]
        counts={table:con.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('trips','trip_accommodations','recommendation_runs','itineraries','itinerary_revisions')}
    logfile=Path(directory)/'location-calls.jsonl'
    calls=[json.loads(line) for line in logfile.read_text().splitlines()] if logfile.exists() else []
    return {'synthetic':True,'external_http_calls':0,'location_invocations':calls,'metered_usage':rows,'jobs':jobs,'counts':counts}


def build():
    os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['STORAGE_BACKEND']='local'
    # Shared tested loopback IdP has real state/nonce/PKCE/signature checks.
    # This import's preliminary app is unused and remains in the same temp directory.
    import browser_fixture as base
    base.BASE='http://127.0.0.1:8766';base.PROVIDER='http://127.0.0.1:8767'
    from api import create_app
    from src.foundation.auth import digest
    from src.foundation.settings import Settings
    from src.location.providers import FakeGeocodingProvider,FakeRouteProvider
    from src.reliability.budget import BudgetPolicy
    from discovery_synthetic import pack
    import secrets

    def log(operation,elements=1):
        with (base.SANDBOX/'location-calls.jsonl').open('a') as out:
            out.write(json.dumps({'operation':operation,'elements':elements,'synthetic':True})+'\n')
            out.flush()
    hotels=[{'name':'合成ホテル桜 — 東京駅前 / Synthetic Sakura Hotel','original_name':'合成ホテル桜 東京駅前',
        'address':'SYNTHETIC ONLY Tokyo station branch A','city':'tokyo','latitude':35.68,'longitude':139.77,
        'provider_place_id':'synthetic-hotel-tokyo-station','timezone':'Asia/Tokyo',
        'map_url':'https://example.org/synthetic-hotel-tokyo-station'},
        {'name':'合成ホテル桜 — 上野 / Synthetic Sakura Hotel','original_name':'合成ホテル桜 上野',
        'address':'SYNTHETIC ONLY Tokyo Ueno branch B','city':'tokyo','latitude':35.714,'longitude':139.778,
        'provider_place_id':'synthetic-hotel-tokyo-ueno','timezone':'Asia/Tokyo',
        'map_url':'https://example.org/synthetic-hotel-tokyo-ueno'}]
    class BrowserGeocoder(FakeGeocodingProvider):
        def resolve(self,query,city,limits):
            log('geocoding')
            return super().resolve(query,city,limits)
    class BrowserRoutes(FakeRouteProvider):
        def matrix(self,origins,destinations,mode,departure,limits):
            log('route_matrix',len(origins)*len(destinations))
            for a in origins:
                for b in destinations:
                    self.routes[(a.get('id'),b.get('id'),mode)]={'duration_seconds':600,'distance_m':700}
            return super().matrix(origins,destinations,mode,departure,limits)
    geo=BrowserGeocoder(hotels);routes=BrowserRoutes()
    settings=Settings(database_path=base.SANDBOX/'stage2.sqlite3',documents_dir=base.SANDBOX/'stage2-documents',
        vectors_dir=base.SANDBOX/'stage2-vectors',environment='development',public_base_url=base.BASE,
        oidc_client_id='browser-fixture',oidc_client_secret='fixture-secret',
        oidc_metadata_url=base.PROVIDER+'/.well-known/openid-configuration',session_secret=secrets.token_urlsafe(48),
        job_poll_seconds=.05)
    policy=deepcopy(BudgetPolicy.for_tests().config)
    policy['prices'].update({'synthetic_location/geocoding':{'currency':'USD','rates_per_million':{'requests':'1'},'max_units':{'requests':1}},
        'synthetic_location/matrix':{'currency':'USD','rates_per_million':{'matrix_elements':'1'},'max_units':{'matrix_elements':30}}})
    app=create_app(settings,parser=base.parse,embedder=base.embed,answer_generator=base.generate,
        budget_policy=BudgetPolicy(policy),geocoding_provider=geo,route_provider=routes)
    actors={}
    for who in ('a','b'):
        invite=app.state.auth.invite(who+'@example.test')
        token=app.state.auth.complete_identity({'iss':base.PROVIDER,'sub':who,'email':who+'@example.test',
            'email_verified':True,'name':'합성 사용자 '+who.upper()},invite)
        with app.state.db.connect() as con:
            user=con.execute('SELECT * FROM users WHERE email=?',(who+'@example.test',)).fetchone()
            con.execute("UPDATE users SET role='admin' WHERE id=?",(user['id'],))
            session=con.execute('SELECT id FROM sessions WHERE token_hash=?',(digest(token),)).fetchone()
        actors[who]=SimpleNamespace(id=user['id'],session_id=session['id'],role='admin')
    actor=actors['a']
    for city in ('tokyo','madrid'):
        if any(p['city']==city for p in app.state.discovery.list_packs(actor)):continue
        data=pack('tokyo' if city=='tokyo' else 'barcelona');data['city']=city;data['version']='synthetic-stage2-browser-v1-'+city
        data['places']=data['places'][:3]
        for n,place in enumerate(data['places'],1):
            place['external_id']='synthetic-stage2-'+city+'-'+str(n)
            place['name']=place['native_name']=f'합성 {"東京" if city=="tokyo" else "Madrid"} {n} · 非常に長い名前の地域食堂と文化散歩'
            place['address']='SYNTHETIC ONLY '+city+' '+str(n)
            place['canonical_url']=f'https://example.org/stage2/{city}/{n}'
            if city=='madrid':place['latitude']=40.4168+n*.001;place['longitude']=-3.7038+n*.001
            for fact in place['facts']:
                if fact['field']=='opening_hours':
                    fact.pop('valid_for_date',None);fact['valid_from']='2026-11-10';fact['valid_until']='2026-11-13'
                if fact['field']=='closed':fact['value']=False
                if fact['field']=='max_party':fact['value']=6
        imported=app.state.discovery.import_pack(actor,data)
        packrow=next(p for p in app.state.discovery.list_packs(actor) if p['id']==imported['pack_id'])
        for place in packrow['places']:
            for source in place['sources']:
                app.state.discovery.review_source(actor,source['id'],{'expected_version':source['version'],'status':'active',
                    'read_confirmed':True,'display_permitted':True,'policy_version':packrow['version'],
                    'evidence':'Author-created synthetic browser fixture only, not live validation.'})
        app.state.discovery.approve_pack(actor,imported['pack_id'],{'status':'approved',
            'evidence':'Synthetic fixtures approved only for loopback browser verification.'})
    if not app.state.repo.list_trips(actor.id):
        trip=app.state.repo.create_trip(actor.id,{'title':'합성 도쿄 · 숙소 주변 저녁','start_date':'2026-11-10','end_date':'2026-11-13',
            'party':{'adults':2,'children':[],'children_status':'none'},
            'stops':[{'sequence':1,'city':'tokyo','start_date':'2026-11-10','end_date':'2026-11-13','timezone':'Asia/Tokyo'}]})
        # Visible initial preferences provide scoreable examples without a fake
        # location; accommodation entry/selection remains entirely a browser task.
        envelope=app.state.discovery.get_conditions(actor,trip['id'])
        conditions=envelope['conditions'];conditions['visit']['local_time']='18:00'
        conditions['preferred']['tags']=['noodles']
        conditions['budget']={'currency':'JPY','amount_max':'3000','basis':'per_person','period':'meal'}
        app.state.discovery.save_conditions(actor,trip['id'],{'expected_version':0,'conditions':conditions})
    @app.get('/__fixture/status',include_in_schema=False)
    def status():return report(base.SANDBOX)
    (base.SANDBOX/'stage2-browser-info.json').write_text(json.dumps({'base_url':base.BASE,
        'auth_path':'/api/v2/auth/login','fixture_dir':str(base.SANDBOX),'synthetic':True},ensure_ascii=False))
    return app,base


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',metavar='FIXTURE_DIRECTORY');args=parser.parse_args()
    if args.report:
        print(json.dumps(report(args.report),ensure_ascii=False,indent=2))
    else:
        import uvicorn
        app,base=build()
        print(json.dumps({'base_url':base.BASE,'auth_path':'/api/v2/auth/login','fixture_dir':str(base.SANDBOX),
            'synthetic':True,'providers':'fake-only; no external HTTP'},ensure_ascii=False),flush=True)
        threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8767,log_level='warning',access_log=False),daemon=True).start()
        uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning',access_log=False)
