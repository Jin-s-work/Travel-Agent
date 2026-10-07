# Travel Agent 05단계 실행 가능한 일정 생성과 변경 검증

너는 이 프로젝트의 일정 도메인 엔지니어이자 제품 구현 담당자다.
다음 저장소에서 일정 엔진, API, 타임라인, 편집 흐름을 실제로 구현해.
시간표를 LLM이 작성한 문장으로만 반환하거나 화면 목업만 만들고 끝내지 마.

작업 경로: /Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag
원격 저장소: https://github.com/Jin-s-work/Travel-Agent
범위: P08과 P09의 기본 편집. 상황별 Plan B는 07단계에서 확장한다.

## 시작과 제품 목표

AGENTS.md, git status, 현재 코드, docs/service-v2/IMPLEMENTATION_STATUS.md를 읽어.
PRD.md, TECHNICAL_SPEC.md, RUNTIME_PROMPTS.md, ACCEPTANCE_TESTS.md를 대조해.
모든 문서 경로의 기준은 docs/service-v2다.
현재 구현한 인증·여행·예약 교정·job·예산·장소 사실·추천 인터페이스를 재사용해.
기존 파일과 사용자 변경을 보존하고 감사 시점 커밋으로 되돌리지 마.

본인과 초대한 소수 지인이 사용할 여행 서비스다.
도쿄·바르셀로나 추천에서 선택한 장소를 방문 가능한 일정 초안으로 바꾼다.
사용자가 원하는 것은 빽빽한 시간표가 아니라 실행할 수 있고 수정할 수 있는 계획이다.
고정 예약을 지키고 이동·영업·인원·휴식 제약을 설명할 수 있어야 한다.
사용자가 고른 장소가 전부 들어가지 않으면 빠진 이유를 알려준다.
저장된 일정과 외부 예약의 확정 상태는 다른 개념이다.

현재 예약 목록 화면과 일정 화면의 책임을 분리한다.
src/itineraries/ 아래 model, intervals, constraints, scheduler,
travel_time, edits, repository 계층은 제안 파일 구조다.
현재 프로젝트의 동등한 모듈이 있으면 재사용하고 인터페이스를 문서화한다.
LLM의 역할은 의도 해석과 검증된 일정 설명이다.
권한, 시간 계산, 충돌 검사, 점수, 저장, 취소는 코드가 담당한다.

## 입력과 저장 모델

일정 생성 입력 snapshot에 다음을 포함한다.
trip_id, trip_version, itinerary_request_version, city/stop,
날짜 범위, IANA timezone, 성인·아동 조건,
숙소 또는 사용자가 고른 출발점, 고정 booking_event 참조,
선택한 place_id와 recommendation_run_id,
여행 밀도, 식사 시간대, 걷기·이동 선호, 휴식 선호,
명시한 필수 조건과 가격 범위, 최신 사실·정책 버전.

어떤 값이 기본 제안이고 어떤 값이 사용자가 확인한 값인지 보존한다.
미입력 출발점·체류시간을 이미 확인한 사용자 선택으로 표시하지 않는다.
여러 도시 구간은 날짜·timezone·도시 간 이동으로 연결한다.
추천 미지원 도시의 예약을 삭제하거나 다른 도시로 옮기지 않는다.
단계 구현이 도시별 일정부터라면 지원 범위를 명시하고 도시 간 경계를 검증한다.

Itinerary는 trip_id, version, input_snapshot_ref, validation_status,
active_revision_id, created_by, created_at, updated_at을 가진다.
Item은 편집 간 안정적인 item_id, item_type, place/booking 참조,
local_start/end, start/end_timezone, start/end_instant,
duration, locked, lock_origin, verification_status, source_refs를 가진다.
item_type은 booking/place/meal/rest/travel 등 구현한 타입만 허용한다.
locked와 reservation_status를 같은 boolean으로 합치지 않는다.
사용자가 잠근 미예약 장소도 잠금 항목이며 확정 예약으로 표기하지 않는다.

TravelLeg는 from/to item, mode, duration, distance nullable,
basis(provider/estimate/unknown), checked_at, expires_at,
source/policy/version을 가진다.
추정 이동시간과 실제 경로 제공자가 확인한 이동시간을 구분한다.
Conflict는 code, item_ids, interval, constraint, reason, possible_actions를 가진다.
UnplacedCandidate는 place_id, reason_codes, missing_facts, 재검토 가능한 조건을 가진다.

revision은 변경 전후, 편집자, command, 기준 version, 검증 결과를 보존한다.
일정이 참조하는 예약이 나중에 바뀌면 원 예약의 변경을 숨기지 않는다.
이전 일정은 오래된 조건임을 표시하고 재검증하며, 고정 예약을 임의로 다른 시간에 유지하지 않는다.

## 시간 계산과 영업 조건

시간 구간은 [start,end)로 통일한다.
종료와 다음 시작이 같더라도 장소가 다르면 이동·버퍼가 들어갈 수 있어야 한다.
UTC instant로 비교하고 사용자에게는 해당 도시의 현지 시각과 필요한 timezone을 표시한다.
도쿄는 Asia/Tokyo, 바르셀로나는 Europe/Madrid다.
항공은 출발과 도착의 현지 날짜·시각·시간대를 각각 보존한다.

현지시각이 DST 때문에 존재하지 않으면 확정하지 말고 오류/확인 대상으로 반환한다.
두 번 존재하는 현지시각은 offset 또는 fold를 정하기 전 확정하지 않는다.
서버 timezone, 사용자 기기 timezone, 시설 timezone을 섞지 않는다.
날짜만 있는 정보에 임의의 자정 예약을 생성하지 않는다.

요일별 복수 영업 구간, 브레이크, 자정 넘는 영업과 예외 휴무를 지원한다.
예: 합성 식당의 월요일 22:00~다음날 02:00은 4시간 구간이다.
방문 전체 체류시간이 영업 구간 안에 있어야 한다.
last_order와 last_entry는 방문 시작 시각의 별도 제약이다.
단순히 도착 때 열려 있었다는 이유로 체류가 가능한 것으로 판정하지 않는다.
방문일 적용이 불명확한 통상 영업시간은 잠정값이고 verified 일정의 근거가 아니다.

숙박 4박을 4일 전체 busy로 만들지 않는다.
체크인 가능 구간·체크아웃 마감·기준 위치·짐 보관 확인 여부를 각각 다룬다.
공항 접근·체크인·출국 준비 버퍼를 일반 도보 이동의 기본값과 합치지 않는다.
사용자 또는 확인된 예약 정보의 버퍼를 우선하고,
제품 기본 버퍼는 조정 가능한 계획 가정으로 표시한다.

## 필수 조건과 선호

필수 조건은 고정 예약, 여행 구간, 명시 인원·연령 제한,
확인된 휴무, 입장 마감, 최소 이동·체류·예약 버퍼다.
사용자가 식단·접근성·최대 이동량·예산을 필수로 지정했다면 그 의미를 유지한다.
미확인 알레르기 대응을 안전하다고 확정하지 않는다.
정보 부족과 실제 불일치를 다른 코드로 반환한다.

각 제약 검증 결과는 satisfied/violated/unknown의 세 값이다.
violated는 배치 금지다. unknown도 기본 엄격 모드에서는 미배치로 반환한다.
사용자가 명시적으로 잠정 초안을 허용한 경우에만 unknown 후보를 provisional로 배치한다.
이 경우에도 확인된 고정 예약·휴무·인원 불일치를 침범할 수 없고,
미확인 이동·영업 조건을 별도 목록과 해당 항목에 표시한다.
job의 succeeded/partial와 일정의 validated/provisional/conflicted는 다른 상태다.

여행 밀도, 음식 다양성, 동네 이동 최소화, 일반 휴식·식사 시간대는 선호로 시작한다.
이 값들은 사용자가 수정 가능하고 어떤 가정으로 일정이 만들어졌는지 볼 수 있어야 한다.
일정에 쉬는 시간이 있다는 이유로 실패로 처리하지 않는다.
모든 후보를 배치하려고 필수 조건을 완화하지 않는다.

## 이동시간 어댑터

04단계 장소 좌표와 출처의 사용 가능 범위를 확인한다.
route provider, 허용 좌표 추정, 정보 없음의 세 경로를 interface로 분리한다.
API key가 없으면 deterministic fake 경로로 통합 테스트를 작성한다.
좌표가 없거나 바다를 가로지르는 직선거리를 실제 도보 경로처럼 제시하지 않는다.
대중교통 환승·출입구·대기 등 정보가 없으면 그 한계를 표시한다.

후보를 지역별로 줄인 뒤 필요한 pair만 조회한다.
요청 수뿐 아니라 matrix의 요소 수와 재시도 비용을 계측한다.
cache key에는 출발/도착 식별자, mode, 필요한 출발 시각과 policy/version을 포함한다.
동일 pair라고 항상 같은 시간인 것으로 취급하지 않는다.
공급자 정책이 허용한 항목·기간만 캐시한다.
실패·미확인을 0분으로 바꾸지 않고 unknown_travel을 남긴다.
이동시간 미확인이 있는 일정은 잠정 초안 저장은 가능하되 검증 완료로 표시하지 않는다.

## 일정 생성 알고리즘

처음부터 최적화 solver를 도입하지 말고 설명 가능한 결정적 삽입으로 구현한다.

1. 입력의 소유권·version·날짜·필수 조건을 검증한다.
2. 예약과 잠금 항목을 먼저 배치하고 서로의 충돌을 검사한다.
3. 각 날짜의 사용 가능 구간에서 이동·버퍼·휴식 요구를 반영해 빈 구간을 만든다.
4. 적합 후보를 도시·권역·유형으로 묶고 추천 결과·선택 우선순위를 적용한다.
5. 각 후보의 가능한 방문 구간과 영업·입장·인원을 계산한다.
6. 삽입 위치마다 이전 항목 → 후보 → 다음 항목의 이동시간을 검사한다.
7. 검증 모드에서 허용한 위치만 비용 함수로 비교한다.
   확인된 위반은 항상 제외하고 unknown은 명시적 잠정 모드에서만 취급한다.
8. 동점은 안정적 place_id·시각 순으로 처리한다.
9. 삽입할 수 없는 후보는 이유를 기록하고 다음 후보를 평가한다.
10. 전체 일정에 같은 validator를 다시 실행한다.

비용 함수는 추가 이동, 명시한 식사창 이탈, 동네 왕복, 밀도·가격 선호를
버전 설정과 fixture로 설명한다. 내부 점수를 성공 확률처럼 표시하지 않는다.
탐색 후보 수, 실행시간, 경로 조회와 비용에 상한을 둔다.
상한에 걸리면 부분 일정과 미배치 사유를 반환하고 무한 재시도하지 않는다.

CONFLICTING_LOCKS, NO_TIME_WINDOW, PARTY_MISMATCH, NO_ROUTE,
UNKNOWN_OPENING_HOURS, MISSING_REQUIRED_FACT 등을 구조화해.
추가 코드는 명세에 등록하고 사용자 문구와 분리한다.
이미 고정 예약끼리 충돌하면 어느 예약도 삭제·이동하지 않는다.
validator가 검증하지 않은 초안에 '방문 가능' 배지를 붙이지 않는다.

## API와 편집 계약

POST /api/v2/trips/{trip_id}/itineraries는 조건·선택 후보·trip_version을 받고
202와 job_id/itinerary_id 참조를 반환한다.
GET /api/v2/trips/{trip_id}/itineraries/{id}는 현재 revision과
items, legs, conflicts, unplaced, unresolved_conditions를 반환한다.
개인 응답은 private,no-store다. 다른 소유자의 ID는 404다.

기존 기술 명세의 PATCH를 commit 경로로 사용한다.
preview 경로가 없다면 같은 일정 아래 POST /edit-previews를 추가하는 안을 제안한다.
preview의 입력은 expected_version과 whitelist command 배열이다.
허용 명령은 add/remove/move/lock/unlock이며 임의 SQL·함수·URL은 금지한다.
자연어 요청도 이 명령으로 변환 후 동일한 코드 검증을 거친다.

preview는 preview_id, base_version, normalized_commands,
before/after diff, 영향 구간, 충돌과 미확인, 만료 시각을 반환한다.
preview에서 원 일정과 외부 예약은 변경하지 않는다.
PATCH는 preview_id, expected_version을 받고 preview의 사용자·여행·내용을 검증한다.
preview를 따로 저장하지 않는 구현이라면 서명/서버 참조와 동등한 변조 방지 계약을 정한다.

적용 직전에 현행 여행·일정 version, tombstone, 고정 예약과 사실 신선도를 재검증한다.
버전이 달라지면 409 VERSION_CONFLICT와 새 상태 조회 경로를 제공한다.
새 충돌이 생겼으면 저장하지 말고 재검토용 preview를 반환한다.
성공은 한 트랜잭션에서 새 revision을 만들고 active pointer와 version을 갱신한다.
SSE 종료·화면 닫힘을 일정 적용이나 취소로 해석하지 않는다.

잠금 해제는 사용자의 명시적인 그 항목 편집 요청이 있어야 한다.
LLM이 다른 조건을 맞추려고 lock을 풀 수 없다.
외부 예약 시간이 바뀌거나 취소되는 것은 이번 편집 API의 효과가 아니다.

undo는 최소 10개 편집을 지원한다.
과거 version 번호로 포인터만 되돌리지 말고 복원하려는 내용을 새 revision으로 만든다.
현재 고정 예약·삭제·사용 권한·정보 신선도로 다시 검증한다.
삭제된 원문·장소 데이터나 폐기된 권한을 undo로 부활시키지 않는다.
자동 정보 갱신 revision과 사용자 편집 revision을 구분하고 되돌릴 대상을 명확히 한다.

## 화면 동작

여행 날짜별 타임라인에 시각·원어/표시 이름·이동·체류·잠금·확인 상태를 보여준다.
사용자가 판단해야 할 충돌과 미확인을 상단 요약과 해당 항목 양쪽에 표시한다.
긴 날은 날짜 전환과 현재 위치 복귀를 제공하고 모바일에서 가로 넘침이 없게 한다.
이동 구간은 방문 항목과 다른 모습으로 표시하되 중요 정보는 색상에만 의존하지 않는다.

'일정 만들기' 전에 사용할 조건을 확인하고 중복 탭은 동일 job으로 처리한다.
작업 중에는 실제 진행 단계, 부분 결과, 취소 가능 여부를 표시한다.
실패해도 이전 저장 일정과 선택 후보를 유지한다.

편집 UI는 항목 선택 → 변경 입력 → 영향 미리보기 → 적용/취소다.
앞뒤 이동 증가, 바뀌는 시각, 영향을 받는 예약을 diff로 보여준다.
드래그뿐 아니라 위/아래 이동과 시간 입력, 키보드 편집을 제공한다.
실패한 적용 후 사용자의 입력은 유지하고 최신 일정 재확인 경로를 제공한다.
확정 예약의 잠금 표시, 일정 저장과 예약 완료의 다른 상태를 명확히 보여준다.
미구현 Plan B 버튼을 실제 기능처럼 노출하지 않는다.

## 합성 시나리오와 검증

다음은 실제 운영시간이나 실제 가게 정보가 아닌 시험 전용 데이터다.
도쿄 4인 여행: 식사 예약 12:00~13:00, 다음 입장 예약 15:00을 고정한다.
체류 90분·양쪽 이동 각 30분 후보는 150분이 필요하므로 120분 창에 들어가지 않는다.
식사를 12:00~12:30으로 바꾼 별도 fixture에서는 150분 창에 정확히 들어가지만,
추가 버퍼 10분이 있으면 배치 실패여야 한다.
합성 식당이 14~17시 브레이크면 13시30분부터 90분 체류는 불가능해야 한다.
4박 호텔은 일정의 모든 낮 시간을 막지 않아야 한다.

바르셀로나 2인 여행은 Europe/Madrid DST 경계를 테스트 clock으로 고정해.
없는/중복 현지 시각, 자정 이후 운영, Tokyo 출발 항공의 양단 timezone을 확인한다.
고정 예약 2개 충돌, 경로 장애, 미래 영업 미확인, 인원 불일치,
동일 장소 중복, 긴 체류, 일정 version 충돌을 포함한다.

PLAN-01~07, PLAN-08의 기본 편집 부분, UI-01~03과 권한·예산 시험을 연결한다.
Plan B 채택 시험은 07단계 미실행 하위 항목으로 남기고 전체 ID를 통과로 처리하지 않는다.
고정 입력·시계·fake provider에서 일정과 validation 결과가 재현되는지 확인한다.
preview만 했을 때 원 일정·예약 행과 외부 예약에 변경이 없는지 검사한다.
preview 저장·usage ledger와 예산 내 읽기 전용 경로 조회는 허용된 부작용이다.
apply 실패·동시 편집·undo 뒤 재조회까지 E2E로 확인한다.
성공한 단위시험만으로 실제 경로 공급자의 결과를 검증했다고 보고하지 않는다.

## 구현 순서와 인계

먼저 interval·timezone·validator 순수 함수와 fixture를 만든다.
그다음 travel adapter → scheduler → repository/job/API를 연결한다.
이후 읽기 타임라인 → preview/apply → undo → 모바일 오류 상태를 구현한다.
각 묶음의 의미 있는 시험이 통과하면 다음으로 진행한다.
같은 제약을 UI·생성·편집마다 따로 구현하지 말고 validator를 재사용한다.

산출물은 코드, API 계약, migration, 합성 시나리오, 경로 비용 계측,
동작하는 타임라인·편집 화면과 실제 검증 기록이다.
docs/service-v2/IMPLEMENTATION_STATUS.md에 구현과 live 검증을 따로 기록한다.
실행 명령·결과·코드 revision·미커밋 변경·미확인 경로를 남긴다.
운영 데이터에 파괴적인 충돌/삭제 실험을 하지 말고 임시 데이터로 검증한다.
키가 없어도 로컬 구현을 마치고 실제 경로 검증만 미완료로 구분한다.
이미 승인된 범위의 작업은 다시 승인받지 말고 수행한다.
다음 단계는 docs/service-v2/prompts/06-beta-launch.md다.
