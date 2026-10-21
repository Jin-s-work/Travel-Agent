"""예약 메일 raw_text에서 LLM으로 구조화 정보를 추출한다."""

from __future__ import annotations

import json
import os
import re
import hashlib
from datetime import date, datetime, time
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.config import (
    EXTRACTION_MODEL,
    OPENAI_API_KEY,
    RESERVATION_TYPES,
    SNIPPET_MAX_CHARS,
)
from src.prompts import load_prompt

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

# Moved verbatim to src/prompts/mail_single_extraction.md (2026-10-21).
SYSTEM_PROMPT = load_prompt("mail_single_extraction").body


def _chat(messages, schema, *, prompt, call_suffix=None, limits=None, model=EXTRACTION_MODEL):
    """One extraction call; adapters own the vendor response shape."""
    from src.llm.gateway import chat
    return chat("extract", model=model, messages=messages, schema=schema, client_factory=_client,
                prompt=prompt.meta(), operation="extract_repair" if call_suffix else "extract",
                call_suffix=call_suffix, limits=limits)


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. .env.example을 참고해 .env를 만드세요."
        )
    return OpenAI(api_key=OPENAI_API_KEY, max_retries=0, timeout=45.0)


def parse_reservation(raw_text: str, model: str = EXTRACTION_MODEL) -> dict:
    """예약 메일 한 통에서 구조화된 예약 정보를 추출한다.

    반환값은 FIELDS의 키를 모두 가지며, 추출 실패한 필드는 None이다.
    """
    if not raw_text or not raw_text.strip():
        return {field: None for field in FIELDS}

    result = _chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"다음 메일에서 예약 정보를 추출해줘.\n\n{raw_text}"},
        ],
        ("reservation", RESERVATION_SCHEMA),
        prompt=load_prompt("mail_single_extraction"),
        model=model,
    )
    parsed = _normalize(json.loads(result.text))
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


# This is separate from the single-reservation CLI/seed interface above. A
# document is one source, not one booking; one flight booking can have many legs.
_DOCUMENT_FIELDS = (
    "kind", "provider", "confirmation_number", "date", "date_end", "time",
    "time_end", "location", "refund_policy", "raw_snippet", "events",
)
_EVENT_FIELDS = (
    "event_type", "start_local", "end_local", "start_timezone",
    "end_timezone", "location",
)
_EVENT_TYPES = ("flight", "stay", "checkin", "checkout", "pickup", "return", "activity")


def _nullable(description: str) -> dict:
    return {"type": ["string", "null"], "description": description}


DOCUMENT_RESERVATIONS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reservations"],
    "properties": {
        "reservations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(_DOCUMENT_FIELDS),
                "properties": {
                    "kind": {"type": ["string", "null"], "enum": [*RESERVATION_TYPES, None]},
                    "provider": _nullable("실제 서비스 제공자. 예약 중개 사이트와 구분한다."),
                    "confirmation_number": _nullable("원문 예약번호. 없으면 null."),
                    "date": _nullable("첫 이용 날짜 YYYY-MM-DD. 연도가 없으면 null."),
                    "date_end": _nullable("마지막 이용 날짜 YYYY-MM-DD. 불명이면 null."),
                    "time": _nullable("첫 이용 시작 시각 HH:MM. 불명이면 null."),
                    "time_end": _nullable("마지막 이용 종료 시각 HH:MM. 불명이면 null."),
                    "location": _nullable("원문의 장소명·주소 또는 공항 구간."),
                    "refund_policy": _nullable("해당 예약의 취소/환불 규정. 불명이면 null."),
                    "raw_snippet": _nullable(f"연속된 원문 발췌, 최대 {SNIPPET_MAX_CHARS}자. 요약 금지."),
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(_EVENT_FIELDS),
                            "properties": {
                                "event_type": {"type": "string", "enum": list(_EVENT_TYPES)},
                                "start_local": _nullable("원문 시작 현지 날짜 YYYY-MM-DD 또는 날짜와 시각 YYYY-MM-DDTHH:MM. 날짜만 있으면 시각을 만들지 않는다."),
                                "end_local": _nullable("원문 종료 현지 날짜 또는 날짜와 시각. 불명이면 null."),
                                "start_timezone": _nullable("명시된 시작 IANA 시간대. 도시/공항 이름으로 추정하지 말고 불명이면 null."),
                                "end_timezone": _nullable("명시된 도착 IANA 시간대. 시작 시간대를 복사하거나 추측하지 않는다."),
                                "location": _nullable("이 이벤트의 장소 또는 구간."),
                            },
                        },
                    },
                },
            },
        },
    },
}

# Moved verbatim to src/prompts/mail_document_extraction.md (2026-10-21).
DOCUMENT_SYSTEM_PROMPT = load_prompt("mail_document_extraction").body


def _exact_keys(value: object, keys: tuple[str, ...], field: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{field}: 추출 스키마와 다른 필드입니다.")
    return value


def _string_or_none(value: object, field: str, limit: int = 4000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field}: 올바른 문자열이 아닙니다.")
    return value.strip() or None


def _iso_day(value: str | None, field: str) -> str | None:
    if value is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{field}: YYYY-MM-DD 형식이 필요합니다.")
        date.fromisoformat(value)
    return value


def _local_value(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if len(value) == 10:
        return _iso_day(value, field)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?", value):
        raise ValueError(f"{field}: 현지 날짜/시각 형식이 올바르지 않습니다.")
    datetime.fromisoformat(value)
    return value


def validate_document_reservations(payload: object, raw_text: str) -> list[dict]:
    """Validate provider JSON before any DB/index activation; never repair by guessing.

    Status remains needs_review: an extraction is not proof of confirmation.
    stable_item_key is a reconciliation hint, never a primary ID or permission.
    Duplicate hints are deliberately retained so the repository can mark ambiguity.
    """
    envelope = _exact_keys(payload, ("reservations",), "document")
    values = envelope["reservations"]
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("reservations: 예약 배열은 최대 100개입니다.")
    result = []
    for item in values:
        _exact_keys(item, _DOCUMENT_FIELDS, "reservation")
        parsed = {key: _string_or_none(item[key], key) for key in _DOCUMENT_FIELDS if key != "events"}
        if parsed["kind"] not in (*RESERVATION_TYPES, None):
            raise ValueError("kind: 지원하지 않는 예약 종류입니다.")
        for key in ("date", "date_end"):
            parsed[key] = _iso_day(parsed[key], key)
        if parsed["date"] and parsed["date_end"] and parsed["date"] > parsed["date_end"]:
            raise ValueError("date_end: 시작 날짜보다 앞섭니다.")
        for key in ("time", "time_end"):
            value = parsed[key]
            if value is not None:
                if not re.fullmatch(r"\d{2}:\d{2}", value):
                    raise ValueError(f"{key}: HH:MM 형식이 필요합니다.")
                time.fromisoformat(value)
        snippet = parsed["raw_snippet"]
        if snippet and (len(snippet) > SNIPPET_MAX_CHARS or snippet not in raw_text):
            raise ValueError("raw_snippet: 실제 원문의 연속된 발췌가 아닙니다.")
        if not isinstance(item["events"], list) or len(item["events"]) > 32:
            raise ValueError("events: 이벤트 배열은 최대 32개입니다.")
        events = []
        for event in item["events"]:
            _exact_keys(event, _EVENT_FIELDS, "event")
            clean = {key: _string_or_none(event[key], key) for key in _EVENT_FIELDS}
            if clean["event_type"] not in _EVENT_TYPES:
                raise ValueError("event_type: 지원하지 않는 이벤트 종류입니다.")
            for key in ("start_local", "end_local"):
                clean[key] = _local_value(clean[key], key)
            for key in ("start_timezone", "end_timezone"):
                if clean[key] is not None:
                    try:
                        ZoneInfo(clean[key])
                    except (ZoneInfoNotFoundError, ValueError) as error:
                        raise ValueError(f"{key}: IANA 시간대가 아닙니다.") from error
            events.append(clean)
        parsed["events"] = events
        parsed["type"] = parsed["kind"]  # Existing search-text and metadata adapters.
        parsed["status"] = "needs_review"
        identity = {
            key: parsed[key] for key in ("kind", "provider", "confirmation_number", "location")
        }
        # Confirmation numbers commonly survive changes to dates/times. Without
        # one, a date/location key remains only a conservative matching hint.
        if not parsed["confirmation_number"]:
            identity["date"] = parsed["date"]
        identity["event_locations"] = [event["location"] for event in events]
        parsed["stable_item_key"] = hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        result.append(parsed)
    return result


class ExtractionFailed(ValueError):
    """The model's document output stayed unusable after at most one repair.

    `errors` are validator messages only (never mail text). `kind` is
    'incomplete' (length/refusal/empty), 'invalid' (schema/validation) or both.
    """
    def __init__(self, message, *, errors=(), repair_attempts=0, kind="invalid", calls=()):
        super().__init__(message)
        self.errors = list(errors)
        self.repair_attempts = repair_attempts
        self.kind = kind
        self.calls = list(calls)


def document_errors(payload: object, raw_text: str, limit: int = 20) -> list[str]:
    """Every validation problem, per reservation index, for the repair request."""
    if not isinstance(payload, dict) or set(payload) != {"reservations"}:
        return ["document: 최상위는 reservations 하나만 가진 객체여야 합니다."]
    items = payload["reservations"]
    if not isinstance(items, list) or len(items) > 100:
        return ["reservations: 예약 배열은 최대 100개입니다."]
    errors = []
    for index, item in enumerate(items):
        try:
            validate_document_reservations({"reservations": [item]}, raw_text)
        except ValueError as error:
            errors.append(f"reservations[{index}].{error}")
        if len(errors) >= limit:
            break
    return errors


def _repair_cap(value: str | None, default: int = 4096) -> int:
    """MAIL_REPAIR_MAX_COMPLETION_TOKENS; a bad value falls back instead of breaking import.

    The default equals the usual per-SKU output bound on purpose: a repair must
    re-emit the whole document JSON, so a lower cap would truncate exactly the
    long mails that need repair. The gateway still clamps it to the policy bound.
    """
    try:
        parsed = int(str(value).strip()) if value is not None else default
    except ValueError:
        return default
    return parsed if parsed > 0 else default


REPAIR_MAX_COMPLETION_TOKENS = _repair_cap(os.getenv("MAIL_REPAIR_MAX_COMPLETION_TOKENS"))
_PREVIOUS_OUTPUT_CHARS = 16000


def _check_document(result, raw_text):
    """Return (bookings, None) or (None, (kind, message, errors))."""
    if result.finish != "stop" or result.refusal:
        return None, ("incomplete", "예약 추출이 완료되지 않았습니다.", [f"finish={result.finish}"])
    if not result.text:
        return None, ("invalid", "예약 추출 결과가 비어 있습니다.", ["document: 응답이 비어 있습니다."])
    try:
        payload = json.loads(result.text)
    except (TypeError, ValueError):
        return None, ("invalid", "예약 추출 결과가 JSON이 아닙니다.", ["document: JSON으로 읽을 수 없습니다."])
    try:
        return validate_document_reservations(payload, raw_text), None
    except ValueError as error:
        return None, ("invalid", str(error), document_errors(payload, raw_text) or [str(error)])


def extract_document_reservations(raw_text: str, model: str = EXTRACTION_MODEL, *, repair: bool | None = None) -> dict:
    """Extract all bookings; on a schema/validation failure, repair exactly once.

    Repair is a separately reserved call (own call key and output cap). A
    truncated or refused answer is not repaired: the same cap would truncate
    again and a refusal is not a format problem. Returns bookings plus the
    metadata recorded on results and telemetry.
    """
    from src.llm.gateway import annotate
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("추출할 메일 본문이 없습니다.")
    prompt = load_prompt("mail_document_extraction")
    messages = [{"role": "system", "content": DOCUMENT_SYSTEM_PROMPT}, {"role": "user", "content": raw_text}]
    schema = ("document_reservations", DOCUMENT_RESERVATIONS_SCHEMA)
    first = _chat(messages, schema, prompt=prompt, model=model)
    bookings, failure = _check_document(first, raw_text)
    calls = [first.meta]
    if failure is None:
        annotate(first.meta, schema_valid=True, outcome="ok")
        return {"bookings": bookings, "repair_attempts": 0, "prompt": prompt.meta(), "calls": calls}
    annotate(first.meta, schema_valid=False, outcome="schema_invalid" if failure[0] == "invalid" else "incomplete")
    from src.reliability.providers import has_metering
    if repair is None:
        repair = has_metering()  # The unmetered legacy CLI branch never pays for a second call.
    if failure[0] != "invalid" or not repair:
        raise ExtractionFailed(failure[1], errors=failure[2], repair_attempts=0, kind=failure[0], calls=calls)
    repair_prompt = load_prompt("mail_extraction_repair")
    repair_messages = list(messages)
    if first.text:
        repair_messages.append({"role": "assistant", "content": first.text[:_PREVIOUS_OUTPUT_CHARS]})
    repair_messages.append({"role": "user", "content": repair_prompt.render(
        ERRORS="\n".join(f"- {line}" for line in failure[2]))})
    from src.llm import Limits
    from src.foundation.repository import DomainError
    try:
        second = _chat(repair_messages, schema, prompt=repair_prompt, call_suffix="repair", model=model,
                       limits=Limits(max_output_tokens=REPAIR_MAX_COMPLETION_TOKENS))
    except DomainError as error:
        error.repair_attempts = 1  # The paid repair was attempted; results record it.
        raise
    calls.append(second.meta)
    bookings, repaired_failure = _check_document(second, raw_text)
    if repaired_failure is None:
        annotate(second.meta, schema_valid=True, repair_attempts=1, outcome="repaired")
        return {"bookings": bookings, "repair_attempts": 1, "prompt": prompt.meta(),
                "repair_prompt": repair_prompt.meta(), "calls": calls}
    annotate(second.meta, schema_valid=False, repair_attempts=1, outcome="repair_failed")
    # Message, errors and kind all describe the final (repair) answer.
    raise ExtractionFailed(repaired_failure[1], errors=repaired_failure[2], repair_attempts=1,
                           kind=repaired_failure[0], calls=calls)


def parse_document_reservations(raw_text: str, model: str = EXTRACTION_MODEL) -> list[dict]:
    """Extract all bookings, preserving return legs; no live call is made by tests."""
    return extract_document_reservations(raw_text, model)["bookings"]


if __name__ == "__main__":
    import sys

    from src.loader import read_email_file

    if len(sys.argv) != 2:
        sys.exit("사용법: python -m src.parser <메일파일경로>")

    parsed = parse_reservation(read_email_file(sys.argv[1]))
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
