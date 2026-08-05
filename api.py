"""HTTP API 계층.

src/의 로직을 그대로 호출하고, web/의 정적 파일을 같은 프로세스에서 서빙한다.
한 출처에서 HTML과 JSON을 모두 내보내므로 CORS 설정이 필요 없다.
"""

from __future__ import annotations

import mimetypes
import re
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import EMAILS_DIR, PROJECT_ROOT, SEED_ON_EMPTY, SUPPORTED_EXTENSIONS

WEB_DIR = PROJECT_ROOT / "web"

# 브라우저는 서비스 워커가 JavaScript MIME 타입이 아니면 등록을 거부한다.
# 시스템 mimetypes DB가 .js를 text/plain으로 매핑하는 환경이 있어 직접 지정한다.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """기동 직후 인덱스가 비어 있으면 데모 예약으로 채운다.

    Render 무료 티어는 디스크가 영구 저장이 아니라 재시작하면 인덱스가 사라진다.
    서버 기동을 막지 않도록 별도 스레드에서 돌린다.
    """
    if SEED_ON_EMPTY:
        threading.Thread(target=_run_seeding, name="seed", daemon=True).start()
    yield


app = FastAPI(
    title="Travel Inbox RAG",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


# ---------------------------------------------------------------- 지연 로딩
# chromadb와 langchain은 로딩이 무겁다. 서버 기동을 막지 않도록 첫 요청에서 만든다.
_store = None
_agent = None

# FastAPI는 동기 엔드포인트를 스레드풀에서 실행한다. 콜드 스타트 중 요청이 겹치면
# 두 스레드가 동시에 초기화를 시도해 chromadb 내부 상태가 깨진다
# (KeyError in shared_system_client). 초기화 구간을 한 번만 통과시킨다.
_store_lock = threading.Lock()
_agent_lock = threading.Lock()

# 인덱싱은 한 번에 하나만 돈다. 기동 시 시딩과 사용자 업로드가 겹치면
# 같은 메일을 두 번 임베딩하게 된다.
_index_lock = threading.Lock()

# 인덱싱 작업 상태. 워커 1개를 전제로 프로세스 메모리에 둔다.
# 여러 워커로 늘리면 외부 저장소(Redis 등)로 옮겨야 한다.
_job_lock = threading.Lock()
_job: dict = {
    "state": "idle",          # idle | running | done | error
    "total": 0, "done": 0,
    "uploaded": [], "rejected": [],
    "indexed": [], "already_indexed": [],
    "total_chunks": 0, "error": None,
}


def store():
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:                # 락을 기다리는 동안 만들어졌을 수 있다
                from src.store import VectorStore

                _store = VectorStore()
    return _store


def agent():
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                from src import agent as agent_module

                agent_module.build_agent()
                _agent = agent_module
    return _agent


# ---------------------------------------------------------------- 스키마
class Message(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Message] = Field(default_factory=list, max_length=20)


class TripStart(BaseModel):
    date: str | None = None


# ---------------------------------------------------------------- 변환
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


# ---------------------------------------------------------------- 엔드포인트
@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/bookings")
def bookings() -> dict:
    records = store().all_reservations()
    items = [_to_booking(r) for r in records]
    dates = [i["date"] for i in items if i["date"]]
    ends = [i["date_end"] for i in items if i["date_end"]]
    return {
        "items": items,
        "count": len(items),
        "trip_start": min(dates) if dates else None,
        "trip_end": max(ends) if ends else None,
    }


# 에이전트를 거치면 LLM을 3번 부른다(도구 선택 → 도구 안의 답변 생성 → 최종 답변).
# 내 예약을 묻는 흔한 질문은 도구 라우팅이 필요 없으므로 곧바로 처리해 1번으로 줄인다.
_DAY_REF_RE = re.compile(r"(\d+\s*일차|첫째\s*날|둘째\s*날|셋째\s*날|넷째\s*날|다섯째\s*날|마지막\s*날)")
_GENERAL_RE = re.compile(
    r"(날씨|기온|환율|맛집|추천|근처|가는\s*법|교통|지하철|버스편|관광지|볼거리|팁|시차)"
)
_BOOKING_RE = re.compile(
    r"(예약|체크인|체크아웃|환불|취소|수수료|위약금|예약번호|확인번호|픽업|반납|"
    r"집합|출발|도착|탑승|숙소|호텔|료칸|항공|비행기|렌터카|투어|일정|몇\s*시|언제)"
)


def _can_answer_directly(question: str) -> bool:
    """에이전트 없이 예약 검색만으로 답할 수 있는 질문인지 본다.

    'N일차'는 날짜 계산 도구가, 날씨·환율 같은 일반 정보는 웹 검색이 필요하다.
    그 외에 예약을 가리키는 표현이 있으면 바로 처리한다.
    """
    if _DAY_REF_RE.search(question) or _GENERAL_RE.search(question):
        return False
    return bool(_BOOKING_RE.search(question))


@app.post("/api/ask")
def ask(body: AskRequest) -> dict:
    question = body.question

    # 빠른 경로. 근거를 못 찾으면 에이전트로 넘겨 다른 도구를 쓰게 한다.
    if not body.history and _can_answer_directly(question):
        from src.rag import answer_question

        try:
            fast = answer_question(question, store=store())
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        if fast["used_context"]:
            return {
                "answer": fast["answer"],
                "tools_used": ["search_bookings"],
                "sources": [
                    {
                        "source_file": hit["metadata"].get("source_file"),
                        "type": hit["metadata"].get("type"),
                        "provider": hit["metadata"].get("provider"),
                        "confirmation_number": hit["metadata"].get("confirmation_number"),
                        "similarity": hit["similarity"],
                    }
                    for hit in fast["hits"]
                ],
            }

    history = [{"role": m.role, "content": m.content} for m in body.history]
    try:
        result = agent().ask(question, history=history)
    except RuntimeError as error:  # API 키 누락 등 설정 문제
        raise HTTPException(status_code=503, detail=str(error)) from error

    return {
        "answer": result["answer"],
        "tools_used": result["tools_used"],
        "sources": result["sources"],
    }


def _run_seeding() -> None:
    """인덱스가 비어 있으면 시드로 채운다. 실패해도 서버는 계속 뜬다."""
    from src.seed import seed_if_empty

    try:
        with _index_lock:
            result = seed_if_empty(store=store())
    except Exception as error:  # 키 누락·네트워크 오류 등
        print(f"시드 실패: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        return

    if result["seeded"]:
        print(f"시드 완료: 청크 {result['total_chunks']}개", file=sys.stderr, flush=True)
    else:
        print(f"시드 건너뜀: {result['reason']}", file=sys.stderr, flush=True)


def _run_indexing(filenames: list[str]) -> None:
    """백그라운드에서 인덱싱을 수행하고 진행 상황을 기록한다."""
    from src.indexer import index_emails

    try:
        with _index_lock:
            summary = index_emails(store=store())
        with _job_lock:
            _job.update(
                state="done",
                done=len(summary["indexed"]),
                indexed=summary["indexed"],
                already_indexed=summary["skipped"],
                total_chunks=summary["total_in_store"],
                error=None,
            )
    except Exception as error:  # 키 누락·API 오류 등
        with _job_lock:
            _job.update(state="error", error=f"{type(error).__name__}: {error}")


@app.post("/api/index", status_code=202)
async def index(
    background: BackgroundTasks, files: list[UploadFile] = File(default=[])
) -> dict:
    """파일을 저장하고 인덱싱은 백그라운드로 넘긴다.

    메일 1건당 LLM 호출이 한 번 들어가므로 요청 안에서 처리하면 프록시
    제한 시간을 넘긴다(Render 무료 티어는 100초). 즉시 응답하고
    /api/index/status 로 진행 상황을 확인하게 한다.
    """
    with _job_lock:
        if _job["state"] == "running":
            raise HTTPException(status_code=409, detail="이미 인덱싱이 진행 중입니다.")

    EMAILS_DIR.mkdir(parents=True, exist_ok=True)

    saved, rejected = [], []
    for upload in files:
        # 업로드된 이름에 경로가 섞여 있어도 파일명만 취한다.
        name = Path(upload.filename or "").name
        if not name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            rejected.append(upload.filename)
            continue
        (EMAILS_DIR / name).write_bytes(await upload.read())
        saved.append(name)

    if not saved:
        return {"state": "done", "uploaded": [], "rejected": rejected,
                "indexed": [], "already_indexed": [], "total": 0}

    with _job_lock:
        _job.update(state="running", total=len(saved), done=0, uploaded=saved,
                    rejected=rejected, indexed=[], already_indexed=[], error=None)

    background.add_task(_run_indexing, saved)
    return {"state": "running", "uploaded": saved, "rejected": rejected,
            "total": len(saved)}


@app.get("/api/index/status")
def index_status() -> dict:
    with _job_lock:
        return dict(_job)


@app.delete("/api/index")
def clear_index(keep_files: bool = False) -> dict:
    """인덱스를 비운다. 기본적으로 업로드된 메일 파일도 함께 지운다.

    벡터만 지우면 원본이 남아 다음 인덱싱에서 되살아난다. 사용자가 "비우기"를
    눌렀는데 자기 메일이 다시 나타나는 것은 개인정보 관점에서도 문제다.
    """
    store().reset()

    removed = []
    if not keep_files and EMAILS_DIR.is_dir():
        for path in EMAILS_DIR.iterdir():
            if path.is_file() and path.name != ".gitkeep":
                path.unlink()
                removed.append(path.name)

    with _job_lock:
        _job.update(state="idle", total=0, done=0, uploaded=[], rejected=[],
                    indexed=[], already_indexed=[], total_chunks=0, error=None)

    return {"total_chunks": store().count(), "removed_files": removed}


@app.post("/api/trip-start")
def set_trip_start(body: TripStart) -> dict:
    agent().set_trip_start(body.date)
    return {"trip_start": body.date}


# ---------------------------------------------------------------- 정적 파일
# API 라우트를 먼저 등록한 뒤 마운트해야 /api/*가 가려지지 않는다.
if WEB_DIR.is_dir():
    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        # 서비스 워커는 루트 경로에서 제공해야 사이트 전체를 제어할 수 있다.
        return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
