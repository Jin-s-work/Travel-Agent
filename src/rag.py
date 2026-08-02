"""검색된 예약 정보만 근거로 답변을 생성한다."""

from __future__ import annotations

from functools import lru_cache

from src.config import ANSWER_MODEL, NO_INFO_MESSAGE, OPENAI_API_KEY, TOP_K
from src.store import VectorStore

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

SYSTEM_PROMPT = f"""너는 사용자의 여행 예약 내역을 정리해 답하는 어시스턴트다.

절대 규칙:
1. 아래 [예약 정보]에 실제로 적힌 내용만으로 답한다. 일반 상식이나 추측으로
   보충하지 않는다. 항공사·호텔의 통상적인 규정을 안다고 해도 쓰지 않는다.
2. [예약 정보]에 답이 없으면 다른 말을 덧붙이지 말고 정확히 이렇게 답한다:
   "{NO_INFO_MESSAGE}"
   여권번호·비자·결제카드처럼 예약 확인 메일에 없는 개인정보는 특히
   지어내면 안 된다.
2-1. **종류가 다르면 대체하지 않는다.** 질문이 특정 예약 종류(항공/숙소/
   렌터카/투어)를 지목했는데 [예약 정보]에 그 종류가 없으면, 비슷해 보이는
   다른 종류의 값을 가져다 쓰지 말고 "{NO_INFO_MESSAGE}"라고 답한다.
   예: 렌터카 픽업 시각을 물었는데 투어 집합 시각만 있으면 답하지 않는다.
   검색은 의미가 가까운 예약을 함께 가져오므로, 종류 확인은 네가 해야 한다.
3. 답변 마지막 줄에 근거가 된 메일을 이렇게 표시한다:
   출처: {{provider}} (예약번호 {{confirmation_number}})
   근거가 여러 건이면 쉼표로 나열한다. 답을 못 찾은 경우에는 출처를 쓰지 않는다.
4. 시각·금액·예약번호는 [예약 정보]에 적힌 그대로 옮긴다. 반올림하거나
   형식을 바꾸지 않는다.
5. 한국어로, 군더더기 없이 두세 문장 안에 답한다."""


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    return OpenAI(api_key=OPENAI_API_KEY)


def answer_question(
    question: str,
    top_k: int = TOP_K,
    where: dict | None = None,
    store: VectorStore | None = None,
) -> dict:
    """질문에 대해 예약 내역을 검색하고, 그 결과만 근거로 답한다.

    반환: {"answer", "sources", "hits", "used_context"}
    """
    store = store or VectorStore()

    # 질문이 예약 종류를 명시했으면 그 종류로 좁힌다. 해당 종류의 예약이
    # 아예 없으면 여기서 0건이 되어 LLM을 부르지 않고 거절한다.
    if where is None:
        detected = detect_reservation_type(question)
        if detected:
            where = {"type": detected}

    hits = store.search(question, top_k=top_k, where=where)

    # 임계값을 넘는 근거가 하나도 없으면 LLM을 부르지 않는다.
    # 부르지 않는 것이 환각을 막는 가장 확실한 방법이다.
    if not hits:
        return {"answer": NO_INFO_MESSAGE, "sources": [], "hits": [], "used_context": False}

    response = _client().chat.completions.create(
        model=ANSWER_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[예약 정보]\n{format_context(hits)}\n\n[질문]\n{question}"},
        ],
    )

    return {
        "answer": response.choices[0].message.content.strip(),
        "sources": [_source_label(hit["metadata"]) for hit in hits],
        "hits": hits,
        "used_context": True,
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
