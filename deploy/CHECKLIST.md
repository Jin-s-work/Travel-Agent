# 배포 전 체크리스트

배포 전에 순서대로 확인하세요. 현재 배포처는 Render이며, 절차는 [RENDER.md](RENDER.md)에 있습니다.
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

- [ ] Render 환경변수 등록 (Dashboard → Environment). `render.yaml`은
  `sync: false`로 두어 값이 저장소에 들어가지 않습니다
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

- [ ] 공개 배포본에는 **실제 예약 메일을 올리지 마세요.** 인덱스가 전역이라
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

## 4. 배포 설정 파일

- [ ] `render.yaml`의 `healthCheckPath`가 `/api/health`인지 확인
- [ ] `Dockerfile`의 `CMD`가 셸 형식인지 확인 (Render가 주입하는 `$PORT`가
  확장되어야 합니다)
- [ ] `seed/`와 `tests/sample_emails/`·`tests/demo_emails/`가 이미지에 들어가는지
  확인. 빠지면 기동 시 인덱스 자동 복구가 조용히 건너뛰어집니다
  (`tests/`는 `.dockerignore`가 제외하므로 `!` 규칙으로 되살립니다)
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

- [ ] 배포 로그에 키가 찍히지 않는지 확인
- [ ] 첫 질문 응답 시간 확인 (무거운 모듈을 지연 로딩하므로 첫 요청만 느립니다)
- [ ] `/api/docs`가 열리는지 확인. 공개하고 싶지 않으면 `docs_url=None`으로 끕니다
- [ ] 재시작 후 인덱스가 시드로 다시 채워지는지 확인
  (로그에 `시드 완료: 청크 7개`)
- [ ] OpenAI 사용량 대시보드에서 예상 밖의 호출이 없는지 확인

---

## 비용 참고

메일 1건 인덱싱 = LLM 호출 1회 + 임베딩 1회.
질문 1건 = 임베딩 1~3회 + LLM 호출 1~2회(에이전트는 도구 호출마다 추가).
공개 배포본은 누구나 쓸 수 있으므로 **사용량 상한(usage limit)을 걸어두세요.**
기동 시 자동 복구는 미리 파싱해 둔 결과를 쓰므로 LLM 호출이 없고 임베딩만 합니다.
