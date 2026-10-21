"""Calendar-day parsing for whole-day trip questions. Pure stdlib, no LLM.

Moved from src/agent.py so the product /ask path (src/foundation/search.py)
does not import LangChain/LangGraph just to resolve a date. src/agent.py
re-exports the same names for the CLI and existing tests.
"""

from __future__ import annotations

import re
from datetime import date, timedelta


_DAILY_REQUEST_RE = re.compile(r"일정|스케줄|뭐\s*있|무엇.*있|예약(?!번호).*(?:전체|모두|목록|알려|있|보여|정리)|예약[은는이가]?\s*[?？]?$")
_ORDINAL_DAYS = {"첫": 1, "첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4, "다섯째": 5,
                 "여섯째": 6, "일곱째": 7, "여덟째": 8, "아홉째": 9, "열째": 10}


def daily_question_date(
    question: str, trip_start: str | None = None, history: list[dict] | None = None,
    trip_end: str | None = None,
) -> str | None:
    """Resolve a whole-day question without an LLM or top-k retrieval.

    Month/day uses the selected trip's year, including a Dec→Jan trip. Vague
    references are accepted only if the latest date-bearing turn has one date.
    None means no unambiguous full-day query; never fall back to today's year.
    """
    if not _DAILY_REQUEST_RE.search(question):
        return None
    if re.search(r"예약번호|환불|취소|체크인|체크아웃|몇\s*시", question) and not re.search(r"전체|모두|모든", question):
        return None

    def resolve(text: str) -> str | None:
        text = re.sub(r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일",
                      lambda m: f"{int(m[1]):04}-{int(m[2]):02}-{int(m[3]):02}", text)
        full = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", text)
        if len(set(full)) == 1:
            try:
                return date.fromisoformat(full[0]).isoformat()
            except ValueError:
                return None
        if full:
            return None
        if not trip_start:
            return None
        start = date.fromisoformat(trip_start)
        ordinal = re.search(r"(?<!\d)(\d{1,3})\s*일\s*차", text)
        number = int(ordinal.group(1)) if ordinal else None
        if number is None:
            for word, value in _ORDINAL_DAYS.items():
                if re.search(rf"{word}\s*날", text):
                    number = value
                    break
        if number is not None:
            return (start + timedelta(days=number - 1)).isoformat() if number >= 1 else None
        month_days = re.findall(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
        if len(set(month_days)) == 1:
            month, day = map(int, month_days[0])
            years = {start.year}
            end = date.fromisoformat(trip_end) if trip_end else None
            if end:
                years.update(range(start.year, end.year + 1))
            candidates = []
            for year in sorted(years):
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    continue
                if end is None or start <= candidate <= end:
                    candidates.append(candidate.isoformat())
            return candidates[0] if len(candidates) == 1 else None
        return None

    resolved = resolve(question)
    if resolved or not re.search(r"그날|그\s*날|당일", question):
        return resolved
    for message in reversed(history or []):
        content = message.get("content", "")
        if re.search(r"\d{4}-\d{2}-\d{2}|\d+\s*월\s*\d+\s*일|\d+\s*일\s*차|째\s*날|첫\s*날", content):
            return resolve(content)
    return None


resolve_daily_question_date = daily_question_date
