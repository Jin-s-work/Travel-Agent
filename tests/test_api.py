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
    "question",
    [
        "그거 환불 되나?",
        "그 호텔 체크아웃 몇 시야?",
        "아까 말한 예약 취소하면 얼마야?",
        "거기 몇 시까지 가야 해?",
        "방금 그 투어 집합 장소 어디야?",
    ],
)
def test_referential_questions_go_to_agent(question):
    """앞 대화를 가리키면 검색어만으로 뜻이 서지 않는다. 맥락을 아는 쪽이 처리해야 한다."""
    from api import _can_answer_directly

    assert _can_answer_directly(question) is False










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


def test_trim_after_refusal_drops_appended_advice():
    """실제로 붙었던 답변이다. 거절 자리에 일반 지식을 얹으면 의미가 흐려진다."""
    from src.agent import _trim_after_refusal
    from src.config import NO_INFO_MESSAGE

    answer = (
        f"{NO_INFO_MESSAGE}\n\n여권번호는 예약서에 없으니 실제 여권을 확인하거나 "
        "항공사에 문의하세요."
    )
    assert _trim_after_refusal(answer, ["search_bookings"]) == NO_INFO_MESSAGE


def test_trim_after_refusal_keeps_web_search_results():
    """웹 검색을 썼다면 뒤 내용이 실제 검색 결과다."""
    from src.agent import _trim_after_refusal
    from src.config import NO_INFO_MESSAGE

    answer = f"{NO_INFO_MESSAGE}\n\n다만 도쿄 10월 평균 기온은 18도입니다."
    assert _trim_after_refusal(answer, ["search_bookings", "web_search"]) == answer


def test_trim_after_refusal_leaves_normal_answers():
    from src.agent import _trim_after_refusal

    answer = "체크아웃은 11:00입니다.\n출처: Hotel Gracery Shinjuku (예약번호 1)"
    assert _trim_after_refusal(answer, ["search_bookings"]) == answer






def test_web_index_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "<title>여정 · 나의 여행</title>" in res.text


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


@pytest.mark.parametrize("method,path",[("GET","/api/bookings"),("POST","/api/ask"),("POST","/api/ask/stream"),("POST","/api/index"),("GET","/api/index/status"),("DELETE","/api/index"),("POST","/api/trip-start"),("GET","/api/download/x")])
def test_retired_global_apis_require_authentication(method,path):
    response=client.request(method,path)
    assert response.status_code == 401
    assert response.json()['error']['code']=='AUTH_REQUIRED'


def test_no_shared_demo_seed_or_private_http_state():
    import api
    for name in ('_job','_agent','_run_seeding','store'):
        assert not hasattr(api,name)


@pytest.mark.parametrize('payload',[{'question':''},{'question':'가'*1001},{'question':'x','history':[{'role':'system','content':'x'}]},{'question':'x','history':[{'role':'tool','content':'x'}]},{'question':'x','history':[{'role':'user','content':'x'*4001}]}])
def test_strict_question_contract(payload):
    from api import AskRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AskRequest.model_validate(payload)
