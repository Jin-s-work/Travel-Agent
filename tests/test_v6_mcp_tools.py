"""v6 stage 3 (S3-08): the local MCP bridge's logic, without the ``mcp`` SDK.

tools/mcp_going/going_client.py and going_tools.py are imported in the app's own
environment and exercised against (1) a fake API on 127.0.0.1 and (2) the real
app through an in-process opener. No external network, no paid call. The
stdio entry point (server.py) needs the separate going-mcp environment and is
checked outside pytest.
"""
from __future__ import annotations

import io
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import error as urlerror

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools" / "mcp_going"
sys.path.insert(0, str(TOOLS_DIR))
import going_client  # noqa: E402
import going_tools  # noqa: E402

from tests.test_v6_personal_tokens import _make_service  # noqa: E402

FAKE_TOKEN = "going_pat_" + secrets.token_urlsafe(16)


class _Fake(BaseHTTPRequestHandler):
    seen: list = []
    replies: dict = {}

    def log_message(self, *args):
        pass

    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        type(self).seen.append({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization"),
                                "body": json.loads(body) if body else None})
        status, payload, extra = type(self).replies.get(self.path.split("?")[0], (200, {"items": [], "count": 0}, {}))
        data = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    do_GET = _reply
    do_POST = _reply


@pytest.fixture
def fake_api():
    _Fake.seen, _Fake.replies = [], {}
    server = HTTPServer(("127.0.0.1", 0), _Fake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------- static contract

def test_logic_modules_do_not_import_the_mcp_sdk():
    for name in ("going_client.py", "going_tools.py"):
        source = (TOOLS_DIR / name).read_text()
        assert "import mcp" not in source and "from mcp" not in source
    assert "from mcp.server.mcpserver import MCPServer" in (TOOLS_DIR / "server.py").read_text()


def test_tools_are_read_only_and_mirror_the_api_allowlist():
    from src.foundation.tokens import ALLOWED_ROUTES
    names = set(going_tools.TOOLS)
    assert names == {"list_trips", "get_trip", "list_bookings", "list_saved_places", "get_place",
                     "list_itineraries", "get_itinerary", "ask_trip"}
    for banned in ("apply", "undo", "delete", "remove", "send", "upload", "revoke", "token", "fetch", "url", "write", "patch"):
        assert not any(banned in name for name in names), banned
    methods = {name: spec[2] for name, spec in going_tools.TOOLS.items()}
    assert {name for name, method in methods.items() if method != "GET"} == {"ask_trip"}
    assert methods["ask_trip"] == "POST" and ("POST", "/api/v2/trips/{trip_id}/ask") in ALLOWED_ROUTES
    for spec in going_tools.tool_specs():
        schema = spec["input_schema"]
        assert schema["additionalProperties"] is False
        # ask_trip spends the answer budget: not advertised as a read.
        assert spec["read_only"] is (spec["name"] != "ask_trip")
        assert not {"user_id", "owner_id"} & set(schema["properties"])
    # Every GET tool maps to an allowlisted GET template; /today is not one (it writes).
    allowed_get = {path for method, path in ALLOWED_ROUTES if method == "GET"}
    assert not any(path.endswith("/today") for path in allowed_get)


def test_base_url_is_loopback_only_and_token_is_hidden():
    for bad in ("https://travel-inbox-rag.onrender.com", "http://example.test:8000", "http://127.0.0.1:8000/api",
                "http://user:pw@127.0.0.1:8000", "ftp://127.0.0.1", ""):
        with pytest.raises(ValueError):
            going_client.GoingClient(bad, FAKE_TOKEN)
    client = going_client.GoingClient("http://127.0.0.1:8000/", FAKE_TOKEN)
    assert client.base_url == "http://127.0.0.1:8000" and FAKE_TOKEN not in repr(client)
    with pytest.raises(ValueError):
        going_client.GoingClient("http://localhost:8000", "")


# ---------------------------------------------------------------- fake API on loopback

def test_tools_call_the_expected_endpoints_with_bearer(fake_api):
    _, base = fake_api
    client = going_client.GoingClient(base, FAKE_TOKEN, timeout=5)
    _Fake.replies["/api/v2/trips/t%2F1/ask"] = (200, {"answer": "합성 답변", "sources": [{"booking_id": "b"}],
        "previews": [{"preview_id": "p1", "kind": "edit", "summary": "이동", "can_apply": True}], "secret_extra": "x"}, {})
    calls = [
        ("list_trips", {}), ("get_trip", {"trip_id": "t/1"}),
        ("list_bookings", {"trip_id": "t/1", "date_from": "2026-11-07", "date_to": None}),
        ("list_saved_places", {"trip_id": "t/1"}), ("get_place", {"trip_id": "t/1", "place_id": "p 1"}),
        ("list_itineraries", {"trip_id": "t/1"}), ("get_itinerary", {"trip_id": "t/1", "itinerary_id": "i1"}),
        ("ask_trip", {"trip_id": "t/1", "question": "내일 예약?"}),
    ]
    results = {name: going_tools.call_tool(client, name, args) for name, args in calls}
    assert all(result["ok"] for result in results.values()), results
    paths = [(s["method"], s["path"]) for s in _Fake.seen]
    assert paths == [
        ("GET", "/api/v2/trips?limit=100"), ("GET", "/api/v2/trips/t%2F1"),
        ("GET", "/api/v2/trips/t%2F1/bookings?date_from=2026-11-07&limit=100"),
        ("GET", "/api/v2/trips/t%2F1/bookmarks"), ("GET", "/api/v2/trips/t%2F1/places/p%201/detail"),
        ("GET", "/api/v2/trips/t%2F1/itineraries?limit=20"), ("GET", "/api/v2/trips/t%2F1/itineraries/i1"),
        ("POST", "/api/v2/trips/t%2F1/ask"),
    ]
    assert all(s["auth"] == "Bearer " + FAKE_TOKEN for s in _Fake.seen)
    assert _Fake.seen[-1]["body"] == {"question": "내일 예약?", "history": []}
    ask = results["ask_trip"]["data"]
    assert "secret_extra" not in ask and ask["previews"][0]["apply_requires_user_click"] is True and "note" in ask


def test_errors_come_back_as_results_not_exceptions(fake_api):
    _, base = fake_api
    client = going_client.GoingClient(base, FAKE_TOKEN, timeout=5)
    _Fake.replies["/api/v2/trips/x"] = (403, {"error": {"code": "TOKEN_SCOPE_REJECTED", "message": "읽기만"}}, {})
    _Fake.replies["/api/v2/trips/r"] = (302, {}, {"Location": "http://127.0.0.1:1/steal"})
    _Fake.replies["/api/v2/trips/big"] = (200, b"x" * (going_client.MAX_RESPONSE_BYTES + 10), {})
    _Fake.replies["/api/v2/trips/bad"] = (200, b"not json", {})
    assert going_tools.call_tool(client, "get_trip", {"trip_id": "x"}) == {"ok": False, "error_code": "TOKEN_SCOPE_REJECTED", "status": 403, "message": "읽기만"}
    redirected = going_tools.call_tool(client, "get_trip", {"trip_id": "r"})
    assert redirected["ok"] is False and redirected["status"] == 302
    assert len(_Fake.seen) == 2  # the redirect target was never requested
    assert going_tools.call_tool(client, "get_trip", {"trip_id": "big"})["error_code"] == "RESPONSE_TOO_LARGE"
    assert going_tools.call_tool(client, "get_trip", {"trip_id": "bad"})["error_code"] == "INVALID_RESPONSE"
    assert going_tools.call_tool(client, "apply_preview", {})["error_code"] == "UNKNOWN_TOOL"
    for name, args in (("get_trip", {}), ("get_trip", {"trip_id": "x", "user_id": "u"}), ("list_bookings", {"trip_id": "x", "date_from": "11/07"}),
                       ("ask_trip", {"trip_id": "x", "question": "q" * 1001}), ("get_trip", {"trip_id": 5}), ("get_trip", {"trip_id": ".."}),
                       ("list_trips", None), ("list_trips", "nope")):
        result = going_tools.call_tool(client, name, args)
        if args is None:
            assert result["ok"] is True
        else:
            assert result["ok"] is False and result["error_code"] == "INVALID_ARGUMENT", (name, args, result)
    offline = going_client.GoingClient("http://127.0.0.1:9", FAKE_TOKEN, timeout=1)
    assert going_tools.call_tool(offline, "list_trips")["error_code"] == "NETWORK_ERROR"


def test_long_free_text_is_shortened_and_output_is_capped(fake_api):
    _, base = fake_api
    client = going_client.GoingClient(base, FAKE_TOKEN, timeout=5)
    _Fake.replies["/api/v2/trips/t/bookings"] = (200, {"items": [{"id": "b", "raw_snippet": "메일" * 2000, "refund_policy": "r" * 50}], "count": 1}, {})
    data = going_tools.call_tool(client, "list_bookings", {"trip_id": "t"})["data"]
    assert len(data["items"][0]["raw_snippet"]) == going_tools.MAX_TEXT_FIELD + 1 and data["items"][0]["refund_policy"] == "r" * 50
    _Fake.replies["/api/v2/trips/t/bookmarks"] = (200, {"items": [{"id": str(i), "title": "x" * 300} for i in range(200)]}, {})
    capped = going_tools.call_tool(client, "list_saved_places", {"trip_id": "t"})["data"]
    assert capped["truncated"] is True and len(capped["partial_json"]) == going_tools.MAX_OUTPUT_CHARS


def test_get_itinerary_never_sends_lodging_or_origin_locations(fake_api):
    """privacy-1: snapshot/legs carry the stay and origin coordinates and labels; MCP gets the trip_agent whitelist only."""
    _, base = fake_api
    client = going_client.GoingClient(base, FAKE_TOKEN, timeout=5)
    origin = {"label": "A 도쿄 숙소 비공개 위치", "latitude": 35.6831, "longitude": 139.7598, "place_id": "stay-1",
              "coordinate_permitted": True}
    _Fake.replies["/api/v2/trips/t/itineraries/i1"] = (200, {
        "id": "i1", "itinerary_id": "i1", "version": 3, "validation_status": "provisional", "data_status": "current",
        "can_undo": True, "active_revision_id": "rev3",
        "snapshot": {"conditions": {"origin": origin}, "origin": origin,
                     "origin_contexts": {"2026-11-07": {"origin": origin}},
                     "origin_resolution_input": {"stays": [{"name": "A 도쿄 숙소 비공개 위치", "latitude": 35.6831,
                                                            "longitude": 139.7598}],
                                                 "overrides": {"origin": origin}}},
        "legs": [{"from_item_id": None, "to_item_id": "it1", "from_endpoint": origin,
                  "to_endpoint": {"latitude": 35.71, "longitude": 139.79}}],
        "items": [{"item_id": "it1", "place_id": "p1", "title": "센소지", "category": "sight", "local_start": "2026-11-07T10:00",
                   "local_end": "2026-11-07T11:00", "locked": False, "latitude": 35.7148, "longitude": 139.7967,
                   "origin": origin}],
        "conflicts": [{"code": "OPENING_HOURS", "detail": {"origin": origin}}, "not-a-dict"],
        "revisions": [{"id": "rev3"}],
    }, {})
    result = going_tools.call_tool(client, "get_itinerary", {"trip_id": "t", "itinerary_id": "i1"})
    assert result["ok"] is True
    data = result["data"]
    assert set(data) == {"itinerary_id", "version", "validation_status", "data_status", "items", "conflicts", "can_undo"}
    assert data["itinerary_id"] == "i1" and data["version"] == 3 and data["conflicts"] == ["OPENING_HOURS"]
    assert data["items"][0]["title"] == "센소지" and set(data["items"][0]) == set(going_tools.ITINERARY_ITEM_KEYS)
    dumped = json.dumps(result, ensure_ascii=False)
    for secret in ("35.6831", "139.7598", "A 도쿄 숙소 비공개 위치", "stay-1", "latitude", "longitude",
                   "snapshot", "origin", "legs", "revisions"):
        assert secret not in dumped, secret


def test_proxy_environment_is_ignored(fake_api, monkeypatch):
    """With HTTP(S)_PROXY set, the token must still go straight to loopback."""
    proxy_seen = []

    class _Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            proxy_seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    proxy = HTTPServer(("127.0.0.1", 0), _Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.setenv(key, proxy_url)
        for key in ("NO_PROXY", "no_proxy"):
            monkeypatch.delenv(key, raising=False)
        _, base = fake_api
        client = going_client.GoingClient(base, FAKE_TOKEN, timeout=5)
        assert going_tools.call_tool(client, "list_trips")["ok"] is True
        assert proxy_seen == []
        assert [(s["path"], s["auth"]) for s in _Fake.seen] == [("/api/v2/trips?limit=100", "Bearer " + FAKE_TOKEN)]
        # A closed loopback port fails as NETWORK_ERROR instead of reaching the proxy.
        offline = going_client.GoingClient("http://127.0.0.1:9", FAKE_TOKEN, timeout=1)
        assert going_tools.call_tool(offline, "list_trips")["error_code"] == "NETWORK_ERROR"
        assert proxy_seen == []
    finally:
        proxy.shutdown()
        proxy.server_close()


# ---------------------------------------------------------------- against the real app

class _Response:
    def __init__(self, status, body):
        self.status, self._body = status, io.BytesIO(body)

    def read(self, n=-1):
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _app_opener(test_client):
    def open_(req, timeout=None):
        url = req.full_url.split("://", 1)[1].split("/", 1)[1]
        response = test_client.request(req.get_method(), "/" + url, content=req.data, headers=dict(req.header_items()))
        if response.status_code >= 400:
            raise urlerror.HTTPError(req.full_url, response.status_code, "error", response.headers, io.BytesIO(response.content))
        return _Response(response.status_code, response.content)
    return open_


def test_tools_against_the_real_app_with_a_personal_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    app, settings = _make_service(tmp_path, monkeypatch)
    with TestClient(app) as lifetime:
        cookie = app.state.auth.complete_identity({"iss": "https://fixture.example.test", "sub": "mcp-a",
            "email": "mcp-a@example.test", "email_verified": True, "name": "mcp-a"})
        member = TestClient(app)
        member.cookies.set(settings.cookie_name, cookie)
        csrf = member.get("/api/v2/session").json()["csrf_token"]
        member.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf})
        trip = member.post("/api/v2/trips", json={"title": "Tokyo", "start_date": "2026-11-06", "end_date": "2026-11-09",
                                                  "party": {"adults": 2, "children": []}}).json()
        raw = member.post("/api/v2/personal-tokens", json={"name": "mcp"}).json()["token"]
        bare = TestClient(app)  # no cookie: only the Bearer header from GoingClient
        client = going_client.GoingClient("http://127.0.0.1:8000", raw, opener=_app_opener(bare))
        assert going_tools.call_tool(client, "list_trips")["data"]["items"][0]["id"] == trip["id"]
        for name, args in (("get_trip", {}), ("list_bookings", {}), ("list_saved_places", {}), ("list_itineraries", {}),
                           ("ask_trip", {"question": "11월 7일 예약 알려줘"})):
            result = going_tools.call_tool(client, name, {"trip_id": trip["id"], **args})
            assert result["ok"] is True, (name, result)
        missing = going_tools.call_tool(client, "get_place", {"trip_id": trip["id"], "place_id": "nope"})
        assert missing == {"ok": False, "error_code": "NOT_FOUND", "status": 404, "message": missing["message"]}
        # A real trip owned by another user is NOT_FOUND through every trip tool.
        cookie_b = app.state.auth.complete_identity({"iss": "https://fixture.example.test", "sub": "mcp-b",
            "email": "mcp-b@example.test", "email_verified": True, "name": "mcp-b"})
        owner_b = TestClient(app)
        owner_b.cookies.set(settings.cookie_name, cookie_b)
        owner_b.headers.update({"Origin": "http://testserver", "X-CSRF-Token": owner_b.get("/api/v2/session").json()["csrf_token"]})
        trip_b = owner_b.post("/api/v2/trips", json={"title": "Osaka", "start_date": "2026-11-06", "end_date": "2026-11-09",
                                                     "party": {"adults": 1, "children": []}}).json()
        assert [t["id"] for t in going_tools.call_tool(client, "list_trips")["data"]["items"]] == [trip["id"]]
        for name, args in (("get_trip", {}), ("list_bookings", {}), ("list_saved_places", {}), ("list_itineraries", {}),
                           ("get_itinerary", {"itinerary_id": "x"}), ("get_place", {"place_id": "x"}),
                           ("ask_trip", {"question": "예약 알려줘"})):
            other = going_tools.call_tool(client, name, {"trip_id": trip_b["id"], **args})
            assert other["ok"] is False and other["status"] == 404 and other["error_code"] == "NOT_FOUND", (name, other)
        owner_b.close()
        # The raw HTTP layer refuses anything but GET/POST, and the app refuses Bearer writes.
        with pytest.raises(going_client.GoingAPIError):
            client._send("DELETE", f"/trips/{trip['id']}")
        with pytest.raises(going_client.GoingAPIError) as caught:
            client._send("POST", "/trips", body={"title": "x"})
        assert caught.value.status == 403 and caught.value.code == "TOKEN_SCOPE_REJECTED"
        settings.personal_tokens_enabled = False
        assert going_tools.call_tool(client, "list_trips")["status"] == 401
        bare.close()
        member.close()


def test_ask_waits_past_the_server_worst_case_and_reports_timeout_as_possibly_billed():
    """B6: /ask 는 서버 최악 시간(25 s deadline + 45 s 공급자)보다 오래 기다리고,
    응답 대기 타임아웃은 '앱 미실행'(NETWORK_ERROR)이 아니라 ASK_TIMEOUT 으로 알린다."""
    seen = []

    def slow(req, timeout=None):
        seen.append((req.get_method(), timeout))
        raise TimeoutError('timed out')

    client = going_client.GoingClient("http://127.0.0.1:8000", FAKE_TOKEN, timeout=5, opener=slow)
    assert client.ask_timeout >= 25 + 45
    with pytest.raises(going_client.GoingAPIError) as caught:
        client.ask("trip-1", "내일 일정?")
    assert caught.value.code == "ASK_TIMEOUT" and "비용" in caught.value.message
    with pytest.raises(going_client.GoingAPIError) as caught:
        client.get("/trips")
    assert caught.value.code == "TIMEOUT"
    assert seen == [("POST", client.ask_timeout), ("GET", 5)]

    def refused(req, timeout=None):
        raise urlerror.URLError(ConnectionRefusedError(61, "refused"))
    offline = going_client.GoingClient("http://127.0.0.1:8000", FAKE_TOKEN, opener=refused)
    with pytest.raises(going_client.GoingAPIError) as caught:
        offline.ask("trip-1", "내일 일정?")
    assert caught.value.code == "NETWORK_ERROR"
