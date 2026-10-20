# 추천·언어 평가 결과

2026-10-08 · 기본 모델 **hybrid_v5 유지**, hybrid_v6 실험용

현재 실제 사람이 검토한 추천 정답/식당 원문 언어 정답이 없다. 따라서 실제 추천 정확도, 언어 품질, 저장률, 방문 만족도와 모델 개선 효과는 **미측정**이다. 아래 합성 수치는 코드 회귀와 측정 도구의 동작 확인에만 사용한다.

## 기준선과 실험 모델

`hybrid_v5.py` 자체는 수정하지 않았다. 두 모델 모두 학습된 신경망이 아닌 결정적 content/context 모델이며 eligibility와 ranking을 분리한다. LLM은 순위 모델이 아니다.

| 성분 | hybrid_v5 | hybrid_v6 |
| --- | --- | --- |
| 취향 | 명시 태그/허용 공용 cuisine, NFKC+고정 별칭, binary TF·smoothed IDF cosine | v5에 일본어·한국어·스페인어·카탈루냐어 음식 별칭과 근거 있는 cuisine 사실을 추가 |
| IDF | 해당 run의 자격 후보 corpus에서 log((1+n)/(1+df))+1 | 동일 원리, 변경된 feature/corpus hash 기록 |
| 평점 | 동일 플랫폼·도시·유형·척도별 평균 prior, prior count200으로 보정 | 동일. 다른 플랫폼 점수를 무검증 합산하지 않음 |
| 현지어 core | 언어 보수 하한70% + 보정 플랫폼 품질30%, 둘 다 있을 때만 | 동일. 현지어 자격을 famous에서 추론하지 않음 |
| 유명한 곳 | official/platform/editorial 유형 우선순위와 플랫폼 cohort별 순위 | 유형/cohort 내부 순위 후 결정적 round-robin interleaving |
| 근접성 | Haversine 직선거리 d에 1/(1+d/2000), 도시 중심 fallback 명시 | 동일. 도보/실제 경로 시간으로 표시하지 않음 |
| 미확인 | null, 남은 가중치 재분배 없음, 기여값 하한/상한 구분 | 동일 |
| 사진·행동 | 사진 수 제외, 사용자가 선택한 선호만 사용 | 동일. 클릭/노출을 학습 정답으로 만들지 않음 |

가중치는 입력 조건으로 선택한다: 근거만1.0 / 취향은 core.55+취향.45 / 원점은 core.65+근접.35 / 취향+원점은 core.40+취향.40+근접.20. 이는 초기 설계이며 실제 정확도 확률이 아니다. 고정 예약·필수 조건·언어 gate를 점수로 상쇄하지 않는다. 기존 체인/동네 다양성 처리를 재사용한다.

`hybrid_v6`, `content_context_v3`, alias/feature/corpus, 관측 정책, 평가 clock과 후보 사실을 저장한다. `RANKER_SHADOW_ENABLED=true`일 때만 같은 frozen 입력으로 v6 진단을 추가하며 사용자 순위는 v5다. shadow에서도 외부 호출0, LLM 호출0이다. 매 요청200개 embedding 추가 호출은 도입하지 않았다. 임베딩/외부 키가 없어도 v5가 동작한다.

## 무엇을 각각 평가하는가

1. **후보 확보:** Candidate Recall@200의 분모는 독립 관련 지점 전체다. 후보에 없는 정답도 포함한다. 정답 목록이 없거나 관련 정답0이면 null. 이번 실제 recall은 미측정이다.
2. **후보만 확장:** 같은 v5에서60/200 pool을 비교해야 한다. corpus와 평점 prior도 달라질 수 있다. 이번 200번째 후보 시험은 기능적 경계 시험이며 실제 품질 개선 실험이 아니다. 실제 두 도시 확대 효과는 미측정이다.
3. **모델만 변경:** 동일 후보·사실·정책·clock에서 v5/v6 비교. 다른 시점의 새 검색 결과를 모델 개선 효과에 넣지 않는다.
4. **언어 판별:** 독립 원문 라벨로 현지어 precision, 한국어 recall, unknown, 언어별 수와95% Wilson 구간을 평가한다. 계산 fixture24건과 별개다.
5. **설명:** 서버 후보·근거를 벗어나는 설명은 기존 검증/템플릿 복구 경로를 유지한다. 실제 LLM 설명 충실도 점수는 미측정이다.
6. **제품:** 실제 노출 후 저장/일정 채택·방문 경험은 운영 관측이 필요하다. 현재 미측정이며 대기하거나 가짜 결과를 만들지 않았다.

## 실제 실행한 합성 비교

환경: macOS arm64, Python3.13.5, 로컬 엔진, 외부 호출0. 기존 `build_recommender_benchmark.build()`의 **합성 개발 질의12개**, K=5, 각 모델 warm-up 후5회=60표본. 실제 holdout0개다.

| 모델 | 합성 개발 NDCG@5 | 합성 개발 Precision@5 | engine median | engine p95 | 알려진 필수 조건 위반 | 입력 역순 동일 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hybrid_v5 | 0.995557 | 0.533333 | 12.594ms | 13.527ms | 0 | 12/12 |
| hybrid_v6 | 0.995557 | 0.533333 | 12.489ms | 13.934ms | 0 | 12/12 |

동일 합성 점수이며 실제 성능 향상 근거가 없다. p95 비율1.030은 이 소규모 로컬 fixture의 엔진 시간일 뿐200개 전체·네트워크·Render 성능이 아니다. 기존 rubric 내 Recall@5=1은 반환 후보를 기준으로 한 순위 지표다. 독립적인 **Candidate Recall@200은 null**로 남긴다. 과거 hybrid_v4 보고서의 수치를 v5 성과로 복사하지 않았다.

전환 가설: 독립 human holdout≥40개(도시당≥20), NDCG@5 평균 절대+0.03, paired bootstrap95% 구간 하한>0, 각 도시/두 추천 방식 -0.02 초과 퇴행 없음, 필수 위반0, warm p95 악화≤20%. 누락 도시/방식·미라벨 holdout·비결정성은 전환 후보 통과 불가다. bootstrap은 질의 쌍 단위, 2,000회, seed20261008. **현재 pair0, 구간null, promotion_candidate=false, 자동 승격 없음.**

## 사람 평가 도구

[blind-evaluation-plan.json](../fixtures/blind-evaluation-plan.json)은60개 작업칸이다. 실제 질의나 라벨60개가 아니다. 도시별 development10/holdout20, 숙소 원점·음식·가격·무선호·현지어·유명한 곳을 배분했다. 각 항목의 질의/후보/독립 정답/정답 이유는 아직 null이다.

- 검토자는 모델 순위를 보지 않고 조건과 허용 지점 자료를 검토한다. 정답0~3, 관련성 기준≥2.
- 분할을 먼저 고정하고 질의/여행/복제 리뷰 그룹 중복을 검사한다. 지점 겹침은 별도 보고하고 지점 분리 holdout을 권장한다.
- 각 질의에 snapshot, 허용 candidates의 사실/정책 버전, section, judgments, independent_relevant_place_ids를 채운 benchmark fixture를 만든다. 개인정보/원문 리뷰는 공용 Git에 넣지 않는다.
- Precision@5 분모는 항상5다. NDCG gain=2^label−1. 후보 전체 라벨이 없으면 성과 지표null이며 새 모델의 미라벨 top ID를 따로 반환한다.
- 같은 snapshot을 사용해 아래 평가를 재실행한다. 개발 정답을 바탕으로 조정한 후 holdout을 변경하지 않는다.

## 언어 평가

기존 로컬 `lingua-language-detector 2.2.0`, Apache-2.0,75언어를 재사용한다. 최소 신뢰.70, 1·2순위 margin.20, 최소 글자8/라틴단어4 조건이다. binary 크기307,235,408bytes, SHA256 `51fbd102097b5ec99ef3d7d98c27d6b03adf75132e7901059a457caa1e7b7e07`를 기록했다. **파일 크기는 실행 메모리가 아니다.**

허용된 실제 식당 원문 평가 자료가 없어 도쿄/바르셀로나 빈 평가를 실행했다. 각각 count0, 현지어 precision=null, 한국어 recall=null,95% 구간=null, 품질 gate=false다. 한국어 표본이 없어서 통과로 처리하지 않는다. 실제 언어별 오류율·unknown 비율·모델이 올라간 뒤 peak RSS는 미측정이다. 빈 입력 RSS를 언어 모델 전체 메모리 요구량으로 사용하지 않는다.

일반 문장 FLORES corpus용 `scripts/review_language_eval.py --help`를 확인했다. 이번에 corpus 다운로드나 일반문장 성과 재평가는 실행하지 않았다. 일반 문장 점수는 실제 식당 원문 품질 승인에 사용할 수 없다.

## 재실행

프로젝트 루트에서 설치된 Python 사용. 이번 실행 binary는 `/private/tmp/going-stage1-native313/bin/python`이다.

```sh
PYTHON_DOTENV_DISABLED=1 python scripts/prepare_v5_blind_evaluation.py \
  --output /tmp/going-v5-human-work.json

PYTHON_DOTENV_DISABLED=1 python - <<'PY'
import json
from pathlib import Path
from scripts.build_recommender_benchmark import build
value = build()
value['k'] = 5
for row in value['queries']:
    row['snapshot']['result_contract'] = 'discovery_result_v1'
    row['snapshot']['candidate_pool_limit'] = 200
Path('/tmp/going-v5-synthetic.json').write_text(json.dumps(value, ensure_ascii=False))
PY
PYTHON_DOTENV_DISABLED=1 python -m src.recommendations.benchmark \
  --fixture /tmp/going-v5-synthetic.json --models hybrid_v5 hybrid_v6 \
  --repeats 5 --output /tmp/going-v5-model-evaluation.json

# 허용된 실제 원문을 사람이 독립 라벨한 뒤 실행. 아래 파일은 자동 생성하지 않는다.
PYTHON_DOTENV_DISABLED=1 python -m src.research.evaluation \
  --input /path/to/permitted-human-review-labels.json \
  --output /tmp/going-review-language-report.json
```

실제 언어 입력 계약: city, domain=restaurant_reviews, synthetic=false, observation_profile, provider_build/category, development/heldout 배열. 각 원문 row에 text/expected/place_id/original_checked를 둔다. report는 원문을 포함하지 않는다. 새로 추가한 observation_profile은 과거 기본값 strict_source_v1과 별개로 B 평가를 연결한다.

## 동시 사용 측정

합성5사용자가 메일 분석5건+추천5건을 겹쳐 실행했다. 임시 SQLite, 단일 dispatcher, FastAPI TestClient, macOS arm64다.

| 측정 | 표본 | p50 | p95 |
| --- | ---: | ---: | ---: |
| 메일+추천 제출 한 쌍 | 5 | 152.48ms | 255.32ms |
| 저장 결과 GET(5개 병렬) | 25 | 513.51ms | 614.35ms |
| 큐 대기 | 10 | 1943.26ms | 3378.73ms |
| 작업 제출→완료 | 10 | 2457.67ms | 3877.53ms |

전체4.175초, peak RSS353,058,816bytes(약336.7MiB), 오류0, 재시도0. fake parse5/embed5/public-map4, 실제 외부 호출0, 비용$0. 공개 공급자의 cooldown clock을 가속했고 네트워크 지연을 모사하지 않았다. 메일의 여행 version 변경이 숙소 원점 변경으로 잘못 해석되던 결함을 수정한 뒤 측정한 값이다.

Render cold start와 운영 Supabase 지연은 미측정이다. 이 메모리/지연만으로 무료 인스턴스의 충분성을 확정하지 않으며 깨우기 ping/자동 증설은 하지 않았다.
