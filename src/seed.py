"""인덱스가 비어 있을 때 데모 예약으로 자동으로 채운다.

배포 환경(Render 무료 티어)은 디스크가 영구 저장이 아니고 메모리가 512MB라
재시작이 잦다. 재시작할 때마다 인덱스가 사라져 데모 링크가 빈 화면이 됐다.

추출 결과를 seed/parsed.json에 넣어 두고 기동 시 그대로 읽어 쓴다. LLM 파싱을
건너뛰므로 비용이 들지 않고, 남는 작업은 임베딩뿐이라 몇 초 만에 끝난다.

시드 갱신:
    python -m src.seed --build
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from src.config import EMAILS_DIR, SEED_FILE, SEED_SOURCE_DIRS
from src.indexer import _hash, index_emails
from src.loader import load_emails
from src.store import VectorStore


def load_seed() -> dict[str, dict]:
    """시드 파일을 content_hash → 추출 결과로 읽는다. 없으면 빈 dict."""
    if not SEED_FILE.is_file():
        return {}
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    return {item["content_hash"]: item["parsed"] for item in data.get("emails", [])}


def seed_if_empty(store: VectorStore | None = None) -> dict:
    """인덱스가 비어 있으면 시드로 채운다. 이미 데이터가 있으면 건드리지 않는다.

    사용자가 올린 메일이 하나라도 있으면 인덱스가 비어 있지 않으므로, 이 함수가
    사용자 데이터를 덮어쓰는 일은 없다.
    """
    store = store or VectorStore()

    if store.count() > 0:
        return {"seeded": False, "reason": "인덱스에 이미 데이터가 있습니다."}

    parse_cache = load_seed()
    if not parse_cache:
        return {"seeded": False, "reason": f"시드 파일이 없습니다: {SEED_FILE}"}

    copied = _copy_demo_emails()
    if not copied:
        # 배포 이미지에 데모 메일을 넣는 것을 빠뜨리면 여기에 걸린다.
        # 조용히 넘어가면 데모 링크가 빈 화면이 된 이유를 찾기 어렵다.
        return {
            "seeded": False,
            "reason": (
                "시드는 있는데 원본 메일을 찾을 수 없습니다: "
                f"{', '.join(str(d) for d in SEED_SOURCE_DIRS)}"
            ),
        }

    summary = index_emails(store=store, parse_cache=parse_cache)

    # 시드에 없는 메일이 섞여 있으면 LLM 파싱이 일어난다. 그런 경우를 드러낸다.
    unparsed = [name for name in summary["indexed"] if name not in copied]

    return {
        "seeded": True,
        "copied": copied,
        "indexed": summary["indexed"],
        "unexpected": unparsed,
        "total_chunks": summary["total_in_store"],
    }


def _copy_demo_emails() -> list[str]:
    """데모 메일을 업로드 디렉터리로 복사한다. 이미 있는 파일은 두고 넘어간다."""
    EMAILS_DIR.mkdir(parents=True, exist_ok=True)

    copied = []
    for directory in SEED_SOURCE_DIRS:
        if not Path(directory).is_dir():
            continue
        for path in sorted(Path(directory).iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            target = EMAILS_DIR / path.name
            if not target.exists():
                shutil.copy2(path, target)
            copied.append(path.name)
    return copied


def build_seed() -> dict:
    """데모 메일을 LLM으로 파싱해 시드 파일을 새로 쓴다.

    이 함수만 LLM을 호출한다. 메일이나 파서를 고쳤을 때 다시 실행한다.
    """
    from src.parser import parse_reservation

    entries = []
    for directory in SEED_SOURCE_DIRS:
        if not Path(directory).is_dir():
            continue
        for email in load_emails(directory):
            print(f"  파싱 중: {email['filename']}", file=sys.stderr, flush=True)
            entries.append({
                "filename": email["filename"],
                "content_hash": _hash(email["raw_text"]),
                "parsed": parse_reservation(email["raw_text"]),
            })

    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEED_FILE.write_text(
        json.dumps({"emails": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"count": len(entries), "path": str(SEED_FILE)}


if __name__ == "__main__":
    if "--build" in sys.argv[1:]:
        result = build_seed()
        print(f"시드 {result['count']}건을 {result['path']}에 저장했습니다.")
        for item in json.loads(SEED_FILE.read_text(encoding="utf-8"))["emails"]:
            parsed = item["parsed"]
            print(f"  {parsed.get('date'):<24} {parsed.get('type'):<4} | {parsed.get('provider')}")
    else:
        print(json.dumps(seed_if_empty(), ensure_ascii=False, indent=2))
