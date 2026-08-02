"""LangChain 에이전트: 예약 검색 / 웹 검색 / 날짜 계산 도구를 라우팅한다."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from langchain.agents import create_agent
from langchain_core.tools import tool

from src.config import (
    AGENT_MODEL,
    NO_INFO_MESSAGE,
    TAVILY_API_KEY,
    TOP_K,
    WEB_SEARCH_MAX_RESULTS,
)
from src.rag import answer_question
from src.store import VectorStore

SYSTEM_PROMPT = f"""너는 사용자의 여행 예약을 관리하는 어시스턴트다.

도구 선택 규칙:
- 사용자 **본인의 예약**에 관한 질문(체크아웃 시간, 환불 규정, 예약번호,
  항공편 시각, 숙소 위치, 일정 등) → 반드시 search_bookings 를 쓴다.
  추측으로 답하지 말고 검색 결과를 근거로만 답한다.
- 예약 메일에 있을 리 없는 **일반 여행 정보**(현지 날씨, 환율, 관광지 추천,
  일반적인 수하물 규정, 교통편 안내) → web_search 를 쓴다.
- "둘째 날", "3일차"처럼 **여행 N일차**가 나오면 먼저 resolve_trip_day 로
  실제 날짜를 구한 뒤, 그 날짜로 search_bookings 를 호출한다.
- 단순 인사나 잡담 → 도구 없이 바로 답한다.

답변 규칙:
- search_bookings 결과에 없는 내용을 지어내지 않는다. 도구가
  "{NO_INFO_MESSAGE}"라고 하면 그대로 전달하고, 아는 척 보충하지 않는다.
- 여권번호·비자·결제카드 정보처럼 예약 확인 메일에 없는 개인정보는
  절대 추측하지 않는다.
- search_bookings 로 답했으면 도구가 준 출처 표시를 답변에 유지한다.
- 한국어로 간결하게 답한다."""


# UI가 "이 답변이 어느 메일에서 나왔는지"를 표시할 수 있도록 마지막 근거를 보관한다.
# 도구는 문자열만 반환할 수 있어 구조화된 출처를 함께 돌려줄 방법이 없다.
_last_sources: list[dict] = []


def get_last_sources() -> list[dict]:
    """직전 search_bookings 호출이 사용한 근거 목록."""
    return list(_last_sources)


@tool
def search_bookings(query: str) -> str:
    """사용자 본인의 여행 예약 메일(항공/숙소/렌터카/투어)에서 정보를 찾는다.

    체크인·체크아웃 시간, 환불·취소 규정, 예약번호, 항공편 시각, 숙소 주소,
    투어 집합 장소 등 '내 예약'에 대한 질문에 사용한다.

    Args:
        query: 찾고자 하는 내용. 예: "체크아웃 시간", "투어 환불 규정"
    """
    global _last_sources

    result = answer_question(query, top_k=TOP_K)
    _last_sources = [
        {
            "source_file": hit["metadata"].get("source_file"),
            "type": hit["metadata"].get("type"),
            "provider": hit["metadata"].get("provider"),
            "confirmation_number": hit["metadata"].get("confirmation_number"),
            "similarity": hit["similarity"],
        }
        for hit in result["hits"]
    ]

    if not result["used_context"]:
        return NO_INFO_MESSAGE
    return result["answer"]


@tool
def web_search(query: str) -> str:
    """예약 메일에 없는 일반 여행 정보를 웹에서 찾는다.

    현지 날씨, 환율, 관광지 추천, 항공사의 일반 규정 등에 사용한다.
    사용자 본인의 예약 정보에는 절대 사용하지 않는다.

    Args:
        query: 검색어. 예: "도쿄 날씨", "대한항공 기내 반입 규정"
    """
    if not TAVILY_API_KEY:
        return (
            "웹 검색을 쓸 수 없습니다. TAVILY_API_KEY가 설정되지 않았습니다. "
            "이 정보는 예약 내역으로는 답할 수 없습니다."
        )

    from langchain_tavily import TavilySearch

    search = TavilySearch(max_results=WEB_SEARCH_MAX_RESULTS, tavily_api_key=TAVILY_API_KEY)
    response = search.invoke({"query": query})

    results = response.get("results", []) if isinstance(response, dict) else []
    if not results:
        return f"'{query}'에 대한 웹 검색 결과가 없습니다."

    return "\n\n".join(
        f"[{item.get('title')}] {item.get('content', '')[:400]}\n출처: {item.get('url')}"
        for item in results
    )


# 사용자가 UI에서 여행 시작일을 직접 지정하면 인덱스에서 추정한 값보다 우선한다.
_trip_start_override: str | None = None


def set_trip_start(value: str | None) -> None:
    """여행 시작일을 'YYYY-MM-DD'로 고정한다. None이면 인덱스에서 추정한다."""
    global _trip_start_override
    _trip_start_override = value


@tool
def resolve_trip_day(day_number: int) -> str:
    """여행 N일차가 실제로 몇 월 며칠인지 계산한다.

    "둘째 날", "3일차" 같은 표현이 나오면 먼저 이 도구로 날짜를 구한 뒤
    그 날짜로 search_bookings 를 호출한다. 여행 시작일은 인덱싱된 예약 중
    가장 빠른 날짜를 쓴다.

    Args:
        day_number: 여행 며칠째인지. 첫날이 1.
    """
    if day_number < 1:
        return "일차는 1 이상이어야 합니다. 첫날이 1일차입니다."

    start, end = _store().trip_date_range()
    if _trip_start_override:
        start = _trip_start_override
    if not start:
        return "인덱싱된 예약이 없어 여행 시작일을 알 수 없습니다."

    start_date = date.fromisoformat(start)
    target = start_date + timedelta(days=day_number - 1)

    message = (
        f"여행 시작일은 {start} (1일차)이므로, {day_number}일차는 {target.isoformat()}입니다."
    )
    if end and target > date.fromisoformat(end):
        message += f" 다만 마지막 예약이 {end}에 끝나므로 여행 기간을 벗어납니다."
    return message


@lru_cache(maxsize=1)
def _store() -> VectorStore:
    return VectorStore()


@lru_cache(maxsize=1)
def build_agent():
    """도구 3개를 가진 에이전트를 만든다."""
    return create_agent(
        model=f"openai:{AGENT_MODEL}",
        tools=[search_bookings, web_search, resolve_trip_day],
        system_prompt=SYSTEM_PROMPT,
    )


def ask(question: str, history: list[dict] | None = None) -> dict:
    """질문 하나를 처리하고 답변·사용한 도구·근거를 함께 반환한다.

    history를 넘기면 이전 대화를 이어간다("그 도시" 같은 지시대명사 해석에 필요).
    """
    global _last_sources
    _last_sources = []

    messages = [*(history or []), {"role": "user", "content": question}]
    result = build_agent().invoke({"messages": messages})

    tools_used = []
    for message in result["messages"]:
        for call in getattr(message, "tool_calls", None) or []:
            tools_used.append(call["name"])

    return {
        "answer": result["messages"][-1].content,
        "tools_used": tools_used,
        "sources": get_last_sources(),
        "messages": result["messages"],
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        sys.exit('사용법: python -m src.agent "질문"')

    outcome = ask(" ".join(sys.argv[1:]))
    print(f"[사용한 도구: {', '.join(outcome['tools_used']) or '없음'}]\n")
    print(outcome["answer"])
