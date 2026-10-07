# Going 백엔드 심층 감사 — 2026-10-07

대상: `/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`. 서비스 고도화 PRD와 후속 구현 지시의 근거를 위한 읽기 전용 감사다. 루트/상위 디렉터리 및 저장소의 적용 가능한 AGENTS.md는 발견되지 않았다. `.env`, 운영 DB, 실제 메일, 개인 데이터는 읽지 않았다. 앱 코드·배포·외부 유료 API는 변경하거나 호출하지 않았다. 기존 `docs/presentation/going-class-presentation.key` 수정 상태는 전후 동일하다.

검증 자료: [실행 가능한 합성 재현 스크립트](backend-probe.py), [측정 결과](backend-probe-results.json). 실제 저장소 fixture와 앱 코드를 사용하고 임시 SQLite/로컬 파일만 생성했다. 전체 pytest는 메인 감사가 별도로 담당한다. 아래 실행 횟수는 합성 환경의 측정이며 운영 추천 45–53초를 직접 재측정한 값이 아니다.

## 1. P1 — 추천 생성과 저장 결과 열기의 순차 N+1 SQL

**확인 수준: 재현.** 외부 API가 전혀 없어도 소수 후보에 수백 개 SQL이 실행된다. DB 연결 풀을 추가하는 과제가 아니다. PostgreSQL 연결 풀은 이미 구현되어 있으며 문제는 반복 checkout과 순차 SQL 왕복이다.

근거 경로:

- `src/recommendations/service.py:112`: `_catalog`가 catalog 전체 조회 → trip_places 쓰기 → `reviews.purge()` → 전역 guard → 후보별 `reviews.evidence()` → 전역 guard를 수행한다.
- `src/research/service.py:366`: `evidence()`는 후보마다 `repo.get_trip()`·`purge()`·근거 조회를 다시 실행한다. `purge()`는 `:421`에서 매번 쓰기 트랜잭션을 시작하고 전역 정책/장소/리뷰 정리를 수행한다. 조회가 전역 쓰기 경합을 유발한다.
- `src/discovery/service.py:369` 및 `:383`: 승인 후보 하나당 출처·사실·후보팩 3개의 추가 조회. `catalog`는 `:430`에서 전체 후보를 가져온 뒤 이 작업을 반복한다.
- `src/recommendations/service.py:165`, `:201`: 작업에서 `_catalog`를 두 번 실행한다. `:206`의 최종 `_guard_catalog`도 승인 후보별 3쿼리를 반복한다.
- `src/recommendations/service.py:266`: 저장 완료된 결과 GET도 `_catalog` 전체를 다시 실행한다. `:293`의 사진 조회도 장소별 조회다.
- `src/storage/postgres.py:155`, `:234`: max_size=5 풀을 이미 사용한다. `:55–75`의 전역 advisory write lock이 반복 purge의 영향 범위를 키운다.

합성 fixture 측정(리뷰 관측 없음, 유료 provider 0회):

| 범위 | 후보 | DB 컨텍스트/checkout | SQL 문 수 | SELECT | UPDATE |
|---|---:|---:|---:|---:|---:|
| `_catalog` 한 번 | 6 | 24 | 120 | 70 | 28 |
| `_catalog` 한 번 | 12 | 42 | 216 | 124 | 52 |
| 추천 `execute` 전체 | 12 | 110 | 667 | 408 | 119 |
| 완료 결과 `get` | 12 | 45 | 244 | 152 | 52 |

SQL 수에는 SQLite BEGIN/COMMIT과 executemany 개별 행이 포함된다. 실행→첫 결과 조회 합계는 checkout 155회·SQL 911개이며 제출·HTTP middleware·SSE/polling 쿼리는 제외했다. 측정한 `_catalog` checkout은 `3N+6`. 실제 리뷰 관측이 있으면 후보당 정책·run 조회가 더해질 수 있다. PostgreSQL wrapper의 BEGIN/advisory lock 왕복 구조는 SQLite와 같지 않으므로 운영 SQL 수로 그대로 단정하지 않는다.

**45–53초와의 관계:** 순차 DB 왕복이 누적될 수 있는 강한 코드 근거다. 예를 들어 911개 동기 왕복의 평균 추가 지연이 50ms이면 약45.6초지만 이는 설명용 산술이며 실측 RTT나 운영 원인 확정이 아니다. 운영 stage별 시간·SQL 수·pool wait·query elapsed를 수집해 cold start/외부지도 호출/대기열 시간과 분리해야 한다.

**재현:** `PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 <repo>/.venv/bin/python backend-probe.py`. 6개/12개 합성 승인 후보를 만들어 `_catalog`·`execute`·`get`만 thread-local DB counter로 계측한다.

**수정 범위:** discovery의 후보/팩/출처/사실 일괄 조회, review evidence 일괄 조회, 읽기 경로의 전역 purge 제거/유지보수로 분리, 한 요청에서 권한·여행·policy 확인 재사용, 변경 version/해시를 통한 최종 재검증. 현재 삭제·권한 철회·만료 방어를 제거해서 빠르게 만드는 것은 수용 불가다. 단순 풀 크기/worker 수 증가는 근본 해결이 아니며 전역 writer 설계와 충돌한다.

**수용기준:** 후보 10/50/100개에서 네트워크 SELECT 왕복 수가 후보 수에 선형 증가하지 않아야 한다(일괄 처리의 bounded batch는 허용). 리뷰 없는 기본 catalog 읽기는 쓰기 0회로 만들고 trip_places 연결은 작업 생성/캡처 단계로 옮긴다. 같은 snapshot 재조회에 외부 호출 0회, 철회·만료·삭제·동시수정 기존 회귀 테스트 통과. DB 계측값은 개인정보 없이 stage·query group·횟수·시간만 기록. warm 운영 동등환경 목표는 첫 결과 p95≤10초, 저장 결과 p95≤2초로 제안하되 승인된 실제 측정 조건에서 검증한다.

## 2. P1 — 무관한 리뷰 정책 변경이 저장한 추천 결과를 없앰

**확인 수준: 재현.** 사용자가 보고 저장한 도쿄 추천이 해당 장소와 무관한 공급자 정책 한 행 추가만으로 `current/result 있음`에서 `stale/result null`로 변했다.

근거: `src/recommendations/service.py:29–35`의 `_review_guard`가 모든 provider_policies, 모든 review_aggregates, 모든 place pointer를 해시한다. `:131`은 그 토큰을 모든 후보에 붙인다. `:266–270`은 재조회 manifest가 달라지면 `result_json=NULL,candidates_json=NULL`로 영구 덮어쓴다. 전체 서비스의 리뷰 운영 활동이 각 여행의 결과 유효성에 불필요하게 결합된다. PostgreSQL adapter의 `Row`는 dict이므로 `:34`의 `tuple(r)`가 값 대신 키를 해시한다는 backend 차이도 함께 점검해야 한다(`src/storage/postgres.py:20`).

**재현:** 합성 도쿄 추천을 완료하고 결과가 current임을 확인 → 어떤 후보에도 참조되지 않은 `provider_policies` 행 하나 추가 → 같은 `Recommendations.get` 호출. `SOURCE_DATA_CHANGED`, `result=None` 확인. 실제 리뷰 수집·외부 API 없이 재현된다.

**수정 범위:** snapshot에서 실제 참조한 place/aggregate/policy/version만 dependency manifest로 기록한다. 전역 표시 차단 control은 유지한다. 무관한 데이터의 추가·변경은 영향시키지 않으며 관련 근거 철회/만료만 재검증한다. 관련 근거 무효화 시에도 비민감한 실행 이력과 사유는 남겨 재추천 행동으로 연결한다.

**수용기준:** 다른 도시/무관한 정책/무관한 aggregate 추가와 갱신 뒤 기존 결과 동일성 유지. 해당 후보의 출처 철회/만료 및 전역 production off에서는 즉시 차단. 최종 활성화 직전 active aggregate 교체도 SQLite/PostgreSQL 양쪽에서 감지. GET으로 무관한 변경을 접한 경우 snapshot/result를 파괴하지 않는다.

## 3. P1 — 배포 중 follower로 시작한 프로세스가 leader가 돼도 유지보수를 시작하지 않음

**확인 수준: 로컬 lifecycle 재현.** 첫 leader 획득만 실패시키고 이후 획득을 허용했다. dispatcher는 살아 있고 lease 획득도 반복 성공했지만 `review_maintenance` task는 0개였다.

근거: `api.py:75`에서 `leader`를 한 번 계산하고, `:108`과 `:110`에서 그 초기 boolean으로 유지보수·백업 task 생성 여부를 영구 결정한다. 반면 `src/reliability/jobs.py:377–379`는 매 claim 때 leader를 다시 획득한다. 이전 배포가 lease를 가진 동안 새 배포가 준비되고, 이전 프로세스가 종료된 뒤 새 프로세스가 leader가 되는 정상적인 겹침 배포가 이 조건에 해당한다. `src/reliability/jobs.py:509`와 `src/operations/health.py:33`은 dispatcher lease만 보고 이 누락을 감지하지 못한다.

영향: 리뷰 정리·remote cleanup·cloud orphan sweep이 자동 진행되지 않을 수 있다. 백업은 BACKUP_ENABLED=1일 때 같은 위험을 갖지만 현재 render.yaml은0이므로 '운영 자동 백업이 중단됐다'고 단정하지 않는다. 조회 시 purge가 일부 보완하지만 remote cleanup/object sweep을 대체하지 않는다. 초기 leader였던 프로세스가 이후 lease를 잃어도 유지보수 loop 자체에는 재검증이 없다.

**재현:** 합성 로컬 앱의 `acquire_dispatcher` 첫 호출만 False → lifespan 진입 → 다음 claim부터 실제 획득 허용 → `dispatcher_alive=true`이면서 유지보수 coroutine 없음 확인. 외부 서버/실제 배포를 사용하지 않았다.

**수정 범위:** 유지보수도 갱신 가능한 DB lease에 연결하거나 모든 프로세스에서 supervisor를 시작해 매 cycle 현재 소유권을 확인한다. leader 획득/상실에 따라 작업을 시작/중단하고, maintenance 마지막 성공·실패·다음 예정·leader id를 운영 관측으로 노출한다.

**수용기준:** 두 임시 프로세스/앱의 shared DB에서 A leader→B follower→A 종료→B leader 전환 후 한 cycle 안에 B의 유지보수 실행. A가 lease를 잃으면 새 외부 정리 호출을 시작하지 않는다. 중복 cycle 방지. 유지보수 실패/미실행을 운영 상태에서 구별하고 기본 예약 조회의 readiness와는 구분한다.

## 4. P2 — cloud orphan 정리가 앞의 100개를 반복하고 다음 배치로 전진하지 않을 수 있음

**확인 수준: 코드로 확인 + 동일 조건의 SQL 호환 재현.** 실제 PostgreSQL 실행계획은 재현하지 않았다. 운영 DB에서 발생 중이라고 단정하지 않는다.

근거: `src/storage/objects.py:116–125`는 `state IN ('pending','orphan','deleted')`까지 포함한 조건에 ORDER BY/커서 없이 LIMIT100을 적용한다. `remove`(`:107–111`)는 삭제 후 manifest를 없애지 않고 state='deleted'로 만든다. 따라서 다음 sweep에서도 같은 완료행이 대상이다. `cloud_import_objects`도 삭제 이후 manifest를 유지하면서 같은 LIMIT100 선택을 반복한다. eventual consistency 재확인 목적은 타당하지만 마지막 sweep/다음 확인 시각/완료 상태/회전 커서가 없다.

**재현:** 1일 이상 된 synthetic orphan101개를 최소 SQL 테이블에 넣고 실제 `SupabaseObjects.reconcile/remove`를 두 번 호출. PostgreSQL `now()-interval '1 day'` 표현만 SQLite에서 같은 고정 cutoff로 치환하고 HTTP DELETE는 로컬 기록 함수로 대체했다. 결과: DELETE200회, 고유 키100개, orphan1개 미처리. SQL은 순서를 보장하지 않으므로 PostgreSQL에서도 지속적인 전진을 보장할 수 없다.

**수정 범위:** bounded confirmation 후 completed로 전환하거나 `next_reconcile_at`, `last_checked_at` 및 안정적인 `(created_at,key)` 커서로 공정하게 회전. 지연된 업로드의 재등장을 막는 tombstone/fence는 유지. 항상 이미 지운100개만 재요청하지 않도록 selection 정책 변경.

**수용기준:** 오래된250개+새 active object를 fixture로 구성, bounded cycle 내250개 모두 방문·삭제하고 active는 보존. DELETE 실패 일부가 있어도 다른 키가 진행. 재시작 이후 커서/진행 보존. late upload eventual-consistency 재확인은 유한하고 기록 가능. 실제 disposable PostgreSQL과 mocked Storage로 확인한다.

## 5. P2 — 승인 후보가 100개를 넘으면 조건과 무관한 ID 순서가 추천 후보를 잘라냄

**확인 수준: 코드로 확인.** 현재 운영 후보 수와 실제 누락 발생은 확인하지 않았다.

근거: `src/discovery/service.py:430–444`의 승인 후보 쿼리는 전달된 `categories`를 필터하지 않는다. 그 인자는 공개지도 `_public_catalog`에만 적용된다. 승인 후보 전체를 `place_id`로 정렬한 뒤 `src/recommendations/service.py:113–114`에서 먼저100개만 선택한다. 추천 engine의 조건 검사가 그 이후다. 따라서 같은 도시의 승인 restaurant100개 뒤에 정렬되는 cafe1개가 있고 cafe를 요청하면, 사용 가능한 cafe가 잘려 빈 결과가 될 수 있다. 기존 OSM category-before-cap 개선(`src/discovery/service.py:399`)은 존재하지만 승인 catalog까지 적용되어 있지는 않다.

**재현 방법:** 임시 DB에 같은 도시의 승인 restaurant100개와 cafe1개를 넣되 cafe place_id를 뒤에 정렬되게 지정하고 categories=['cafe']로 submit. `_catalog`에 cafe가 없는지 검사한다. 운영 데이터는 사용하지 않는다.

**수정 범위:** 지원 카테고리 등 안전한 1차 조건을 SQL/batched retrieval에 적용한 뒤 cap을 적용하고, 검증 가능한 결정적 선별 기준을 둔다. 전체 자료를 무제한 LLM에 넘기는 방안은 필요 없다. 데이터 크기와 hard constraint coverage를 응답 관측치로 남긴다.

**수용기준:** 100개를 넘는 복수 카테고리 fixture에서 요청 카테고리의 유효 후보가 ID/등록순서 때문에 사라지지 않는다. 제외/철회/만료 자료가 cap을 소비하지 않아야 한다. `_guard_catalog`와 실제 capture가 같은 선별 규칙을 사용한다.

## 기존 구현과 실제 운영 제약

- durable job, lease/heartbeat, fencing token, checkpoint, SSE replay, idempotency, 실패 파일 재시도, 삭제 tombstone, provider receipt replay, 불명확 과금의 자동 재호출 중단, 예산 예약/정산은 이미 있다. 신규 기능으로 중복 제안하지 않는다 (`src/reliability/jobs.py`, `providers.py`, `budget.py`, `handlers.py`, `src/foundation/routes.py:245`).
- 메일은 `.txt/.eml`, 파일당1MiB, 요청당10개로 제한되고 기본 규칙 분석과 AI 분석, 사용자 수정 overlay/재추출 충돌 보존도 존재한다 (`src/foundation/documents.py:31`, `settings.py:29`, `handlers.py:44`, `repository.py:413`). PDF 첨부 분석이나 메일 계정 동기화는 이 감사에서 새로 구현된 것으로 보지 않는다.
- global dispatcher는 동시에1개 job만 허용한다 (`jobs.py:390`, `dispatcher.py:57`). 지연된 추천이 다른 사용자의 메일 분석/삭제/숙소 처리를 대기시킬 수 있다. 먼저 N+1을 줄이고 작업 종류별 대기시간을 측정해야 한다. per-trip fence를 보존한 제한적 병렬화는 별도 설계 문제다.
- `render.yaml`의 저장소 선언은 Render free1인스턴스, WEB_CONCURRENCY1, Supabase, poll3초/lease90초/heartbeat20초/deadline900초, BACKUP_ENABLED0이다. 실제 배포 환경값을 읽지는 않았다. Supabase 원문 저장 상한은 앱 전체100MiB (`src/storage/objects.py:70–78`), PostgreSQL 풀은5개다.
- operations에는 기본 HTTP p50/p95, queue, 비용/불명확 청구, 오류 집계가 존재한다 (`src/operations/health.py`). endpoint/stage별 SQL 왕복과 pool wait, maintenance 마지막 성공은 현재 집계에서 구분되지 않는다. '모니터링이 전혀 없다'는 표현은 부정확하다.

## 2단계 구현으로 연결할 권고

1단계는 추천 생성/재조회 SQL 일괄화와 관련 근거만 추적하는 snapshot 검증에 집중한다(발견1·2·5). 기존 권한·만료·철회 회귀를 유지하고 운영 동등환경 계측을 남겨 속도와 저장 결과의 안정성을 함께 증명한다.

2단계는 leader 전환과 원문 정리의 진행성에 집중한다(발견3·4). 두 앱 shared PostgreSQL·강제중단·재시작·250개 object fixture로 검증하고 유지보수 상태를 운영 관측에 추가한다. 새 외부 서비스/유료 worker/무제한 병렬 처리 도입 없이 기존 구조를 고칠 수 있는 범위다.
