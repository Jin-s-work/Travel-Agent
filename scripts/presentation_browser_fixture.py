"""Public documentation capture fixture: synthetic identity, mail and bookings.
Run with PYTHON_DOTENV_DISABLED=1. Does not load production secrets or call AI.
Uses the current real UI and local parser. Restaurant facts retain public citations.
"""
import json, threading, time
import mail_design_browser_fixture as fixture
app, base, actor = fixture.app, fixture.base, fixture.actor
trip=next(t for t in app.state.repo.list_trips(actor.id) if t['stops'][0]['city']=='tokyo')
trip=app.state.repo.update_trip(actor.id,trip['id'],{'expected_version':trip['version'],'title':'발표 예시 · 도쿄 3박 4일'})
if not app.state.repo.list_bookings(actor.id,trip['id']):
    for name,start,end in [('아사쿠사 산책 투어 (예시)','10:00','11:30'),('점심 예약 (예시)','12:00','13:00'),('미술관 입장 (예시)','15:00','16:30')]:
        app.state.repo.create_booking(actor.id,trip['id'],{'kind':'음식점' if name.startswith('점심') else '투어','provider':name,'date':'2026-11-07','time':start,'time_end':end,'status':'user_confirmed','events':[{'event_type':'visit','start_local':'2026-11-07T'+start+':00','end_local':'2026-11-07T'+end+':00','start_timezone':'Asia/Tokyo','end_timezone':'Asia/Tokyo','location':'발표용 가상 장소'}]})
    accepted=[]
    for name in ('01-tokyo-roundtrip.eml','02-tokyo-hotel.eml','03-tokyo-dinner.eml'):
        doc=app.state.documents.save(actor.id,trip['id'],name,(fixture.ROOT/'examples/mail-test-pack'/name).read_bytes())
        accepted.append({'document_id':doc['id'],'filename':name})
    trip=app.state.repo.get_trip(actor.id,trip['id'])
    app.state.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip['id'],'documents',{'accepted':accepted,'duplicates':[],'rejected':[]},trip['version'],'presentation-mail-v1')

def prepare_itinerary():
    for _ in range(180):
        with app.state.db.connect() as con:
            busy=con.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
        if not busy: break
        time.sleep(.5)
    if not app.state.itineraries.list(actor,trip['id'])['items']:
        current=app.state.repo.get_trip(actor.id,trip['id'])
        conditions=app.state.discovery.get_conditions(actor,trip['id'])
        app.state.itineraries.submit(actor,trip['id'],{'trip_version':current['version'],'conditions_version':conditions['version'],'start_date':'2026-11-07','end_date':'2026-11-07','selected':[],'allow_provisional':True},'presentation-itinerary-v1')

if __name__=='__main__':
    import uvicorn
    print(json.dumps({'url':base.BASE,'fixture_dir':str(base.SANDBOX),'data':'synthetic account, mail and reservations; real public restaurant facts','paid_calls':0}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8767,log_level='warning',access_log=False),daemon=True).start()
    threading.Thread(target=prepare_itinerary,daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8766,log_level='warning',access_log=False)
