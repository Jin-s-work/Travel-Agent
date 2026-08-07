"""API 계층 테스트.

/api/ask는 LLM을 호출하므로 여기서는 다루지 않는다. 나머지 엔드포인트가
올바른 모양의 응답을 주는지, 잘못된 입력을 막는지를 확인한다.
"""

import pytest
from fastapi.testclient import TestClient

from api import _policy_lines, _to_booking, app

client = TestClient(app)


def test_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_bookings_shape():
    res = client.get("/api/bookings")
    assert res.status_code == 200

    body = res.json()
    assert {"items", "count", "trip_start", "trip_end"} <= body.keys()
    assert body["count"] == len(body["items"])

    for item in body["items"]:
        # UI가 기대하는 키가 빠지면 화면이 조용히 비어버린다.
        assert {"id", "kind", "provider", "date", "time", "source_file", "policy"} <= item.keys()
        assert isinstance(item["policy"], list)


def test_bookings_sorted_by_date():
    items = client.get("/api/bookings").json()["items"]
    dates = [i["date"] for i in items if i["date"]]
    assert dates == sorted(dates), "일정은 날짜순이어야 한다"


def test_index_rejects_unsupported_extension():
    """지원하지 않는 형식만 올리면 인덱싱을 시작하지 않는다."""
    res = client.post(
        "/api/index",
        files={"files": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert res.status_code == 202
    body = res.json()
    assert body["uploaded"] == []
    assert "notes.pdf" in body["rejected"]
    # 저장할 파일이 없으므로 백그라운드 작업을 걸지 않는다.
    assert body["state"] == "done"


def test_index_rejects_oversized_file():
    """업로드 본문을 통째로 메모리에 읽는다. 512MB 인스턴스에서는 상한이 필요하다."""
    from src.config import MAX_UPLOAD_BYTES

    huge = b"x" * (MAX_UPLOAD_BYTES + 1024)
    res = client.post("/api/index", files={"files": ("huge.txt", huge, "text/plain")})
    assert res.status_code == 202
    body = res.json()
    assert body["uploaded"] == []
    assert "huge.txt" in body["rejected"]


def test_upload_limit_leaves_room_for_real_emails():
    """상한이 실제 예약 메일보다 훨씬 커야 한다. 데모 메일 중 가장 큰 것의 100배 이상."""
    from pathlib import Path

    from src.config import MAX_UPLOAD_BYTES

    largest = max(
        path.stat().st_size
        for directory in ("tests/sample_emails", "tests/demo_emails")
        for path in Path(directory).iterdir()
        if path.is_file()
    )
    assert MAX_UPLOAD_BYTES > largest * 100


def test_index_status_shape():
    res = client.get("/api/index/status")
    assert res.status_code == 200
    body = res.json()
    assert body["state"] in {"idle", "running", "done", "error"}
    # UI가 진행 표시에 쓰는 키가 빠지면 화면이 멈춘 것처럼 보인다.
    assert {"total", "done", "indexed", "already_indexed", "error"} <= body.keys()


def test_index_returns_202_not_200():
    """인덱싱은 즉시 끝나지 않는다. 202로 '접수됨'을 알려야
    프론트가 폴링으로 넘어간다."""
    from api import app as fastapi_app

    route = next(
        r for r in fastapi_app.routes
        if getattr(r, "path", "") == "/api/index" and "POST" in getattr(r, "methods", set())
    )
    assert route.status_code == 202


def test_clear_index_reports_removed_files():
    """비우기는 벡터뿐 아니라 업로드된 원본도 지운다.

    파일이 남으면 다음 인덱싱에서 되살아나 '비웠는데 다시 나타나는' 문제가 된다.
    """
    res = client.request("DELETE", "/api/index", params={"keep_files": True})
    assert res.status_code == 200
    body = res.json()
    assert "removed_files" in body
    assert body["removed_files"] == [], "keep_files=True면 파일을 지우지 않는다"


def test_clear_index_resets_job_state():
    client.request("DELETE", "/api/index", params={"keep_files": True})
    status = client.get("/api/index/status").json()
    assert status["state"] == "idle"
    assert status["indexed"] == []


# ---------------------------------------------------------------- 빠른 경로
@pytest.mark.parametrize(
    "question,fast",
    [
        ("체크아웃 시간 언제야?", True),
        ("투어 환불 규정 알려줘", True),
        ("귀국 항공편 예약번호는?", True),
        # 날짜 계산이 필요하면 에이전트로 넘어가야 한다.
        ("둘째 날 일정 뭐야?", False),
        ("3일차 투어 몇 시야?", False),
        # 일반 정보는 웹 검색이 필요하다.
        ("도쿄 지금 날씨 어때?", False),
        ("호텔 근처 맛집 추천해줘", False),
        # 예약과 무관한 잡담은 빠른 경로를 타지 않는다.
        ("안녕 반가워", False),
    ],
)
def test_fast_path_routing(question, fast):
    from api import _can_answer_directly

    assert _can_answer_directly(question) is fast


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 실제로 화면에 나왔던 답변이다.
        ("search_bookings 결과: 예약 내역에서 확인할 수 없습니다.",
         "예약 내역에서 확인할 수 없습니다."),
        ("[bookings_on_date] 2026-10-13 예약 2건", "2026-10-13 예약 2건"),
        ("web_search 결과: search_bookings 결과: 없습니다.", "없습니다."),
        ("resolve_trip_day: 둘째 날은 2026-10-13입니다.", "둘째 날은 2026-10-13입니다."),
        # 멀쩡한 답변은 건드리지 않는다.
        ("체크아웃은 11:00입니다.\n출처: Hotel (예약번호 1)",
         "체크아웃은 11:00입니다.\n출처: Hotel (예약번호 1)"),
        # 본문 중간의 콜론은 말머리가 아니다.
        ("시각: 15:00 ~ 11:00", "시각: 15:00 ~ 11:00"),
    ],
)
def test_strip_tool_mentions(raw, expected):
    """도구 이름이 사용자에게 보이면 안 된다. 프롬프트만으로는 막히지 않았다."""
    from src.agent import _strip_tool_mentions

    assert _strip_tool_mentions(raw) == expected


def test_ask_rejects_empty_question():
    res = client.post("/api/ask", json={"question": ""})
    assert res.status_code == 422


def test_ask_rejects_overlong_question():
    res = client.post("/api/ask", json={"question": "가" * 1001})
    assert res.status_code == 422


def test_web_index_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "Travel Inbox" in res.text


def test_service_worker_served_from_root():
    """서비스 워커는 루트에서 나와야 사이트 전체를 제어할 수 있다."""
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]


def test_manifest_served():
    res = client.get("/manifest.webmanifest")
    assert res.status_code == 200


# ---------------------------------------------------------------- 순수 함수
def test_policy_lines_extracts_refund_block():
    document = (
        "[숙소] Hotel Gracery Shinjuku 예약 확인.\n"
        "이용 날짜는 2026-10-12 ~ 2026-10-15.\n"
        "환불 및 취소 규정: - 10월 5일까지 무료 취소\n"
        "- 이후 첫 1박 부과\n"
        "메일 원문 발췌: 확인번호 4471938265"
    )
    lines = _policy_lines(document)
    assert lines == ["10월 5일까지 무료 취소", "이후 첫 1박 부과"]
    assert not any("원문 발췌" in line for line in lines)


def test_policy_lines_without_policy():
    assert _policy_lines("[항공] 대한항공 예약 확인.") == []
    assert _policy_lines("") == []


def test_to_booking_splits_time_range():
    record = {
        "type": "숙소", "provider": "H", "time": "15:00 ~ 11:00",
        "date": "2026-10-12", "date_end": "2026-10-15",
        "source_file": "03.txt", "confirmation_number": "44719",
        "document": "",
    }
    out = _to_booking(record)
    assert out["time"] == "15:00"
    assert out["time_end"] == "11:00"


def test_to_booking_handles_single_time():
    out = _to_booking({"time": "07:40", "source_file": "05.txt", "document": ""})
    assert out["time"] == "07:40"
    assert out["time_end"] is None
