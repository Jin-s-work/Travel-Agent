#!/usr/bin/env bash
# Hugging Face Spaces로 배포한다.
#
#   hf auth login          # 먼저 한 번만
#   ./deploy/push_to_spaces.sh <owner>/<space-name>
#
# Space 저장소에는 앱 실행에 필요한 파일만 올린다. 평가셋·테스트·문서는 제외한다.

set -euo pipefail

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  echo "사용법: $0 <owner>/<space-name>" >&2
  echo "예:    $0 Jin-s-work/travel-inbox-rag" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- 안전장치: 키가 섞여 들어가지 않는지 확인 -----------------------------
if [[ -f .env ]] && git check-ignore -q .env; then
  echo "OK  .env는 git이 무시합니다."
else
  echo "중단: .env가 추적 대상입니다. .gitignore를 먼저 고치세요." >&2
  exit 1
fi

# --- 인증 확인 -------------------------------------------------------------
# 토큰은 .env의 HF_TOKEN 또는 `hf auth login`으로 저장된 값을 쓴다.
# 어느 쪽이든 이 스크립트가 값을 출력하지 않는다.
PY_BIN="${PY_BIN:-./.venv/bin/python}"

if ! "$PY_BIN" - <<'PY'
from dotenv import load_dotenv
load_dotenv(".env")
from huggingface_hub import whoami
print("로그인:", whoami()["name"])
PY
then
  echo "중단: Hugging Face 인증을 찾을 수 없습니다." >&2
  echo "      .env에 HF_TOKEN=hf_... 을 추가하거나 hf auth login 을 실행하세요." >&2
  exit 1
fi

# --- Space 생성 (이미 있으면 통과) -----------------------------------------
"$PY_BIN" - "$TARGET" <<'PY'
import sys
from huggingface_hub import HfApi
from dotenv import load_dotenv
load_dotenv(".env")
api = HfApi()
repo = sys.argv[1]
api.create_repo(repo_id=repo, repo_type="space", space_sdk="docker", exist_ok=True)
print(f"Space 준비됨: https://huggingface.co/spaces/{repo}")
PY

# --- 업로드 ----------------------------------------------------------------
# README.md는 Spaces용(frontmatter 포함)으로 바꿔 올린다.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp Dockerfile requirements.txt api.py "$STAGE/"
cp -R src web "$STAGE/"
cp deploy/README_SPACES.md "$STAGE/README.md"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +

"$PY_BIN" - "$TARGET" "$STAGE" <<'PY'
import sys
from huggingface_hub import HfApi
repo, folder = sys.argv[1], sys.argv[2]
from dotenv import load_dotenv
load_dotenv(".env")
HfApi().upload_folder(
    repo_id=repo, repo_type="space", folder_path=folder,
    commit_message="Travel Inbox RAG 배포",
)
print(f"업로드 완료: https://huggingface.co/spaces/{repo}")
PY

cat <<'EOS'

남은 단계 (웹에서 직접):
  Settings → Variables and secrets 에서 Secret 등록
    OPENAI_API_KEY   (필수)
    TAVILY_API_KEY   (선택)
  * Variables가 아니라 Secrets로 등록해야 로그에 찍히지 않습니다.

등록 후 Space가 자동으로 다시 빌드됩니다.
EOS
