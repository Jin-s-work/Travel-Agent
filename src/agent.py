"""LangChain 에이전트: 예약 검색 / 웹 검색 / 날짜 계산 도구를 라우팅한다."""

from __future__ import annotations

import re
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
from src.store import get_store

SYSTEM_PROMPT = f"""너는 사용자의 여행 예약을 관리하는 어시스턴트다.

도구 선택 규칙:
- 사용자 **본인의 예약**에 관한 질문(체크아웃 시간, 환불 규정, 예약번호,
  항공편 시각, 숙소 위치, 일정 등) → 반드시 search_bookings 를 쓴다.
  추측으로 답하지 말고 검색 결과를 근거로만 답한다.
- 예약 메일에 있을 리 없는 **일반 여행 정보**(현지 날씨, 환율, 관광지 추천,
  일반적인 수하물 규정, 교통편 안내) → web_search 를 쓴다.
- "둘째 날", "3일차"처럼 **여행 N일차**가 나오면 먼저 resolve_trip_day 로
  실제 날짜를 구한다.
- **하루치 일정 전체**를 묻는 질문("둘째 날 일정 뭐야", "10월 13일에 뭐 있어")
  → bookings_on_date 를 쓴다. search_bookings 는 상위 몇 건만 돌려주므로
  그날 예약이 누락된다. 특정 항목 하나("그날 숙소 체크인 몇 시")를 물을
  때만 search_bookings 를 쓴다.
- 단순 인사나 잡담 → 도구 없이 바로 답한다.

답변 규칙:
- **도구 이름을 답변에 쓰지 않는다.** "search_bookings 결과:" 같은 말머리를
  붙이지 않는다. 어떤 도구를 썼는지는 화면이 따로 보여준다.
- 도구 결과에 없는 내용을 지어내지 않는다. 도구가
  "{NO_INFO_MESSAGE}"라고 하면 **그 문장만** 전한다. 어디서 찾아보라는 조언이나
  일반적인 설명을 덧붙이지 않는다.
- bookings_on_date 가 여러 건을 돌려주면 **하나도 빼놓지 않고** 전한다.
  "숙박 정보만 있다"처럼 목록에 있는 항목을 누락한 채 단정하지 않는다.
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
def bookings_on_date(day: str) -> str:
    """특정 날짜의 예약을 빠짐없이 모두 나열한다.

    "그날 일정", "둘째 날 뭐 있어" 처럼 **날짜 기준으로 전체 목록**이 필요할 때
    쓴다. search_bookings 는 의미가 가까운 상위 몇 건만 돌려주므로 하루치
    일정을 묻는 질문에는 빠지는 예약이 생긴다.

    Args:
        day: 'YYYY-MM-DD' 형식의 날짜. resolve_trip_day 결과를 그대로 넣는다.
    """
    global _last_sources

    records = get_store().reservations_on_date(day)
    _last_sources = [
        {
            "source_file": record.get("source_file"),
            "type": record.get("type"),
            "provider": record.get("provider"),
            "confirmation_number": record.get("confirmation_number"),
            "similarity": None,  # 유사도가 아니라 날짜 필터로 뽑은 결과다
        }
        for record in records
    ]

    if not records:
        return f"{day}에 해당하는 예약이 없습니다."

    lines = [f"{day} 예약 {len(records)}건:"]
    for record in records:
        span = record.get("date")
        if record.get("date_end") and record["date_end"] != record.get("date"):
            span = f"{record['date']} ~ {record['date_end']}"
        lines.append(
            f"- [{record.get('type') or '미상'}] {record.get('provider') or '제공처 미상'}"
            f" / 기간 {span} / 시각 {record.get('time') or '미상'}"
            f" / 장소 {record.get('location') or '미상'}"
            f" / 예약번호 {record.get('confirmation_number') or '없음'}"
        )
    return "\n".join(lines)


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

    "둘째 날", "3일차" 같은 표현이 나오면 먼저 이 도구로 날짜를 구한다.
    이어서 그날 일정 전체가 필요하면 bookings_on_date 를, 특정 항목 하나만
    필요하면 search_bookings 를 호출한다. 여행 시작일은 인덱싱된 예약 중
    가장 빠른 날짜를 쓴다.

    Args:
        day_number: 여행 며칠째인지. 첫날이 1.
    """
    if day_number < 1:
        return "일차는 1 이상이어야 합니다. 첫날이 1일차입니다."

    start, end = get_store().trip_date_range()
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
def build_agent():
    """도구 4개를 가진 에이전트를 만든다."""
    return create_agent(
        model=f"openai:{AGENT_MODEL}",
        tools=[search_bookings, bookings_on_date, web_search, resolve_trip_day],
        system_prompt=SYSTEM_PROMPT,
    )


# 프롬프트로 "도구 이름을 쓰지 마라"라고 해도 모델이 말머리를 붙이는 경우가 있다
# (실제로 "search_bookings 결과: ..."가 화면에 그대로 나왔다). 지시에만 기대지 않는다.
_TOOL_NAMES = r"(?:search_bookings|bookings_on_date|web_search|resolve_trip_day)"
_LABEL = r"(?:\s*(?:결과|응답|출력|result|output))?"
_TOOL_PREFIX_RE = re.compile(
    # 괄호로 감싼 형태는 콜론이 없어도 말머리다. 맨 앞의 대괄호는 본문에 쓸 일이 없다.
    rf"^\s*(?:[\[(]\s*{_TOOL_NAMES}\s*[\])]{_LABEL}\s*[:：]?"
    # 괄호가 없으면 콜론이 있어야 한다. '시각: 15:00'처럼 멀쩡한 문장을 자르지 않는다.
    rf"|{_TOOL_NAMES}{_LABEL}\s*[:：])\s*",
    re.IGNORECASE,
)


def _strip_tool_mentions(answer: str) -> str:
    """답변 앞에 붙은 도구 이름 말머리를 걷어낸다."""
    previous = None
    while previous != answer:                 # "web_search 결과: search_bookings 결과:"
        previous = answer
        answer = _TOOL_PREFIX_RE.sub("", answer, count=1)
    return answer.strip()


def _trim_after_refusal(answer: str, tools_used: list[str]) -> str:
    """거절로 시작하면 그 문장만 남긴다.

    "예약 내역에서 확인할 수 없습니다" 뒤에 "여권 원본이나 정부 사이트에서
    확인하세요" 같은 조언이 붙는 일이 있었다. 프롬프트로 막아도 계속 나온다.
    근거가 없다고 답하는 자리에 일반 지식을 얹으면 거절의 의미가 흐려진다.

    웹 검색을 썼다면 뒤 내용이 실제 검색 결과일 수 있어 건드리지 않는다.
    """
    if "web_search" in tools_used or not answer.startswith(NO_INFO_MESSAGE):
        return answer
    return NO_INFO_MESSAGE


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
        "answer": _trim_after_refusal(
            _strip_tool_mentions(result["messages"][-1].content), tools_used
        ),
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
