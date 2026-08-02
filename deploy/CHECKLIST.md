# 배포 전 체크리스트

Hugging Face Spaces에 올리기 전에 순서대로 확인하세요.
**1번 항목은 되돌릴 수 없습니다** — 키가 한 번 공개되면 폐기 외에 방법이 없습니다.

---

## 1. API 키 노출 (가장 중요)

- [ ] `.env`가 git에 추적되지 않는지 확인
  ```bash
  git check-ignore -v .env
  ```
  → `.gitignore:2:.env  .env` 처럼 규칙이 출력되면 정상.
  아무것도 안 나오면 **중단하고** `.gitignore`부터 고치세요.

- [ ] 커밋 대상에 `.env`가 없는지 확인
  ```bash
  git status --porcelain | grep -i env
  ```
  → `.env.example`만 보여야 정상.

- [ ] 빌드 컨텍스트에 `.env`가 들어가지 않는지 확인
  ```bash
  grep -E "^\.env$|^data/" .dockerignore
  ```
  → `Dockerfile`이 `COPY`하는 경로에 `.env`가 섞이면 이미지 레이어에 키가 박힙니다.
  이미지를 지워도 레지스트리 캐시에 남을 수 있습니다.

- [ ] 저장소 전체에 실제 키 패턴이 없는지 스캔
  ```bash
  grep -rIn -E "sk-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{20,}" . \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=data
  ```
  → `.env`(무시됨)와 `.env.example`(플레이스홀더)만 나와야 정상.

- [ ] **git 히스토리**에도 키가 없는지 확인 (과거 커밋에 남아 있으면 삭제해도 노출됨)
  ```bash
  git log -p --all | grep -E "sk-proj-[A-Za-z0-9]{20,}" | head
  ```
  → 결과가 있으면 **해당 키를 즉시 폐기하고 재발급**하세요. 히스토리 정리보다
  키 교체가 먼저입니다.

- [ ] Spaces Secrets 등록 (Settings → Variables and secrets)
  - `OPENAI_API_KEY` (필수)
  - `TAVILY_API_KEY` (선택, 없으면 웹 검색만 비활성)
  - Secrets는 **Variables가 아니라 Secrets**로 등록해야 로그에 안 찍힙니다.

---

## 2. 개인정보

- [ ] `data/emails/`에 실제 예약 메일이 없는지 확인 (git에서 제외되지만 재확인)
  ```bash
  ls data/emails/
  ```
  → `.gitkeep`만 있어야 정상.

- [ ] `data/chroma/`(벡터 DB)가 커밋되지 않는지 확인
  ```bash
  git check-ignore -v data/chroma/chroma.sqlite3
  ```

- [ ] `reports/`에 실제 메일 내용이나 개인정보가 남아 있지 않은지 확인
  (샘플 메일은 가짜 데이터이므로 무방)

- [ ] 공개 Space에는 **실제 예약 메일을 올리지 마세요.** 인덱스가 전역이라
  다른 방문자의 질문에 내 예약이 검색될 수 있습니다.

---

## 3. requirements 정확성

- [ ] 로컬 venv와 requirements가 일치하는지 확인
  ```bash
  .venv/bin/pip check
  ```
  → `No broken requirements found.` 여야 정상.

- [ ] 깨끗한 환경에서 설치가 되는지 검증 (가장 확실한 방법)
  ```bash
  python3.13 -m venv /tmp/verify && /tmp/verify/bin/pip install -r requirements.txt
  ```

- [ ] `requirements.txt`에 개발 전용 패키지(pytest 등)가 없는지 확인
  → 개발용은 `requirements-dev.txt`에 분리돼 있습니다.

- [ ] **Python 버전 고정 확인**: `Dockerfile`의 `FROM python:3.13-slim`.
  3.14에서는 `import chromadb`가 멈춥니다.

---

## 4. Spaces 설정 파일

- [ ] `deploy/README_SPACES.md`를 Space 저장소 루트에 **`README.md`로** 복사
  (HF는 루트 README.md의 YAML frontmatter로 SDK·버전을 읽습니다)
  ```bash
  cp deploy/README_SPACES.md README.md   # Space 저장소에서만
  ```
  GitHub 저장소의 포트폴리오용 README와 내용이 다르므로 섞이지 않게 주의하세요.

- [ ] `sdk: docker` 와 `app_port: 7860` 이 `Dockerfile`의 `EXPOSE`·`CMD` 포트와
  일치하는지 확인
- [ ] 로컬에서 이미지가 빌드되는지 확인
  ```bash
  docker build -t travel-inbox . && docker run --rm -p 7860:7860 --env-file .env travel-inbox
  ```

---

## 5. 동작 확인

- [ ] 로컬에서 실행되는지
  ```bash
  make serve   # http://localhost:8000
  ```

- [ ] **키가 없는 상태**에서도 앱이 죽지 않고 안내 메시지를 내는지
  (`.env`를 잠시 옮겨서 테스트)

- [ ] 인덱스가 빈 상태에서 첫 화면이 정상인지 (안내 문구가 나와야 함)

- [ ] 업로드 → 인덱싱 → 질문 흐름이 끝까지 도는지

- [ ] 평가가 통과하는지
  ```bash
  python -m src.evaluate
  ```

---

## 6. 배포 후

- [ ] Space 로그에 키가 찍히지 않는지 확인
- [ ] 첫 질문 응답 시간 확인 (무거운 모듈을 지연 로딩하므로 첫 요청만 느립니다)
- [ ] `/api/docs`가 열리는지 확인. 공개하고 싶지 않으면 `docs_url=None`으로 끕니다
- [ ] Space 재시작 후 인덱스가 사라지는 것을 문서에 명시했는지 확인
- [ ] OpenAI 사용량 대시보드에서 예상 밖의 호출이 없는지 확인

---

## 비용 참고

메일 1건 인덱싱 = LLM 호출 1회 + 임베딩 1회.
질문 1건 = 임베딩 1~3회 + LLM 호출 1~2회(에이전트는 도구 호출마다 추가).
공개 Space는 누구나 쓸 수 있으므로 **사용량 상한(usage limit)을 걸어두세요.**
