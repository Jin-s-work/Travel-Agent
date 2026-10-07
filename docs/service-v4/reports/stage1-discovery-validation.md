# 1단계 장소·추천·리뷰 검증

2026-10-07. 운영 데이터·실제 메일·실제 리뷰를 사용하지 않았다. 외부 유료 호출과 배포는 하지 않았다.

## 구현한 계약

- 승인 장소의 카테고리를 SQL에서 먼저 걸러 100번째 뒤의 카페가 남도록 했다. 원점에 따른 결정적 공간 정렬과 필수 조건 예비 선별 뒤 최종 평가 100개를 선택한다. 승인/공개 저장 후보 조회는 각각 최대 1,000행이며 수집 호출은 기존 최대 60개를 유지한다. 공개 카드 12개 제한은 엔진의 조건 판단 뒤에 적용한다.
- 검색 생략은 현재 카테고리·방문 조건에 맞는 실재 검수 후보가 요청 개수만큼 있는 경우에만 허용한다. 식당만 있는 도시의 카페 요청을 막지 않는다. 언어 리뷰가 필수이면 공개지도를 대체 리뷰로 쓰지 않는다.
- `_catalog_on(con, trip_id, ..., place_ids=None, origin=None, available_only=False)`는 호출자가 소유권을 확인한 동일 연결에서 승인/공개 후보를 함께 읽는다. 장소 팩, 출처·사실, 사진, 리뷰는 일괄 조회한다. 사진은 최대 2 SELECT, 출처·사실은 2 SELECT, 리뷰 근거는 최대 6 SELECT다.
- 추천 GET은 `trip_places`를 쓰거나 전역 purge를 실행하지 않는다. 연결은 최초 candidate capture 트랜잭션에만 저장한다. 리뷰 단일/일괄 GET과 관리자 run GET도 삭제 작업을 실행하지 않고 현재 권한을 확인한다.
- manifest는 실제 참조 지점/팩 버전·출처/사실·관련 리뷰 policy/aggregate/run·production control을 담는다. 무관한 정책·지점 추가는 저장 결과를 무효화하지 않는다. 이전 전역 `review_guard_token` 형식도 관련 근거 비교로 읽는다.
- 관련 근거가 바뀌면 해당 카드의 수치·사진·파생 주장을 응답에서 숨기고 안전한 나머지 카드를 보존한다. `data_status=stale`, `changed_dependencies`, `withheld_place_ids`를 반환한다. SQL의 원래 결과·후보 snapshot과 사용자 선택은 GET이 NULL로 덮지 않는다.
- 리뷰 읽기는 policy/place/run tombstone, display rights, run/aggregate expiry, place version, production OFF를 즉시 검사한다. maintenance의 실제 삭제 및 미사용 비용 예약 해제는 전달된 leader guard를 쓰고 LEASE_LOST를 전파한다.

## 검증 결과

| 범위 | 결과 | 근거 |
|---|---:|---|
| 기존 관련 API/공개 탐색/사진/리뷰/보존/제품 회귀 | 150 passed | stage1-discovery-targeted.log |
| 신규 장소·SQL·권한 계약, SQLite | 16 passed | stage1-discovery-contracts-sqlite.log |
| 신규 장소·SQL 8개, disposable PostgreSQL | 8 passed | stage1-discovery-contracts-postgres.log |
| 권한·삭제·만료 경계 8개, disposable PostgreSQL | 8 passed | stage1-review-boundaries-postgres.log |
| 마지막 guard 전달 후 원격 정리 회귀 | 6 passed | stage1-review-cleanup-final.log |

신규 시험: 숙소 100m 안의 13번째 공개 후보, 100개 식당 뒤 카페, 검수 식당이 있는 도시의 공개 카페 보완, 무관 policy 추가 시 결과 보존, 후보 6/12/50/100개의 고정 SQL 호출 수, policy/run/place tombstone, run/aggregate 만료, 지점 버전 변경, production OFF, display rights 철회. 권한 경계 시험은 purge를 호출하면 실패하도록 하고, 읽기 쓰기/트랜잭션 수 0과 원래 SQL 집계가 아직 보존되어 있음을 확인한다.

시험 파일: `tests/test_stage1_discovery.py`, `tests/test_public_discovery.py`, `tests/test_recommendation_api.py`, `tests/test_recommendation_persistence.py`. 테스트는 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest ... -q -p no:cacheprovider`로 실행했다. PostgreSQL은 `-p tests.postgres_plugin`을 추가하고 loopback의 폐기 가능한 전용 DB를 사용했다.

## SQL 전후 계측

두 backend에서 application execute/executemany 호출 수가 동일했다. 커넥션 초기화 SQL, HTTP 인증, SSE/상태 polling은 계측 밖이며 executemany는 한 batch로 센다. JSON에는 SELECT/쓰기/트랜잭션·checkout·외부 호출·elapsed·pool wait·queue wait를 구분해 저장했다.

| 후보 | 신규 실행 SQL 이전→이후 | GET SQL 이전→이후 | 신규 실행 checkout 이전→이후 | GET checkout 이전→이후 |
|---:|---:|---:|---:|---:|
| 6 | 406→209 | 135→32 | 74→28 | 27→4 |
| 12 | 592→209 | 231→32 | 110→28 | 45→4 |
| 50 | 1,770→209 | 839→32 | 338→28 | 159→4 |
| 100 | 3,320→209 | 1,639→32 | 638→28 | 309→4 |

이후 신규 실행은 SELECT 157/쓰기 27/명시 transaction 25, GET은 SELECT 32/쓰기 0/transaction 0이다. 이미 완료된 실행 재호출은 전후 모두 SQL 3회/checkout 1회/쓰기 0이다. 계측 범위의 외부 호출은 모두 0이다. 원본은 `stage1-discovery-metrics.json`이다. 이전 감사의 667/244는 fixture·계측 경계가 달라 여기의 수치와 같은 실험으로 간주하지 않는다.

baseline ref는 `e8ea36faadac4912a523ae7bea5f02368d7f1ba1`이며 `GOING_STAGE1_BASELINE_REF`로 변경 가능하다. `GOING_STAGE1_METRICS_BASELINE=1 GOING_STAGE1_METRICS_DIR=<출력 폴더>`로 6/12/50/100 비교 JSON을 다시 생성한다. baseline은 세 서비스 클래스만 고정 ref에서 가져오고 엔진/DB adapter/jobs/fixture는 공통으로 사용한다.

단일 표본의 시간은 운영 성능이나 p95가 아니다. 검증 중 macOS iCloud dataless 의존성/bytecode 읽기 및 pytest 플러그인 로딩 지연이 확인되었다. 기록된 비교 시험은 복구 후 완료했지만 elapsed/queue/checkout 시간은 호스트 I/O 영향을 받을 수 있으므로 SQL 왕복 수 감소와 구분한다.

## 명시적으로 남은 범위

실제 숙소 원점 또는 그 위치에서 계산한 coarse tile을 Photon에 전송하는 변경은 구현하지 않았다. 자동 승인 검토가 목적지와 위치 payload에 대한 명시적 사용자 승인이 없다는 이유로 거절했다. 기존 고정 도심 좌표만 전송하며 카테고리별 query/cache key만 구분한다. `origin_scope_supported=false`, `scope_reason=CITY_CENTER_ONLY`를 반환하고, 원점이 도심 수집 범위 밖이면 `coverage=origin_outside_city_center_scope`와 `PUBLIC_DISCOVERY_OUTSIDE_COVERAGE`로 설명한다. 개인 정밀 좌표나 owner_id를 공개 cache key에 추가하지 않았다.

Photon의 공식 API 자체는 reverse 좌표·반경·OSM category 필터를 지원한다. 따라서 이 제한은 공급자의 기능 부족이라는 주장이 아니라 승인된 전송 범위의 한계다: https://github.com/komoot/photon/blob/master/docs/api-v1.md#reverse

브라우저/시각/itinerary 통합/전체 suite 결과는 루트 구현 보고서에서 별도로 집계한다. 새 리뷰 수집 품질, 실제 외부 공급자 응답, 운영 부하, 운영 배포는 검증하지 않았다.
