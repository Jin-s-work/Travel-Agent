# 현재 상태와 고도화 근거

검토일: 2026-10-08. 코드 기준 `e9f2c26`, 최근 앱 배포 기록 `2da85c0`. 공개 저장소와 원본 작업 폴더의 변경 상태를 구분했다. 원본에는 기존 수정이 많아 reset·덮어쓰기를 하지 않았다.

## 확인 범위

- 인증·추천·홈·공개지도·사진·리뷰 계약 관련 코드와 v3/v4 진행 기록을 읽었다.
- 실제 배포 브라우저는 로그아웃 상태였다. 로그인 이후 전체 흐름을 이번 기획에서 새로 시험했다고 주장하지 않는다. 지난 배포 확인 및 기존 화면을 보조 자료로 사용했다.
- 외부 수집·유료 호출·데이터베이스 변경·배포는 하지 않았다. 실제 추천 만족도는 측정하지 않았다.
- Node의 홈 후보 선택과 Python의 상태 변환 순수 함수에 합성 입력을 주어 두 동작을 재현했다. 전체 회귀 시험은 실행하지 않았다.

## 확인한 동작과 제품 영향

| 항목 | 코드 근거 | 관찰 또는 추론 | 우선순위 |
| --- | --- | --- | --- |
| 홈에서 유명한 곳이 먼저 채워짐 | `web/js/home.js`의 `previews`, 순서 landmark/local_discovery/reference, 마지막 slice(0,3) | 유명 후보 3개와 유효한 현지어 후보 1개를 넣으면 홈은 유명 후보 3개만 반환. 현지어 차별점이 가려질 수 있음 | P0 |
| 여러 원인이 하나의 상태로 축약됨 | `src/recommendations/general.py:_state` | OFF와 UNKNOWN을 같이 넣으면 `quality_insufficient`. 사용자가 설정 미지원과 품질 부족을 혼동할 가능성 | P0 |
| 검색 후보가 도심 범위로 제한됨 | `src/discovery/public_places.py:query`, MAX_CANDIDATES=60, scope=city_center | 숙소 기준 순위가 있어도 후보가 도심에서만 왔다면 먼 숙소 주변 식당을 놓칠 수 있음. 실제 누락률은 미측정 | P0 |
| 사진이 없는 이유가 공급 범위에도 있음 | `src/discovery/photo_provider.py:linked_titles`, Commons 허용 라이선스, 정확한 OSM 지점 연결 | 로딩 UI 수정만으로 음식·실내 사진이 생기지 않음. 지점 일치와 사진 제공 경로 보강 필요 | P0 |
| Apify 키와 엄격 기능의 조건이 다름 | `src/providers/apify_reviews.py` source_pagination_observable=False; `src/research/contracts.py` ADAPTER_SOURCE_PAGINATION_UNOBSERVABLE | 현재 어댑터는 Google 원천 페이지 연속성을 입증하지 못함. 키만 입력해 strict_ready로 바꿀 수 없는 구조 | P0 |
| 모델은 있으나 실제 관련성 평가는 없음 | `src/recommendations/hybrid_v5.py`, 기존 hybrid-v4 개발 benchmark | TF-IDF·보정 평점·직선거리의 비학습 모델. 이전 합성 개발 점수를 v5 실서비스 정확도로 옮겨 적으면 안 됨 | P0 |
| 유명한 곳 정렬에 근거 유형 우선순위가 있음 | `hybrid_v5.py:order_key`의 official/platform/editorial prefix | 개인 취향 점수가 높아도 다른 근거 유형을 앞지를 수 없음. 버그로 단정하기보다 묶음별 순위와 화면 배합을 평가할 것 | P1 |
| 후보 집단 변화가 점수에도 영향 | `hybrid_v5.py:prepare`의 실행별 IDF·평점 prior | 새 후보 추가 때문에 기존 점수가 달라질 수 있음. 검색 개선과 모델 개선의 실험을 분리해야 함 | P1 |
| 문서의 ‘최신’ 표기가 누적되어 혼란 | `docs/service-v4/IMPLEMENTATION_STATUS.md`의 상단 v4 모델 기록과 하단 후속 배포 기록 | 실행자가 오래된 OFF/배포 상태를 현재 상태로 오인할 가능성. v5는 최상단 단일 상태 표 유지 | P1 |

## UI 검토

기존 스타일을 재설계하지 않고 행동과 정보 순서를 바꾼다. 아래 After는 제안이며 구현 완료가 아니다.

| Before | After | Why |
| --- | --- | --- |
| 홈의 서로 다른 근거 후보를 한 목록으로 축약 | 현지어 리뷰/유명한 곳을 짧은 두 구획으로 제공, 둘러보기는 별도 | 사용자가 어떤 방식으로 고른 장소인지 이해 |
| 여행·출발점·날짜·분류·방식·필터가 여러 영역에 반복 | 여행 맥락 한 줄과 발견 방식, 음식 종류만 우선 노출 | 입력 수보다 판단할 항목 수를 줄임 |
| 처음부터 준비 중인 구획에 들어갈 수 있음 | 이용 가능한 시작 화면, 준비 중 구획에는 한 문장과 대안 | 빈 화면에서 이유를 해석하는 부담 감소 |
| 필터 변경과 다시 찾기 동작의 차이가 불분명 | 보기 전환은 저장 결과, 조건 변경은 명시된 1회 요청 | 중복 실행과 결과 맥락 혼란 감소 |
| 모든 미확인·탈락 이유를 사용자 화면에 나열 | 선택에 직접 영향을 주는 1개와 ‘자세히’, 운영 원인은 관리자 화면 | 사실을 숨기지 않으면서 경고 피로 감소 |
| 저장·일정 선택의 상태가 여러 화면에 흩어짐 | 카드의 저장 상태 통일, 저장한 장소에서 일정 미리보기로 연결 | 다음 행동을 이해하기 쉬움 |
| 사진 없는 카드도 큰 빈 사진 영역을 유지할 수 있음 | 사진 없음 확정 시 텍스트 중심 카드, 로딩 중에만 골격 표시 | 없는 사진을 계속 기다리게 하지 않음 |

## 공식 문서 확인

2026-10-08 확인. 아래는 기술 계약 참고이며 데이터 이용 권한 검토를 대신하지 않는다.

- [Apify 입력 계약](https://apify.com/compass/google-maps-reviews-scraper/input-schema): 최대 수·최신순·날짜·Google 원천을 지정하고 개인정보 출력을 끌 수 있다. `language`를 원문 언어로 간주하지 않는다. 외부 Actor 실행 전 최신 build와 실제 필드를 재확인한다.
- [Render Free](https://render.com/docs/free): 유휴 상태에서 중지될 수 있어, cold start와 warm 요청 목표를 분리한다. 서버가 잠든 동안 내부 dispatcher가 일정에 맞춰 실행된다고 약속하지 않는다.
- [Google Place Photos](https://developers.google.com/maps/documentation/places/web-service/place-photos): 사진 이름의 만료·캐시 제약과 저작자 표시 요구가 있다. 도입 시 기존 Commons 영구 캐시 정책을 그대로 적용하지 않는다.

최근 계정 확인 기록의 Apify Free 월 크레딧·상한은 각각 $5지만 이번에는 계정 잔액을 다시 조회하지 않았다. PRD의 비용 상한은 제안이며 실제 호출 전 현재 사용량과 승인 범위를 다시 검사한다.
