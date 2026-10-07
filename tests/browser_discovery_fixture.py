"""Phase04 local browser harness; synthetic identities and packs, no paid calls."""
import json
from types import SimpleNamespace
import threading
import uvicorn
import browser_fixture as base
from discovery_synthetic import pack

app = base.app
token = app.state.auth.complete_identity({'iss': base.PROVIDER, 'sub': 'a', 'email': 'a@example.test',
    'email_verified': True, 'name': '합성 사용자 A'}, base.invites['a'])
from src.foundation.auth import digest
with app.state.db.connect() as con:
    user = con.execute("SELECT * FROM users WHERE email='a@example.test'").fetchone()
    con.execute("UPDATE users SET role='admin' WHERE id=?", (user['id'],))
    session = con.execute('SELECT * FROM sessions WHERE token_hash=?', (digest(token),)).fetchone()
actor = SimpleNamespace(id=user['id'], session_id=session['id'], role='admin')

# Setup exercises the same service validation as the administrator HTTP API.
# Both synthetic packs must stay labelled synthetic in every consumer view.
for city in ('tokyo', 'barcelona'):
    if any(p['city']==city for p in app.state.discovery.list_packs(actor)):continue
    result = app.state.discovery.import_pack(actor, pack(city))
    imported = next(item for item in app.state.discovery.list_packs(actor) if item['id'] == result['pack_id'])
    for place in imported['places']:
        for source in place['sources']:
            app.state.discovery.review_source(actor, source['id'], {'expected_version': source['version'],
                'status': 'active', 'read_confirmed': True, 'display_permitted': True,
                'policy_version': imported['version'], 'evidence': 'Synthetic fixture only; author-created test source.'})
    app.state.discovery.approve_pack(actor, result['pack_id'], {
        'status': 'approved', 'evidence': 'Explicit synthetic browser fixture approval; not live candidate validation.'})

if __name__ == '__main__':
    print(json.dumps({'base_url': base.BASE, 'invites': base.invites,
        'fixture_dir': str(base.SANDBOX), 'synthetic': True}, ensure_ascii=False), flush=True)
    threading.Thread(target=lambda: uvicorn.run(base.idp, host='127.0.0.1', port=8766,
        log_level='warning', access_log=False), daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=8765, log_level='warning', access_log=False)
