"""Verified OIDC sign-in, ordinary-member registration and revocable sessions."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid
from fastapi import Request
from authlib.integrations.starlette_client import OAuth
from .repository import DomainError
from .tokens import PersonalTokens, bearer_value, enabled as tokens_enabled


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
    # 'cookie' (browser session) or 'token' (personal access token, read/ask only).
    auth_kind: str = 'cookie'
    token_id: str | None = None
    scopes: tuple = ()


class Auth:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.tokens = PersonalTokens(db)
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

    def complete_identity(self, claims, invitation=None, *, invitation_hash=None):
        # Only claims returned by Authlib's verified ID token path may enter here.
        # Legacy invitation arguments remain compatible with maintenance callers;
        # they no longer grant or restrict access. Roles come from the database.
        if not claims.get('sub') or not claims.get('iss') or not claims.get('email') or claims.get('email_verified') is not True:
            raise DomainError('IDENTITY_UNVERIFIED', '이메일 인증이 완료된 Google 계정으로 로그인해 주세요.', 403)
        stamp = now().isoformat()
        provider, subject = str(claims['iss']), str(claims['sub'])
        email = str(claims['email']).casefold()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            user = con.execute('SELECT * FROM users WHERE auth_provider=? AND auth_subject=?', (provider,subject)).fetchone()
            if user and user['status'] != 'active':
                raise DomainError('ACCESS_REVOKED', '서비스 이용 권한이 회수되었습니다.', 403)
            if not user:
                uid = str(uuid.uuid4())
                con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,display_name,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', (uid,email,provider,subject,str(claims.get('name') or email)[:200],stamp,stamp))
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

    def token_actor(self, request, raw):
        # OFF (production default): a Bearer request is treated like a missing session.
        if not tokens_enabled(self.settings) or not self.settings.auth_configured:
            raise DomainError('AUTH_REQUIRED','로그인이 필요합니다.',401)
        return self.tokens.authenticate(raw, Actor)

    def revalidate(self, request, actor):
        """Re-check the caller with the same credential kind that admitted it."""
        if actor.auth_kind == 'token':
            if not tokens_enabled(self.settings):
                raise DomainError('AUTH_REQUIRED','로그인이 필요합니다.',401)
            self.tokens.revalidate(actor)
            return actor
        return self.actor(request)

    def write_guard(self, request, actor):
        # Writes are cookie + CSRF + Origin only. Token actors never reach the
        # comparison, and an empty CSRF value never matches (compare_digest('','')).
        if actor.auth_kind != 'cookie':
            raise DomainError('TOKEN_SCOPE_REJECTED','개인 접근 토큰으로는 변경할 수 없습니다. 앱 화면에서 해 주세요.',403)
        if request.headers.get('origin') != self.settings.public_base_url:
            raise DomainError('ORIGIN_REJECTED','요청 출처를 확인할 수 없습니다.',403)
        supplied = request.headers.get('x-csrf-token','')
        if not actor.csrf_token or not supplied or not secrets.compare_digest(supplied, actor.csrf_token):
            raise DomainError('CSRF_REJECTED','화면을 새로고침한 뒤 다시 시도해 주세요.',403)


def require_actor(request: Request):
    auth = request.app.state.auth
    raw = bearer_value(request)
    if raw is not None:
        # Bearer never falls back to the cookie and never passes write_guard:
        # only the allowlisted read GETs and POST ask are reachable.
        actor = auth.token_actor(request, raw)
        auth.tokens.authorize(request, actor)
        return actor
    actor = auth.actor(request)
    if request.method not in {'GET','HEAD','OPTIONS'}:
        auth.write_guard(request,actor)
    return actor
