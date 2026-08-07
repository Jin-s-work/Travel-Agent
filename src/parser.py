"""예약 메일 raw_text에서 LLM으로 구조화 정보를 추출한다."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from src.config import (
    EXTRACTION_MODEL,
    OPENAI_API_KEY,
    RESERVATION_TYPES,
    SNIPPET_MAX_CHARS,
)

FIELDS = (
    "type",
    "provider",
    "confirmation_number",
    "date",
    "time",
    "location",
    "refund_policy",
    "raw_snippet",
)

# strict 모드는 모든 필드를 required로 요구하므로, "없음"은 null로 표현한다.
RESERVATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": list(FIELDS),
    "properties": {
        "type": {
            "type": ["string", "null"],
            "enum": [*RESERVATION_TYPES, None],
            "description": (
                "예약 종류. 판정 기준은 '무엇을 예약했는가'다.\n"
                "- 항공: 항공권·좌석. 항공사가 운항하는 여객편.\n"
                "- 숙소: 호텔·료칸·게스트하우스 등 숙박.\n"
                "- 렌터카: 본인이 직접 운전할 차량을 빌리는 것만 해당한다.\n"
                "- 투어: 가이드·기사가 동행하는 관광 상품. 왕복 전세버스나 "
                "차량 이동이 포함돼 있어도 상품 자체가 관광이면 투어다.\n"
                "넷 중 어디에도 해당하지 않으면 null."
            ),
        },
        "provider": {
            "type": ["string", "null"],
            "description": (
                "실제로 서비스를 제공하는 곳의 이름. 항공사·호텔·렌터카 업체·투어 운영사다.\n"
                "예약을 중개한 사이트(Booking.com, Expedia, Klook, 아고다 등)나 메일 발신자가 "
                "아니다. 발신자가 중개 사이트여도 본문에서 실제 숙소·항공사·운영사 이름을 찾아 쓴다.\n"
                "예: 발신자가 Booking.com이고 본문에 'Hotel Gracery Shinjuku'가 있으면 "
                "provider는 'Hotel Gracery Shinjuku'다."
            ),
        },
        "confirmation_number": {
            "type": ["string", "null"],
            "description": "예약번호·확인번호·PNR. 여러 개면 대표 확인번호 하나.",
        },
        "date": {
            "type": ["string", "null"],
            "description": (
                "이용 날짜 YYYY-MM-DD. 기간이면 'YYYY-MM-DD ~ YYYY-MM-DD'. "
                "연도가 본문에 없으면 추측하지 말고 null."
            ),
        },
        "time": {
            "type": ["string", "null"],
            "description": (
                "24시간제 HH:MM만 사용하고 설명 문구는 넣지 않는다. 종류별로 "
                "항공은 '출발 ~ 도착', 숙소는 '체크인 ~ 체크아웃', "
                "렌터카는 '픽업 ~ 반납', 투어는 집합 시각 하나. "
                "'15:00-24:00'처럼 체크인 가능 시간대가 주어지면 시작 시각만 쓰고, "
                "'11:00까지'는 11:00으로 쓴다. 예: '15:00 ~ 11:00'."
            ),
        },
        "location": {
            "type": ["string", "null"],
            "description": (
                "장소 이름과 주소, 또는 공항 코드 구간. "
                "전화번호·교통편 안내·부가 설명은 넣지 않는다."
            ),
        },
        "refund_policy": {
            "type": ["string", "null"],
            "description": "취소·환불·변경 수수료 규정을 원문 기준으로 요약.",
        },
        "raw_snippet": {
            "type": ["string", "null"],
            "description": (
                "위 정보의 근거가 되는 원문 발췌. 요약하지 말고 원문 그대로, "
                f"{SNIPPET_MAX_CHARS}자 이내."
            ),
        },
    },
}

SYSTEM_PROMPT = (
    "너는 여행 예약 확인 메일에서 정보를 추출하는 도구다.\n"
    "규칙:\n"
    "1. 메일 본문에 명시된 내용만 사용한다. 추론·보완·창작 금지.\n"
    "2. 확실하지 않거나 본문에 없는 필드는 반드시 null로 둔다.\n"
    "3. 값은 원문의 표기를 유지한다(예약번호 대소문자 등).\n"
    "4. 한 메일에 여러 구간이 있으면 가장 처음 이용하는 구간을 기준으로 채운다."
)


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. .env.example을 참고해 .env를 만드세요."
        )
    return OpenAI(api_key=OPENAI_API_KEY)


def parse_reservation(raw_text: str, model: str = EXTRACTION_MODEL) -> dict:
    """예약 메일 한 통에서 구조화된 예약 정보를 추출한다.

    반환값은 FIELDS의 키를 모두 가지며, 추출 실패한 필드는 None이다.
    """
    if not raw_text or not raw_text.strip():
        return {field: None for field in FIELDS}

    response = _client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"다음 메일에서 예약 정보를 추출해줘.\n\n{raw_text}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "reservation",
                "strict": True,
                "schema": RESERVATION_SCHEMA,
            },
        },
    )
    parsed = _normalize(json.loads(response.choices[0].message.content))
    parsed = _correct_type(parsed, raw_text)
    # 시각 정리 규칙이 종류에 따라 다르므로 종류가 확정된 뒤 한 번 더 손본다.
    parsed["time"] = _coerce_time(parsed["time"], parsed["type"])
    return _restore_time_range(parsed, raw_text)


# \b는 '11:00까지'처럼 뒤에 한글이 붙으면 경계로 잡히지 않으므로 숫자 룩어라운드를 쓴다.
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-4]):([0-5]\d)(?!\d)")


def _coerce_time(value: str | None, reservation_type: str | None = None) -> str | None:
    """time 값을 'HH:MM' 또는 'HH:MM ~ HH:MM'으로 강제한다.

    스키마 설명만으로는 모델이 원문을 그대로 옮겨 적는 경우가 있어
    (예: '체크인 ... 15:00 - 24:00; 체크아웃 ... 11:00까지'),
    HH:MM 토큰만 뽑아 처음과 마지막을 시작/종료로 재구성한다.

    투어는 집합 시각 하나만 쓴다. '집합 07:40 (출발 08:00)'처럼 시각이 둘
    적힌 메일에서 모델이 둘 다 담아 '07:40 ~ 08:00'이 되면, 화면에 투어가
    08:00에 끝나는 것처럼 보인다. 같은 형식의 다른 투어 메일은 하나만
    담아 결과가 메일마다 달라지기도 했다.
    """
    if not value:
        return None
    times = [f"{int(hour):02d}:{minute}" for hour, minute in _TIME_RE.findall(value)]
    if not times:
        return None
    if len(times) == 1 or reservation_type == "투어":
        return times[0]
    return f"{times[0]} ~ {times[-1]}"


# 원문에서 예약 종류를 가리키는 신호. LLM 분류가 흔들릴 때 바로잡는 데 쓴다
# (같은 메일이 실행마다 '투어'와 '항공'을 오가는 일이 실제로 있었다).
# 각 그룹은 서로 겹치지 않는 표현으로만 구성한다.
_TYPE_SIGNALS: dict[str, tuple[re.Pattern, ...]] = {
    "항공": (
        re.compile(r"\b[A-Z]{2}\s?\d{2,4}\b"),          # 편명 KE703, NH 867
        re.compile(r"e-?ticket|항공권|탑승|boarding|PNR", re.I),
        re.compile(r"\b(ICN|NRT|HND|GMP|KIX|CTS|FUK)\b"),
        re.compile(r"위탁\s*수하물|checked\s*baggage", re.I),
    ),
    "숙소": (
        re.compile(r"체크인|check-?in", re.I),
        re.compile(r"체크아웃|check-?out", re.I),
        re.compile(r"\d\s*박|\d\s*nights?\b", re.I),
        re.compile(r"객실|room\s*type|숙박세", re.I),
    ),
    "렌터카": (
        re.compile(r"렌터카|렌트카|car\s*rental", re.I),
        re.compile(r"픽업.*반납|반납.*픽업", re.S),
        re.compile(r"면책보상|CDW|국제운전면허", re.I),
        re.compile(r"차종|만탱크|주행거리", re.I),
    ),
    "투어": (
        re.compile(r"투어|tour\b", re.I),
        re.compile(r"집합\s*(시간|장소)"),
        re.compile(r"가이드|guide\b", re.I),
        re.compile(r"바우처|voucher|액티비티", re.I),
    ),
}


def infer_type(raw_text: str) -> tuple[str | None, int]:
    """원문에서 예약 종류를 추정한다. (종류, 일치한 신호 수)를 반환한다.

    1위가 단독이 아니면 판단을 보류한다. 동점을 사전 순으로 깨면 근거 없이
    한쪽 종류로 쏠린다.
    """
    scores = sorted(
        (
            (sum(1 for pattern in patterns if pattern.search(raw_text)), kind)
            for kind, patterns in _TYPE_SIGNALS.items()
        ),
        reverse=True,
    )
    (top, kind), (runner_up, _) = scores[0], scores[1]
    return (kind, top) if top > runner_up else (None, 0)


# 종류별로 시작·끝을 가리키는 줄 라벨. 시각이 둘 필요한 종류만 넣는다.
# (투어는 집합 시각 하나만 쓰므로 여기 없다.)
_RANGE_LABELS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "항공": (("출발", "departure", "depart"), ("도착", "arrival", "arrive")),
    "숙소": (("체크인", "check-in", "check in", "checkin"),
             ("체크아웃", "check-out", "check out", "checkout")),
    "렌터카": (("픽업", "대여", "pick-up", "pickup"), ("반납", "return")),
}


def _labelled_time(raw_text: str, keywords: tuple[str, ...]) -> str | None:
    """라벨이 붙은 줄에서 첫 HH:MM을 찾는다. 시각이 없는 줄은 건너뛴다."""
    for line in raw_text.splitlines():
        lowered = line.lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        found = _TIME_RE.search(line)
        if found:
            return f"{int(found.group(1)):02d}:{found.group(2)}"
    return None


def _restore_time_range(parsed: dict, raw_text: str) -> dict:
    """시각을 하나만 뽑았을 때 원문에서 나머지 한쪽을 찾아 채운다.

    ANA 귀국편이 어떤 실행에서는 '18:55 ~ 21:35', 다른 실행에서는 '18:55'로
    나왔다. 원문에는 Departure와 Arrival이 모두 적혀 있다.

    규칙이 찾은 시작 시각이 모델이 준 값과 같을 때만 채운다. 서로 다르면
    규칙이 엉뚱한 줄을 본 것일 수 있어 모델 판단을 그대로 둔다.
    """
    labels = _RANGE_LABELS.get(parsed.get("type"))
    value = parsed.get("time")
    if not labels or not value or "~" in value:
        return parsed

    start = _labelled_time(raw_text, labels[0])
    end = _labelled_time(raw_text, labels[1])
    if start and end and start == value and end != start:
        parsed["time"] = f"{start} ~ {end}"
    return parsed


def _correct_type(parsed: dict, raw_text: str) -> dict:
    """LLM이 매긴 종류를 원문 신호와 대조해 필요하면 바로잡는다.

    신호가 2개 이상 잡히고 LLM 결과와 다를 때만 덮어쓴다. 하나만 걸리는 것은
    지나가는 단어일 수 있어 근거로 삼지 않는다.
    """
    inferred, score = infer_type(raw_text)
    if not inferred:
        return parsed
    if parsed.get("type") is None or (score >= 2 and parsed["type"] != inferred):
        parsed["type"] = inferred
    return parsed


def _normalize(data: dict) -> dict:
    """누락 키를 채우고, 빈 문자열을 null로 바꾸고, 형식을 강제한다."""
    result = {}
    for field in FIELDS:
        value = data.get(field)
        if isinstance(value, str):
            value = value.strip() or None
        result[field] = value

    if result["type"] not in RESERVATION_TYPES:
        result["type"] = None
    result["time"] = _coerce_time(result["time"])
    snippet = result["raw_snippet"]
    if snippet and len(snippet) > SNIPPET_MAX_CHARS:
        result["raw_snippet"] = snippet[:SNIPPET_MAX_CHARS].rstrip() + "…"
    return result


if __name__ == "__main__":
    import sys

    from src.loader import read_email_file

    if len(sys.argv) != 2:
        sys.exit("사용법: python -m src.parser <메일파일경로>")

    parsed = parse_reservation(read_email_file(sys.argv[1]))
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
