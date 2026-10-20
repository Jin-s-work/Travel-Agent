# 1단계 보고서 — 기준선: 보존 · CI · E2E · 결함

> v6 1단계 프롬프트(`prompts/01-baseline-ci-e2e.md`)를 실행한 기록. 바꾼 파일, 실제로 돌린 명령과 결과, 돌리지 못한 것, 대표에게 넘길 일을 담는다.

2026-10-20 · Claude Code 세션 · 로컬 커밋까지만 · push·Actions·배포·패키지 설치 없음

**세 줄 요약**

- v5 작업을 로컬 커밋 11개로 묶고, 게시 점검 스크립트·CI 워크플로·`make ci`를 추가했다. `make ci` 로컬 실행 결과 Python 1302 · JS 220 · PG 282+16 통과, 실패 0이다.
- 결함 3건을 고쳤다: 기본 배포의 이동시간 전부 미확인(G4, 결정 A 반영), PG에서 500이 나던 리뷰 계약 SQL, 오래된 schema 12 재현 시험. 결함마다 회귀 시험을 붙였다.
- 브라우저 E2E E01~E05는 Playwright 설치 승인이 없어 골격만 있다. 대신 합성 OIDC 로그인과 E02 HTTP 격리는 실제 서버로 돌려 통과했다. 원격은 로컬보다 25커밋 앞서 있어 push 전 병합이 필요하다.

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

파일을 병렬로 읽어 내려받았다(설치·재생성 아님). 처리량은 동시 128~256개에서 초당 약 8파일이었다. 1단계 마지막 확인에서 `.venv`에 남은 dataless 파일은 17개다. 백그라운드 내려받기가 계속 줄이고 있었다.

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
| `.github/workflows/ci.yml` | python · js · assets · postgres · safety 5 job |
| `Makefile` | `ci`, `ci-python`, `ci-js`, `ci-assets`, `ci-postgres`, `ci-safety`, `e2e` 추가. 기존 타깃 그대로 |
| `src/itineraries/travel_time.py` | 비활성 제공자일 때 도보 추정 분기에 도달 (G4) |
| `tests/test_location_providers.py` | `ROUTES_DISABLED` 기대 시험을 결정 A 계약으로 갱신 |
| `tests/test_itinerary_api.py` | 기본 배포 배선으로 strict/잠정안 회귀 시험 추가 |
| `web/js/foundation.js` · `web/index.html` · `web/sw.js` | 추정 구간 문구 "직선거리 기준 추정", 자산 hash |
| `tests/test_discovery_ui.cjs` | 추정 구간 문구 회귀 시험 |
| `src/storage/postgres.py` | `json_extract(…,'$.observation_profile')` PG 번역 |
| `tests/test_postgres_translate.py` | 번역 회귀 시험 2개 |
| `tests/test_cloud_storage.py` | schema 12 재현에 `place_photo_cache` 포함 |
| `scripts/stage1_browser_fixture.py` | `STAGE1_APP_PORT`·`STAGE1_IDP_PORT`로 포트 지정. 기본값 8766/8767 그대로 |
| `tests/e2e/` | 빈 포트 서버 fixture, 합성 OIDC 로그인, E02 HTTP, E01~E05 브라우저 골격 |
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

로컬 작업본은 원격 내용을 바탕으로 더 나아간 상태로 보인다. 그래도 병합은 하지 않았다. 원격은 발표 대본 2개를 이미 지웠고 발표 자료 v5~v9를 공개했는데, 로컬 발표 파일과 내용이 달라 병합 방식이 공개 범위를 바꾸기 때문이다. 권장 절차는 [출시 체크리스트 2절](RELEASE_CHECKLIST.md#2-원격과-갈라진-이력--사람이-결정)에 있다.

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
| E2E opt-in (`GOING_E2E=1`) | 2 | 아직 job 없음 |
| Playwright 미설치 (브라우저 골격 모듈) | 1 | 승인 후 `e2e` job |

인수인계의 과거 기록(1264 통과 / 17 skip)과 PG 필요 17개가 같다. 늘어난 3개는 이번에 추가한 E2E다.

PG 실행의 skip 3개는 SQLite 전용 리허설·다운그레이드·아카이브 시험이며 SQLite 실행이 덮는다.

### 선택 과제 — `@ts-check` 계획 (미실행)

`web/js` 12파일 2,662줄에 `@ts-check`가 0개다. `typescript`는 설치 승인 전이라 계획만 적는다.

1. `jsconfig.json`(checkJs, `lib: ["dom","es2022"]`, `noEmit`)을 둔다
2. 작은 순수 모듈(`offline-store.js`)부터 파일 머리에 `// @ts-check`, API 응답에 JSDoc `@typedef`
3. `npx tsc --noEmit -p jsconfig.json`이 0 오류인 파일만 CI js job에 포함
4. `foundation.js`(가장 큼)는 마지막. 오류 수를 줄여 가는 방식으로 기록

## 묶음 C — E2E

Playwright 설치 승인을 요청했으나(2026-10-20, 상위 세션 경유) 이 보고 시점까지 답이 없어 설치하지 않았다. 요청 내용은 `playwright==1.63.0`(PyPI 2026-09-15, macOS arm64 wheel 약 43MB)와 Chromium 1개이며, `pytest-playwright`는 의존성을 줄이려고 빼고 자체 fixture로 대체한다.

| ID | 상태 | 근거 |
| --- | --- | --- |
| 서버·로그인 | 통과 (HTTP) | 빈 포트로 fixture를 띄우고 합성 IdP와 실제 authorize/callback 왕복, A·B 각각 로그인 |
| E02 | **HTTP 절반 통과** | B가 A의 여행·예약·북마크·조건·일정·숙소·문서·작업 URL에 접근 → 전부 404, 응답에 A 여행 제목 0 |
| E01 · E03 · E04 · E05 | 미실행 | Playwright 미설치. 시나리오와 통과 기준은 `tests/e2e/test_golden_path_browser.py`에 고정 |

```bash
make e2e
# 출력: 2 passed, 1 skipped (Playwright not installed)
```

포트는 항상 빈 포트를 새로 잡는다. 사용자가 8766/8767에서 fixture를 띄워 둬도 건드리지 않는다. 스크린샷은 없다(`docs/service-v6/reports/e2e/` 미생성). CI `e2e` job은 E2E 통과가 조건이라 추가하지 않았다.

## 묶음 D — 결함

심각도 순이다. 결함마다 회귀 시험을 붙였고, 회귀 시험이 수정 전 코드에서 실패하는 것을 확인했다.

| # | 결함 | 심각도 | 수정 | 회귀 시험 |
| --- | --- | --- | --- | --- |
| D1 | 기본 배포(`DisabledRouteProvider`)에서 모든 이동 구간이 `ROUTES_DISABLED` | 높음 — 운영 일정 전부 미확인 | 분기 순서 변경 | `test_disabled_route_provider_estimates_only_short_same_city_walking`, `test_default_disabled_routes_estimate_walking_only_in_provisional_mode`, JS 문구 1 |
| D2 | v5 리뷰 계약 조회 `json_extract(evidence_json,'$.observation_profile')`가 PG에서 `42883` → 관리자 리뷰 경로 500 | 높음 — Supabase에서만 발생 | `translate()`에 번역 규칙 1줄 | `tests/test_postgres_translate.py` (모든 `json_extract` 사용처 검사) |
| D3 | `test_cloud_storage`의 schema 12 재현이 schema 15 테이블을 안 지움 → 이관 시 `table place_photo_cache already exists` | 보통 — 시험 결함, 원격에도 같음 | 재현 목록에 1개 추가. 기대값 불변 | 해당 시험 자체 |

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
| `ci-js` | 220 통과 · 0 실패 (기존 219 + 이번 1) |
| `ci-python` (SQLite) | 1302 통과 · 0 실패 · 20 skip · 225초 |
| `ci-postgres` plugin 묶음 | 282 통과 · 0 실패 · 3 skip · 281초 |
| `ci-postgres` 자체 schema 묶음 (`test_cloud_storage` · `test_postgres_migration_concurrency`) | 16 통과 · 0 실패 |
| `make e2e` (별도) | 2 통과 · 1 skip (Playwright 미설치) |

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

## 미실행과 이유

| 항목 | 이유 |
| --- | --- |
| 변경 전 Python 전체 기준선 | iCloud 대기로 멈춤 |
| GitHub Actions | 로컬에서 실행 불가. push는 사람 |
| 브라우저 E2E E01·E03·E04·E05, E02 브라우저 절반, 스크린샷 | Playwright 설치 승인 대기 |
| CI `e2e` job | E2E 통과가 조건 |
| `typescript` 검사 | 설치 승인 필요. 계획만 |
| 원격과 병합 | 공개 범위 결정이 섞여 대표 결정 |
| 실제 Render 메모리 · 운영 로그인 | 배포 금지 |

## 대표에게 넘길 일

1. 원격 25커밋과의 병합 방식 결정 (발표 자료는 원격 유지 권장) → `make ci` → push
2. push 뒤 Actions 5개 job 결과 확인
3. Playwright 설치 승인 여부
4. 새로 올라갈 절대경로 13줄 처리, 추적 중인 발표 대본 처리(원격은 이미 삭제)
5. Render 수동 배포와 `/health/ready`·로그인 확인, 실측 메모리 기록
