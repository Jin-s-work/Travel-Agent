# 1단계 보고서 — 기준선: 보존 · CI · E2E · 결함

> v6 1단계 프롬프트(`prompts/01-baseline-ci-e2e.md`)를 실행한 기록. 바꾼 파일, 실제로 돌린 명령과 결과, 돌리지 못한 것, 대표에게 넘길 일을 담는다.

2026-10-20 · Claude Code 세션 · 로컬 커밋까지만 · push·Actions·배포·패키지 설치 없음

**세 줄 요약**

- v5 작업을 로컬 커밋 11개로 묶고, 게시 점검 스크립트·CI 워크플로·`make ci`를 추가했다. `make ci` 로컬 실행 결과 Python 1302 · JS 220 · PG 282+16 통과, 실패 0이다.
- 결함 4건을 고쳤다: 기본 배포의 이동시간 전부 미확인(G4, 결정 A), PG에서 500이 나던 리뷰 계약 SQL, 오래된 schema 12 재현 시험, 로드 중 고른 탭이 초안 복원에 덮이는 문제. 결함마다 회귀 시험을 붙였다.
- Playwright 승인 뒤 브라우저 E2E E01~E05와 회귀 E01b를 실제로 돌려 8개 모두 통과했다. 첫 실행에서 앱 결함 1건(D4)을 찾아 고쳤다. 원격의 앞선 25커밋은 대표 지시로 병합했다(`27bdfaa`). push는 하지 않았다.

## 출발점

인수인계와 같은 HEAD·같은 변경 수에서 시작했다. 다른 점은 셋이다.

| 항목 | 인수인계 | 이번 확인 |
| --- | --- | --- |
| HEAD · 브랜치 | `5da54dd` · `codex/private-beta-launch` | 같음 |
| 수정 · 미추적 | 88 · 65 | 88 · 66 (`docs/service-v6/` 추가) |
| Python · Node | 3.13 · 24 | 3.13.5 · v24.20.0, `.venv` 재사용 |
| 원격 브랜치 | 로컬과 같다고 기록 | **`df01153`, 로컬보다 25커밋 앞섬** (`git fetch`로 확인) |
| 작업 폴더 | — | **iCloud Desktop 동기화로 파일 다수가 내려가 있음** |

### iCloud로 내려간 파일

첫 pytest가 0% CPU로 5분간 멈췄다. `.venv/.../langsmith/pytest_plugin.py`가 iCloud dataless 상태였다.

| 위치 | dataless 파일 |
| --- | ---: |
| `.venv` | 25,543 |
| `.git` | 1,053 |
| 저장소 소스 (`src/reliability/jobs.py` 등 v5 수정 파일 포함) | 43 |
| `node_modules` | 73 |

파일을 병렬로 읽어 내려받았다(설치·재생성 아님). 처리량은 동시 128~256개에서 초당 약 8파일이었다. 한때 17개까지 줄었지만, E2E 작업 중 다시 확인하니 `.venv` 27,560개와 `src`·`tests`·`web` 201개가 또 내려가 있었다. iCloud 저장 공간 최적화가 파일을 계속 다시 내린다.

> [!WARNING]
> 내려간 파일을 읽는 시험은 10초 작업 제한에 걸려 실패한다. 실제로 전체 실행에서 `test_foundation_api.py` 7개가 `google/protobuf` 파일 대기로 실패했고, 내려받은 뒤에는 통과했다. 이 폴더에서 시험할 때는 `find .venv -flags +dataless -type f | wc -l`을 먼저 보거나, iCloud 밖에 저장소를 두는 것을 권한다.

## 기준 시험 — 변경 전

| 명령 | 결과 |
| --- | --- |
| `node --test tests/*.cjs` | 219 통과 · 0 실패 · 74.2초 |
| `.venv/bin/python scripts/version_web_assets.py --check` | 통과 |
| `PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests -q` | **미완료** — iCloud 대기로 5분간 출력 0, 중단 |

Python 전체 기준선은 변경 전에 얻지 못했다. 변경 후 결과와 비교할 수 없으므로, 아래 실패는 모두 원인을 따로 확인해 적었다.

## 바꾼 파일

| 파일 | 내용 |
| --- | --- |
| `scripts/check_publish_safety.py` | 게시 전 비밀·절대경로·대본 점검. 값 출력 없음 |
| `tests/test_publish_safety.py` | 규칙·출력·모드 시험 22개 |
| `.github/workflows/ci.yml` | python · js · assets · postgres · safety · e2e 6 job |
| `Makefile` | `ci`, `ci-python`, `ci-js`, `ci-assets`, `ci-postgres`, `ci-safety`, `e2e`, `e2e-evidence` 추가. 기존 타깃 그대로 |
| `src/itineraries/travel_time.py` | 비활성 제공자일 때 도보 추정 분기에 도달 (G4) |
| `tests/test_location_providers.py` | `ROUTES_DISABLED` 기대 시험을 결정 A 계약으로 갱신 |
| `tests/test_itinerary_api.py` | 기본 배포 배선으로 strict/잠정안 회귀 시험 추가 |
| `web/js/foundation.js` · `web/index.html` · `web/sw.js` | 추정 구간 문구 "직선거리 기준 추정", 자산 hash |
| `tests/test_discovery_ui.cjs` | 추정 구간 문구 회귀 시험 |
| `src/storage/postgres.py` | `json_extract(…,'$.observation_profile')` PG 번역 |
| `tests/test_postgres_translate.py` | 번역 회귀 시험 2개 |
| `tests/test_cloud_storage.py` | schema 12 재현에 `place_photo_cache` 포함 |
| `scripts/stage1_browser_fixture.py` | `STAGE1_APP_PORT`·`STAGE1_IDP_PORT`로 포트 지정. 기본값 8766/8767 그대로 |
| `tests/e2e/` | 빈 포트 서버 fixture, 합성 OIDC 로그인, 브라우저·trace fixture, E01~E05 + E01b |
| `web/js/foundation.js` (D4) | 로드 중 탭 클릭을 복원보다 우선. 자산 hash 갱신 |
| `requirements-dev.txt` · `.gitignore` | `playwright==1.63.0` 고정, `.e2e-traces/` 제외 |
| `docs/service-v6/reports/e2e/` | 합성 자료 스크린샷 10장 |
| `scripts/measure_stage1_memory.py` | 기동·추천·메일 분석 뒤 RSS, Lingua 적재 측정 |
| `docs/service-v6/reports/*`, `IMPLEMENTATION_STATUS.md` | 이 보고서와 계획·체크리스트·상태표 |

## 묶음 A — 작업 보존

v5 변경 212경로를 커밋 11개로 묶고 발표 자료 31경로는 남겼다. 분류·순서·남긴 이유는 [STAGE1_COMMIT_PLAN.md](STAGE1_COMMIT_PLAN.md)에 있다.

게시 점검 결과는 비밀 0, 경고 83(절대경로 76 · 루프백 DSN 5 · 추적 대본 2)이다. 절대경로는 고치지 않고 처리안만 적었다.

### 원격이 앞서 있다

`git fetch` 결과 원격 `codex/private-beta-launch`와 `main`이 `df01153`이다. 로컬에 없던 25커밋에 운영 기록 커밋 `d154dc0`이 들어 있다. 발표 자료를 뺀 188경로를 비교했다.

| 관계 | 경로 수 |
| --- | ---: |
| 로컬 = 원격 | 77 |
| 로컬만 변경 | 86 |
| 원격만 변경 (로컬에 빠진 원격 변경) | 0 |
| 양쪽 변경, 로컬이 원격을 포함 | 5 |
| 양쪽 변경, 3-way 병합 충돌 | 20 |

로컬 작업본은 원격 내용을 바탕으로 더 나아간 상태였다. 처음에는 공개 범위가 걸려 병합하지 않았고, 대표가 권장안을 승인한 뒤 병합했다. 발표 자료는 iCloud 밖에 백업(54파일, sha256 일치)한 뒤 원격판을 따랐고, 21개 충돌은 로컬 기준으로 풀되 원격 전용 줄을 파일마다 검토했다. 원격 줄을 살린 곳은 v4 진행 기록 한 파일이다. 전체 기록은 [출시 체크리스트 2절](RELEASE_CHECKLIST.md#2-원격과-갈라진-이력--병합-완료)에 있다.

## 묶음 B — CI

`ci.yml`은 `permissions: contents: read`, 같은 ref 중복 실행 취소, secret 참조 0, 배포 job 0이다. 각 job은 `make ci-*`를 불러 로컬과 같은 명령을 쓴다. Actions는 실행하지 않았다.

| job | 고정한 것 | 근거 |
| --- | --- | --- |
| 공통 action | checkout v7.0.1 · setup-python v7.0.0 · setup-node v7.1.0 커밋 SHA | `git ls-remote --tags` 2026-10-20. 배포일 [미확인] |
| postgres | `pgvector/pgvector:0.8.7-pg17@sha256:ac08538c…` | Docker Hub tags API digest와 로컬 이미지 digest 일치 |

postgres job은 trust 인증 일회용 컨테이너라 DSN에 비밀번호가 없다. `tests/postgres_plugin.py`의 localhost 제한은 바꾸지 않았다.

### ZERO_SPEND를 CI 시험 환경에서 뺀 이유

프롬프트 표는 `ZERO_SPEND=1 pytest`였지만 넣지 않았다. 시험 결과가 근거다.

- 1차 PG 실행에서 `ZERO_SPEND=1` 때문에 10개가 실패했다(`test_itinerary_api` 9, `test_product_feedback` 1). 가짜 과금 경로 공급자를 일부러 부르는 시험인데, `ZERO_SPEND`가 공급자 호출 전에 막는다.
- 같은 시험은 `ZERO_SPEND=0`에서 통과한다. `ZERO_SPEND` 자체는 `test_location_providers.py`가 `monkeypatch`로 켜서 따로 시험한다.
- CI에는 secret이 없고 `conftest.py`가 API 키를 `test-placeholder`로 덮으므로, 유료 호출은 인증될 수 없다.

시험 기대값을 바꾸지 않고 환경변수 쪽을 바꿨다. `Makefile`에 이유를 주석으로 남겼다.

### skip 분류

SQLite 전체 실행의 skip 20개다. CI postgres job이 채우는지 함께 적었다.

| 사유 | 수 | CI에서 |
| --- | ---: | --- |
| 일회용 PostgreSQL 필요 (`test_cloud_storage` 13 · `test_postgres_migration_concurrency` 3 · `test_stage2_postgres_migration` 1) | 17 | postgres job이 실행 |
| E2E opt-in (`GOING_E2E=1`, HTTP 2 · 브라우저 6) | 8 | `e2e` job이 실행 |

인수인계의 과거 기록(1264 통과 / 17 skip)과 PG 필요 17개가 같다. 늘어난 8개는 이번에 추가한 E2E다.

PG 실행의 skip 3개는 SQLite 전용 리허설·다운그레이드·아카이브 시험이며 SQLite 실행이 덮는다.

### 선택 과제 — `@ts-check` 계획 (미실행)

`web/js` 12파일 2,662줄에 `@ts-check`가 0개다. `typescript`는 설치 승인 전이라 계획만 적는다.

1. `jsconfig.json`(checkJs, `lib: ["dom","es2022"]`, `noEmit`)을 둔다
2. 작은 순수 모듈(`offline-store.js`)부터 파일 머리에 `// @ts-check`, API 응답에 JSDoc `@typedef`
3. `npx tsc --noEmit -p jsconfig.json`이 0 오류인 파일만 CI js job에 포함
4. `foundation.js`(가장 큼)는 마지막. 오류 수를 줄여 가는 방식으로 기록

## 묶음 C — E2E

대표가 2026-10-20 상위 세션을 통해 설치를 승인했다(원문: "playwright==1.63.0과 Chromium 1개 설치를 승인해."). 승인 범위만 설치했다.

| 설치 | 값 |
| --- | --- |
| `pip install playwright==1.63.0` (dry-run으로 먼저 확인) | `greenlet-3.5.6 playwright-1.63.0 pyee-13.0.1` |
| `python -m playwright install --only-shell chromium` | Chrome Headless Shell 153.0.8010.12 (다운로드 94.3MiB, 디스크 204MB, `~/Library/Caches/ms-playwright`). Playwright가 녹화용 ffmpeg 2.5MB를 함께 받음 |

`pytest-playwright`는 쓰지 않았다. 브라우저·컨텍스트·실패 시 trace는 `tests/e2e/conftest.py`의 자체 fixture가 맡는다. `requirements-dev.txt`에 `playwright==1.63.0`을 고정했다.

### 결과 — 8개 모두 통과

| ID | 흐름 | 결과 · 실제로 확인한 것 |
| --- | --- | --- |
| 서버·로그인 (HTTP) | 빈 포트 fixture, 합성 IdP authorize/callback | 통과 |
| E01 | 로그인 → 마지막 여행 → 현지어 리뷰 / 유명한 곳 → 2곳 저장 → 저장한 곳 → 일정 미리보기 → 적용 → 편집 → 새로고침 → 되돌리기 | 통과. 현지어 리뷰 카드 0(검증 자료 없음), 저장 2 → 보관함 2, 버전 1 → 편집 2 → 새로고침 후 2 유지 → 되돌리기 3(새 revision), 화면에 "이동 · 직선거리 기준 추정" |
| E01b | 새로고침 중 고른 탭이 늦게 온 초안 복원에 덮이지 않음 | 통과 (D4 회귀) |
| E02 (HTTP) | B가 A의 여행·예약·북마크·조건·일정·숙소·문서·작업 URL | 404, A 제목 0 |
| E02 (브라우저) | B 로그인 → 여행 목록 · A의 여행·장소 상세·보관함·일정 URL 직접 접근 | 목록에 B 여행만, 모든 URL 404, 응답에 A 전용 문자열 0 |
| E03 | 도쿄 여행에 `07-eight-bookings-one-day.eml` 업로드(local) → "11월 7일 예약을 모두 알려줘" | 11/7 9건(메일 8 + 수동 1), 답변 "총 9개", 근거 9건, 브라우저 외부 요청 0, 사용량 기록 증가 0 |
| E04 | 390px, 키보드만으로 찾기 → 조건 수정 열기·닫기 | 가로 스크롤 0, 대화상자 안으로 포커스, Esc 뒤 "조건 수정"으로 복귀 |
| E05 | 종류 필터 3회 연속 변경 → 여행 전환 | 마지막 필터(볼거리)로만 요청·표시, 전환 뒤 도쿄 결과 0 |

```bash
make e2e
# 출력: 8 passed in 22.72s
make e2e-evidence   # 같은 실행 + 스크린샷 저장
```

스크린샷 10장(합성 자료만, 1.9MB)은 [`e2e/`](e2e/)에 있다. 실패 trace는 `.e2e-traces/`(Git 제외)에만 남는다. 첫 실행의 실패 trace 3개는 원인 확인 뒤 지웠다.

### 첫 실행에서 실패한 4개

| 시험 | 원인 | 조치 |
| --- | --- | --- |
| E01 | 앱 결함 D4 — 새로고침 직후 누른 "일정" 탭이 늦게 온 초안 복원("저장한 곳")에 덮임 | 앱 수정 + 회귀 시험 |
| E02 | 시험 코드 — 북마크의 장소 키는 `matched_place_id` | 시험 수정 |
| E03 | 시험 코드 — 날짜 버튼의 접근성 이름은 "2026-11-07 토요일 · 9건" | 시험 수정. 데이터(9건)는 처음부터 맞았음 |
| E05 | 시험 코드 — 필터 요청이 디바운스돼 `networkidle`만으로는 요청을 못 기다림 | 응답을 기다리도록 수정 |

E03의 서버 사용량 단언도 바꿨다. 앞 시나리오의 합성 지도 호출(비용 0)이 같은 사용자 기록에 남아 있어, 전체 0 대신 "E03 동안 증가 0"으로 본다.

### CI `e2e` job

`ci.yml`에 6번째 job을 추가했다. Chromium headless shell만 설치(`--with-deps --only-shell chromium`)하고 `make e2e`를 돌린다. 실패하면 `e2e-traces/`를 artifact로 7일 보관한다. Actions에서는 아직 실행하지 않았다(Linux 실행 [미확인]).

## 묶음 D — 결함

심각도 순이다. 결함마다 회귀 시험을 붙였고, 회귀 시험이 수정 전 코드에서 실패하는 것을 확인했다.

| # | 결함 | 심각도 | 수정 | 회귀 시험 |
| --- | --- | --- | --- | --- |
| D1 | 기본 배포(`DisabledRouteProvider`)에서 모든 이동 구간이 `ROUTES_DISABLED` | 높음 — 운영 일정 전부 미확인 | 분기 순서 변경 | `test_disabled_route_provider_estimates_only_short_same_city_walking`, `test_default_disabled_routes_estimate_walking_only_in_provisional_mode`, JS 문구 1 |
| D2 | v5 리뷰 계약 조회 `json_extract(evidence_json,'$.observation_profile')`가 PG에서 `42883` → 관리자 리뷰 경로 500 | 높음 — Supabase에서만 발생 | `translate()`에 번역 규칙 1줄 | `tests/test_postgres_translate.py` (모든 `json_extract` 사용처 검사) |
| D3 | `test_cloud_storage`의 schema 12 재현이 schema 15 테이블을 안 지움 → 이관 시 `table place_photo_cache already exists` | 보통 — 시험 결함, 원격에도 같음 | 재현 목록에 1개 추가. 기대값 불변 | 해당 시험 자체 |
| D4 | 페이지를 불러오는 동안 사용자가 고른 탭을, 뒤이어 도착한 작업 초안 복원이 초안의 옛 탭으로 덮음 (E01에서 발견) | 보통 — 새로고침 직후 화면이 저절로 바뀜 | 로드 중(`workspaceSuspended`) 탭 클릭을 기존 `workspaceNavigationOverride`에 기록. '저장한 곳' 별칭도 유지 | JS `navigate` 시험 1, E2E E01b |

### D1 — 이동시간 정책 (PRD 10절 결정 1 = A)

결정이 적혀 있어 진행했다. `elif self.travel.provider is not None:` 분기를 도보 추정 분기 뒤로 옮겼다.

- 같은 도시 · 도보 · 좌표 있음 · 직선 3km 이하 → `basis=estimate`, `WALKING_ROUTE_UNVERIFIED`, 공급자 호출·과금 0
- 3km 초과 → `ROUTE_DISTANCE_REQUIRES_PROVIDER`, 다른 도시·대중교통·자동차 → `ROUTES_DISABLED` 유지
- strict는 기존 검증기가 `estimate`를 `ESTIMATED_TRAVEL`(unknown)로 보므로 그대로 배치하지 않는다. 잠정안에서만 배치된다
- 화면 제목을 "계획 추정"에서 "직선거리 기준 추정"으로 바꿨다

기존 `tests/test_location_providers.py:252` `test_explicit_disabled_route_preserves_geometry_without_minutes`는 "비활성이면 분 단위 값 없음"을 고정했다. 결정 A가 이 계약을 바꾸므로 이름과 의도를 `…estimates_only_short_same_city_walking`으로 바꾸고, 비활성일 때 여전히 `ROUTES_DISABLED`인 경우(다른 도시·대중교통·자동차)와 3km 초과를 같은 시험에서 확인한다. 직선거리 보존 단언은 남겼다.

경로 정책 지문(`policy_fingerprint`)은 바꾸지 않았다. 바꾸면 운영의 기존 일정이 모두 stale이 된다. 배포 전 만든 미리보기는 옛 구간(미확인)을 그대로 적용할 수 있는데, 더 보수적인 값이라 위험은 없다고 판단했다.

### D2 — PG 리뷰 계약 SQL

`src/storage/postgres.py`의 `translate()`는 "알려진 구문만 번역" 원칙이다. 기존 `resume_from`·`strict_pass` 규칙과 같은 형식으로 `observation_profile`을 추가했다. 1차 PG 실행의 실패 15개(`test_stage2_review_contracts` 7, `test_stage1_discovery` 8)가 수정 후 통과했고, `ZERO_SPEND=1`에서도 통과해 원인이 이것임을 확인했다.

### D4 — 로드 중 탭 선택이 초안 복원에 덮임

여행을 불러오는 동안 `restoreDraft()`는 마지막에 `setTab(workspaceNavigationOverride || context.tab)`을 부른다. override는 여행을 직접 바꿀 때만 채워져서, 새로고침 직후 사용자가 누른 탭은 초안의 탭으로 바뀌었다. 초안 저장은 디바운스라 새로고침 직전 화면이 초안에 없을 수 있다.

`web/js/foundation.js`에 `navigate(tab)`을 두고, 탭 버튼 클릭이 로드 중이면 override에 기록한다. 이미 있는 복원 경로가 그 값을 따른다. 여행 전환·초안 복원의 기존 동작과 기존 JS 시험은 그대로다.

수정 전 코드 확인은 Playwright 라우팅으로 HEAD의 `foundation.js`를 대신 내려보내 했다(작업 파일 그대로). 서비스 워커를 막은 상태에서 E01이 첫 실행과 똑같이 실패했고 수정 후 통과했다. E01b(초안 GET 1.5초 지연 + 초안 탭을 "저장한 곳"으로 바꿈)는 수정 전 2회 중 1회 실패해 타이밍에 의존한다. 결정적인 회귀는 JS 단위 시험이 맡는다.

## 묶음 E — 출시 준비

### 메모리

`scripts/measure_stage1_memory.py`로 합성 fixture(로컬 저장소, `MAIL_ANALYSIS_MODE=local`, `ZERO_SPEND`)를 띄워 쟀다. 두 번째 열은 설치 없이 기존 로컬 이미지 `travel-inbox-beta:20261006-tls`(Python 3.13.12, `requirements.lock`과 패키지 일치)에 현재 소스를 읽기 전용으로 붙여 `--memory=512m`으로 돌렸다.

| 시점 | macOS RSS (MiB) | Docker 512MB RSS (MiB) |
| --- | ---: | ---: |
| 기동 직후 | 77.4 | 87.0 |
| 추천 1회 뒤 | 87.6 | 93.9 |
| 로컬 메일 분석 1회(`07-eight-bookings-one-day.eml`) 뒤 | 85.6 | 99.0 |
| Lingua 전 언어, 탐지 5회 (별도 프로세스 최대) | 76.5 | 131.8 |
| Lingua 전 언어 모델 선적재 (별도 프로세스 최대) | 55.7 | 104.3 |

- 서버 프로세스에 Lingua는 적재되지 않았다. Lingua는 관리자 리뷰 연구 작업(`src/research/service.py`)에서만 지연 import된다
- 컨테이너 전체 cgroup 최대치는 첫 실행에서 211.6MiB였다. 두 번째 측정의 cgroup 값은 RSS보다 작게 나와 신뢰하지 않는다
- 512MB 대비 여유가 커서 Lingua 언어 제한은 제안·구현하지 않았다

> [!NOTE]
> 실제 Render 인스턴스가 아니다. 운영은 Supabase·AI 메일 분석(`gpt-5-mini`)·pgvector 경로를 쓰고, 측정은 로컬 SQLite·로컬 메일 파서다. 실측 RSS는 배포 후 Render 대시보드에서 확인해야 한다.

### 배포 차이와 체크리스트

[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)에 `d154dc0` 대비 차이와 사람이 할 순서를 적었다. schema 버전은 둘 다 15이고 `src/foundation/db.py`는 `d154dc0`과 같다.

## make ci — 로컬 실행 결과

마지막 실행은 아래 한 줄이다. PG는 일회용 루프백 컨테이너(`pgvector/pgvector:0.8.7-pg17`, 127.0.0.1:55432, trust)를 썼다.

```bash
TRAVEL_TEST_POSTGRES_DSN=postgresql://postgres@127.0.0.1:55432/postgres make ci PYTEST_ADDOPTS=-rs
```

| 단계 | 결과 |
| --- | --- |
| `ci-safety` | `publish safety: 0 fail, 83 warn (absolute_user_path=76, db_url_loopback_password=5, presentation_script_tracked=2)` |
| `ci-assets` | `Public asset content hashes match index.html` |
| `ci-js` | 220 통과 · 0 실패 (기존 219 + 이번 1). D4 수정 뒤 221 |
| `ci-python` (SQLite) | 1302 통과 · 0 실패 · 20 skip · 225초 |
| `ci-postgres` plugin 묶음 | 282 통과 · 0 실패 · 3 skip · 281초 |
| `ci-postgres` 자체 schema 묶음 (`test_cloud_storage` · `test_postgres_migration_concurrency`) | 16 통과 · 0 실패 |
| `make e2e` (별도, Playwright 설치 뒤) | 8 통과 · 0 실패 · 22.7초 |
| `ci-python` 재실행 (Playwright·D4 뒤) | 1301 통과 · **1 실패** · 25 skip · 29분 36초 |

마지막 재실행은 iCloud가 파일을 다시 내려놓은 상태라 29분이 걸렸다. 실패한 `test_v5_concurrent_workload.py::test_five_users_overlap_documents_and_recommendations`(409 ≠ 202)는 이번 세션이 건드리지 않은 v5 시험이다. 단독으로 5회 돌려 4회 통과했고, 실패한 1회는 33초가 걸린 느린 실행이었다(평소 9초). 입출력이 느릴 때 드러나는 경합일 수 있어 후속 조사 대상으로 남긴다.

### 원격 병합(`27bdfaa`) 뒤

병합이 바꾼 것은 `docs/service-v4/IMPLEMENTATION_STATUS.md` 1개와 발표 자료 38개다. `src`·`api.py`·`web`·`tests`·`scripts`·`Makefile`·`.github`·`requirements*`는 병합 전 HEAD(`50d705a`)와 바이트 단위로 같다(`git diff --quiet HEAD^1 HEAD -- …` 확인).

| 단계 | 결과 |
| --- | --- |
| `ci-safety` | 0 fail · 81 warn (절대경로 76 · 루프백 DSN 5). 추적 대본 경고는 0이 됨 |
| `ci-assets` | 통과 |
| `ci-js` | 1회차 1 실패(`node_modules/fake-indexeddb/package.json`이 iCloud dataless) → 내려받은 뒤 221 통과 |
| `ci-python` (SQLite) | 1302 통과 · 0 실패 · 25 skip · 220초 |
| `ci-postgres` plugin | 281 통과 · 1 오류 · 3 skip. 오류는 `test_public_discovery.py::test_network_wait…`의 시작 단계 `ssl.SSLError: NO_CERTIFICATE_OR_CRL_FOUND` — certifi 인증서 파일이 dataless라 빈 내용으로 읽힘 |
| `ci-postgres` 자체 schema · plugin 오류 1개 재실행 | **미완료** — 22분간 시험 수집 단계에서 iCloud 대기(CPU 0%)로 중단. 같은 코드로 병합 전 `make ci`에서 16 통과 |
| `make e2e` | **미실행** — 같은 이유. 같은 코드·화면으로 병합 전(`1c9a75e`) 8 통과. 병합은 웹 자산을 바꾸지 않음 |

> [!WARNING]
> 병합 뒤 시험이 느리고 일부가 막힌 원인은 디스크다. 데이터 볼륨이 97~98% 차(여유 11~14GB) iCloud가 받은 파일을 곧바로 다시 내려놓았다. 일괄로 다시 받으면 내려간 파일 수가 5,677 → 13,822로 오히려 늘어 중단했다. 이 폴더에서 안정적으로 시험하려면 디스크 여유를 늘리거나 저장소를 iCloud 밖으로 옮겨야 한다.

### 이번 세션의 실행 이력

중간 실패를 숨기지 않으려고 순서대로 남긴다.

| # | 실행 | 결과 | 원인 · 조치 |
| ---: | --- | --- | --- |
| 1 | 변경 전 전체 Python | 출력 0, 중단 | iCloud dataless 대기 |
| 2 | 대상 4파일 (변경 후) | 83 통과 | — |
| 3 | 전체 Python, `ZERO_SPEND=1` | 16% 지점까지 실패 누적, 중단 | `ZERO_SPEND` 충돌 + iCloud |
| 4 | PG plugin, `ZERO_SPEND=1` | 25 실패 · 257 통과 · 3 skip | `ZERO_SPEND` 10 + D2 15. 자체 schema 묶음은 `&&`로 미실행 → 둘 다 돌도록 수정 |
| 5 | 전체 Python, `ZERO_SPEND` 없음 | 7 실패 · 1295 통과 · 20 skip | 7개 모두 iCloud(`google/protobuf`) 대기로 작업 10초 초과. 내려받은 뒤 통과 |
| 6 | PG, D2 수정 후 | plugin 282 통과 · 3 skip / 자체 15 통과 · 1 실패 | D3 발견 → 수정 |
| 7 | `make ci` 1회차 | SQLite 1 실패 · 1318 통과 · 3 skip | `make ci`가 DSN을 SQLite 단계에도 넘김 → `ci-python`에서 DSN 비우도록 수정 |
| 8 | `make ci` 2회차 | 위 표 | — |
| 9 | E2E 1회차 (Playwright 설치 직후) | 4 실패 · 3 통과 | D4 앱 결함 1 + 시험 코드 3 → 수정 |
| 10 | E2E 4회차 이후 | 8 통과 | — |
| 11 | `ci-python` 재실행 | 1 실패 · 1301 통과 · 25 skip | 동시 작업 시험의 타이밍 민감성(위 설명). 단독 5회 중 4회 통과 |

## 미실행과 이유

| 항목 | 이유 |
| --- | --- |
| 변경 전 Python 전체 기준선 | iCloud 대기로 멈춤 |
| GitHub Actions | 로컬에서 실행 불가. push는 사람 |
| GitHub Actions `e2e` job (Linux) | push는 사람 |
| `typescript` 검사 | 설치 승인 필요. 계획만 |
| push | 사람이 한다 |
| 동시 작업 시험 409 원인 조사 | 범위 밖. 느린 입출력에서만 재현 |
| 실제 Render 메모리 · 운영 로그인 | 배포 금지 |

## 대표에게 넘길 일

1. push — 병합(`27bdfaa`)은 끝났고 원격이 조상이라 fast-forward다. push 직전 `git fetch`로 원격이 그대로인지 확인
2. push 뒤 Actions 6개 job 결과 확인 (특히 Linux에서 처음 도는 `e2e`)
3. 새로 올라갈 절대경로 13줄 처리 (추적 중인 발표 대본은 병합으로 0개)
5. 디스크 여유 확보 뒤 `make ci`·`make e2e` 재실행 — 병합 뒤 PG 자체 시험과 E2E가 iCloud 대기로 미완료
4. Render 수동 배포와 `/health/ready`·로그인 확인, 실측 메모리 기록
