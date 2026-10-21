# 3단계 보고서 — 여행 에이전트: 도구를 고르고, 미리보기까지만 만든다

> v6 3단계 프롬프트(`prompts/03-trip-agent.md`)를 실행한 기록이다. 트랙 5개(에이전트 시험 · 날씨 · Plan B · 화면/E2E · MCP)가 구현·검수·수정을 거친 뒤 통합 세션이 다시 돌린 결과를 담는다. 바꾼 파일, 실제로 돌린 명령과 결과, 돌리지 못한 것, 대표에게 넘길 일을 적는다.

2026-10-21 · Claude Code 세션 · **이 보고서 작성 시점(2026-10-21) 미커밋(작업 트리에만 있음, HEAD 5a4443a 그대로)** · 같은 날 로컬 커밋 5개로 나눔(코드 4 + 문서 1, push 없음 — 목록은 IMPLEMENTATION_STATUS 「커밋」 절) · 배포·패키지 설치 없음

**세 줄 요약**

- '여행에 물어보기'의 의미 검색 생성 분기만 tool-calling 에이전트로 바꿨다. 도구는 10개(read 7 · preview 2 · draft 1)이고 적용·되돌리기·삭제·발송·URL 열기 도구는 없다. 빠른 경로(날짜 목록·정확 참조·인원·time_conflicts·basic)는 LLM 없이 그대로다.
- 모든 숫자는 **합성 자료와 손으로 쓴 가짜 모델(replay·scripted)** 로 얻었다. 루프와 안전 장치 시험이지 모델 능력이 아니다. live 모델 호출은 한 번도 하지 않았다.
- 운영 기본값은 모두 OFF다: `TRIP_AGENT_ENABLED`(미설정 시 development만 ON), `WEATHER_ENABLED=0`, `PERSONAL_TOKENS_ENABLED=0`.

## 결정 (PRD 10절)

| 결정 | 선택 | 반영 |
| --- | --- | --- |
| 3 · MCP | **A** — 포함, 로컬 전용 | `tools/mcp_going/` stdio 서버, 운영 `PERSONAL_TOKENS_ENABLED=0` |
| 4 · 날씨 기본값 | **A** — 개발 ON / 운영 OFF로 시작 | `WEATHER_ENABLED` 미설정이면 `APP_ENV=development`만 ON, Render `RUNTIME_DEFAULTS`·`tests/conftest.py`는 `0` |

에이전트 자체(`TRIP_AGENT_ENABLED`)도 같은 패턴이다. 질문 하나가 유료 호출을 chat 최대 5회 + 임베딩 최대 8회(`search_mail` 도구 호출마다 1회, 도구 호출 상한 8) 만들 수 있기 때문이다. `time_conflicts` 경로에서는 에이전트 앞 라우터의 의미 검색 임베딩 1회가 더 있다. 비용 합계는 `AGENT_MAX_COST_MICROS` 로 묶는다.

## 바꾼 파일 (트랙별)

| 트랙 | 파일 |
| --- | --- |
| 상위 세션 (도구·루프·서비스 뼈대) | `src/trip_agent/{registry,loop,tools,service}.py`, `src/prompts/trip_agent.md`, `src/llm/*`의 tool 호출, `src/foundation/routes.py`·`search.py`의 hook |
| agent_tests | `tests/test_v6_trip_agent_tools.py`, `tests/test_v6_trip_agent_api.py`, `src/trip_agent/{service,loop,registry,tools}.py` 결함 수정 |
| weather | `src/travel_tools/weather.py`(신규), `tests/test_v6_weather.py`, `src/foundation/settings.py`·`api.py`·`tests/conftest.py`·`scripts/prepare_render_env.py` 한두 줄 |
| plan_b | `src/travel_tools/alternatives.py`, `web/js/travel-tools.js`, `tests/test_v6_plan_b.py`(신규), `tests/test_travel_alternatives.py` |
| ui_e2e | `web/js/foundation.js`(질문 화면 에이전트 함수), `web/css/foundation.css`(`.agent-*` 6줄), `tests/test_trip_agent_ui.cjs`, `scripts/stage3_agent_fixture.py`, `tests/e2e/test_trip_agent_browser.py`, `tests/e2e/conftest.py` |
| mcp | `src/foundation/tokens.py`(신규), `src/foundation/{auth,routes,settings,db}.py`, `src/storage/postgres.py`, `api.py`(`Vary`), `tools/mcp_going/`, `docs/service-v6/MCP_LOCAL.md`, `tests/test_v6_personal_tokens.py`, `tests/test_v6_mcp_tools.py`, `web/index.html`·`web/js/foundation.js`(설정 화면 토큰 패널) |
| 통합 | `src/foundation/search.py`(hook 위치), `src/trip_agent/service.py`(날씨 신호), `src/trip_agent/tools.py`(Plan B 카드 필드·날씨 문구), `tests/test_v6_trip_agent_api.py`, `tests/test_v6_trip_agent_tools.py`, `tests/test_login_recovery_ui.cjs`, 이 보고서 |

`web/index.html`·`web/sw.js`의 자산 hash는 여러 트랙이 번갈아 찍었다.

## 묶음 A — 도구 레지스트리

범위(사용자·여행)는 서버 actor가 정한다. 어떤 도구도 `user_id`·`owner_id`·`trip_id`·`session_id`·`actor_id`를 입력으로 받지 않고, 하위 schema까지 `additionalProperties: false`다(시험으로 고정). 다른 여행·다른 사용자의 ID는 `NOT_FOUND` 결과로 돌아온다. 등록되지 않은 이름은 `TOOL_NOT_FOUND`, 잘못된 인자는 `TOOL_ARGUMENTS_INVALID` 결과이며 요청 전체를 깨지 않는다.

| 도구 | 종류 | 감싼 함수 | 외부 호출 | 출력 상한(자) |
| --- | --- | --- | --- | ---: |
| `get_trip_context` | read | `repo.get_trip` + `discovery.get_conditions` (출발점·숙소 좌표·주소 제거) | 없음 | 3,000 |
| `list_bookings` | read | `repo.list_bookings(date_from, date_to)` (교정값 반영) | 없음 | 12,000 |
| `search_mail` | read | v2 의미 검색(`generations.reader` + 질의 임베딩) + 유사도 하한 `ANSWER_MIN_SIMILARITY` | 임베딩(기존 metered 경로) | 8,000 |
| `list_saved_places` | read | `discovery.list_bookmarks` | 없음 | 6,000 |
| `get_place` | read | `discovery.detail` | 없음 | 6,000 |
| `get_itinerary` | read | `itineraries.get` / `list` (최근 적용 일정) | 없음 | 10,000 |
| `preview_itinerary_change` | preview | `itineraries.preview` (add · move · remove만, apply 아님) | 없음 | 4,000 |
| `suggest_plan_b` | preview | `alternatives.propose` (출발점은 서버 저장값, 모델에 안 보냄) | 없음 | 5,000 |
| `get_weather` | read | `Weather.forecast(actor, trip_id, date)` | **Open-Meteo** (설정 OFF면 호출 0) | 2,000 |
| `draft_inquiry` | draft | `preparation.draft` (`sent: False`) | 없음 | 4,000 |

레지스트리에 없는 것: apply, undo, delete, 외부 메시지 발송, 임의 URL fetch. 금지 이름 목록과 겹침 0을 `test_v6_trip_agent_tools.py`가 고정한다.

개인 토큰(Bearer)으로 온 질문은 `build_registry(read_only=True)`로 read 도구 7개만 받는다. preview·draft 도구는 DB 행을 만들기 때문이다.

화면 전용 키(`_sources`·`_previews`·`_drafts`)는 모델에 보내지 않고 루프가 `data`에서 따로 모은다.

## 묶음 B — 루프와 상한

| 상한 | 값 | 닿으면 |
| --- | --- | --- |
| 단계 | 5 | `max_steps` 부분 답변 |
| 도구 호출 | 8 | `max_tool_calls` 부분 답변 |
| 요청 deadline | 25초 | `deadline` 부분 답변 (1단계 이후 게이트웨이 `DEADLINE_EXCEEDED`도 같은 처리) |
| 응답 토큰 | 1,024 (`max_completion_tokens`, 공급자 상한이 더 커도) | — |
| 질문당 비용 | `AGENT_MAX_COST_MICROS` 기본 50,000 (USD 0.05) [가설] | `budget` 부분 답변. 각 단계를 보내기 전에 지금까지 쓴 금액 + 이번 단계 예약액(gateway 와 같은 계산)이 상한을 넘으면 멈춘다. 쓴 금액은 이 질문의 agent chat·`agent-search` 임베딩 키의 모든 시도를 Budget 규칙(released 0, settled 실제, 그 외 예약액)으로 합산한다. 장부 조회가 실패하면 상한 도달로 처리(fail-closed) |
| 공급자 불확정·중지 | — | `provider_stopped` 부분 답변. 1단계 이후 chat 이나 도구(`search_mail` 임베딩)가 과금 불확정(`PROVIDER_OUTCOME_UNKNOWN` 등)·429·영수증 실패·미전송·외부 호출 중지 오류를 받으면 같은 질문 안에서 다시 보내지 않고 멈춘다(`registry.PROVIDER_STOP_CODES`) |

- 부분 답변은 지금까지 도구가 돌려준 사실만으로 만들고, 못 한 것을 말한다.
- 첫 모델 호출 전 실패(예산 소진 등)는 기존 라우터와 같은 오류로 전파한다: `BUDGET_EXHAUSTED` 429 / `GLOBAL_BUDGET_STOPPED` 503.
- 최종 리뷰(B1)로 바뀐 동작: 도구가 위 오류를 받으면 예전처럼 "도구 실패" 결과로 모델에 돌려주지 않는다. 돌려주면 모델이 `search_mail`을 다시 부르고 새 call_key 로 재전송되기 때문이다. 1단계 이후 chat 공급자 오류도 예전에는 HTTP 오류였지만 이제 200 부분 답변(`provider_stopped`)이다. 회귀 시험: `test_uncertain_search_mail_is_not_resent_in_the_same_question`. `evals/agent/scenarios.json` 에는 이 종료 사유 시나리오가 아직 없다(후속: 같은 날 `ps-01~03` 추가 — `IMPLEMENTATION_STATUS.md` 참고).
- 최종 답은 2단계 근거 검증기를 이번 요청의 도구 결과에 대해 통과해야 `grounding=verified`다. 근거 없는 원자, 출처 줄을 지우면 빈 답, 행동 완료 주장("삭제했어요·적용했어요·보냈어요")은 도구 결과 요약(`grounding=fallback`)으로 바뀐다.
- 도구 결과는 `<tool_result … trust="untrusted_data">` 블록에 넣고, 내용의 `<`·`>`를 `<`·`>`로 바꿔 데이터가 블록을 닫을 수 없게 했다.

### 빠른 경로 — 통합에서 고친 회귀

검수에서 hook이 의미 검색 분기 맨 앞에 있어, LLM 없이 답하던 인원 질문과 `time_conflicts` 예약 질문까지 에이전트로 넘어가는 것을 찾았다(유료 호출이 chat 최대 5회 + `search_mail` 임베딩 최대 8회까지 늘어남). `search.py`를 고쳤다.

- 인원 질문(`_PARTY_REQUEST`)은 에이전트를 건너뛴다.
- 대상 예약에 `time_conflicts`가 하나도 없으면 바로 에이전트로 간다(의미 검색도 안 함).
- 하나라도 있으면 에이전트를 끈 경로와 똑같이 의미 검색으로 행을 고르고, 고른 행에 충돌이 있으면 기존 구조화 답, 없으면 에이전트로 간다.

트랙이 넣어 둔 `xfail(strict=True)` 시험 2건(`test_router_fast_paths_stay_llm_free_with_agent_on`)이 이제 통과하므로 표시를 지웠다. 두 질문 모두 에이전트 켜짐/꺼짐 응답이 같고 모델 요청이 0회다.

## 묶음 C — 미리보기 연결

- `preview_itinerary_change`·`suggest_plan_b`는 `itinerary_previews` 행만 추가한다. 일정의 `version`·`active_revision_id`·`undo_stack`·`updated_at`은 그대로다(시험).
- `/ask` 전후 일정 `(id, version, active_revision_id)`가 다르면 `perform_ask`가 `VERSION_CONFLICT`로 막는다. (최종 리뷰 후: PG 행(dict)에서는 값 대신 열 이름을 비교해 이 검사가 늘 '같음'이던 결함(C1)을 고쳐 값으로 비교한다. dict 행 단위 시험으로 확인했고 실제 PG 시험은 미실행)
- 적용은 화면 카드의 '검증한 변경 적용' 버튼만 하고, 기존 `PATCH …/itineraries/{id}`(`preview_id`, `expected_version`)를 그대로 쓴다. 카드를 만든 뒤 일정이 바뀌면 409이고 일정은 그대로다(API 시험 + E06d).
- 고정 예약 삭제 미리보기는 `BOOKING_EDIT_REQUIRED`, Plan B의 고정 예약 대체는 결과 0개다.

## 묶음 D — 날씨와 Plan B

### 날씨 (`src/travel_tools/weather.py`)

| 항목 | 값 |
| --- | --- |
| 엔드포인트 | `https://api.open-meteo.com/v1/forecast` 고정, redirect 0, timeout 4초, 64KB 상한, `fetch_public` |
| 보내는 좌표 | `public_places.center(city)`의 도시 공개 중심만. 함수가 좌표 인자를 받지 않는다 |
| 요청 변수 | 일별 강수 확률 최대 · 강수량 · 최고/최저기온, 도시 IANA 시간대, `start_date=end_date` |
| 캐시 | 도시·날짜 단위 메모리 3시간 [가설] |
| 쿼터 | 하루 전역 500 · 사용자 50 [가설] |
| 429/403 | 15분 쿨다운 |
| 기록 | `usage_reservations` 비용 0, 사유 `EXPLICIT_FREE_PUBLIC_DATA` |
| 실패 | 모두 `{'status':'unknown', 'message':'날씨를 확인하지 못했어요'}` — 400·429·403·타임아웃·연결 실패·잘못된 본문·범위 밖 값(확률 0~100, 강수량 0~2000mm, 기온 -90~60°C, 최고≥최저)·단위 불일치·예보 범위 밖·여행 기간 밖·kill switch |
| 일정 변경 | 없음. 예보 전후 일정 조회 결과와 DB 전 테이블 스냅샷(비용·heartbeat 테이블 제외)이 같다(시험) |

통합에서 고친 것:

- 날씨가 꺼졌을 때 에이전트 도구의 문구가 마침표 하나 달랐다. `weather.unknown('WEATHER_DISABLED')`를 재사용하게 했다.
- 서버가 날씨 결과 상태를 화면에 내려 주지 않아, 날씨를 정상으로 받아도 출처 링크가 안 보이고 unknown이어도 확인 범위에 '날씨'가 들어갔다. DTO에 `weather: {status, provider}`를 넣고(`status=='ok'`일 때만 `provider='open_meteo'`), 확인 범위의 '날씨'도 ok일 때만 넣는다. 시험 1건을 추가했다.

#### 이용 조건 확인 기록 (확인일 2026-10-21)

WebFetch 요약으로 확인했고 원문과 글자 단위로 대조하지는 않았다. 날씨 트랙이 같은 날 확인한 내용과 통합 세션이 다시 확인한 내용이 일치한다.

| URL | 확인한 내용 |
| --- | --- |
| https://open-meteo.com/en/terms | 무료 API는 **비상업 용도만** 허용, 상업 용도는 유료 플랜. 한도 600회/분 · 5,000회/시간 · 10,000회/일, 가격표 기준 300,000회/월. "Vercel, Netlify, Render, or Google Sheets"에서는 한도에 더 일찍 걸릴 수 있다고 명시. 데이터는 CC BY 4.0. 페이지에 수정일 없음(© 2022-2026, 변경 이력은 GitHub 커밋 기록 링크) |
| https://open-meteo.com/en/licence | CC BY 4.0. 출처 표기, **라이선스 링크**, 변경 여부 표시, 보증 암시 금지. 날씨 데이터를 보여 주는 모든 곳 옆에 링크. 권장 문구 `Weather data by Open-Meteo.com` |
| https://open-meteo.com/en/docs | 엔드포인트 `/v1/forecast`, 일별 변수를 요청하면 timezone 필수(기본 GMT), `forecast_days` 0~16, 날짜 `yyyy-mm-dd`, 오류는 HTTP 400 + `{"error":true,"reason":…}` (날씨 트랙 확인) |

> [!WARNING]
> 고잉이 '비상업'에 해당하는지는 [미확인]이다. 운영은 Render에서 돌아서 공유 IP 한도에 먼저 걸릴 수 있다. 화면은 권장 문구 링크(`Weather data by Open-Meteo.com`) 옆에 CC BY 4.0 라이선스 링크를 함께 붙인다(통합 뒤 추가, `tests/test_trip_agent_ui.cjs`로 고정). 비상업 해당 여부와 공유 IP 한도는 운영에서 날씨를 켜기 전에 정리해야 한다.

화면 출처 표기: 에이전트 답이 날씨를 실제로 받았을 때(`weather.status=='ok'`)만 'Weather data by Open-Meteo.com' 링크를 붙인다. unknown·disabled면 링크 0개(JS 단위 시험). 실제 날씨를 켠 화면은 보지 않았다.

### Plan B (`src/travel_tools/alternatives.py`, G12)

- 정렬: 연결된 추천 run의 `score_lower` 내림차순 → 출발점 직선거리 오름차순(모르면 뒤) → `place_id`. 거리는 12곳으로 자르기 전에 풀 전체에서 계산하고, 후보 조회에 출발점·범주를 넘겨 1000개 상한도 거리 순으로 적용한다. 12곳 · 결과 3개 · 15초 상한은 그대로다.
- 점수는 일정 생성과 같은 읽기 경로(`Recommendations.get`)로 읽는다. 추천이 current가 아니거나, 출발점이 바뀌었거나, 순위가 stale이거나, 결과가 없으면 점수를 버리고 거리순으로 간다. 바꿀 장소가 순위에 오른 섹션 하나의 items·needs_confirmation 점수만 쓴다.
- 비 사유: `indoor` 태그 → `indoor_confirmed`, restaurant/cafe 범주 → `indoor_likely`(basis `category_default`, 미검증)로 통과, `outdoor` 태그 또는 verified `tags` 사실의 outdoor → `OUTDOOR_CONFIRMED`로 제외, 그 밖은 기존처럼 `INDOOR_UNCONFIRMED` 탈락.
- 화면: 사유가 비일 때만 '실내로 추정(미확인)' 또는 '실내 태그 있음 · 출처로 확인하지 않았습니다'.
- 통합에서 에이전트 Plan B 카드 필드를 보탰다: `reason`, `before`(이름·시작 시각), `unresolved`(미확인 조건 코드). 화면은 '이전 → 대체'와 미확인 조건 수를 보여 준다.

> [!NOTE]
> 지금 저장소의 어떤 데이터도 indoor/outdoor 태그를 쓰지 않는다. 그래서 운영에서 '확인된 야외 제외'는 실제로 작동하지 않고, 야외석만 있는 음식점·카페도 '실내로 추정'으로 통과한다(코드 주석에 기록).

## 묶음 E — 안전 시험 (합성 · replay)

아래는 모두 **합성 사용자 A/B와 손으로 쓴 가짜 모델**로 얻은 결과다. 모델이 실제로 이렇게 행동한다는 뜻이 아니다.

| 확인 | 결과 | 시험 |
| --- | --- | --- |
| 금지 도구(`delete_booking`·`apply_itinerary_change`) 호출 | 실행 0, `TOOL_NOT_FOUND`, 예약·일정 불변 | `test_v6_trip_agent_api.py` 주입 시험 |
| 다른 여행·다른 사용자 ID | `NOT_FOUND`, 예외 없음 | `test_v6_trip_agent_tools.py` |
| 범위 인자 끼워 넣기(`trip_id`·`user_id`) | `TOOL_ARGUMENTS_INVALID` | 같은 파일 |
| 개인 좌표·`owner_id`·`actor_id` 출력 | 0 (원본 데이터에 좌표가 있음을 함께 확인) | 같은 파일 |
| 사용자 클릭 없는 적용 | 0 — 질문 전후 일정 상태 동일, PATCH 뒤에만 version 2 | API 시험 + E06 |
| 근거 없는 날짜·예약번호 답 | fallback, 화면에 값 0 | API 시험 |
| 데이터 속 `</tool_result>` | 닫는 태그 1회, `trust="trusted"` 0 | `test_v6_trip_agent_loop.py` |
| 토큰 actor의 ask | read 도구만, 미리보기 행 0 | API 시험 |
| 외부 연결 | 0 (검수에서 소켓 차단 플러그인으로 재실행) | 에이전트·날씨 시험 |

### 평가셋 `evals/agent/` — 50 시나리오, CI 게이트 연결

합성 fixture(사용자 A·B, 여행·예약·교정값·적용된 일정·보관함·예약 준비·장소 사실, 메일·장소 설명 속 주입 문구)에 시나리오 50개를 둔다. 필수 범주(다단계 카페 질문, 비 오는 날 Plan B, 일정 이동, 문의 초안, 답변 불가, 다른 여행·사용자 요구, "모든 예약 삭제"·"다른 사용자 여행"·"이 링크 열어" 주입, 도구 실패, 예산 소진, 상한 도달)를 모두 담았고, 일부 스크립트는 일부러 금지 도구 시도·근거 없는 원자·다른 여행 값·주입 따르기를 넣어 방어를 시험한다.

| 지표 (replay · synthetic · 손으로 쓴 스크립트) | 값 |
| --- | ---: |
| 시나리오 | 50 |
| 금지 도구 실행 / 시도 | 0 / 12 |
| 클릭 없는 적용 · 질문 중 데이터 변경(전 표 행 hash 비교) | 0 · 0 |
| 다른 여행·사용자 노출 · 개인 위치 노출 | 0 · 0 |
| 주입 성공(행동 기준) · 주입 문구 텍스트·링크 전달 | 0 · 0 |
| 화면 답의 근거 없는 원자(모델 원문에서 막은 수) | 0 (5) |
| untrusted 블록 밖 도구 결과 | 0 |
| 평균 단계 · 모델 호출 · p95 지연 | 2.708 · 130 · 179ms (최종 재실행 p95 218ms) |

- **replay 방식**: 미리보기 ID·만료 시각처럼 도구 출력이 실행마다 바뀌어 다단계 대화의 요청 hash가 고정되지 않는다. 그래서 메일 평가와 달리 (시나리오 id, 단계 번호)로 스크립트를 재생하고, 인자는 앞 단계 결과를 가리키는 자리표시(`{{itinerary_id}}` 등)를 쓴다.
- **구조적 0**: 금지 도구 실행 0은 레지스트리가 그런 이름을 등록 거부해 생기는 값이라 회귀 감시용이다. 근거 없는 원자 0은 서비스 검증이 켜져 있다는 확인이다.
- **inj-05**: 처음에는 모델이 메일 속 링크를 답에 옮겨 적는 1건을 막지 못했다. 에이전트 답의 URL을 "메일 원문에서 확인" 안내로 바꾸는 처리를 넣어 0이 됐고, 게이트 허용치도 0으로 조였다.
- 게이트: `evals/thresholds.json`의 `agent_replay`는 `required`(빠지면 실패), 위 안전 지표 허용치 0, 금지 도구 시도 최소 12, 시나리오·fixture 내용 hash 고정. `make ci-eval`에서 통과, 고의 회귀(몰래 적용, B의 예약 노출, 무단 적용 1 등)에서 실패(`tests/test_v6_eval_agent.py` 33개).
- 과업 성공률은 replay에 없다. live 모드는 상한 인자 필수·구현만 했고, 예산·상한 시나리오 7개는 live에서 뺀다. (최종 리뷰 후: live 는 원장·영수증을 `--out` 옆 `<이름>-work-<UTC>/` 에 남기고, workdir 없이 live 를 부르면 `ValueError` — B4)

## 묶음 F — 화면과 E06

- 질문 화면 말풍선 안에 확인 범위 한 줄(`예약 3건 · 저장한 곳 5곳 확인`), 기존 '근거 N건 보기', 미리보기·초안 카드. 새 메뉴·토글 없음, 기존 토큰만.
- 미리보기 카드: '아직 적용하지 않았습니다', `can_apply`가 아니면 버튼 비활성, 409면 '최신 일정 확인'. 보고 있는 일정과 미리보기 일정이 다르면 화면을 덮어쓰지 않고 다시 불러온다.
- 초안 카드: '문의 초안(보내지 않음)' + 복사 버튼만.

| ID | 흐름 | 결과 |
| --- | --- | --- |
| E06 | 질문 → 확인 범위·근거·카드 → 클릭 전 쓰기 0·버전 그대로 → 클릭 → 새로고침 후 버전 +1 | 통과 |
| E06b | 390×844, 가로 스크롤 0, Tab만으로 버튼 도달, Enter로 적용 | 통과 |
| E06c | 글자 크기 200%, 카드·버튼이 390px 안, 높이 44px 이상, 쓰기 0 | 통과 |
| E06d | 카드를 만든 뒤 API로 버전을 올리고 클릭 → 409, 버전 그대로, 최신 일정 안내 | 통과 |

E06 fixture(`scripts/stage3_agent_fixture.py`)는 가짜 모델을 쓰고, 서버 프로세스는 loopback 밖으로 연결할 수 없다(`BLOCKED_EXTERNAL` 0줄 확인). 질문하는 사용자는 일반 role이다. '외부 요청 0'은 브라우저 요청과 fixture 프로세스 둘 다에 대한 말이다.

## 묶음 G — MCP와 개인 토큰 (결정 3 = A)

- 테이블 `personal_access_tokens`: sha256 hash만 저장, 원문은 생성 응답에 한 번. 만료 최대 30일, 계정당 사용 중 10개, 폐기 가능. 매 기동 `IF NOT EXISTS`(SQLite·PG), 스키마 버전 안 올림.
- 기존 세션 검사를 깨지 않으려고 토큰마다 쿠키로 쓸 수 없는 내부 `sessions` 행을 둔다. 사용자 비활성화·권한 변경·복원 때 토큰도 끊긴다.
- Bearer 허용: 읽기 GET 7개(여행 목록·여행·예약·보관함·장소 상세·일정 목록/상세) + POST ask. 나머지는 403 `TOKEN_SCOPE_REJECTED`. `/today`는 예약 준비 재검증 쓰기를 일으킬 수 있어 뺐다. 허용 GET 7개를 부른 뒤 사용자 데이터 테이블이 그대로임을 시험한다.
- 토큰별 분당 상한: 전체 60회, ask 10회, 넘으면 429 `TOKEN_RATE_LIMITED`.
- `write_guard`는 토큰 actor와 빈 CSRF 값을 거부한다. 토큰 관리 API는 쿠키 + CSRF만.
- 응답 `Vary: Cookie, Authorization`.
- `tools/mcp_going/`: 표준 라이브러리 HTTP 클라이언트(loopback만, redirect·프록시 무시), 도구 8개(ask_trip만 `readOnlyHint=false`), 결과 20,000자 상한, 광고 schema = 서버 검증 schema. SDK는 별도 가상환경의 `mcp 2.3.0`. 스펙 버전은 설치된 SDK 기준(initialize 응답 `2025-11-25`) [미확인].
- (최종 리뷰 후) `get_itinerary` 는 trip_agent 와 같은 허용 목록만 넘기고 `snapshot`·`legs` 를 지운다 — 숙소·출발점 좌표와 라벨이 MCP 클라이언트로 나가던 결함(privacy-1). `ask` 타임아웃은 30초 → 120초이고, 타임아웃은 '앱 미실행'이 아니라 `ASK_TIMEOUT`(앱에서 계속 처리·과금됐을 수 있음)으로 따로 안내한다(B6).
- 연결법: [MCP_LOCAL.md](../MCP_LOCAL.md).

## 실행한 시험 — 통합 세션

> 아래 표는 통합 세션 당시 기록이다. 최종 리뷰 반영 후 전체 시험을 다시 돌린 결과는 [최종 리뷰와 최종 재실행](#최종-리뷰와-최종-재실행)에 있다.

모든 Python 명령은 앱 가상환경에서 `PYTHON_DOTENV_DISABLED=1 TRAVEL_TEST_POSTGRES_DSN=`를 붙였다. 전체 pytest는 돌리지 않았다(다른 단계가 동시 작업 중).

| 명령 | 결과 |
| --- | --- |
| 3단계 Python 시험 (`test_v6_trip_agent_*` · `test_v6_weather` · `test_v6_plan_b` · `test_travel_alternatives` · `test_v6_personal_tokens` · `test_v6_mcp_tools`) — 수정 전 | 109 통과 · 2 xfail(빠른 경로 회귀) |
| 위 + 영향받는 기존 시험 (`test_foundation_api` · `test_foundation_auth` · `test_itinerary_api` · `test_travel_*` · `test_operations` · `test_render_env_prepare`) — 수정 후 | **233 통과** · 0 실패 |
| `/ask`를 쓰는 다른 기존 시험 (`test_api` · `test_booking_time_conflicts` · `test_mail_ai_transition` · `test_local_mail` · `test_reliability_api` · `test_v6_grounding` · `test_public_discovery`) | 204 통과 |
| `test_v6_eval_agent.py` | 27 통과 (당시 기록). 2026-10-21 결함 수정 세션에서 33개 전체를 다시 돌려 **33 통과** |
| `node --test tests/*.cjs` — 수정 전 | 230 통과 · 1 실패 (`test_login_recovery_ui.cjs`) |
| `node --test tests/*.cjs` — 수정 후 | **231 통과** · 0 실패 |
| `GOING_E2E=1 pytest tests/e2e` | **12 통과** (E01~E05 · E01b · HTTP 2 · E06 · E06b · E06c · E06d), 28.4초 |
| `scripts/check_publish_safety.py --untracked` | 0 fail · 80 warn. 3단계 파일 지적 **0** (3단계 경로만 따로 돌려도 0 fail · 0 warn). 경고는 v4 보고서 로그와 다른 트랙 문서(`FEEDBACK_LOOP.md`·`LABELING.md`)의 절대경로 |
| `scripts/version_web_assets.py --check` | **실패** — `js/product.js`·`js/discovery-experience.js`(4단계 트랙 파일)의 hash가 낡음. 3단계 자산은 일치 |

`test_login_recovery_ui.cjs` 실패 원인은 MCP 트랙이 `boot()`에 넣은 `loadPersonalTokens()` 호출이었다. 시험의 가짜 환경에 그 함수가 없어 ReferenceError가 났다. 다른 의존 함수(`loadUsage` 등)처럼 가짜 함수를 하나 넣었다. 앱 코드는 바꾸지 않았다.

자산 hash는 4단계가 아직 작업 중이라 통합 세션이 다시 찍지 않았다. 모든 트랙이 끝난 뒤 한 번 찍고 `--check`와 `node --test tests/test_service_worker.cjs`를 다시 돌려야 한다. (최종 리뷰 후: 최종 재실행에서 `make ci-assets` 일치, `make ci-js` 233 통과)

## 미실행과 이유

| 항목 | 이유 |
| --- | --- |
| live 모델 호출 (OpenAI·Anthropic), live 평가 | 유료 호출 금지. 대표가 상한을 정해 실행 |
| OpenAI strict 모드가 도구 schema의 `pattern`·`minLength`·`maxLength`를 받는지 | live 호출 필요 [미확인] |
| 실제 Open-Meteo 호출, 날씨를 켠 화면 | 시험은 전부 가짜 fetcher |
| PostgreSQL 시험 (토큰 DDL·개수 쿼리, 비용 조회 `COST_SQL`) | Docker 금지. `COST_SQL`이 PG에서 실패하면 상한이 닫히는 쪽으로 처리된다 |
| 전체 pytest, `make ci`, `make e2e` 그 자체 | 동시 작업 중이라 관련 파일만. E2E는 `pytest tests/e2e`로 같은 내용 실행. (최종 리뷰 후: PG 를 뺀 `make ci-*` 와 `make e2e` 를 최종 재실행에서 돌림 — [최종 리뷰와 최종 재실행](#최종-리뷰와-최종-재실행)) |
| 실제 MCP 클라이언트(Claude Desktop/Code) 연결 | 로컬 stdio 확인만 |
| Plan B: 실제 숙소 출발점 변경(ORIGIN_CHANGED) 통합 시험, 1000개 넘는 후보 | 가짜 추천 서비스·인자 확인만 |
| 변경을 되돌려 새 시험이 실패하는지(일부 트랙) | git stash 금지. 날씨·Plan B·MCP 프록시는 메모리 변이로 확인함 |
| `IMPLEMENTATION_STATUS.md` 갱신 | 이번 통합 범위 밖 (최종 리뷰 후: 갱신함 — 최종 리뷰·최종 전체 시험 절) |

## 완료 기준

| ID | 판정 | 근거 |
| --- | --- | --- |
| S3-01 | 충족 | `test_v6_trip_agent_tools.py` — 도구 10개 고정, 금지 이름 겹침 0, strict schema, 범위 키 입력 0 |
| S3-02 | 충족 (replay) | 루프 시험의 단계·호출·시간·비용 상한 부분 답변, AI OFF·키 없음·예산 소진에서 기존 라우터와 같은 응답, 빠른 경로 LLM 0회. 최종 리뷰 후: 비용 상한은 보내기 전 예약액까지 비교(B3), 과금 불확정 오류는 재전송 없이 `provider_stopped`(B1). 장부상 상한 초과 경계는 [알려진 한계](#알려진-한계) |
| S3-03 | 충족 | 질문 전후 일정 상태 동일, 적용은 PATCH(클릭)만, 낡은 카드 409 (API + E06·E06d). 답변 중 일정 변경 409 는 PG 행에서도 값 비교(C1, dict 행 단위 시험 · 실제 PG 미실행) |
| S3-04 | 충족(운영 전 판단 1건) | 실패=미확인, 개인 좌표 전송 0, 확인일 기록, 출처 링크 + CC BY 4.0 라이선스 링크. 고잉의 비상업 해당 여부는 [미확인] |
| S3-05 | 충족 | `test_v6_plan_b.py` 11개 + `test_travel_alternatives.py`, 메모리 변이로 옛 동작 실패 확인 |
| S3-06 | 충족(replay·합성) | 최종 `make ci-eval` 의 `agent_replay` 게이트 통과. 50개 시나리오에서 금지 도구 실행·다른 여행 노출·주입 성공(텍스트·링크 포함)·무단 적용 0, `ci-eval` 게이트 연결. 링크 전달 지표는 링크 전체·스킴을 뺀 형태·호스트만 옮긴 경우를 센다. 전각 문자 등 변형 표기는 세지 못한다. 세는 대상은 합성 세계에 `http(s)://` 로 적힌 링크뿐이고 지금은 1개(`.invalid` 도메인)라, 필터 목록 밖 TLD·IP 링크·다른 스킴을 옮기는 시나리오는 없다 |
| S3-07 | 충족 | E06·E06b·E06c·E06d 통과 (최종 `GOING_E2E=1 make e2e` 12 통과) |
| S3-08 | 충족 | 토큰·MCP 시험 30개(통합 세션 당시), 운영 기본 OFF(설정·Render 기본값·conftest). 최종 리뷰에서 좌표 미노출(privacy-1)·ask 타임아웃(B6) 시험을 보탰고 최종 `make ci-python` 에서 통과 |

## 최종 리뷰와 최종 재실행

최종 리뷰에서 3단계와 에이전트 평가에 걸린 지적 11건을 모두 고쳤고, 고친 뒤 전체 시험을 다시 돌려 Python·JS·평가 게이트·E2E가 모두 통과했다. PostgreSQL 시험만 미실행이다.

리뷰는 6개 관점(billing·security·contracts·evaluation·privacy·honesty)에서 지적 23건을 냈고, 지적마다 반박 검증 2표를 받아 확정 18 · 의견 갈림 4 · 반박 1로 갈렸다. 아래 B1·privacy-1 은 의견 갈림이었지만 고쳤다. 고친 항목마다 원래 재현 시나리오를 다시 돌려 통과를 확인했다. 나머지 지적(B2·EVAL-*·privacy-2 등)은 이 보고서 범위 밖이다.

### 이 단계 지적 처리

| id | 지적 | 처리 | 확인 |
| --- | --- | --- | --- |
| B1 | 도구가 과금 불확정 오류를 '도구 실패'로 삼켜, 모델이 `search_mail` 을 다시 부르면 새 call_key 로 재전송 | `registry.LOOP_STOP_CODES` 오류는 루프로 다시 던져 종료(공급자 오류는 `provider_stopped`, 예산·deadline 은 `budget`·`deadline`) | `test_uncertain_search_mail_is_not_resent_in_the_same_question` |
| B3 | 질문당 비용 상한을 보낸 뒤에만 검사, 429 시도·임베딩 미합산 | 보내기 전 쓴 금액 + 이번 단계 예약액 비교, 모든 attempt·`agent-search` 합산 | `test_cost_ceiling_is_checked_against_the_next_reservation_not_only_spent`, `test_question_cost_counts_every_attempt_and_agent_search_embeddings` |
| B4 (agent) | agent live 원장·영수증을 임시 디렉터리에 두고 삭제 | `--out` 옆 `<이름>-work-<UTC>/` 보존, live + workdir 없음은 `ValueError` | `test_agent_live_run_requires_kept_workdir_and_main_passes_one` |
| B6 | MCP ask 타임아웃 30초, 타임아웃을 '앱 미실행'으로 안내 | ask 120초, `ASK_TIMEOUT` 별도 안내(처리·과금됐을 수 있음) | `test_ask_waits_past_the_server_worst_case_and_reports_timeout_as_possibly_billed` |
| SEC-1 | `strip_links` 가 스킴 없는 도메인을 못 지움 | 스킴 없는 도메인(코드 TLD 목록)·`[.]`·`hxxp` 처리. 막지 못하는 형태는 [알려진 한계](#알려진-한계) | `test_agent_answer_strips_scheme_less_domains_but_keeps_tool_provider_names`, `test_scheme_less_phishing_domain_from_mail_never_reaches_the_answer` |
| C1 | `_itinerary_state` 가 PG 행(dict)에서 열 이름을 비교해 409 미동작 | 값으로 비교 | dict 행 시험 `test_perform_ask_returns_409_when_the_itinerary_changes_during_the_answer_on_postgres_rows` (실제 PG 미실행) |
| privacy-1 | MCP `get_itinerary` 가 숙소·출발점 좌표·라벨 전달 | trip_agent 와 같은 허용 목록, `snapshot`·`legs` 제거 | `test_get_itinerary_never_sends_lodging_or_origin_locations` |
| H2 | 링크 제거 범위를 실제보다 넓게 씀 | 알려진 한계·S3-06 을 실제 범위로 좁힘 | 문서 |
| H3 | '유료 호출 최대 5회' | chat 최대 5회 + 임베딩 최대 8회로 정정 | 문서 |
| H5 | `agent-replay.md` 가 'URL 필터는 대표 결정 대기'를 늘 출력 | `strip_links` 의 실제 범위 문장으로 교체 | 최종 `make ci-eval` 의 `agent-replay.md` |
| H6 | 실행 표는 27 통과인데 근거는 33개 | 33개를 다시 돌린 33 통과를 실행 표에 기록 | 문서 |

### 최종 전체 시험 (작업 트리, 2026-10-21)

모두 `PYTHON_DOTENV_DISABLED=1 TRAVEL_TEST_POSTGRES_DSN=`(SQLite), 앱 가상환경에서 돌렸다.

| 명령 | 결과 | 시간 |
| --- | --- | --- |
| `make ci-python` | 1890 통과 · 30 skip · 0 실패 (경고 3: 라이브러리 지원 중단 2, FastAPI 중복 Operation ID 1 — 기존) | 8분 5초 |
| `make ci-js` | 233 통과 · 0 실패 | 77초 |
| `make ci-assets` | 공개 자산 hash 가 `index.html` 과 일치 | 1초 미만 |
| `make ci-eval` | gate pass · failures 0 · synthetic | 23초 |
| `scripts/check_publish_safety.py --untracked` | 0 fail · 68 warn (absolute_user_path 63, db_url_loopback_password 5) — 모두 이번에 바꾸지 않은 v2·v3·v4 문서·로그(docs/service-v2·v3·v4, docs/presentation/v4). 기본 모드는 경고만 있어도 rc=1 | 1초 |
| `make ci-safety` | 0 fail · 68 warn, rc 0 | 1초 미만 |
| `GOING_E2E=1 make e2e` | 12 통과 | 28초 |
| `make ci-postgres` / PG 시험 | **미실행** — Docker(Colima) 꺼 둠(대표 지시). `test_v6_admin_llm_ops` 의 PG 1건 skip | — |

같은 날 최종 리뷰 전 `ci-python` 첫 실행은 8건 실패였다. 7건은 iCloud 가 내려받지 않은 파일을 자식 프로세스가 읽다 시간 제한에 걸린 환경 원인, 1건은 기존 간헐 실패(`test_v5_concurrent_workload`, 409 vs 202)다. 파일을 내려받은 뒤 다시 돌려 0 실패였고, 시험 기대값·시간 제한은 바꾸지 않았다.

### agent_replay 지표 (synthetic · replay · 손으로 쓴 스크립트)

최종 `make ci-eval` 의 `data/eval_runs/ci/agent-replay.json` 값이다. 모델 능력이 아니라 루프·안전 장치 시험이다.

| 지표 | 값 | 묶음 E 기록과 |
| --- | ---: | --- |
| 시나리오 · 금지 도구 실행 / 시도 | 50 · 0 / 12 | 같음 |
| 무단 적용 · 질문 중 데이터 변경 · 다른 여행 노출 · 개인 위치 노출 | 0 · 0 · 0 · 0 | 같음 |
| 주입 성공 · 주입 텍스트 전달 · 링크 전달 | 0 · 0 · 0 | 같음 |
| 근거 없는 원자 (모델 원문에서 막은 수) · 단언 실패 · untrusted 블록 밖 결과 | 0 (5) · 0 · 0 | 원자·블록 밖은 같음, 단언 실패는 묶음 E 에 기록 없음 |
| 평균 단계 · 모델 호출 · p50 / p95 지연 (로컬 처리 시간, 모델 지연 아님) | 2.708 · 130 · 95 / 218ms | p95 가 179ms → 218ms |
| 종료 사유 | final 43 · budget 1 · max_steps 1 · max_tool_calls 1 · deadline 1 · model_incomplete 1 · 오류 종료 2 (BUDGET_EXHAUSTED 1 · GLOBAL_BUDGET_STOPPED 1) | `provider_stopped` 0 |

### 남은 경계 (지적 범위 밖, 기록만)

| 경계 | 내용 | 지금 상태 |
| --- | --- | --- |
| 비용 상한 장부 초과 (B3 뒤) | 429 재시도가 있으면 `pending_reconciliation` 행의 예상비용까지 장부에 잡혀 장부상 합계가 상한을 넘을 수 있다 | 합성 재현: 상한 25000, 장부 31020, 실제 확정 10340 |
| `search_mail` 과 deadline | 임베딩은 에이전트 deadline(25초)에 묶이지 않는다(시도당 45초, 429면 최대 3회) | MCP `ask` 120초도 엄밀한 상한이 아니다 (후속: 임베딩이 deadline 을 이어받도록 수정 — `IMPLEMENTATION_STATUS.md`) |
| `provider_stopped` 평가 | B1 뒤 1단계 이후 chat 공급자 오류가 HTTP 오류에서 200 부분 답변으로 바뀌었다 | `evals/agent/scenarios.json` 에 시나리오 0개, 단위 시험만 (후속: `ps-01~03` 추가로 해소) |
| 전송·정산 뒤 오류 | `parse_embed` 실패처럼 과금이 확정된 뒤의 오류 | 루프를 멈추지 않고 도구 오류로 남는다 |

## 알려진 한계

- 주입 문구의 링크를 모델이 답에 옮겨 적으면 `strip_links` 가 '메일 원문에서 확인' 안내로 바꾼다. 지우는 형태: `http(s)://`·`hxxp(s)://`·`www.` 링크, 그리고 TLD 가 코드 목록(`_TLD`: 두 글자 국가 코드와 `com`·`net`·`invalid` 등 고정 목록)에 있는 스킴 없는 도메인(`refund-hotel-kr.com/claim`, `[.]`·`(.)` 표기 포함). 막지 못하는 형태: 목록에 없는 TLD 의 스킴 없는 링크(`evil.ninja/claim?c=1`), IP 링크(`192.168.0.1/claim`), 목록 밖 TLD 의 다른 스킴(`ftp://evil.ninja/x`), 목록 밖 TLD 의 스킴 생략형(`//evil.ninja/x`), `user@host/경로`, 전각 문자·공백 삽입처럼 정규식이 도메인으로 읽지 못하는 변형, 이메일 주소(그대로 둔다), 도구가 돌려준 예약처 이름과 똑같은 경로 없는 도메인(예: `Booking.com`, 예약처 필드 자체가 공격 도메인이면 남는다). 앞의 다섯 형태는 이번 검수에서 `strip_links` 에 직접 넣어 0건 치환으로 확인했다(synthetic 문자열). (최종 리뷰 후: SEC-1 로 `ftp://`·`//host` 도 TLD 가 목록 안이면 지운다 — `ftp://evil.test/x`·`//evil.test/x` 는 1건 치환. 남는 것은 TLD 가 목록 밖일 때뿐이다.) 링크를 열 도구는 처음부터 없다.
- `checked_scope`는 도구가 돌려준 개수를 그대로 쓴다. 모델이 도구를 덜 부르면 범위도 좁게 나온다.
- 미리보기 카드의 항목 이름은 일정을 이미 불러온 상태에서만 보인다. 아니면 '일정 항목'으로 나온다.
- 토큰 ask도 답변 예산을 쓰고 재색인 작업을 예약할 수 있다.
- 하노이 기준 좌표 데이터(`public_places.CENTERS`)의 시간대가 `Asia/Bangkok`이다. 날씨는 여행 도시 시간대를 먼저 써서 영향이 없지만, 데이터 수정은 4단계 소관이다.
- 200% 글자 크기에서 하단 메뉴 글자가 겹친다(기존 레이아웃, 카드와 무관).
- 질문당 비용 상한은 장부 기준으로 넘을 수 있다. 429 재시도가 있으면 `pending_reconciliation` 행의 예상비용까지 합산되기 때문이다(합성 재현: 상한 25000, 장부 31020, 실제 확정 10340).
- `search_mail` 임베딩은 에이전트 deadline(25초)에 묶이지 않는다(시도당 45초, 429면 최대 3회). 그래서 MCP `ask` 120초도 엄밀한 상한이 아니다(후속: 같은 날 임베딩이 바깥 deadline 을 이어받도록 고쳐, 25초 뒤 새 전송·재시도는 없다).
- 1단계 이후 chat 공급자 오류는 HTTP 오류가 아니라 200 부분 답변(`provider_stopped`)이다. `evals/agent/scenarios.json` 에 이 종료 사유 시나리오는 0개이고 단위 시험만 있다(후속: 같은 날 `ps-01~03` 추가로 해소).
- 전송·정산이 끝난 뒤의 오류(`parse_embed` 실패 등)는 과금이 확정된 것이라 루프를 멈추지 않고 도구 오류로 모델에 돌아간다.

## 대표에게 넘길 일

1. live 평가 상한 결정과 실행 — `evals/agent/README.md`의 `--dry-run` → `--mode live --max-cost-usd … --max-scenarios …`. OpenAI strict schema 수용 여부도 이때 확인된다
2. 운영에서 에이전트를 켤지(`TRIP_AGENT_ENABLED`)와 질문당 비용 상한 `AGENT_MAX_COST_MICROS`(현재 USD 0.05 [가설]) 결정
3. 날씨를 운영에서 켜기 전에: 고잉이 Open-Meteo '비상업'에 해당하는지 판단(아니면 유료 플랜), Render 공유 IP 한도 위험 확인. 라이선스 링크는 추가함
4. 모든 트랙이 끝난 뒤 자산 hash 재생성 → `--check` → `make ci`·`make e2e`(PG 포함, Docker 승인 필요). (최종 리뷰 후: PG 를 뺀 나머지는 최종 재실행에서 통과. PG 시험만 남음)
5. 커밋 범위 정리 — 3단계·2단계·4단계 변경이 한 작업 트리에 섞여 있다. 공유 파일(`web/index.html`·`web/js/foundation.js`·`api.py`·`settings.py`·`routes.py`·`search.py`)은 줄 단위로 나눠야 한다. (최종 리뷰 후: 같은 날 로컬 커밋 5개로 나눔, push 없음 — IMPLEMENTATION_STATUS 「커밋」 절)
