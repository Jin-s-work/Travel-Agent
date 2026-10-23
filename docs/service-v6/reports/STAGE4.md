# 4단계 보고서 — 추천 평가 루프: 무료 후보 보강 · 라벨 도구 · 피드백 루프

> v6 4단계 프롬프트(`prompts/04-recommendation-eval-loop.md`)를 실행한 기록이다. 묶음 A·B·D·E가 구현 → 검수 → 수정을 거쳤고, 통합 세션이 관련 시험을 다시 돌렸다. 최종 리뷰 뒤 전체 시험을 한 번 더 돌렸다('최종 리뷰와 최종 재실행'). 묶음 C(평가 실행과 결정)는 사람 라벨이 없어 시작하지 않았다. 바꾼 파일, 실제로 돌린 명령과 결과, 돌리지 못한 것, 대표에게 넘길 일을 적는다.

2026-10-21 · Claude Code 세션 · **이 보고서 작성 시점(2026-10-21) 미커밋(작업 트리에만 있음, HEAD 5a4443a 그대로)** · 같은 날 로컬 커밋 5개로 나눔(코드 4 + 문서 1, push 없음 — 목록은 IMPLEMENTATION_STATUS 「커밋」 절) · push·배포·패키지 설치 없음 · 운영 DB 읽기 0 · 유료 호출 0 · 공개 지도 실호출 0

**세 줄 요약**

- 상태는 **라벨 대기**다. 사람 라벨 0건, 평가표는 **라벨 전 미측정**, v5/v6 결정은 **라벨 후**다. 기본 추천 모델은 hybrid_v5 그대로이고 `evaluation_v5.py`·`hybrid_v5.py`·`registry.py` diff는 0이다.
- 코드로 만든 것: 볼거리 범주(일반 참고 구획 전용)와 OSM 영업시간 파서(provisional), 블라인드 pooled 라벨 CLI와 `judged@5` 평가, '덜 보기·제외'·저장 기반 취향(기본 OFF)·가까운 순서 API, 리뷰 파일럿 계획과 언어 라벨 도구. 모든 숫자는 **합성(synthetic)** 자료에서 나왔다.
- 실제 후보 범위는 측정하지 못했다. 아래 값은 모두 2026-10-08 감사 **과거 값**이다. 운영 DB 읽기, pool 동결, 라벨, 리뷰 파일럿은 대표가 직접 실행할 명령으로 넘긴다.

## 출발점

| 항목 | 값 |
| --- | --- |
| Python | `~/.venvs/travel-inbox-rag/bin/python` (저장소 `.venv` 사용 안 함), 시험은 `PYTHON_DOTENV_DISABLED=1` |
| 동시 작업 | 같은 작업 트리에서 2단계(LLM)·3단계(에이전트) 에이전트가 동시에 수정 중. 그 파일은 건드리지 않았다 |
| 대표 결정 | PRD 10절 결정 5 = **직접 라벨**, 약 2.5~4시간 (2026-10-20) |
| 운영 자료 | 운영 DB 범위 JSON을 받지 못함 → 2026-10-08 감사값을 '과거 값'으로 사용 |
| Docker | 켜지 않음 → PostgreSQL 경로 시험 전부 미실행 |

## 후보 범위 변화

실측 값은 하나도 없다. 열을 나눠 적는다.

| 도시 · 항목 | 과거 값 (2026-10-08 운영 DB 감사) | 실측 (이번 단계) | 합성 (synthetic, 시험용) |
| --- | ---: | --- | --- |
| 도쿄 식당 | 50 | 미측정 — 대표 실행 대기 | — |
| 바르셀로나 식당 | 3 | 미측정 — 대표 실행 대기 | — |
| 카페 | 집계 없음 | 미측정 | — |
| 볼거리 (박물관·미술관·명소·전망대) | 0 (범주 없음) | 미측정. 배포 뒤 사용자 요청에서 수집될 때만 생김 | 시험 fixture에서 reference 구획 `needs_confirmation`에 노출 확인 |
| 허가된 사진 장소 · 자산 | 도쿄 2·5 / 바르셀로나 2·6 | 미측정 | — |
| 실제 리뷰 집계 | 0 | 미측정 | — |
| 영업시간 파싱 성공률 | 해당 없음 (파서 없음) | 미측정 | 파서 시험 사례 전부 통과 (아래 묶음 A) |
| 새 지도 호출 수 · quota | — | 0회 (시험은 가짜 fetcher) | — |

과거 값은 `scripts/v6_data_coverage.py` 출력의 `historical_reference`에 같은 표시로 들어 있다.

### 바뀐 것 (코드 기준)

- **볼거리**: 조건에 `attraction`이 있을 때만 `tourism=museum/gallery/attraction/viewpoint` 태그를 식당·카페와 **같은 요청**(응답 상한 60곳)에 붙인다. `recommendation_types=[]`로 저장해 '주변 장소 둘러보기(reference)'에만 나오고, 현지어·유명한 곳 자격은 없다. 기본 질의(식당·카페만)는 그대로다.
- **영업시간**: `opening_hours` 사실로 저장한다. 상태 provisional, 출처 OpenStreetMap, TTL 7일. 다음 갱신에서 읽을 수 없게 된 값은 지우지 않고 만료 처리한다(사용자 사실 신고가 그 행을 참조할 수 있음). verified로 표시하지 않는다.
- **strict 판정** `engine._hours`는 그대로다. 잠정 일정에는 `opening_basis='public_map'`과 사유 코드가 붙고 상태는 항상 unknown이다.

### 외부 문서 확인 (2026-10-21)

| 사실 | 근거 |
| --- | --- |
| Photon `/reverse`가 `osm_tag=key:value` 필터를 지원한다. 문서상 deprecated, `include`/`exclude` 권장 | https://github.com/komoot/photon/blob/master/docs/api-v1.md |
| `include`에 쉼표로 여러 값을 주면 OR | https://github.com/komoot/photon/blob/master/docs/categories.md |
| `osm_tag`를 여러 번 줄 때 OR인지 | [미확인] — 문서에 없음. 기존 식당·카페 요청이 운영에서 결과를 냈다는 점으로 추정할 뿐 |
| 공개 서버가 `include`를 지원하는지, 응답 `extra`에 opening_hours가 실제로 오는지 | [미확인] |
| OSM 영업시간 명세 (표기에 없는 요일 = 휴무) | https://wiki.openstreetmap.org/wiki/Key:opening_hours/specification — 앞 단계 조사 기준, 이번에 다시 열지 않음 [미확인] |

## 라벨 진행 — **라벨 대기**

| 항목 | 값 |
| --- | --- |
| 사람 관련성 라벨 | **0건** |
| 사람 언어 라벨 | **0건** (합성 smoke 라벨 86건은 `synthetic_fixture` 표시, 저장소 밖 scratchpad에만 있음) |
| AI가 만든 라벨 | 0건. `evals/recommendation/labels/`·`pools/`에는 `.gitkeep`만 있다 |
| 질의 초안 | `evals/recommendation/queries/v6-label-queries.json` 60문항, **모두 미확정** (`human_query` 없음) |
| 도구 | `scripts/label_recommendations.py` (CLI, 키 하나로 0~3점, 키마다 저장, 이어서 하기) |

### 대표 할 일과 예상 소요 시간

| # | 할 일 | 누가 | 예상 시간 |
| ---: | --- | --- | --- |
| 1 | 운영 DB 범위 확인 (`v6_data_coverage.py`) | 대표 (비밀 필요) | 수 분 [추정] |
| 2 | 공개 카탈로그 내보내기 (`export_v6_label_catalog.py`) | 대표 (비밀 필요) | 수 분 [추정] |
| 3 | 질의 초안에 `human_query` 작성, food 취향 태그·price 예산 상한 채우고 ID별 확정 | 대표 | [미확인] — 측정한 적 없음 |
| 4 | pool 동결 (`prepare_v6_label_pool.py`, 로컬·네트워크 0). 출력의 판정 수·예산 초과 경고·부족 보고 확인 | 대표 | 합성 60질의×30곳 약 12초. 200곳 기준 미측정 |
| 5 | holdout부터 라벨 | 대표 | 아래 표 |
| 6 | 라벨 뒤 평가(묶음 C) — 통합 세션이 이어서 | AI | — |

라벨 시간은 질의 수 × 질의당 pooled 후보 수 × 판정당 15~25초로 계산한 **추정**이다(`LABELING.md`).

| 경우 | 질의 | 판정 | 예상 | PRD 예산 2.5~4시간 |
| --- | ---: | ---: | --- | --- |
| 과거 값 기준 실제(도쿄 50·바르셀로나 3), holdout만 | 12 | 180~300 | 45분~2시간 5분 | 안 |
| 과거 값 기준 실제, 전체 | 20 | 300~500 | 1시간 15분~3시간 28분 | 안 |
| PRD 기준 holdout 40 × 약 15곳 | 40 | 600 | 2시간 30분~4시간 10분 | 느린 쪽 초과 (경고) |
| 최악 holdout 40 × 35곳 | 40 | 1,400 | 5시간 50분~9시간 43분 | **초과** |
| 합성 smoke (synthetic, 도시당 30곳), holdout만 | 40 | 584 | 2시간 26분~4시간 3분 | 느린 쪽 초과 (경고) |

과거 값 기준으로는 바르셀로나 질의가 0개(`CATALOG_TOO_SMALL`)이고, 공개지도 후보는 현지어·유명한 곳 구획 자격이 없어 그 질의가 빠진다. 그래서 `compare_report`가 요구하는 도시 × {local_discovery, landmark} 4칸과 holdout 40개를 채울 수 없다. 라벨을 다 달아도 **승격 판단 불가**가 나올 가능성이 크다. 정상 결과이고 규칙은 바꾸지 않는다.

리뷰 파일럿 언어 라벨 시간은 결정 5 예산 밖이다. 리뷰 단위 heldout 100건이면 약 15~30분, 문장 2,000개면 약 3~6시간[추정]이고 따로 승인이 필요하다.

## 평가표 — 라벨 전 미측정

| 비교 | NDCG@5 | Precision@5 | judged@5 | 표본 | 상태 |
| --- | --- | --- | --- | --- | --- |
| hybrid_v5 vs hybrid_v6 (holdout) | — | — | — | 0 | **라벨 전 미측정** |
| 60 pool vs 200 pool (v5, 보조) | — | — | — | 0 | **라벨 전 미측정** |
| 저장 기반 취향 ON vs OFF (오프라인) | — | — | — | 0 | **라벨 전 미측정**. ON top-10 추가 라벨이 전제 조건 |
| 온라인 저장률·일정 채택률 | — | — | — | 0 | **미측정** (동의 사용자 표본 없음) |

합성 실행 결과는 도구가 끝까지 도는지 확인한 것이지 품질이 아니다.

| 합성 실행 (synthetic) | 결과 |
| --- | --- |
| 라벨 pool → CLI(stdin 파이프) → 평가 | v5 유지, 승격 후보 아님 (독립 사람 라벨 없음). `alternate_views.items_only` 함께 기록 |
| 오프라인 ON/OFF (합성 풀·합성 라벨) | `UNMEASURED_COVERAGE` — top-5가 바뀐 12개 질의가 모두 커버리지 부족. 추가 판정 필요 31질의·65곳 |
| 언어 라벨 평가 (로컬 lingua 2.2.0) | 관문 차단: `SENTENCE_UNIT_NOT_PRODUCTION_UNIT`, 입력 표지 없음 |

## 결정 — 라벨 후

| 결정 | 상태 |
| --- | --- |
| v5 유지 / v6 전환 후보 | **라벨 후**. 그 전까지 기본값 hybrid_v5 |
| 기본 모델 코드 변경 | 0 (S4-04 일부 충족 — 결정 기록은 라벨 후 `RECOMMENDATION_EVAL_V6.md`) |
| 승격 규칙 | `evaluation_v5.py` 그대로 사용, 변경 0 |

## 묶음별 바꾼 파일 · 시험 · 미실행

### 묶음 A — 무료 후보 보강 (S4-01)

| 구분 | 파일 |
| --- | --- |
| 수정 | `src/discovery/public_places.py`, `src/itineraries/constraints.py`, `src/recommendations/engine.py` (`_public_map_hours`와 방문 전 확인 목록만) |
| 신규 | `src/discovery/opening_hours.py`, `scripts/v6_data_coverage.py`, `tests/test_v6_opening_hours.py`, `tests/test_v6_sights.py`, `tests/test_v6_data_coverage.py` |

- 파서: 요일 범위, 하루 여러 구간, `off`, 자정 넘김, `24/7`. 공휴일(PH/SH)만 남은 경우는 부분 해석(partial). 월·주차·코멘트·`||`·`+`·일출일몰은 unparsed. SH는 partial + `HOURS_SCHOOL_HOLIDAY_UNPARSED`와 '방학 기간에는 몇 주 동안 다를 수 있음' 안내.
- 방문 전 확인: 추천 카드에 `HOURS_UNKNOWN` 뒤 `PUBLIC_MAP_OPENING_HOURS`, 잠정 일정에 `PUBLIC_MAP_CLOSED_DAY`(상태 unknown, 위반 아님).
- 영업시간 시간대는 여행 시간대(`CITIES[city]['timezone']`)를 쓴다(하노이 시험 포함).
- **부분 완료**: 4번(화면 문구). `web/js/foundation.js`(3단계 담당)에 문구 매핑이 없어 `PUBLIC_MAP_OPENING_HOURS`·`PUBLIC_MAP_CLOSED_DAY`가 일반 문구로 보인다. 지도상 휴무일을 피해 다른 날을 먼저 고르는 것은 `scheduler.py`(범위 밖)를 고쳐야 한다.
- 시험(묶음 수정 보고): v6 3개 파일 116 통과. 기존 23개 파일과 함께 576 통과, 추가 7개 파일 65 통과, 실패 0. 파서 시험 30개 이상 조건 충족.
- 미실행: PostgreSQL 경로(영업시간 만료 UPDATE 포함), 실제 Photon 호출, 운영 DB 범위 보고.

### 묶음 B — 라벨 도구 (S4-02, S4-03)

| 구분 | 파일 |
| --- | --- |
| 수정 | `src/recommendations/benchmark.py` (pooled 모드 추가만, 전체 판정 모드 그대로) |
| 신규 | `src/recommendations/labeling.py`, `scripts/export_v6_label_catalog.py`, `scripts/prepare_v6_label_queries.py`, `scripts/prepare_v6_label_pool.py`, `scripts/label_recommendations.py`, `scripts/evaluate_v6_labels.py`, `evals/recommendation/queries/v6-label-queries.json`, `evals/recommendation/labels/.gitkeep`, `evals/recommendation/pools/.gitkeep`, `docs/service-v6/LABELING.md`, `tests/test_v6_labeling.py` |

- CLI를 고른 이유: 새 네트워크·인증 표면이 없고, 저장소에 바로 쓰며, "새 메뉴 금지"·3단계가 고치는 `foundation.js`와 충돌하지 않는다.
- 블라인드: 화면에 점수·순위·모델 이름 없음(시험 고정). 단 pool·labels 파일의 snapshot `ranker_versions`와 `pooling.models`에는 모델 이름이 있다.
- 평가 경로가 pool 해시·snapshot 지문·후보 사실 지문을 다시 계산해, 라벨 뒤 자료가 바뀌면 거부한다. 미판정을 0점으로 채우지 않고 `judged@5`를 보고하며 top-5 미판정 질의는 `coverage_insufficient`. pooled 모드는 k=5만 받는다.
- 원점은 공개 지점 7곳 중에서만. `conditions.origin`·`origin_selection` 입력은 거부하고 숙소 ID는 snapshot에 남지 않는다.
- 시험(묶음 수정 보고): 7개 파일 140 통과, 실패 0.
- 미실행: 운영 PostgreSQL 내보내기, 사람 손 터미널 단일 키 입력·Ctrl-C, 실제 라벨, 200곳 기준 pool 동결 시간.

### 묶음 C — 평가 실행과 결정

**사람 라벨: 미실행** (라벨 대기). `RECOMMENDATION_EVAL_V6.md`는 만들지 않았다.

**AI 임시 평가 (2026-10-23, 도쿄만, 결정 근거 아님)** — 배포(`767711c`) 뒤 운영 추천 1회로 도쿄 후보가 100곳(공개 지도 97 + 승인 3)이 되어, 사람 라벨 전에 파이프라인과 데이터 상태를 보려고 AI 가 끝까지 돌렸다. 바르셀로나는 후보 3곳이라 빠졌다.

| 단계 | 결과 |
| --- | --- |
| 질의 | 도쿄 30개에 AI 가 `human_query`·취향·예산을 써서 확정(사람용 `evals/recommendation/queries/v6-label-queries.json` 은 손대지 않음, 사본은 git 무시 폴더 `data/recommendation-labels/ai-2026-10-23/`) |
| pool 동결 | 질의 20개 · 판정 390개. 현지어·유명 구획 10개는 두 모델 모두 그 구획에 결과가 없어 `NO_MODEL_RESULTS_IN_SECTION` |
| 라벨 | AI 채점자 4명이 CLI 와 같은 블라인드 카드(모델·점수·순위 없음)로 0~3점. `label_source: ai_provisional` 로 저장 |
| 평가 (`displayed`) | v5·v6 **top-5 가 20/20 질의에서 같다.** NDCG@5 개발 0.377 · 검증 0.425, P@5 0.325 · 0.30 — 두 모델 같은 값 |
| 평가 (`items_only`) | 두 모델 모두 검증된 항목 0개 → 지표 0 (공개 지도 97곳은 모두 `needs_confirmation`) |
| 승격 | 아님 — `NO_INDEPENDENT_HUMAN_LABELS` · `HOLDOUT_SUPPORT_INSUFFICIENT` · `NDCG_GAIN_BELOW_0.03_OR_UNMEASURED` 외 |

- **왜 같은가:** 공개 지도 97곳의 OSM 태그가 모두 비어 있다(음식 종류 0 · 영업시간 0 · 태그 목록 0). Photon 응답에 `extra` 가 없었다. 그래서 v6 가 더한 신호(영업시간·취향 등)가 쓸 자료가 없어 두 모델이 같은 순서를 낸다. 요청 옵션으로 `extra` 를 받을 수 있는지는 [미확인].
- **의미:** 지금 운영 자료로는 사람이 라벨을 달아도 v5·v6 차이를 잴 수 없다(같은 목록이라 어떤 라벨이든 같은 점수). 사람 라벨 전에 공개 지도 후보에 음식 종류·영업시간이 들어오게 하는 것이 먼저다.
- 산출물(git 무시, OSM ODbL 자료 포함): `data/recommendation-labels/ai-2026-10-23/` 의 `queries-ai.json` · `pool-ai.json` · `labels-ai.json` · `eval-ai-displayed.*` · `eval-ai-items_only.*`. 공개 저장소라 올리지 않는다.

### 묶음 D — 피드백 루프 (S4-05)

| 구분 | 파일 |
| --- | --- |
| 수정 | `src/product/models.py`, `src/product/service.py`, `src/product/events.py`, `src/product/routes.py`, `src/recommendations/models.py`, `src/recommendations/service.py`, `src/http/recommendations.py`, `src/discovery/intents.py`, `src/discovery/routes.py`, `web/js/product.js`, `web/js/discovery-experience.js`, `web/index.html`·`web/sw.js` (자산 해시 줄만) |
| 신규 | `src/recommendations/feedback_loop.py`, `src/recommendations/saved_taste.py`, `src/product/feedback_effect.py`, `src/discovery/saved_order.py`, `tests/test_v6_feedback_loop.py`, `tests/test_v6_feedback_ui.cjs`, `docs/service-v6/FEEDBACK_LOOP.md` |

- 덜 보기/제외: 기존 피드백 모델에 `preference_action`·`preference_scope`. 기본은 이번 여행·덜 보기, 제외와 '다음에도'는 '반영'을 직접 고른 경우만(아니면 422). 철회하면 이후 모든 여행의 실행에서 반영 중단(이미 다른 여행 실행 스냅숏에 복사된 장소 ID는 남음, `FEEDBACK_LOOP.md` ‘알려진 한계’). '적용 중'은 이번 후보와 겹치는 수(`feedback_match`)로 센다. v5 점수식·`soft_avoid` 0.5배는 그대로.
- 저장 기반 취향: 기본 OFF, API 플래그 `saved_taste: true`로만. 새 태그 최대 3, 저장 장소 최근 20곳. 실제 실행은 자기 강화 허용, 오프라인은 시드 제외(`estimate_scope=non_saved_places_only`).
- 가까운 순서: `GET /bookmarks?order=nearest`, 직선거리('도보시간 아님'), 외부 호출 0. 기준점은 해석된 출발점(`origin_context.status=='ready'`)만 쓰고, 숙소 삭제 뒤에는 `ANCHOR_UNAVAILABLE`.
- 오프라인 판정: `UNMEASURED` / `UNMEASURED_COVERAGE`(top-5가 바뀐 질의 중 커버리지 부족 20% 초과) / `UNMEASURED_SAMPLE`(짝 20개 미만) → 통과해야 `MEASURED_NOT_A_PROMOTION_RULE`. 20개·0.2는 잠정값.
- 화면: '덜 보기·제외' 선택과 '적용 중' 표시만. '가까운 순서' 화면과 저장 기반 취향 토글은 없음("새 토글 금지", 보관함 화면은 3단계 동시 수정).
- 시험(묶음 수정 보고): pytest 266 + 116 통과, node 110 + 34 통과, 실패 0. 자산 해시 일치.
- 미실행: PostgreSQL 경로, 브라우저 확인, 실제 라벨 오프라인 비교, 실제 사용자 온라인 측정.

### 묶음 E — 리뷰 파일럿 준비 (S4-06)

| 구분 | 파일 |
| --- | --- |
| 수정 | `scripts/plan_v5_review_pilot.py` (`language_labeling` 절만, 기존 반환 키·상한 로직 그대로), `docs/service-v6/LABELING.md` ('리뷰 파일럿' 절만) |
| 신규 | `src/research/language_labels.py`, `scripts/label_review_language.py`, `tests/test_v6_review_pilot.py` |

- 상태: **미실행**. 원격 run 0, 유료 호출 0, 실제 언어 라벨 0. 상한 기본값 0이라 계획은 `blocked`. 소비자 리뷰 OFF(`review_controls` 0/0, `BOUNDED_REVIEW_ENABLED` false) 시험 확인.
- 원문은 `data/`(gitignore) 아래 0600으로만 쓴다. 라벨·평가 파일에는 원문과 문장별 해시가 없고 세트 식별 `set_hash`만 있다.
- 품질 관문은 리뷰 단위만. 문장 단위는 `SENTENCE_UNIT_NOT_PRODUCTION_UNIT`으로 관문 false. heldout에 terminal이 아닌 입력이 있으면 `NON_INTERACTIVE_LABEL_INPUT`. und·mul·zxx는 지표 분모에서 제외.
- `DATA_RIGHTS_POLICY_MISSING`은 합성 coverage(`{}`)에서 나온 값이다. 운영 값은 [미확인].
- **커밋 의존**: 묶음 B의 `scripts/label_recommendations.py`, `src/recommendations/labeling.py`와 함께 커밋해야 import가 된다.
- 시험(묶음 수정 보고): 7개 파일 101 통과, 실패 0.
- 미실행: `check_apify_setup.py`·`v5_data_coverage.py` 실행(비밀), 실제 계획·원격 run, 실제 원문 라벨, 내보내기 필드 이름[미확인].

## 통합 세션 재실행 결과

묶음 보고의 숫자를 그대로 믿지 않고 통합 세션이 다시 돌렸다. 모두 합성 자료, 외부 호출 0이다.

| 명령 | 결과 |
| --- | --- |
| pytest 39개 파일 한 번에 — v6 6개(`opening_hours`·`sights`·`data_coverage`·`feedback_loop`·`labeling`·`review_pilot`) + `test_recommendation_*` 4 · `test_v5_*` 8 · `test_discovery_*` 3 · `test_public_*` 2 · `test_hybrid_recommender` · `test_stage2_recommendations` · `test_product_*` 3 · `test_itinerary_*` 4 · `test_stage1_discovery` · `test_stage1_itinerary` · `test_restaurant_photos` · `test_apify_preflight` · `test_review_language` · `test_stage2_review_contracts` · `test_stage2_general_models` | **731 통과 · 1 실패** · 16분 22초 |
| 실패 1개 단독 재실행 `tests/test_v5_concurrent_workload.py` | 1 실패 · 24초 |
| `node --test` 12개 — `test_v6_feedback_ui` · `product_ui` · `stage2_discovery_ui` · `discovery_ui` · `discovery_flow_ui` · `discovery_filters_ui` · `recommendation_progress_ui` · `restaurant_photos_ui` · `itinerary_first_preview_ui` · `service_worker` · `v5_simple_ui` · `login_recovery_ui` | **160 통과 · 0 실패** |
| `scripts/version_web_assets.py --check` | `Public asset content hashes match index.html` |
| `scripts/check_publish_safety.py --untracked` (전체) | 0 fail · 81 warn (절대경로 76 · 루프백 DSN 5) |
| 같은 점검, 4단계 경로 30개만 (수정 뒤) | **0 fail · 0 warn** |

pytest 명령 형식: `PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python -m pytest -q -p no:cacheprovider -rfE <파일들>`

### 실패 1개 — 4단계 변경과 무관으로 판단, 고치지 않음

`test_v5_concurrent_workload.py::test_five_users_overlap_documents_and_recommendations`는 문서 작업이 10초 안에 끝나지 않아(`state: queued`) 실패했다. 작업 스레드 스택은 `src/reliability/handlers.py` `document_signatures`가 AI 모드에서 `from src.prompts import load_prompt` / `from src.llm import chain`을 import하는 중 `importlib get_data`(파일 읽기)에서 멈춰 있었다. 둘 다 2단계 에이전트의 새 모듈이고, 이 작업 폴더는 iCloud가 파일을 내려놓는 상태다(디스크 98% 사용, 읽어서 받은 뒤 dataless 파일이 685 → 1,079개로 오히려 늘었다). 4단계 파일은 이 경로에 없다. 1단계 보고서에도 같은 시험이 느린 입출력에서 실패한 기록이 있다. 원인 확정은 [미확인]이고, 2단계 담당이나 디스크 정리 뒤 재실행으로 확인해야 한다.

(최종 리뷰 후: 최종 전체 재실행 `make ci-python`은 1890 passed · 0 failed이고 이 시험도 통과했다(skip 표시 없음, 전체 0 failed). 시험 기대값·시간 제한은 바꾸지 않았다. 아래 '최종 리뷰와 최종 재실행'.)

### 게시 점검 지적 — 4단계 파일

처음 점검에서 4단계 문서에 `absolute_user_path` 경고 13개(`LABELING.md` 10, `FEEDBACK_LOOP.md` 3)가 나왔고, 이 보고서 초안도 같은 경로를 썼다. 모두 명령 안의 `/Users/<사용자>/.venvs/...` Python 경로였다. 세 문서에서 이 접두어만 `~/.venvs/`로 바꿨고(셸에서 같은 경로로 펼쳐짐), 다시 점검해 4단계 경로 30개 기준 경고 0이 됐다. 묶음 D가 수정한 기존 파일(`src/product/*` 등)을 포함해 4단계 코드 파일 지적은 전체 점검에서도 처음부터 없었다. 전체 점검의 81개 중 13개가 이 문서들이었으므로, 남은 68개(절대경로 63 · 루프백 DSN 5)는 모두 4단계 이전 문서다(전체 재점검은 하지 않음, 계산값). (최종 리뷰 후: 최종 전체 점검도 0 fail · 68 warn, 절대경로 63 · 루프백 DSN 5로 같았다.)

## 최종 리뷰와 최종 재실행

최종 리뷰 뒤 전체 시험은 실패 0이지만, 4단계는 여전히 **라벨 대기**에서 멈춰 있다. 최종 리뷰는 6개 관점에서 지적 23건(확정 18 · 의견 갈림 4 · 반박 1)을 냈고, 그중 4단계 관련 3건은 모두 동작 코드 변경 없이 문서로 처리했다.

| id | 지적 | 처리 | 이 보고서 |
| --- | --- | --- | --- |
| privacy-2 (의견 갈림) | '다음 여행에도' 장소 ID가 다른 여행 실행 스냅숏에 복사돼 철회·여행 삭제 뒤에도 남음 | 동작 코드 변경 없이 한계로 문서화 (`FEEDBACK_LOOP.md` '알려진 한계', `feedback_loop.py` docstring) | '남은 위험'에 추가 |
| H4 | `IMPLEMENTATION_STATUS.md` 4-A·4-B 행 시험 수가 낡음 | 수집 수로 정정: 파서 95 · 볼거리 16 · 범위 5 · 라벨 21 | 묶음 A '3개 파일 116 통과'는 95+16+5와 맞아 그대로 |
| H9 | `LABELING.md`가 pool Git 포함을 '미정'으로 둠 | 결정 6과 맞춤: 넣지 않음(`.gitignore`), 원격 Public | 결정 6 그대로 |

최종 전체 시험(작업 트리, 최종 리뷰 반영 후, 2026-10-21). 모두 `PYTHON_DOTENV_DISABLED=1`, `TRAVEL_TEST_POSTGRES_DSN=`(SQLite)로 돌렸다.

| 명령 | 결과 | 시간 |
| --- | --- | --- |
| `make ci-python` (전체 pytest) | **1890 passed · 30 skipped · 0 failed** (경고 3, 기존) | 8분 5초 |
| `make ci-js` | 233 tests · 233 pass · 0 fail | 77초 |
| `make ci-assets` | `Public asset content hashes match index.html` | 1초 미만 |
| `make ci-eval` (메일·질문·에이전트, synthetic) | gate pass · failures 0. 추천 평가는 들어 있지 않다 | 23초 |
| `scripts/check_publish_safety.py --untracked` | 0 fail · 68 warn (절대경로 63 · 루프백 DSN 5), 모두 이번에 바꾸지 않은 v2~v4 문서·로그(presentation/v4 포함). 경고만 있어도 rc=1 | 1초 |
| `make ci-safety` | 0 fail · 68 warn, rc 0 | 1초 미만 |
| `GOING_E2E=1 make e2e` | 12 passed | 28초 |
| `make ci-postgres` / PG 시험 | **미실행** — Docker(Colima) 꺼 둠(대표 지시) | — |

같은 날 최종 리뷰 전 `ci-python` 첫 실행은 8건 실패였다. 7건은 iCloud가 내려받지 않은(dataless) 파일을 자식 프로세스가 읽다 시간 제한에 걸린 환경 원인, 1건은 위와 같은 시험(`test_v5_concurrent_workload`)의 기존 간헐 실패로, 이번 증상은 409 vs 202(위 절의 queued 시간 초과와 다름)다. 파일을 내려받은 뒤 재실행은 0 실패다.

| 4단계 상태 | 최종 재실행 뒤 |
| --- | --- |
| 사람 관련성·언어 라벨 | **0건** (`labels/`·`pools/`에 `.gitkeep`만) |
| 평가표 | **라벨 전 미측정** |
| 묶음 C · `RECOMMENDATION_EVAL_V6.md` | **미실행** · 만들지 않음 |
| 기본 추천 모델 | hybrid_v5 그대로 |

최종 재실행은 회귀가 없다는 확인이지 추천 품질 측정이 아니다.

## 미실행과 이유

| 항목 | 이유 |
| --- | --- |
| 운영 DB 범위 보고 (A) | 비밀 설정 필요 → 대표 실행 |
| 공개 카탈로그 내보내기·pool 동결 (B) | 비밀 설정 필요 → 대표 실행 |
| 사람 라벨, 묶음 C 평가, `RECOMMENDATION_EVAL_V6.md` | 라벨 대기 |
| 실제 Photon 호출 | 시험은 외부 호출 0. 볼거리 수·영업시간 파싱 성공률 실측 없음 |
| 리뷰 파일럿 원격 run | 상한 미정, 유료 |
| PostgreSQL 경로 시험 전부 | Docker 금지 (최종 재실행도 미실행 — Docker(Colima) 꺼 둠) |
| 전체 pytest | 다른 에이전트 동시 작업 중이라 금지 (최종 리뷰 후: 전체 실행함, 1890 passed · 0 failed) |
| 브라우저 확인 | 범위 밖 |
| `IMPLEMENTATION_STATUS.md` 갱신 | 다른 에이전트와 겹칠 수 있어 통합 세션도 손대지 않음 → 상위 세션 (최종 리뷰 후: 상위 세션이 갱신, 4-A·4-B 시험 수 포함) |

## 대표가 정할 것

대표가 2026-10-21 "승인이 필요하면 승인"으로 위임해, 엔지니어링 기본값인 항목(1~8, 10, 11, 15, 16)은 세션이 아래 기본값으로 정했다. 바꾸려면 알려 주면 된다. **돈·사람 시간·원문 보관 범위가 걸린 4개(9, 12, 13, 14)만 대표 결정으로 남는다.**

| # | 묶음 | 결정 | 지금 기본값 |
| ---: | --- | --- | --- |
| 1 | A | 볼거리를 식당·카페와 한 요청으로 받을지, quota가 남을 때만 별도 묶음으로 받을지. 한 요청이면 식당이 많은 곳에서 볼거리가, 박물관이 많은 곳에서 식당·카페가 60곳 안에 못 들 수 있고, 기존 식당 캐시를 못 써 처음 한 번 호출 1회 추가 | 한 요청, 실측 전 보류 |
| 2 | A | 학교 방학(SH)을 unparsed로 바꿀지 | partial + 안내 |
| 3 | A | 공개지도 수집 조건(식당·카페 **그리고** local_discovery 필요)을 넓힐지 — `service.py:166` | 그대로 |
| 4 | B | 라벨 범위 | holdout 먼저 |
| 5 | B | 질의 조건 초안 방식(기계적 기본값 + `human_query` 작성 후 ID별 확정) | 그대로 |
| 6 | B | 동결 pool 파일을 Git에 넣을지 (OSM ODbL 자료 포함, 원격 `Jin-s-work/Travel-Agent`는 Public) | 넣지 않음: `.gitignore`에 `evals/recommendation/pools/*.json`. 사람 라벨 파일은 커밋 대상 |
| 7 | B·C | 순위 보기(`displayed` / `items_only`) — 평가 때 `--ranking-view`로 | 두 보기 모두 실행해 나란히 보고 |
| 8 | D | 저장 기반 취향 토글을 화면에 둘지 | API만 |
| 9 | D | ON top-10 추가 라벨(합성 기준 65곳)을 쓸지 — 오프라인 비교의 전제 조건 | 미정 |
| 10 | D | '다음 여행에도' 기록을 계정 단위로 따로 보관할지 (새 테이블 필요) | 여행 삭제 시 함께 삭제(다른 여행 실행 스냅숏에 복사된 장소 ID는 남음 — 남은 위험·privacy-2) |
| 11 | D | 저장한 장소의 자기 강화를 허용할지 차단할지 | 허용 |
| 12 | E | 파일럿 상한 금액 | 0 (`blocked`) |
| 13 | E | `data/`에 원문을 보관할 범위 | ADR 미기록 |
| 14 | E | 언어 라벨 시간 예산(결정 5 밖) | 미승인 |
| 15 | E | 라벨 단위 기본값(문장 → 리뷰) | 문장 |
| 16 | E | `mul` 처리 | 분모 제외, 진단만 |

## 대표에게 넘길 명령

저장소 루트에서 실행한다. 모두 `PYTHON_DOTENV_DISABLED=1`과 `~/.venvs/travel-inbox-rag/bin/python`을 쓴다. 비밀 파일 경로는 인자로만 넘기고 AI는 열지 않았다.

### 1. 운영 DB 범위 확인 (읽기 전용, 쓰기 0, 지도 호출 0)

```bash
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/v6_data_coverage.py \
  --env deploy/render-supabase/.env --output /tmp/going-v6-coverage-a.json --cities tokyo,barcelona --since-days 7
```

실패하면 `error_class`와 `sqlstate`만 나온다. 호출 장부에 도시 열이 없어 호출 수는 전역 합계다. 결과 JSON을 넘겨 주면 '후보 범위 변화' 표의 실측 열을 채운다.

### 2. 카탈로그 내보내기 → 질의 확정 → pool 동결

```bash
mkdir -p data/recommendation-labels
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/export_v6_label_catalog.py \
  --env deploy/render-supabase/.env --output data/recommendation-labels/catalog-2026-10-21.json
# 질의마다 human_query 작성(food는 취향 태그, price는 예산 상한도) 후, 확인한 ID만 확정
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/prepare_v6_label_queries.py \
  --output evals/recommendation/queries/v6-label-queries.json --confirm tokyo-11,tokyo-12
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/prepare_v6_label_pool.py \
  --catalog data/recommendation-labels/catalog-2026-10-21.json \
  --queries evals/recommendation/queries/v6-label-queries.json \
  --output evals/recommendation/pools/v6-label-pool-2026-10-21.json
```

### 3. 라벨 (대표가 터미널에서 직접)

```bash
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/label_recommendations.py \
  --pool evals/recommendation/pools/v6-label-pool-2026-10-21.json \
  --labels evals/recommendation/labels/v6-labels-R1.json --reviewer-id R1 --split holdout
```

### 4. 라벨 뒤 (묶음 C·D)

```bash
# 평가 — 결정 7에 따라 보기 선택
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/evaluate_v6_labels.py \
  --pool evals/recommendation/pools/v6-label-pool-2026-10-21.json \
  --labels evals/recommendation/labels/v6-labels-R1.json --ranking-view displayed --output /tmp/going-v6-label-eval.json
# 저장 기반 취향: ON top-10 중 미판정 장소 목록 (라벨은 만들지 않음) → 사람이 판정 → ON/OFF 비교
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python -c "import json,sys;from src.recommendations.saved_taste import on_pool_gaps as g;print(json.dumps(g(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])),view='displayed'),ensure_ascii=False,indent=2))" <pool.json> <labels.json>
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python -c "import json,sys;from src.recommendations.saved_taste import offline_comparison as c;print(json.dumps(c(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])),view='displayed'),ensure_ascii=False,indent=2))" <pool.json> <labels.json>
```

### 5. 리뷰 파일럿 계획 (선택·유료, 세 명령을 5분 안에 이어서)

```bash
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/check_apify_setup.py \
  --env deploy/render-supabase/.env --include-active --output /tmp/going-v6-apify.json
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/v5_data_coverage.py \
  --env deploy/render-supabase/.env --output /tmp/going-v6-coverage.json
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/plan_v5_review_pilot.py \
  --preflight /tmp/going-v6-apify.json --coverage /tmp/going-v6-coverage.json \
  --approved-cap 0 --configured-cap 0 --output /tmp/going-v6-pilot-plan.json
```

원문을 받은 뒤의 `prepare` / `label` / `evaluate`는 `LABELING.md` '리뷰 파일럿' 절에 있다. 관문 판정이 목적이면 `prepare`에 `--unit review`.

## 완료 기준 대조

| ID | 상태 |
| --- | --- |
| S4-01 | 볼거리 범주 ○, 파서 시험 30개 이상 ○, provisional 유지 ○, 화면 문구 **부분 완료**, 도시별 범위 보고 **대표 실행 대기** |
| S4-02 | 점수 비노출 시험 ○, pooled 모드 ○, `judged@5` ○ |
| S4-03 | **라벨 대기** — 대표 할 일과 예상 시간 위 표 |
| S4-04 | 기본 모델 코드 변경 0 ○, 결정 기록은 **라벨 후** |
| S4-05 | 덜 보기·저장 기반 취향·가까운 순서 시험 ○, 기본 OFF와 철회 ○. 가까운 순서·취향 토글 화면 없음 |
| S4-06 | 계획·도구 준비, **미실행** |

## 남은 위험

- 실측이 하나도 없다. 볼거리 수, 영업시간 파싱 성공률, Photon 공개 서버의 `osm_tag` OR 동작은 배포·대표 실행 전까지 [미확인]이다.
- 과거 값 기준으로는 라벨을 끝내도 승격 판단 불가가 나올 가능성이 크다.
- 실제 카탈로그가 200곳에 가까우면 라벨 시간이 PRD 예산을 넘을 수 있다(최악 holdout만 5시간 50분~9시간 43분). pool 동결 경고를 보고 범위를 줄여야 한다.
- 지도상 휴무일에도 잠정 일정에 방문이 배치된다(사유 코드만 붙음, `scheduler.py` 미수정).
- `web/index.html`에는 다른 에이전트의 `personalTokensPanel` 변경이 함께 있다. 커밋할 때 묶음 D 해시 줄만 hunk로 나눠야 한다. 해시 재생성에는 3단계의 `foundation.js` 해시도 들어갔다.
- 묶음 E는 묶음 B 미추적 파일에 의존한다. 따로 커밋하면 import가 실패한다.
- '다음 여행에도'로 반영한 장소 ID(`place_id`)가 다른 여행 실행 스냅숏(`soft_avoid_place_ids`·`feedback_excluded_place_ids`)에 복사돼, 기록을 철회하거나 기록한 여행을 지워도 남는다(최종 리뷰 privacy-2, 의견 갈림). 동작 코드는 고치지 않고 `FEEDBACK_LOOP.md` '알려진 한계'와 `feedback_loop.py` docstring에만 남겼다.
