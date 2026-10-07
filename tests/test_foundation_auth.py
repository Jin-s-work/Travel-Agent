"""Authentication ≠ invitation authorization; all identities here are synthetic."""
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
def test_unverified_identity_is_not_invited(auth,verified):
    invitation=auth.invite('a@example.test')
    with pytest.raises(DomainError):auth.complete_identity(claims(verified=verified),invitation)
    with auth.db.connect() as con: assert con.execute('SELECT count(*) FROM users').fetchone()[0]==0

def test_wrong_email_expired_and_revoked_invites(auth):
    invitation=auth.invite('a@example.test')
    with pytest.raises(DomainError):auth.complete_identity(claims('b'),invitation)
    auth.revoke_invitation(invitation)
    with pytest.raises(DomainError):auth.complete_identity(claims(),invitation)
    invitation=auth.invite('a@example.test')
    with auth.db.connect() as con:con.execute('UPDATE invitations SET expires_at=?',((now()-timedelta(hours=1)).isoformat(),))
    with pytest.raises(DomainError):auth.complete_identity(claims(),invitation)

def test_invitation_single_consumer_under_competing_subjects(auth):
    invitation=auth.invite('a@example.test')
    def consume(subject):
        c=claims();c['sub']=subject
        try:return auth.complete_identity(c,invitation)
        except DomainError:return None
    with ThreadPoolExecutor(2) as executor: results=list(executor.map(consume,['first','second']))
    assert sum(result is not None for result in results)==1
    with auth.db.connect() as con:
        assert con.execute('SELECT count(*) FROM users').fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM invitations WHERE used_at IS NOT NULL').fetchone()[0]==1

def test_existing_subject_needs_no_new_invite_but_disabled_is_denied(auth):
    auth.complete_identity(claims(),auth.invite('a@example.test'))
    auth.complete_identity(claims(),None)
    with auth.db.connect() as con:uid=con.execute('SELECT id FROM users').fetchone()[0]
    auth.disable_user(uid)
    with pytest.raises(DomainError):auth.complete_identity(claims(),None)
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

def test_invitation_post_has_origin_check(auth,tmp_path):
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
    app.state.auth.complete_identity(claims(),app.state.auth.invite('a@example.test'))
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
