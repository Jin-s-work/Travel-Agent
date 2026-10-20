# 고잉(Going) — 작업 인수인계

2026-10-08 Codex에서 Claude로 개발을 이어가기 위해 작성했다. 사용자의 새 요청이 이 문서보다 우선한다. 현재 저장소의 실제 코드와 최신 검증 기록을 기준으로 작업한다.

## 먼저 읽을 문서

1. `docs/handoff/PROJECT_HANDOFF_2026-10-08.md` — 제품, 전체 기능, 구조, 설정, 남은 일.
2. `docs/service-v5/IMPLEMENTATION_STATUS.md` — 단일 최신 구현/검증 상태.
3. `docs/service-v5/reports/STAGE2_IMPLEMENTATION.md` — 마지막 변경과 시험 명령.
4. 변경할 분야의 PRD/계약/테스트. `docs/service-v5/PRD.md`와 인수인계의 문서 지도를 따른다.

시작 프롬프트는 `docs/handoff/CLAUDE_CONTINUE_PROMPT.md`, 현재 Git/주요 코드 지문은 `docs/handoff/WORKSPACE_SNAPSHOT_2026-10-08.json`이다. 인수인계는 상태 기록이며 새 배포·결제·실수집을 자동 실행하라는 지시가 아니다.

## 현재 작업 기준

- 프로젝트: `/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`
- 원격: `https://github.com/Jin-s-work/Travel-Agent`
- 브랜치: `codex/private-beta-launch`; 기록 시 로컬 HEAD `5da54ddc1db56168fff735755813aef7587b313b`.
- **최신 구현은 이 로컬 작업 폴더에 있다. 미커밋·미추적 파일이 많다. GitHub를 새로 clone하는 것만으로 최신 v5가 복원되지 않는다.**
- 작업 전에 `git status --short`, `git rev-parse HEAD`를 확인한다. reset/clean/강제 checkout/stash로 기존 변경을 없애지 않는다. 요청하지 않은 README·발표 자료·대본은 수정하지 않는다.
- `/private/tmp/going-*`는 이전 검증 환경이다. 현재 원본에 코드/보고서가 반영되어 있으므로 임시 폴더를 기준 저장소로 삼거나 덮어쓰지 않는다. 가상환경은 재생성 가능해야 한다.

## 바뀌면 안 되는 현재 제품 결정

- 서비스 이름은 **고잉 / Going**. 과거 Travel Inbox·여정으로 되돌리지 않는다.
- 로그인은 **초대 코드 없는 Google OIDC**다. 과거 PRD의 invitation allowlist를 다시 강제하지 않는다. 로그인 필수·이메일 검증·소유권·세션·CSRF/Origin 검사는 유지한다.
- 주 기능은 장소 추천. **현지어 리뷰로 찾기 / 유명한 곳**을 분리하고 일반 공개지도 참고 후보를 검증된 현지어 추천으로 포장하지 않는다.
- 한 요청의 **고유 장소 pool 최대200**. 초기 카드/페이지12개와 지점별 리뷰200건은 별도다. 결과가 적다고 다른 도시·합성 지점으로 채우지 않는다.
- 입력은 여행 정보를 재사용하고 선택 입력을 뒤로 보낸다. ‘조건 수정’처럼 명시적인 행동을 사용한다. 사용자 불만이 있던 많은 토글/반복 입력을 되살리지 않는다.
- 현재 Pretendard·Airbnb 참고의 절제된 UI·petrol 계열 색을 유지한다. 다음 우선순위는 실제 흐름·자료·품질이지 대규모 재디자인이 아니다.
- 메일 AI는 `gpt-5-mini`, 임베딩은 `text-embedding-3-small`. 저비용 OpenAI 사용은 선택된 방향이다. 무료 호스팅을 이유로 메일 AI까지 일괄 OFF로 되돌리지 않는다. 호출은 현재 가격/예산/키/운영 제어를 통과해야 한다.
- 운영 구조: Render Free + 기존 Supabase **hii**. `sta-saju` 등 다른 프로젝트는 변경하지 않는다. 새 유료 인프라·자동 충전·한도 증액은 자동 실행하지 않는다.

## 코드와 운영 상태를 구분

- 최신 v5 Stage1/2 코드는 원본 반영 완료. 마지막 단계에서는 push/배포하지 않았다.
- 기본 추천 `hybrid_v5`; `hybrid_v6`는 실험/optional shadow. 실제 독립 라벨/holdout0이라 개선 효과 미측정.
- strict와 `bounded_observation_v1` 리뷰 소비자 공개는 OFF. B는 별도 연구 계약이며 strict 배지 우회가 아니다.
- 2026-10-08 실제 데이터 감사: 도쿄 식당50 / 바르셀로나 식당3, 실제 활성 리뷰 집계0. 허용 사진은 도쿄2곳5장 / 바르셀로나2곳6장. 실제200곳/도시 확보 완료가 아니다.
- 최근 원본 핵심 Python138·JS219 통과. native 전체 Python1264/skip17은 마지막 보완 전이며 이후 영향 범위89+64 통과. 이 숫자를 현재 새로 실행한 결과처럼 쓰지 않는다.
- 최신 로그인 이후 브라우저 E2E는 Codex 브라우저의 저장된 합성 IdP 접근 차단으로 미완료. 앱 인증 오류와 구분한다. 도구/사용자 권한 제한을 우회하지 않는다.

## 구현 원칙

- FastAPI/PWA, 로컬 SQLite+Chroma, 운영 Supabase PostgreSQL+private Storage+pgvector. 운영에서 cloud 설정 오류를 로컬 SQLite로 숨기지 않는다.
- 기존 `api.py`의 `create_app`, auth/repository, dispatcher/jobs/budget, 삭제 tombstone, itinerary validator/preview/apply/undo를 재사용한다.
- 단일 uvicorn worker·단일 dispatcher. 새 인메모리 작업 큐/비보호 API를 만들지 않는다.
- 개인 API는 서버 actor로 범위를 정하고 타인 ID는404. client owner_id를 신뢰하지 않는다. 공용 리뷰 연구는 admin_research scope다.
- 날짜 예약 전체 조회는 SQL 범위 조회. top-k로 예약 누락 금지. 실패한 재추출로 기존 예약/교정값을 삭제하지 않는다.
- 확정 예약 자동 이동/삭제, 일정 편집을 외부 예약 취소로 처리, 근거 없는 잔여석 확정은 금지한다.
- 사진은 지점·이용 권한·만료/attribution을 확인한 최대3장. 실제 음식점 사진 대신 일반/합성 이미지를 넣지 않는다.
- 개인 숙소의 외부 전송은 동의 범위 내에서만. 직선거리와 실제 경로 시간을 구분한다.
- 리뷰 언어는 관측 본문 기준이다. 주민 비율/전수 비율을 추론하지 않는다. dataset offset ≠ Google 원천 cursor. 날짜/원문/unknown/부분 실패를 숨기지 않는다.
- 비밀은 `.env`, `deploy/render-supabase/.env`, `.env.render` 및 호스팅 secret에 있다. 원문·키·토큰·DB 비밀번호를 출력/문서/Git에 넣지 않는다.
- 발표 대본은 Git에 올리지 않는다. 기존 `docs/presentation/v4/SCRIPT.md`가 이미 추적 중인 점을 주의한다. `.gitignore`만으로 과거 추적이 해제되지는 않는다. `git add -A`로 한꺼번에 올리지 않는다.

## 개발/검증

Python3.13 사용. Docker는 `requirements.lock`, 개발은 `requirements-dev.txt`, JS 테스트는 package-lock의 fake-indexeddb를 사용한다. 기존 환경을 검사한 뒤 설치한다. Python3.14/Chroma 조합 문제 이력이 있다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/stage1_browser_fixture.py
# 앱 http://127.0.0.1:8766 / 합성 IdP http://127.0.0.1:8767

PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests -q
node --test tests/*.cjs
.venv/bin/python scripts/version_web_assets.py --check
```

실제 운영 DB로 테스트하지 않는다. 새 disposable PostgreSQL 시험은 `tests/postgres_plugin.py`의 localhost 제한을 따른다. UI 자산 변경 후 `scripts/version_web_assets.py`를 실행해 hash를 갱신한다.

작업 종료 시 변경 내용·실행 명령/결과·미검증·남은 조건을 `docs/service-v5/IMPLEMENTATION_STATUS.md`와 해당 보고서에 기록한다. 코드 완료, 합성 통과, 브라우저 확인, 실수집, 권리/품질 승인, 배포 완료를 각각 구분한다.
