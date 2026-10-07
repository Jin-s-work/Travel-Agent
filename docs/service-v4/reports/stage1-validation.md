# 고잉 v4 1단계 구현·검증

2026-10-07 · 로컬 코드와 합성 데이터 검증. 운영 배포 완료 기록이 아니다.

## 범위와 현재 판정

추천 후보 누락, 반복 DB 조회, 공개지도 후보의 일정 연결, 여행별 초안, 첫 일정 미리보기, 유지보수 승격과 삭제 진행성을 구현했다. 기존 고잉 이름·여행가방 아이콘·Pretendard·색상·사진 카드를 유지했다. emil-design-eng의 입력 보존·명확한 피드백·포커스·작은 수정 흐름을 적용했다.

기준 commit은 `e8ea36faadac4912a523ae7bea5f02368d7f1ba1`, 작업 branch는 `codex/private-beta-launch`다. 이 보고서는 그 위의 미커밋 작업 트리를 대상으로 한다. 사용자 수정 `docs/presentation/going-class-presentation.key`와 실제 `.env`, 운영 DB·메일을 변경하지 않았다. GitHub push·새 배포·실제 리뷰 수집은 실행하지 않았고, 실제 인증키로 유료 공급자를 호출하지 않았다. 최초 격리 시험의 가짜 키 외부 접근 실패는 아래에 따로 기록한다.

코드 및 관련 자동 시험과 로그인 후 브라우저 검증은 별도 상태다. **브라우저 도구에 저장된 합성 로그인 주소 차단으로 인증 후 전체 흐름·반응형 화면 검증은 미완료**다. 아래 자동 시험 통과를 화면 E2E 통과로 해석하지 않는다.

## 구현한 동작

### 후보와 읽기

- 지점·사용권·만료·카테고리를 먼저 검토하고, 한정된 후보 풀에서 원점과 필수 조건을 평가한 뒤 표시 개수를 제한한다. 13번째 숙소 근처 공개 후보와 100개 식당 뒤 카페를 보존한다.
- 식당 존재로 카페 보완 검색을 생략하지 않는다. 엄격 언어 조건에 공개지도를 대체 근거로 넣지 않는다.
- 장소 팩·사실·출처·사진·리뷰를 batch 조회한다. 추천/리뷰 GET에서 전역 purge와 `trip_places` 쓰기를 제거했다.
- 실제 참조한 지점·팩·근거·리뷰 정책과 집계 버전을 저장한다. 무관한 도시/정책 변경은 결과를 무효화하지 않는다. 관련 근거가 철회되면 해당 카드만 응답에서 숨기고 SQL snapshot·안전한 나머지를 보존한다.
- 승인 상태와 공개지도 지점 식별을 구분한다. 유효한 OSM ID·정규 URL·현재 표시 허용 출처를 가진 공개 후보는 일정 validator로 전달되며, 영업 미확인은 strict에서 미배치, 명시적 잠정 모드에서 provisional이다. 휴무·인원 초과·모호한 지점·철회는 그대로 차단한다.

### 초안과 일정

- 사용자+여행+현재 세션별 서버 초안, version CAS, 7일 TTL, 24KB 제한, 타인 404를 추가했다. GET은 TTL을 갱신하거나 쓰지 않는다. 삭제는 버전 tombstone, 로그아웃은 세션 FK cascade를 사용한다.
- 탐색일·선택 장소·체류시간·필터와 입력 중인 조건·화면 위치를 복원한다. 늦은 다른 여행 응답은 적용하지 않는다. 409에서는 입력을 보존하고 저장된 초안과 비교한다.
- 첫 일정은 별도 비활성 생성 초안으로 계산한다. job 완료가 활성 일정 저장을 뜻하지 않는다. 사용자의 apply에서 소유권·삭제·버전·근거/경로 신선도·고정 예약을 다시 확인하고 첫 revision을 원자적으로 저장한다.
- 준비된 미리보기의 적용 기한은 10분이다. refresh는 기존 job/preview를 복원하고, 응답 유실 후 같은 apply는 같은 revision을 반환한다. 기존 수정 preview/apply/undo와 고정 예약 보호를 재사용한다.

### 유지보수

- 최초 leader 여부와 무관하게 각 프로세스의 supervisor가 현재 lease를 확인한다. 승격 후 실행하고 권한 상실 시 SQL·외부 부작용·예산 해제를 중단한다. 기존 startup/readiness 기준은 유지했다.
- `maintenance_status`에 소유자·마지막 시작/성공/실패·지연을 기록한다. health에서 유료 API를 부르지 않는다.
- 원문/import 정리는 영수증·시도 수·다음 재확인 시각으로 전진한다. 250개 대상에서 완료된 앞 100개가 이후 처리를 독점하지 않는다. 실패 키는 backoff하고 나머지를 진행한다.
- 완료 뒤에도 삭제 tombstone을 보존한다. 뒤늦은 upload/import 등록을 거부하고 재확인을 예약한다. 기존 schema 12 snapshot의 import를 계속 허용한다.

## Before / After / Why

| 이전 | 변경 | 이유 |
| --- | --- | --- |
| 다른 일정에서 마지막으로 본 날짜가 카드 담기의 제안일이 될 수 있음 | 현재 탐색일을 처음 제안하고 사용자가 고친 날짜를 우선 | 탐색 맥락과 명시적 의도를 보존 |
| 첫 일정 생성과 저장의 경계가 불명확 | 짧은 날짜·시각·체류 입력 → 미리보기 → 적용 | 예상 배치와 확정 저장을 구분 |
| 여행 전환/새로고침에 선택이 사라질 수 있음 | 소유권 있는 서버 초안과 충돌 비교 | 재입력 감소, 다른 여행/사용자 누출 차단 |
| 인원 하나를 바꾸기 위해 전체 조건 폼 탐색 | 날짜·인원·출발점 요약 옆에서 해당 값 수정 | 작은 변경의 비용 감소 |
| 식단/이동 조건의 내부 키가 입력 부담 | 한국어 선택명과 hint 제공 | 전문 문자열을 몰라도 사용 가능 |
| 단일 예약의 대표 시각과 구간 시각을 따로 수정 | 일치하는 변경안을 보여주고 확인 후 저장 | 값 불일치 감소, 왕복 항공의 반대 구간 보존 |
| 구체 예약을 눌러도 목록부터 다시 탐색 | 상세/필드로 직접 이동하고 돌아갈 맥락 보존 | 오류 확인과 수정의 이동 횟수 감소 |
| 홈에 여러 문제를 나열 | 기존 SQL 상태에서 다음 행동 최대 3개 | 새 대시보드 없이 우선 행동을 명확히 함 |

## 성능 근거

[원본 계측 JSON](stage1-discovery-metrics.json), [상세 계약·시험 로그](stage1-discovery-validation.md).

SQLite와 disposable PostgreSQL에서 같은 fixture와 측정 경계의 SQL execute/executemany 호출 수가 동일했다. HTTP 인증·SSE polling·connection 초기화 SQL은 제외한다. executemany는 한 batch로 센다.

| 후보 수 | 신규 실행 SQL 이전→이후 | 결과 GET SQL 이전→이후 | GET checkout 이전→이후 |
| ---: | ---: | ---: | ---: |
| 6 | 406→209 | 135→32 | 27→4 |
| 12 | 592→209 | 231→32 | 45→4 |
| 50 | 1,770→209 | 839→32 | 159→4 |
| 100 | 3,320→209 | 1,639→32 | 309→4 |

변경 후 GET 쓰기/transaction은 0, 완료된 실행 재호출은 SQL 3·checkout 1·쓰기 0이다. 계측 범위 실제 외부 호출은 0회다. JSON의 시간은 각 크기/버전/backend 단일 표본이며 5명·20회 p95나 Render 운영 응답시간 측정이 아니다. macOS의 온라인 전용 의존성 파일 읽기 지연도 관측되어, elapsed 숫자를 일반적인 속도 개선율로 사용하지 않는다.

## 자동 시험

최종 전체 실행과 추가 수정 결과는 이 절의 아래 표에 기록한다. 서로 겹치는 파일 재실행을 더해 전체 통과 수를 만들지 않는다.

| 실행 범위 | 확인 결과 |
| --- | --- |
| 최종 Python 전체 / 새 native Python 3.13.5 환경 | **1,089 passed / 17 skipped / 0 failed / 2 warnings / 202.30s**, exit 0; [로그](stage1-python-final.log) |
| 관련 장소·추천·리뷰·사진·보존 회귀 | 150 passed / 44.83s |
| 신규 장소/SQL/철회 계약 SQLite | 16 passed / 7.49s |
| 같은 신규 계약 PostgreSQL | 첫 8 passed / 38.27s, 철회 8 passed / 9.04s |
| 마지막 원격 정리 guard 회귀 | 6 passed / 2.88s |
| 유지보수·원문 삭제·백업·예산 경계 SQLite | 15 passed / 4.17s |
| 같은 유지보수 계약 PostgreSQL | 15 passed / 10.54s |
| 최종 schema12/13 클라우드 import·복원·늦은 업로드 회귀 | 13 passed / 16.81s, 자체 PostgreSQL fixture + mocked Storage |
| 최종 schema13 이관·초안·첫 일정 계약 PostgreSQL | 14 passed / 11.42s |
| 최종 JavaScript 전체 | 155 passed / 0 failed / 0 skipped / 289.48ms; 자산 hash 일치 |
| 파일 내용 기반 사진 캐시 + 후보 계약 | 62 passed / 9.76s |
| 최종 job 진단·재시도·SIGKILL·시계 역행 추가 확인 | 31 passed / 4.94s; [로그](stage1-job-diagnostics-final.log), 전체 시험과 중복되는 부분집합 |

최종 PG 계약에는 아직 없던 초안을 DELETE한 뒤 늦게 도착한 첫 PUT이 409로 차단되는 시험도 포함한다. 초기 PG 통합 명령에서 cloud 파일에 전역 PG 플러그인을 함께 적용해 3 failed/18 passed가 발생했다. 세 실패는 이관 원본 SQLite 파일이 플러그인으로 대체된 fixture 구성 오류였으며, 올바른 별도 명령으로 위 13개를 다시 실행했다. 이 실패 실행을 숨기거나 최초 통과로 보고하지 않는다.

최종 검토에서 예약 재미리보기의 제안값/사용자 입력 오인, 초기 초안 GET의 늦은 응답에 의한 입력 유실, 빠른 A→B→A 전환, 오래된 snapshot에 새 삭제 checkpoint를 적용할 때의 초안 제거, 철회된 장소/삭제 예약/연결 휴식의 양방향 이동 좌표도 추가 검증했다. 사진 파일은 같은 크기·수정 시각으로 교체되어도 현재 내용을 반영한다. 읽기는 1MiB+1 sentinel로 제한하고, 파싱 캐시는 실제 bytes 기준 최대 4개다. 파일이 읽히지 않으면 이전 캐시로 권한을 유지하지 않는다.

### macOS 전체 시험 중단과 격리 재실행

macOS Python 3.13.5에서 전체 수집/import가 여러 분간 파일 읽기를 기다린 뒤, 메일 fixture 작업의 10초 timeout이 연속 발생했다. 실패 전용 진단은 18개 보고에서 공통으로 `state=running`, `stage=building_index`, `attempt=1`을 확인했다. 한 worker가 Chroma→numpy/jsonschema 의존성의 `importlib.get_data`에서 대기하고 후속 worker가 같은 module import lock을 기다렸다. OS sample/lsof에서도 해당 의존성 `.py` 파일 read 대기를 관측했다. 완료되지 않은 전체 실행은 중단했으므로 전체 pass/fail 합계를 제공하지 않는다. [개인정보 없는 실패 진단](stage1-macos-import-stall.json)에 출력된 범위만 보존했다.

10초 timeout, 서비스 코드, fixture 기대값을 완화하지 않고 별도 Linux Python 3.13.12 컨테이너에 코드·합성 fixture만 복사해 재실행했다. 실제 `.env`, 개인 `data`, Keynote, 호스트 `.venv`는 복사하지 않았다. 기존 테스트 이미지 `travel-agent:supabase-local`의 digest는 `sha256:b7ae5df29f8c0f513c9a237ea1cf8ced821fa2a11650f4d120ec7bcedc52bcaf`다. 임시 컨테이너에 pytest 개발 의존성만 추가했고 운영 이미지는 변경하지 않았다. 이번 관측으로 과거 감사의 7개 timeout까지 같은 원인이었다고 단정하지 않는다.

첫 Linux 실행은 **1069 passed / 5 failed / 5 errors / 17 skipped / 177.44s**였다([로그](stage1-linux-first.log)). macOS tar가 만든 `._` 메타데이터 파일 323개가 fixture 복사본에 섞여 synthetic 메일로 오인됐고, 5개 pipeline fixture는 가짜 API key로 OpenAI 401 응답을 받았다. 실제 API key/개인 메일은 사용하지 않았으나 이 최초 실행의 외부 시도를 0회라고 표시하지 않는다. 측정 JSON과 신규 계약 시험의 외부 호출 0회는 별도 범위다.

임시 복사본의 메타데이터 파일만 제거하고 이후 실행에는 HTTP(S)/ALL proxy를 미사용 loopback 포트로 지정해 우발적인 SDK 외부 접근을 차단했다. 별도 자식 프로세스에서 pytest를 찾지 못한 2개 SIGKILL 시험은 임시 컨테이너 전체 Python 경로에 개발 의존성을 설치해 복구했다. 이 범위 재실행은 **74 passed / 4.21s**다. 나머지 job claim 2개 실패는 독립 재실행 **2 passed / 1.11s**였지만 최초의 정확한 원인은 미확정이다. 단독 통과를 전체 통과로 합산하지 않는다.

두 번째 Linux 전체는 **1080 passed / 3 failed / 17 skipped / 172.96s**였다([로그](stage1-linux-second.log)). 사진 manifest의 same-size/same-mtime 캐시 결함을 여기서 확인해 실제 내용 기반으로 수정하고, 실패하던 교체/권한 회귀를 추가했다. 나머지 실패는 dispatcher 동시 실행 계측과 review SIGKILL 진입이다. 합성 실패 DB에서 review job `available_at=05:55:24.071410Z`인 반면, 그 뒤 실행한 자식의 dispatcher `heartbeat_at=05:55:22.495831Z`였다. 작업은 정상 session/controls에서 queued로 남아 있어, 당시 시각에 미래인 작업을 claim하지 않은 것이다. Docker VM의 시계 역행을 관측했으며 이를 무시하도록 운영 시간/lease 검사를 변경하지 않았다.

최종 전체 확인에는 Desktop의 기존 `.venv`와 별개로 `/private/tmp/going-stage1-native313`에 Python 3.13.5 및 `requirements.lock`의 고정 의존성과 pytest를 설치했다. 운영 환경·기존 `.venv`·호스트 시간 설정은 변경하지 않았다. 이 환경에서 **1,089 passed, 17 skipped, 2 warnings / 202.30s, exit 0**으로 완료했다. 이 전체 실행은 PostgreSQL DSN을 비워 로컬 SQLite 경로를 확인했고, 건너뛴 PostgreSQL/cloud 계약은 표의 별도 disposable PostgreSQL 실행으로 구분한다. warning 2개는 Starlette/Authlib의 httpx 지원 중단 예고다.

동시성 계측은 fencing으로 종료된 handler도 `finally`에서 active 수를 내리도록 바로잡았다. claim 진단은 재시도나 시간 보정 없이 한 번만 실행하고 실패할 때만 안전한 상태를 남긴다. 별도 결정적 시험에서 clock을 2초 되돌렸을 때 미래 작업이 queued·attempt 0에 남고, 원래 시각으로 돌아온 뒤 정상 claim되는 것을 확인했다. 운영 jobs/lease 코드를 완화하지 않았다. [시계 역행의 합성 증거](stage1-clock-evidence.json)는 별도로 보존한다.

시각 역행은 컨테이너의 합성 기록에서 관측했으며 VM/NTP/CPU 중 어느 요소가 원인인지는 확정하지 않았다. 검증 종료 후 이 작업의 합성 로그인 서버를 종료하고 `going-stage1-test-runner`, `going-stage1-test-pg` 두 임시 컨테이너를 제거했다. 다른 컨테이너와 운영 서비스를 변경하지 않았다. 보고서·로그는 저장소에 보존했다.

## 수용 기준 대응과 미검증 구분

| 기준 | 자동 검증 또는 구현 근거 | 한계 |
| --- | --- | --- |
| A01~A03 | `test_stage1_discovery.py`: 13번째 근처 후보, 101번째 카페, 식당 존재 시 카페 보완 | fake provider |
| A04 | `test_public_discovery.py`: category 이후 display cap, exclusion/source withdrawal 후 보충, 만료 차단 | 무제한 전수 검색 아님 |
| A05 | `test_thirteenth_public_candidate_is_evaluated_from_requested_origin`의 도심 scope 반환 및 공급자 metadata | 숙소 기준 외부 수집은 미구현 |
| A06 | `test_recommendation_persistence.py`: parallel identical intent, 기존 idempotency/cache 계약 | 실제 공급자 부하 시험 아님 |
| A07~A10 | `test_stage1_itinerary.py`: strict/provisional/add, 휴무·party·모호성·철회, apply 재검증 | 실제 영업/잔여석 확인 아님 |
| A11~A14 | SQLite/PG `test_stage1_discovery.py`, 근거 철회/만료 8가지, 관련 추천 API 회귀 | 단일 표본 성능 계측 |
| A15~A16 | `test_stage1_workspace.py`, `test_workspace_ux.cjs`: A/B/refresh, 세션·여행 삭제, 늦은 응답·409 | 브라우저 화면 미검증 |
| A17 | `test_itinerary_first_preview_ui.cjs`, `test_stage1_itinerary.py`: 탐색일 우선, 명시 날짜, inactive preview/apply | 브라우저 클릭 연결 미검증 |
| A18~A19 | `test_workspace_ux.cjs`: 단일 예약 정합 교정·왕복 반대편 보존, `test_booking_time_conflicts.py` | 실제 사용자 메일 미사용 |
| A20 | 기존 날짜/DST validator와 `test_discovery_ui.cjs`, 날짜 전용 교정 시험 | 모바일 날짜 키보드 미검증 |
| A21 | 서버 CAS와 초안/미리보기 409 입력 보존 JS 시험 | 비교 화면의 시각/포커스 미검증 |
| A22~A23 | 직접 상세 이동·복귀 맥락, 작은 조건 수정, 한국어 선택 UI 구현 | 실제 키보드/포커스 흐름 미검증 |
| A24 | CSS wrapping·label/hint 연결·기존 modal focus 처리 유지 | 폭 4종/200%/모바일 모두 미검증 |
| A25 | maintenance progress/boundaries: follower 승격, 중간 lease 상실, backup 경계 | 임시 DB/합성 owner |
| A26~A27 | 250개 object/import, 실패 키 이후 진행, 늦은 POST/import 등록 차단, 영수증 재확인 | 실제 Supabase 삭제 아님 |
| A28 | 기존 job/SSE 재사용, 첫 일정의 만료·응답 유실·409 UI 회귀 | 실기 재접속 전 흐름 미검증 |
| A29~A30 | `test_foundation_api.py`: A/B 두 여행·동일 파일명·8개+수동·동시 질문·parse/embed/vector 실패 | 전체 실행 결과는 아래 최종 기록을 확인 |

### 재실행

repo에서 실행한다. conftest는 원문·DB·색인 경로를 임시 위치로 바꾸고 실제 key/공급자를 사용하지 않는다.

위 최종 전체 결과를 만든 실제 명령은 다음과 같다. 새 가상환경에는 `requirements.lock`과 pytest 9.1.1을 설치했다. proxy는 우발적인 외부 SDK 접근을 실패시키기 위한 시험 설정이며 운영 설정이 아니다.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  TRAVEL_TEST_POSTGRES_DSN= HTTP_PROXY=http://127.0.0.1:9 \
  HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
  /private/tmp/going-stage1-native313/bin/python -m pytest tests -q \
  -p no:cacheprovider --tb=short
```

마지막 실패 진단 추가분의 재확인은 같은 환경 변수와 Python으로 `-m pytest tests/test_reliability_jobs.py tests/test_review_recovery.py -q -p no:cacheprovider --tb=short`를 실행했다.

일반 개발 환경에서는 다음 명령을 사용한다. `.venv`의 온라인 전용 의존성이 읽기를 지연시키면 별도 로컬 가상환경에 같은 lock 파일로 설치해 확인한다.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -q -p no:cacheprovider
npm test
```

PostgreSQL은 운영 DB 대신 loopback의 폐기 가능한 DB를 `TRAVEL_TEST_POSTGRES_DSN`으로 지정한다. schema12→13/초안/일정은 다음으로 확인한다.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -p tests.postgres_plugin \
  tests/test_stage1_migration.py tests/test_stage1_workspace.py tests/test_stage1_itinerary.py \
  tests/test_maintenance_progress.py tests/test_maintenance_boundaries.py \
  tests/test_stage1_discovery.py -q -p no:cacheprovider
```

`tests/test_cloud_storage.py`는 자체 PostgreSQL fixture와 실제 SQLite 이관 원본을 사용한다. 이 파일에는 `tests.postgres_plugin`을 붙이지 않는다. 붙이면 이관 원본까지 PostgreSQL로 바뀌어 SQLite 파일이 없다는 fixture 오류가 발생한다.

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest tests/test_cloud_storage.py -q -p no:cacheprovider
```

## 브라우저와 실행 제한

- 환경: macOS 26.6.2, Chrome의 합성 서비스 `127.0.0.1:8766`, 합성 OIDC `127.0.0.1:8767`. 실제 Google·운영 사용자·운영 자료를 쓰지 않는다.
- 로그인 전 페이지 DOM만 확인했다. 사용자가 합성 계정 진행을 승인했으나 브라우저 도구의 별도 저장 설정이 8767 접근을 차단해 로그인 동작이 거절됐다. 차단 해제를 요청했고 우회하지 않았다.
- 따라서 로그인 → 여행 → 메일 → 교정 → 탐색 → 저장 → 일정 preview/apply → refresh → undo의 **실제 브라우저 연결 검증은 하지 못했다**. 화면 검증 screenshot 증거도 없다.
- 320/390/768/1440px, 글자 200%, Tab/Escape, 긴 다국어명, 모바일 소프트 키보드는 실기 검증 전이다. JS 상태 시험과 CSS 보완으로 이를 통과했다고 표시하지 않는다. iOS Safari/Android도 미검증이다.
- 재실행 fixture: `PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/stage1_browser_fixture.py`. A/B 각 두 여행, 고정 예약, 합성 장소·가짜 검색, Authlib OIDC 검증을 사용한다.

숙소 유래 좌표/타일을 Photon으로 전송하는 변경은 자동 승인 검토가 구체적 위치 payload와 외부 목적지에 대한 승인 부족으로 거절했다. 기존 고정 도심 검색을 유지했고 `origin_scope_supported=false`, `CITY_CENTER_ONLY`, 도심 범위 밖 원점의 부족 사유를 명시한다. 숙소 거리의 내부 계산은 가능하지만 **숙소 주변을 공급자가 완전히 탐색했다고 주장하지 않는다**.

## 다음 단계가 재사용할 계약

- schema 13과 [이관/dry-run/rollback](stage1-migration.md). 새 테이블을 삭제하는 운영 downgrade는 없다.
- `GET/PUT/DELETE /api/v2/trips/{trip_id}/workspace-draft`: `expected_version`, context, selected_places, conditions_draft; 409는 입력 비교가 필요하다. 현재 로그인 세션에 묶이므로 로그아웃 후에는 복원하지 않는다.
- `POST /api/v2/trips/{trip_id}/itineraries/generation-previews`: 기존 Generation payload와 Idempotency-Key, 202 job receipt.
- `GET /api/v2/trips/{trip_id}/itineraries/{id}/generation-preview`: pending/ready/stale/expired, can_apply, applied_revision_id.
- `POST .../generation-preview/apply`: expected_version=0, preview_id. 기존 수정/undo API는 유지한다.
- `_catalog_on`은 외부 HTTP 계약이 아닌 소유권 검사 뒤 같은 연결로 호출하는 내부 batch API다. identity/display/visit certainty와 model score를 혼합하지 않는다.
- Stage 2의 실제 리뷰 지점 연결·새 추천 모델·실제 언어 품질 gate는 이번 구현 완료 범위가 아니다. 실제 도시별 리뷰 지원/운영 성능/새 배포는 별도 확인해야 한다.
