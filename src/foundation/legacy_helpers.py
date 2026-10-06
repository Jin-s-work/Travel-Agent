"""Pure display/routing helpers retained for CLI/regression compatibility."""
import re

_POLICY_RE = re.compile(r"환불 및 취소 규정:\s*(.+?)(?:\n메일 원문 발췌:|$)", re.S)

def _policy_lines(document: str) -> list[str]:
    """검색용 문서에서 환불 규정 부분만 잘라 줄 단위로 나눈다.

    규정 전문은 메타데이터가 아니라 청크 본문에 들어 있다. 재인덱싱 없이
    상세 화면에 보여주려고 여기서 뽑는다.
    """
    found = _POLICY_RE.search(document or "")
    if not found:
        return []
    lines = [line.strip(" -·\t") for line in found.group(1).splitlines()]
    return [line for line in lines if line and not line.startswith("[")]

def _to_booking(record: dict) -> dict:
    """벡터 스토어 메타데이터를 UI가 쓰는 형태로 바꾼다."""
    time_value = record.get("time") or ""
    start_time, _, end_time = (part.strip() for part in time_value.partition("~"))

    return {
        "id": record.get("source_file", ""),
        "kind": record.get("type"),
        "provider": record.get("provider"),
        "confirmation_number": record.get("confirmation_number"),
        "date": record.get("date"),
        "date_end": record.get("date_end") or record.get("date"),
        "time": start_time or None,
        "time_end": end_time or None,
        "location": record.get("location"),
        "source_file": record.get("source_file"),
        "policy": _policy_lines(record.get("document", "")),
    }

_DAY_REF_RE = re.compile(r"(\d+\s*일차|첫째\s*날|둘째\s*날|셋째\s*날|넷째\s*날|다섯째\s*날|마지막\s*날)")

_GENERAL_RE = re.compile(
    r"(날씨|기온|환율|맛집|추천|근처|가는\s*법|교통|지하철|버스편|관광지|볼거리|팁|시차)"
)

_BOOKING_RE = re.compile(
    r"(예약|체크인|체크아웃|환불|취소|수수료|위약금|예약번호|확인번호|픽업|반납|"
    r"집합|출발|도착|탑승|숙소|호텔|료칸|항공|비행기|렌터카|투어|일정|몇\s*시|언제)"
)

_REFERS_BACK_RE = re.compile(
    r"(그것|그거|그건|그게|그때|그날|그\s*예약|그\s*호텔|그\s*항공|그\s*투어|"
    r"거기|저기|아까|방금|앞서|이전에|위에서|말한|같은\s*거)"
)

def _can_answer_directly(question: str) -> bool:
    """에이전트 없이 예약 검색만으로 답할 수 있는 질문인지 본다.

    'N일차'는 날짜 계산 도구가, 날씨·환율 같은 일반 정보는 웹 검색이 필요하다.
    "그거 환불돼?"처럼 앞 대화를 가리키면 맥락이 필요하다.
    그 외에 예약을 가리키는 표현이 있으면 바로 처리한다.
    """
    if _DAY_REF_RE.search(question) or _GENERAL_RE.search(question):
        return False
    if _REFERS_BACK_RE.search(question):
        return False
    return bool(_BOOKING_RE.search(question))
