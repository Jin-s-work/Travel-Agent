"""Phase07 loopback browser fixture. Never imported in production; no paid calls."""
from datetime import datetime,timedelta,timezone
import json
import threading
import uvicorn
import browser_itinerary_fixture as prior
from tests.discovery_synthetic import conditions

base,app,actor=prior.base,prior.app,prior.discovery.actor
app.state.settings.offline_enabled=True
for pack in app.state.discovery.list_packs(actor):
    for place in pack['places']:
        source=place['sources'][0]
        app.state.today.grant(actor,{'source_id':source['id'],'source_version':source['version'],'fields':['name','native_name','address'],'expires_at':(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(),'policy_version':source['policy_version']})
        if not any(f['field']=='booking_open_rule' for f in place['facts']):
            app.state.discovery.add_fact(actor,place['place_id'],{'field':'booking_open_rule','value':{'type':'rolling_days','days_before_visit':30,'timezone':'Asia/Tokyo' if pack['city']=='tokyo' else 'Europe/Madrid','explicit_local_time':'10:00'},'status':'verified','source_key':source['source_key'],'checked_at':source['checked_at'],'expires_at':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(),'valid_from':'2026-11-01','valid_until':'2026-11-30','valid_for_date':None})
trips=app.state.repo.list_trips(actor.id)
if not trips:
    trip=app.state.repo.create_trip(actor.id,{'title':'7단계 합성 검증 여행 · 도쿄','start_date':'2026-11-06','end_date':'2026-11-09','party':{'adults':4,'children':[]}})
    cond=app.state.discovery.save_conditions(actor,trip['id'],{'expected_version':0,'conditions':conditions()})
    places=app.state.discovery.catalog(actor,trip['id'],'tokyo');p=next(p for p in places if ' 1 ' in p['name'])
    app.state.itineraries.submit(actor,trip['id'],{'trip_version':trip['version'],'conditions_version':1,'start_date':'2026-11-06','end_date':'2026-11-06','selected':[{'place_id':p['place_id'],'duration_minutes':60,'duration_origin':'user'}],'buffers':{'general_minutes':0},'rest_preferences':{'minutes':0},'allow_provisional':True},'browser-tools-seed')
if __name__=='__main__':
    print(json.dumps({'base_url':base.BASE,'fixture_dir':str(base.SANDBOX),'synthetic':True}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8766,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8765,log_level='warning',access_log=False)
