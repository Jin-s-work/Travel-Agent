"""프로젝트 전역 설정값."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- 경로 ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EMAILS_DIR = PROJECT_ROOT / "data" / "emails"
SAMPLE_EMAILS_DIR = PROJECT_ROOT / "tests" / "sample_emails"
DEMO_EMAILS_DIR = PROJECT_ROOT / "tests" / "demo_emails"
CHROMA_DIR = PROJECT_ROOT / "data" / "chroma"

# --- 시드 ---
# 배포 환경(Render 무료 티어)은 디스크가 영구 저장이 아니라 재시작하면 인덱스가
# 사라진다. 데모 링크가 빈 화면이 되지 않도록, 인덱스가 비어 있으면 미리 파싱해 둔
# 결과로 자동으로 채운다. 파싱 결과를 저장소에 넣어 두므로 기동 시 LLM 호출은 없고
# 임베딩만 한다.
SEED_FILE = PROJECT_ROOT / "seed" / "parsed.json"
SEED_SOURCE_DIRS = (SAMPLE_EMAILS_DIR, DEMO_EMAILS_DIR)
SEED_ON_EMPTY = os.getenv("SEED_ON_EMPTY", "1").lower() not in ("0", "false", "no")

# --- LLM ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-5-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# --- 로딩 ---
SUPPORTED_EXTENSIONS = (".txt", ".eml")
# 예약 확인 메일은 1~2KB다. 업로드 본문을 통째로 메모리에 읽으므로 상한을 둔다.
# 배포 인스턴스가 512MB라 큰 파일 몇 개로도 프로세스가 죽는다.
MAX_UPLOAD_BYTES = 1 * 1024 * 1024

# --- 임베딩 ---
# OpenAI 임베딩 API의 요청당 입력 개수 상한보다 넉넉히 낮게 잡는다.
EMBEDDING_BATCH_SIZE = 100

# --- 청킹 ---
# 예약 메일은 짧으므로 "메일 1건 = 청크 1개"가 기본이다.
# 이 길이를 넘는 메일만 CHUNK_SIZE 단위로 쪼갠다.
MAX_SINGLE_CHUNK_CHARS = 2000
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120

# --- 벡터 스토어 / 검색 ---
COLLECTION_NAME = "travel_reservations"
DISTANCE_METRIC = "cosine"
TOP_K = 3

# 이 유사도 미만은 근거로 쓰지 않는다. 값은 샘플 질의 분포를 재서 정했다.
# (관련 질의와 무관 질의의 1위 유사도가 갈리는 지점)
MIN_SIMILARITY = 0.20
# 1위 대비 이 비율보다 낮은 결과는 버린다.
# 0.90으로 두었더니 평가에서 정답 메일이 2위로 들어온 질문 2건(Q02/Q11)을
# 잘라내 "확인할 수 없습니다"로 오답 처리됐다. 예약 메일은 서로 형식이 비슷해
# 유사도가 좁게 몰리므로, 2~3위까지는 근거로 넘기고 판단은 생성 단계에 맡긴다.
RELATIVE_SIMILARITY_RATIO = 0.75

# --- 답변 생성 ---
ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-5-mini")
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-5-mini")

# 추론 강도. 응답 시간의 대부분이 추론 토큰 생성에 쓰인다(출력 736토큰 중 640이
# 추론이던 적도 있다). "low"로 낮추면 2배 이상 빨라지고 평가 지표는 그대로다.
# "minimal"은 쓰지 않는다. 근거가 있는데도 거절하고 문구까지 망가진다.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low")
NO_INFO_MESSAGE = "예약 내역에서 확인할 수 없습니다."

# --- 웹 검색 ---
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
WEB_SEARCH_MAX_RESULTS = 3

# --- 추출 ---
# raw_snippet에 담을 원문 최대 길이
SNIPPET_MAX_CHARS = 300
# 예약 종류 화이트리스트
RESERVATION_TYPES = ("항공", "숙소", "렌터카", "투어")
