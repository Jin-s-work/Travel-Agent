"""검색된 예약 정보만 근거로 답변을 생성한다."""

from __future__ import annotations

from functools import lru_cache

from src.config import (
    ANSWER_MODEL,
    NO_INFO_MESSAGE,
    OPENAI_API_KEY,
    REASONING_EFFORT,
    TOP_K,
)
from src.prompts import load_prompt
from src.store import VectorStore, get_store

# 질문이 특정 예약 종류를 지목했는지 판단하는 키워드.
# 프롬프트로 "종류가 다르면 답하지 마라"고 지시해봤지만 모델이 무시했다
# (렌터카 픽업 시각을 물으면 투어 집합 시각을 가져다 썼다).
# 그래서 검색 단계에서 메타데이터 필터로 막는다.
TYPE_KEYWORDS = {
    "렌터카": ("렌터카", "렌트카", "차량 픽업", "차 픽업", "카시트"),
    "항공": ("항공", "비행기", "비행편", "탑승", "기내", "수하물"),
    "숙소": ("숙소", "호텔", "료칸", "체크인", "체크아웃", "숙박", "조식"),
    "투어": ("투어", "가이드", "집합"),
}

# Moved verbatim to src/prompts/trip_answer.md (2026-10-21); only the refusal
# sentence is a variable so config and prompt cannot drift apart.
ANSWER_PROMPT = load_prompt("trip_answer")
SYSTEM_PROMPT = ANSWER_PROMPT.render(NO_INFO_MESSAGE=NO_INFO_MESSAGE)


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return OpenAI(api_key=OPENAI_API_KEY, max_retries=0, timeout=45.0)


def answer_question(
    question: str,
    top_k: int = TOP_K,
    where: dict | None = None,
    store: VectorStore | None = None,
) -> dict:
    """질문에 대해 예약 내역을 검색하고, 그 결과만 근거로 답한다.

    반환: {"answer", "sources", "hits", "used_context"}
    """
    hits = retrieve(question, top_k=top_k, where=where, store=store)

    # 임계값을 넘는 근거가 하나도 없으면 LLM을 부르지 않는다.
    # 부르지 않는 것이 환각을 막는 가장 확실한 방법이다.
    if not hits:
        return {"answer": NO_INFO_MESSAGE, "sources": [], "hits": [], "used_context": False}

    return {
        "answer": generate(question, hits),
        "sources": [_source_label(hit["metadata"]) for hit in hits],
        "hits": hits,
        "used_context": True,
    }


def retrieve(
    question: str,
    top_k: int = TOP_K,
    where: dict | None = None,
    store: VectorStore | None = None,
) -> list[dict]:
    """질문에 맞는 근거를 찾는다. 1초 안에 끝나므로 답변보다 먼저 보여줄 수 있다."""
    store = store or get_store()

    # 질문이 예약 종류를 명시했으면 그 종류로 좁힌다. 해당 종류의 예약이
    # 아예 없으면 여기서 0건이 되어 LLM을 부르지 않고 거절한다.
    if where is None:
        detected = detect_reservation_type(question)
        if detected:
            where = {"type": detected}

    return store.search(question, top_k=top_k, where=where)


def generate_result(question: str, hits: list[dict]):
    """검색된 근거만 가지고 답변을 만든다. 정규화된 결과(ChatResult)를 돌려준다."""
    from src.llm.gateway import chat
    return chat(
        "answer",
        model=ANSWER_MODEL,
        reasoning_effort=REASONING_EFFORT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[예약 정보]\n{format_context(hits)}\n\n[질문]\n{question}"},
        ],
        client_factory=_client,
        prompt=ANSWER_PROMPT.meta(),
        operation="answer",
    )


def generate(question: str, hits: list[dict]) -> str:
    """검색된 근거만 가지고 답변을 만든다. 여기서 시간의 대부분이 쓰인다."""
    result = generate_result(question, hits)
    if result.text is None:
        raise ValueError("답변 생성 결과가 비어 있습니다.")
    return result.text.strip()


def source_of(hit: dict) -> dict:
    """검색 결과 하나를 UI가 쓰는 출처 형태로 바꾼다."""
    metadata = hit["metadata"]
    return {
        "source_file": metadata.get("source_file"),
        "type": metadata.get("type"),
        "provider": metadata.get("provider"),
        "confirmation_number": metadata.get("confirmation_number"),
        "similarity": hit.get("similarity"),
    }


def detect_reservation_type(question: str) -> str | None:
    """질문이 지목한 예약 종류를 찾는다.

    정확히 한 종류만 걸릴 때에만 반환한다. 두 종류 이상이 섞이면
    (예: "호텔에서 공항까지") 잘못 좁힐 위험이 있어 필터를 걸지 않는다.
    """
    matched = [
        reservation_type
        for reservation_type, keywords in TYPE_KEYWORDS.items()
        if any(keyword in question for keyword in keywords)
    ]
    return matched[0] if len(matched) == 1 else None


def format_context(hits: list[dict]) -> str:
    """검색 결과를 LLM에 넣을 형태로 만든다."""
    blocks = []
    for index, hit in enumerate(hits, start=1):
        metadata = hit["metadata"]
        blocks.append(
            f"--- 예약 {index} "
            f"(type={metadata.get('type') or '미상'}, "
            f"provider={metadata.get('provider')}, "
            f"confirmation_number={metadata.get('confirmation_number') or '없음'}, "
            f"파일={metadata.get('source_file')}) ---\n"
            f"{hit['document']}"
        )
    return "\n\n".join(blocks)


def _source_label(metadata: dict) -> str:
    provider = metadata.get("provider") or "출처 미상"
    number = metadata.get("confirmation_number")
    return f"{provider} (예약번호 {number})" if number else provider
