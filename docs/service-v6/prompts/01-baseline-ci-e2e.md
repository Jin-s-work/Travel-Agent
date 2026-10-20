# 1단계 구현 프롬프트 — 기준선: 잃지 않고, 자동으로 확인하고, 실제로 도는지 보기

> 새 Claude Code 세션을 저장소 루트에서 열고 구분선 아래 전체를 붙여 넣는다.

- 실행 전에 [PRD](../PRD.md) 10절 결정 1(이동시간 정책)을 적어 둔다.
- 이 프롬프트는 `docs/handoff/CLAUDE_CONTINUE_PROMPT.md`의 A·B 작업을 포함한다. 둘을 같이 실행하지 않는다.

---

너는 여행 서비스 고잉(Going)의 릴리스 엔지니어 겸 백엔드 개발자다. 이번 단계의 목표는 새 기능이 아니다. v5까지의 작업을 잃지 않게 묶고, 사람이 없어도 매 커밋 검사되게 만들고, 로그인 이후 실제 화면 흐름이 도는지 자동으로 확인하는 것이다.

저장소: `~/Desktop/ChatGPT/travel-inbox-rag` (이 저장소 루트)
기획: `docs/service-v6/PRD.md` 2절(G1~G5), 4.1절, 5절 D4

## 0. 먼저 확인해

1. `CLAUDE.md`, `docs/handoff/PROJECT_HANDOFF_2026-10-08.md`, `docs/service-v5/IMPLEMENTATION_STATUS.md`, `docs/service-v6/PRD.md`를 읽어.
2. `git status --short`, `git branch --show-current`, `git rev-parse HEAD`, `python3.13 --version`, `node --version`을 확인하고 인수인계(HEAD `5da54dd`, 수정 88 · 미추적 65)와 다른 점을 기록해.
3. `.venv`가 있으면 재사용하고 없을 때만 만들어. 기존 환경을 덮어쓰지 마.
4. 아무것도 고치기 전에 기준 시험을 한 번 돌려 출발점을 남겨. 실패는 원인 추정과 함께 그대로 적어.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests -q
node --test tests/*.cjs
.venv/bin/python scripts/version_web_assets.py --check
```

## 공통 규칙 — 모든 묶음에 우선

- `.env`, `deploy/render-supabase/.env`, `.env.render`와 호스팅 secret을 읽거나 출력하지 마. 필요한 경우 이름과 존재 여부만 확인해.
- `git reset`/`clean`/`stash`/강제 checkout으로 기존 변경을 없애지 마. `git add -A`·`git add .` 금지, 파일을 명시해서 stage해.
- push·PR·Render 배포·운영 설정 변경은 하지 마. 로컬 커밋까지만. 커밋 전에 `git config user.email`이 GitHub noreply 주소인지 확인하고, 아니면 멈추고 물어.
- 패키지 설치는 이름·버전·이유·대략 크기를 목록으로 먼저 보여 주고 승인을 받은 뒤에만 해. 승인이 없으면 그 묶음은 골격만 만들고 미실행으로 기록해.
- 유료 외부 호출(OpenAI, Apify, Google)은 하지 마. 시험은 `PYTHON_DOTENV_DISABLED=1`, 임시 DB, 합성 사용자 A/B로만. 운영 DB 금지.
- 기존 계약을 약화하지 마: 타인 ID 404, 삭제 tombstone, 고정 예약 보호, 예산 예약·정산, preview/apply/undo, 리뷰 소비자 OFF, 기본 모델 hybrid_v5.
- 원인을 숨기는 예외 처리, 안전 검사 제거, 테스트 기대값만 바꾸기로 통과시키지 마.
- 실행하지 않은 것을 통과로 쓰지 마. code / synthetic / browser / live / 배포를 구분해 기록해.
- 기존 파일을 통째로 다시 쓰지 말고 필요한 부분만 고쳐. README·발표 자료·대본은 수정하지 마.

## 묶음 A — 작업 보존

GitHub를 clone해도 v5가 복원되도록 로컬 커밋을 만든다. push는 사람이 한다.

1. 수정·미추적 파일을 분류한 표를 `docs/service-v6/reports/STAGE1_COMMIT_PLAN.md`에 먼저 써. 분류: 앱 코드 / 테스트 / v5 문서 / 인수인계 / 설정 예시 / 발표 자료 / 기타.
2. 커밋하지 않을 것: `docs/presentation/**`(특히 `v4/SCRIPT.md`와 `.key`), 예시가 아닌 `.env*`, `data/`, `.venv/`, `node_modules/`, `__pycache__/`, `.pytest_cache/`. 이미 추적 중인 `SCRIPT.md`의 추적 해제는 하지 말고 대표 결정 사항으로 보고만 해.
3. `scripts/check_publish_safety.py`를 새로 만들어. 공개 저장소(`Jin-s-work/Travel-Agent`)에 올리기 전에 아래를 검사하고, 값은 출력하지 말고 `파일:줄 규칙이름`만 보여 줘.
   - `sk-`·`sk-proj-` 형태 키, `-----BEGIN ... PRIVATE KEY-----`, DB 연결 문자열 형태(`postgres://user:pass@`)
   - 추적 중인 `.env`(예시 파일 제외)
   - `/Users/<사용자명>/` 같은 절대경로
   - 발표 대본 추적 여부
   - `--ci` 모드: 키·비밀 규칙은 실패, 절대경로·대본은 경고
4. 절대경로는 `CLAUDE.md`와 인수인계 문서에 이미 있다. 자동으로 고치지 말고 목록과 처리안(상대경로로 바꾸기 / 그대로 두기)을 보고해.
5. 점검을 통과한 범위만 의미 단위로 나눠 커밋해. 예: v5 후보·추천, v5 리뷰 관측, v5 사진, v5 테스트, v5 문서, 인수인계, v6 문서. 메시지는 영어 명령형 한 줄 + 본문에 범위.
6. 커밋 후 남은 `git status --short`와 남긴 이유를 계획 문서에 적어.

## 묶음 B — CI

공개 저장소의 GitHub-hosted standard runner는 무료다(PRD 11절). 외부 호출 없이 push·PR마다 같은 검사를 돌린다.

1. `.github/workflows/ci.yml`을 만들어. `permissions: contents: read`, 같은 브랜치 중복 실행 취소(`concurrency`), secret 참조 0, 배포 job 0.

   | job | 내용 |
   | --- | --- |
   | python | Python 3.13, `requirements.lock` + `requirements-dev.txt`, pip 캐시, `PYTHON_DOTENV_DISABLED=1 ZERO_SPEND=1 pytest tests -q` |
   | js | Node 24, `npm ci --ignore-scripts --no-audit --no-fund`, `node --test tests/*.cjs` |
   | assets | `python scripts/version_web_assets.py --check` |
   | postgres | pgvector가 포함된 PostgreSQL 서비스 컨테이너, `TRAVEL_TEST_POSTGRES_DSN`은 `localhost`, `-p tests.postgres_plugin`로 cloud 계열 시험 |
   | safety | `python scripts/check_publish_safety.py --ci` |

2. `tests/postgres_plugin.py`의 localhost 제한은 바꾸지 마. 서비스 컨테이너는 runner의 localhost로 접근된다. 이미지 이름·태그는 공식 저장소에서 확인해 고정하고 출처를 주석으로 남겨 [미확인].
3. `Makefile`에 `ci` 타깃을 추가해 같은 검사를 로컬에서 한 번에 돌린다. postgres 검사는 `TRAVEL_TEST_POSTGRES_DSN`이 있을 때만. 기존 타깃은 바꾸지 마.
4. Actions는 로컬에서 실행할 수 없다. 워크플로 각 단계와 같은 명령을 로컬에서 실행한 결과를 기록하고, "Actions에서 실행 확인"은 대표가 push한 뒤의 항목으로 남겨.
5. skip된 시험을 사유별로 분류하고, CI의 postgres job으로 채워지는지 표시해.
6. 선택: `web/js/*.js`에 `// @ts-check`와 JSDoc을 붙이고 `tsc --noEmit --allowJs --checkJs`로 검사하는 계획을 적어. `typescript` 설치는 승인 후에만.

## 묶음 C — 로그인 이후 E2E

1. 도구는 Playwright(Python + pytest 통합)를 권장한다. 기존 fixture와 `tests/conftest.py`를 재사용하기 쉽기 때문이다. 다른 선택을 하면 이유를 적어. 설치할 패키지·버전·브라우저 다운로드 크기를 보여 주고 **승인 후** 설치해.
2. `scripts/stage1_browser_fixture.py`의 앱(8766)과 합성 IdP(8767)를 시험 fixture가 띄우고 내리게 해. 같은 포트를 사용자가 쓰고 있으면 종료하지 말고 빈 포트를 쓰거나 멈추고 알려.
3. 이것은 프로젝트 자체의 시험 도구다. 다른 도구에 저장된 접근 차단을 우회하는 것이 아니다. 실행 환경이 localhost 접근을 막으면 우회하지 말고 그대로 보고해.
4. 시나리오:

   | ID | 흐름 | 통과 기준 |
   | --- | --- | --- |
   | E01 | 합성 로그인 → 마지막 여행 → 찾기(현지어 리뷰 / 유명한 곳) → 저장 → 저장한 곳 → 일정 미리보기 → 적용 → 새로고침 → 되돌리기 | 단계별 DOM 확인, 새로고침 후 유지, undo가 새 revision |
   | E02 | 사용자 B가 A의 여행·장소·일정 URL에 직접 접근 | 404, A 자료 노출 0 |
   | E03 | `examples/mail-test-pack/07-eight-bookings-one-day.eml` 업로드(local 모드) → 그날 예약 질문 | 8개 + 수동 1개 모두, 외부 호출 0 |
   | E04 | 390px, 키보드만으로 조건 수정 열기·닫기 | 가로 스크롤 0, 포커스 복귀 |
   | E05 | 필터 빠른 3회 변경과 여행 전환 | 최신 요청만 반영, 다른 여행 결과 0 |

5. 스크린샷은 합성 자료만 담아 `docs/service-v6/reports/e2e/`에 저장해. 실패 trace는 로컬에 두고 Git에는 요약만.
6. E2E가 통과하면 CI에 `e2e` job을 추가해(Chromium만, 실패 시 trace artifact 업로드).

## 묶음 D — 드러난 결함 고치기

1. 기준 시험과 E2E에서 나온 결함을 심각도 순으로 고치고, 결함마다 회귀 테스트를 추가해.
2. 이동시간 정책(PRD G4): 기본 배포는 `DisabledRouteProvider`를 넘기므로 `src/itineraries/travel_time.py`의 `elif self.travel.provider is not None` 분기에서 모든 구간이 `ROUTES_DISABLED`가 되고, 도보 추정 분기에 도달하지 않는다. PRD 10절 결정 1을 따른다. 결정이 비어 있으면 "부족한 정보: 이동시간 정책"이라고 적고 이 항목만 멈춰.
   - A안: 제공자가 비활성일 때 같은 도시 3km 이하 도보만 `basis=estimate`로 계산한다. strict 판정에서는 기존대로 미확인으로 취급하고, 잠정안에서만 배치한다. 화면에는 '직선거리 기준 추정'을 보여 줘.
   - `tests/test_location_providers.py:252`의 기존 기대는 결정된 계약에 맞게 이름과 의도를 갱신하고, 바꾼 이유를 보고서에 적어.
3. 줄 번호는 2026-10-20 기준이다. 코드가 바뀌었으면 현재 코드를 따른다.

## 묶음 E — 출시 준비 (배포하지 않음)

1. 메모리: 운영과 같은 설정(로컬 저장소 백엔드)으로 앱을 띄워 기동 직후·추천 1회·메일 분석 1회(local) 뒤 RSS를 재. Lingua를 import하는 경로가 운영 요청에서 실행되는지 확인하고, 실행된다면 적재 후 RSS를 따로 재. Render Free는 512MB다(PRD 11절).
2. Docker가 이미 설치되어 있으면 `--memory=512m`로 같은 측정을 해 봐. 없으면 설치하지 말고 미측정으로 남겨.
3. 위험하면 Lingua를 필요한 언어만 적재하는 방법을 공식 문서에서 확인해 제안해. 구현은 측정 결과가 위험할 때만.
4. 배포 차이: 기록된 운영 커밋 `d154dc0`과 현재 커밋 사이의 파일 수, schema·migration 변경, 새 설정 이름을 정리해. 설정은 `.env.example`의 이름만 비교해.
5. `docs/service-v6/reports/RELEASE_CHECKLIST.md`: push 전 점검 → push → Render 수동 배포 → `/health/ready` → 본인 계정 로그인 확인 → 롤백. 각 줄의 실행 주체를 '사람'으로 표시해.

## 완료 기준

| ID | 기준 |
| --- | --- |
| S1-01 | 커밋 계획 문서, 로컬 커밋 목록, 남긴 파일과 이유 |
| S1-02 | `check_publish_safety.py` 결과: 비밀 0, 절대경로·대본 목록 보고 |
| S1-03 | `make ci` 로컬 통과의 실제 출력(PG는 DSN이 있을 때) |
| S1-04 | `ci.yml` 존재, secret 참조 0, 배포 job 0 |
| S1-05 | E01~E05 통과 로그·스크린샷, 또는 미실행과 사유 |
| S1-06 | 결함마다 회귀 테스트, 이동시간 결정 반영 |
| S1-07 | 메모리 측정표, 배포 차이표, 출시 체크리스트 |

## 보고

- `docs/service-v6/IMPLEMENTATION_STATUS.md`를 새로 만들고 단일 최신표를 둬. 열: 영역 / code / synthetic / browser / live / 배포.
- `docs/service-v6/reports/STAGE1.md`: 바꾼 파일, 실행한 명령과 실제 출력 요약, 미실행과 이유, 대표에게 넘길 일(push, Actions 확인, 배포).
- 채팅 보고는 15줄 이내로 하고, 맨 위에 대표가 결정할 것의 개수를 적어.
