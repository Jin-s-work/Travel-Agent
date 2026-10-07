> **2026-10-06 적용 변경:** 사용자가 기존 Render Free + Supabase Free(`hii`)를 선택했다. 운영 DB는 PostgreSQL, 원문은 private Storage, 검색은 pgvector로 전환한다. 아래 SQLite/Chroma/영구 VM 설명은 로컬 모드와 이전 설계 이력이다. 현재 배포·비용·백업 계약은 [전환 안내](../operations/RENDER_SUPABASE.md)를 우선 적용한다. 새 버전 실제 배포는 별도 검증 상태다.

# Travel Agent 비공개 베타 기술 명세

작성일: 2026-10-01. 이 문서는 **구현 제안**이며 현재 서비스에 아래 기능이 구현되었다는 뜻이 아닙니다.
현재 코드 `aefee57`의 FastAPI·PWA·예약 추출·Chroma 검색을 출발점으로 합니다. 제품 범위는 [PRD](PRD.md), 데이터 이용 판단은 [데이터와 운영 검토](DATA_AND_OPERATIONS.md)를 함께 따릅니다.
우선 대상은 본인과 초대받은 소수 지인이며, 각 사용자는 자신의 여행을 관리합니다. 공유 여행·공동 편집·공개 가입은 필수 선행조건이 아닙니다.

## 1. 실행 구조와 변경 경계

단일 FastAPI 프로세스 안에 HTTP 처리와 내구성 있는 작업 실행 루프를 둡니다. 작업 기록의 기준은 SQLite이고 프로세스 메모리는 실행 상태의 복제본입니다.
PWA는 같은 출처의 API를 호출합니다. 초기에는 Redis·별도 큐 서비스·새 프런트엔드 프레임워크를 도입하지 않습니다.
원본 메일과 SQLite·Chroma는 영구 디스크의 서로 다른 경로에 두며 정적 파일 경로에서는 접근할 수 없습니다.
외부 검색·LLM·장소·크롤러 호출은 공급자 어댑터를 통과합니다. 공급자가 중단되어도 저장된 여행·예약·일정의 읽기는 가능합니다.

| 제안 모듈 | 책임 | 기존 코드와 연결 |
| --- | --- | --- |
| `api.py`, `src/http/` | 앱 조립, 인증, DTO, 오류·SSE | 현재 엔드포인트를 기능별 라우터로 이동 |
| `src/auth/` | 초대, 세션, 소유권, CSRF | 새 모듈; 모든 기존 개인 API에도 적용 |
| `src/db/` | SQLite 연결, 마이그레이션, 트랜잭션 | 새 모듈; 구조화 사실의 기준 저장소 |
| `src/trips/` | 여행 조건, 예약 교정, 일정 버전 | 현재 여행 시작일 전역값을 대체 |
| `src/documents/` | 업로드, 문서 버전, 복수 예약 추출 | `loader.py`, `parser.py`를 adapter로 재사용 |
| `src/search/` | 검색 세대, 조회 범위, 예약 근거 조립 | `indexer.py`, `store.py`, `rag.py`를 분리 |
| `src/jobs/` | DB 작업 큐, lease, 취소, 재개 | `_job`, `BackgroundTasks`에 의존한 상태를 대체 |
| `src/providers/` | API·수집 어댑터, 정책·예산 gate | Tavily 도구를 직접 호출하지 않고 공통 계약 사용 |
| `src/research/` | 출처, 지점 대조, 리뷰 관측, 검토 | 공급자 원문과 제품에 쓸 사실을 분리 |
| `src/recommendations/` | 필터, 점수, 다양성, 이유 | 결정적 순위 계산 후 제한된 설명 생성 |
| `src/itineraries/` | 시간 구간, 배치, 충돌, 부분 재계산 | 새 모듈; LLM이 최종 시간표를 확정하지 않음 |
| `src/observability/` | 요청·작업 지표, 비용, 상태 | 원문·예약번호를 제외한 구조화 기록 |
| `web/js/`, `web/css/` | API client, 여행·추천·일정 화면 | 단일 `index.html`에서 기능별로 추출 |

기존 순수 함수와 가짜 메일 fixtures는 보존합니다. API v2 전환 중에도 인증 없는 구 API로 우회할 수 없어야 합니다.
`get_store()`가 전역 공용 예약을 반환하는 계약은 폐기하고 `TripSearchContext`를 반드시 받도록 변경합니다.
각 요청의 context는 `request_id`, 인증 주체, `trip_id`, 권한 검증 결과, 데이터 버전으로 구성합니다. 도구에서 전역 마지막 출처를 읽지 않습니다.

## 2. 저장소 공통 규칙

모든 내부 ID는 서버가 생성한 불투명 ID입니다. 사용자 입력 파일명·상호·이메일을 기본키 또는 저장 경로로 사용하지 않습니다.
모든 개인 행은 직접 `owner_id/trip_id`를 갖거나 소유자가 명확한 상위 행을 참조합니다. API가 받은 `owner_id`로 권한을 결정하지 않습니다.
시각 기록은 UTC instant와 원래의 IANA 시간대·현지 시각을 구분합니다. 날짜만 있는 사실에 임의의 자정 instant를 부여하지 않습니다.
금액은 부동소수점 대신 통화와 정수 최소 단위 또는 Decimal 문자열로 저장합니다. 원화·엔화·유로의 소수 단위를 동일하게 가정하지 않습니다.
SQLite 연결마다 foreign keys를 활성화하고 WAL, 제한된 busy timeout, 짧은 쓰기 트랜잭션을 사용합니다. 네트워크 호출 중 DB 쓰기 잠금을 잡지 않습니다.
스키마에 `created_at`, `updated_at`, 필요 시 `deleted_at`, `version`을 둡니다. 삭제 표시와 실제 원문 제거의 상태를 구분합니다.

| 엔터티 | 핵심 필드와 연결 | 필수 인덱스·불변 조건 |
| --- | --- | --- |
| `users` | auth subject, 초대 상태, role, session_epoch | 인증 공급자·subject 조합 unique |
| `invitations` | 대상, token_hash, expires_at, used_at, revoked_at | token_hash unique; 원본 초대 토큰 미보관 |
| `sessions` | user_id, token_hash, expires_at, epoch | token_hash unique; 만료·사용자별 조회 |
| `trips` | owner_id, 조건 JSON, version, active_index_id | `(owner_id, deleted_at, updated_at)` |
| `trip_stops` | trip_id, 도시·체류 날짜·시간대·숙소 기준점·추천 지원 상태 | `(trip_id, sequence)` unique; 미지원 도시의 예약 보존 |
| `source_documents` | trip_id, opaque_path, content_hash, active_generation_id | `(trip_id, content_hash)`; 파일명 unique 금지 |
| `document_generations` | document_id, parse_version, status, extracted JSON | `(document_id, generation_no)` unique |
| `bookings` | trip_id, document_id 또는 수동 입력, stable item key, status | `(trip_id, deleted_at)`; 복수 예약 허용 |
| `booking_events` | booking_id, event_type, 양단 시각·시간대, 장소 | `(trip_id, start_instant, end_instant)` |
| `booking_overrides` | booking_id, field_path, value, editor_id, revision | 활성 field_path unique; 수정 이력 별도 보존 |
| `trip_index_generations` | trip_id, collection_name, source_manifest, state | collection_name unique; 준비 완료 후만 활성화 |
| `place_identities` | 내부 지점 ID, 검증한 이름·주소, 외부 ID 연결 | `(provider, external_id)` 연결 unique |
| `evidence_sources` | URL, source_group, 발행·확인 시각, policy_id | 정규 URL·내용 fingerprint; 재전재 그룹 구분 |
| `place_facts` | place_id, field, value, status, source_id, 유효기간 | `(place_id, field, expires_at)`; 상충 사실 공존 |
| `research_candidates` | place_id, 유형, 근거 상태, 검토자·검토 버전 | `(city_id, recommendation_type, status)` |
| `collection_runs` | provider, query spec, pagination·정렬·기간·coverage | run ID로 원본·집계 추적; 완료 여부 명시 |
| `review_observations` | run_id, opaque review key, 원문 언어·판별 상태 | 허용된 중복 제거 key unique; 개인 프로필 배제 |
| `provider_policies` | 기능별 허용 여부, TTL, 검토 기록, policy version | 공급자·기능·버전; 활성 정책 하나 |
| `recommendation_runs` | trip revision, 후보·점수 근거·정책 버전, state | `(trip_id, created_at)`; 결과 재현 가능 |
| `itineraries/items` | trip_id, version, 고정 여부, 검증·예약 상태 | 여행별 목록; item ID는 편집 간 안정적 |
| `bookmarks/comparison_sets` | owner/trip, 입력 URL·메모, 장소 연결 상태 | 비교 묶음에 동일 장소 중복 금지 |
| `reservation_tasks` | trip/item, 작업 종류, 출처·오픈 규칙, due_at, 상태 | 날짜 계산 버전·timezone 보존 |
| `itinerary_alternatives` | 기준 item/version, 대체 후보, 조건·검증 상태 | 원 일정에 자동 반영하지 않음 |
| `jobs/job_events` | 범위, payload reference, lease, attempt, 결과·진행 | `(state, available_at, priority)`; 이벤트 sequence |
| `usage_reservations/ledger` | 호출 ID, 예산 scope, 예상·실제 사용, 상태 | 호출 ID unique; 사용자·전역·월 scope 조회 |
| `deletion_tombstones` | target, deletion_epoch, 범위, 요청·완료 시각 | target unique; 복원 후에도 삭제 우선 |

`TripMember`를 나중에 추가하더라도 첫 버전 권한은 `trips.owner_id == session.user_id`입니다. 지인 초대는 같은 여행의 편집 권한을 의미하지 않습니다.
공개 장소 사실은 공용일 수 있지만 개인 선호·제외·메모·방문 후 평가는 사용자 또는 여행 범위입니다. 운영자 권한을 일반 지인 계정에 부여하지 않습니다.
동일 메일의 중복 판정은 여행 안에서 수행합니다. 타인의 파일 존재 여부가 hash 충돌 응답·처리시간으로 드러나지 않도록 전역 dedup 결과를 반환하지 않습니다.
계약 종료·보관 만료로 삭제해야 하는 공급자 데이터는 사용자 선택 기록과 분리합니다. 내부 장소 ID와 사용자의 직접 입력까지 함께 잃지 않도록 합니다.

## 3. 업로드·추출·검색 세대 전환

업로드 요청은 파일별 1MiB를 초기 기본값으로 유지하고 요청당 파일 수·총량도 별도 제한합니다. 값은 운영 설정이며 실제 메일 크기 분포를 보고 조정합니다.
확장자만 검사하지 않고 허용한 MIME·디코딩·메시지 크기 제한을 확인합니다. 메일의 링크·첨부·스크립트는 실행하지 않습니다.
저장은 임시 파일 → 크기·해시 검증 → 서버 ID 경로로 이동 순서입니다. 파일은 정적 경로 밖에 놓고 다운로드마다 소유권을 확인합니다.
문서 하나의 추출 결과는 예약 배열입니다. 왕복 항공·다구간·숙소와 부가 투어를 별도 booking/event로 만들며 알 수 없는 구간은 확인 필요로 남깁니다.
재추출에서는 공급자 예약번호·구간·기존 매칭 key로 기존 booking을 연결합니다. 애매한 대응은 새 예약으로 조용히 복제하지 않고 검토 대상으로 반환합니다.
사용자 교정은 원문 추출 위에 덮는 별도 레이어입니다. 추출 버전 변경이 교정을 덮어쓰지 않으며 충돌하면 사용자가 두 값을 비교할 수 있습니다.

SQLite와 Chroma에는 단일 트랜잭션이 없으므로 **여행별 불변 검색 스냅샷**을 기본 구현으로 제안합니다.
새 문서 하나를 처리하더라도 새 여행 index generation을 만들고, 기존 활성 청크의 벡터는 배치 복사하여 불필요한 재임베딩을 피합니다.
작은 비공개 여행 단위를 전제로 하는 방식입니다. 문서가 커져 복사 비용이 문제라면 활성 generation metadata prefilter 대안을 별도 성능 검증 후 도입합니다.

1. SQL에서 document generation을 `staged`로 만들고 원문 참조·해시·작업 ID를 기록합니다. 현재 활성 예약은 바꾸지 않습니다.
2. 추출·사용자 교정 병합·임베딩을 완료하고 허용 타입, 개수, 벡터 차원과 필수 참조를 검증합니다.
3. 같은 여행의 인덱스 작성 job lease를 확보하고 새 Chroma collection을 만듭니다. 현재 활성 문서와 교체 후보로 manifest를 작성합니다.
4. 청크 수·문서 ID·generation·벡터 차원을 검증하고 index generation을 `ready`로 표시합니다. 이 collection은 아직 검색 대상이 아닙니다.
5. 짧은 SQL 트랜잭션에서 소유권·tombstone·기준 version·job fencing token을 재확인합니다.
6. booking 활성 세대와 `trips.active_index_id`를 함께 전환합니다. 기준 버전이 달라졌으면 새 결과를 활성화하지 않고 재계산합니다.
7. 기존 collection은 진행 중 읽기 참조가 끝난 후 회수합니다. 최소 보존 시간만으로 안전을 추정하지 않고 in-flight 참조를 관리합니다.

검색 요청은 SQL에서 활성 collection을 한 번 읽어 해당 collection에만 질의합니다. `staged` 또는 과거 청크가 top-k 자리를 차지하면 안 됩니다.
검색 후에도 trip/document/generation과 삭제 상태를 검사합니다. 이 검사는 prefilter의 대체물이 아니라 마지막 권한 방어입니다.
답변에 사용할 예약 사실은 SQL의 현재 교정값으로 재조립합니다. 인덱스 갱신 중 오래된 검색 문서가 교정 전 시간을 사실로 출력하지 않아야 합니다.
질문 중 직접 예약번호·날짜 전체 조회는 SQL의 정확·범위 조회를 사용합니다. 검색 갱신 지연으로 그날 예약이나 수동 예약이 누락되지 않습니다.
요청 시작 후 삭제·권한 회수가 발생하면 응답을 보내기 직전에 다시 검사합니다. 결과를 다른 사용자·삭제된 여행의 출처로 반환하지 않습니다.

| 중단 지점 | 재기동 처리 |
| --- | --- |
| staged 원문 저장 전후 | 참조 없는 임시 파일 회수; 유효 원문이면 job 재개 |
| 일부 벡터 저장 후 | manifest로 누락 확인; 같은 generation에 idempotent upsert 또는 폐기 |
| ready 후 SQL 전환 전 | 이전 active 유지; lease와 기준 version 검증 후 재개 |
| SQL 전환 후 완료 이벤트 전 | active manifest를 기준으로 성공 처리; 다시 유료 호출하지 않음 |
| 이전 collection 회수 도중 | 현재 active와 in-flight 대상은 제외; 나머지만 반복 삭제 |
| active collection 손상·누락 | 예약 SQL 조회는 유지; 검색은 복구 중으로 반환하고 재생성 job 생성 |

## 4. 내구성 작업 실행

FastAPI lifespan에서 dispatcher 하나를 시작합니다. 기본 외부 호출 작업 동시성은 1이며 HTTP 읽기는 별도 처리합니다.
작업 범위는 개인 여행 작업과 관리자 공용 장소 조사로 구분합니다. `scope_kind=personal_trip/admin_research`, 비어 있지 않은 `scope_id`, 실행자 `actor_id`를 저장합니다. 개인 작업은 `trip_id`와 소유권을 필수로 확인합니다. 관리자 조사는 `trip_id=null`을 허용하되 별도 관리자 role·정책·장소 범위를 검사하며 임의 개인 여행에 배정하지 않습니다. 멱등성 unique key에는 실행자·scope kind·scope ID·operation을 포함하고 nullable trip ID만으로 중복 방지를 구성하지 않습니다. 일반 사용자는 관리자 작업 조회·SSE에 접근할 수 없습니다.
동기 LLM·Chroma 호출은 제한된 worker thread에서 실행해 HTTP event loop를 막지 않습니다. dispatcher의 존재와 heartbeat 상태를 readiness에 반영합니다.
프로세스가 하나여도 HTTP thread와 dispatcher 사이의 권한·예산·상태 경쟁은 DB 트랜잭션으로 처리합니다.
`queued → running → succeeded/partial/failed/cancelled`가 기본 상태 전이입니다. 재시도는 `running → queued`로 돌아가되 attempt가 증가합니다.
`partial`은 일부 결과가 사용 가능하다는 뜻입니다. 성공한 장소 결과를 공급자 하나의 실패 때문에 모두 버리지 않습니다.
작업 claim은 조건부 UPDATE와 lease token 발급을 한 트랜잭션에서 수행합니다. worker가 메모리에서 읽은 상태만 믿고 실행하면 안 됩니다.
초기 lease 90초·heartbeat 20초를 제안하며 실제 작업 시간을 측정해 조정합니다. 외부 호출 timeout은 lease 갱신 가능 여부와 함께 설정합니다.
완료·중간 저장에는 claim 때 발급한 fencing token을 요구합니다. lease가 만료된 이전 실행자는 뒤늦게 결과를 활성화할 수 없습니다.
`Idempotency-Key`의 유효 범위는 개인 작업에서는 인증 사용자·여행·연산이며, 관리자 작업은 위의 실행자·연구 scope·연산을 사용합니다. 같은 key와 같은 정규화 요청 hash는 기존 job을 돌려줍니다.
같은 key에 다른 payload면 409를 반환합니다. 키 보관기간은 API 계약에 명시하고 클라이언트 재시도보다 짧게 정하지 않습니다.
취소는 `cancel_requested_at`을 먼저 기록합니다. 진행 중 HTTP 호출은 취소 가능하면 중단하고 불가능하면 응답을 받아도 제품 상태를 변경하지 않습니다.
여행 삭제는 tombstone 생성 → 새 작업 차단·세션 scope 검증 → 기존 작업 취소 → 원문·인덱스 정리 순서입니다.
삭제 job은 마지막 접근 차단 시점을 사용자에게 즉시 알리고 물리 삭제 완료 상태를 별도로 제공합니다. 삭제 후 202가 곧 디스크 삭제 완료는 아닙니다.
모든 job은 호출 전과 활성화 직전에 tombstone·소유권·입력 revision을 다시 확인합니다. 삭제 전에 큐에 들어간 작업도 예외가 아닙니다.
429·일시적 5xx·연결 실패는 제한된 지수 backoff와 jitter로 재시도합니다. 400·권한·정책 불허는 자동 반복하지 않습니다.
외부 공급자의 exactly-once 실행은 보장하지 않습니다. 공급자 idempotency가 없고 응답 유실이면 과금·실행 상태를 unknown으로 기록합니다.
무한 재시도 대신 전체 deadline, 최대 attempt, 누적 호출 예산을 둡니다. 운영자가 실패 job을 재실행할 때도 새 예산 검사를 거칩니다.

## 5. 인증·초대·개인정보 접근

검증된 인증 라이브러리 또는 인증 제공자를 이용하고 초대 allowlist를 앱에서 별도 확인합니다. 인증 성공만으로 가입이 허용되지는 않습니다.
초대 token은 충분한 무작위값·1회 사용·만료·폐기를 지원합니다. 이메일 기반이면 인증 제공자가 확인한 이메일과 초대 대상을 대조합니다.
쿠키는 `__Host-session`, Secure, HttpOnly, SameSite=Lax, Path=/를 기본으로 하고 Domain은 지정하지 않습니다. 배포 HTTPS를 전제로 합니다.
OAuth/OIDC를 사용한다면 state·nonce·PKCE·허용된 redirect URI를 검증된 라이브러리로 처리합니다. 자체 서명 token 구현을 목표로 삼지 않습니다.
변경 요청에는 Origin 검증과 세션에 결합된 CSRF token을 적용합니다. 로그아웃·업로드·삭제·job 재시도도 변경 요청입니다.
세션 만료·사용자 비활성·초대 회수 후 기존 cookie로 작업 상태와 원문을 읽을 수 없어야 합니다. session_epoch 증가로 일괄 회수를 지원합니다.
미설정 인증은 개인 API를 닫습니다. development bypass는 테스트 dependency override로만 허용하고 운영 환경변수만으로 활성화되지 않게 합니다.
자신에게 없는 여행·job·문서는 404로 통일하며 내부 존재 여부를 설명하지 않습니다. 관리자 연구 화면은 별도 role 검사를 요구합니다.
원본 메일·예약번호·알레르기 같은 개인 조건은 URL query, 추적 이벤트, 일반 로그에 넣지 않습니다. 꼭 필요한 공급자에 최소 입력만 전달합니다.

## 6. API 공통 계약

개인 API prefix는 `/api/v2`입니다. 개인 응답은 기본 `Cache-Control: private, no-store`이며 공개 장소 표시 캐시와 분리합니다.
목록은 cursor pagination을 사용합니다. 서버 최대 page size를 강제하고 범위 없는 전체 예약·전체 출처 endpoint를 만들지 않습니다.
PATCH는 `If-Match` 또는 명시적인 `expected_version` 중 하나를 일관되게 사용합니다. 여기서는 JSON의 `expected_version`을 제안합니다.
날짜는 ISO date, instant는 offset 포함 RFC 3339, IANA zone은 별도 필드입니다. JSON null은 0·false·제한 없음과 다릅니다.

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "여행 정보가 변경되었습니다. 최신 내용을 확인해 주세요.",
    "request_id": "req_example",
    "retryable": false,
    "details": {"current_version": 8}
  }
}
```

오류 code는 `AUTH_REQUIRED`, `NOT_FOUND`, `VALIDATION_FAILED`, `VERSION_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `POLICY_UNAVAILABLE`, `BUDGET_EXHAUSTED`, `PROVIDER_UNAVAILABLE`로 구분합니다.
기본 HTTP 상태는 각각 401·404·422·409·409·422·429·503입니다. 사용자 한도가 아닌 전역 운영 중지는 별도 code로 구분합니다.
공급자 오류 본문·stack trace·파일 경로·키를 클라이언트에 그대로 돌려주지 않습니다. 내부 요청 ID로만 연계합니다.

| API | 입력·응답 핵심 | 완료·권한 계약 |
| --- | --- | --- |
| `GET/POST /trips` | 소유 여행 목록 / 조건과 확인 필요 항목 | 서버가 owner 결정; 생성 201 |
| `GET/PATCH/DELETE /trips/{id}` | 여행 DTO / expected_version / 삭제 job | 삭제 202; 읽기 즉시 차단 |
| `POST /trips/{id}/documents` | multipart files + idempotency key | 파일별 accepted/rejected + job ID, 202 |
| `GET /trips/{id}/bookings` | 날짜·종류·상태 filter | SQL 현재 사실, 모든 구간·미확인 포함 |
| `PATCH/DELETE /trips/{id}/bookings/{bid}` | 교정 patch·version / 삭제 | 교정 provenance 유지; 재색인 job 연결 |
| `GET /trips/{id}/documents/{did}/content` | 인증된 명시적 다운로드 | attachment; 원문을 HTML로 실행하지 않음 |
| `POST /trips/{id}/recommendations` | 방문 조건·후보 모드·trip version | run/job ID 202; 입력 snapshot 저장 |
| `GET /trips/{id}/recommendations/{rid}` | cursor·현재 단계 | 결과·부족 사유·정책·신선도 상태 |
| `GET /trips/{id}/places/{pid}` | 방문 날짜·인원 | 필드별 사실 + 별도 공급자 표시 DTO |
| `POST /trips/{id}/itineraries` | 후보 ID·고정 항목·조건 버전 | 일정 초안 job, 예약 행위 없음 |
| `GET/PATCH /trips/{id}/itineraries/{iid}` | 저장 DTO / 편집·expected_version | 전체 재검증·충돌·unplaced 반환 |
| `POST /trips/{id}/ask` | question, user/assistant history | 예약·연구 source scope별 답변 |
| `GET /jobs/{jid}`, `GET /jobs/{jid}/events` | job snapshot / SSE | 개인 작업은 owner/trip, 관리자 연구는 role/actor/scope 확인 |
| `POST /jobs/{jid}/cancel` | CSRF·사용자 요청 | 취소 요청 상태와 외부 호출 상태 구분 |
| `POST /trips/{id}/bookmarks` | 사용자가 저장한 URL·메모 | 장소 resolve job은 정책·네트워크 검증 후 별도 수행 |
| `POST /trips/{id}/comparisons` | 서로 다른 2~3개 장소 ID·방문 조건 | 같은 기준일·통화·인원으로 비교 DTO 생성 |
| `GET/PATCH /trips/{id}/reservation-tasks/{tid}` | 예약 확인·오픈 대기·마감 작업 | 사용자 완료와 실제 예약 근거 확인을 분리 |
| `POST /trips/{id}/itineraries/{iid}/alternatives` | 대상 item·변경 이유·version | Plan B 후보 반환; 원 일정 변경 없음 |

```json
{
  "trip_version": 8,
  "visit": {"date": "2026-11-06", "local_time": "19:00", "timezone": "Europe/Madrid"},
  "party": {"adults": 4, "children": []},
  "categories": ["restaurant"],
  "recommendation_types": ["local_discovery", "landmark"],
  "review_language_filter": {"mode": "observed_window", "required": true, "apply_only_if_qualified": true}
}
```

위 요청의 `apply_only_if_qualified`는 미지원일 때 조건을 충족했다고 가장하는 옵션이 아닙니다. 응답에 미적용 사유와 대체 탐색 유형을 명시합니다.
`required=true`는 명시적인 엄격 언어 조건입니다. 자료 자격과 언어 조건을 통과한 후보만 결과에 포함하고, 미지원 또는 개수 부족은 그대로 반환합니다. 편집 근거 모델로의 전환은 사용자의 별도 선택과 새 요청으로 처리합니다. `required=false`에서도 근거 없는 언어 점수나 통과 배지를 만들지 않습니다.
추천에는 `requested_constraints`, `applied_constraints`, `unsupported_constraints`를 함께 반환합니다. UI가 요청과 적용의 차이를 숨기지 않습니다.
아동은 나이·예약 기준일을 알면 저장하고 모르면 unknown으로 둡니다. 인원 조건 판정에 필요한 나이가 빠졌다면 적합을 확정하지 않습니다.

```json
{
  "job_id": "job_example", "run_id": "run_example", "state": "queued",
  "status_url": "/api/v2/jobs/job_example",
  "events_url": "/api/v2/jobs/job_example/events",
  "unsupported_constraints": [{"field": "review_language_filter", "reason": "INSUFFICIENT_COVERAGE"}]
}
```

SSE event는 `progress`, `partial_result`, `needs_review`, `completed`, `failed`, `cancelled`입니다. event ID는 job별 단조 증가 sequence입니다.
재연결은 `Last-Event-ID` 이후 저장 이벤트를 재생하고 너무 오래된 cursor이면 snapshot 조회를 요구합니다. 재연결 자체로 작업을 다시 실행하지 않습니다.
SSE에는 원문·예약번호·전체 공급자 응답을 넣지 않고 필요한 DTO ID와 상태만 전달합니다. 최종 결과는 인증된 GET으로 조회할 수 있습니다.
세션 만료·권한 회수 시 연결을 종료합니다. 연결 종료가 job 취소와 같지는 않으며 취소 endpoint만 명시적인 취소를 수행합니다.

```text
id: 12
event: progress
data: {"job_id":"job_example","stage":"verify_facts","done":4,"total":8}

id: 13
event: completed
data: {"job_id":"job_example","state":"partial","result_id":"run_example","warnings":["LANGUAGE_FILTER_UNAVAILABLE"]}
```

## 7. 공급자·크롤러·정책 경계

`ResearchProvider`, `PlaceDisplayProvider`, `ReviewCollectionProvider`, `TravelTimeProvider`는 인증·policy·budget context를 받는 공통 호출 wrapper를 사용합니다.
응답은 `data`, `provenance`, `coverage`, `freshness`, `usage`, `limitations`, `policy_version`을 분리합니다. 공급자 raw payload를 제품 DTO로 그대로 전달하지 않습니다.
Google API, 다른 API, 공개 웹 수집, 수동 확인, 사용자 제공 자료는 서로 다른 adapter입니다. 기술적으로 읽을 수 있다는 이유로 같은 이용 권한을 부여하지 않습니다.
크롤러를 처음부터 불가능한 기능으로 제외하지 않습니다. **자료 접근성과 품질을 알아보는 연구 단계**와 **제품에 데이터를 공급하는 운영 단계**를 별도 기능으로 둡니다.
연구 단계는 먼저 공식 문서·요금·허용 조건·페이지 구조·대체 공급원을 정리합니다. 표본 수집 자체도 해당 접근이 허용된 범위에서 수행합니다.
`crawl_research_enabled`, `crawl_production_enabled`를 분리하고 둘 다 기본 off입니다. 운영 켜짐에는 대상 origin·필드·목적·보관·표시별 검토 기록이 필요합니다.
정책에 수집·저장·파생 통계·LLM·화면 표시 권한을 각각 둡니다. 하나가 허용되었다고 나머지를 true로 전파하지 않습니다.
정책이 불명확하면 해당 실행은 대기 상태로 두고 무엇을 확인하면 가능한지 기록합니다. 이 상태가 영구 불가능이라는 결론은 아닙니다.
robots와 페이지 공개 여부는 접근 검토의 일부이지 모든 저장·재사용 권한의 대체물이 아닙니다. 이 문서는 특정 플랫폼의 법적 허가를 선언하지 않습니다.
수집 실행은 origin allowlist, 연결·전체 timeout, 응답 크기·MIME 제한, 최대 페이지·리뷰·실행시간, per-origin 동시성·간격을 강제합니다.
DNS 해석과 모든 redirect에서 private·loopback·link-local·metadata 목적지를 차단합니다. 파일 URL·비HTTP scheme·임의 JavaScript 실행을 허용하지 않습니다.
동적 페이지 adapter가 필요하면 승인된 origin의 공개 렌더링만 제한된 별도 실행 예산으로 처리합니다. 512MB 웹 프로세스에 브라우저를 상주시켜서는 안 됩니다.
로그인 자격·쿠키·사용자 세션을 크롤러에 자동 전달하지 않습니다. 차단·CAPTCHA·인증 요구를 만나면 stop reason을 남기고 접근 통제를 우회하지 않습니다.
페이지 locale, 원문/번역 상태, 정렬 방식, query, cursor, 수집 기간, 중단 사유를 run에 기록합니다. 요청 언어를 바꿨다고 전체 리뷰의 언어 표본이 되지는 않습니다.
HTML fixture 기반 parser 테스트와 소규모 허용된 라이브 표본 검증을 분리합니다. 화면 구조 변경·빈 응답·중복 반복 페이지를 coverage 실패로 탐지합니다.

## 8. 리뷰 관측과 근거 보존

리뷰 기능은 `discovered → access_reviewed → sampled → quality_reviewed → production_enabled` 상태를 가집니다. 각 단계의 담당자·날짜·미해결 조건을 남깁니다.
최소 관측 필드는 공급자·지점 ID, run ID, 허용된 opaque review key, 게시일 정밀도, 수집일, 원문 언어·판별 신뢰, 평점 척도, 번역 여부입니다.
작성자 이름·사진·프로필 링크·방문 이력은 언어 통계에 필요하지 않으므로 기본 저장하지 않습니다. 국적·민족·거주지·개인 신원을 추정하지 않습니다.
원문은 언어 판별 또는 검수에 필요하고 보관 권한이 확인된 경우에만 별도 raw 저장소에 둡니다. 권한이 없거나 불분명하면 raw TTL=0입니다.
raw TTL은 공급자 허용기간·실험 필요기간·앱 상한 중 가장 짧은 값입니다. 앱의 초기 연구 상한은 24시간을 제안하되 권한을 새로 만들어주는 기간이 아닙니다.
원문 폐기 후 집계·fingerprint·증거 발췌를 계속 보관할 권한도 별도로 확인합니다. raw 삭제만으로 파생 데이터의 무제한 보관이 허용된다고 가정하지 않습니다.
중복 제거는 허용된 review ID를 우선하며 텍스트 hash 사용도 정책 검토 대상입니다. 동일 리뷰의 편집본·번역본을 독립 리뷰로 세지 않습니다.
`전체 별점 평가 수`, `텍스트 리뷰 수`, `관측 수`, `언어 판별 가능 수`, `unknown 수`를 분리합니다. 각 비율은 사용한 분모와 기간을 명시합니다.
제품 모드는 `observed_window`와 `population_estimate`를 분리합니다. 최신순 수집이 전체 리뷰의 대표표본이 아니라는 이유로 관측 구간 자체의 언어 기능까지 배제하지 않습니다.
`observed_window`의 파일럿 정의는 조회 시점 기준 최근 180일 안의 최신 리뷰 기록 최대 K=200개입니다. K에는 텍스트 없는 별점 기록도 포함하며 중간에서 언어별 선택 수집하지 않습니다.
기간 경계 또는 K 도달까지 공급자가 반환한 페이지 구간의 순서·연결을 검증합니다. 이 검사는 플랫폼 전체에서 누락이 없음을 보장하지 않습니다. 정렬 기준은 published_at/edited_at/unknown으로 기록하고 날짜 경계는 같은 기준에서만 판단합니다. `stop_reason`, `records_seen`, 가장 오래된 기록 시각·정밀도, 정렬 검증 상태를 저장합니다.
K에 먼저 도달했다면 "최근 180일 전체"라고 쓰지 않고 실제 관측 시작·종료와 "공급자가 최신순으로 반환한 관측 200건 범위"를 표시합니다. 게시일 정밀도가 낮아 경계가 모호하면 그 불확실성을 보존합니다.
`text_presence`는 present/rating_only/unextractable을 구분합니다. 화면 추출 실패를 텍스트 없는 별점으로 분류해 분모를 줄여서는 안 됩니다.
관측 구간의 `n_records`, `n_text`, `n_classified`, `n_unknown`을 각각 저장합니다. 텍스트가 있어도 언어 판별 불가일 수 있고 별점만 있는 기록은 언어 비율 분모가 아닙니다.
이용 권한·공급자 관측 구간의 연속성·분류 품질 기준을 통과하면 관측 언어 비율을 표시하고 공개된 proxy로 필터·순위에 사용할 수 있습니다. 주민 비율이나 전체 리뷰 구성비로 확대하지 않습니다.
관측 비율의 분모와 미판별 수를 함께 표시합니다. 엄격 조건의 보수적 경계는 현지어 수/n_text, (한국어 수+n_unknown)/n_text 등으로 평가하되 구체적 gate는 [리뷰 데이터 명세](REVIEW_DATA_SPEC.md)를 따릅니다.
부분 수집·관련도순·언어 선택 수집은 탐색용 관측으로만 제공합니다. 수집한 5개를 요약하는 것과 언어 조건을 통과했다고 추천하는 것을 분리합니다.
`population_estimate`는 전수 범위 또는 검증된 확률표본이 확보된 경우에 별도로 활성화합니다. 적절한 추정법 없이 최신순 코호트에 Wilson 구간을 붙여 전체 모집단의 신뢰구간처럼 표시하지 않습니다.
현지어 집합은 도시 설정입니다. 도쿄 ja, 바르셀로나 es/ca로 시작하고 원래 language tag와 정규화한 tag를 함께 둡니다.
언어 조건 pass/fail/unsupported 판정은 서버가 수행합니다. unknown 제외가 결과를 과장하는지 검증 전 엄격 통계 필터를 켜지 않습니다.
평점·리뷰 규모와 언어 통계는 같은 지점·플랫폼·기간 기준을 확인합니다. 서로 다른 플랫폼의 숫자를 하나의 분모처럼 합치지 않습니다.
보관 만료·정책 철회·원문 수정으로 근거가 바뀌면 영향을 받은 fact·집계·추천을 stale 또는 unsupported로 전환합니다. 기존 결과에 남은 파생 표시도 갱신합니다.

## 9. 추천과 시간표 엔진

추천은 정책·지점 일치·폐업·명시적 인원/식단 불일치 → 시간대 적합성 → 점수 → 다양성 → 설명 순서로 실행합니다.
점수 가중치·지역 근거 정의·언어 필터 임계값은 PRD의 versioned config를 사용합니다. 결측값을 좋은 점수로 정규화하지 않습니다.
후보마다 `eligible/ineligible/needs_confirmation`과 reason codes를 저장합니다. 좋은 별점이 휴무·예약 인원 불일치를 상쇄하지 못합니다.
추천 설명은 입력 후보 ID·순서·서버 compatibility를 변경할 수 없습니다. 모든 사실 문장에 source ID를 붙이고 미확인 조건은 서버 목록을 그대로 표시합니다.
시간표의 interval은 `[start, end)`입니다. 종료와 다음 시작이 같더라도 장소가 다르면 이동·버퍼가 있어야 하므로 단순 비중복만으로 적합 판정하지 않습니다.
항공은 출발지·도착지의 현지 시각과 IANA zone을 각각 저장하고 UTC instant로 소요시간·충돌을 비교합니다. 날짜 변경선을 건너는 구간도 같은 방식으로 처리합니다.
DST의 존재하지 않는 현지 시각은 입력 오류 또는 사용자 확인 대상으로 처리합니다. 두 번 존재하는 시각은 offset/fold를 명확히 선택하기 전 확정하지 않습니다.
영업시간은 요일별 여러 구간과 다음날 종료를 지원합니다. 월요일 22:00~화요일 02:00을 월요일 22:00~02:00의 음수 길이로 계산하지 않습니다.
방문 전체 체류시간이 영업 구간 안에 들어가야 합니다. 마지막 입장·주문은 별도 시작 제약이며 브레이크타임·특별 휴무가 통상 영업표보다 우선합니다.
미래 날짜에 특별 일정이 확인되지 않았다면 통상 영업 구간을 provisional로 사용합니다. 모델이 미래 영업 확정값을 만들어 채우지 않습니다.
숙박은 기간 전체를 busy interval로 만들지 않습니다. 체크인 가능 구간·체크아웃 마감·짐 보관 확인 여부와 기준 위치로 모델링합니다.
고정 예약·연령·명시 인원 제한·방문 구간·최소 이동과 예약 버퍼는 hard constraint입니다. 인기·음식 다양성·걷기·비용·밀도는 soft preference입니다.
알레르기 정보가 없다는 사실은 안전하다는 뜻이 아닙니다. 관련 장소는 확인 필요로 분리하고 자동 확정하지 않습니다.
좌표·경로는 허용된 출처와 정확도를 가진 경우만 사용합니다. 직선거리 기반 도보 추정은 provider route와 구분하며 바다·큰 도로·대중교통 환승을 실제 경로처럼 보장하지 않습니다.
경로가 없으면 이동시간을 0으로 두지 않습니다. 배치 초안은 가능하되 `unknown_travel`로 두고 방문 가능 검증 완료를 막습니다.
제약 결과는 `satisfied/violated/unknown`으로 구분합니다. 확인된 위반은 항상 배치를 거절하고, unknown은 기본 엄격 모드에서 미배치합니다. 사용자가 잠정 초안을 명시적으로 허용한 경우에만 확인 필요 표시와 함께 provisional로 배치할 수 있습니다. 작업의 성공 여부와 일정의 `validated/provisional/conflicted` 상태는 별개입니다.
알고리즘은 고정 이벤트 배치 → 지역별 후보 군집 → 가능한 빈 구간 평가 → 결정적 탐욕 삽입 → 전체 검증입니다. 동점은 안정적인 ID 정렬로 재현합니다.
후보를 모두 넣는 것이 목표가 아닙니다. 미배치 장소마다 `NO_TIME_WINDOW`, `PARTY_MISMATCH`, `NO_ROUTE`, `CONFLICTING_LOCKS` 등의 이유를 반환합니다.
고정 예약끼리 충돌하면 원본을 보존하고 충돌 목록을 반환합니다. 두 건 중 하나를 삭제하거나 이동해 성공처럼 만들지 않습니다.
사용자가 항목을 옮기면 앞뒤 이동·영업·고정 예약을 다시 검사하고 영향받은 구간만 재계산합니다. 저장 상태와 예약 확정 상태는 별도 enum입니다.

### 비교·Plan B·예약 할 일

비교 DTO는 장소마다 같은 방문일·인원·예산 기준으로 이동 근거, 가격, 예약 방법, 인원 조건, 확인 필요, 언어 관측 범위를 나란히 반환합니다.
가격·평점·언어 관측의 기준이 다르면 그 차이를 표시합니다. 다른 플랫폼 별점을 보정 없이 한 순위 숫자로 합치지 않습니다.
Plan B는 비·휴무·예약 실패·과도한 이동 등 사용자가 선택한 이유에 대해 같은 시간 구간에 들어갈 후보를 재검증합니다.
대체 후보에도 동일한 hard constraints와 데이터 신선도 규칙을 적용합니다. 사용자가 채택하기 전에는 원래 장소·고정 예약을 변경하지 않습니다.
대체할 항목이 이미 예약된 항목이면 취소 규정·비용 확인을 별도 작업으로 남깁니다. Plan B 채택을 공급자 예약 취소로 실행하지 않습니다.
예약 할 일은 `확인 필요/예약 오픈 대기/예약 진행/사용자 완료/근거 확인/취소` 상태를 가집니다. 외부 예약 화면 방문만으로 완료 처리하지 않습니다.
예약 오픈 규칙에는 시설 시간대·매일 또는 월별 규칙·유효기간·예외 근거를 저장합니다. 공식 규칙이 없으면 오픈 날짜를 추정 생성하지 않습니다.
날짜 계산 결과가 모호하거나 DST와 겹치면 확인 필요로 반환합니다. 앱 안의 마감 표시와 외부 알림 발송은 별도 기능이며 초기에는 앱 내 목록으로 충분합니다.
URL 북마크는 저장 즉시 임의 fetch하지 않습니다. 운영 정책과 URL 검증을 통과한 resolve job만 실행하고 미지원이면 사용자 메모·원래 링크 상태로 남깁니다.
북마크에 붙여넣은 자료도 출처·사용 범위를 보존합니다. 사용자가 URL을 입력한 사실이 플랫폼 원문 전체의 수집·보관 권한을 부여하지 않습니다.
그룹 투표와 공동 편집은 다음 단계입니다. 현재 comparison/bookmark의 존재만으로 다른 지인에게 여행 또는 선호를 공개하지 않습니다.

## 10. 오프라인 이용과 브라우저 저장

기본은 현재 서비스 워커처럼 앱 셸만 캐시하고 개인 API 응답을 자동 캐시하지 않는 방식입니다.
추가 기능으로 사용자가 기기·여행별 `오프라인 일정 저장`을 명시적으로 선택할 때 최소 일정 snapshot만 IndexedDB에 저장합니다.
snapshot에는 이름·날짜·시각·사용자 메모·허용된 방문 링크만 선택적으로 넣습니다. 원본 메일·예약번호·결제정보·세션 token·공급자 raw 리뷰는 제외합니다.
공급자 정책이 오프라인 저장을 허용하지 않는 필드는 snapshot에서 제외합니다. 로그인된 화면에 보였다는 사실만으로 기기 보관 권한을 추정하지 않습니다.
사용자 ID namespace와 schema version을 분리하고 로그아웃·계정 전환·로컬 삭제 시 snapshot을 제거합니다. 앱 셸 cache에 개인 JSON을 넣지 않습니다.
오프라인에서는 마지막 동기화 시각·확인 필요 상태를 표시하며 신규 예약 확인·최신 영업 확인을 했다고 표시하지 않습니다.
오프라인 편집은 첫 버전에서 읽기 전용입니다. 나중에 편집을 허용하면 서버 version 대조와 명시적 충돌 해결을 별도로 설계합니다.
기기가 오프라인인 동안 서버의 원격 삭제·초대 회수를 즉시 적용할 수는 없습니다. 짧은 로컬 만료와 다음 연결 시 삭제 동기화를 제공하고 이 한계를 저장 전 설명합니다.

## 11. 예산·관측·복구

외부 호출 전에 SQL 쓰기 트랜잭션에서 사용자 일일·전역 일일·월 예산을 함께 검사하고 예상 최대 비용을 예약합니다. 동시 요청이 남은 예산을 중복 소비하지 못하게 합니다.
LLM 예상치는 입력 크기·출력 토큰 상한·도구 호출 상한으로 계산합니다. API 단가·SKU·무료량은 운영자가 확인한 versioned config에서 읽고 이 문서에 고정하지 않습니다.
공급자별 무료량·rate limit도 비용과 별도 quota로 관리합니다. 다른 앱의 계정 사용을 관측하지 못하면 무료 잔여량을 확정하지 않습니다.
응답 뒤 실제 usage로 정산합니다. timeout처럼 과금 여부가 불명확하면 예약액을 즉시 환급하지 않고 unknown-charge 상태와 보수적 상한을 유지합니다.
재시도는 별도 attempt 비용을 예약합니다. 부분 성공·사용자 취소·worker crash도 ledger에 남고 숨은 재시도로 예산을 우회하지 않습니다.
`/health/live`는 프로세스 생존, `/health/ready`는 DB·디스크·마이그레이션·dispatcher 상태를 검사합니다. 매번 외부 유료 API를 호출하지 않습니다.
측정값은 요청 지연, queue age, 작업 duration/attempt, index 상태, provider timeout, 정책 차단, budget 예약·정산, 빈 후보 비율, unknown 비율입니다.
trace에는 request/job/run ID와 익명화한 내부 참조만 남깁니다. 연구 원문·사용자 질문 전체·LLM 입출력은 기본 로그에서 제외합니다.
SQLite는 일관된 backup 절차를 사용하고 원본 manifest·정책 버전·schema version을 함께 보관합니다. 파일 복사 한 번이 실행 중 DB의 안전한 백업이라는 가정은 하지 않습니다.
외부 백업은 암호화하고 운영 디스크와 다른 장애 범위에 둡니다. 백업 주기·보관기간·복원 책임은 배포 ADR에 확정합니다.
삭제 tombstone의 최신 checkpoint를 오래된 DB 백업과 별도로 복구 가능하게 유지합니다. tombstone을 확보하지 못하면 복원본을 사용자에게 공개하기 전에 검토합니다.
복원은 서비스 접근 차단 → DB·원문 복원 → 최신 tombstone 적용 → 만료 공급자 raw 제거 → 인덱스 재생성 → 격리 검증 → 서비스 재개 순서입니다.
마이그레이션 전 백업·dry-run을 수행하고 destructive 변경은 새 열/테이블 도입과 데이터 전환 단계를 나눕니다. 이전 앱이 새 schema를 읽을 수 있는지 rollback 조건을 명시합니다.
메모리 관리는 청크·리뷰·페이지의 batch 처리, 제한된 response body, 작은 Chroma handle pool, 작업 동시성 1을 기본으로 합니다. 전체 메일·리뷰 집합을 한 번에 메모리에 올리지 않습니다.
메모리 부족으로 활성 프로세스가 죽는 경우를 부하 시험에 포함합니다. 브라우저 수집은 별도 수동 연구 실행으로 분리하는 안을 먼저 비교하며 자동 유료 증설을 전제로 하지 않습니다.

## 12. 기술 경계 검토와 구현 완료 증거

아래 TECH 항목은 기술 경계 요약이며, 출시 판정의 정식 ID와 전체 목록은 [인수시험](ACCEPTANCE_TESTS.md)을 따릅니다. 테스트는 synthetic 메일·허용된 HTML fixtures·fake provider·임시 SQL/Chroma 경로를 사용합니다. 실제 계정·유료 호출 검증은 별도 결과로 기록합니다.
각 시나리오는 입력 fixture, 기대 API code·상태·부작용, 실제 실행 로그의 request/job ID를 남깁니다. 정상 응답 코드만 확인하는 테스트로 끝내지 않습니다.

| ID | 시나리오 | 통과 조건 |
| --- | --- | --- |
| TECH-AUTH-01 | 인증 설정 없이 구 API와 v2 호출 | 모든 개인 조회·업로드·삭제·상태·원문 차단 |
| TECH-AUTH-02 | A 사용자가 B의 trip/job/source ID 사용 | 404, 출처·존재 여부·이벤트 누출 없음 |
| TECH-AUTH-03 | 세션 회수 중 열려 있는 SSE·다운로드 | 재검사 후 종료, 이후 읽기 거절 |
| TECH-AUTH-04 | 교차 출처 변경 요청·system/tool history | CSRF 차단·422, 외부 호출 0회 |
| TECH-DOC-01 | 왕복·다구간 메일 하나와 수동 교정 | 모든 구간 추출, 재추출 후 교정 보존 |
| TECH-DOC-02 | 동일 파일명 두 여행·중복 요청 | 서로 다른 원문 경로, 동일 요청 재처리 없음 |
| TECH-IDX-01 | 임베딩 실패·ready 이전 강제 종료 | 기존 예약·검색 계속 사용 가능 |
| TECH-IDX-02 | SQL 활성 전환 직전·직후 강제 종료 | 혼합 generation 없음, 안전한 재개 |
| TECH-IDX-03 | 더 유사한 과거/staged 청크 20개 삽입 | 활성 청크 top-k 결과 불변 |
| TECH-IDX-04 | 날짜 하나에 예약 8개·입력 교정 중 검색 | 8개 전부 반환, 교정 전 시각을 사실로 출력하지 않음 |
| TECH-JOB-01 | lease 만료 후 이전 실행자 완료 | stale fencing token의 활성화 거절 |
| TECH-JOB-02 | 같은 key 같은/다른 payload | 같은 job 반환 / 409, 추가 과금 없음 |
| TECH-JOB-03 | 수집·인덱싱 중 여행 삭제 | 즉시 읽기 차단, 완료돼도 데이터 부활 없음 |
| TECH-POL-01 | 수집 허용·저장 불허 정책 | 허용된 처리만 실행, raw·로그·백업에 보관 안 됨 |
| TECH-POL-02 | 정책 만료·데이터 TTL 종료 | 원문 제거, 영향받은 집계·표시 비활성화 |
| TECH-CRAWL-01 | pagination 반복·HTML 변경·차단 | 범위 불완전·stop reason, 전체 수집으로 표시 안 함 |
| TECH-CRAWL-02 | private DNS·redirect·초과 크기 | 요청 차단·중단, 내부 네트워크 읽기 없음 |
| TECH-REV-01 | 한국어 0/5, 전체 평가 1,000 | 전체 한국어 비율 생성 안 함, 분모 혼합 없음 |
| TECH-REV-02 | 원문/번역/편집 중복·미판별 다수 | 중복 제외·unknown 표시, 엄격 필터 비활성 |
| TECH-REV-03 | 최신 200기록에 별점-only 50·텍스트 150 포함 | cap=200, 언어 분모는 관측 텍스트/판별 기준으로 별도 표시 |
| TECH-REV-04 | 공급자 관측 구간·품질·권한 gate 통과 | observed_window proxy 사용 가능, 모집단 비율·CI로 표현 안 함 |
| TECH-REV-05 | K 전에 pagination 실패·경계 게시일 불명 | 탐색용 부분 관측 표시, 언어 hard filter 미적용 |
| TECH-REC-01 | 4인 조건에 최대 2인·총좌석 20석 | 부적합·미확인 각각 구분, 분할 예약 자동 제안 없음 |
| TECH-REC-02 | 언어 통계 미지원·후보 2개만 적합 | 미지원 표시, 가짜로 5개 채우지 않음 |
| TECH-TIME-01 | 자정 영업·브레이크타임·입장 마감 | 전체 체류시간과 각각의 제약 일관 검증 |
| TECH-TIME-02 | DST gap/fold·양단 시간대·날짜 변경선 | 모호함 확인 요구, UTC duration 일관 |
| TECH-TIME-03 | 고정 예약 충돌·없는 경로·미래 영업 | 원본 유지, 검증 완료로 승격 안 함 |
| TECH-EDIT-01 | 두 탭이 같은 version 일정 수정 | 하나만 저장, 다른 요청 409 |
| TECH-PLAN-01 | Plan B 후보 확인·예약 오픈 규칙 미확인 | 기존 예약 불변, 오픈 시각 임의 생성 없음 |
| TECH-LINK-01 | 북마크가 비공개 IP로 redirect | 저장 메모 보존, resolve 차단, 원문 수집 없음 |
| TECH-OFF-01 | opt-in 없음·로그아웃·사용자 교체 | 개인 API 자동 cache 없음, 타 사용자 snapshot 안 보임 |
| TECH-COST-01 | 마지막 예산으로 동시 두 요청 | 원자 예약, 한도 넘는 외부 호출 차단 |
| TECH-COST-02 | timeout 뒤 재시도·프로세스 종료 | unknown 과금 유지, 재시도 예산 다시 검사 |
| TECH-OPS-01 | 외부 API 전체 장애·예산 소진 | 저장 여행·예약·일정 조회 정상 |
| TECH-OPS-02 | 오래된 백업·최신 tombstone 복원 | 삭제 자료 비공개 유지, 재색인으로 부활 안 함 |
| TECH-OPS-03 | 동시 5사용자 읽기와 작업 1개 | 실측 지연·메모리 기록, OOM·잠금 장기 점유 없음 |

완료 보고에는 구현된 항목·보류 항목·실제로 실행한 테스트를 분리합니다. 공급자 연결이 fake 상태라면 크롤링·실시간 예약·언어 통계를 라이브 검증했다고 보고하지 않습니다.
배포 전 최소 gate는 사용자 격리, 삭제 경쟁, 세대 전환 crash, 시간 충돌, 비용 경쟁, 백업 복원입니다. 기능 화면이 보이는 것만으로 운영 준비 완료를 판정하지 않습니다.
