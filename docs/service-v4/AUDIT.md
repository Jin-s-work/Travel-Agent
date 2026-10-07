# 고잉 서비스 심층 감사

2026-10-07 · 기준 commit `e8ea36faadac4912a523ae7bea5f02368d7f1ba1` · branch `codex/private-beta-launch`

**기능 수는 충분히 늘었지만, 핵심 추천 근거와 추천→일정의 연결에 보완이 필요하다.** 새 메뉴를 더 만드는 것보다 현재 결과를 정확하게 찾고, 근거를 이해하고, 선택을 잃지 않고 일정으로 이어가는 작업이 우선이다.

이번 결과는 코드·기존 운영 기록·격리된 합성 실행·공식 문서 조사에 근거한다. 앱 소스 수정·운영 DB 변경·유료 공급자 실행·배포는 하지 않았다. 감사 시작 시 존재하던 `docs/presentation/going-class-presentation.key`의 사용자 수정은 보존한다.

## 핵심 결론

1. 사용자 차별점인 **현지어 리뷰 기반 추천은 단순 OFF 설정 이상으로 연결 작업이 남아 있다.** 공식 카탈로그 지점과 리뷰 수집 지점의 ID를 검증해서 연결해야 한다.
2. **후보가 있는데도 0건이 되는 경로가 재현되었다.** 조건 평가 전 12개 제한과 카테고리를 고려하지 않은 검색 생략이 원인이다.
3. **공개지도 후보는 일정 버튼이 있어도 잠정 일정 접수에서 404가 난다.** 방문 조건 미확인을 장소가 없는 것으로 잘못 처리하고 있다.
4. **저장 추천 읽기가 너무 많은 SQL을 수행한다.** 무료 호스팅 cold start와 별개로 줄일 수 있는 내부 작업이다.
5. **선택 입력인 취향·예산을 비우면 순위가 미완성으로 빠진다.** 입력 없는 일반 모델을 명시적으로 지원해야 한다.
6. 유명 식당 구획은 이미 일부 존재한다. 새로 만들었다고 포장하지 않고 `유명한 곳`으로 식당·카페·명소의 근거 범위를 확대한다.

## 확인 수준

- **재현:** 현재 앱·엔진을 임시 데이터로 실행해 관측.
- **코드 확인:** 명확한 실행 경로가 있으나 실제 사용자·운영 환경에서의 발생 여부는 미확인.
- **과거 운영 기록:** 저장소에 남아 있는 이전 관측. 이번 실측이 아님.
- **미검증:** 환경·권한·실제 자료가 없어 확인하지 못함.

## 발견 목록

| ID | 우선순위 | 발견 | 수준 | 영향/기획 반영 |
| --- | --- | --- | --- | --- |
| D01 | P1 | 공식 후보와 리뷰 지점의 ID 연결 단절 | 합성 API 재현 | 핵심 차별점 연결, 2단계 기준 지점 연결(canonical link) |
| D02 | P1 | 도심 기준 12개 후보 제한 후 거리 평가 | 합성 API 재현 | 숙소에서 가까운 13번째 후보 누락, 1단계 |
| D03 | P1 | 식당 검수 자료가 있다는 이유로 카페 조회 생략 | 합성 API 재현 | 잘못된 0건 판정·검색 보완, 1단계 |
| D04 | P1 | 공개 후보의 잠정 일정 접수에서 404 | 합성 API 재현 | 탐색 → 일정 연결 단절, 1단계 |
| D05 | P2 | 사실(fact) 하나가 만료되면 추천 전체가 NULL | 합성 API 재현 | 영향 범위 제한·안전한 이력 보존, 1단계 |
| D06 | P1 | 선택 입력인 선호·예산 미입력 → 순위 0건 | 순수 엔진 재현 | 기본 순위 모델(general ranker), 2단계 |
| R01 | P1 | 추천 생성·조회의 N+1과 전역 정리(purge) | SQL 계측 재현 | 일괄 처리·읽기 경로 분리, 1단계 |
| R02 | P1 | 무관한 정책(policy) 변경에도 결과가 NULL | 합성 재현 | 관련 근거 목록(dependency manifest), 1단계 |
| R03 | P1 | follower 승격 뒤 유지보수(maintenance) 없음 | lifecycle 재현 | leader 상태와 연동, 1단계 |
| R04 | P2 | orphan 삭제 시 앞의 100개를 반복할 가능성 | SQL 호환 재현, PostgreSQL 미검증 | 정리 작업의 진행성, 1단계 |
| R05 | P2 | 승인 후보의 카테고리 평가 전에 100개로 제한 | 코드 확인 | 규모 확장 회귀 검사, 1단계 |
| X01 | P1 | 현지어 필터가 고급 조건 뒤에 있고 자료 종류가 혼재 | 코드 확인 | 핵심·참고 구획의 실제 분리, 2단계 |
| X02 | P1 | 시각 교정 중복·선택 유실·날짜 우선순위·대상 복구 단절 | 코드 확인 | 입력·맥락 복원, 1단계 |
| T01 | 조사 | 전체 실행 중 메일 테스트 7건의 10초 timeout | 전체 실행에서 실패, 단독 실행에서는 재현되지 않음 | 계측 추가·원인 미확정 |

심각도는 이번 소규모 베타에서의 영향 기준이다. 다른 사용자 데이터 노출·실제 데이터 유실을 이번 감사에서 재현했다는 뜻은 아니다.

세부 파일·줄 번호, 재현 방법, 수용 기준:

- [탐색·리뷰·일정 감사](reports/discovery-audit.md)
- [백엔드 감사](reports/backend-audit.md)
- [UX Before/After/Why](reports/ux-audit.md)
- [메일 시험 timeout 조사](reports/stage1-test-investigation.md)

## 실제 시험 결과

| 실행 | 결과 | 시간/비고 |
| --- | --- | --- |
| 전체 Python | **1032 passed / 7 failed / 15 skipped** | 502.74초, 경고 2건 |
| JavaScript | **124 passed / 0 failed** | 13.07초 |
| 실패 파일 단독 재실행 | **39 passed** | 13.00초, timeout 변경 없음 |
| 전체 import + 선행 12개 파일 실행 순서 재현 | **220 passed / 11 skipped / 823 deselected** | 30.64초 |
| 추가 탐색 감사 재현 | **6 passed** | 2.28초, 결함의 존재를 assert한 결과이며 수정 완료를 뜻하지 않음 |
| 백엔드 진단(probe) | SQL·무관한 정책·lifecycle·sweep 재현 | JSON 보고서, 외부 호출 0회 |
| 브라우저 | 로컬 로그인 첫 화면 확인 | 로그인 서버 접근 거절, 로그인 후 전체 흐름 미검증 |

최초 전체 실행의 실패 7건은 `test_foundation_api.py`에서 최초 업로드 job이 10초 내에 종료되지 않은 timeout이다. 단독 재실행과 실행 순서 재현에서는 통과했지만, 최초 실패의 원인은 확정하지 못했다. 재추출 실패 시나리오 3건도 장애 주입 전 최초 업로드에서 실패했으므로 “교정값 유실”을 재현한 것으로 해석하면 안 된다. 재실행 통과가 최초 실패를 취소하는 것은 아니며, 전체 1039개 통과로 합산하지 않는다.

실행 명령(작업 디렉터리: 저장소 루트):

```sh
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
node --test tests/*.cjs
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_foundation_api.py -q -p no:cacheprovider --durations=20 --tb=short
```

로그·진단 스크립트는 `reports`에 보관한다. 실제 계정 키는 사용하지 않았다. `tests/conftest.py`가 임시 DB·파일·가짜 키·외부 검색 OFF를 설정한다. 건너뛴 15개 테스트는 해당 외부 환경을 검증했다는 의미가 아니다.

### SQL 계측의 의미

| 합성 후보 12개 | DB context/checkout | SQL 문 |
| --- | ---: | ---: |
| 추천 execute | 110 | 667 |
| 저장 결과 get | 45 | 244 |
| 합계 | 155 | 911 |

SQLite trace에는 BEGIN/COMMIT과 executemany의 개별 행 실행이 포함된다. HTTP middleware·접수·SSE/polling은 제외했다. PostgreSQL은 이미 연결 풀(pool)을 사용하므로 이 수치를 신규 TCP 연결 수로 해석하지 않는다. 운영 지연의 원인을 확정하려면 실제 처리 단계(stage), 왕복 지연(RTT), 연결 풀 대기 시간(pool wait)을 계측해야 한다.

## 현재 서비스의 실제 범위

아래 내용은 저장소의 최신 운영 기록 기준이다. 이번에 운영 DB나 로그인을 다시 검증하지는 않았다.

- 100개 도시 지원은 입력·시간대(timezone)·통화 등록부(registry)를 뜻한다. 100개 도시의 현지어 맛집 데이터가 갖춰진 상태가 아니다.
- 공식 검수를 거친 기초 후보는 도쿄 3곳·바르셀로나 3곳·마드리드 3곳이라는 기록이 있다. 날짜별 영업·인원 조건까지 확정된 추천 수와는 다르다.
- 런던 공개 검색에는 50개 지점, 음식점 카드 12개, 방문 적합성이 검증된 추천 0개라는 기록이 있다. “카드가 없는 도시”와 “검증된 추천이 0개인 도시”를 구분한다.
- 이전 공개 요청 52.89초 / 캐시 44.45초는 한 번의 운영 관측이며 p95나 이번 측정값이 아니다.
- 실제 리뷰 수집과 도시별 식당 리뷰의 언어 품질 검증 완료는 확인되지 않았고, 엄격 기능은 OFF로 기록되어 있다.
- 일반 문장의 Lingua 진단에서 한국어 recall 94.5%라는 기록이 있지만, 실제 식당 리뷰에서의 성능을 입증하는 근거는 아니다.
- 자동 ICS·Plan B·오늘 보기·피드백·지출·preview/apply/undo는 기존 구현이다. 신규 기능 목록에 중복 기재하지 않는다.

근거: [공개 검색 검증](../service-v3/reports/going-design-search-validation.md), [공식 식당 자료](../service-v3/reports/official-restaurant-sources.md), [언어 평가](../service-v2/reports/LANGUAGE_EVALUATION.md), [리뷰 운영 안내](../service-v2/REVIEW_RUNBOOK.md).

## 외부 공식 근거

확인일: 2026-10-07. 공식 문서 확인은 실제 유료 수집·계정 잔액·이용 권한 확인을 대체하지 않는다.

1. Places API (New)의 reviews는 관련도순으로 최대 5개다. 이는 해당 API의 한도이며 별도 웹 수집의 상한이 아니다. [Google Places 자료형](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places).
2. 기존에 선택한 어댑터의 공식 리뷰 Actor는 원문·번역·원문 언어의 출력 예시와 리뷰별 수집을 문서화한다. [Apify Reviews Scraper](https://apify.com/compass/google-maps-reviews-scraper).
3. newest·기간·상한·origin·personalData 입력을 확인했다. language는 표시 언어 설정이며 원문 언어 판정으로 복사하지 않는다. [공식 입력 스키마](https://apify.com/compass/google-maps-reviews-scraper/input-schema).
4. 공개 요금표에서 Free는 월 $5의 사용액을 포함하고, Starter는 월 $19에 초과 사용액이 추가되는 방식이다. 리뷰 Actor의 광고상 시작 가격을 Free 계정의 실제 단가로 확정하지 않았다. 이번 metadata 가격 엔드포인트 조회는 성공하지 않아, 이전 문서의 건당 단가를 현재 값으로 재확정하지 않는다. 계정 잔액·시작/저장/읽기 비용·세금·실행 상한은 실행 전에 확인한다. [Apify 요금](https://apify.com/pricing).
5. Render Free는 15분 유휴 후 정지하고 재시작에 약 1분이 걸릴 수 있으며, 로컬 파일은 영구 저장되지 않는다. Supabase 저장 구조를 유지하고 cold/warm 성능을 분리한다. [Render Free](https://render.com/docs/free).
6. 소비자용 Maps 자료와 Maps Platform API 계약은 서로 다른 경로다. 공개 열람이나 수집 업체 결제만으로 저장·파생 집계·공개 표시 권한이 확인된 것으로 취급하지 않는 기존 제품 계약을 유지한다. 이 문서는 특정 사용이 법적으로 허용된다는 판정이 아니다. [Maps 추가 약관](https://www.google.com/help/terms_maps/), [Maps Platform 약관](https://cloud.google.com/maps-platform/terms).

## 미검증·한계

- 브라우저에서 로컬 합성 IdP `http://127.0.0.1:8767`에 접근하는 작업은 자동 승인 검토에서 거절되었다. 우회하지 않았고, 로그인 후 사용자 흐름과 실제 모바일 화면을 이번에 검증하지 못했다. UI에 관한 판정은 코드 감사임을 명시한다.
- 현재 운영 URL에서 인증 후 여행 흐름·실제 메일·실제 리뷰·실제 Supabase 성능 및 삭제를 재검증하지 않았다.
- 일부 SQL 정리 시험은 PostgreSQL 표현을 동등한 SQLite 조건으로 바꾼 시험이다. 실제 PostgreSQL의 실행 계획을 보장하지 않는다.
- 전체 테스트에서 발생한 timeout의 원인은 미확정이다. 별도 실행에서 통과했다는 이유로 원인을 환경 탓으로 돌리지 않는다.
- 실사용 만족도·추천 정확도·저장률 향상은 미측정이다.
- 이 문서 세트는 기획 산출물이다. 여기에 나열한 앱 결함을 이미 수정하거나 배포했다고 주장하지 않는다.
