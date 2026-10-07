"""Authlib OIDC authentication followed by independent invitation authorization."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid
from fastapi import Request
from authlib.integrations.starlette_client import OAuth
from .repository import DomainError


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Actor:
    id: str
    email: str
    display_name: str
    role: str
    session_id: str
    csrf_token: str
    expires_at: str


class Auth:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.oauth = OAuth()
        if settings.auth_configured:
            self.oauth.register('identity', client_id=settings.oidc_client_id,
                client_secret=settings.oidc_client_secret,
                server_metadata_url=settings.oidc_metadata_url,
                client_kwargs={'scope': 'openid email profile', 'code_challenge_method': 'S256'})

    def invite(self, email, hours=72):
        if '@' not in email or len(email) > 320 or not 1 <= hours <= 720:
            raise ValueError('유효한 이메일과 1~720시간을 입력해 주세요.')
        token = secrets.token_urlsafe(32)
        with self.db.connect() as con:
            con.execute('INSERT INTO invitations(id,email,token_hash,expires_at,created_at) VALUES (?,?,?,?,?)',
                (str(uuid.uuid4()), email.strip().casefold(), digest(token), (now()+timedelta(hours=hours)).isoformat(), now().isoformat()))
        return token

    def revoke_invitation(self, token):
        with self.db.connect() as con:
            con.execute('UPDATE invitations SET revoked_at=? WHERE token_hash=?', (now().isoformat(), digest(token)))

    def disable_user(self, user_id):
        with self.db.connect() as con:
            con.execute("UPDATE users SET status='disabled',session_epoch=session_epoch+1,updated_at=? WHERE id=?", (now().isoformat(), user_id))
            con.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))

    def complete_identity(self, claims, invitation, *, invitation_hash=None):
        # Only claims returned by Authlib's verified ID token path may enter here.
        if not claims.get('sub') or not claims.get('iss') or not claims.get('email') or claims.get('email_verified') is not True:
            raise DomainError('INVITATION_REQUIRED', '확인된 이메일이 있는 초대 계정만 이용할 수 있습니다.', 403)
        stamp = now().isoformat()
        provider, subject = str(claims['iss']), str(claims['sub'])
        email = str(claims['email']).casefold()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            user = con.execute('SELECT * FROM users WHERE auth_provider=? AND auth_subject=?', (provider,subject)).fetchone()
            if user and user['status'] != 'active':
                raise DomainError('ACCESS_REVOKED', '서비스 이용 권한이 회수되었습니다.', 403)
            if not user:
                invited = con.execute('SELECT * FROM invitations WHERE token_hash=? AND email=? AND used_at IS NULL AND revoked_at IS NULL AND expires_at>?', (invitation_hash or digest(invitation or ''),email,stamp)).fetchone()
                if not invited:
                    raise DomainError('INVITATION_REQUIRED', '초대가 없거나 만료되었습니다. 초대받은 계정으로 로그인해 주세요.',403)
                uid = str(uuid.uuid4())
                con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,display_name,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', (uid,email,provider,subject,str(claims.get('name') or email)[:200],stamp,stamp))
                con.execute('UPDATE invitations SET used_at=?,user_id=? WHERE id=? AND used_at IS NULL', (stamp,uid,invited['id']))
                user = con.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
            token = secrets.token_urlsafe(32)
            sid, csrf = str(uuid.uuid4()), secrets.token_urlsafe(32)
            expires = (now()+timedelta(hours=self.settings.session_hours)).isoformat()
            con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES (?,?,?,?,?,?,?)', (sid,user['id'],digest(token),csrf,expires,user['session_epoch'],stamp))
        return token

    def actor(self, request):
        if not self.settings.auth_configured:
            raise DomainError('AUTH_REQUIRED','인증 제공자 설정이 필요합니다.',401)
        token = request.cookies.get(self.settings.cookie_name, '')
        if not token or len(token) > 200:
            raise DomainError('AUTH_REQUIRED','로그인이 필요합니다.',401)
        with self.db.connect() as con:
            row = con.execute('SELECT u.id,u.email,u.display_name,u.role,s.id AS session_id,s.csrf_token,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND s.epoch=u.session_epoch AND u.status=\'active\'', (digest(token),now().isoformat())).fetchone()
        if not row:
            raise DomainError('AUTH_REQUIRED','세션이 만료되었거나 회수되었습니다. 다시 로그인해 주세요.',401)
        return Actor(**dict(row))

    def write_guard(self, request, actor):
        if request.headers.get('origin') != self.settings.public_base_url:
            raise DomainError('ORIGIN_REJECTED','요청 출처를 확인할 수 없습니다.',403)
        if not secrets.compare_digest(request.headers.get('x-csrf-token',''), actor.csrf_token):
            raise DomainError('CSRF_REJECTED','화면을 새로고침한 뒤 다시 시도해 주세요.',403)


def require_actor(request: Request):
    actor = request.app.state.auth.actor(request)
    if request.method not in {'GET','HEAD','OPTIONS'}:
        request.app.state.auth.write_guard(request,actor)
    return actor
