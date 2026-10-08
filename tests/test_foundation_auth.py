"""Verified open registration and private sessions; all identities are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from src.foundation.settings import Settings
from src.foundation.db import Database
from src.foundation.auth import Auth, now
from src.foundation.repository import DomainError

@pytest.fixture
def auth(tmp_path):
    return Auth(Database(tmp_path/'auth.sqlite'),Settings(database_path=tmp_path/'auth.sqlite',environment='development',public_base_url='http://testserver',oidc_client_id='fixture',oidc_client_secret='fixture',session_secret='s'*40))

def claims(who='a',verified=True):
    return {'iss':'https://synthetic.test','sub':who,'email':who+'@example.test','email_verified':verified}

@pytest.mark.parametrize('verified',[False,'true',None])
def test_unverified_identity_cannot_register(auth,verified):
    with pytest.raises(DomainError) as error:auth.complete_identity(claims(verified=verified))
    assert error.value.code=='IDENTITY_UNVERIFIED'
    with auth.db.connect() as con:
        assert con.execute('SELECT count(*) FROM users').fetchone()[0]==0
        assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==0

@pytest.mark.parametrize('field',['iss','sub','email'])
def test_incomplete_identity_cannot_register(auth,field):
    identity=claims();identity.pop(field)
    with pytest.raises(DomainError):auth.complete_identity(identity)

def test_new_verified_account_registers_as_member_without_invitation(auth):
    identity={**claims(),'role':'admin','owner_id':'someone-else'}
    assert auth.complete_identity(identity)
    with auth.db.connect() as con:
        user=con.execute('SELECT * FROM users').fetchone()
        assert user['role']=='member' and user['id']!='someone-else'
        assert user['status']=='active'
        assert con.execute('SELECT count(*) FROM invitations').fetchone()[0]==0

def test_legacy_invites_do_not_gate_registration_or_grant_permissions(auth):
    invitation=auth.invite('a@example.test')
    auth.revoke_invitation(invitation)
    with auth.db.connect() as con:con.execute('UPDATE invitations SET expires_at=?',((now()-timedelta(hours=1)).isoformat(),))
    auth.complete_identity(claims('b'),invitation)
    auth.complete_identity(claims(),None,invitation_hash='irrelevant')
    with auth.db.connect() as con:
        assert con.execute("SELECT count(*) FROM users WHERE role='member'").fetchone()[0]==2
        assert con.execute('SELECT count(*) FROM invitations WHERE used_at IS NOT NULL').fetchone()[0]==0

def test_concurrent_first_logins_create_one_identity_and_independent_sessions(auth):
    with ThreadPoolExecutor(2) as executor:
        results=list(executor.map(lambda _:auth.complete_identity(claims()),range(2)))
    assert len(set(results))==2
    with auth.db.connect() as con:
        assert con.execute('SELECT count(*) FROM users').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==2

def test_matching_email_does_not_link_distinct_provider_identities(auth):
    for change in [{},{'sub':'other'},{'iss':'https://other-provider.test'}]:
        auth.complete_identity({**claims(),**change})
    with auth.db.connect() as con:assert con.execute('SELECT count(*) FROM users').fetchone()[0]==3

def test_existing_disabled_identity_remains_denied_after_open_registration(auth):
    auth.complete_identity(claims())
    with auth.db.connect() as con:uid=con.execute('SELECT id FROM users').fetchone()[0]
    auth.disable_user(uid)
    with pytest.raises(DomainError) as error:auth.complete_identity(claims())
    assert error.value.code=='ACCESS_REVOKED'
    with auth.db.connect() as con:assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==0

def test_production_configuration_fails_closed_and_cookie_policy(tmp_path):
    from api import create_app
    settings=Settings(database_path=tmp_path/'app.sqlite',environment='production',public_base_url='http://insecure.test',oidc_client_id='x',oidc_client_secret='x',session_secret='s'*40)
    assert not settings.auth_configured
    assert settings.cookie_name=='__Host-session'
    app=create_app(settings);client=TestClient(app)
    assert client.get('/api/v2/session').json()['authenticated'] is False
    for method,path in [('GET','/api/bookings'),('GET','/api/v2/trips'),('DELETE','/api/index'),('POST','/api/ask')]:
        res=client.request(method,path)
        assert res.status_code==401
        assert res.headers['cache-control']=='private, no-store'
    assert client.get('/api/v2/auth/login').status_code==503

def test_login_post_still_has_origin_check(auth,tmp_path):
    from api import create_app
    app=create_app(auth.settings)
    response=TestClient(app).post('/api/v2/auth/login',data={'invitation':'secret'},headers={'Origin':'https://evil.test'})
    assert response.status_code==403
    assert 'secret' not in response.text


def test_production_callback_cookie_and_replay_of_invalid_state(tmp_path,monkeypatch):
    from api import create_app
    settings=Settings(database_path=tmp_path/'prod.sqlite',environment='production',public_base_url='https://testserver',oidc_client_id='fixture',oidc_client_secret='fixture',session_secret='s'*40)
    app=create_app(settings)
    client=TestClient(app,base_url='https://testserver')
    # No state: Authlib rejects callback before any identity or session is created.
    rejected=client.get('/api/v2/auth/callback?code=synthetic-invalid&state=unknown',follow_redirects=False)
    assert rejected.status_code==303 and 'auth_error=' in rejected.headers['location']
    app.state.auth.complete_identity(claims())
    async def verified_claims(request): return {'userinfo':claims()}
    # Mock only the verified-provider boundary; session and cookie code are real.
    monkeypatch.setattr(app.state.auth.oauth.identity,'authorize_access_token',verified_claims)
    response=client.get('/api/v2/auth/callback',follow_redirects=False)
    cookie=response.headers['set-cookie']
    assert '__Host-session=' in cookie and 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=lax' in cookie and 'Path=/' in cookie
    assert 'Domain=' not in cookie
    state=client.get('/api/v2/session').json()
    assert state['authenticated']
    assert client.get('/').headers['referrer-policy']=='strict-origin-when-cross-origin'


def production_app(tmp_path):
    from api import create_app
    return create_app(Settings(database_path=tmp_path/'multi-device.sqlite',environment='production',
        public_base_url='https://testserver',oidc_client_id='fixture',oidc_client_secret='fixture',session_secret='s'*40))


def test_existing_identity_can_sign_in_on_fresh_devices_without_invitation(tmp_path,monkeypatch):
    app=production_app(tmp_path)
    app.state.auth.complete_identity(claims())
    async def verified(_request):return {'userinfo':claims()}
    monkeypatch.setattr(app.state.auth.oauth.identity,'authorize_access_token',verified)
    first=TestClient(app,base_url='https://testserver')
    second=TestClient(app,base_url='https://testserver')
    for client in [first,second]:
        assert not client.get('/api/v2/session').json()['authenticated']
        response=client.get('/api/v2/auth/callback',follow_redirects=False)
        assert response.headers['location']=='/'
        assert client.get('/api/v2/session').json()['authenticated']
    a,b=first.get('/api/v2/session').json(),second.get('/api/v2/session').json()
    assert a['user']['id']==b['user']['id']
    assert a['csrf_token']!=b['csrf_token']
    assert first.cookies.get('__Host-session')!=second.cookies.get('__Host-session')
    response=first.post('/api/v2/auth/logout',headers={'Origin':'https://testserver','X-CSRF-Token':a['csrf_token']})
    assert response.status_code==204
    assert not first.get('/api/v2/session').json()['authenticated']
    assert second.get('/api/v2/session').json()['authenticated']


@pytest.mark.parametrize('kind,expected',[
    ('unverified','identity_unverified'),('revoked','access_revoked'),('cancel','login_cancelled'),
    ('configuration','provider_configuration'),('transport','provider_unavailable'),('unexpected','login_failed')])
def test_callback_failure_is_actionable_and_never_logs_provider_secrets(tmp_path,monkeypatch,caplog,kind,expected):
    from authlib.integrations.base_client.errors import OAuthError
    from httpx import ConnectError
    app=production_app(tmp_path)
    secret='synthetic-credential-never-log'
    failures={'unverified':DomainError('IDENTITY_UNVERIFIED',secret,403),
        'revoked':DomainError('ACCESS_REVOKED',secret,403),'cancel':OAuthError(error='access_denied',description=secret),
        'configuration':OAuthError(error='invalid_client',description=secret),'transport':ConnectError(secret),
        'unexpected':RuntimeError(secret)}
    async def rejected(_request):raise failures[kind]
    monkeypatch.setattr(app.state.auth.oauth.identity,'authorize_access_token',rejected)
    client=TestClient(app,base_url='https://testserver')
    response=client.get('/api/v2/auth/callback?code=synthetic-code&state=synthetic-state',follow_redirects=False)
    assert response.status_code==303
    assert 'auth_error='+expected in response.headers['location']
    assert response.headers['x-request-id'] in response.headers['location']
    assert secret not in caplog.text and secret not in response.text
    assert '__Host-session=' not in response.headers.get('set-cookie','')
    assert not client.get('/api/v2/session').json()['authenticated']
    assert 'code='+expected in caplog.text


def test_missing_state_cookie_has_recovery_code_but_never_bypasses_state_check(tmp_path):
    app=production_app(tmp_path)
    response=TestClient(app,base_url='https://testserver').get('/api/v2/auth/callback?state=unknown&code=invalid',follow_redirects=False)
    assert 'auth_error=cookie_missing' in response.headers['location']
    with app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM sessions').fetchone()[0]==0


def test_provider_discovery_failure_returns_to_working_login_screen(tmp_path,monkeypatch):
    from httpx import ConnectTimeout
    app=production_app(tmp_path)
    async def unavailable(_request,_callback):raise ConnectTimeout('no sensitive error output')
    monkeypatch.setattr(app.state.auth.oauth.identity,'authorize_redirect',unavailable)
    response=TestClient(app,base_url='https://testserver').get('/api/v2/auth/login',follow_redirects=False)
    assert response.status_code==303
    assert 'auth_error=provider_unavailable' in response.headers['location']


def test_first_login_callback_registers_without_code_and_isolates_trips(tmp_path,monkeypatch):
    app=production_app(tmp_path)
    who='a'
    async def verified(_request):return {'userinfo':claims(who)}
    monkeypatch.setattr(app.state.auth.oauth.identity,'authorize_access_token',verified)
    first=TestClient(app,base_url='https://testserver')
    second=TestClient(app,base_url='https://testserver')
    for name,client in [('a',first),('b',second)]:
        who=name
        assert client.get('/api/v2/auth/callback',follow_redirects=False).headers['location']=='/'
        session=client.get('/api/v2/session').json()
        assert session['authenticated'] and session['user']['role']=='member'
        client.headers.update({'Origin':'https://testserver','X-CSRF-Token':session['csrf_token']})
    response=first.post('/api/v2/trips',json={'title':'Synthetic private trip','start_date':'2026-11-06','end_date':'2026-11-09'})
    assert response.status_code==201,response.text
    trip_id=response.json()['id']
    assert first.get('/api/v2/trips/'+trip_id).status_code==200
    assert second.get('/api/v2/trips/'+trip_id).status_code==404
    assert second.get('/api/v2/trips').json()['items']==[]
    with app.state.db.connect() as con:
        assert con.execute('SELECT count(*) FROM invitations').fetchone()[0]==0
        assert con.execute('SELECT count(*) FROM users').fetchone()[0]==2


def test_login_post_without_invitation_starts_oidc_and_preserves_provider_state(tmp_path,monkeypatch):
    from fastapi.responses import RedirectResponse
    app=production_app(tmp_path)
    async def authorize(request,callback):
        assert dict(request.session)=={}
        assert callback=='https://testserver/api/v2/auth/callback'
        request.session['_state_synthetic']='provider-owned-state'
        return RedirectResponse('https://synthetic.test/authorize',status_code=302)
    monkeypatch.setattr(app.state.auth.oauth.identity,'authorize_redirect',authorize)
    response=TestClient(app,base_url='https://testserver').post('/api/v2/auth/login',headers={'Origin':'https://testserver'},follow_redirects=False)
    assert response.status_code==302
    assert response.headers['location']=='https://synthetic.test/authorize'
    cookie=response.headers['set-cookie']
    assert '__Host-oidc=' in cookie and 'httponly' in cookie.lower() and 'secure' in cookie.lower()
