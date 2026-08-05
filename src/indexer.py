"""메일 로딩 → 구조화 추출 → 검색용 텍스트 → 임베딩 → 벡터 저장 파이프라인."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from src.config import CHUNK_OVERLAP, CHUNK_SIZE, MAX_SINGLE_CHUNK_CHARS
from src.embedder import embed_texts
from src.loader import load_emails
from src.parser import parse_reservation
from src.store import VectorStore

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# time 필드는 예약 종류마다 의미가 다르다. 검색어("체크아웃 몇 시")와 맞물리도록
# 라벨을 붙여 문장으로 만든다.
TIME_LABELS = {
    "항공": "출발~도착 시각",
    "숙소": "체크인~체크아웃 시각",
    "렌터카": "픽업~반납 시각",
    "투어": "집합 시각",
}


def index_emails(
    directory: str | Path | None = None,
    store: VectorStore | None = None,
    force: bool = False,
    parse_cache: dict[str, dict] | None = None,
) -> dict:
    """디렉터리의 메일을 인덱싱한다.

    이미 인덱싱된 메일은 건너뛴다. 파일 내용이 바뀌면(해시 불일치) 다시 인덱싱한다.
    force=True면 전부 다시 인덱싱한다.

    parse_cache는 content_hash를 키로 하는 추출 결과다. 해시가 맞으면 LLM을
    호출하지 않고 그대로 쓴다(시드 인덱싱에 쓰인다).
    """
    store = store or VectorStore()
    emails = load_emails(directory)
    already = store.indexed_sources()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    indexed, skipped = [], []

    for email in emails:
        filename = email["filename"]
        content_hash = _hash(email["raw_text"])

        if not force and already.get(filename) == content_hash:
            skipped.append(filename)
            continue

        parsed = (parse_cache or {}).get(content_hash)
        if parsed is None:
            print(f"  파싱 중: {filename}", file=sys.stderr, flush=True)
            parsed = parse_reservation(email["raw_text"])
        else:
            print(f"  시드 사용: {filename}", file=sys.stderr, flush=True)
        metadata = build_metadata(parsed, filename, content_hash)
        chunks = _chunk(build_search_text(parsed))

        # 내용이 바뀐 경우 이전 청크가 남지 않도록 먼저 지운다.
        store.delete_by_source(filename)

        for chunk_index, chunk in enumerate(chunks):
            ids.append(f"{filename}::{chunk_index}")
            documents.append(chunk)
            metadatas.append({**metadata, "chunk_index": chunk_index})
        indexed.append(filename)

    if documents:
        embeddings = embed_texts(documents)
        store.add(ids, documents, embeddings, metadatas)

    return {
        "indexed": indexed,
        "skipped": skipped,
        "chunks_added": len(documents),
        "total_in_store": store.count(),
    }


def build_search_text(parsed: dict) -> str:
    """구조화 정보 + 원문 발췌를 검색용 자연어 문장으로 합친다."""
    reservation_type = parsed.get("type") or "예약"
    provider = parsed.get("provider") or "제공처 미상"

    lines = [f"[{reservation_type}] {provider} 예약 확인."]

    if parsed.get("confirmation_number"):
        lines.append(f"예약번호(확인번호)는 {parsed['confirmation_number']}.")
    if parsed.get("date"):
        lines.append(f"이용 날짜는 {parsed['date']}.")
    if parsed.get("time"):
        label = TIME_LABELS.get(reservation_type, "이용 시각")
        lines.append(f"{label}은 {parsed['time']}.")
    if parsed.get("location"):
        lines.append(f"장소는 {parsed['location']}.")
    if parsed.get("refund_policy"):
        lines.append(f"환불 및 취소 규정: {parsed['refund_policy']}")
    if parsed.get("raw_snippet"):
        lines.append(f"메일 원문 발췌: {parsed['raw_snippet']}")

    return "\n".join(lines)


def build_metadata(parsed: dict, filename: str, content_hash: str = "") -> dict:
    """필터링에 쓸 메타데이터를 만든다.

    date는 정렬 가능한 YYYY-MM-DD로 정규화한다. Chroma의 $gte/$lte는 문자열을
    받지 않고 int/float만 받으므로, 범위 필터용 YYYYMMDD 정수 키를 함께 넣는다.
    """
    start, end = _split_date_range(parsed.get("date"))

    return {
        "type": parsed.get("type"),
        "provider": parsed.get("provider"),
        "date": start,
        "location": parsed.get("location"),
        "source_file": filename,
        # 답변 출처 표시와 요약 카드에 쓰인다.
        "confirmation_number": parsed.get("confirmation_number"),
        "time": parsed.get("time"),
        # --- 범위 필터용 ---
        "date_end": end,
        "date_start_int": _to_int(start),
        "date_end_int": _to_int(end),
        "content_hash": content_hash,
    }


def _split_date_range(value: str | None) -> tuple[str | None, str | None]:
    """'2026-10-12 ~ 2026-10-15' → ('2026-10-12', '2026-10-15').

    단일 날짜면 시작과 끝이 같다. 날짜를 못 찾으면 (None, None).
    """
    if not value:
        return None, None
    found = ["-".join(groups) for groups in _DATE_RE.findall(value)]
    if not found:
        return None, None
    return found[0], found[-1]


def _to_int(date: str | None) -> int | None:
    """'2026-10-12' → 20261012. 범위 비교 필터에 쓴다."""
    return int(date.replace("-", "")) if date else None


def _chunk(text: str) -> list[str]:
    """예약 메일은 짧으므로 기본은 1건 = 1청크. 너무 길 때만 나눈다."""
    if len(text) <= MAX_SINGLE_CHUNK_CHARS:
        return [text]

    chunks = []
    start = 0
    # overlap이 chunk 크기 이상이면 start가 전진하지 않아 무한 루프가 된다.
    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += step
    return chunks


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


if __name__ == "__main__":
    from src.config import SAMPLE_EMAILS_DIR

    args = sys.argv[1:]
    target = SAMPLE_EMAILS_DIR if "--sample" in args else None
    summary = index_emails(directory=target, force="--force" in args)

    print(f"인덱싱 완료: {len(summary['indexed'])}건")
    for name in summary["indexed"]:
        print(f"  + {name}")
    if summary["skipped"]:
        print(f"건너뜀(이미 인덱싱됨): {len(summary['skipped'])}건")
        for name in summary["skipped"]:
            print(f"  - {name}")
    print(f"저장된 청크 총 {summary['total_in_store']}개")
