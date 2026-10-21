"""v6 stage 3 (S3-08): personal access tokens for the local MCP bridge.

Synthetic users A/B, temporary SQLite, fake parser/embedder/answer generator.
No network and no paid call. Identity claims enter through Auth's internal
verified-claims boundary exactly as in tests/test_foundation_api.py.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.foundation import tokens as tokens_module
from src.foundation.settings import Settings

ORIGIN = "http://testserver"


def _fact(i=0):
    return {"kind": "투어", "provider": f"Synthetic Tour {i}", "confirmation_number": f"SYN-{i}",
            "date": "2026-11-07", "date_end": None, "time": "09:00", "time_end": None,
            "location": "Tokyo", "refund_policy": "Synthetic refundable policy",
            "raw_snippet": "Synthetic reservation", "status": "needs_review", "events": [],
            "stable_item_key": f"fixture-item-{i}"}


def _make_service(tmp_path, monkeypatch, *, enabled=True):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "module-default.sqlite3"))
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path / "module-default-docs"))
    monkeypatch.setenv("VECTORS_DIR", str(tmp_path / "module-default-vectors"))
    from api import create_app
    from src.reliability.budget import BudgetPolicy

    def parse(raw):
        return copy.deepcopy([_fact()])

    def embed(texts):
        return [[0.1] * 8 for _ in texts]

    def generate(question, hits):
        return "현재 예약 근거: " + " | ".join(hit["document"] for hit in hits)

    settings = Settings(database_path=tmp_path / "service.sqlite3", documents_dir=tmp_path / "documents",
                        vectors_dir=tmp_path / "vectors", environment="development",
                        public_base_url=ORIGIN, oidc_client_id="fixture-client",
                        oidc_client_secret="fixture-secret", session_secret="s" * 40, job_poll_seconds=0.01,
                        personal_tokens_enabled=enabled)
    app = create_app(settings, parser=parse, embedder=embed, answer_generator=generate,
                     budget_policy=BudgetPolicy.for_tests())
    return app, settings


@pytest.fixture
def service(tmp_path, monkeypatch):
    app, settings = _make_service(tmp_path, monkeypatch)
    lifetime = TestClient(app)
    lifetime.__enter__()
    clients = []

    def login(name):
        cookie = app.state.auth.complete_identity({"iss": "https://fixture.example.test", "sub": name,
            "email": name + "@example.test", "email_verified": True, "name": name})
        client = TestClient(app)
        client.cookies.set(settings.cookie_name, cookie)
        session = client.get("/api/v2/session").json()
        assert session["authenticated"]
        client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": session["csrf_token"]})
        clients.append(client)
        return SimpleNamespace(client=client, user=session["user"], cookie=cookie)

    def bearer(raw):
        client = TestClient(app)          # no cookie, no CSRF, no Origin
        client.headers.update({"Authorization": "Bearer " + raw})
        clients.append(client)
        return client

    yield SimpleNamespace(app=app, settings=settings, login=login, bearer=bearer)
    for client in clients:
        client.close()
    lifetime.__exit__(None, None, None)


def _trip(client, title="Tokyo"):
    response = client.post("/api/v2/trips", json={"title": title, "start_date": "2026-11-06",
        "end_date": "2026-11-09", "party": {"adults": 2, "children": []}})
    assert response.status_code == 201, response.text
    return response.json()


def _token(member, **body):
    response = member.client.post("/api/v2/personal-tokens", json={"name": "MCP 로컬", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _db_rows(service, sql, params=()):
    with service.app.state.db.connect() as con:
        return [dict(row) for row in con.execute(sql, params)]


# ---------------------------------------------------------------- settings / defaults

def test_flag_defaults_off_and_production_profile_pins_zero(monkeypatch):
    monkeypatch.delenv("PERSONAL_TOKENS_ENABLED", raising=False)
    assert Settings().personal_tokens_enabled is False
    for value, expected in (("0", False), ("", False), ("false", False), ("1", True), ("true", True), ("TRUE", True)):
        monkeypatch.setenv("PERSONAL_TOKENS_ENABLED", value)
        assert Settings().personal_tokens_enabled is expected
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("prepare_render_env", Path(__file__).resolve().parents[1] / "scripts" / "prepare_render_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RUNTIME_DEFAULTS["PERSONAL_TOKENS_ENABLED"] == "0"


def test_disabled_flag_rejects_every_bearer_and_creation_but_allows_listing_and_revoke(service):
    a = service.login("token-a")
    trip = _trip(a.client)
    created = _token(a)
    assert service.bearer(created["token"]).get("/api/v2/trips").status_code == 200
    service.settings.personal_tokens_enabled = False
    for path in ("/api/v2/trips", f"/api/v2/trips/{trip['id']}"):
        response = service.bearer(created["token"]).get(path)
        assert response.status_code == 401 and response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert service.bearer(created["token"]).post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "예약?"}).status_code == 401
    off = a.client.post("/api/v2/personal-tokens", json={"name": "x"})
    assert off.status_code == 404 and off.json()["error"]["code"] == "FEATURE_DISABLED"
    listing = a.client.get("/api/v2/personal-tokens").json()
    assert listing["enabled"] is False and [item["id"] for item in listing["items"]] == [created["id"]]
    assert a.client.delete(f"/api/v2/personal-tokens/{created['id']}").json()["state"] == "revoked"


def test_app_built_from_environment_without_the_flag_is_off(tmp_path, monkeypatch):
    """create_app() reading the environment (no Settings passed) with the flag unset."""
    monkeypatch.delenv("PERSONAL_TOKENS_ENABLED", raising=False)
    for key, value in {"APP_ENV": "development", "PUBLIC_BASE_URL": ORIGIN, "OIDC_CLIENT_ID": "fixture-client",
                       "OIDC_CLIENT_SECRET": "fixture-secret", "SESSION_SECRET": "s" * 40, "JOB_POLL_SECONDS": "0.01",
                       "DATABASE_PATH": str(tmp_path / "env.sqlite3"), "DOCUMENTS_DIR": str(tmp_path / "env-docs"),
                       "VECTORS_DIR": str(tmp_path / "env-vectors")}.items():
        monkeypatch.setenv(key, value)
    from api import create_app
    from src.reliability.budget import BudgetPolicy
    app = create_app(parser=lambda raw: [], embedder=lambda texts: [[0.1] * 8 for _ in texts],
                     answer_generator=lambda question, hits: "", budget_policy=BudgetPolicy.for_tests())
    assert app.state.settings.personal_tokens_enabled is False
    with TestClient(app):
        cookie = app.state.auth.complete_identity({"iss": "https://fixture.example.test", "sub": "env-a",
            "email": "env-a@example.test", "email_verified": True, "name": "env-a"})
        member = TestClient(app)
        member.cookies.set(app.state.settings.cookie_name, cookie)
        member.headers.update({"Origin": ORIGIN, "X-CSRF-Token": member.get("/api/v2/session").json()["csrf_token"]})
        off = member.post("/api/v2/personal-tokens", json={"name": "x"})
        assert off.status_code == 404 and off.json()["error"]["code"] == "FEATURE_DISABLED"
        user_id = member.get("/api/v2/session").json()["user"]["id"]
        # Even a token row that exists (made while the flag was on) is refused.
        raw = app.state.auth.tokens.create(SimpleNamespace(id=user_id, auth_kind="cookie"), name="old", is_enabled=True)["token"]
        bearer = TestClient(app)
        response = bearer.get("/api/v2/trips", headers={"Authorization": "Bearer " + raw})
        assert response.status_code == 401 and response.json()["error"]["code"] == "AUTH_REQUIRED"
        member.close()
        bearer.close()


# ---------------------------------------------------------------- storage contract

def test_raw_token_is_shown_once_and_only_its_hash_is_stored(service):
    a = service.login("token-a")
    created = _token(a, expires_in_days=7, scopes=["read"])
    raw = created["token"]
    assert raw.startswith(tokens_module.TOKEN_PREFIX) and created["token_shown_once"] is True
    assert created["scopes"] == ["read"]
    expires = datetime.fromisoformat(created["expires_at"]) - datetime.fromisoformat(created["created_at"])
    assert expires == timedelta(days=7)
    rows = _db_rows(service, "SELECT * FROM personal_access_tokens")
    assert len(rows) == 1 and rows[0]["token_hash"] == tokens_module.digest(raw)
    assert all(raw not in str(value) for value in rows[0].values())
    assert all(raw[len(tokens_module.TOKEN_PREFIX):][:8] not in str(value) for value in rows[0].values())
    listing = a.client.get("/api/v2/personal-tokens").json()
    assert raw not in str(listing) and "token" not in listing["items"][0] and "token_hash" not in listing["items"][0]
    # The internal session cannot be presented as a cookie (its hash belongs to a discarded value).
    session = _db_rows(service, "SELECT * FROM sessions WHERE id=?", (rows[0]["session_id"],))[0]
    assert session["token_hash"] != tokens_module.digest(raw)
    cookie_client = TestClient(service.app)
    cookie_client.cookies.set(service.settings.cookie_name, raw)
    assert cookie_client.get("/api/v2/trips").status_code == 401
    assert cookie_client.get("/api/v2/session").json()["authenticated"] is False


def test_expiry_is_capped_at_thirty_days_and_inputs_are_validated(service):
    a = service.login("token-a")
    default = _token(a)
    assert datetime.fromisoformat(default["expires_at"]) - datetime.fromisoformat(default["created_at"]) == timedelta(days=30)
    for body in ({"name": "x", "expires_in_days": 31}, {"name": "x", "expires_in_days": 0}, {"name": ""},
                 {"name": "x", "scopes": ["write"]}, {"name": "x", "scopes": []}, {"name": "x", "extra": 1}):
        assert a.client.post("/api/v2/personal-tokens", json=body).status_code == 422, body
    with pytest.raises(Exception):
        service.app.state.auth.tokens.create(SimpleNamespace(id=a.user["id"], auth_kind="cookie"), name="x", days=31, is_enabled=True)


def test_active_token_count_is_limited(service, monkeypatch):
    monkeypatch.setattr(tokens_module, "MAX_ACTIVE_PER_USER", 2)
    a = service.login("token-a")
    first = _token(a)
    _token(a)
    response = a.client.post("/api/v2/personal-tokens", json={"name": "third"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "TOKEN_LIMIT"
    a.client.delete(f"/api/v2/personal-tokens/{first['id']}")
    _token(a, name="after revoke")
    # Tokens stopped by a session revocation (role change bumps the epoch) are
    # unusable, show as inactive and no longer hold a slot.
    from src.foundation.cli import set_user_role
    set_user_role(service.app.state.db, a.user["id"], "admin")
    a = service.login("token-a")
    states = [item["state"] for item in a.client.get("/api/v2/personal-tokens").json()["items"]]
    assert states.count("inactive") == 2 and states.count("revoked") == 1
    _token(a, name="after role change")
    _token(a, name="second after role change")
    assert a.client.post("/api/v2/personal-tokens", json={"name": "third"}).status_code == 409


# ---------------------------------------------------------------- allowlist

def test_allowlisted_reads_and_ask_work_with_bearer_only(service, monkeypatch):
    a = service.login("token-a")
    trip = _trip(a.client)
    raw = _token(a)["token"]
    client = service.bearer(raw)
    trips = client.get("/api/v2/trips")
    assert trips.status_code == 200 and [t["id"] for t in trips.json()["items"]] == [trip["id"]]
    assert client.get(f"/api/v2/trips/{trip['id']}").json()["id"] == trip["id"]
    assert client.get(f"/api/v2/trips/{trip['id']}/bookings").status_code == 200
    assert client.get(f"/api/v2/trips/{trip['id']}/bookmarks").json() == {"items": []}
    # jobs._user (sessions JOIN) still works for the token's internal session.
    assert client.get(f"/api/v2/trips/{trip['id']}/itineraries").json()["items"] == []
    missing = client.get(f"/api/v2/trips/{trip['id']}/itineraries/missing")
    assert missing.status_code == 404
    place = client.get(f"/api/v2/trips/{trip['id']}/places/unknown-place/detail")
    assert place.status_code == 404 and place.json()["error"]["code"] == "NOT_FOUND"
    ask = client.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "11월 7일 예약 알려줘"})
    assert ask.status_code == 200, ask.text
    assert "answer" in ask.json()
    used = _db_rows(service, "SELECT last_used_at FROM personal_access_tokens")[0]["last_used_at"]
    assert used is not None


WRITE_ATTEMPTS = [
    ("post", "/api/v2/trips", {"json": {"title": "x", "start_date": "2026-11-06", "end_date": "2026-11-09", "party": {"adults": 1, "children": []}}}),
    ("patch", "/api/v2/trips/{trip}", {"json": {"title": "변경", "expected_version": 1}}),
    ("delete", "/api/v2/trips/{trip}", {}),
    ("post", "/api/v2/trips/{trip}/bookings", {"json": {}}),
    ("put", "/api/v2/trips/{trip}/workspace-draft", {"json": {}}),
    ("delete", "/api/v2/trips/{trip}/workspace-draft", {}),
    ("post", "/api/v2/trips/{trip}/bookmarks", {"json": {"input_kind": "place", "input_value": "p", "note": ""}}),
    ("post", "/api/v2/trips/{trip}/documents", {"files": [("files", ("a.txt", b"Synthetic", "text/plain"))]}),
    ("post", "/api/v2/trips/{trip}/itineraries", {"json": {}}),
    ("post", "/api/v2/trips/{trip}/itineraries/x/edit-previews", {"json": {}}),
    ("patch", "/api/v2/trips/{trip}/itineraries/x", {"json": {}}),
    ("post", "/api/v2/trips/{trip}/itineraries/x/undo-previews", {"json": {}}),
    ("post", "/api/v2/trips/{trip}/itineraries/x/alternatives", {"json": {}}),
    ("post", "/api/v2/trips/{trip}/discovery-intents", {"json": {}}),
    ("patch", "/api/v2/trips/{trip}/discovery-conditions", {"json": {}}),
    ("post", "/api/v2/trips/{trip}/ask/stream", {"json": {"question": "예약?"}}),
    ("post", "/api/v2/personal-tokens", {"json": {"name": "chain"}}),
    ("delete", "/api/v2/personal-tokens/{token}", {}),
    ("post", "/api/v2/auth/logout", {}),
    ("post", "/api/v2/jobs/j/cancel", {}),
]

FORBIDDEN_READS = [
    "/api/v2/trips/{trip}/documents",
    "/api/v2/trips/{trip}/documents/d/content",       # raw mail download
    "/api/v2/jobs/j/events",                           # SSE
    "/api/v2/jobs/j",
    "/api/v2/usage",
    "/api/v2/readiness",
    "/api/v2/mail-capabilities",
    "/api/v2/personal-tokens",
    "/api/v2/trips/{trip}/workspace-draft",
    "/api/v2/trips/{trip}/discovery-conditions",
    "/api/v2/trips/{trip}/discovery-catalog",
    "/api/v2/trips/{trip}/reservation-tasks",
    "/api/v2/admin/discovery-packs",
    "/api/v2/admin/review-controls",
    "/api/v2/trips/{trip}/today",                      # revalidates reservation tasks (writes)
]


def test_token_cannot_write_even_with_forged_origin_and_empty_csrf(service):
    a = service.login("token-a")
    trip = _trip(a.client)
    created = _token(a)
    client = service.bearer(created["token"])
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": ""})
    for method, path, kwargs in WRITE_ATTEMPTS:
        url = path.format(trip=trip["id"], token=created["id"])
        response = getattr(client, method)(url, **kwargs)
        # require_actor (allowlist) runs before body validation, so never 422.
        assert response.status_code == 403, (method, url, response.status_code, response.text)
        assert response.json()["error"]["code"] == "TOKEN_SCOPE_REJECTED", (method, url, response.text)
    # Nothing changed.
    assert [t["id"] for t in a.client.get("/api/v2/trips").json()["items"]] == [trip["id"]]
    assert a.client.get(f"/api/v2/trips/{trip['id']}").json()["version"] == trip["version"]
    assert len(_db_rows(service, "SELECT id FROM personal_access_tokens")) == 1
    assert _db_rows(service, "SELECT revoked_at FROM personal_access_tokens")[0]["revoked_at"] is None
    assert _db_rows(service, "SELECT count(*) AS n FROM jobs")[0]["n"] == 0


def test_non_allowlisted_reads_are_rejected(service):
    a = service.login("token-a")
    trip = _trip(a.client)
    client = service.bearer(_token(a)["token"])
    for path in FORBIDDEN_READS:
        response = client.get(path.format(trip=trip["id"]))
        assert response.status_code == 403, (path, response.status_code, response.text)
        assert response.json()["error"]["code"] == "TOKEN_SCOPE_REJECTED"
    # The session endpoint never hands a CSRF token to a Bearer caller.
    session = client.get("/api/v2/session").json()
    assert session["authenticated"] is False and session["csrf_token"] is None


def test_bearer_takes_precedence_over_a_valid_cookie_on_writes(service):
    a = service.login("token-a")
    raw = _token(a)["token"]
    a.client.headers.update({"Authorization": "Bearer " + raw})
    response = a.client.post("/api/v2/trips", json={"title": "x", "start_date": "2026-11-06", "end_date": "2026-11-09",
                                                     "party": {"adults": 1, "children": []}})
    assert response.status_code == 403 and response.json()["error"]["code"] == "TOKEN_SCOPE_REJECTED"
    # A malformed Bearer never falls back to the cookie either.
    a.client.headers.update({"Authorization": "Bearer not-a-token"})
    assert a.client.get("/api/v2/trips").status_code == 401
    del a.client.headers["Authorization"]
    assert a.client.get("/api/v2/trips").status_code == 200


def test_allowlist_routes_exist_and_are_reads_or_ask(service):
    paths = service.app.openapi()["paths"]
    routes = {(method.upper(), path) for path, operations in paths.items() for method in operations}
    for (method, path), scope in tokens_module.ALLOWED_ROUTES.items():
        assert (method, path) in routes, (method, path)
        assert (method == "GET" and scope == "read") or (method == "POST" and path.endswith("/ask") and scope == "ask")
        for banned in ("admin", "content", "events", "session", "stream", "personal-tokens", "documents", "jobs"):
            assert banned not in path


def test_scope_read_only_token_cannot_ask(service):
    a = service.login("token-a")
    trip = _trip(a.client)
    client = service.bearer(_token(a, scopes=["read"])["token"])
    assert client.get("/api/v2/trips").status_code == 200
    response = client.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "예약?"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "TOKEN_SCOPE_REJECTED"


# personal_access_tokens: last_used_at. dispatcher_leases: the background job
# dispatcher's own heartbeat, which ticks with or without requests.
def _table_snapshot(service, skip=("personal_access_tokens", "dispatcher_leases")):
    with service.app.state.db.connect() as con:
        names = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: sorted(map(tuple, con.execute(f'SELECT * FROM "{name}"').fetchall()), key=repr)
                for name in names if name not in skip}


def test_allowlisted_gets_leave_every_other_table_unchanged(service):
    """Bearer GETs write nothing but the token's own last_used_at."""
    from tests.test_foundation_api import _upload
    a = service.login("token-a")
    trip = _trip(a.client)
    _upload(a.client, trip["id"])
    bookings = a.client.get(f"/api/v2/trips/{trip['id']}/bookings").json()["items"]
    assert bookings, "fixture upload produced no booking"
    client = service.bearer(_token(a)["token"])
    before = _table_snapshot(service)
    base = f"/api/v2/trips/{trip['id']}"
    for path in ("/api/v2/trips", base, base + "/bookings", base + "/bookmarks", base + "/itineraries",
                 base + "/itineraries/missing", base + "/places/missing/detail"):
        response = client.get(path)
        assert response.status_code in {200, 404}, (path, response.status_code, response.text)
    assert _table_snapshot(service) == before
    assert len([(m, p) for (m, p) in tokens_module.ALLOWED_ROUTES if m == "GET"]) == 7


def test_api_responses_vary_on_authorization(service):
    a = service.login("token-a")
    client = service.bearer(_token(a)["token"])
    response = client.get("/api/v2/trips")
    assert response.status_code == 200
    assert {part.strip() for part in response.headers["vary"].split(",")} >= {"Cookie", "Authorization"}
    assert "no-store" in response.headers["cache-control"]


# ---------------------------------------------------------------- CSRF hole

def test_empty_csrf_value_never_matches(service):
    a = service.login("token-a")
    with service.app.state.db.connect() as con:
        con.execute("UPDATE sessions SET csrf_token=''")
    client = TestClient(service.app)
    client.cookies.set(service.settings.cookie_name, a.cookie)
    for headers in ({"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": ""}):
        response = client.post("/api/v2/trips", headers=headers, json={"title": "x", "start_date": "2026-11-06",
            "end_date": "2026-11-09", "party": {"adults": 1, "children": []}})
        assert response.status_code == 403 and response.json()["error"]["code"] == "CSRF_REJECTED"
    from src.foundation.auth import Actor
    request = SimpleNamespace(headers={"origin": ORIGIN})
    actor = Actor("u", "e", "n", "member", "s", "", "2099")
    with pytest.raises(Exception) as caught:
        service.app.state.auth.write_guard(request, actor)
    assert caught.value.code == "CSRF_REJECTED"
    token_actor = Actor("u", "e", "n", "member", "s", "abc", "2099", auth_kind="token", token_id="t", scopes=("read", "ask"))
    with pytest.raises(Exception) as caught:
        service.app.state.auth.write_guard(SimpleNamespace(headers={"origin": ORIGIN, "x-csrf-token": "abc"}), token_actor)
    assert caught.value.code == "TOKEN_SCOPE_REJECTED"


def test_cookie_flows_unchanged(service):
    a = service.login("token-a")
    trip = _trip(a.client)
    assert a.client.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "11월 7일 예약 알려줘"}).status_code == 200
    bare = TestClient(service.app)
    bare.cookies.set(service.settings.cookie_name, a.cookie)
    assert bare.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "예약?"}).status_code == 403


# ---------------------------------------------------------------- ownership and revocation

def test_other_users_trip_is_404_through_a_token(service):
    a, b = service.login("token-a"), service.login("token-b")
    trip = _trip(a.client)
    client = service.bearer(_token(b)["token"])
    assert client.get("/api/v2/trips").json()["items"] == []
    base = f"/api/v2/trips/{trip['id']}"
    for path in (base, base + "/bookings", base + "/bookmarks", base + "/itineraries", base + "/itineraries/any",
                 base + "/places/any/detail"):
        response = client.get(path)
        assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND", (path, response.text)
    assert client.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "예약?"}).status_code == 404
    # B cannot revoke or see A's token.
    a_token = _token(a)
    assert b.client.delete(f"/api/v2/personal-tokens/{a_token['id']}").status_code == 404
    assert a_token["id"] not in str(b.client.get("/api/v2/personal-tokens").json())


def test_revoke_expiry_deactivation_and_role_change_reject_immediately(service):
    a = service.login("token-a")
    first, second, third, fourth = (_token(a, name=f"t{i}") for i in range(4))
    for item in (first, second, third, fourth):
        assert service.bearer(item["token"]).get("/api/v2/trips").status_code == 200
    # Revoke
    assert a.client.delete(f"/api/v2/personal-tokens/{first['id']}").json()["state"] == "revoked"
    assert service.bearer(first["token"]).get("/api/v2/trips").status_code == 401
    assert a.client.delete(f"/api/v2/personal-tokens/{first['id']}").status_code == 200  # idempotent
    # Expiry
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with service.app.state.db.connect() as con:
        con.execute("UPDATE personal_access_tokens SET expires_at=? WHERE id=?", (past, second["id"]))
    assert service.bearer(second["token"]).get("/api/v2/trips").status_code == 401
    states = {item["id"]: item["state"] for item in a.client.get("/api/v2/personal-tokens").json()["items"]}
    assert states[first["id"]] == "revoked" and states[second["id"]] == "expired" and states[third["id"]] == "active"
    # Role change (offline operator) bumps the epoch: tokens die with sessions.
    from src.foundation.cli import set_user_role
    set_user_role(service.app.state.db, a.user["id"], "admin")
    assert service.bearer(third["token"]).get("/api/v2/trips").status_code == 401
    # Deactivation
    b = service.login("token-b")
    b_token = _token(b)
    assert service.bearer(b_token["token"]).get("/api/v2/trips").status_code == 200
    service.app.state.auth.disable_user(b.user["id"])
    assert service.bearer(b_token["token"]).get("/api/v2/trips").status_code == 401


def test_ask_revalidates_the_token_after_answering(service, monkeypatch):
    a = service.login("token-a")
    trip = _trip(a.client)
    created = _token(a)
    import src.foundation.routes as routes

    def revoking_answer(*args, **kwargs):
        service.app.state.auth.tokens.revoke(SimpleNamespace(id=a.user["id"], auth_kind="cookie"), created["id"])
        return {"answer": "합성 답변", "sources": []}

    monkeypatch.setattr(routes, "answer", revoking_answer)
    response = service.bearer(created["token"]).post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "예약?"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_cookie_ask_still_revalidates_the_cookie_session(service, monkeypatch):
    a = service.login("token-a")
    trip = _trip(a.client)
    import src.foundation.routes as routes

    def logout_mid_answer(*args, **kwargs):
        with service.app.state.db.connect() as con:
            con.execute("DELETE FROM sessions WHERE user_id=?", (a.user["id"],))
        return {"answer": "합성 답변", "sources": []}

    monkeypatch.setattr(routes, "answer", logout_mid_answer)
    assert a.client.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "예약?"}).status_code == 401


# ---------------------------------------------------------------- rate limit

def test_per_token_rate_limit(service, monkeypatch):
    monkeypatch.setattr(tokens_module, "RATE_PER_MINUTE", 3)
    monkeypatch.setattr(tokens_module, "ASK_PER_MINUTE", 1)
    a = service.login("token-a")
    trip = _trip(a.client)
    first, second = _token(a, name="one"), _token(a, name="two")
    client = service.bearer(first["token"])
    assert [client.get("/api/v2/trips").status_code for _ in range(3)] == [200, 200, 200]
    limited = client.get("/api/v2/trips")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "TOKEN_RATE_LIMITED"
    assert limited.json()["error"]["details"]["limit_per_minute"] == 3
    # Limits are per token; cookie traffic is not limited by this.
    other = service.bearer(second["token"])
    assert other.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "11월 7일 예약"}).status_code == 200
    ask_limited = other.post(f"/api/v2/trips/{trip['id']}/ask", json={"question": "11월 7일 예약"})
    assert ask_limited.status_code == 429
    assert all(a.client.get("/api/v2/trips").status_code == 200 for _ in range(5))


def test_rate_limiter_window_slides():
    now = [0.0]
    limiter = tokens_module.RateLimiter(clock=lambda: now[0])
    limiter.hit("k", 2)
    limiter.hit("k", 2)
    with pytest.raises(Exception) as caught:
        limiter.hit("k", 2)
    assert caught.value.status == 429
    now[0] = 60.5
    limiter.hit("k", 2)
