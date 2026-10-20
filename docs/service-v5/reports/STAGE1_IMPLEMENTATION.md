# v5 1단계 구현 기록

2026-10-08. 범위는 **여행 맥락 → 탐색 → 저장 → 일정**이다. 추천 모델은 기존 `hybrid_v5`를 유지한다. 실제 후보 수집 확대, 새 리뷰 수집·권리 승인·언어 품질 평가, 운영 배포는 수행하지 않았다.

## 작업 기준과 보존

- 요청 저장소: `~/Desktop/ChatGPT/travel-inbox-rag`.
- 원본은 `codex/private-beta-launch`, HEAD `5da54dd`와 많은 기존 변경을 보유했다. reset/stash/강제 checkout을 하지 않았다.
- 파일 읽기가 지연되는 원본 대신 기존 네이티브 체크아웃 `/private/tmp/going-hybrid-verify.uPktYX`, 같은 이름의 브랜치 HEAD `df01153`에서 구현·시험했다. 이 HEAD에는 직전 로그인·초대 제한 제거가 포함되어 있다.
- 적용 가능한 AGENTS.md 없음. v5 계획·v4 진행 기록·기존 실행/인증/작업/예산/일정 계약을 확인했다. `emil-design-eng`의 기존 토큰·포커스·즉각적 피드백 원칙을 적용했다.
- README·발표·스크립트·운영 secret·외부 데이터는 수정하지 않았다. 로컬 합성 사용자/임시 SQL/가짜 공급자로 검증한다.

## 구현 범위

### A. 서버가 판단하는 여행 맥락

`inherited_context_v2`는 도시 현지 오늘 → 미래 시작일 → 종료 여행의 이전 선택일/시작일을 구분하며 `date_reason`을 반환한다. 기존 도시 구간의 양 끝 날짜 포함 계약을 재사용한다. 경계일에 두 도시가 겹치면 한 도시를 선택해야 하고, 빈 날짜·삭제된 구간은 해당 필드의 오류로 반환한다. 숙소 출발점 결정기는 기존 `src/accommodations/origin.py`를 재사용한다.

`POST /api/v2/trips/{trip_id}/discovery-context/preview`는 사용자 소유권, 여행 버전, 전달된 방문 조건 버전을 검사한다. 쓰기·job·외부 호출 없이 도시/날짜/인원/출발점을 반환한다. owner_id는 클라이언트 입력을 받지 않는다. `actor_id`, `trip_context`, `conditions`, `overrides`, `provenance`, `selection`, `validation`, `warnings`, `origin_context`, `context_version`을 제공한다.

기본 화면은 요약과 **조건 수정**을 제공한다. 작은 패널에서 날짜·도시·인원·출발점을 조정하고, 취향·가격·아동·필수 조건은 기존 상세 패널에서 편집한다. 겹치는 숙소는 이름으로 선택할 수 있다. 패널을 닫으면 적용 전 입력은 현재 여행 메모리에만 남으며, 여행/계정 전환 시 정리한다. 적용 전 서버 preview와 실제 제출 모두 버전을 검증하고, 409에서 초안을 유지한 채 최신 조건을 비교할 수 있다.

미입력 인원은 `null`이다. 가격 환산과 인원 조건은 `PARTY_UNKNOWN`으로 남긴다. 기존에 명시적으로 저장한 1명은 변경하지 않는다. 네 자리 달력 날짜를 서버에서 확인하고 `0000`, 여섯 자리 연도, 잘못된 윤일, 숫자 timestamp의 날짜 전환을 거절한다.

상위 경로는 **찾기 / 저장한 곳 / 일정 / 예약**이다. 여행에 물어보기 버튼을 유지하고 로고에서 여행 요약과 질문 입력란으로 이동한다. 삭제된 마지막 여행은 다른 여행을 임의 선택하지 않고 여행 선택 화면으로 돌려보낸다.

### B. 독립된 추천 구획과 저장된 결과 페이지

홈은 현지어 리뷰와 유명한 곳을 각각 최대 2개씩 보여준다. 지도 참고 자료는 별도 둘러보기 경로다. 최초 결과는 사용 가능한 구획으로 안내하지만, 직접 선택하거나 복원한 구획은 자동 전환하지 않는다. OFF와 UNKNOWN이 함께 발생하면 `disabled`가 품질 부족에 우선한다. 빈 카탈로그도 기능 OFF 원인을 보존한다.

새 결과 계약 `discovery_result_v1`은 요청당 **최대 200개 고유 지점 후보**를 저장한다. 리뷰 기록 200건 상한과 별개다. 기존 점수 계산을 유지하고 기존 다양성 선택을 이어지는 묶음에도 적용한다. 첫 페이지와 추가 페이지 크기는 12다. 경로 수집은 최대 12후보와 기존 공급자 한도 중 작은 값을 사용한다. 후보 풀을 늘렸다는 이유로 경로 200곳을 호출하지 않는다.

- 새 조회: `GET /api/v2/trips/{trip_id}/recommendations/{run_id}?page_size=12`.
- 추가 조회: 위 주소에 `section=local_discovery|landmark|reference&cursor=...`.
- 기존 무페이지 API는 이전 표시 한도(핵심 구획 요청 limit, reference 12)를 유지한다. 내부 비교·일정 검증기는 전체 저장 결과를 읽는다.
- HMAC cursor는 사용자·여행·run·조건 snapshot hash·조건 version·구획·offset·현재 표시 가능한 ID 집합에 묶인다. 다른 사용자/삭제 여행은 404, 다른 run/변조/철회 뒤 cursor는 409다.
- GET·탭 전환·사진 넘기기·추가 보기는 추천/사진 수집 job을 만들지 않는다. v5에서 기존 자동 사진 수집 트리거를 끄고 허용된 저장 사진만 표시한다.
- 추가 보기는 기존 DOM 뒤에 붙이고 중복 ID를 제거한다. 같은 현재 snapshot을 다시 읽으면 이미 읽은 순서를 유지한다. 출처가 철회되거나 결과가 바뀌면 이전 페이지를 재사용하지 않는다.

DTO는 `job_state / availability_state / freshness_state / result_count / blocking_reason / warnings / next_actions`를 각각 반환한다. 구획별 상태도 별도다. 기존 job 상태는 변경하지 않는다. 실제 provider fetched count가 없으면 `null`이며 수집 성공이나 200곳 확보로 바꾸지 않는다.

`candidate_counts`: requested_limit, fetched_count, unique_count, deduped_count, eligible_count, scored_count, displayable_count, displayed_count, deferred_count, evaluated_count, stored_candidates_considered, coverage, truncation_reason. 카탈로그·평가 수는 저장 snapshot 기준, 표시 수는 권한 재확인 후 응답 기준이다. coverage에는 기존 provider_limit=60과 실제 제공 범위를 유지한다. 각 구획 12개를 포함한 첫 응답을 받아도 화면에는 선택한 구획만 렌더링한다.

카드는 선택 이유 최대 2개, 우선 확인 1개, 실제 거리/가격, 저장 행동을 앞에 둔다. 현지어 카드에 본문·판별·미판별 수와 관측 기간/확인일을 표시한다. 사진은 최대 3장 중 현재 장만 src를 연결하며, 없음/실패/만료를 구분한다. 사진 없는 카드는 큰 빈 공간을 만들지 않는다. 원어명·주소 복사와 지도 링크를 상세에서 함께 제공한다. 사진 출처/라이선스는 유지한다.

### C. 저장과 일정

기존 SQL 저장 중복 방지·소유권 검사를 재사용한다. 저장 성공 응답을 받은 뒤 보관함 재조회가 실패하더라도 저장을 실패로 되돌리지 않는다. 홈·탐색·상세는 동일 보관함 상태를 사용한다.

보관함에서 장소 여러 개와 방문일을 고르고 **N곳 일정 미리보기**로 기존 생성 preview를 연다. 날짜·선택·인원·조건을 다시 입력하지 않는다. 체류시간과 잠정안 허용은 기존 검토 단계에서 확인한다. 기존 preview/apply/undo·고정 예약 보호·삭제/버전/근거 재확인을 유지하며, 외부 예약 취소는 실행하지 않는다. 여행 한정 제외/해제도 기존 API를 재사용한다.

## Before / After / Why

| Before | After | Why |
| --- | --- | --- |
| 도시·날짜·인원·출발점 조작이 기본 화면 여러 곳에 분산 | 요약 + 조건 수정 패널, 서버의 같은 resolver | 입력 중복과 서로 다른 날짜 판단 감소 |
| 누락 인원이 1명으로 보임 | 미정으로 보존, 필요한 판정만 unknown | 가격·인원 적합성 오인 방지 |
| 마지막 여행이 사라지면 첫 여행 선택 | 여행 선택으로 복귀 | 다른 여행을 잘못 편집하지 않도록 함 |
| 홈 3장에 유명한 곳이 먼저 들어감 | 각 추천 방식의 독립 구획 | 현지어 후보가 가려지지 않도록 함 |
| OFF와 UNKNOWN을 품질 부족으로 축약 | 기능 준비 상태와 다른 구획 안내 | 수정할 입력과 서비스 준비 상태 구분 |
| 이미 찾은 후보도 고정 표시 수 밖이면 접근 불가 | 저장 snapshot의 12개 단위 페이지 | 재수집 없이 후보를 더 살펴보기 |
| 보기만 해도 사진 조회 job 생성 | 저장된 사진을 읽음 | 조회와 외부 작업·비용을 분리 |
| 보관함에서 장소별로 일정 연결 | 여러 곳 선택 + 날짜 유지 + 하나의 미리보기 | 이동·반복 입력 감소 |
| 카드마다 긴 설명, 전체 카드 live region | 짧은 이유/미확인, 상태 영역만 알림 | 읽기 부담과 스크린리더 반복 감소 |

## 사용자 행동 수

다음은 코드 경로 기준이며, 브라우저 실측 완료 값이나 사용성 개선율이 아니다. 필드 타이핑 횟수는 제외했다.

| 흐름 | 행동 수 / 변화 |
| --- | --- |
| 기존 여행 → 탐색 | 찾기 1회, 조건 재입력 0. 처음 결과가 없으면 기존 자동 요청 사용 |
| 카드 → 저장 | 저장 1회, 성공은 서버 응답 뒤 확정 |
| 조건 수정 | 열기 1회 + 필요한 값 수정 + 적용 1회 |
| 홈 → 보관함 | 기존 추천 이동+내부 보관함 전환 2회 → 저장한 곳 1회 |
| 보관함 N곳 → 일정 | N곳 선택 + 미리보기 열기 + 검증 요청 + 적용. 검증 확인을 생략하지 않음 |
| 추가 후보 | 12곳 더 보기 1회, 외부 추천 수집 0회 |

## 검증 기록

### 실제 실행 결과

시험은 외부 호출 없는 합성 데이터/가짜 공급자와 임시 저장소에서 실행했다. 네이티브 체크아웃은 원본 폴더의 파일 접근 지연을 피하기 위해 사용했다. 최종 변경 파일 37개의 원본/시험 체크아웃 바이트 일치를 확인하고 [해시 목록](stage1-source-hashes.json)에 남겼다. 원본 전체의 기존 변경과 테스트되지 않은 운영 환경까지 같다는 뜻은 아니다.

| 실행 위치 / 명령 | 실제 결과 | 기록 |
| --- | --- | --- |
| 네이티브: `PYTHON_DOTENV_DISABLED=1 /private/tmp/going-stage1-native313/bin/python -m pytest -q` | **1,241 passed, 17 skipped**, 2 warnings / 181.80초 | [Python 로그](stage1-python-tests.log) |
| 네이티브: `node --test tests/*.cjs` | **219 passed, 0 failed** | [JS 로그](stage1-js-tests.log) |
| 원본: `PYTHON_DOTENV_DISABLED=1 /private/tmp/going-stage1-native313/bin/python -m pytest tests/test_v5_context.py tests/test_v5_result_pages.py -q` | **22 passed**, 2 warnings / 151.04초 | [원본 API 로그](stage1-original-python-tests.log) |
| 원본: `node --test tests/*.cjs` | **219 passed, 0 failed** | [원본 JS 로그](stage1-original-js-tests.log) |
| 원본: `python3 scripts/version_web_assets.py --check` | 통과, public asset hash 일치 | 마지막 소스 동기화 후 실행 |
| 네이티브: `git diff --check` | 통과 | 작업 중 공백 오류 없음 |

17개 skip은 실행 환경에 필요한 임시 PostgreSQL/pgvector 또는 선택 언어 판별기 의존성이 없는 시험이다. 이번 결과를 실제 Supabase/운영 DB 검증으로 해석하지 않는다. 경고 2개는 기존 Starlette/Authlib 의존성 경고다. 원본 폴더에서는 전체 Python 회귀 대신 새 API 시험을 재실행했다.

실패 이력도 보존한다. 첫 전체 회귀는 1,235 통과/4실패였고, 기존 API 표시 한도를 바꾼 문제였다. 새 페이지 API와 기존 unpaged `legacy_view`를 분리해 수정했다. 다음 실행은 1,239 통과/1실패였으며 순수 합성 결과에 summary가 없는 경우를 보완했다. 위 최종 전체 회귀에서 모두 통과했다. 원본 JS의 손상된 `fake-indexeddb` 설치는 `npm ci --ignore-scripts --no-audit --no-fund`로 복구했다. 파일 버전 생성이 끝나기 전 실행한 시험은 hash 불일치 1건이 있었으며, 생성 완료 후 최종 219개가 통과했다. 의존성 lockfile은 변경하지 않았다.

### U01–U12 추적

아래 ‘통과’는 명시한 API 또는 DOM harness의 실제 실행 결과다. 로그인 이후 실제 브라우저 조작과 화면 크기/포커스 검증은 별도이며 아직 완료되지 않았다.

| ID | 자동 시험/구현 결과 | 근거 | 브라우저 |
| --- | --- | --- | --- |
| U01 | 숙소·취향 없이 기존 여행 조건 상속, 인원 미정 유지 통과 | `test_discovery_intents.py`, `test_v5_context.py`, `test_discovery_foundation.py` | 미실행 |
| U02 | 현지 오늘/미래/종료 여행, 경계 중복·빈 구간·삭제 구간 통과 | `test_v5_context.py` | 미실행 |
| U03 | 0000/6자리 연도/윤일 거절, 서버 오류 후 입력 보존 통과 | `test_v5_context.py`, `test_v5_simple_ui.cjs`, 기존 날짜 입력 시험 | 모바일 입력 미실행 |
| U04 | 유명 후보3+현지어1에서 두 구획 독립 노출 계산 통과 | `test_v5_simple_ui.cjs`, `test_home_ui.cjs` | 시각 검증 미실행 |
| U05 | OFF+UNKNOWN 및 빈 카탈로그에서 disabled 우선, 대안 상태 통과 | `test_v5_result_pages.py`, `test_stage2_discovery_ui.cjs` | 미실행 |
| U06 | 3개 preview 역순 응답, 계정/여행 전환, 이전 결과/초안 보존 통과 | `test_v5_simple_ui.cjs`, `test_discovery_flow_ui.cjs`, `test_workspace_ux.cjs` | 미실행 |
| U07 | 동시 동일 요청의 한 run, 동일 key의 다른 입력409 통과 | `test_recommendation_api.py`, `test_recommendation_persistence.py`, `test_reliability_api.py` | API 검증 |
| U08 | 완료 후 결과 GET 재시도·기존 run 복원·SSE 이어받기 통과 | `test_recommendation_progress_ui.cjs`, `test_recommendation_api.py`, `test_reliability_api.py` | 새로고침 조작 미실행 |
| U09 | 저장 더블클릭1요청·POST 실패 재시도·성공 후 GET 실패 시 저장 유지·다른 여행 늦은 응답 차단 통과 | `test_v5_simple_ui.cjs`, `test_discovery_foundation.py`, `test_home_ui.cjs` | 화면 간 일관성 미실행 |
| U10 | 저장 장소/날짜 연결 구현, preview/apply 충돌과10회undo 및 여행별 초안 복원 통과 | `test_itinerary_first_preview_ui.cjs`, `test_itinerary_api.py`, `test_workspace_ux.cjs` | 전체 연결 조작 미실행 |
| U11 | 기존 포커스/키보드/반응형 토큰 재사용, **수용 시험 미완료** | 390px·200%·긴 다국어명·키보드 실측이 필요 | 차단으로 미실행 |
| U12 | 계정 namespace·로그아웃·삭제/늦은 저장·오프라인 정리 통과 | `test_travel_offline.cjs`, `test_workspace_ux.cjs`, `test_recommendation_persistence.py` | 계정 전환 조작 미실행 |

추가로 `test_v5_result_pages.py`는 200개 고유 후보를 12×16+8의 17페이지로 모두 읽고, 새 SQL 연결에서도 순서/ID 중복 없음과 job 수 불변을 검증했다. 다른 사용자/여행/run/cursor 변조/출처 철회는 재사용을 거절한다. 실제 공급자의 200개 수집 증거가 아니다. 기존 사진/비교/예산/예약 보호 시험도 전체 회귀에 포함되었다.

### 다음 검증 순서

1. 저장된 브라우저 차단 해제 후 아래 fixture로 로그인 및 U01–U12 실제 화면 흐름을 실행한다.
2. 390px/넓은 화면/200% 확대에서 일본어·스페인어·카탈루냐어 이름, 모달 포커스와 키보드 전 경로를 확인한다.
3. 별도 임시 PostgreSQL/pgvector 환경에서 skip 시험을 실행한다. 운영 DB를 테스트 대상으로 사용하지 않는다.
4. 여기까지 확인한 뒤 2단계의 실제 후보 확대·리뷰/사진 공급·모델 비교에 착수한다.

## 브라우저 상태

Chrome 확장 브라우저에서 `http://127.0.0.1:8766`의 실제 로그인 페이지와 로그인 버튼을 확인했다. 버튼 이후 합성 OIDC `http://127.0.0.1:8767` 접근은 **저장된 사용자 차단 설정으로 거절**됐다. 사용자가 해제 예정이라고 답한 후에도 같은 차단이 감지됐다. 포트 변경, 인증 쿠키 주입, 다른 브라우저 경로 등 우회는 하지 않았다.

따라서 로그인 후 여행 생성→조건 변경→저장→상세→일정 preview/apply→새로고침→undo, 390px/200%/다국어/키보드/포커스의 실제 화면 검증은 **미완료**다. DOM harness와 API 회귀 통과는 이 브라우저 검증을 대신한 완료 증거가 아니다. iOS Safari·Android Chrome·실제 Google 로그인·운영 서비스는 이번 작업에서 검증하지 않았다.

재실행: `PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/stage1_browser_fixture.py` (앱 8766, 가짜 IdP 8767). 사용자 A/B 각각 도쿄·바르셀로나 여행, 긴 다국어 합성 장소와 고정 예약을 사용하는 기존 fixture다. OIDC 차단 해제 후 위 전체 흐름을 실행하고, `tests/test_v5_result_pages.py::result200` 합성팩은 페이지 검증에 재사용한다. 운영 원문·토큰·계정으로 대체하지 않는다.

## 호환성과 롤백

- 새 SQL 테이블이나 파괴적 마이그레이션 없음. 기존 recommendation_runs JSON·discovery_contexts·bookmarks·일정 revisions를 재사용한다.
- `SIMPLE_DISCOVERY_ENABLED=false`는 새 요약 패널·주 메뉴·페이지 클라이언트의 운영 전환용 완화 스위치다. 모든 변경을 되돌리는 스위치는 아니다. 서버 날짜/소유권/버전 검증과 수정된 상태 분류는 유지된다.
- 전체 코드 롤백은 작업 전 코드와 맞는 이미지를 사용한다. 이 단계에서 push/배포/이미지 교체를 하지 않았으므로 운영 롤백은 실행하지 않았다.
- `null` 인원은 명시적인 새 계약이다. 1명을 전제로 한 이전 버전으로 무조건 되돌리지 말고 nullable 대응 백엔드를 함께 유지한다. 사용자가 입력하지 않은 인원을 일괄 1명으로 채우지 않는다.
- 기존 무페이지 GET은 작은 응답을 유지하므로 이전 프론트가 200개 카드를 한 번에 그리지 않는다.

## 2단계에 전달할 것과 남은 제한

- `src/discovery/context.py`, `ContextPreview`, `web/js/simple-discovery.js`: 서버 맥락과 초안 계약.
- `src/recommendations/pages.py`: 결과 버전, 후보/표시 수, cursor, 독립 상태.
- `tests/test_v5_context.py`, `test_v5_result_pages.py`, `test_v5_simple_ui.cjs`: 날짜·A/B 소유권·200후보/17페이지·변조/철회·역순 응답·초안 복구.
- 공개지도 공급자는 현재 최대 60개와 기존 범위를 유지한다. 실제 200개 수집은 미완료다. 실제 fetched_count가 제공되지 않는 경로는 null이다.
- 실제 후보/사진 확보 수, 사용 권리, 언어 판별 품질, 추천 만족도는 이번 작업에서 측정하지 않았다. 엄격 현지어 기능을 새로 활성화하지 않는다.
- 후보 200개는 평가 후보 상한이며 200개 통과/추천 보장이 아니다. 일정 후보는 기존 별도 상한 30개를 유지한다.
- 브라우저 검증이 차단되어 출시 준비 완료로 판단하지 않는다. 2단계 시작 전 실제 화면 검증을 마무리해야 한다.
