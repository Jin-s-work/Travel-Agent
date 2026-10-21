"""Personal access tokens for the local MCP bridge (v6 stage 3, decision 3 = A).

Contract
- Only the SHA-256 digest of a token is stored. The raw value is returned once,
  in the creation response, and never again (no prefix or suffix is kept).
- Expiry is at most 30 days. ``last_used_at`` is refreshed at most once a minute.
- Revocation, expiry and user deactivation take effect on the next request.
- Each token owns an internal ``sessions`` row whose cookie hash is the digest
  of a discarded random value, so it can never be presented as a cookie. The
  row exists only so that the service layer's own session checks
  (``jobs._user``, ``documents.ensure_active_user``) keep working unchanged,
  and so that everything that already revokes sessions (user deactivation,
  role change, restore/import: delete rows and bump ``session_epoch``) also
  revokes tokens immediately.
- Bearer is accepted only for the explicit allowlist below: a few read GET
  routes whose handlers only SELECT, and POST ask. GET /trips/{trip}/today is
  deliberately absent: it runs the reservation-task revalidation
  (preparation.list -> _refresh), which UPDATEs reservation_tasks and INSERTs
  reservation_task_events when a manifest is stale.
- Writes a Bearer request itself still causes: ``last_used_at`` on this table
  (at most once a minute) and, for POST ask only, what ask always does (answer
  budget reservation, usage rows, possibly a search-index rebuild job). The
  agent route gets read tools only for a token actor, so no preview or draft
  row is created. tests/test_v6_personal_tokens.py pins that allowlisted GETs
  leave every other table unchanged.
  Every write route stays cookie + CSRF + Origin only. Raw mail download,
  session, SSE, administrator and token-management routes are not listed.
- Production default is OFF (``PERSONAL_TOKENS_ENABLED`` unset or ``0``): every
  Bearer request is then treated like a missing session (401) and creation is
  disabled. Listing and revoking keep working so old tokens can be cleaned up.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import threading
import time
import uuid

from .repository import DomainError

# Additive and idempotent: applied on every start after the versioned
# migrations (SQLite) and after the cloud schema (PostgreSQL), like
# llm_calls. Plain TEXT columns only; the PostgreSQL path runs it untranslated.
SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_access_tokens (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE, scopes_json TEXT NOT NULL, session_id TEXT,
 expires_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS personal_access_tokens_owner ON personal_access_tokens(owner_id, created_at);
"""

TOKEN_PREFIX = 'going_pat_'
MAX_TOKEN_LENGTH = 200
MAX_DAYS = 30
MAX_ACTIVE_PER_USER = 10
SCOPES = ('read', 'ask')
RATE_PER_MINUTE = 60        # every Bearer request of one token
ASK_PER_MINUTE = 10         # POST ask of one token (it may spend the answer budget)
LAST_USED_RESOLUTION_SECONDS = 60

# (method, route template) -> required scope. Route templates come from
# FastAPI's matched route, so path parameters cannot widen the list.
ALLOWED_ROUTES = {
    ('GET', '/api/v2/trips'): 'read',
    ('GET', '/api/v2/trips/{trip_id}'): 'read',
    ('GET', '/api/v2/trips/{trip_id}/bookings'): 'read',
    ('GET', '/api/v2/trips/{trip_id}/bookmarks'): 'read',
    ('GET', '/api/v2/trips/{trip_id}/places/{place_id}/detail'): 'read',
    ('GET', '/api/v2/trips/{trip_id}/itineraries'): 'read',
    ('GET', '/api/v2/trips/{trip_id}/itineraries/{ident}'): 'read',
    ('POST', '/api/v2/trips/{trip_id}/ask'): 'ask',
}


def _now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def enabled(settings):
    return bool(getattr(settings, 'personal_tokens_enabled', False))


def bearer_value(request):
    """Raw Bearer credential, or None when the request carries no Bearer header."""
    header = request.headers.get('authorization')
    if header is None:
        return None
    scheme, _, value = header.strip().partition(' ')
    if scheme.lower() != 'bearer':
        return None
    return value.strip()


def _unauthorized(message='토큰이 유효하지 않거나 만료·폐기되었습니다.'):
    return DomainError('AUTH_REQUIRED', message, 401)


class RateLimiter:
    """In-memory sliding window. Production runs one web process (WEB_CONCURRENCY=1)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def hit(self, key, limit, window=60.0):
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(1, int(hits[0] + window - now) + 1)
                raise DomainError('TOKEN_RATE_LIMITED', '토큰 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.', 429,
                                  {'retry_after_seconds': retry, 'limit_per_minute': limit})
            hits.append(now)


class PersonalTokens:
    def __init__(self, db, clock=_now):
        self.db, self.clock = db, clock
        self.limiter = RateLimiter()

    # ---- management (cookie session only; enforced by the route allowlist and here) ----
    @staticmethod
    def _cookie_only(actor):
        if getattr(actor, 'auth_kind', 'cookie') != 'cookie':
            raise DomainError('TOKEN_SCOPE_REJECTED', '개인 접근 토큰으로는 토큰을 관리할 수 없습니다.', 403)

    def _dto(self, row, now):
        if row['revoked_at']:
            state = 'revoked'
        elif row['expires_at'] <= now:
            state = 'expired'
        elif not row['session_live']:
            state = 'inactive'      # sessions revoked (deactivation, role change, restore)
        else:
            state = 'active'
        return {'id': row['id'], 'name': row['name'], 'scopes': json.loads(row['scopes_json']),
                'created_at': row['created_at'], 'expires_at': row['expires_at'],
                'last_used_at': row['last_used_at'], 'revoked_at': row['revoked_at'], 'state': state}

    def _rows(self, con, owner_id, token_id=None, limit=50):
        now = self.clock().isoformat()
        sql = ('SELECT t.*, CASE WHEN s.id IS NOT NULL AND s.epoch=u.session_epoch AND s.expires_at>? THEN 1 ELSE 0 END AS session_live '
               'FROM personal_access_tokens t JOIN users u ON u.id=t.owner_id '
               'LEFT JOIN sessions s ON s.id=t.session_id AND s.user_id=t.owner_id WHERE t.owner_id=?')
        params = [now, owner_id]
        if token_id is not None:
            sql += ' AND t.id=?'
            params.append(token_id)
        sql += ' ORDER BY t.created_at DESC, t.id DESC LIMIT ?'
        params.append(limit)
        return [self._dto(row, now) for row in con.execute(sql, params)]

    def listing(self, actor, *, is_enabled):
        self._cookie_only(actor)
        with self.db.connect() as con:
            items = self._rows(con, actor.id)
        return {'enabled': bool(is_enabled), 'items': items, 'max_days': MAX_DAYS,
                'max_active': MAX_ACTIVE_PER_USER, 'scopes': list(SCOPES)}

    def create(self, actor, *, name, days=MAX_DAYS, scopes=SCOPES, is_enabled):
        self._cookie_only(actor)
        if not is_enabled:
            raise DomainError('FEATURE_DISABLED', '개인 접근 토큰이 꺼져 있습니다.', 404)
        name = ' '.join(str(name or '').split())
        if not 1 <= len(name) <= 60:
            raise DomainError('VALIDATION_FAILED', '토큰 이름은 1~60자로 입력해 주세요.')
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS:
            raise DomainError('VALIDATION_FAILED', f'만료는 1~{MAX_DAYS}일 사이로 정해 주세요.')
        chosen = sorted(set(scopes or ()), key=SCOPES.index) if set(scopes or ()) <= set(SCOPES) else None
        if not chosen:
            raise DomainError('VALIDATION_FAILED', '권한은 read, ask 중에서 하나 이상 골라 주세요.')
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = self.clock()
        stamp, expires = now.isoformat(), (now + timedelta(days=days)).isoformat()
        token_id, session_id = 'pat_' + uuid.uuid4().hex, str(uuid.uuid4())
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            user = con.execute("SELECT id,session_epoch FROM users WHERE id=? AND status='active'", (actor.id,)).fetchone()
            if not user:
                raise DomainError('ACCESS_REVOKED', '서비스 이용 권한이 회수되었습니다.', 403)
            # Same liveness rule as _rows: a token whose internal session was
            # revoked (role change, deactivation, restore/import) is unusable and
            # must not hold a slot.
            active = con.execute(
                'SELECT count(*) FROM personal_access_tokens t JOIN sessions s ON s.id=t.session_id AND s.user_id=t.owner_id '
                'WHERE t.owner_id=? AND t.revoked_at IS NULL AND t.expires_at>? AND s.expires_at>? AND s.epoch=?',
                (actor.id, stamp, stamp, user['session_epoch'])).fetchone()[0]
            if active >= MAX_ACTIVE_PER_USER:
                raise DomainError('TOKEN_LIMIT', f'사용 중인 토큰은 최대 {MAX_ACTIVE_PER_USER}개입니다. 안 쓰는 토큰을 먼저 폐기해 주세요.', 409)
            # Internal session: its cookie hash is the digest of a value nobody keeps.
            con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES (?,?,?,?,?,?,?)',
                        (session_id, actor.id, digest(secrets.token_urlsafe(32)), secrets.token_urlsafe(32), expires, user['session_epoch'], stamp))
            con.execute('INSERT INTO personal_access_tokens(id,owner_id,name,token_hash,scopes_json,session_id,expires_at,last_used_at,revoked_at,created_at) '
                        'VALUES (?,?,?,?,?,?,?,NULL,NULL,?)',
                        (token_id, actor.id, name, digest(raw), json.dumps(chosen), session_id, expires, stamp))
        return {'id': token_id, 'name': name, 'scopes': chosen, 'created_at': stamp, 'expires_at': expires,
                'last_used_at': None, 'revoked_at': None, 'state': 'active', 'token': raw, 'token_shown_once': True}

    def revoke(self, actor, token_id):
        """Always allowed (even when the feature is OFF) so old tokens can be retired."""
        self._cookie_only(actor)
        stamp = self.clock().isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT id,session_id FROM personal_access_tokens WHERE id=? AND owner_id=?', (token_id, actor.id)).fetchone()
            if not row:
                raise DomainError('NOT_FOUND', '토큰을 찾을 수 없습니다.', 404)
            con.execute('UPDATE personal_access_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (stamp, token_id))
            if row['session_id']:
                con.execute('DELETE FROM sessions WHERE id=? AND user_id=?', (row['session_id'], actor.id))
            return self._rows(con, actor.id, token_id, 1)[0]

    # ---- Bearer verification ----
    def _lookup(self, con, token_hash):
        now = self.clock().isoformat()
        return con.execute(
            'SELECT u.id,u.email,u.display_name,u.role,s.id AS session_id,t.expires_at,t.id AS token_id,t.scopes_json,t.last_used_at '
            'FROM personal_access_tokens t JOIN users u ON u.id=t.owner_id '
            'JOIN sessions s ON s.id=t.session_id AND s.user_id=t.owner_id '
            "WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.expires_at>? AND s.expires_at>? AND s.epoch=u.session_epoch AND u.status='active'",
            (token_hash, now, now)).fetchone()

    def authenticate(self, raw, actor_type):
        if not raw or len(raw) > MAX_TOKEN_LENGTH or not raw.startswith(TOKEN_PREFIX):
            raise _unauthorized()
        token_hash = digest(raw)
        with self.db.connect() as con:
            row = self._lookup(con, token_hash)
            if not row:
                raise _unauthorized()
            now = self.clock()
            last = row['last_used_at']
            if not last or datetime.fromisoformat(last) <= now - timedelta(seconds=LAST_USED_RESOLUTION_SECONDS):
                con.execute('UPDATE personal_access_tokens SET last_used_at=? WHERE id=?', (now.isoformat(), row['token_id']))
        # csrf_token stays empty: write_guard rejects token actors before comparing it.
        return actor_type(id=row['id'], email=row['email'], display_name=row['display_name'], role=row['role'],
                          session_id=row['session_id'], csrf_token='', expires_at=row['expires_at'],
                          auth_kind='token', token_id=row['token_id'], scopes=tuple(json.loads(row['scopes_json'])))

    def revalidate(self, actor):
        """Re-check a token actor (perform_ask before and after answering)."""
        with self.db.connect() as con:
            row = con.execute(
                'SELECT 1 FROM personal_access_tokens t JOIN users u ON u.id=t.owner_id '
                'JOIN sessions s ON s.id=t.session_id AND s.user_id=t.owner_id '
                "WHERE t.id=? AND t.owner_id=? AND s.id=? AND t.revoked_at IS NULL AND t.expires_at>? AND s.expires_at>? "
                "AND s.epoch=u.session_epoch AND u.status='active'",
                (actor.token_id, actor.id, actor.session_id, self.clock().isoformat(), self.clock().isoformat())).fetchone()
        if not row:
            raise _unauthorized()

    def authorize(self, request, actor):
        """Allowlist + scope + per-token rate limit for a verified token actor."""
        self.limiter.hit(('all', actor.token_id), RATE_PER_MINUTE)
        route = request.scope.get('route')
        template = getattr(route, 'path', None)
        regex = getattr(route, 'path_regex', None)
        # Fail closed: the matched template must also match the actual request path
        # (guards against a router being mounted under an extra prefix later).
        scope = ALLOWED_ROUTES.get((request.method, template)) if regex is not None and regex.match(request.url.path) else None
        if scope is None:
            raise DomainError('TOKEN_SCOPE_REJECTED', '개인 접근 토큰은 읽기 조회와 질문에만 쓸 수 있습니다. 변경은 앱 화면에서 해 주세요.', 403)
        if scope not in actor.scopes:
            raise DomainError('TOKEN_SCOPE_REJECTED', '이 토큰에는 해당 권한이 없습니다.', 403)
        if scope == 'ask':
            self.limiter.hit(('ask', actor.token_id), ASK_PER_MINUTE)
