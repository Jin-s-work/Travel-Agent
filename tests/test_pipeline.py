"""파이프라인 순수 함수 단위 테스트.

LLM이나 임베딩 API를 호출하지 않으므로 빠르고 비용이 들지 않는다.
"""

import json
from pathlib import Path

import pytest

from src.config import MIN_SIMILARITY, RESERVATION_TYPES, SEED_FILE
from src.evaluate import _is_refusal
from src.indexer import _chunk, _hash, _split_date_range, _to_int, build_metadata, build_search_text
from src.loader import read_email_file
from src.parser import (
    _coerce_time,
    _correct_type,
    _labelled_time,
    _normalize,
    _restore_time_range,
    infer_type,
    FIELDS,
)
from src.rag import detect_reservation_type
from src.store import _apply_threshold, _clean_metadata, _to_int as _date_to_int


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


def test_coerce_time_keeps_only_meeting_time_for_tours():
    """투어는 집합 시각 하나만 쓴다.

    '집합 07:40 (출발 08:00)'을 범위로 두면 화면에 투어가 08:00에 끝나는 것처럼
    보인다. 같은 형식의 다른 투어 메일은 하나만 담아 결과가 메일마다 갈렸다.
    """
    assert _coerce_time("집합 07:40 (출발 08:00 정시)", "투어") == "07:40"
    assert _coerce_time("07:40 ~ 08:00", "투어") == "07:40"
    # 다른 종류는 범위를 유지한다.
    assert _coerce_time("07:40 ~ 08:00", "항공") == "07:40 ~ 08:00"
    assert _coerce_time("07:40 ~ 08:00") == "07:40 ~ 08:00"


def test_normalize_fills_all_fields_and_rejects_unknown_type():
    result = _normalize({"provider": "ANA", "type": "기차", "location": "   "})
    assert set(result) == set(FIELDS)
    assert result["provider"] == "ANA"
    assert result["type"] is None, "화이트리스트 밖 종류는 null이어야 한다"
    assert result["location"] is None, "공백뿐인 값은 null이어야 한다"


def test_normalize_truncates_long_snippet():
    result = _normalize({"raw_snippet": "가" * 500})
    assert len(result["raw_snippet"]) <= 301


# --------------------------------------------------- 종류 보정 (규칙 기반)
EMAIL_DIRS = (Path("tests/sample_emails"), Path("tests/demo_emails"))
EXPECTED_TYPES = {
    "01_korean_air_outbound.txt": "항공",
    "02_ana_return.eml": "항공",
    "03_shinjuku_hotel.txt": "숙소",
    "04_hakone_ryokan.txt": "숙소",
    "05_fuji_day_tour.txt": "투어",
    "06_hakone_rentcar.eml": "렌터카",
    "07_asakusa_tour.eml": "투어",
}


@pytest.mark.parametrize("filename,expected", sorted(EXPECTED_TYPES.items()))
def test_infer_type_matches_every_sample_email(filename, expected):
    """규칙만으로 7건 전부를 맞혀야 LLM 오분류를 되돌릴 근거가 된다."""
    path = next(d / filename for d in EMAIL_DIRS if (d / filename).exists())
    assert infer_type(read_email_file(str(path)))[0] == expected


def test_correct_type_overrides_confident_misclassification():
    """실제로 관측된 사례. ANA 귀국편이 '투어'로 분류돼 검색에서 누락됐다."""
    ana = read_email_file("tests/sample_emails/02_ana_return.eml")
    assert _correct_type({"type": "투어"}, ana)["type"] == "항공"


def test_correct_type_fills_null():
    tour = read_email_file("tests/demo_emails/07_asakusa_tour.eml")
    assert _correct_type({"type": None}, tour)["type"] == "투어"


def test_restore_time_range_fills_dropped_arrival():
    """실제로 관측된 사례. ANA 귀국편에서 도착 시각이 실행에 따라 빠졌다."""
    ana = read_email_file("tests/sample_emails/02_ana_return.eml")
    assert _restore_time_range({"type": "항공", "time": "18:55"}, ana)["time"] == "18:55 ~ 21:35"


def test_restore_time_range_leaves_complete_values_alone():
    ana = read_email_file("tests/sample_emails/02_ana_return.eml")
    complete = {"type": "항공", "time": "18:55 ~ 21:35"}
    assert _restore_time_range(dict(complete), ana) == complete


def test_restore_time_range_abstains_when_rule_disagrees():
    """규칙이 찾은 시작 시각이 모델 값과 다르면 엉뚱한 줄을 봤을 수 있다."""
    ana = read_email_file("tests/sample_emails/02_ana_return.eml")
    assert _restore_time_range({"type": "항공", "time": "07:00"}, ana)["time"] == "07:00"


def test_restore_time_range_skips_tours():
    """투어는 집합 시각 하나뿐이라 채울 짝이 없다."""
    tour = read_email_file("tests/demo_emails/07_asakusa_tour.eml")
    assert _restore_time_range({"type": "투어", "time": "16:30"}, tour)["time"] == "16:30"


def test_labelled_time_skips_lines_without_a_clock():
    """'closes 60 minutes before departure'처럼 라벨만 있고 시각이 없는 줄이 많다."""
    text = "Cancellation before departure: fee JPY 6,000\nDeparture : 17 OCT 2026 18:55 HND"
    assert _labelled_time(text, ("departure",)) == "18:55"


def test_correct_type_keeps_llm_answer_without_evidence():
    """근거가 없으면 LLM 판단을 존중한다. 규칙은 뒤집을 때만 개입한다."""
    assert _correct_type({"type": "숙소"}, "예약이 확정되었습니다.")["type"] == "숙소"


def test_infer_type_abstains_on_tie():
    """1위가 단독이 아니면 판단을 보류한다. 숙소 1점, 투어 1점."""
    assert infer_type("체크인 후 투어 안내")[0] is None


def test_correct_type_ignores_single_incidental_keyword():
    """신호가 하나뿐이면 지나가는 단어일 수 있어 뒤집지 않는다."""
    text = "투어 일정 안내입니다."  # '투어' 하나만 걸린다
    assert _correct_type({"type": "숙소"}, text)["type"] == "숙소"


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


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-10-13", 20261013),
        ("20261013", 20261013),
        ("2026-10", None),      # 자릿수가 모자라면 범위 비교가 어긋난다
        ("내일", None),
        ("", None),
        (None, None),
    ],
)
def test_store_to_int(value, expected):
    assert _date_to_int(value) == expected


# --------------------------------------------------------------- 시드
def test_seed_file_covers_every_demo_email():
    """시드에 빠진 메일이 있으면 기동 시 그 메일만 LLM 파싱이 일어난다."""
    entries = {item["filename"]: item for item in _seed_entries()}
    assert set(entries) == set(EXPECTED_TYPES)


def test_seed_hashes_match_current_emails():
    """메일을 고치고 시드를 다시 만들지 않으면 해시가 어긋나 시드가 무시된다."""
    for item in _seed_entries():
        path = next(d / item["filename"] for d in EMAIL_DIRS
                    if (d / item["filename"]).exists())
        assert item["content_hash"] == _hash(read_email_file(str(path))), (
            f"{item['filename']}의 시드가 낡았습니다. python -m src.seed --build 를 실행하세요."
        )


def test_seed_entries_are_usable():
    """시드 값이 그대로 인덱싱되므로 종류와 날짜는 채워져 있어야 한다."""
    for item in _seed_entries():
        parsed = item["parsed"]
        assert parsed["type"] == EXPECTED_TYPES[item["filename"]]
        assert parsed["provider"], f"{item['filename']}에 provider가 없습니다"
        assert _split_date_range(parsed["date"])[0], f"{item['filename']}에 날짜가 없습니다"


def test_load_seed_keys_by_content_hash():
    """인덱서는 해시로 시드를 찾는다. 파일명이 아니라 내용이 기준이다."""
    from src.seed import load_seed

    cache = load_seed()
    assert cache, "시드가 비어 있습니다"
    for item in _seed_entries():
        assert cache[item["content_hash"]] == item["parsed"]


class _FakeStore:
    """index_emails가 쓰는 최소 인터페이스만 흉내낸다."""

    def __init__(self):
        self.added: list[dict] = []

    def indexed_sources(self):
        return {}

    def delete_by_source(self, source_file):
        pass

    def add(self, ids, documents, embeddings, metadatas):
        self.added = list(metadatas)
        return len(ids)

    def count(self):
        return len(self.added)


def test_index_emails_uses_seed_without_calling_llm(monkeypatch):
    """시드 해시가 맞으면 LLM 파싱이 일어나면 안 된다.

    기동할 때마다 파싱하면 비용이 들고 값이 또 흔들린다. 시드를 두는 이유가 없어진다.
    """
    import src.indexer as indexer
    from src.seed import load_seed

    def explode(*_args, **_kwargs):
        raise AssertionError("시드가 있는데 LLM 파싱이 호출됐습니다")

    monkeypatch.setattr(indexer, "parse_reservation", explode)
    # 임베딩도 네트워크를 타므로 차단한다. 차원은 아무 값이나 상관없다.
    monkeypatch.setattr(indexer, "embed_texts", lambda texts: [[0.0] for _ in texts])

    store = _FakeStore()
    summary = indexer.index_emails(
        directory=Path("tests/demo_emails"), store=store, parse_cache=load_seed()
    )

    assert summary["indexed"] == ["06_hakone_rentcar.eml", "07_asakusa_tour.eml"]
    assert [m["type"] for m in store.added] == ["렌터카", "투어"]


def test_deploy_image_includes_seed_and_its_emails():
    """이미지에 시드나 원본 메일이 빠지면 배포 환경에서만 조용히 실패한다.

    실제로 seed/와 tests/를 빠뜨려 데모 링크가 빈 화면이 된 적이 있다.
    """
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")

    for path in ("seed/", "tests/sample_emails/", "tests/demo_emails/"):
        assert f"COPY --chown=user {path}" in dockerfile, f"{path}가 이미지에 복사되지 않습니다"

    # tests/ 전체를 제외하고 있으므로 두 디렉터리는 되살려 놓아야 한다.
    for path in ("tests/sample_emails/", "tests/demo_emails/"):
        assert f"!{path}" in dockerignore, f"{path}가 .dockerignore에서 되살아나지 않습니다"


def _seed_entries() -> list[dict]:
    assert SEED_FILE.is_file(), f"시드 파일이 없습니다: {SEED_FILE}"
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))["emails"]


def test_get_store_returns_one_shared_instance():
    """계층마다 스토어를 따로 만들면 인덱스를 비운 뒤 서로 어긋난다.

    실제로 에이전트가 삭제된 컬렉션 핸들을 들고 있어 "인덱스 비우기" 다음
    N일차 질문이 NotFoundError로 죽었다.
    """
    import api
    import src.rag as rag
    import src.seed as seed
    from src.store import get_store

    assert get_store() is get_store()
    assert api.store() is get_store()
    # rag·seed가 기본값으로 쓰는 스토어도 같은 인스턴스여야 한다.
    assert rag.get_store() is get_store()
    assert seed.get_store() is get_store()


@pytest.fixture(scope="module")
def seeded_store(tmp_path_factory):
    """시드 7건을 임시 스토어에 넣는다. 임베딩은 가짜로 넣어 API를 부르지 않는다.

    날짜 조회는 유사도를 쓰지 않으므로 벡터 값이 무엇이든 결과가 같다.
    """
    import src.indexer as indexer
    from src.seed import load_seed
    from src.store import VectorStore

    real_embed = indexer.embed_texts
    indexer.embed_texts = lambda texts: [[0.0] * 8 for _ in texts]
    try:
        store = VectorStore(persist_dir=tmp_path_factory.mktemp("dates"), collection_name="date_lookup")
        for directory in EMAIL_DIRS:
            indexer.index_emails(directory=directory, store=store, parse_cache=load_seed())
        yield store
    finally:
        indexer.embed_texts = real_embed


@pytest.mark.parametrize(
    "day,expected",
    [
        # 하루에 여러 건이 겹치고, 기간이 있는 예약은 중간 날짜에도 걸려야 한다.
        ("2026-10-12", {"대한항공", "Korean Air", "Hotel Gracery Shinjuku"}),
        ("2026-10-13", {"Hotel Gracery Shinjuku", "Tokyo Local Walks Inc."}),
        ("2026-10-14", {"Hotel Gracery Shinjuku", "Japan Highlight Travel Co., Ltd."}),
    ],
)
def test_reservations_on_date_includes_everything_that_day(seeded_store, day, expected):
    """'둘째 날 일정'이 그날 투어를 빠뜨렸던 버그를 결정적으로 막는다.

    의미 검색은 상위 몇 건만 주므로 누락이 생겼다. 날짜 필터는 전부 가져와야 한다.
    """
    found = {r.get("provider") for r in seeded_store.reservations_on_date(day)}
    assert found <= expected, f"{day}에 엉뚱한 예약이 걸렸습니다: {found - expected}"
    assert len(found) == 2, f"{day}에 예약 2건이 나와야 하는데 {found}"


def test_reservations_on_date_catches_overlapping_stays(seeded_store):
    """체크아웃하는 날과 체크인하는 날이 겹치면 셋 다 나와야 한다."""
    found = {r.get("provider") for r in seeded_store.reservations_on_date("2026-10-15")}
    assert found == {
        "Hotel Gracery Shinjuku",          # 이날 체크아웃
        "Yumotoso Hakone (箱根 湯本荘)",     # 이날 체크인
        "Times CAR RENTAL",                # 이날 픽업
    }


def test_reservations_on_date_returns_nothing_outside_the_trip(seeded_store):
    assert seeded_store.reservations_on_date("2026-10-20") == []
    assert seeded_store.reservations_on_date("내일") == []


def test_store_recovers_when_collection_is_recreated_elsewhere(tmp_path):
    """다른 프로세스가 컬렉션을 다시 만들어도 살아남아야 한다.

    실제로 평가 스크립트가 인덱스를 지우자 떠 있던 서버가 옛 UUID를 들고
    NotFoundError로 죽었다. 그 뒤 모든 요청이 실패했다.
    """
    from src.store import VectorStore

    kwargs = {"persist_dir": tmp_path, "collection_name": "heal"}
    server = VectorStore(**kwargs)
    server.add(["x::0"], ["[숙소] 테스트"], [[0.1] * 8], [{"source_file": "x", "type": "숙소"}])
    assert server.count() == 1

    VectorStore(**kwargs).reset()          # 다른 프로세스가 지우고 다시 만든다

    assert server.count() == 0             # 예전에는 여기서 NotFoundError
    assert server.all_reservations() == []
    server.add(["y::0"], ["[항공] 테스트"], [[0.2] * 8], [{"source_file": "y", "type": "항공"}])
    assert server.count() == 1


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
