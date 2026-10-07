# Travel Agent 구현·검증 상태

마지막 갱신: **2026-10-07**. 1~18절 구현의 기준 Git revision은 `aefee57`이며 당시에는 미커밋 변경이었다. 현재 배포 준비와 Git 반영 상태는 19절을 따른다. 현재 Render Free + Supabase Free 전환은 14절을 따른다. Supabase hii의 private 버킷은 생성했다. 작업 시작 당시 `docs/service-v2/`가 사용자 미추적 자료였으며 이 상태 문서는 없어 새로 작성했다. 기존 PRD·기술 명세·상세 프롬프트를 보존했다. 적용되는 AGENTS.md는 저장소와 조사한 상위 경로에서 발견되지 않았다.

구현 상태 값: `not_started / partial / complete / blocked`. 실환경 검증 값: `not_run / partial / verified / not_required`.

## 1. 단계별 상태

| 범위 | 구현 | 실환경 검증 | 판정 |
| --- | --- | --- | --- |
| 01 사용자별 여행·예약 기반 | complete | partial | 합성 OIDC 브라우저 흐름과 자동 테스트 완료. 실제 외부 계정·실제 LLM 미검증 |
| 실제 Google 등 OIDC 등록·로그인 | partial | not_run | Authlib 연동 코드·설정 안내 있음. 현재 저장소 환경의 `auth_configured=false` 확인 |
| 기존 자료 이관 도구·백업·복원 | complete | partial | 합성 이관·복원 테스트 통과. 기존 메일 7개 inventory만 실행, 실제 이관/복원 미실행 |
| 02 내구성 작업·장애 복구 | complete | partial | 영구 큐·fencing·검색 세대·비용·SSE·복구 화면 및 합성 장애 시험 완료. 라이브 제공자·운영 환경 미검증 |
| 03 리뷰 수집·원문 언어·근거 화면 | complete | partial | 교체 가능한 공급자·작업/예산/정책·화면·180개 신규 시험 완료. 실제 수집·이용권·도시별 리뷰 품질은 not_run, 운영 OFF |
| 04 장소 보관·종합 추천·비교 | complete | partial | 저장/API/점수/화면·합성 E2E 완료. 실제 후보 각 도시 6곳 검수, 운영 사용 승인 대기 |
| 05 일정 생성·편집·되돌리기 | complete | partial | migration6·시간/제약 엔진·작업·편집/undo·합성 브라우저 검증 완료. 실제 경로/외부 계정은 미검증 |
| 06 운영 코드·백업·복원·용량 검증 | complete | partial | migration7·운영 launcher/controls/health·암호화 백업/삭제 checkpoint·Docker5명 부하·브라우저 검증 완료 |
| Render Free + Supabase 저장 전환 | complete | partial | PostgreSQL/Storage/pgvector·복구·이관 코드 및 로컬 검증 완료. hii private 버킷 생성. 14절 |
| 07-A 예약 준비·ICS·문의 초안 | complete | partial | migration9·증빙 대조·규칙/날짜 계산·버전·합성 브라우저 검증. 실제 시설/캘린더 앱 미검증. 15절 |
| 07-B 상황별 Plan B | complete | partial | 미래 선택 방문의 영향 구간 preview/apply/undo 검증. 실제 날씨·대기 공급자 미연결. 15절 |
| 07-C 오늘·선택 오프라인 저장 | complete | partial | 서버 종료/새로고침·선택 snapshot·권한 재확인·두 탭/계정 변경 검증. 운영 OFF, 실제 모바일 미검증. 15절 |
| 08 피드백·최소 분석·예상 지출·평가 | complete | partial | schema10·기록/신고/분석 동의/가격/평가·SQLite/PG/브라우저 합성 검증 완료. 실사용 성과 미측정. 16절 |
| 실제 비공개 개방 | blocked | not_run | 기존 Render URL 확인, 새 코드 배포/DB 비밀/OIDC/실제 외부 백업 검증 대기. 유료 리소스 없음 |

`01 complete`는 본 문서의 구현 범위 판정이다. 실제 사용자에게 비공개 베타를 출시할 수 있다는 판정이나 64개 전체 인수 테스트 통과를 뜻하지 않는다.

## 2. 구현된 사용자 행동

- 초대받은 이메일로 OIDC 로그인하고 사용자별 세션을 발급받는다. 이미 가입한 계정은 새 초대 없이 다시 로그인한다.
- 여행을 생성·수정·전환·삭제하고 도시 구간, 현지 시간대, 성인/아동을 저장한다.
- 메일 없이 수동 예약을 만든다. 날짜만 알면 시각 미확인으로 보존한다.
- `.txt/.eml`을 업로드하고 파일별 성공·중복·거절·실패·검토 필요 결과를 확인한다.
- 한 메일의 여러 예약과 왕복 이벤트를 조회하고, 추출값과 교정값을 비교한다.
- 버전 충돌 시 입력을 유지하고 최신 서버 값과 비교한 뒤 다시 저장한다.
- 날짜/N일차 질문으로 SQL의 해당 예약을 모두 확인한다. 일반 정책·정확한 예약·후속 질문도 현재 교정값을 사용한다.
- 답변 근거의 예약 상세와 원문 텍스트를 연다. 원문 다운로드 API는 인증을 검사하고 attachment로 반환한다.
- 새로고침 후 서버에 저장한 여행·예약·교정값을 다시 읽는다. 개인 대화는 브라우저 메모리에만 있고 새로고침하면 지워진다.
- 로그아웃·세션 만료·계정 전환 시 개인 DOM과 요청 상태를 지운다. 개인 API/원문은 서비스 워커 캐시에 넣지 않는다.

## 3. 01단계 변경 파일과 마이그레이션 이력

| 파일 | 구현 내용 |
| --- | --- |
| `api.py` | app factory, bounded request body, 표준 오류, no-store, 인증된 OpenAPI, 구 개인 API 폐기 |
| `src/foundation/settings.py`, `auth.py` | 운영 fail-closed, Authlib OIDC, 초대·서버 세션·회수, Origin/CSRF |
| `src/foundation/db.py` | `PRAGMA user_version=1` 마이그레이션, users/invitations/sessions/trips/trip_stops/source_documents/document_generations/bookings/booking_events/booking_overrides/deletion_tombstones/processing_receipts |
| `src/foundation/models.py`, `repository.py` | 소유권·복수 예약·시간대·교정·낙관적 잠금·날짜 SQL·삭제 tombstone·세션 검증 후 세대 활성화 |
| `src/foundation/documents.py`, `routes.py` | 비공개 원문 경로, 검증·중복·작업 영수증·재처리·원문·다운로드·v2 API |
| `src/foundation/search.py` | 날짜 전체 조회, 현재 SQL 사실 조립, 여행별 active generation 검색, 후속 질문·출처 |
| `src/agent.py`, `parser.py`, `indexer.py`, `store.py`, `rag.py` | 요청별 context, 복수 예약·왕복 추출, 기존 데이터 선삭제 제거, 메일 지시문 격리 |
| `src/foundation/cli.py` | 초대 관리, 읽기 전용 조사, 명시적 매핑 이관, 체크섬 백업·새 위치 복원 |
| `web/index.html`, `web/js/foundation.js`, `web/css/foundation.css`, `web/sw.js` | 실제 여행/질문/메일/설정 화면, 교정·오류·출처·세션·모바일·앱 셸 캐시 |
| `.env.example`, `Dockerfile`, `Makefile`, `README.md` | 실행 설정, 공용 자동 seed 중단, OAuth code access log 방지 안내 |

구조와 트레이드오프는 [ADR](ADR-001-FOUNDATION.md), 실행·인증·이관·복구 명령은 [실행 안내](FOUNDATION_RUNBOOK.md)를 따른다.

## 4. 01단계 자동 검증 이력

변경 전 기준: 임시 저장소·dummy API key에서 **117 passed**. 기존 익명 개인 API·공용 seed 테스트는 새 비공개 계약에 맞게 401/410·자동 seed 없음 검사로 교체했다. 파서·검색 helper·기존 pipeline 회귀는 유지했다.

01단계 종료 당시 실행(02단계 최종 결과는 8절):

```bash
OPENAI_API_KEY=test SEED_ON_EMPTY=0 .venv/bin/python -m pytest tests -q
# 230 passed, 2 warnings in 7.19s
node --test tests/test_service_worker.cjs
# 2 tests, 2 passed, 0 failed
node --check web/js/foundation.js
node --check web/sw.js
git diff --check
# 모두 exit 0
```

두 Python 경고는 Starlette/Authlib의 httpx 호환 API deprecation이다. 현재 기능 실패는 없으나 의존성 업그레이드 시 재검증한다.

| 인수 범위 | 실제 테스트 | 근거·한계 |
| --- | --- | --- |
| AUTH-01 | `test_foundation_auth.py`, `test_foundation_api.py`, `test_api.py` | 운영 미설정 구/신 개인 API 닫힘, 안전한 cookie 및 csrf/origin |
| AUTH-02, AUTH-06 | `test_foundation_api.py`, `test_foundation_storage.py` | A/B×각 2여행, 타인 trip/booking/doc/job/download/ask/SSE 404, owner_id 거절, 외부 호출 없음 |
| AUTH-03 | 위 테스트 및 `test_session_revoke_during_processing_preserves_old_activation` | 답변 중 회수·변경, 처리 중 회수, 활성화 트랜잭션의 만료/epoch/타인 세션 검사 |
| AUTH-04, AUTH-05 | `test_foundation_auth.py`, `test_foundation_pipeline.py`, API 테스트 | history 역할·개별/전체 크기, 잘못된 Origin/CSRF, 초대 만료/회수/중복·동시 소비, 이메일 검증 |
| AUTH-07 | pipeline/API 병렬 테스트 | A/B 동시 질문·interleaved stream 출처·여행 시작일 분리, 현재 교정값만 전달 |
| AUTH-08 | API/provider error 테스트, 코드 검수 | 공급자 오류 원문 숨김, 초대 POST, 웹 검색에 예약 정보 전송 안 함. 운영 프록시 실제 로그 검증은 미실행 |
| DATA-01 | API 업로드 테스트 | 같은 파일명 서버 ID 분리, 여행 내부 중복, 경로 이동·형식·MIME·크기 거절, 부분 실패·저장 실패 영수증 |
| DATA-02 | parser/storage/API 테스트 | 복수 예약·왕복·재추출 교정 유지·충돌·모호한 매칭·형제 예약 유지·삭제 후 재활성화 방지 |
| DATA-03 | API/storage 날짜 테스트 | 하루 8개+수동 1개를 pagination/top-k와 무관하게 모두 조회, 왕복일 해당 이벤트만 표시 |
| 검색 실제 저장소 | `test_real_chroma_uses_only_active_document_generation_and_current_sql` | 임시 실제 Chroma + 가짜 임베딩. 비활성 generation 제외·현재 SQL 교정 적용 |
| UI-05 | `test_service_worker.cjs` | 워커 코드를 실행해 개인 API/다운로드/POST/외부 URL 캐시 우회, 과거 캐시 제거 검사 |
| 이관·복구 | storage/CLI 테스트 | 가짜 자료의 매핑·재실행·체크섬 오류·삭제 tombstone 반영·세션 무효화. 실제 운영 장애 복구와 다름 |

모든 자동 검증의 API key는 dummy이며 실제 OpenAI/지도/검색 API 비용은 발생시키지 않았다. DB·메일·Chroma 테스트 경로는 임시 디렉터리다.

## 5. 실제 브라우저 검증

`tests/browser_fixture.py`를 loopback 포트 8765/8766에서 실행하고 Codex 내장 브라우저로 조작했다. 별도 합성 OIDC 제공자가 RSA 서명 ID token/JWKS를 제공하며 Authlib의 state·nonce·PKCE·서명 검증 경로를 실제로 통과했다. 메일 추출·임베딩·생성 답변만 가짜 adapter다. 실제 Google 계정 로그인이나 실제 모델 정확도 검증으로 표현하지 않는다.

확인한 흐름:

1. 초대 입력 → 합성 A 로그인 → 도쿄 여행(기간·성인 2명·Asia/Tokyo) 생성.
2. 왕복 항공+투어 문서 업로드 → 예약 2개, 항공 이벤트 2개 보존. PDF 동시 선택은 파일별 거절 표시.
3. 투어 상세 교정 `15:00 → 16:30`, 원문 값과 사용자 교정을 함께 표시.
4. 날짜만 있는 수동 음식점 예약 생성, `시각 미확인` 표시.
5. 1일차 전체 질문 → 3개 예약과 교정된 `16:30`, 출처 3개 표시.
6. 출처/메일 목록 → 원문 텍스트 보기 성공. HTML 실행 없는 pre 표시.
7. 새로고침 후 3개 예약과 교정값 유지, 바르셀로나 생성·여행 전환 때 해당 자료만 표시.
8. 잘못된 종료일 오류를 필드 옆에 표시하고 여행 이름·도시 입력 보존, 수정 후 생성 성공.
9. A 로그아웃 후 예약번호·이름·시각의 잔여 DOM 없음. B 로그인 때 A 여행 0개 노출. B도 도쿄/바르셀로나 두 여행 생성.
10. 합성 B 세션을 만료시킨 후 예약 새로고침 → 만료 안내와 개인 화면 제거. A 재로그인은 새 초대 없이 성공.
11. 다른 편집을 합성 DB에 반영한 뒤 저장 → 409 비교 안내, 입력 `17:00` 유지. 최신 장소값 확인 후 취소 → 원래 `16:30` 유지.
12. 360px 화면 가로 너비/scrollWidth 모두 360 확인. 긴 일본어 이름·시각 표시와 네 가지 메뉴 유지. 변경 후 브라우저 error log 0개.

브라우저 원문 다운로드 버튼은 서버의 200 응답까지 확인했지만 내장 브라우저의 download event가 오지 않아 **OS 파일 저장 완료는 미확인**이다. 대신 원문 보기에서 내용을 직접 확인했고 HTTP 통합 테스트에서 attachment/content/권한을 검사했다. 일반 브라우저의 다운로드 UX는 외부 로그인 설정 후 재검증한다.

브라우저 주요 흐름은 구현 중 정상 수행했고 마지막 백엔드 보완(세션 활성화 트랜잭션·긴 텍스트 chunk)은 최종 자동 테스트로 재검증했다. 완성된 최신 서버로 외부 제공자와 전체 브라우저 회귀를 수행하는 것은 배포 전 체크다.

증거 화면: [여행 화면](screenshots/foundation-desktop.jpg), [360px 화면](screenshots/foundation-mobile.jpg). 합성 자료만 포함한다.

## 6. 기존 데이터 조사와 운영 제한

실행 명령:

```bash
.venv/bin/python -m src.foundation.cli legacy-inventory \
  --source-root data/emails \
  --output data/foundation-legacy-inventory-20261001.json
```

결과: 지원 파일 **7개**, 동일 내용 중복 그룹 **0개**, 소유자 미확정 **7개**. 예약 수는 `null/parse_status=not_run`: 파일 개수를 예약 개수로 간주하지 않고 실제 메일을 모델로 재분석하지 않았다. 본문·예약번호는 도구 출력에 표시하지 않았다. 자세한 보고서는 Git 제외 private data 경로에 두었다. 원본·기존 Chroma를 수정하거나 임의 계정에 배정하지 않았다.

현재 OIDC 설정이 없어 실제 앱 개인 API는 닫혀 있다. 운영 등록·초대·외부 로그인·실제 메일 추출을 확인해야 실사용할 수 있다. 서버 단일 인스턴스와 영구 디스크가 필요하며 현재 Render free 예시는 개인 데이터의 상시 영속 운영 구성이 아니다.

## 7. 다음 단계 진입

02 구현과 아래의 합성 검증을 완료했다. 다음 개발 프롬프트는 [03-review-data.md](prompts/03-review-data.md)다. 장소/리뷰 공급자 조사 정책·크롤러·추천·일정 기능은 이번 요청으로 실행하지 않았다. 03 개발을 시작할 기반은 준비됐으나 실제 계정·과금·호스팅을 검증한 출시 판정은 아니다.

남은 운영 항목은 영구 디스크 배포·상시 호스팅·백업 스케줄·운영 관측 및 실제 OIDC/모델/가격/브라우저 다운로드 검증이다. 06단계 전체 재해 복구 훈련을 완료했다고 주장하지 않는다.

## 8. 02단계 구현·검증 — 2026-10-01

기준 revision `aefee57`, 이전 01 구현과 사용자 문서를 보존한 미커밋 변경이다. 테스트 도중 실제 원문·기존 개인 Chroma를 변경하지 않았으며, 외부 공급자 요청/배포/커밋/push는 실행하지 않았다. 실행·복구 절차는 [RELIABILITY_RUNBOOK.md](RELIABILITY_RUNBOOK.md), 단가·예산·정산은 [BUDGET_RUNBOOK.md](BUDGET_RUNBOOK.md)에 기록했다.

### 구현 범위

| 구성 | 주요 파일 | 실제 동작 |
| --- | --- | --- |
| SQLite v2 | `src/reliability/schema.py`, `src/foundation/db.py` | jobs/events, non-null scope 멱등성, dispatcher/writer lease, index generation/reader, 삭제 영수증, 비용 예약/원장. v1 미완료 영수증 RECOVERY_REQUIRED |
| 실행기 | `src/reliability/jobs.py`, `dispatcher.py`, `api.py` | lifespan 시작/종료, 단일 worker, 외부 작업의 스레드 실행, lease/heartbeat/fencing, 시도/기한/취소, checkpoint 복구 |
| 업무 단계 | `src/reliability/handlers.py` | 문서별 추출·임베딩·활성화 저장, 부분 성공, 실패 파일만 재시도, 완료된 호환 결과 재사용, 모델 변경 검사 |
| 검색 전환 | `src/reliability/generations.py`, `foundation/repository.py`, `search.py` | 검증된 불변 collection, SQL 활성 예약과 pointer 동시 전환, 기존 벡터 복사, reader 참조, 손상 시 SQL 읽기 유지 |
| 비용 | `src/reliability/budget.py`, `providers.py`, parser/embedder/rag | 원자적 사용자/전역 일·월 한도, 통화/단가 snapshot, 실제 정산/unknown 보유, 안전한 재시도, SDK retries=0, 실제 공급자 호출 동시성 1 |
| 저장/삭제 | `foundation/documents.py`, `reliability/maintenance.py`, `handlers.py`, `foundation/cli.py` | fsync+원자적 원문 저장, 고아 정리, tombstone 우선, 늦은 worker/문서별 삭제 방어, 최소 삭제 영수증, provider/checkpoint 포함 백업 |
| API/화면 | `foundation/routes.py`, `web/js/foundation.js`, `web/index.html`, CSS | 상태/여행 작업 목록/취소/실패 재시도/SSE/usage/readiness, refresh 복구, 실제 처리 개수, 입력 결과 불명 시 같은 intent key 유지 |

동일 key·다른 payload는 409다. 동일 파일이 처리 중이면 다른 key의 중복 업로드도 기존 작업을 재사용한다. 실패 부모 작업에는 후속 작업 하나만 만들어 새 버튼 키로도 중복 재과금하지 않는다. 관리자 공용 조사의 scope/role/NULL-trip 계약은 테스트했지만 공용 조사 실행기·HTTP 생성 경로는 아직 개방하지 않았다.

### 실제 자동 실행 결과

```bash
.venv/bin/python -m pytest tests -q
# 315 passed, 2 warnings in 17.82s
node --test tests/test_service_worker.cjs
# 2 passed, 0 failed
node --check web/js/foundation.js
node --check web/sw.js
git diff --check
# exit 0
```

`tests/test_reliability_*.py`는 83개 검사로 수집된다. 나머지는 기존 parser/pipeline/인증/예약/API 회귀다. Python 두 경고는 Starlette/Authlib httpx 호환 API deprecation이며 기능 실패가 아니다. 테스트는 conftest와 각 fixture가 설정한 임시 경로·dummy key·명시적 합성 가격 정책으로 격리했다.

| 장애·경합 실험 | 근거 | 결과 / 인수 계약 |
| --- | --- | --- |
| 원문 rename 후 SQL 등록 전 실제 SIGKILL | `test_reliability_maintenance.py` | 기존 활성 예약 보존, SQL 참조/진행 writer 보호, 유예 후 고아 회수. DATA-03, OPS-03 |
| 추출 후·임베딩 저장 후·SQL 활성화 후 실제 SIGKILL | `test_reliability_runtime.py` | restart 후 parse/embed 각각 1회, 결제 중복 없음, 기존 수동 예약 읽기 유지. OPS-01/03/04 |
| ready 전환 후 SQL 이전·SQL pointer 이후 실제 SIGKILL | `test_reliability_generations.py` | 이전 active 또는 새 active 일관성, 완료 결과 복원, 불필요한 재임베딩 없음. OPS-03/04 |
| lease 만료·이전 worker·dispatcher 2개 | `test_reliability_jobs.py`, generations | 늦은 activation/heartbeat/checkpoint 거절, 동시 실행 1, event loop 응답 유지. OPS-01/03 |
| 동일/다른 key 동시 업로드·후속 재시도 경합 | jobs/API 테스트 | 원래 key는 입력 hash 검증, pending 동일 문서 job 1개/parse 1회, 실패 단위 재사용. OPS-01/03 |
| 마지막 예산에 독립 프로세스 동시 요청 | `test_reliability_budget.py` | 원자적 한도 검사로 허용한 요청만 실행. OPS-05 |
| 원격 성공 후 응답 유실/영수증-정산 사이 종료 | budget/runtime | unknown 예약 유지 또는 영수증 재사용, 오류 때문에 환불/자동 재과금하지 않음. OPS-05 |
| 삭제/회수 중 늦은 응답 | API/runtime/budget/generations | 여행 job/SSE 404, 최소 삭제 영수증, 원문/예약/vector 부활 없음, 실제 비용만 정산, 문서 삭제 뒤 새 embedding 금지. AUTH job 범위, OPS-06 |
| 오래 읽는 요청·이전 세대 회수 | generations | reader 해제 전 collection 유지, 종료 후 회수. OPS-04 |
| 활성 collection 손상·모델 변경 | generations/API | SEARCH_REBUILDING, 중복 없는 복구, SQL 날짜 200 유지, 모델 바뀐 checkpoint 차단. OPS-04/07 |
| Last-Event-ID·연결 종료·세션 회수 | API/jobs | 순서대로 저장 이벤트 재생, 호출 횟수 불변, 권한 회수 뒤 종료. AUTH SSE, OPS-07, UI-03 |

### 브라우저에서 실행한 흐름

```bash
.venv/bin/python tests/browser_fixture.py
# loopback :8765 앱 / :8766 합성 OIDC, 임시 저장소, fake provider
```

1. 합성 OIDC A 로그인 후 도쿄 여행 생성. 왕복 항공+투어 파일과 1회 임베딩 실패 파일을 함께 업로드했다.
2. 원문 분석 0/2 단계에서 새로고침했고 같은 진행 작업을 서버 목록에서 복원했다.
3. 바르셀로나 여행 생성·전환 후 도쿄로 복귀하자 해당 여행의 부분 성공과 예약 2개가 보였다.
4. 임시 서버를 재시작하고 다시 상태를 읽었다. 실패 파일만 재시도해 예약 3개가 됐다. 합성 호출 기록은 추출 2회·성공 임베딩 2회로, 이미 끝난 추출과 성공 파일을 반복하지 않았다.
5. 테스트용 활성 collection을 삭제하고 fake embedding을 오프라인으로 바꿨다. 일반 검색은 검색 복구 안내, 복구 job은 전송 실패 상태를 보였다.
6. 같은 상황에서 `2026-11-06 예약을 전부 알려줘`는 3개와 정확한 왕복 출국 이벤트·현지 시간대를 반환했다. 근거 3건과 원문 텍스트도 열렸다.
7. 새 UI에서 검색 복구 재시도 행동과 완료된 후속 작업이 있는 이전 실패의 중복 버튼 제거를 확인했다.

증거: [장애 중 예약 조회 화면](validation/reliability-browser.png). 전부 합성 데이터다. 서버 재시작은 테스트용 경로를 명시한 `BROWSER_FIXTURE_DIR=...`로 같은 SQLite/Chroma를 재사용했다. 브라우저 작업은 내장 브라우저로 실행했으며 OS 파일 다운로드는 01의 기존 미확인 사항이다. 후반의 문서별 삭제 guard·서로 다른 키 경쟁·고아 파일 정리 보완은 최종 315개 자동 검사로 확인했다. 브라우저에서 모든 강제 종료 지점을 다시 실행했다고 주장하지 않는다.

### 미검증·운영 경계

- 실제 OIDC 제공자 로그인, 실제 OpenAI 추출/임베딩/답변, 실제 단가/청구 조회, 공급자 지연 과금은 `not_run`이다. fake 계약·합성 OIDC 검증과 구분한다.
- 가격 템플릿은 의도적으로 미설정 상태다. 운영자가 현재 단가·확인일·상한을 넣어야 유료 호출이 열린다. 인증 미설정 운영 API는 기존과 같이 닫힌다.
- 한 프로세스·로컬 영구 디스크가 전제다. 다중 인스턴스 운영, 외부 exactly-once, 공급자 청구 hard cap을 보장하지 않는다.
- 서버가 꺼진 동안 작업을 실행하는 클라우드 호스팅은 이번 구현에 포함하지 않는다. 운영 배포와 06 전체 백업/재해 복구 훈련은 별도다.
- 개인정보 관련 실제 기존 자료 이관·삭제·임의 소유자 배정은 하지 않았다. 01의 사용자 미확정 원본을 유지했다.


## 9. 03 리뷰 데이터 구현과 검증 (2026-10-01)

**코드/합성 검증 complete, 라이브 수집 not_run, 운영 활성화 OFF.** 기존 01/02 구현과 사용자 변경을 보존하고 migration 3을 추가했다. 공급자 교체 인터페이스와 fake/Apify, 관리자 연구 scope, 원자적 run/job, 호출별 비용·실행별 상한, 최소 SQL 페이지 영수증, 원격 삭제 outbox, 로컬 언어 모델, 순수 집계와 근거 화면을 연결했다.

현재 전체 Python **495 passed, 2 warnings**, 서비스워커 **2 passed**. 리뷰 신규 180개에는 지정 합성 24사례 전체, 페이지/원문/번역/분모/부분 실패, 동시 요청, 역할/정책/지점 회수, 실제 프로세스 SIGKILL/lease, 판별 버전 변경, 응답 유실 unknown, 원격 데이터 삭제, 과거 백업의 이후 철회 내역 적용을 포함한다. 상세 실행 명령·테스트 대응과 브라우저 사진은 [03 결과 보고서](reports/review-pilot-2026-10-01.md)에 있다.

합성 OIDC 브라우저에서 관리자 로그인→수집→진행 중 새로고침→동일 job 복원→200건 완료→일반 사용자 전환을 실행했다. 소비자 화면은 품질/운영 미검증 사유를 표시하며 대체 한국어 0%를 만들지 않는다. 모바일 390px에서 가로 넘침 없음, 브라우저 error console 0건을 확인했다. 실제 OIDC 제공자 로그인을 새로 검증한 것은 아니다.

| 검증 축 | 결과 |
| --- | --- |
| 기술 실행 | HTTP adapter/mock/fake/SQL/UI 시험 완료; 실제 Apify 리뷰 수집 0건 |
| 이용 범위 | 개별 access/collect/calculate/raw_store/aggregate_store/id_store/llm/display gate 구현; 실제 플랫폼 이용 권한은 미확정 |
| 수집 품질 | 합성 원문·번역·정렬·기간·페이지 검증 완료; 실제 Apify newest 의미/원천 연속성/원문 대조 미실행 |
| 언어 모델 | 실제 Lingua + 일반 문장 heldout 1,000건 진단. 현지어 precision 100%, 한국어 recall 94.5%, unknown 5.1%. 음식점 리뷰 독립 품질 미검증 |
| 제품 활성화 | 연구/운영 모두 기본 OFF. 합성 provider·일반문장 진단·한국어 정답 0건으로 production 통과 불가 |
| 실제 A/B | 6×100, 10×200 모두 not_run. APIFY_TOKEN/APIFY_API_TOKEN 없음, 실제 과금 없음 |

초기 임계값 C≥100, U/T≤10%, L/T≥60%, (K+U)/T≤10%는 버전 관리된 가설이다. 전체 평가 수나 주민 비율의 분모로 쓰지 않는다. 판별 불가/번역-only는 unknown, 본문 존재 불명은 별도 extraction_unknown, 0분모는 null이다. 이 단계의 언어 조건 통과와 종합 장소 추천 통과는 다르다.

운영 절차는 [REVIEW_RUNBOOK.md](REVIEW_RUNBOOK.md), 실제 어댑터/공식 가격 검토는 [Apify 보고서](reports/APIFY_PROVIDER_VERIFICATION.md), 모델/라이선스/hash/메모리/오류는 [언어 평가](reports/LANGUAGE_EVALUATION.md)에 있다. 원문은 앱에 저장하지 않고, Apify 원격 보관은 별도 권한·기한·확인된 삭제를 요구한다. 원격 삭제 실패/시작 응답 유실/원 비용 actor 비활성화는 operator 확인 필요로 남기며 완료로 표시하지 않는다.

추가 파일: `src/providers/`, `src/research/`, `scripts/review_language_eval.py`, `tests/test_review_*.py`, `tests/review_browser_fixture.py`, 리뷰 관련 reports/examples. 기존 `api.py`, `src/foundation/{db,cli,routes}.py`, `src/reliability/jobs.py`, frontend·requirements·env 안내를 확장했다. 04 추천·일정, 배포/상시 호스팅은 아직 완료하지 않았다. 커밋·push·PR·배포 없음.


## 10. 04 장소 탐색·추천·비교 (2026-10-05)

**코드/합성 검증 complete, 실제 후보 운영 활성화 대기.** migration 4/5와 `src/discovery`, `src/recommendations`, 인증된 API·관리자 검수·실제 탐색 화면을 연결했다. 개인 조건·메모·제외는 공용 사실과 분리한다. 순수 추천 계산은 세 가지 모델을 구분하고 결측을 null로 보존한다. 엄격 조건은 통과한 장소만 반환하며 조건을 몰래 완화하지 않는다. 출처 철회와 완료 경쟁을 검사하고, 동일 요청은 하나의 job/run으로 묶는다.

전체 Python 검증 **601 passed, 2 warnings** 후 입력 schema 강화 5개를 추가하여 기반 테스트 **30 passed**, URL reader **24 passed**, 추천 엔진/API/경합 **57 passed**를 확인했다. JS 상태·응답 순서·멱등 키·비교·캐시·자산 hash **9 passed**. `scripts/version_web_assets.py --check`로 HTML이 현재 JS/CSS를 참조하는지 검사한다. 04 브라우저 검증 시 JS hash `c7b4b9c50433`, CSS hash `6f1134782179`였다.

합성 OIDC 실제 브라우저에서 여행/조건 → URL·메모 저장 → 지점 확인 → 추천 → 상세 → 서로 다른 두 장소 비교 → 새로고침 복원을 실행했다. 엄격 리뷰 기능 OFF 상태에서 현지 탐색은 0곳이며 대표 명소는 독립적으로 표시됐다. 이전 조건 결과 표기, 실제 잔여석 미확인, 비교 조건 일치, 320px 긴 다국어명에서 페이지 가로 넘침 없음, 모달 Escape와 호출 버튼 포커스 복귀, 콘솔 error 0건을 확인했다. 비교표는 작은 화면에서 자체 스크롤을 제공한다. 최대 3곳/4번째 거절은 자동 상태 검사로 확인했다. 최종 화면 검사에서는 320px·앱 글자크기200%에서도 가로 넘침 없이 탐색·편집 모달을 사용할 수 있었다.

실제 도쿄·바르셀로나 각 6곳/총12곳의 지점·공식 출처를 조사했다. 출처 확인일은 2026-10-01이며 실제 표시 사용 범위 승인은 대기다. 도시별30곳 목표나 실제 경로/예약/리뷰 품질 검증을 완료했다고 보지 않는다. 운영 자동 seed 없이 합성 시험과 실제 후보를 구분했다. 상세: [실행 보고서](reports/discovery-validation-2026-10-05.md), [실제 후보 검수](reports/DISCOVERY_CANDIDATE_AUDIT.md), [점수 계약](reports/RECOMMENDATION_ENGINE.md), [실행 안내](DISCOVERY_RUNBOOK.md).

04 화면 증거: [장소 비교](reports/discovery-comparison-browser.png), [저장·상세](reports/discovery-foundation-browser.png). 05 일정의 구현·검증은 아래11절에 이어 기록한다. 06 운영 배포와07 Plan B는 미완료다.

## 11. 05 일정 생성·편집·되돌리기 (2026-10-05)

**코드/합성 검증 complete, 실제 경로·운영 검증 partial.** 사용자 요청으로 잠시 중지한 뒤 같은 작업 트리에서 재개했다. 기존 인증·여행·예약·장소·추천·작업·예산을 재사용했다. 원래 사용자 자료를 이관·삭제하거나 운영 서버에 배포하지 않았다.

### 수정 코드와 저장 계약

| 구성 | 파일 | 구현한 동작 |
| --- | --- | --- |
| SQLite migration6 | `src/itineraries/schema.py`, `src/foundation/db.py` | 여행/사용자별 itinerary, 입력 snapshot·manifest, 불변 revision, 만료 preview, 활성 포인터·version·undo stack |
| 시간·검증 | `src/itineraries/intervals.py`, `constraints.py` | UTC [start,end), 현지 시각/IANA timezone·DST, 자정 영업·브레이크·휴무·마감·전체 체류·인원·필수 조건, satisfied/violated/unknown |
| 생성·편집 | `src/itineraries/scheduler.py`, `edits.py` | 예약 보존→빈 시간→양쪽 이동→후보 삽입→전체 검증, 결정적 동점, 미배치 이유, 밀도·식사창·휴식, preview·현재 자료 재검증 |
| 이동·비용 | `src/itineraries/travel_time.py` | provider/estimate/unknown, 허용 좌표와 출발 시각별 cache, matrix 요소·시간 상한, 기존 비용 예약/정산·receipt 재사용·unknown 과금 보존 |
| API·영구 작업 | `src/itineraries/models.py`, `service.py`, `routes.py`, `api.py`, `src/reliability/jobs.py` | 생성202/멱등 키/job, 조회/복원, 명령 whitelist, preview/apply/undo, A/B404, version/세션/삭제/fencing/현재 사실 guard |
| 삭제·복원 | `src/reliability/handlers.py`, `src/discovery/maintenance.py` | 여행 tombstone 우선, 일정/preview/revision 개인 payload 정리, 복원 후 삭제 자료 재활성화 금지 |
| 실제 화면 | `web/js/foundation.js`, `web/css/foundation.css`, `web/index.html` | 장소 선택→생성 조건→타임라인→시각/체류·앞뒤 이동·잠금·삭제 preview→적용→undo, 작업·이전 결과·오류 복구, 모바일·키보드·200% 글자 |

예약 상태와 사용자 잠금은 별개다. 날짜만 있는 예약에 임의 자정을 만들지 않으며 호텔 체류 전체를 종일 busy로 막지 않는다. 항공은 출발/도착 각각의 현지 시각과 timezone을 보존한다. 실제 경로 실패를0분으로 채우지 않는다. 기본 엄격 모드는 미확인 후보를 미배치하고 명시적 잠정 모드에서도 확인된 위반은 허용하지 않는다. 잠정 브레이크 사실이 원래 확인된 영업 종료·마지막 입장/주문을 약화하지 않도록 근거의 확인 상태를 따로 유지한다.

편집은 저장된 preview와 expected_version으로만 적용한다. 적용 직전에 현재 사용자·여행·예약·조건·출처·경로 유효 기간을 검사하고 새 revision/version을 원자적으로 만든다. 거절된 preview를 재검증 과정에서 통과시키지 않는다. 최소10개 편집의 undo를 새 revision으로 지원하며 최대100개의 사용자 편집 복원 대상을 유지한다. 외부 예약은 생성·변경·취소하지 않는다.

### 실제 브라우저 검증

임시 합성 OIDC와 fake route provider로 저장한 도쿄4인 여행의 장소2곳을 일정으로 만들었다. version1의 둘째 방문11:20~12:20을11:30~12:10으로 미리보기→적용해version2, 새로고침 유지, undo 미리보기→적용으로 **새 version3**을 확인했다. 사용자 체류시간 변경 후 기본 제안 안내가 남지 않는다. 충돌 미리보기에서는 적용을 차단하고 기존 버전을 보존했다. 휴식과 예약 확정 문구를 분리했다.

서버 재시작 후 저장된 작업과 일정을 다시 읽었고, 합성팩 source version이 바뀐 과거 일정은 오래된 근거 상태로 표시했다. 320px·실제 앱 글자크기200%, 긴 다국어명, 시간 입력과 Enter/Escape·포커스 복귀, 가로 넘침 없음과 콘솔 error0건을 확인했다. 확정 예약·항공·호텔·동시 편집·실제 SIGKILL의 모든 사례를 브라우저에서 반복한 것은 아니며 아래 자동 시험과 구분한다.

증거: [최종 타임라인](reports/itinerary-browser.png), [충돌 미리보기](reports/itinerary-conflict-browser.png), [작은 화면·큰 글자](reports/itinerary-mobile-large-text.png). 세부 기록: [통합 실행 결과](reports/itinerary-validation-2026-10-05.md), [엔진](reports/ITINERARY_ENGINE.md), [API·SIGKILL 복구](reports/ITINERARY_API_VERIFICATION.md), [실행 안내](ITINERARY_RUNBOOK.md).

### 최종 자동 실행 결과

```bash
.venv/bin/python -m pytest tests -q
# 682 passed, 2 warnings in 49.47s
node --test tests/test_discovery_ui.cjs tests/test_service_worker.cjs
# 19 passed, 0 failed
python3 scripts/version_web_assets.py --check
# Public asset content hashes match index.html
git diff --check
# exit 0
```

이 결과는01~05 전체 Python 회귀를 포함한다.05의 엔진39/API21/이동14/실제 프로세스 복구2 시험도 포함된다. 체류90분+양쪽 이동30분의120/150분 창, 추가버퍼10분, DST·자정·호텔·항공·휴무·고정 충돌·경로 실패·마지막 예산 경쟁·동시 apply·10회 undo·현재 자료/사용권 철회를 확인했다. SIGKILL은 경로 receipt 저장 뒤와 revision 활성화 뒤의 두 지점에서 실행했으며 새 프로세스가 이전 공급자 결과를 재사용하고 활성 revision을 중복 생성하지 않았다.

기존 Starlette/Authlib httpx API deprecation 경고2건은 남아 있다. 실제 공급자 호출·유료 과금 없이 임시 저장소에서 검증했다. 최종 JS hash는 `15765a3d21b1`, CSS hash는 `e690107dbce3`다. 기준677회귀 통과 후 교차 검토에서 발견한 잠정 브레이크/확정 마감 회귀5개를 추가해 최종682개를 실행했다. 기본 제안/사용자 입력 출처와 미확인 이동의 남은 동일 장소 설명도 정리했다.

### 지원 범위와 라이브 미검증

한 요청은 도쿄 또는 바르셀로나의 한 도시 체류 구간1~14일·최대30개 선택 장소다. 도시 간 자동 연결 최적화는 제공하지 않으며 경계를 넘는 생성은 거절한다. 경로 기본값은 실제 provider 미설정이고 허용 좌표의 짧은 도보 추정은 계획 가정이다. 자연어 편집 API는 선택 항목의 삭제/잠금/잠금 해제/명시적 날짜·시각 이동을 제한된 parser로 해석하며 모호한 지시는 명확화한다. 일반적인 자유 대화 일정 편집이나 실제 예약 변경을 지원한다고 표시하지 않는다.

실제 외부 계정 로그인, 실제 지도·과금·교통·예약 슬롯, 실제 리뷰 수집과 대상 리뷰 언어 품질, 운영 환경 장애 복구는 미검증이다. 검수한 실제 후보는 각 도시6곳이며 운영 표시 승인 대기다. 합성 후보·경로를 운영 자료로 자동 등록하지 않는다. 06 상시 호스팅/베타 출시·전체 재해 복구 및07 Plan B는 완료하지 않았다. 커밋·push·PR·배포 없음.


## 12. 06 비공개 베타 운영 준비 (2026-10-06)

**구현 complete, 실환경 검증 partial, 실제 배포 not_run, 전체 R1 출시 blocked.** 자세한 명령·범위·인수 기준 대응은 [운영 검증](../operations/VALIDATION.md), 설정과 복원 명령은 [RUNBOOK](../operations/RUNBOOK.md), 현재 공식 가격과 상한은 [COSTS](../operations/COSTS.md)에 기록했다.07~08 부가 기능을 선행조건으로 추가하지 않았다.

Dockerfile을 Python3.13.12 digest/requirements.lock/UID1000으로 고정하고, seed·개인자료·테스트·.env를 제외했다. Render는 `0.5c-512mb`·영구disk1GB·1인스턴스·자동배포OFF·ready health 구성이다. 운영 mount·HTTPS OIDC·쓰기 권한·worker 수를 검사하며 lifetime flock과 migration lock 이후에만 HTTP를 연다. schema7에는 운영 identity/controls/audit를 추가했다. migration 실패와 인증 미설정은 기동exit78로 차단됨을 실제 이미지에서 확인했다.

liveness/readiness/관리자 상태 API를 분리했다. health는 유료 API를 호출하지 않는다. 외부 전체/공급자별 OFF, 읽기 전용, 점검 모드와 queue·비용 잔여·unknown·오류/재시도·RSS/CPU/disk·백업 결과 관측을 연결했다. SQL 읽기는 공급자/검색 장애와 분리한다. 로그에는 code와 request ID만 남기고 일반 access log는 끈다.

온라인 SQLite backup과 허용 원문·활성세대 manifest를 AES-GCM으로 암호화한다. 별도 삭제/권한/비용 checkpoint 및 S3 호환 upload/download·retention·정기 scheduler를 구현했다. 복원은 새 디렉터리에서 최신 tombstone을 먼저 적용하고 세션/초대를 회수한다. 미완료 job/unknown 비용을 자동 재실행하지 않으며 외부 호출OFF/read-only/pending gate를 유지한다. 실제 offhost bucket은 미설정이며 mock 왕복과 로컬 격리 복원만 검증했다.

최종 Python **692 passed, 2 warnings in59.40s**, Node **19 passed**. Docker Linux arm64 빌드, 실제 UID1000 쓰기, 두 번째 프로세스 거절, schema999/인증 미설정exit78, private API401 검증을 완료했다. 512MB·0.5CPU 제한에서5명/1,200읽기+문서1개+추천1개: p50 **46.23ms**, p95 **90.42ms**, 오류/OOM/retry0, fake2회·실제외부0회. 추천은 문서 뒤 큐에서 직렬 실행됐다. 실제 Docker재시작 후 예약9개 유지·타인404·중복 요청/SSE 재연결 추가 호출0을 확인했다. 짧은15.811초 시험이며 Render 장기 성능 보증이 아니다. [부하 원본](reports/operations-load-512mb.json), [이미지 검사](reports/operations-image-startup.json).

격리 복원은0.042초, 실제 임시Chroma 재생성0.198초/fake임베딩1회, 실제비용0이었다. 백업 이후 삭제된B여행은 원문/SQL/API/재색인에서 부활하지 않았고 A교정11:30/일정v1은 보존됐다. 일반 데이터는snapshot, 삭제/권한/비용은checkpoint시점까지만 복구하며 이후 손실/삭제 대조가 필요하다. [복원 원본](reports/operations-restore.json).

실제 브라우저(합성 OIDC)에서 로그인→여행→왕복메일→교정15:30→날짜 질문·출처→보관함→추천·상세·비교→일정→preview/apply v2→새로고침→undo 새v3을 확인했다. 시간대 없는 예약 주변은 미배치했고 고정 예약을 임의 변경하지 않았다. 390px 모바일 가로 넘침0, consoleerror0. 기존 event가 없는 예약에 새 구간을 추가하는 교정 UI와 새로고침 후 선택 목록 표시명 복원은 남은 UX 제한이며 [검증 문서](../operations/VALIDATION.md)에 명시했다. [화면](reports/operations-browser.png), [모바일](reports/operations-mobile.png).

실제 후보는 **도쿄6/바르셀로나6**, 표시 사용 승인/팩 승인 대기로 운영 유효 후보는 **각0곳**이다. 이 사실을 화면에 표시하고 운영 합성 활성화·조회·지점 매칭을 거절한다. 기존 사실 확인일을 이번 URL열람만으로 갱신하지 않았다. 실제경로·잔여석·엄격리뷰는OFF/미검증이다. [도시별 보고서](../operations/CITY_READINESS.md).

현재 새 운영URL은 없다. Render로그인 상태/서비스 접근, 실제OIDC client·callback, 외부backup destination/credential/key, 신규 월지출 상한이 준비되면 관리자→준비된2명→최대5명 순으로 실제 HTTPS·쿠키·CSRF·재시작·offhost복원을 확인한다. 기본 개인서비스 배포와 추천 전체R1/리뷰기능 출시를 분리한다. 외부 계정이나 결제를 임의 생성하지 않았고 타인에게 초대 메시지를 보내지 않았다. 커밋·push·PR·외부배포 없음.


## 13. 비용0원 우선으로 배포 방향 변경 (2026-10-06)

사용자가 유료 서버 지출을 원하지 않아 Render 유료 제안을 보류했다. root `render.yaml`은 유료 대안임을 명시하고 sync하지 않는다. Render Free는15분 sleep/비영구 파일시스템 때문에 현재 SQLite·Chroma·원문 저장과 맞지 않는다. 기본 구조를 유지하는 Oracle Always Free VM을 우선 후보로 검토했으며 카드없는 대안은 Render+Supabase 저장/작업 계층 이관이 필요하다. 현재 문서는 [무료배포안](../operations/FREE_HOSTING.md)을 우선한다.

`deploy/free-vm/{compose.yaml,https.yaml,Caddyfile,.env.example,pricing-zero.json}`을 추가했다. cloud resource 자동 생성 없이 기존 persistent VM에서 실행하며 앱/proxy비루트, 앱rootfs read-only, single dispatcher, hostnameTLS,0원 halted정책·유료 key빈값 강제를 제공한다. 실제계정/VM/DNS/OIDC/백업은 미설정이다. OpenAI기반 추출/임베딩/자유답변·유료지도/리뷰가 무료가 된다고 주장하지 않으며 수동여행/예약/보관함/저장조회·SQL날짜질문을 우선한다.

[무료 profile 실행 결과](reports/free-vm-profile.json)는 별도 로컬Docker 검증이며 **passed=true**다. 실제 production launcher의 UID1000/read-only 실행, 유료 key 빈 값 강제, 공급자 전송 전0원 예산 거절, 컨테이너 재시작 후 여행1개·수동예약1개 보존을 확인했다. 실제 유료 공급자 호출0회·신규 클라우드 리소스0개다. 실행 명령은 `.venv/bin/python scripts/check_free_vm.py --report docs/service-v2/reports/free-vm-profile.json`이다. 이번 변경은 배포 profile·검증 script·문서이며 앞서 통과한692개 Python 회귀 전체를 다시 실행한 결과는 아니다.

Caddy validate는 통과했으나 실제인증서발급·외부HTTPS·Oracle계정생성·리소스배포는 not_run이다. 현재 공식 Oracle A1한도는2OCPU/12GB상당이며 idle회수/용량부족/카드본인확인 조건이 있다. 새지출·유료전환·자동초대 없음.

## 14. 기존 Render Free + Supabase Free 전환 (2026-10-06)

사용자가 두 기존 계정을 확인하고 **Supabase hii를 재개해 사용**하도록 선택했다. 13절의 Oracle 우선안은 대체되며 설정·운영 계약은 [RENDER_SUPABASE](../operations/RENDER_SUPABASE.md)를 따른다. **저장 구조 코드 complete, 로컬 검증 verified, 실제 Supabase 연동/새 Render 배포 not_run, 비공개 개방 blocked**다. 기존 URL https://travel-inbox-rag.onrender.com 은 이전 aefee57 배포다.

`src/storage/{postgres,objects,vectors,artifacts,transfer}.py`를 추가했다. 기존 Repository/Auth/Jobs/Budget/Discovery/Itinerary를 재사용하며 cloud backend에서 PostgreSQL private `travel` 스키마를 사용한다. 사용자·여행·예약·교정·일정·lease·SSE·비용, provider receipt와 checkpoint를 외부 DB에 보존한다. 원문은 Supabase private Storage의 서버 ID 키와 SQL manifest/hash로, 검색은 pgvector 세대별 collection/활성 포인터로 보존한다. SQLite/Chroma는 local 모드로 유지했다. schema8은 후보 순서를 명시적 sort_order로 이관한다. SQL advisory lock으로 writer/예산/lease를 직렬화하며 session read pin이 살아 있는 과거 검색 세대는 회수하지 않는다. cloud 설정 오류의 SQLite 자동 fallback은 없다.

공개 bucket 거절, 원문 서버 ID/크기/내용/hash 검증, 업로드 이전 pending manifest, 삭제 후 늦은 완료의 재등록 차단/고아 회수를 구현했다. Storage 장애에서도 SQL 예약은 읽힌다. 암호화 consistent cloud snapshot·최신 삭제 checkpoint·격리 복원·소유권을 보존하는 dry-run/apply 이관 CLI를 추가했다. 소유자 불명 자료는 가져오지 않으며 원본 파일을 수정하지 않는다. import 후 session/초대 회수·read-only·외부호출OFF·unknown 정산·검색 재구축 상태를 유지한다. cloud 자동 offhost backup은 아직 연결하지 않았으므로 이전 SQLite scheduler의 운영 RPO를 그대로 약속하지 않는다.

`render.yaml`은 기존 서비스명, Free, disk 없음, instance1, autoDeploy off, private `/tmp/travel-cache`, Supabase settings,0원 halted policy를 지정한다. Docker에 secret 없이 해당 가격 정책을 포함한다. session pooler5432/TLS verify-full·비루트·HTTPS OIDC·단일worker·zero policy 검사, migration 실패 시 기동 차단을 유지한다. Cold start의 비JSON 오류에는 재접속 안내를 제공하며 mutation을 자동 재전송하지 않는다.

검증 상세와 재현 명령은 [보고서](reports/render-supabase-validation.md):

- 기존 전체 Python: **694 passed, 11 skipped, 2 warnings /76.42초**. skip11은 아래 실제PG 별도 suite에 해당한다.
- 실제 로컬 PostgreSQL17/pgvector cloud suite: **11 passed /11.16초**. cache삭제 후 원문/8예약/벡터/session/job 유지, SIGKILL/lease recovery·receipt 재사용, A/B404, 마지막 예산 경합, RLS, 삭제/업로드 경합, 암호화 backup→later deletion→restore→PG import.
- 기존 API/추천/일정/리뷰/작업을 실제PG로 실행: **158 passed, 3 deselected /140.56초**. SQLite 파일 직접 이관/하위프로세스3개는 기존 suite와 cloud 전용 시험으로 분리한다. 삭제 시험의300ms lease를 해당 시험에만10초로 고쳐 네트워크 실행 시간과 만료 시험을 구별했다.
- Node **19 passed**, content hash/compileall/diff check 통과. Linux arm64 Docker build 및512MiB 컨테이너에서 cloud import·UID1000·zero policy·.env 미포함 확인.
- CPU0.5/512MiB에서5명/1,200조회+문서/추천: p50 **15.14ms**, p95 **51.92ms**, 오류/OOM/retry0, actual provider0/fake2. 재시작 후9예약·세션 유지/타인404/중복job 재사용/SSE 확인. [부하 JSON](reports/render-supabase-load.json). 같은 Docker host의 PostgreSQL이며 실제 Render–Tokyo 네트워크 지연이나 장기 운영 성능은 미측정이다.
- 브라우저 합성 OIDC→여행 생성→수동 예약12:30→새로고침→서버 재시작→기존 세션/예약 보존. [화면](screenshots/cloud-restart.jpg). 이번 Chrome 업로드는 extension file URL 권한 미허용으로 중단했고 우회하지 않았다. HTTP upload/recovery는 통과했다. 이전06 전체 UI 검증과 이번 cloud 브라우저 검증을 구분한다.

실제 계정에서는 기존 Render Free/Docker/Blueprint 서비스와 이전 배포를 읽기 확인했다. hii Healthy 및 session pooler를 확인하고 **private travel-private 버킷(1MB/public OFF/policies0)**을 생성했다. DB 스키마·실제 메일·사용자 자료는 cloud에 올리지 않았다. Render에는 기존 OpenAI/Tavily 설정만 있고 OIDC/DB 설정이 없어 사용자가 비밀값을 Environment에 직접 넣는 절차를 요청했다. 비밀값 공개·비밀번호 재설정·계정 생성·결제·초대 메시지는 하지 않았다. **기존 Render 배포의 유료 기능이 이번 zero policy로 이미 바뀐 것은 아니다.** 새 배포의 유료 추출/임베딩/AI/리뷰는 기본 OFF이며 수동 여행/예약/SQL조회가 가능하다. 승인 운영 후보는 도쿄0/바르셀로나0, 엄격 리뷰OFF를 유지한다.

남은 실제 개방 요건: DB 연결 비밀·서버 Storage key·Google OIDC 설정 입력, Git 반영/기존 Render 새 배포, 실제 HTTPS 로그인·bucket 왕복·재시작·외부 백업 복원.7~8단계는 선행조건으로 추가하지 않았다. 이번 변경도 커밋·push·PR·Render 배포는 하지 않았다.

## 15. 07 예약 준비·Plan B·오늘/오프라인 (2026-10-06)

**A/B/C 구현 complete, 로컬 합성 검증 verified, 운영 검증 not_run**이다. 상세 실행·범위·제약은 [검증 보고서](reports/phase07-travel-tools-validation.md)에 기록했다. 14절의 Render/Supabase 전환 구조를 재사용했으며 새 운영 배포나 실제 계정/결제를 실행하지 않았다. 실제 외부 유료 호출은 0회다. A/B/C 완료는 아래 v1 범위의 코드·API·화면·합성 검증 판정이며 실제 여행지 영업/예약 정확도나 모든 모바일 기기 지원 판정이 아니다.

`src/travel_tools/`와 `web/js/{travel-tools,offline-store}.js`를 추가하고 기존 앱 셸/일정/예약/삭제/복원 계층에 연결했다. SQLite·PostgreSQL **schema9**는 예약 준비와 버전 이력, 출처별 오프라인 필드 허용을 저장한다. 일반 예약 추출값과 교정값은 유지하며 증빙 대조용 `place_id`·`party` 교정 경로를 추가했다. 기존 로그아웃의 303 HTML 응답이 화면에서 실패로 표시되는 문제도 204 API 응답으로 고쳤다.

- **A**: 6개 준비 상태, 생성 idempotency/수정 version, 공식 규칙·출처·입력 재검증, 같은 여행 문서의 날짜·지점·인원 증빙 대조. 30일/월별/명시 날짜, 월말·윤년·DST·날짜 전용, 지난 예정일 안내. 안정 UID/SEQUENCE·UTF-8·개행 안전 ICS, ja/es/ca 및 한국어 문의 초안. 자유 추가 요청은 원문과 번역 확인 필요로 남기고 자동 메시지/예약/결제는 없다.
- **B**: 미래 선택 방문 한 항목을 대체하며 다른 시간·고정 예약·필수 조건을 보존한다. 후보 12개/15초/대안 3개 상한, 0~1개도 그대로 반환. 기존 비용/경로 영수증·validator·preview/apply/undo 재사용. 이미 시작한 방문/과거/잠금/확정 예약의 자동 변경은 거절한다. 실제 날씨·대기·잔여석은 미확인이고 비용 차이도 근거 없으면 null이다.
- **C**: 현지 날짜와 다음 일정, 근거가 없으면 출발 시각 미확인. 서버 허용 필드 DTO, 도시별 최신 일정, 별도 메모 opt-in/내용 미리보기, 512KiB/최대24시간 읽기 전용 저장. 임시 저장→검증→활성 교체, quota/부분 실패 시 이전 본 유지, logout/account/trip 정리와 늦은 저장 fence, 다중 탭 purge, 재연결 권한·삭제 우선 검사. 일반 개인 API·원문·예약번호·결제·세션·raw 리뷰·지도 타일을 SW에 저장하지 않는다.

독립 설정은 `PREPARATION_ENABLED=true`, `PLAN_B_ENABLED=true`, `OFFLINE_ENABLED=false`다. 오늘 조회는 제공하되 오프라인 저장만 기본 OFF다. 오프라인을 켜도 출처의 표시 허용과 별개인 필드별 저장 정책 및 여행별 개인 기기 선택이 필요하다. 실제 운영 출처 권한을 임의 승인하지 않았다.

실행 결과:

```bash
.venv/bin/python -m pytest tests -q
# 711 passed, 11 skipped, 2 warnings / 72.59s
.venv/bin/python -m pytest tests/test_travel_preparation.py tests/test_travel_alternatives.py tests/test_travel_today.py -q
# 17 passed, 2 warnings / 8.25s
# 일회용 실제 로컬 PostgreSQL16/pgvector + tests.postgres_plugin로 같은 07 suite
# 17 passed, 2 warnings / 15.37s
# 같은 DB의 tests/test_cloud_storage.py
# 11 passed, 2 warnings / 16.59s
node --test tests/test_travel_offline.cjs tests/test_service_worker.cjs tests/test_discovery_ui.cjs
# 24 passed, 0 failed
node --check web/js/travel-tools.js
.venv/bin/python scripts/version_web_assets.py --check
.venv/bin/python -m compileall -q src/travel_tools
git diff --check
# 모두 exit 0
```

전체 회귀의 skip11은 별도 PostgreSQL이 필요한 cloud 시험이며 위 환경에서 모두 실행했다. 새 브라우저 저장 시험은 fake-indexeddb에서 quota 오류·최종 활성 전환 실패·저장 중 logout·namespace 교체·변조/만료를 주입했다. 실제 기기의 디스크를 가득 채운 시험은 아니다. 2개 Python 경고는 기존 Starlette/Authlib의 httpx 호환 API deprecation이다.

Codex 내장 브라우저에서 합성 A 로그인→공식 예약 규칙→준비 항목→ICS 파일 다운로드→일본어/한국어 문의→오늘→휴무 대안 1개→preview/apply→기존 undo→새로고침(version3)→저장 내용 미리보기→선택 다운로드→서버 종료→새로고침→읽기 전용 확인까지 실행했다. 재연결 후 출처 변경 manifest 제거, 로그아웃의 다른 탭 전파, B 계정의 빈 여행/이전 자료 없음, 390×844·큰 글자200%·키보드도 확인했다. [최종 오프라인 화면](reports/phase07-offline.png). 테스트용 snapshot·탭·서버·DB 컨테이너는 종료/정리했다.

실제 iOS/Android/PWA 설치·OS 비행기 모드·저장공간 회수·외부 캘린더 가져오기·원어민 번역·실제 OIDC/HTTPS/Render cold start·실제 시설과 경로는 미검증이다. 증빙 교정 직후 최신 version으로 이어가는 최종 UI 보완은 API 시험으로 검증했으며 그 화면을 다시 브라우저에서 실행한 것으로 표시하지 않는다. 현장 사용 화면의 오프라인 원격 삭제 지연/읽기 전용 한계는 사용자에게 표시한다.

실제 비공개 개방은 14절의 인증/DB secret·배포·백업 복원 검증이 계속 선행한다. 07 구현을 위해 06 개방 요건을 늘리지 않았다. 당시 미착수였던 **08 피드백 단계는 아래 16절에서 구현·로컬 검증을 완료**했다. 커밋·push·PR·운영 배포 없음.


## 16. 08 피드백·최소 분석·예상 지출·평가 (2026-10-06)

**구현 complete(v1), 로컬 합성 검증 verified, 실제 배포/실사용 결과 not_run·미측정**이다. 기준 revision `aefee57`의 기존 미커밋 변경을 보존했다. 상세 계약·실행·한계·다음 우선순위는 [검증 보고서](reports/phase08-feedback-validation.md), [고정 입력 평가 결과](reports/phase08-synthetic-evaluation.json), [미측정 회고 양식](reports/phase08-retrospective.json)에 기록했다.

`src/product/`와 `web/js/product.js`를 추가하고 기존 인증·여행·장소·보관함·추천·일정 revision·예약 준비·비용 ledger를 재사용했다. **기록·지출** 화면은 추천 선호/방문 경험 수정·철회, 사실 신고 상태, 여행 예상 지출, 관리자 오류 검토·최소 집계·JSON export를 제공한다. 장소 상세의 사실별 신고와 일정/카드의 기록 진입을 연결했다.

- 방문 못함/아직 안 감에는 현장 경험 입력을 요구하지 않는다. 메모는 개인 저장이며 이벤트/집계와 분리한다. 관심 없음의 다음 추천 반영은 명시 선택만 사용하고 기존 필수 조건·언어 gate를 완화하지 않는다.
- 신고는 검토 대상이다. 공식 출처·같은 필드/적용 기간·새 확인일로 정정하면 추천·일정은 재확인 상태가 되며 확정 예약과 원 revision을 변경하지 않는다.
- SQLite/PG **schema10**은 피드백·신고·예상 지출·분석 동의·최소 요약을 저장한다. schema9 이관 후 기존 여행을 보존하고 과거 이벤트를 새 노출 성과에서 제외하는 시험을 실행했다. 새 테이블도 PG private schema/RLS 적용이다.
- 분석 기본 OFF, 기본30일(설정1~90) 보관이다. 동의 이후 실제 노출/서버 저장·적용·완료/기록 전이만 계측하고 client 성공 위조·중복/타인 참조를 거절한다. modal/숨김/offline/여행 전환 노출을 제외한다. opt-out은 과거 분석을 삭제하며 재참여가 개인 기록을 소급 집계하지 않는다. 철회·여행 삭제·백업 이후 최신 tombstone 적용을 연결했다.
- Decimal 가격 계산은 인당/그룹·아동·세금·수수료·포함 관계·선결제·환급 예치금을 구분한다. 0과 null, 일부 하한과 미확인 상한을 분리하고 JPY/EUR를 합산하지 않는다. FX·KRW환산은 OFF다. 개인 여행비와 API 운영비를 별도 영역으로 보여준다.
- 보고서는 UTC 반개구간·city/type/language/config version·합성 여부·분자/분모/표본/제외 범위를 표시한다. 분모0은 미측정이다. 실패/취소·retry 정산/unknown 상한은 귀속 한계를 명시해 별도로 포함한다. 평가 도구는 후보·사실·정책·clock을 고정하고 순위/성분/다양성/위반을 비교한다. LLM 설명 검증은 별도이며 자동 A/B 승자는 없다.

검증 결과:

```bash
.venv/bin/python -m pytest tests -q
# 732 passed, 11 skipped, 2 warnings / 83.36s
# 마지막 가격 null 표시·정정 후 예약/일정 불변 보강은 아래 대상 시험으로 추가 검증
.venv/bin/python -m pytest tests/test_product_feedback.py tests/test_product_prices.py tests/test_product_expense_api.py -q
# 21 passed, 2 warnings / 8.29s
# 일회용 PostgreSQL16/pgvector + tests.postgres_plugin: 같은 08 suite
# 21 passed, 2 warnings / 23.50s
# 별도 tests/test_cloud_storage.py (plugin 없이)
# 11 passed, 2 warnings / 16.34s
node --test tests/*.cjs
# 27 passed, 0 failed
```

전체 skip11은 실제 로컬PG cloud suite로 별도 통과했다. 경고2개는 기존 Starlette/Authlib의 httpx 호환 API deprecation이다. 해시·JS syntax·compileall·diff check도 통과했다. 새 테스트는 권한/중복/분모0/재참여/메모 제외/철회/삭제·복원/가격/정정 후 예약 보존/현재 근거 만료/고정 평가/비용 retry·unknown/이관을 다룬다. AUTH-02·06·08, DATA-06·07, BOOK-05, REC-02·03·05·06 관련 범위와 실제 미검증은 상세 보고서에 구분했다.

내장 브라우저에서 A 로그인→방문 못함 저장→수정→새로고침→철회, 금액 입력→재시작 후 유지, opt-in→합성 추천 노출2·저장1→선호 기록→집계, 사실 신고→공식 근거 필요 처리, 고정 버전 비교, opt-out→개인 기능 유지, 로그아웃→B 빈 여행/이전 기록 없음까지 확인했다. 합성 저장1/2건은 계측 검증 수치이며 제품 성과가 아니다. 390×844·큰 글자200% 가로 넘침0, Escape/포커스 복귀 확인. [모바일 화면](reports/phase08-mobile-cost.png). 실제 모바일 기기·PWA 설치·라이브 로그인/LLM/방문 경험·Render 신규 배포는 미검증이다.

운영 후보는 계속 도쿄0/바르셀로나0, 엄격 리뷰OFF다. 다음 1순위는 **기존 두 도시 후보 중 각3곳의 공식 지점/영업/예약/인원/가격/표시 범위를 검수하는 것**이다. 관측된 운영 자료 부족을 줄이며 새 유료 API 없이 시작할 수 있다. 예약 알림·추가 도시·동행자 투표의 가치/비용/데이터 의존성은 회고 양식에서 비교하고 보류했다. 실제 서비스 개방은 여전히 14절의 secret·OIDC·배포·외부 백업 검증이 필요하다. 새 지출·외부 초대·commit/push/PR/배포 없음.

## 17. UI/UX 정돈 (2026-10-06)

사용자가 지정한 `emil-design-eng` 스킬로 기존 01~08 화면을 다듬었다. [변경·검증 상세](reports/uiux-refinement-2026-10-06.md), [데스크톱 화면](reports/design-after-desktop.png), [모바일 예상 지출](reports/design-after-mobile-cost.png).

- 주요 여행 화면/여행 도구 메뉴를 분리하고 모바일은 여행·탐색·일정·오늘·더보기로 구성했다. 기존 10개 기능의 진입 경로를 유지한다.
- 작업은 진행/확인 필요 건수와 접힌 내역으로 표시한다. 여행 홈은 비어 있을 때 다음 행동을 안내하고 예약이 있으면 작은 바로가기로 줄인다.
- 탐색 조건·추천 옵션·과거 요청·운영 도구는 펼치기로 정리하되 자료 부족·합성 자료·이전 조건 경고는 유지한다. 상세 사실을 운영/예약/가격으로 나눴다.
- 기록 화면은 예상 지출·나의 기록·정보 신고로 전환한다. 통화별 범위, 미확인 수, 선결제·환급 예치금과 입력 기준을 구분한다. 분석 참여는 별도 선택으로 유지한다.
- 긴 모달이 아래쪽 버튼으로 스크롤되는 문제를 수정했다. 모바일 비교표에 좌우 이동 안내·고정 항목명을 제공한다. 정상 로그아웃은 오류와 구분하고 로그인 화면 맨 위로 복귀한다.
- 색상·아이콘·글자·여백·버튼·폼·다크 모드를 통일했다. 키보드/화면 전환은 무애니메이션, 포인터 누름만 140ms 피드백이며 reduced motion에서는 제거한다. public shell v11/내용 해시 갱신, 개인 API 캐시 제외 유지.

최종 Node 시험 **27 passed, 0 failed**, JS syntax·asset hash·HTML 구조/ID·diff check 통과. 내장 브라우저에서 로그인→예약 직접 입력→여행 수정→새로고침 유지→추천 설정/작업→저장/피드백→비교→로그아웃을 합성 자료로 실행했다. 390px·320px 및 다크/200% 글자에서 가로 넘침0, Escape 포커스 복귀, 비교표 키보드 이동, 앱 console error 없음 확인. 전체 Python suite는 이번 UI 변경에서 재실행하지 않았다. 실기기·전체 스크린리더·운영 로그인·Render 신규 배포는 미검증이다.

백엔드·DB·권한·일정 검증·추천 점수·비용 계산과 실제 후보 상태는 변경하지 않았다. 도쿄0/바르셀로나0 실제 검수 후보·엄격 리뷰OFF와 14절 배포 제약이 계속 적용된다. 새 의존성·유료 호출·commit/push/배포 없음.

## 18. UI/UX 두 번째 개선 — 2026-10-06

`emil-design-eng` 적용. 상세 기록: [2차 UI/UX 개선](reports/uiux-refinement-round2-2026-10-06.md), [여행 홈](reports/design-round2-desktop.png), [일정](reports/design-round2-itinerary.png).

- 여행 제목·기간·도시·인원·예약 수를 요약 카드로 묶고 작업 내역을 상단 펼침 패널로 이동했다. 진행/확인 필요 상태, Escape·닫기·포커스 복귀를 지원한다.
- 여행 날짜·요일·예약 수를 누르는 필터와 일정 날짜 선택을 추가했다. 상세 필터는 펼치기, 빈 결과는 전체 예약 복구 행동을 제공한다. 31일 초과는 기존 날짜 입력/전체 목록을 유지한다. 수동 예약·숙박·전체 날짜 조회 규칙을 재사용한다.
- 추천의 상세/저장·일정/비교·근거/관심 없음을 구분했다. 일정 후보·빠른 시간 조정·잠금·삭제는 펼치고 기존 preview 동선에 연결했다. 미확인/합성/자료 만료 안내를 유지한다.
- 같은 날·시간대는 시각만 간결하게 표시하고 자정·항공 시간대 차이는 날짜를 보존한다. 숨긴 탭에서는 알림 시간을 멈춘다. public shell v12와 asset hashes 갱신.

최종 Node **33 passed, 0 failed**, JS syntax·asset hashes·HTML(166개 ID)·diff check 통과. 내장 브라우저에서 날짜 필터·빈 결과 복귀·작업 패널·추천/피드백 폼/비교·모바일 상세·일정 시간 편집 진입·새로고침·로그아웃 검증. 390px 및 320px/다크/글자200%에서 가로 넘침 없음, 키보드 선택 포커스 유지, console error 없음. 실제 apply/undo 전체 흐름과 Python 전체 suite는 이번 UI 라운드에서 재실행하지 않았다.

기존 변경 보존. 백엔드·DB·비용·인증 계약 변경 없음. 실기기·운영 로그인·Render 신규 배포 미검증. 도쿄0/바르셀로나0 실제 검수 후보·엄격 리뷰OFF와 기존 배포 제약 유지. 유료 호출·새 의존성·commit/push/배포 없음.


## 19. 실제 배포 요청 — 2026-10-06 (진행 중)

사용자가 직접 사용할 수 있도록 실제 배포를 요청했다. 기존 Render `travel-inbox-rag` Free/Docker, 연결 main, 자동배포 On Commit, 이전 성공 revision `aefee57`을 콘솔에서 확인했다. Environment에는 기존 OpenAI/Tavily 키만 있고 새 DB/OIDC 설정이 없으므로 새 버전을 아직 배포하지 않았다. Supabase hii의 Session pooler5432 연결 정보는 확인했으며 실제 비밀번호/키는 노출하지 않았다.

- 전체 Python 회귀 **732 passed / 11 skipped / 2 warnings (86.70s)**, Node **33 passed**. 이후 추가한 비밀 입력 변환 시험 **5 passed**.
- 사용자 요청에 따라 `deploy/render-supabase/.env`를 권한600으로 생성하고 Git 제외를 검증했다. 기존 루트 `.env`는 보존. 사용자 입력: hii DB 비밀번호, 서버 Storage key, Google OIDC ID/Secret, 본인 로그인 이메일. 세션 난수와 운영/무료 설정은 파일에 준비했다.
- `scripts/prepare_render_env.py`는 비밀값을 출력하지 않고 누락/지정 프로젝트/Session pooler/TLS/운영 설정을 검사한다. Render import 파일을 권한600으로 만들며 비밀번호를 인코딩하고 유료 키를 빈 값으로 고정한다. 입력 파일과 출력 파일 모두 Git 제외다.
- [입력·적용 절차](../operations/DEPLOYMENT_HANDOFF.md)를 추가했다. 운영 변경 전에 별도 `codex/private-beta-launch` 브랜치에서 코드를 준비한다. 기존 서비스의 인증이나 기동 gate를 약화하지 않는다.

로컬 Docker native arm64 빌드 `travel-inbox-beta:20261006` 성공. amd64 지정 빌드는 호스트의 legacy Docker builder platform metadata 문제로 실패하여 Render Linux amd64 검증으로 간주하지 않는다. staged diff 검사는 기존 PRD의 의도된 Markdown hard-break 3줄을 제외하고 통과했다.

DB 비밀번호 확인/필요 시 소유자의 직접 재설정과 Google 웹 클라이언트 발급/입력 대기다. 실제 새 배포·HTTPS 로그인·Supabase 왕복/재시작/외부 복원은 아직 미검증이다. 새 지출·지인 초대 발송 없음.

배포용 코드 커밋 `039c63c`를 `origin/codex/private-beta-launch`에 push했다. `main` 및 기존 Render 서비스는 변경하지 않았다. Docker native 이미지의 UID1000·비밀 .env 미포함·최신 public shell v12 포함을 검증했고, 설정 없는 실제 launcher가 exit78로 거절하는 것을 확인했다. 입력 파일은 권한600이며 Git tracked 목록에서 제외됨을 재확인했다. 새 운영 서비스 배포는 아직 실행하지 않았고, Google 웹 클라이언트 및 hii 비밀값 입력이 남았다.


### 19.1 배포 재개·입력 단순화·Supabase 연결 (2026-10-06)

- `.env`를 사용자 입력 5개(OIDC ID/Secret, hii DB 비밀번호/서버 key, 본인 이메일)와 기존 세션 키만 남기도록 정리했다. 6개 값은 정리 전후 동일함을 검증했다. 고정 주소·무료 정책·경로·job 설정은 배포 변환 스크립트가 채운다. 루트 개발용 `.env`는 변경하지 않았다.
- Supabase `travel-private` 서버 key GET 200 및 private=true, hii Session pooler 5432 DB 연결을 실제 확인했다. 일반 OS CA만으로는 인증서 검증이 실패하여 공식 Supabase Root 2021 CA를 Docker 이미지에 포함했다. `verify-full`과 호스트 검증을 유지한다. 공개 인증서 지문·만료·출처는 `deploy/render-supabase/CERTIFICATE.md`에 기록했다.
- 실제 운영 드라이버(libpq)의 CA/호스트 검증과 자격 증명 연결 통과. Python 3.13 일반 SSLContext의 strict 검사는 해당 공급자 루트 인증서의 keyUsage 부재로 실패했으므로 이를 DB 운영 드라이버 결과와 혼동하지 않는다. 인증서 검증을 끄지 않았다.
- 설정 변환/인증서/최소 입력 시험 **9 passed**, diff check 통과. native arm64 Docker `travel-inbox-beta:20261006-tls` 빌드 성공.
- 실제 hii에 전용 `travel` 스키마를 기동 migration으로 생성하고 512MiB 제한 로컬 운영 컨테이너에서 `/health/live` 200, `/health/ready` 200(schema/storage/dispatcher/identity/restore true), 비로그인 `/api/v2/trips` 401 확인. 단일 시점 약66MiB/CPU0.26%이며 5명 부하 측정은 아니다. identity=true는 설정 확인이고 실제 Google 로그인 성공을 뜻하지 않는다.
- Render의 기존 운영 배포는 아직 `main/aefee57`이다. 설정 파일을 가져오려면 Chrome ChatGPT 확장프로그램의 파일 URL 접근 허용 또는 사용자의 직접 파일 선택이 필요하다. `.env.render`는 준비했고 비밀값을 출력하지 않았다. 실제 Render 신규 배포·HTTPS OAuth·재시작/복원 검증은 계속 진행 중이다.


### 19.2 Render Free 실제 배포·Google 로그인·재시작 검증 (2026-10-06)

**기본 비공개 베타 배포 완료.** 실제 URL: https://travel-inbox-rag.onrender.com . Render 기존 Free 서비스와 Supabase hii를 재사용했다. 운영 revision은 `b90c44965b928c7529fcfce09dc21e919d4f5036`, 배포 ID `dep-db28rvjncjis73dqkaqg`, 15:00 KST Live(콘솔 소요1분46초)다. 이것을 전체 추천 데이터/유료 AI/5명 베타 검증 완료로 해석하지 않는다.

- Chrome 파일 URL 권한 적용 후 `.env.render`를 실제 Render Environment에 가져왔다. 기존 중복 OpenAI/Tavily 행을 제거하고 **활성31개 키 중 중복0·유료3개 키 빈 값**을 확인해 Save only 했다. 비밀값은 Git/출력에 기록하지 않았다.
- 서비스와 Blueprint를 `codex/private-beta-launch`로 연결, Auto-Deploy Off/Auto Sync No 유지. 공개 기본 main은 변경하지 않았다. Free에서 거절된 `maxShutdownDelaySeconds`를 삭제하고 Blueprint 검증을 통과했다. health 경로 변경에 따라 구형 이미지로 시작된 자동 재배포는 취소한 뒤 최신 커밋을 명시적으로 수동 배포했다.
- 실제 HTTPS GET `/` 200, `/health/live` 200, `/health/ready` 200(schema/storage/dispatcher/identity/restore 모두 true). 비로그인 신규 `/api/v2/trips`와 기존 `/api/bookings`는401, 잘못된 Origin의 로그인 POST는403. 로그인 리다이렉트는 Google·정확한 callback·openid/email/profile·PKCE S256이며 임시 쿠키 Secure/HttpOnly/SameSite=Lax를 확인했다.
- **실제 Google 계정 로그인 성공**: 지정한 본인 이메일에 발급한 초대 코드를 사용하고 Google 계정 선택→callback→여행 화면까지 확인했다. 외부 OIDC 미설정 시험이 아니다. 입력의 `DEPLOY_ADMIN_EMAIL`과 검증된 Google 사용자가 일치함을 확인한 뒤 기존 운영 CLI로 admin 역할을 적용, 세션 회수 및 초대 코드 없는 재로그인까지 확인했다. 타인 초대 메시지는 발송하지 않았다.
- Chrome/macOS 실제 브라우저: `배포 확인용 도쿄 여행`(2026-10-15~17) 생성→`저장 확인용 점심 · 실제 예약 아님` 수동 예약→날짜 질문→총1개·시각 미확인 응답→직접 입력 근거 모달→새로고침 조회를 통과했다. 실제 외부 식당 예약이 아닌 합성 검증 자료이며 사용자 계정에 남겼다.
- Render 15:04 KST Restart service 실행, 신규 프로세스 기동·이전 프로세스 정상 종료 로그 확인. 작업자 인계 중 readiness503(dispatcher false)을 관측했고 이후200·전체true로 회복했다. 서버 재시작 및 재로그인 후 동일 여행·예약1건을 확인했다. 중단 없는 재시작이나 무지연 복구를 주장하지 않는다.
- 실제 hii의 로그인 전 스키마 암호화 백업→로컬 격리 복원 성공: 백업16.29초, 복원0.04초, integrity ok, FK 오류0, archive768044bytes. 당시 users/trips/bookings/itineraries0이므로 실사용 자료·메일·일정의 전체 복구 시험은 아니다. 파일/암호키는 Git 제외 `data/beta-release/`에 권한600으로 저장했다. 첫 시도는 서버 기동 DDL과 읽기 잠금 deadlock으로 안전하게 중단됐고 기동 완료 후 재실행했다. **현재 수동 백업을 배포/재시작 migration과 동시에 실행하지 않는다.** 자동 오프호스트 백업과 populated 복구 검증은 남았다.
- 무료 정책 유지: ZERO_SPEND1·halted·예산0·유료 키 제거. 새 메일 AI 추출/임베딩·자유형 AI·외부 지도/검색/리뷰 OFF. 저장/수동 입력/SQL 날짜 질문/비용 없는 계산은 사용 가능. 도쿄·바르셀로나 승인 운영 추천 후보는 각각0, 엄격 언어 추천 OFF. 실제 모바일 기기·최대5명 동시 부하·추천/일정 전체 실환경 E2E는 이번 배포 시험에 포함하지 않았다.

재실행 순서와 제한은 `docs/service-v2/reports/live-deployment-2026-10-06.md`에 기록했다. 로컬 전체 시험 결과와 실제 배포 시험 결과는 별도로 유지한다.

## 20. V3 입력 피로 개선·100도시·메일 체험팩 (2026-10-06)

기획·상세2단계 프롬프트와 긴급 수정은 `docs/service-v3/`로 이어간다. [상세 PRD](../service-v3/PRD.md), [1단계](../service-v3/prompts/01-simple-trip-and-discovery.md), [2단계](../service-v3/prompts/02-stay-distance-and-itinerary.md), [실제 진행 기록](../service-v3/IMPLEMENTATION_STATUS.md).

100도시 registry/시간대·통화/탐색 조건, Madrid 체류일 오류 수정, 오래된조건 차단, 날짜 연도4자리 범위, 간단 여행 생성, 합성메일12개+다운로드를 구현했다. 전체748 passed/11 skipped 및 이후관련73 passed, Node36 passed. 로컬Chrome 실제 생성·탐색·조건저장·새로고침·메일ZIP다운로드·390px 가로넘침없음 확인. 실제숙소 지점확인/도보경로/전체메뉴재편은 후속구현이다. 실제후보없는100도시를 맛집추천완성으로 보고하지 않는다.


## 21. 여정 디자인·사진·무료 도시 탐색 (2026-10-07)

후속 현재 구현은 [V3 진행 기록](../service-v3/IMPLEMENTATION_STATUS.md)과 [실제 검증 보고서](../service-v3/reports/yeojeong-design-validation.md)를 따른다. 이름·폰트·사진3장·공개지도100도시 보조 검색을 구현했고, 기존 인증/개인 여행/무료 정책과schema12를 유지한다. 이전 절의 후보0/배포 전 상태는 당시 이력이며 최신 배포 여부는 연결한 보고서에서 별도로 확인한다.

여정 최종 운영 revision은 `9c97e86`, 배포 `dep-db2i79mgekts73cfodrg`(2026-10-07 Live)다. 디자인·사진·무료도시탐색과 결과 조회 중 진행 표시를 반영했다. 최종 시험 Python962passed/15skipped·Node99passed 및 실제 운영 검증 범위는 위 V3보고서에 기록한다.

## 22. 무료 메일 분석 복구·Pretendard·필터 개편 (2026-10-07)

기존 유료 추출/임베딩 의존 때문에 실패하던 메일 흐름에 무료 기본 분석·SQL 질문을 추가했다. 사용자 교정 및 원문 보존, 형식 미지원 안내, 교정 시각 충돌 비교/일정 확정 차단을 적용했다. Pretendard와 탭형 필터를 사용한다. 최종 회귀는 Python1020/Node119/PostgreSQL58 통과, Python15건 외부 환경 의존 skip이다. 라이브 AI 호출은 수행하지 않았다. 상세 구현·실제 브라우저·배포 상태는 [검증 보고서](../service-v3/reports/mail-recovery-pretendard-validation.md)를 따른다. schema migration 없이 기존 무료 인프라를 유지한다.
