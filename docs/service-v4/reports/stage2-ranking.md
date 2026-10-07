# 2단계 추천 모델 구현 및 검증

2026-10-07. 운영 리뷰 수집·이용 승인·언어 품질 검증의 완료를 뜻하지 않는다.

## 구현

새로운 추천 run은 `general_v3`와 구획별 고정 모델을 snapshot에 저장한다. 과거 snapshot에 버전 표시가 없으면 기존 v1/v2를 그대로 replay한다. 선호·숙소·예산을 입력하지 않아도 자격 있는 후보는 정렬할 수 있다. `rankable=true`, `score=null`, `score_complete=false`를 분리해 총점의 부재를 0건으로 오해하지 않는다. 기본 모델을 개인화나 학습된 모델로 표시하지 않는다.

- **현지어 리뷰로 찾기**: `local_observed_general_v3`. 품질/이용/지점 gate → 고정 C≥100·U/T≤10% → 언어 비율 → 동일 플랫폼 평점·평가 수 → 방문 제약. L/T 내림차순, (K+U)/T와 U/T 오름차순, ID 순. editorial fallback이 없다.
- **유명한 곳**: `iconic_general_v3`. 공식 대표성, 플랫폼 인기, 편집 선정의 근거 소그룹을 유지한다. 플랫폼 인기는 같은 플랫폼·도시·종류·척도 안에서만 평가 수와 평점을 정렬한다. 언어 검증이 OFF여도 동작한다.
- **주변 장소 참고하기**: `reference_general_v3`. 공개 지도 후보를 별도 보조 구획으로 제공하며 기본 결과 수를 채우지 않는다. cap 12는 카테고리/필수 조건을 검사한 이후에 적용한다.

거리 우선은 명시한 `ordering_profile=nearby`에서만 사용한다. 확인된 휴무·인원·예산·거리 위반은 제외하고 미확인 방문 조건은 `needs_confirmation`에 둔다. 현지어 탭에서 비율 하한/상한 또는 플랫폼 평점/평가 수를 완화하면 새 run에서 `criteria_group=custom_criteria`, `strict_badge=false`를 사용한다. C/U 품질이나 권한·지점 gate를 요청 필드로 변경할 수 없다.

리뷰만 만료/철회되어도 동일 장소의 유효한 유명함과 지도 근거는 유지한다. 해당 local 카드만 숨기며 다른 구획의 과거 리뷰 수치는 제거한다. 출처/지점 자체가 바뀌면 기존 전체 지점 차단을 유지한다.

설명은 서버가 정한 실제 건수·출처·거리만 사용한다. LLM에 요청하지 않는다. 수치 변조, 주민 비율 주장, 새 장소와 순위는 설명 검증기가 거부한다.

## 평가 자료 및 명령

- `fixtures/model-registry-v3.json`: 모델/특징 사전. 최적화된 가중치나 학습 정확도를 주장하지 않음.
- `fixtures/general-v3-frozen-synthetic.json`: 조건·후보·사실·정책·시각 고정 합성 입력.
- `reports/stage2-model-evaluation.json` / `.md`: 같은 입력의 기존/일반 v3 비교. 선택 입력이 없는 합성 자료에서 local 2곳·iconic 2곳을 정렬했고 필수 조건 위반은 0건. 실제 추천 만족도의 증거가 아니다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m src.product.evaluation \
  --fixture docs/service-v4/fixtures/general-v3-frozen-synthetic.json \
  --candidate-version general-v3 \
  --output docs/service-v4/reports/stage2-model-evaluation.json
```

CLI는 JSON과 동일 이름의 Markdown을 같이 생성한다. 순위 변화·특징·결측 이유·다양성·도시/구획 지원 상태·선택 입력 생략·동일 입력 결정성을 기록한다. 외부 조회 0회. NDCG·실제 만족도·A/B 승자는 미측정이다. 설명 계약과 결정적 순위 평가를 분리한다.

## 실제 시험

지원 Python 3.13 임시 환경 `/private/tmp/going-stage1-native313/bin/python` 사용. 기존 `.venv`를 변경하지 않았다. `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHON_DOTENV_DISABLED=1`, `-p no:cacheprovider --tb=short`. API 시험은 `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`를 `http://127.0.0.1:9`로 지정해 실수로 유료 네트워크에 접근하지 않게 했다.

- 엔진·추천 API·지속성·숙소 경로·피드백·공개 지도·첫 일정 preview/apply·intent 회귀 **194 passed, 2 warnings, 43.49s** (`stage2-ranking-tests.log`).
- 마지막 운영 상태 사유 분리 및 설명 검사까지 추가한 순수 모델 시험 **19 passed** (`stage2-general-model-tests.log`). 앞 집합과 중복되므로 합산하지 않는다.
- 첫 API 회귀에서는 5건이 예전 public/editorial local 카드 계약을 가정했고, 1건은 스키마14의 과거 버전 fixture 정리가 필요했다. 새 구획 계약과 마이그레이션 fixture를 반영한 뒤 통과했다. 공개 참고 목록의 불필요한 카테고리 다양성 cap 때문에 12개가 4개로 줄어드는 결함도 발견·수정했다.

브라우저, 실제 데이터 이용 승인, 실제 언어 판별 정확도, 유료 공급자 수집 결과는 이 보고서의 검증 범위에 포함하지 않는다. canonical API 흐름과 공급자 계약 검증은 별도 리뷰 계약 보고서에서 확인한다.

## 통합 감사 추가 검증

실제 후보 파일 `data/iconic-pilot-2026-10-07.json`을 격리 DB에서 일반 관리자 import/source 승인/pack 승인 API로 넣고 사용자 여행·추천·상세 API를 실행했다. `test_stage2_pilot_catalog.py`에서 도쿄 6곳·바르셀로나 6곳(도시별 식당 3·볼거리 3)을 확인했다. 유명함의 근거는 표시되지만 모두 방문 조건 확인 그룹이며, 좌표·영업시간·가격·평가 수·잔여석·이동시간을 새로 만들지 않는다. 엄격 현지어 결과는 0건이다. 다른 사용자 조회는 404, 외부 공급자 호출은 0회였다. 이 실행은 새 라이브 출처 감사를 대신하지 않는다.

감사에서 v3 후보 사전 선별과 경로 후보 선별 일부가 예전 언어 필터를 사용해 유명한 곳을 누락시킬 수 있는 경로를 발견했다. v3에서는 별도 방문 제약 판단을 재사용하도록 고쳤다. 명시적 편집 선정 유형이 기존 `level=city` 보조 필드 때문에 공식 대표 명소로 바뀌는 충돌도 고쳤다.

관련 최종 회귀 **77 passed, 2 warnings, 14.63s**, `stage2-ranking-audit-tests.log`. 앞서 기록한 집합과 중복되며 합산하지 않는다.
