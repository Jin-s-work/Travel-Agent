# 1단계 구현 프롬프트 — 입력을 줄이고 여행에서 탐색까지 연결

아래 전체를 새 구현 요청으로 그대로 사용한다. 2단계의 숙소/경로까지 동시에 완료했다고 주장하지 않는다.

---

너는 Travel Agent의 제품 엔지니어이자 UI 구현 책임자다. 다음 저장소의 기존 서비스를 실제로 단순하게 만들어라.

`/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`

이 작업은 PRD를 다시 요약하는 작업이 아니라 저장·API·실제 화면·실패 복구·브라우저 검증까지 완료하는 구현 작업이다. 다만 이미 구현된 부분을 재작성하지 말고 현재 코드와 결과를 먼저 확인하라.

## A. 읽을 자료와 보존할 계약

1. 적용되는 `AGENTS.md`, `git status --short`, 현재 branch/log, 실행 방법, `docs/service-v3/IMPLEMENTATION_STATUS.md`를 확인한다.
2. `docs/service-v3/PRD.md`, `CITY_COVERAGE.md`, `docs/service-v2/IMPLEMENTATION_STATUS.md`, `TECHNICAL_SPEC.md`, `REVIEW_DATA_SPEC.md`, `RUNTIME_PROMPTS.md`를 읽는다.
3. `/Users/jinsangwoo/.codex/skills/emil-design-eng/SKILL.md`를 읽고 적용한다. 정보 우선순위·기본값·접근성·불필요한 단계 제거를 실제 코드로 바꾼다. Before/After/Why 표를 결과 보고서에 포함한다.
4. 기존 인증/초대/소유권, jobs/idempotency/budget, 예약 교정, 삭제 tombstone, 검색 세대, itinerary validator와 preview/apply/undo를 유지한다.
5. 현재 배포는 Render Free + Supabase Free이며 `ZERO_SPEND`가 켜져 있을 수 있다. 코드가 무료라고 외부 호출까지 무료로 간주하지 말라. 루트 개발 `.env`와 운영 `.env.render`를 섞지 말고 비밀값을 출력하지 말라.
6. 기존 사용자 변경을 보존하고, 적용 가능한 체크아웃에서 작업한다. 배포 시 이미 승인된 feature branch/서비스를 사용하며 main으로의 강제 push·인증 우회·유료 증설을 하지 말라.

## B. 목표 사용자 경로

로그인 → 도시·여행 기간 입력 → 여행 생성 → 탐색 진입 → 가져온 조건으로 추천 보기 → 상세 → 저장 → 새로고침.

예약 메일이나 숙소, 예산, 리뷰 필터를 입력해야만 첫 여행을 만들 수 있어서는 안 된다. 데이터가 없는 도시는 분명하게 설명하고 저장 기능으로 이어져야 한다. 모든 도시가 실제 맛집 추천까지 완성됐다는 문구를 만들지 말라.

이번 단계의 필수 완료는 ‘여행 정보 중복 입력 없이 탐색하고 장소를 저장하는 흐름’이다. 부가 기능을 새로 늘리는 것으로 이 문제를 해결했다고 보고하지 말라.

## C. 현재 코드에서 먼저 확인할 문제

- `src/destinations/cities.json`과 `__init__.py`: 100개 도시, 정규화 별칭, IANA 시간대, 기본 통화. 별칭이 다른 도시와 충돌하지 않는지 확인한다.
- `src/discovery/models.py`, `service.py`, `routes.py`: `CityId`, `stay_options`, `context_state`, `context_issue`, 필드 오류, 후보 자료 상태.
- `src/recommendations/service.py`: 새 요청 입력 snapshot과 trip/condition version 검사, Idempotency-Key. 이미 해결한 stale 체류일 버그를 되돌리지 않는다.
- `web/js/foundation.js`: `tripForm`, `field`, `calendarDate`, `discoveryStayChoice`, `discoveryConditionsForm`, `applyRecommendations`, `renderRecommendationResults`.
- `web/index.html`, `web/css/foundation.css`, `web/sw.js`, `scripts/version_web_assets.py`.
- 기존 간소화 폼과 네 자리 date min/max는 재사용한다. 폼을 접었다는 이유로 잘못된 필드가 보이지 않거나 포커스를 받을 수 없으면 수정한다.

Madrid 여행을 Barcelona로 선택하게 한 흐름을 재현할 필요는 없지만, 현재 Madrid가 올바른 ID/시간대/체류일로 동작하는 회귀 시험을 유지해야 한다. 시간대가 같다고 도시가 같은 것은 아니다.

## D. 화면 구조를 네 개의 주요 경로로 정리

홈 / 탐색 / 일정 / 예약을 주요 메뉴로 둔다. 더보기에는 여행 질문·기록/예상 지출·오프라인·설정을 둔다. 관리자는 검수 도구를 별도 입구로 연다. 기존 탭 링크와 호출 경로는 alias/redirect 또는 동일 렌더러로 보존한다.

- 홈: 여행·기간·도시·인원, 다음 일정, 해야 할 확인 최대3개. 아무 자료도 없으면 예약 업로드와 장소 찾기 두 개 CTA만 우선한다.
- 탐색: ‘현지 탐색/대표 명소’와 ‘음식점/카페/볼거리’를 독립 필터로 유지한다. 보관함은 같은 화면 내 탭으로 접근한다.
- 일정: 오늘 보기는 별도 필수 단계가 아니라 여행 현지 날짜의 타임라인 보기다.
- 예약: 메일 업로드/수동 예약과 예약 준비 할 일을 구분하되 한 문맥에서 접근한다.
- 모든 화면 상단에 현재 여행·도시 구간·기간을 유지한다. 여행 변경으로 URL/히스토리/모달이 다른 여행 내용을 가리키지 않게 한다.

메뉴 축소로 기존 기능을 삭제하지 말라. 키보드 포커스 순서와 모바일 ‘더보기’ 위치도 함께 설계한다. 처음 방문에서 긴 기능 설명을 읽도록 강요하지 말라.

## E. 첫 여행 폼과 도시 선택

필수 입력은 도시·시작일·종료일이다. 성인 기본값1명은 보이는 요약에서 수정할 수 있다. 제목은 도시명으로 제안하고 이름 편집은 선택이다. 기존 title 최대길이/오류 처리는 유지한다.

도시는 로컬 등록부를 검색한다. 한국어/영문/등록한 현지어 별칭을 입력하면 같은 ID 후보를 반환한다. 자동완성은 외부 API 호출 없이 수행한다. 후보에는 국가와 영문명을 함께 보여 같은 이름 혼동을 줄인다. 기본 시간대·통화는 후보에서 가져오며 사용자는 IANA 문자열을 직접 알 필요가 없다.

등록부가 일시적으로 로드되지 않아도 저장된 여행·예약 조회는 유지한다. 도시 직접 입력 경로를 유지하되 추천 지원은 별도 상태로 표시한다. 새 도시를 자유 문자열로 저장했다고 현재 지원100도시 중 하나인 것처럼 꾸미지 않는다.

한 도시의 stop 날짜는 여행 전체 기간을 상속한다. 다른 도시 추가 후에만 구간 날짜를 드러낸다. 반환 방문(도쿄→다른 도시→도쿄)에서 각 stop_id를 유지하고 가운데 체류 공백을 채우지 않는다. 날짜 변경으로 이미 있는 예약을 이동하거나 삭제하지 않는다. 항공 출발/도착 시간대도 바꾸지 않는다.

아동은 동행 여부를 선택한 뒤에만 인원/나이를 받는다. 아직 확인하지 않은 값을 ‘아동 없음’으로 변환하지 않도록 상속 모델과 기존 데이터 이관을 결정한다. 기존 명시 아동 목록과 사용자 교정은 보존한다. 전체 여행 인원을 고치지 않고 식사 인원만 바꿀 수 있어야 한다.

## F. 공통 날짜 컨트롤

모든 여행·도시 구간·탐색·예약·일정·예약 준비에서 공통 date helper를 사용한다. API는 `YYYY-MM-DD`, 4자리 연도, 실제 달력 날짜를 검증한다. 날짜+시각은 timezone과 분리한다.

native date 입력에는 적절한 min/max를 사용하고, 연도0000/6자리/불가능한 날짜를 제출하지 못하게 한다. maxlength만 넣고 해결했다고 주장하지 말라. 브라우저별로 year segment가 달라질 수 있으므로 실제 Chrome에서 연속 숫자, 전체 붙여넣기, 달력 선택, 날짜 수정, 윤년을 확인한다. 네 자리 이후 숫자를 다른 연도로 잘라 저장하거나 현재 연도로 추측하지 않는다.

사용자가 숫자8자리로 입력하는 편의 기능을 넣는다면 명시된 한 포맷으로만 파싱하고 잘못된 입력을 그대로 보여 수정하게 한다. 거대한 date-picker 라이브러리가 꼭 필요한지 검토하고 새로운 프레임워크 전환을 이 단계에 끼워 넣지 말라.

탐색에서는 여행일 칩을 우선하고 긴 여행은 달력/주 단위 이동을 제공한다. 도시 변경 시 이전 날짜가 새 stop 안에 있으면 유지, 아니면 유효일을 제안하고 화면에 이유를 표시한다. 여러 비연속 stop의 min~max 사이 공백은 서버에서도 거절한다.

## G. 조건 상속과 저장 모델

현재 `discovery_conditions`를 재사용하거나 호환 가능한 migration으로 확장한다. 반복 입력을 줄이는 것은 진실의 원본을 여러 개 만드는 일이 아니다.

다음 구조를 구현한다.

- `trip_context`: trip_id/version, stop_id, city_id, visit_date, party, timezone.
- `overrides`: 사용자가 이 탐색에서 바꾼 필드만. 값이 null인 것과 override가 없는 것을 구분한다.
- `resolved_conditions`: 기본값+명시 override의 결정적 결과, provenance와 validation 결과.
- `snapshot`: recommendation_run에서 읽을 수 있는 불변 입력, 소유 범위와 모든 관련 version.

여행 변경 시 상속값만 다시 계산한다. 사용자 override를 자동 덮어쓰지 않는다. 도시/날짜 변경으로 숙소/기준점이 맞지 않으면 유지 중인 낡은 좌표로 다른 도시 거리를 계산하지 말고 재확인 상태로 둔다. 2단계의 숙소모델이 아직 없으면 label-only 상태로 보존한다.

기존 명시 conditions JSON은 사용자 override로 보수적으로 이관한다. 기존 snapshots/jobs/itinerary revision은 과거 의미가 유지돼야 한다. 사용자 불명의 자료를 임의 계정에 배정하지 않는다. SQLite 로컬과 Postgres/Supabase 운영 경로의 migration/복구 전략을 함께 작성한다.

## H. ‘추천 보기’ 한 번으로 끝나는 API

기존 저장 API를 호환 유지하면서 새 단일 행동 엔드포인트 또는 동등한 서버 orchestration을 구현한다. 권장 계약 예시:

`POST /api/v2/trips/{trip_id}/discovery-intents`

헤더 `Idempotency-Key`, 본문 `expected_trip_version`, `expected_conditions_version`, `stop_id`, `visit_date`, `overrides`, `filters`. client owner_id는 받지 않는다. 실행자·scope·operation·payload hash를 기존 저장소 계약으로 묶는다.

서버 순서: 인증/소유권/삭제 확인 → 버전 확인 → 상속값 계산 → 체류일/시간대/필수조건 검증 → 조건 snapshot 저장 → durable recommendation job 접수 → receipt 반환. 같은 데이터베이스 안에서 가능한 범위를 원자적으로 저장한다. 외부 네트워크를 SQL 트랜잭션 안에서 기다리지 않는다. 조건 저장 이후 enqueue 실패 가능성이 남으면 durable intent state로 복구하며 사용자가 ‘조건 저장’ 버튼을 따로 누르지 않게 한다.

응답은 `intent_id`, `conditions_version`, `resolved_context`, `run_id`, `job_id`, `state`, 상태/이벤트 링크를 제공한다. queued와 succeeded를 혼동하지 않는다. 새로고침/재연결은 GET/SSE 재개이며 POST 반복이 아니다. 재시도에서 기존 idempotency key를 유지하고 payload가 달라지면 새 key를 만든다.

단순히 두 fetch를 프런트엔드에서 연달아 실행하고 한쪽 실패를 무시하는 구현은 완료가 아니다. 제출 버튼 잠금은 보조 수단이다. 기대 버전 충돌은409, 다른 사용자/삭제 여행은404다.

## I. 빈 결과와 카드/상세/저장

카드에는 이름/원어명, 서버가 계산한 이유2~3개, 중요한 미확인, 가격 기준, 확인일, 상세/저장 행동을 먼저 둔다. 긴 기술 근거는 펼치지만 인원·영업·예약 미확인은 감추지 않는다. 분류와 추천 관점을 카드마다 혼합하지 않는다.

빈 결과는 다음을 구분한다: 미등록 도시, 등록됐지만 후보자료0, 필수조건을 모두 위반, 자료부족으로 판단불가, 예산/공급자 제한, 세션 만료. ‘아무것도 없어요’ 하나로 처리하지 말라. 각각 장소 저장, 필터 직접 변경, 근거 확인, 재로그인 같은 행동을 하나 제안한다.

조건 완화는 사용자 선택 새 요청으로만. 엄격 리뷰 OFF에서 한국어0%를 만들지 않는다. no_live_candidates를 예산 부족이라고 표시하지 않는다. 합성팩은 테스트 표시를 유지하고 운영에서 제외한다.

카드가 늦게 도착해 최신필터를 덮어쓰지 않게 epoch/request sequence를 유지한다. 이전 결과가 표시 중이면 그 snapshot 기준임을 명시한다. 저장은 서버 성공 후 반영하며 실패/중복에서 입력·메모·이미 저장한 항목을 보존한다.

## J. 메일 체험 경로

`examples/mail-test-pack/README.md`, `expected-results.json`, `web/examples/mail-test-pack.zip`을 재사용한다. 예약 화면에서 한 번 눌러 받게 한다. ZIP을 압축 해제해 eml을 업로드한다는 안내와 ‘실제 예약 아님’을 표시한다.

무료 운영/AI OFF이면 파일 접수·검사·실패 복구와 수동 예약 체험을 안내한다. 테스트메일을 감지해 운영에서 몰래 fake extraction 결과를 넣지 말라. 허가된 공급자가 없는 상태를 테스트 성공으로 꾸미지 말라. 파일별 성공/실패와 재시도를 구분하고 이전 활성 예약 보존을 검증한다.

## K. 검증과 완료 조건

먼저 기존 시험을 실행해 baseline을 기록하고 의미 있는 실패를 수정한다. 정확한 실행 명령은 환경에 맞추되 다음 그룹을 포함한다.

```
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_destinations.py tests/test_discovery_foundation.py tests/test_recommendation_api.py -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
```

추가 시험:

1. A/B 각각2여행, Madrid/도쿄/바르셀로나/뉴욕. 다른사용자ID404, stop_id가 다른여행이면 거절.
2. 100개 도시 등록/별칭/유효timezone/통화, 잘못된 조합, 등록목록 밖 도시.
3. 첫 여행의 필수입력3개, 상속값 반복입력0, 아동/예산/수동조건 보존.
4. 비연속 재방문, 기간수정, 오래된조건, 날씨/도시중심 추정 없는 처리.
5. 단일의도 접수 중 응답유실·동시클릭·중복key·다른payload409·서버재시작.
6. no_live_candidates와 필수조건 부족, 세션만료, 필드오류, 이전결과 유지.
7. 0000/6자리연도/윤년/잘못된월일/달력/붙여넣기, 실패후입력유지.
8. 12eml 로더/검사, 합성여행의8+수동1전체조회. 실AI정확도는 별도 결과.
9. Chrome 실제 생성→탐색→상세→저장→새로고침. 320/390px·200%·긴 다국어·키보드·Escape/포커스복귀.
10. 외부키 없이 fake로 흐름검증하고 실제 후보0도시는0으로 유지. 순서만 검증한 synthetic E2E를 운영 추천 품질로 보고하지 않음.

최종 보고: 변경 파일/DB이관 여부, Before/After/Why, 실제 시험 명령·통과/실패/skip, 브라우저·기기 범위, 실제데이터/라이브미검증, 남은위험, 2단계 연결점. `docs/service-v3/IMPLEMENTATION_STATUS.md`와 필요시 기존 진행 기록을 갱신한다. 스크린샷을 보존한다. 실제 배포했다면 revision과 URL/health를 기록하고, 배포하지 않았다면 로컬완료와 운영반영을 구분한다.
