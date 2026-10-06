"""Phase08 disposable loopback fixture. All users, prices, routes and sources synthetic."""
import json
import threading
import uvicorn
import browser_travel_tools_fixture as prior
base,app,actor=prior.base,prior.app,prior.actor
with app.state.db.connect() as con:
    con.execute("UPDATE trips SET title='8단계 합성 검증 여행 · 도쿄' WHERE owner_id=?",(actor.id,))
    # Author-created fixture only; allows exercising the official-review workflow.
    # The candidate pack remains synthetic, so it is excluded from live analytics.
    source=con.execute("SELECT s.id FROM evidence_sources s JOIN place_identities p ON p.id=s.place_id WHERE p.city='tokyo' ORDER BY s.id LIMIT 1").fetchone()
    if source:con.execute("UPDATE evidence_sources SET source_type='official' WHERE id=?",(source[0],))
trip=app.state.repo.list_trips(actor.id)[0]
app.state.recommendations.submit(actor,trip['id'],{'trip_version':trip['version'],'conditions_version':1,'rating_filter':{'enabled':False}},'browser-feedback-initial')
if __name__=='__main__':
    print(json.dumps({'base_url':base.BASE,'fixture_dir':str(base.SANDBOX),'synthetic':True}),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8766,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8765,log_level='warning',access_log=False)
