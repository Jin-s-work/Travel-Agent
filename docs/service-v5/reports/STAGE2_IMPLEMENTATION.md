# v5 2단계 구현·검증 기록

2026-10-08. 후보200 처리, 관측 계약 분리, 사진 상태, v6 실험/평가 도구를 구현했다. **실제 도시별200곳 수집·리뷰 공개·언어/추천 정확도 개선·로그인 이후 브라우저 검증·운영 배포는 완료 상태가 아니다.**

현재 요약은 [단일 최신 진행 표](../IMPLEMENTATION_STATUS.md)를 따른다. [1단계 보고서](STAGE1_IMPLEMENTATION.md)의 과거 ‘2단계 미착수’는 당시 기록이다.

## 보존 및 재사용

- 원본 `~/Desktop/ChatGPT/travel-inbox-rag`, branch `codex/private-beta-launch`, HEAD `5da54dd`와 기존 변경을 보존했다. reset/stash/강제 checkout 없음.
- 기존 native 검증 체크아웃 `/private/tmp/going-hybrid-verify.uPktYX`에서 수정·시험하고 작업 시작 파일 hash와 원본을 비교해 이번 변경만 반영했다. 최종 코드·시험·문서/로그54파일의 byte hash가 일치한다. 원본과 다른 native HEAD를 통째로 복사하지 않는다.
- v5 PRD/AUDIT/1단계 기록, v2~v4의 리뷰/운영/추천 계약과 `APIFY_SETUP.md`를 확인했다. 적용 가능한 AGENTS.md 없음.
- 인증/소유권, admin_research scope, 단일 dispatcher, lease/fencing, SQL job/checkpoint, Idempotency-Key, 공통 비용 예약/정산, 삭제 tombstone, 일정 preview/apply/undo를 재사용했다.
- 새 테이블/파괴적 migration 없음. 기존 JSON snapshot/계약/집계/가격·호출 ledger에 additive 필드를 저장한다. 기존 strict request hash와 과거 모델 버전 재생을 보존한다.
- README·발표·대본·운영 secret·Git HEAD·배포를 변경하지 않았다. 외부 계정 생성·업그레이드·충전 없음.

## A. 후보 확보

`src/discovery/candidate_pool.py`는 공개 지리7구간을 기존 Photon adapter로 조회한다. 한 응답60, pool200/raw400, 기존 quota/cooldown, 전체 deadline을 각각 적용한다. 상수만200으로 바꾸지 않았다. 중간 실패는 확보 자료와 이유를 보존하고 한도 때문에37곳이면37곳으로 끝낸다.

도시 중심/인접 공개 지역 조회와 개인 숙소 원점의 내부 거리 계산을 구분한다. 개인 위치를 공용 캐시나 외부에 전송하지 않는다. 지역별 query hash/조회 건수/실제 수신/중복·제외/범위/checkpoint를 저장한다. 같은 이름만으로 merge하지 않는다. 별도 출처가 같은 canonical ID에 연결된 경우 후보1곳으로 센다.

전체 pool의 자격과 점수를 평가한 후 결과를 저장한다. `discovery_result_v1`과12개 페이지/cursor를 재사용하고 추가 GET의 외부 호출0을 검증했다. 신규 run의 query/deadline/pool/사전 선택 버전을 고정해 설정 변경 후 조용히 다른 결과를 만들지 않는다.

세부 수치 정의와 실제 후보 수는 [DATA_COVERAGE.md](DATA_COVERAGE.md)에 있다.

## B. 리뷰 관측

`CollectionRequest.observation_profile`로 strict와 bounded 연구 계약을 분리했다. 기본 strict는 과거 request hash를 유지한다. 계약 선택과 언어 평가 연결도 프로필별이며 신규 B 계약이 과거 strict run에 소급되지 않는다.

원천 페이지/내부 재시도는 Apify에서 null이다. dataset 페이지 수를 별도로 표시한다. B에서는 날짜 불명을 기간 집계에서 제외하되 고유200 상한에는 포함한다. 원문/번역/rating-only/추출 불명/unknown 분류와 T/C/U/L/K 산술을 유지한다. 기존 remote run ID/호출 receipt/checkpoint/cleanup outbox로 재시작을 복구한다.

관리자 화면에 관측 프로필, 별도 B readiness, 개별 날짜/무필터/비용 검증 입력, 종료/수신/집계 수를 연결했다. `BOUNDED_REVIEW_ENABLED=false`가 기본이며 true라도 연구 출력만 제공한다. B 집계는 strict badge로 소비하지 않는다. 사용자 화면은 기존 사용 가능한 evidence 상세/분모·확인일 경로를 재사용한다.

실제 계정 확인과 DB 조사는 읽기 전용으로 실행했다. 이용 정책·검수 지점 연결·공급자 계약·실제 언어 평가·Apify 단가/승인 상한이 없어 실수집0회다. [ADR](REVIEW_OBSERVATION_ADR.md)에 대안, 비용, 실행/중단/복구 준비와 남은 승인을 기록했다. 단순 키 존재를 제품 활성화로 처리하지 않았다.

## C. 사진

서버에 `photo_status.availability_state`를 추가하고 기존 state 필드는 유지했다. available/no_photo/provider_failed/expired/policy_blocked/identity_unresolved를 구분하고 loading은 브라우저 이미지 로딩 상태다. 만료 캐시/지점 불일치를 일반 무사진으로 뭉개지 않는다.

최대3장, 지점/권한/attribution 검증, 음식·내부·외관/unknown 구분, 첫 상세 사진 우선과 후속 lazy load, 고정 사진 영역, 실패 안내/지도 링크를 연결했다. 사진 클릭은 추천/리뷰 job을 시작하지 않는다. negative cache와 실패 backoff는 기존 별도 TTL을 유지한다. 새 사진 유료 공급자 없음.

실제 허용 사진4곳11장을 확인했다. 로그인 이후 브라우저 차단으로 실제 img load 성공률은 미측정이다. 현재 무사진 비율을 네트워크 실패율로 대신 쓰지 않았다.

## D. 추천 평가와 기본 모델

v5 코드를 고정한 채 `hybrid_v6.py`를 추가했다. 음식 별칭/cuisine 근거와 유명함 유형/cohort별 interleaving이 주요 차이다. 모든 언어·필수 방문 조건 gate는 동일하다. missing은null, 사진 수는 점수에 미반영, 외부 embedding/LLM 추가 호출 없음.

shadow 플래그가 켜지면 같은 snapshot의 v6 결과를 진단용으로 저장한다. 기본 모델은v5이며 자동 승격하지 않는다. source 철회 시 진단 결과도 제거한다. 독립 정답 Candidate Recall@200, 고정 분모 Precision@5, NDCG@5, paired bootstrap95%, 도시/방식 퇴행·누락·미라벨·필수 위반·latency gate와 분할 누출 검사를 추가했다.

60개 블라인드 평가 작업 양식을 생성했다. 실제 사람 라벨0개다. 합성12질의 비교는 v5/v6 동일 rubric 점수였으며 실제 정확도는 미측정이다. [평가 보고서](RECOMMENDATION_EVALUATION.md)에 정의·표본·실행 명령을 기록했다.

## 추가로 발견해 수정한 결함

1. 같은 추천 job의 여러 공개 지역 조회가 하나의 ledger key를 사용해 두 번째 호출부터 충돌했다. job+공개 query hash와 job attempt를 사용해 각 실제 요청과 재시도를 구분했다.
2. 메일 분석 후 여행의 일반 version이 증가하면 숙소 원점이 그대로인데 `ORIGIN_CHANGED`로 추천을 실패시켰다. `stay_origin_v2`는 날짜/도시 구간/해당 숙소/실제 원점의 의미 있는 변경만 hash에 포함한다. 과거 v1 snapshot은 v1 방식으로 검증한다. 실제 숙소 삭제/변경은 계속 차단한다.
3. 사진 DTO에 필드가 추가되어 기존 exact-dict 시험1건이 실패했다. 기존 호환 필드와 새 상태를 함께 검증하도록 수정했다.
4. 배포 설정 변경 뒤 미완료 후보 job이 다른 query 계획을 사용하는 것을 막았다. B 계약과 strict 계약이 서로 대체되지 않도록 조회 프로필을 분리했다.
5. 좋은 평균만으로 누락 도시/추천 방식이나 미라벨 holdout을 숨겨 v6 전환 후보로 처리하지 않게 했다.

## 실제 실행 결과

실행 환경: macOS arm64 / Python3.13.5 / Node24.1.0. `PYTHON_DOTENV_DISABLED=1`, native Python `/private/tmp/going-stage1-native313/bin/python`. 외부 호출은 fixture가 대체한다.

| 실행 | 결과 | 범위 |
| --- | --- | --- |
| native `python -m pytest -q` | **1264 passed,17 skipped**,187.88초 | 당시 전체 Python. 아래 마지막 보완 전 |
| native `node --test tests/*.cjs` | **219 passed,0 failed** | 전체 JS/DOM harness |
| 마지막 후보/관측/계약/비용 준비/언어·복구 시험 | **89 passed**,18.37초 | query 설정 고정, B 프로필 분리, active 계정/상한 시험 추가 후 |
| 마지막 모델/사진 원격 정리/숙소/5인 동시 작업 시험 | **64 passed**,16.73초 | 누락 평가 gate/동시성 변경 후 |
| `python scripts/version_web_assets.py --check` | 통과 | public asset hash 일치 |
| 원본 핵심 Python 재검증 | **138 passed**,128.80초 | 새 후보/관측/사진/모델/동시성·1단계 연결·기존 계약/리뷰 API |
| 원본 전체 JavaScript | **219 passed,0 failed** | 원본 화면 스크립트/DOM 회귀 |
| 원본 자산/공백 검사 | 통과 | asset hash, 변경 범위 git diff --check |
| 실제 Supabase 읽기 전용 감사 | 성공,쓰기0 | 공용 후보/리뷰/허용 사진만 집계 |
| 실제 Apify preflight | 성공,원격 run 시작0 | 계정/한도/현재 가격/build/active run 조회 |
| 실제 리뷰 파일럿 | 미실행 | 권리/지점 연결/가격/기존 승인 상한 미설정 |
| 브라우저 | 로그인 페이지 확인, 이후 미실행 | 합성 IdP8767 저장된 접근 차단 |

17개 skip은 별도 disposable PostgreSQL/pgvector 등 환경이 필요한 시험이다. 이번 read-only 운영 감사는 SQL mutation·migration 시험을 대신하지 않는다. 경고2개는 기존 Starlette/Authlib의 httpx 전환 경고다. 전체 Python을 마지막 보완 이후 다시 모두 실행했다고 주장하지 않는다. 마지막 변경의 영향 범위는89+64 시험으로 검증했다. 원본 반영 후 재검증 결과는 최신 진행 표에 기록한다.

처음 실패했던 다중 조회 ledger 충돌, photo DTO 기대값, 동시 메일의 원점 version 충돌은 위처럼 수정 후 재검증했다. 존재하지 않는 과거 시험 파일명을 한 번 지정한 명령은 수집 오류였으며 실제 파일 목록을 확인해 수정했다. 실행 실패를 통과 수에 넣지 않았다.

로그는 이 보고서와 같은 디렉터리의 `stage2-*-tests.log`, 안전한 실측 요약은 [stage2-measurements.json](stage2-measurements.json)에 보관한다. raw 응답·원문·계정 ID·token은 포함하지 않는다.

### 수용 시험 추적

아래 통과는 API/순수 함수/DOM harness 기준이다. 브라우저 및 실제 공급자 품질은 별도다.

| ID | 검증 결과 | 시험 |
| --- | --- | --- |
| D01 | pool200의 마지막 최적 후보가 상위, 입력 역순 결정성 통과 | test_v5_candidate_retrieval |
| D02 | 중복 ID1곳, 두 구획 counts 합집합, 이름만 동일은 병합 안 함 | test_v5_candidate_retrieval, test_v5_result_pages, 기존 canonical contract |
| D03 | 185수신/37고유/148중복, 사용자5회 한도에서 중단 | test_v5_candidate_retrieval |
| D04 | pool200·raw 상한 및 마지막 요청 축소·설정400 초과 거절, GET 호출0 | test_v5_candidate_retrieval, test_v5_result_pages |
| D05 | 동의 없는 숙소 좌표 미전송, 내부 거리/범위 분리 | test_v5_candidate_retrieval, test_accommodations_origin, 기존 location privacy |
| D06 | 상태별 사진/지점 불일치·만료·권한/크기/MIME·TTL 통과 | test_v5_photo_states, test_restaurant_photos, test_public_place_photos |
| R01 | Apify source 비관측 유지, B 연구만 허용, strict 프로필 분리 | test_review_provider, test_stage2_review_contracts, test_v5_review_observation |
| R02 | 번역/추출 불명/rating-only 분리 | test_review_normalization, test_review_metrics |
| R03 | T200/C190/U10/L150/K4 산술 일치 | test_review_metrics, test_review_integration, test_v5_review_observation |
| R04 | K0/U70 strict·all-local 거절 | 기존24합성 metrics 및 general model 시험 |
| R05 | 반복 cursor/부분 실패/역전/날짜 불명 분리 | test_review_provider, test_review_recovery, test_v5_review_observation |
| R06 | 한국어 정답0이면 recall/구간null·품질 gate실패 | test_review_language, test_v5_review_observation |
| R07 | 삭제/정책·링크 철회/late worker fencing·원격정리 | test_review_recovery, test_review_remote_cleanup, test_stage2_review_contracts, test_reliability_* |
| M01 | v5/v6 고정 입력/역순 결정성, snapshot/hash 기록 | test_v5_ranker_evaluation, test_hybrid_recommender |
| M02 | pool 확대와 동일 pool 모델 비교 분리. **실제 확대 품질 효과 미측정** | 마지막 후보 시험, benchmark 입력 hash/같은 pool |
| M03 | 사진만 변경해도 순위 동일, 개인 정보 추천 feature로 사용 안 함 | test_v5_ranker_evaluation, normalization/계정·권한 회귀 |
| M04 | 합성5명 메일5+추천5, 오류/재시도0, latency/queue/RSS/calls 기록 | test_v5_concurrent_workload |
| M05 | 사람 라벨/holdout 부족·도시 방식 미측정에서 v5 유지 | test_v5_ranker_evaluation |

전체 회귀에는 실제 임시 프로세스 SIGKILL/checkpoint 재개, lease 만료/늦은 활성화 거절, 마지막 예산2요청(스레드/프로세스), 동일 key충돌, unknown 과금 보존, 여행 삭제/늦은 완료, SSE 재연결과 기존1단계 U01–U12 자동 시험도 포함된다. 실제 Apify run을 강제 종료해 시험한 것은 아니다.

### 주요 재실행 명령

```sh
PYTHON_DOTENV_DISABLED=1 python -m pytest -q
node --test tests/*.cjs
python scripts/version_web_assets.py --check

PYTHON_DOTENV_DISABLED=1 python -m pytest \
  tests/test_v5_candidate_retrieval.py tests/test_v5_review_observation.py \
  tests/test_v5_pilot_plan.py tests/test_stage2_review_contracts.py \
  tests/test_review_integration.py tests/test_review_recovery.py \
  tests/test_review_language.py tests/test_apify_preflight.py -q

PYTHON_DOTENV_DISABLED=1 python -m pytest \
  tests/test_v5_ranker_evaluation.py tests/test_hybrid_recommender.py \
  tests/test_review_remote_cleanup.py tests/test_v5_concurrent_workload.py \
  tests/test_accommodations_origin.py tests/test_accommodations_api.py -q

PYTHON_DOTENV_DISABLED=1 GOING_LOAD_REPORT=/tmp/going-v5-load.json \
  python -m pytest tests/test_v5_concurrent_workload.py -q
```

측정 결과의 환경과 제한은 [RECOMMENDATION_EVALUATION.md](RECOMMENDATION_EVALUATION.md)를 따른다. 운영 secret을 자동으로 읽지 않도록 테스트에서 dotenv 자동 로딩을 끈다.

## 브라우저 제한

앱 `http://127.0.0.1:8766` 로그인 페이지를 실제 Chrome에서 확인했다. 합성 로그인 이동 대상 `http://127.0.0.1:8767`은 브라우저 도구의 저장된 사용자 접근 차단으로 거절됐다. 사용자가 ‘직접 차단을 풀어 달라’고 요청했지만 해당 설정을 해제하는 API가 제공되지 않는다. 주소/포트 변경, 다른 브라우저 표면, 쿠키 주입, raw CDP 등 우회는 하지 않았다.

따라서 여행/방문일/숙소→두 추천 방식→사진→저장→상세→일정 preview/apply→새로고침은 API/DOM 시험만 완료했다. 로그인 이후 실제 UI·390px/200%·키보드·사진 img load, iOS/Android, 운영 Google 로그인은 **미검증**이다. 준비된 합성 fixture는 `PYTHON_DOTENV_DISABLED=1 python scripts/stage1_browser_fixture.py`로 실행한다. 저장된 차단이 사용자 설정에서 해제된 뒤 같은 주소로 재검증해야 한다. 이것이 출시 검증 완료를 막는 별도 항목이다.

## 독립 플래그와 되돌리기

| 설정 | 기본값 | OFF/복구 동작 |
| --- | --- | --- |
| CANDIDATE_EXPANSION_ENABLED | true | false면 기존 단일 공개 조회. 저장된 run GET은 유지. 이미 대기 중인 다른 계획의 새 run은 version conflict로 새 요청 필요 |
| CANDIDATE_POOL_LIMIT/RAW_RECORD_LIMIT/QUERY_LIMIT/DEADLINE_SECONDS | 200/400/7/120 | 서버 검토 상한 내 낮출 수 있음. 인증/quota 우회 불가 |
| BOUNDED_REVIEW_ENABLED | false | B 신규 연구 제출 차단. true도 consumer 공개 OFF. 과거 관측은 원래 profile로 읽음 |
| PHOTO_COLLECTION_ENABLED | true | false면 새 공용 사진 조사 OFF. 이미 허용된 사진 읽기는 권리/만료 검사를 계속 적용 |
| RANKER_SHADOW_ENABLED | false | v6 비교 진단만 중단. 사용자 기본 모델v5는 그대로 |
| SIMPLE_DISCOVERY_ENABLED | 기존값 유지 | 1단계 화면 계약 재사용. 이번 단계에서 대규모 재디자인 없음 |

플래그를 끄는 것과 이미 시작한 원격 유료 run의 취소는 다르다. 실행 중 job은 기존 취소 API→remote abort/cleanup으로 처리하고 과금 unknown을 유지한다. flag OFF가 환불/삭제 완료를 뜻하지 않는다. 전체 이미지 rollback은 마지막 검증 코드로 별도 수행하며 DB/집계를 삭제할 필요가 없다. 이번 작업은 배포/rollback 자체를 실행하지 않았다.

## 남은 실행 조건

1. 저장된 브라우저 접근 차단 해제 후 전체 사용자 흐름/사진/접근성 직접 검증.
2. 검수 지점과 데이터 이용·원격 보관/삭제 정책, 공급자 build/필드 계약, 현재 가격·기존 승인 한도를 갖춘 소액 파일럿. 지금 상한0이라 실행하지 않았다.
3. 허용 원문을 사람이 독립 라벨한 언어 평가. B를 소비자에 공개하려면 별도 승인/노출 계약까지 연결.
4. 실제60질의 목표의 블라인드 추천 평가. 증거 전에는v5 유지.
5. 별도 disposable PostgreSQL 회귀와 운영 환경 warm/cold 측정·배포. 이번 요청에서 push/배포를 자동 수행하지 않는다.

원본 최종 재실행 명령: `PYTHON_DOTENV_DISABLED=1 /private/tmp/going-stage1-native313/bin/python -m pytest tests/test_v5_candidate_retrieval.py tests/test_v5_review_observation.py tests/test_v5_photo_states.py tests/test_v5_ranker_evaluation.py tests/test_v5_concurrent_workload.py tests/test_v5_pilot_plan.py tests/test_v5_context.py tests/test_v5_result_pages.py tests/test_stage2_review_contracts.py tests/test_review_integration.py tests/test_restaurant_photos.py -q`. 결과는 [원본 Python 로그](stage2-original-python-tests.log), [원본 JS 로그](stage2-original-js-tests.log)에 보관했다. 원본 전체 git diff 검사는 기존 대용량 발표 파일 읽기가 지연돼 중단했고, 이번 변경 범위(.env.example/api/src/scripts/web/tests/docs/service-v5) 검사로 통과를 확인했다. 임시 브라우저 fixture 프로세스는 종료했다.
