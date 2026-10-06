# Travel Agent 02단계 작업 복구와 검색 일관성 및 비용 제한

문서 기준 디렉터리는 docs/service-v2다. 아래 문서명과 examples 경로는 이 디렉터리를 기준으로 읽어.
아래 내용 전체를 개발 에이전트에게 전달해. 이 문서만으로 이번 단계의 목표와 구현·검증 방법을 알 수 있게 작성한 실행 요청이다.

---

너는 Travel Agent의 제품 개발 책임자이자 구현 엔지니어다. 다음 저장소에서 예약 처리 작업이 재시작으로 사라지지 않고,
검색 갱신 실패가 기존 예약을 지우지 않으며 외부 호출이 설정된 예산을 넘어 무작정 실행되지 않도록 실제 코드를 구현해.
저장소는 `/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`, 원격은 `https://github.com/Jin-s-work/Travel-Agent`다.
기획 요약이나 큐 라이브러리 추천만으로 끝내지 말고 작업 실행기·API·상태 UI·복구 테스트까지 연결해.

## 1. 제품과 이번 단계의 범위

본인과 소수 지인이 사용하는 비공개 여행 서비스이며 FastAPI·동일 출처 PWA·SQLite·Chroma·단일 인스턴스를 유지한다.
이 단계는 01단계의 사용자·여행·예약·소유권 구조 위에 내구성 있는 실행과 읽기 일관성을 올리는 작업이다.
여행별 업로드·재추출·검색 갱신·삭제 정리를 영구 job으로 처리하고, 느린 작업 중에도 저장된 예약을 읽게 해.
후속 리뷰 수집·추천·일정 생성이 재사용할 작업/공급자/예산 계약을 만들되 실제 크롤러·추천 엔진까지 구현하지 마.
Redis, 다중 worker 서버, 별도 대형 메시지 시스템을 도입하지 마. 단일 프로세스 안의 동시성도 DB에서 제어해.
정확한 월 예산은 아직 확정되지 않았다. 설정 가능한 상한과 실행 전 추정·예약 구조를 만들고 임의의 유료 가입을 하지 마.

## 2. 선행 확인과 기존 코드의 교체 지도

AGENTS.md, git status, 실행 환경, docs/service-v2/IMPLEMENTATION_STATUS.md를 확인하고 TECHNICAL_SPEC.md의 2~6절 및 P02를 읽어.
ACCEPTANCE_TESTS.md의 AUTH·DATA-03·OPS·UI 관련 조건을 실제 테스트에 연결해.
01단계가 완료라는 기록이 있어도 세션 권한, 여행별 DB, 원문 ID, 교정 보존 계약을 코드에서 확인해.
선행 기능이 빠졌으면 이번 작업에 필요한 최소 수정부터 하고 범위를 기록해. 전체 UI를 다시 설계하지 마.
현재 파일 이름은 실행 시 달라질 수 있으므로 아래 책임을 확인하고 이미 분리된 모듈을 재사용해.

| 기존 위치·위험 | 이번 단계의 대체 | 제안 모듈 |
| --- | --- | --- |
| `api.py`의 전역 `_job`·`_job_lock` | SQLite jobs/job_events를 기준으로 한 상태 조회 | `src/jobs/repository.py` |
| `BackgroundTasks`, `_run_indexing`, daemon seed thread | lifespan dispatcher와 실행 handler | `src/jobs/dispatcher.py`, `handlers.py` |
| `_index_lock`의 프로세스 메모리 의존 | DB claim·trip write lock·lease·fencing token | `src/jobs/leases.py` |
| `src/indexer.py`의 기존 청크 삭제 후 임베딩 | 준비한 immutable collection을 검증 후 활성화 | `src/search/generations.py` |
| `src/store.py`의 collection 자동 재생성 | 활성 세대 손상을 감지하고 복구 job을 생성 | scoped vector adapter |
| LLM·임베딩·Tavily 직접 호출 | 호출 ID·예산 예약·timeout·관측이 있는 provider 계약 | `src/providers/`, `src/observability/` |
| `/api/index/status` polling | `/api/v2/jobs/{id}`와 재연결 가능한 저장 SSE | `src/http/jobs.py`, web API client |

위 경로는 제안 이름이다. 기존 구조를 조사한 뒤 의미가 같은 모듈이 있으면 그곳을 확장해.
공용 Chroma client 연결 재사용과 공용 개인 데이터 collection 공유를 구분해. client를 재사용해도 검색 scope는 여행별이어야 해.
실제 .env·메일·예약번호·토큰을 출력하지 말고 임시 DB·임시 Chroma·fake provider로 먼저 구현과 검증을 완료해.

## 3. 개발 순서

1. jobs·job_events·사용량·세대 테이블의 migration과 repository를 작성해.
2. 상태 전이·claim·lease·heartbeat·취소·idempotency를 순수 규칙과 DB 통합 테스트로 검증해.
3. dispatcher와 fake handler를 연결한 뒤 문서 처리 handler를 교체해.
4. 검색 세대 build/verify/activate/retire와 SQL 활성 pointer 전환을 구현해.
5. 삭제 tombstone·교정·동시 갱신과의 경쟁을 처리해.
6. 외부 공급자 호출 wrapper·예산 예약/정산·unknown 과금을 구현해.
7. 상태 API·SSE·브라우저 복구 UI를 연결하고 프로세스 종료 실험을 실행해.

각 순서의 실제 동작이 확인되기 전에 UI에 완료로 표시하지 마. 큐에 등록됐다는 사실과 작업 성공을 구분해.
외부 계정이 없어도 복구·예산·동시성 테스트는 fake provider로 완료할 수 있다. 환경 부재를 전체 작업 중단 이유로 삼지 마.

## 4. DB 모델과 상태 전이

01단계 SQLite 설정을 재사용하고 네트워크 호출 중 DB 쓰기 트랜잭션을 잡지 마.
서버가 만든 job ID, scope, operation, payload reference를 사용하고 원문·비밀 토큰을 job JSON에 복제하지 마.
다음 필드는 제안 schema다. 실제 명칭은 일관되게 정하고 migration/DTO/상태 기록에서 같은 의미를 유지해.

| 테이블 | 주요 필드 | 인덱스·불변 조건 |
| --- | --- | --- |
| jobs | id, actor_id, scope_kind, scope_id, owner_id/trip_id nullable, operation, state, payload_ref/hash, input_version, deletion_epoch | `(state, available_at, priority)`; 개인 작업의 owner/trip 필수, 관리자 연구 scope 별도 |
| jobs 실행 | attempt, max_attempts, available_at, deadline_at, lease_expires_at, heartbeat_at, fencing_token, cancel_requested_at | claim마다 fencing 증가, 마지막 token만 상태 변경 |
| jobs 결과 | result_ref, error_code, retryable, stage, done_count, total_count, started_at, finished_at | terminal state 불변, 민감 오류 본문 저장 금지 |
| idempotency_keys | actor_id, scope_kind, scope_id, operation, key_hash, request_hash, job_id, expires_at | 비NULL scope+key unique, 다른 payload 재사용 금지 |
| job_events | job_id, sequence, event_type, sanitized_payload, created_at | `(job_id, sequence)` unique, 단조 증가 |
| document_generations | document_id, generation_no, parse_version, state, extracted_ref, job_id | 준비값과 활성값 분리 |
| trip_index_generations | id, trip_id, collection_name, source_manifest, state, base_trip_version, job_id | collection unique, ready만 활성화 가능 |
| trip index lock | trip_id, holder_job_id, fencing_token, lease_expires_at | 여행 하나에 유효 writer 하나 |
| usage_reservations | call_id, owner/trip/job, provider, SKU, period, currency, estimated_units/cost, state | call_id unique, 원자적 한도 예약 |
| usage ledger | call_id, attempt, actual_units/cost, estimate basis, reconciliation state, observed_at | 정산 중복 금지, 원기록 보존 |

기본 전이는 `queued → running → succeeded/partial/failed/cancelled`다.
일시적 실패의 재시도는 `running → queued`로 돌아가고 attempt를 증가시키되 전체 deadline·최대 attempt를 넘지 마.
`partial`은 일부 결과가 실제 사용 가능한 terminal 상태다. 하나라도 실패했다는 이유만으로 모든 작업을 partial로 뭉뚱그리지 마.
`cancel_requested_at`은 요청 표시이며 즉시 cancelled와 같지 않다. 부작용을 멈췄거나 결과를 폐기한 뒤 terminal 상태로 전환해.
queued 상태 취소는 외부 호출 없이 cancelled로 만들고, 이미 terminal인 job의 취소 요청은 현재 snapshot을 반환하는 멱등 동작으로 해.
사용자의 재시도는 원 job을 다시 running으로 바꾸지 말고 필요한 실패 단위만 대상으로 새 job과 새 호출 예산을 생성해.
오류는 설정/검증/권한/정책/일시적 공급자/timeout/예산/내부 오류를 구분해. 자동 재시도 가능 여부는 코드가 결정해.

## 5. dispatcher·lease·fencing을 실제로 동작시키기

FastAPI lifespan에서 dispatcher 하나를 시작하고 shutdown에서는 신규 claim을 멈춘 뒤 정해진 유예 시간 안에 정리해.
기본 외부 호출 작업 동시성은 1이다. 동기 LLM·Chroma 작업은 제한된 thread에서 실행해 HTTP event loop를 막지 마.
기동 시 두 dispatcher가 켜지는 개발 reload·잘못된 multi-worker 구성을 감지하거나 실행 제한을 문서화해.
job claim은 SELECT 후 무조건 UPDATE가 아니라 조건부 UPDATE를 포함한 짧은 트랜잭션으로 구현해.
claim 시 새 fencing token을 발급하고 worker의 heartbeat·중간 저장·결과 활성화에 해당 token을 요구해.
기본 lease 90초·heartbeat 20초를 설정값으로 시작하되 timeout과 최대 작업 시간을 함께 검증해.
heartbeat가 실패하면 worker는 소유권을 잃은 것으로 취급하고 새 외부 호출이나 제품 결과 활성화를 중단해.
lease가 만료되면 복구 루프가 checkpoint를 읽고 재개/재큐/실패 중 하나를 선택해. 모든 작업을 무조건 처음부터 실행하지 마.
문서 해시·추출 완료·임베딩 배치·ready generation·SQL 활성 pointer 같은 checkpoint를 사용해 중복 유료 호출을 줄여.
임베딩 모델·차원·정규화·parser 버전이 바뀌면 기존 checkpoint 재사용 조건을 검증해.
동일 여행의 두 index writer는 직렬화하되 다른 여행의 읽기 요청까지 전역 lock으로 막지 마.
재시도는 429·일시적 5xx·명확한 연결 실패에 제한된 backoff/jitter를 적용하고 Retry-After가 있으면 상한 내에서 존중해.
400·인증 실패·정책 불허는 자동 반복하지 마. 총 시도 횟수뿐 아니라 전체 deadline과 누적 비용을 함께 검사해.
HTTP timeout 후 공급자가 작업을 완료했을 수 있으면 unknown으로 기록하고 provider 조회/정산 가능성을 먼저 확인해.
외부 exactly-once를 보장한다고 표현하지 마. 공급자의 idempotency key 지원 여부를 adapter capability로 명시해.

## 6. Idempotency-Key와 사용자 중복 행동

동일 요청의 범위는 인증 사용자·여행·operation이며 정규화한 payload hash를 함께 저장해.
이 규칙은 개인 여행 작업의 기본이다. 03단계 공용 장소 조사를 위해
scope_kind=personal_trip/admin_research, actor_id, 비어 있지 않은 scope_id를 추가해.
personal_trip은 trip_id와 여행 소유권을 필수로 검사하고,
admin_research만 trip_id=null을 허용하며 관리자 role·정책·대상 장소 범위를 확인해.
관리자 작업을 임의 개인 여행에 넣거나 일반 job API의 권한 검사를 생략하지 마.
unique key는 actor/scope_kind/scope_id/operation/idempotency_key로 구성해
SQLite의 nullable trip_id 때문에 중복 요청이 여러 행으로 들어가지 않게 해.
scope_id는 재시도에 안정적인 연구 범위 ID이며 새 run마다 임의로 바꾸지 마.
일반 사용자에게 관리자 job/SSE를 노출하지 않고 실행자 권한 회수·정책 만료도 검사해.
정규화 규칙은 key 순서·의미 없는 공백·파일 content hash를 고려하되 사용자 의미가 다른 요청을 하나로 합치지 마.
같은 key+같은 payload는 기존 job_id와 현재 state를 반환하고 새로운 외부 호출·예약·job 행을 만들지 마.
같은 key+다른 payload는 409 IDEMPOTENCY_CONFLICT로 반환해. key가 같다는 이유로 새로운 입력을 버리지 마.
보관 기간은 client 재시도보다 길게 잡고 API 계약에 명시해. 만료된 키의 재사용이 새 작업임을 로그에 구분해.
브라우저는 사용자의 한 번 실행 의도에 대해 key를 만들고 네트워크 응답 유실 시 같은 key로 조회/재전송해.
실패 후 사용자가 입력을 바꾸거나 명시적으로 재실행하면 새로운 key를 사용하되 원 job과 연결 기록을 남겨.
중복 클릭 방지는 버튼 disable만으로 끝내지 마. DB unique 제약으로 동시 요청도 방어해.

## 7. SQLite와 Chroma의 안전한 세대 교체

SQLite와 Chroma에는 공통 트랜잭션이 없다. 새 여행별 immutable collection을 준비한 뒤 SQL pointer를 원자적으로 전환해.
단일 문서 갱신도 새 trip index generation으로 만들며 unchanged 청크의 벡터를 복사해 불필요한 재임베딩을 줄여.
source manifest에는 document ID·generation ID·content hash·chunk count·embedding model/dimension을 기록해.
새 collection 이름은 서버 생성 ID로 정하고 trip 이름·파일명·예약번호를 넣지 마.
구현 순서는 아래와 같이 고정해. 단계마다 프로세스 종료 후의 재개 규칙을 만들어.

1. SQL에서 문서 generation을 staged로 저장하고 기존 활성 예약·index pointer는 그대로 둬.
2. 문서를 추출·검증하고 현재 사용자 교정 레이어를 반영한 사실과 검색 청크를 준비해.
3. 여행 writer lease를 확보하고 base trip version·삭제 epoch·현재 manifest를 고정해.
4. 바뀌지 않은 유효 벡터는 복사하고 새 청크만 임베딩해 별도 collection에 upsert해.
5. 청크 수·문서/세대 scope·벡터 차원·중복 ID·누락을 검증한 뒤 index generation을 ready로 기록해.
6. 짧은 SQL 트랜잭션에서 세션/사용자 권한·tombstone·trip version·job/writer fencing을 다시 검증해.
7. 예약 활성 generation과 trips.active_index_id를 같은 SQL 트랜잭션에서 전환하고 여행 버전을 증가시켜.
8. 성공 결과/event를 기록한 뒤 이전 collection은 읽기 참조가 없을 때 retire/삭제해.

검증 실패나 권한/버전 충돌이 있으면 새 세대를 active로 만들지 마. 기존 활성 데이터는 계속 읽을 수 있어야 해.
사용자가 교정한 시점과 build 시점이 겹치면 최신 overlay를 다시 병합하거나 명시적 재계산으로 처리해.
검색 요청은 활성 pointer를 한 번 읽고 해당 generation에 대한 읽기 참조를 획득해 요청 종료까지 유지해.
pointer 읽기와 참조 등록 사이의 경쟁도 처리해. SQL reader lease 또는 같은 프로세스 critical section 등 선택한 방법을 테스트해.
이전 세대 회수는 단순히 N초가 지났다는 조건만 사용하지 말고 active 여부·in-flight 참조·writer checkpoint를 확인해.
활성 collection이 없거나 손상되면 자동 빈 collection을 만들어 정상 검색처럼 처리하지 마.
SQL 예약 조회는 유지하고 검색은 복구 중으로 응답하며 멱등 복구 job을 만들어.
날짜 전체 질문·예약번호 정확 조회는 SQL 현재 사실로 계속 처리하고 stale 검색 문서의 사실은 SQL 교정값으로 재조립해.

## 8. 삭제·취소·늦은 응답의 경쟁 처리

여행 삭제는 tombstone 기록과 접근 차단을 먼저 수행하고 이후 queued/running job 취소와 물리 정리를 진행해.
삭제 API의 202는 ‘접근이 차단되고 정리가 접수됨’이며 모든 파일이 지워졌다는 의미가 아니다.
문서/예약 삭제도 해당 scope의 tombstone 또는 deletion revision으로 추적해 동일 문서의 다른 예약을 지우지 마.
worker는 외부 호출 직전과 제품 상태 활성화 직전에 tombstone·권한·입력 version을 검사해.
삭제 전에 시작된 외부 요청이 뒤늦게 성공해도 예약·원문·청크가 살아나면 안 돼.
제품 결과를 폐기하더라도 이미 발생한 공급자 비용은 정산해. 취소가 과금 취소를 의미하지 않는다.
SQL 삭제 표시 후 Chroma 삭제가 실패하면 재시도 가능한 정리 job으로 남기고 응답·검색에서 즉시 제외해.
여행 자체를 삭제한 뒤에는 기존 일반 job URL을 404로 막아. 삭제 진행 영수증은 삭제 작업자에게 최소 상태만 제공하는 별도 계약을 정의해.
삭제 영수증은 trip 원문·예약 제목·기존 job payload를 노출하지 않고 세션 회수 시 접근도 닫아.
세션 회수된 사용자의 pending job이 새 결과를 활성화하지 않게 검사하고 복구/정리 시스템 작업 권한은 별도로 명시해.

## 9. 비용 예약·정산·운영 중단

모든 외부 LLM·임베딩·검색·수집 호출은 provider wrapper를 지나게 하고 직접 호출이 남았는지 rg로 검사해.
요청 전 call_id를 만들고 provider/SKU/단위/예상량/통화/단가 버전/예상 비용을 계산해.
사용자·일/월·전역 scope 중 적용되는 모든 상한을 한 트랜잭션에서 확인하고 예상 비용을 원자적으로 예약해.
이번 설정의 월 경계를 UTC 또는 선택한 운영 시간대로 명시해. 서버 로컬 timezone에 따라 바뀌게 하지 마.
다른 통화는 승인된 환산 정책 없이는 더하지 마. 통화별 한도로 운영하거나 기준 환율·확인일을 저장해.
추정 최대치가 없는 호출은 보수적인 상한 또는 제한된 batch/token/output cap이 설정되기 전 자동 실행하지 마.
실제 사용량을 응답에서 얻으면 기존 reservation을 정확히 한 번 정산하고 차액을 조정해.
공급자가 사용량을 늦게 주면 pending_reconciliation, 전송/과금 여부가 불명확하면 unknown으로 남겨.
unknown reservation은 오류가 났다는 이유만으로 즉시 해제하지 마. 조회·정산·운영자 검토 중 하나로 해소해.
명확히 전송하지 않은 요청만 안전하게 해제해. SDK 내부 retry도 실제 호출·비용 추적 범위에 포함해.
retry는 새 attempt/call record로 기록하고 새 예상 비용 검사를 거쳐. 동일 call 응답 재처리로 중복 정산하지 마.
실제 비용이 예상보다 크면 차이를 기록하고 후속 호출을 중단해. 앱 한도가 공급자 청구의 절대 상한이라고 보장하지 마.
공유 계정의 다른 앱 사용량과 공급자 지연 과금은 앱 ledger 밖일 수 있음을 운영 UI에 표시해.
개인 예산 소진은 429 BUDGET_EXHAUSTED로, 전역 운영 중지는 별도 code로 구분해.
예산이 소진돼도 저장된 여행·예약·일정 읽기는 유지하고 사용자가 새 요청을 반복하도록 무조건 재시도 버튼을 제공하지 마.
실제 단가·무료 크레딧은 설정 근거와 확인일을 기록해. 이 단계에서 과거 문서 가격을 무조건 하드코딩하지 마.

## 10. 상태 API와 SSE 계약

모든 경로는 `/api/v2` prefix와 인증/권한 dependency를 적용해.
`GET /jobs/{jid}`는 scope 확인 후 job snapshot을 반환하고 다른 사용자의 ID는 404로 통일해.
`GET /jobs/{jid}/events`는 저장된 event를 SSE로 전달하며 job와 trip 권한을 모두 검사해.
이는 personal_trip 작업의 계약이다. admin_research 작업은 관리자 role·실행자·연구 범위로 검사하고 일반 사용자에게 노출하지 마.
`POST /jobs/{jid}/cancel`은 CSRF·Origin을 검사하고 취소 요청 상태와 현재 snapshot을 반환해.
`POST /jobs/{jid}/retry`는 제안 추가 경로다. 재시도 가능한 실패 단위와 새 job_id를 반환하고 예산 검사를 다시 수행해.
업로드는 202로 job_id·파일별 결과·status_url·events_url을 반환해. 요청 재전송은 같은 job을 반환해야 해.

```json
{"job_id":"job_example","state":"running","stage":"embedding","progress":{"done":2,"total":5,"unit":"document"},"attempt":1,"can_cancel":true,"can_retry":false,"result_ref":null,"warnings":[]}
```

SSE event는 progress/partial_result/needs_review/completed/failed/cancelled를 사용하고 job별 sequence를 event ID로 사용해.
`Last-Event-ID` 다음의 저장 event를 재생해. 너무 오래된 cursor는 snapshot 재조회 필요 응답을 정의하고 client가 연결을 다시 잡게 해.
heartbeat comment는 연결 유지용일 뿐 진행률·job heartbeat·비용 발생으로 세지 마.
각 SSE payload에는 ID·단계·개수·정제된 reason만 보내고 메일·예약번호·전체 공급자 응답은 넣지 마.
최종 결과는 인증된 GET으로 조회할 수 있어야 해. 마지막 completed event를 놓쳤어도 성공 결과를 복원할 수 있어야 해.
세션 만료·권한 회수는 스트림을 종료하고 다시 인증하게 해. 네트워크 연결 종료 자체는 job 취소로 처리하지 마.

```text
id: 12
event: progress
data: {"job_id":"job_example","stage":"verify_index","done":8,"total":8}

id: 13
event: completed
data: {"job_id":"job_example","state":"succeeded","result_id":"gen_example"}
```

## 11. 화면에서의 작업 복구와 오류 처리

여행 화면에 해당 여행의 진행 중 작업과 최근 실패 작업을 보여주고, 새로고침 시 서버에서 active jobs를 조회해 복원해.
필요한 active-job 목록 경로는 owner/trip cursor filter로 추가하고 전체 사용자 job 목록을 일반 사용자에게 제공하지 마.
client에 job_id를 보관하더라도 권한의 근거로 쓰지 말고 원문·예약번호·payload를 localStorage에 넣지 마.
대기/처리/확인 필요/부분 성공/실패/취소 요청/취소 완료를 별개 문구로 보여줘.
전체 개수를 알 수 없으면 ‘원문 분석 중’처럼 단계를 표시하고 90%에서 멈추는 가짜 진행률을 만들지 마.
파일별 성공과 실패를 보여주고 재시도는 실패 파일에만 적용해. 이미 성공한 파일을 다시 과금 처리하지 마.
SSE가 끊기면 제한된 재연결 후 snapshot polling으로 복구하고, 새 job 생성을 fallback으로 사용하지 마.
다른 여행으로 이동해도 작업은 계속되며 돌아오면 상태를 복원해. 사용자가 취소해야 실제 취소를 요청해.
예산 소진은 결과를 삭제하지 않고 어느 기능이 일시 중단됐는지 알려줘. 운영자용 사용량에는 예상/실제/미정산을 구분해.
임베딩 장애 중 SQL 예약 목록·날짜 질문이 계속 작동하는 사용자 흐름을 브라우저에서 확인해.

## 12. 중단 지점별 복구 실험과 필수 검증

테스트 clock·fake provider·임시 SQLite/Chroma/원문 경로로 격리한 실행 환경을 구성해.
테스트 전용 fault injection hook을 넣고 운영 기본값에서 켜지지 않게 해. 실제 사용자 데이터로 프로세스 종료 실험을 하지 마.

| 장애/경합 지점 | 검증해야 할 결과 |
| --- | --- |
| 원문 임시 저장 직후 종료 | 고아 파일 정리 또는 유효 참조 복구, 기존 활성 자료 유지 |
| 추출 성공·임베딩 일부 저장 후 종료 | checkpoint에 따라 재개, 동일 generation upsert 또는 안전한 폐기 |
| ready 직후 SQL 전환 전 종료 | 이전 active 유지, 권한/version/fencing 확인 후만 활성화 |
| SQL pointer 전환 후 완료 event 전 종료 | active manifest로 성공 복원, 유료 추출을 다시 하지 않음 |
| writer A lease 만료 후 B가 claim | A의 늦은 완료/heartbeat/활성화가 거절됨 |
| 같은 idempotency key 동시 2회 | job 하나, 외부 호출 한 번; 다른 payload면 409 |
| 남은 예산에 동시 호출 2회 | 원자적 예약으로 허용량만 실행, 실패 요청 비용 0 또는 미전송 해제 |
| 응답 유실·과금 unknown | reservation 유지, 자동 무료 재시도로 간주하지 않음 |
| 삭제/취소와 worker 완료 동시 | 삭제 자료 부활 없음, 늦은 실제 비용만 정산 |
| 오래 읽는 요청과 이전 세대 회수 | 읽기 참조 종료 전 collection 삭제 없음 |
| 활성 collection 손상 | SQL 조회 유지, 검색 복구 상태, 중복 복구 job 없음 |
| SSE 끊김·오래된 cursor·권한 회수 | snapshot 복구 또는 안전한 종료, 새 job·비밀 유출 없음 |

OPS-01·OPS-03~07, AUTH의 job/SSE 범위, DATA-03, UI-03·UI-05를 관련 코드와 실행 결과에 연결해.
읽기 일관성은 단순 state 값만 검사하지 말고 SQL·벡터·원문·event·외부 호출 횟수의 부작용을 함께 검사해.
운영 복구/백업 전체 실험은 06단계에서 다시 수행하지만 이번 단계의 프로세스 종료 복구를 생략하지 마.
브라우저에서 업로드 시작 → 새로고침 → 여행 이동/복귀 → 일부 실패 → 실패만 재시도 → 완료 확인을 수행해.

## 13. 배포·이관·인계와 완료 조건

스키마 변경은 versioned migration으로 추가하고 이전 버전 DB를 임시 사본에서 업그레이드해 검증해.
기존 메모리 job은 재시작 뒤 복구할 근거가 없을 수 있다. 완료라고 추정하지 말고 파일/manifest를 대조해 복구 필요로 기록해.
새 dispatcher로 전환할 때 구 BackgroundTasks·seed loop가 중복 실행되지 않게 한 경로로 통합해.
기동 시 DB schema version, 영구 경로 쓰기, dispatcher heartbeat, 활성 index 상태를 readiness에서 확인해.
세대 정리는 현재 active·in-flight·복구 checkpoint를 보호하며 반복 실행 가능해야 해.
롤백은 dispatcher 중지 → 일관된 DB/원문/index snapshot → 호환 버전 복귀 절차로 정리해. 전환 후 새 데이터를 조용히 버리지 마.
외부 exactly-once·공급자 청구 hard cap·multi-instance 지원은 이번 단계에서 보장하지 않는 제약으로 명시해.

산출물은 영구 작업 실행기, 상태 API/SSE, 세대 교체, 비용 wrapper, 연결된 UI, migration과 복구 통합 테스트다.
docs/service-v2/IMPLEMENTATION_STATUS.md에 날짜·revision·주요 파일·실행 명령·결과·인수 ID·미실행 이유를 기록해.
구현 상태 not_started/partial/complete/blocked와 실환경 검증 not_run/partial/verified/not_required를 분리해.
실제 공급자 호출을 하지 않았다면 fake 계약 검증 완료라고 기록하고 라이브 비용·복구까지 검증했다고 쓰지 마.
최종 답변에는 사용 가능한 복구 행동, 주요 설계 결정, 실제 장애 실험 결과, 남은 제약, 03단계 진입 가능 여부를 적어.
다음 단계는 `docs/service-v2/prompts/03-review-data.md`이며 이번 요청으로 후속 크롤러까지 자동 구현하지 마.
