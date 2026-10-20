# 4단계 구현 프롬프트 — 추천·일정 품질 실측 루프

> 1단계가 끝난 뒤라면 2·3단계와 상관없이 새 세션으로 실행할 수 있다.

- 실행 전에 [PRD](../PRD.md) 10절 결정 5(라벨 시간)를 적어 둔다.
- 이 단계는 중간에 한 번 멈춘다. 묶음 A·B·D를 끝낸 뒤 대표가 라벨을 달고, 같은 세션에 "라벨 완료, 묶음 C 진행"이라고 보내면 평가를 시작한다.

---

너는 고잉의 추천 품질 담당이다. 이번 단계의 산출물은 새 모델이 아니라 **사람 라벨로 hybrid_v5와 hybrid_v6를 비교한 결과와 그에 따른 결정 기록**이다. 결과가 '차이 없음'이어도 정상적인 결과다.

저장소: `~/Desktop/ChatGPT/travel-inbox-rag` (이 저장소 루트)
기획: `docs/service-v6/PRD.md` 2절(G14~G16), 4.4절, 5절 D5

## 0. 먼저 확인해

1. `CLAUDE.md`, `docs/service-v6/PRD.md`, `docs/service-v6/IMPLEMENTATION_STATUS.md`, v5의 `reports/RECOMMENDATION_EVALUATION.md`·`DATA_COVERAGE.md`·`REVIEW_OBSERVATION_ADR.md`, `docs/service-v5/fixtures/blind-evaluation-plan.json`을 읽어.
2. 코드(줄 번호는 2026-10-20 기준):
   - `src/recommendations/benchmark.py`(지표 23~47), `evaluation_v5.py`(승격 규칙 27~38), `hybrid_v5.py`, `hybrid_v6.py`, `service.py`(덜 보기 104~105)
   - `src/discovery/public_places.py`(범주 59·86, 태그 115), `candidate_pool.py`, `models.py`(사실 필드 166~)
   - `src/recommendations/engine.py`(영업시간 158·491), `src/itineraries/constraints.py`(49)
   - `src/product/service.py`·`evaluation.py`·`reporting.py`, `scripts/prepare_v5_blind_evaluation.py`
3. 실제 자료 범위는 운영 DB 읽기라 비밀 설정이 필요하다. 직접 실행하지 말고 아래 명령을 대표에게 넘겨. 결과 JSON을 받으면 그것을 기준으로 진행하고, 못 받으면 2026-10-08 감사값(도쿄 식당 50, 바르셀로나 3)을 '과거 값'으로 표시해 사용해.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/v5_data_coverage.py \
  --env deploy/render-supabase/.env --output /tmp/going-v6-coverage.json
```

## 공통 규칙 — 모든 묶음에 우선

- 비밀 파일을 읽거나 출력하지 마. reset/clean/stash·`git add -A`·push·PR·배포 금지. 로컬 커밋까지만, noreply 이메일 확인.
- 패키지 설치는 목록을 보여 주고 승인 후에만. 유료 외부 호출 금지. 공개 지도 호출은 기존 quota(전역 20회/일·사용자 5회/일·15초)를 넘지 않아.
- **AI는 관련성 라벨이나 언어 정답을 만들지 않는다.** 모델 특징으로 정답을 자동 생성하지 마. 라벨은 대표가 단다.
- 기존 계약 유지: 현지어·유명한 곳·일반 참고 구획 분리, 후보 200·페이지 12, 미확인은 null, 가중치 재분배 금지, 리뷰 소비자 OFF, 기본 모델 v5.
- 승격 규칙을 결과에 맞춰 바꾸지 마. 기본 모델 코드를 바꾸지 마.
- 실행하지 않은 것을 통과로 쓰지 마. 실측·합성을 열로 분리해 기록해.

## 묶음 A — 무료 후보 보강

1. '볼거리' 범주: Photon 공식 API 문서로 관광 계열 태그(예: museum, attraction, viewpoint) 필터가 가능한지 확인해. 가능하면 기존 quota·cooldown·200 pool·checkpoint 안에서 `reference`(주변 장소 둘러보기) 자격으로만 저장해. 유명한 곳·현지어 자격으로 올리지 마.
2. OSM `opening_hours` 파서: 흔한 문법 부분집합만 지원해 — 요일 범위, 하루 여러 시간 범위, `off`, 자정 넘김, `24/7`. 공휴일(`PH`) 등 해석할 수 없는 부분은 unparsed로 남겨.
3. 파싱 결과는 `opening_hours` 사실로 저장하되 상태 provisional, 출처 public_map, 만료는 기존 pack TTL 7일. verified로 표시하지 마.
4. strict 판정은 기존대로 두고, 잠정안과 상세의 '방문 전 확인'에서 '지도 정보 기준 영업시간'으로 사용해.
5. 파서 단위 테스트 30개 이상. 실제 OSM 표기를 본뜨되 합성으로 다시 써.
6. 도시별 범위 보고: 식당·카페·볼거리 수, 영업시간 파싱 성공률, 사진 상태 분포, 새 지도 호출 수와 quota 사용.

## 묶음 B — 라벨 도구

1. 관리자 전용(`admin_research` 범위) 웹 화면 또는 CLI 중 빠르게 라벨을 달 수 있는 쪽을 골라. 고른 이유를 적어.
2. 블라인드: 질의 조건(도시·날짜·원점·취향·추천 방식)과 후보의 허용 사실만 무작위 순서로 보여 줘. 모델 점수·순위·모델 이름은 표시하지 마(테스트로 고정).
3. pooled 판정: 질의마다 v5 top-10 ∪ v6 top-10 ∪ 60 pool v5 top-10 ∪ 무작위 5곳. 0~3점과 짧은 이유. 키보드 단축키로 빠르게 달 수 있게 해.
4. 저장: 질의 snapshot·후보 사실 버전과 함께 `evals/recommendation/labels/`. 공개 장소 자료만 담고, 개인 숙소 좌표는 넣지 마(원점은 공개 동네 좌표나 합성 숙소).
5. 진행률과 남은 예상 시간을 보여 줘. 목표는 blind-evaluation-plan 분배(도시당 development 10 / holdout 20). 자료가 부족한 도시는 확보한 수만큼만 만들고 부족을 그대로 보고해.
6. 평가 코드에 pooled 모드를 추가해. 미판정 후보를 0으로 채우지 말고 `judged@5` 커버리지를 함께 보고해. top-5에 미판정이 있는 질의는 커버리지 부족으로 표시해. 기존 전체 판정 모드는 유지해.
7. 라벨 도구 사용법과 예상 소요 시간을 `docs/service-v6/LABELING.md`에 쓰고 여기서 멈춰. 대표에게 "라벨 대기"라고 보고해.

## 묶음 C — 평가 실행과 결정 (라벨 완료 후)

1. 같은 frozen snapshot으로 v5/v6, 60/200 pool을 비교해. 지표 정의는 기존 그대로: NDCG@5 gain 2^label−1, Precision@5 분모 5, 질의 쌍 bootstrap 2,000회, seed 고정.
2. 승격 판단은 기존 `evaluation_v5.py` 규칙을 그대로 써.
3. `docs/service-v6/reports/RECOMMENDATION_EVAL_V6.md`: 표본 수, 라벨러 1명이라는 한계, 도시·추천 방식별 결과, development에서 조정한 이력, 결정(v5 유지 / v6 전환 후보).
4. 전환 후보가 나와도 코드 기본값은 바꾸지 말고 결정 기록과 플래그 제안만 남겨.
5. 라벨이 목표에 못 미치면 확보한 수로 계산하되 '승격 판단 불가'를 명시해.

## 묶음 D — 피드백 루프

1. '이번 여행에서 덜 보기 / 제외': 기존 피드백 모델을 재사용하고 범위(이번 여행 / 다음에도), 철회, 화면의 '적용 중' 표시를 연결해. 클릭만으로 영구 선호를 바꾸지 마.
2. 저장한 장소 기반 여행 범위 취향: 저장한 장소의 허용 태그를 이번 여행의 취향 입력에 더하는 옵션(기본 OFF, 명시적 선택). 가중치 상한과 진단을 기록하고, v5 점수식은 바꾸지 말고 입력만 확장해 비교할 수 있게 해.
3. '저장한 곳 가까운 순서': 저장된 좌표로 최근접 순서 보기. 외부 호출 0, 직선거리로 표시.
4. 효과 측정:
   - 오프라인은 holdout 라벨로 '저장 기반 취향 ON/OFF'를 비교할 수 있는 질의에서만. 저장 시드는 development에서만 골라 누출을 막아.
   - 온라인 저장률·일정 채택률은 분석에 동의한 사용자 이벤트로만. 표본이 적으면 미측정.

## 묶음 E — (선택·유료) 리뷰 파일럿 준비

1. 기존 `scripts/check_apify_setup.py`, `scripts/plan_v5_review_pilot.py`로 계획만 만들어. 원격 run은 시작하지 마.
2. 대표가 상한을 정하고 직접 실행했을 때 쓸 언어 라벨 화면(문장 단위 정답: 언어, 번역 여부)을 묶음 B와 같은 방식으로 준비하고, 현지어 precision·한국어 recall의 Wilson 95% 구간 계산을 연결해.
3. 승인·실행이 없으면 미실행으로 기록해. 소비자 리뷰 기능은 OFF로 유지해.

## 완료 기준

| ID | 기준 |
| --- | --- |
| S4-01 | 볼거리 범주(가능할 때), 영업시간 파서 테스트 30개 이상, provisional 유지, 도시별 범위 보고 |
| S4-02 | 라벨 도구: 점수 비노출 테스트, pooled 모드, `judged@5` |
| S4-03 | 라벨 수와 평가 결과, 또는 '라벨 대기'와 대표 할 일 |
| S4-04 | 결정 기록, 기본 모델 코드 변경 0 |
| S4-05 | 덜 보기·저장 기반 취향·가까운 순서 테스트, 기본 OFF와 철회 |
| S4-06 | 리뷰 파일럿 계획, 또는 미실행 |

## 보고

- `docs/service-v6/reports/STAGE4.md`: 후보 범위 변화(실측·과거 값 구분), 라벨 진행, 평가표, 결정과 이유, 미실행.
- `docs/service-v6/IMPLEMENTATION_STATUS.md` 단일 최신표 갱신.
- 채팅 보고는 15줄 이내, 맨 위에 대표가 결정할 것의 개수.
