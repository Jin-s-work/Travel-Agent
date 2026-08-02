"""파이프라인 순수 함수 단위 테스트.

LLM이나 임베딩 API를 호출하지 않으므로 빠르고 비용이 들지 않는다.
"""

import pytest

from src.config import MIN_SIMILARITY, RESERVATION_TYPES
from src.evaluate import _is_refusal
from src.indexer import _chunk, _split_date_range, _to_int, build_metadata, build_search_text
from src.parser import _coerce_time, _normalize, FIELDS
from src.rag import detect_reservation_type
from src.store import _apply_threshold, _clean_metadata


# --------------------------------------------------------------- parser
@pytest.mark.parametrize(
    "value,expected",
    [
        # 체크인 가능 시간대와 체크아웃이 섞여 있으면 처음과 마지막을 취한다.
        ("체크인 2026년 10월 12일 15:00 - 24:00; 체크아웃 11:00까지", "15:00 ~ 11:00"),
        ("from 15:00 (latest arrival 19:00)", "15:00 ~ 19:00"),
        ("9:20 ~ 11:45", "09:20 ~ 11:45"),  # 한 자리 시각은 0을 채운다
        ("07:40 집합", "07:40"),
        ("미정", None),
        ("", None),
        (None, None),
    ],
)
def test_coerce_time(value, expected):
    assert _coerce_time(value) == expected


def test_coerce_time_handles_korean_suffix():
    """'11:00까지'처럼 뒤에 한글이 붙으면 \\b가 경계로 잡히지 않는다."""
    assert _coerce_time("체크아웃 11:00까지") == "11:00"


def test_normalize_fills_all_fields_and_rejects_unknown_type():
    result = _normalize({"provider": "ANA", "type": "기차", "location": "   "})
    assert set(result) == set(FIELDS)
    assert result["provider"] == "ANA"
    assert result["type"] is None, "화이트리스트 밖 종류는 null이어야 한다"
    assert result["location"] is None, "공백뿐인 값은 null이어야 한다"


def test_normalize_truncates_long_snippet():
    result = _normalize({"raw_snippet": "가" * 500})
    assert len(result["raw_snippet"]) <= 301


# --------------------------------------------------------------- indexer
@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-10-12 ~ 2026-10-15", ("2026-10-12", "2026-10-15")),
        ("2026-10-14", ("2026-10-14", "2026-10-14")),
        ("미정", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_date_range(value, expected):
    assert _split_date_range(value) == expected


def test_to_int():
    assert _to_int("2026-10-12") == 20261012
    assert _to_int(None) is None


def test_chunk_keeps_short_email_whole():
    assert len(_chunk("가" * 100)) == 1


def test_chunk_splits_long_email_and_terminates():
    chunks = _chunk("가" * 5000)
    assert len(chunks) > 1
    assert all(chunks), "빈 청크가 생기면 안 된다"


def test_build_metadata_has_filter_keys():
    parsed = {
        "type": "숙소",
        "provider": "Hotel Gracery Shinjuku",
        "confirmation_number": "4471938265",
        "date": "2026-10-12 ~ 2026-10-15",
        "time": "15:00 ~ 11:00",
        "location": "신주쿠",
    }
    metadata = build_metadata(parsed, "03_shinjuku_hotel.txt", "abc123")

    assert metadata["date"] == "2026-10-12"
    assert metadata["date_end"] == "2026-10-15"
    # Chroma의 $gte는 문자열을 받지 않으므로 정수 키가 반드시 있어야 한다.
    assert metadata["date_start_int"] == 20261012
    assert metadata["date_end_int"] == 20261015
    # 출처 표시와 요약 카드에 쓰인다.
    assert metadata["confirmation_number"] == "4471938265"
    assert metadata["time"] == "15:00 ~ 11:00"


def test_build_search_text_labels_time_by_type():
    """숙소와 항공의 time은 의미가 달라 라벨이 달라야 검색에 걸린다."""
    stay = build_search_text({"type": "숙소", "provider": "H", "time": "15:00 ~ 11:00"})
    flight = build_search_text({"type": "항공", "provider": "A", "time": "09:20 ~ 11:45"})

    assert "체크인~체크아웃 시각" in stay
    assert "출발~도착 시각" in flight


# --------------------------------------------------------------- store
def _hit(source, similarity):
    return {"metadata": {"source_file": source}, "similarity": similarity}


def test_apply_threshold_drops_everything_below_absolute_floor():
    hits = [_hit("a", 0.10), _hit("b", 0.05)]
    assert _apply_threshold(hits, MIN_SIMILARITY) == []


def test_apply_threshold_keeps_close_second_place():
    """0.90으로 잘랐을 때 정답 2위가 탈락하던 문제 때문에 0.75로 완화했다."""
    hits = [_hit("a", 0.40), _hit("b", 0.32), _hit("c", 0.20)]
    kept = _apply_threshold(hits, MIN_SIMILARITY)
    assert [h["metadata"]["source_file"] for h in kept] == ["a", "b"]


def test_apply_threshold_disabled_returns_all():
    hits = [_hit("a", 0.40), _hit("b", 0.01)]
    assert _apply_threshold(hits, None) == hits


def test_clean_metadata_drops_none_and_empty():
    cleaned = _clean_metadata({"a": 1, "b": None, "c": "", "d": "ok"})
    assert cleaned == {"a": 1, "d": "ok"}


# --------------------------------------------------------------- rag
@pytest.mark.parametrize(
    "question,expected",
    [
        ("렌터카 예약은 몇 시에 픽업이야?", "렌터카"),
        ("신주쿠 호텔 체크아웃 몇 시야?", "숙소"),
        ("대한항공 기내식 메뉴가 뭐야?", "항공"),
        ("후지산 투어 예약번호 알려줘", "투어"),
        ("내 여권 번호 뭐야?", None),
        # 두 종류가 섞이면 잘못 좁힐 위험이 있어 필터를 걸지 않는다.
        ("투어 집합 장소에서 호텔까지 얼마나 걸려?", None),
    ],
)
def test_detect_reservation_type(question, expected):
    assert detect_reservation_type(question) == expected


def test_detected_types_are_valid_reservation_types():
    detected = detect_reservation_type("렌터카 픽업")
    assert detected in RESERVATION_TYPES


# --------------------------------------------------------------- evaluate
@pytest.mark.parametrize(
    "answer",
    [
        "예약 내역에서 확인할 수 없습니다.",
        "해당 정보는 예약 내역에서 확인할 수 없습니다",
        "메일에서 찾을 수 없었습니다.",
        "관련 정보가 없습니다.",
    ],
)
def test_is_refusal_accepts_varied_phrasing(answer):
    assert _is_refusal(answer)


def test_is_refusal_rejects_actual_answer():
    assert not _is_refusal("체크아웃 시간은 11:00입니다.")
