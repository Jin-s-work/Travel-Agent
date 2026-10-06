# 무료 공개지도 도시 탐색 — 2026-10-07

기존 100도시 등록부에 **도심 주변 식당·카페를 요청 시 찾는 경로**를 연결했다. 공식 자료를 검수한 3도시 9지점은 그대로 우선 사용한다. 기존 검수 후보가 없는 등록 도시는 소유 여행의 추천 job 안에서 OpenStreetMap 공개 데이터를 요청할 수 있다. 100도시 전체의 자료 수·정확도·완전성을 검증했다는 의미는 아니다.

## 데이터와 범위

- `src/destinations/centers.json`: GeoNames `cities15000.zip` 원본에서 국가 코드·정식/대체 이름을 대조한 100개 도시 기준 좌표. 정식 이름 일치를 우선하고 동명이인은 인구와 국가를 대조했다. New Delhi는 Delhi가 아닌 GeoNames1261481을 선택했다. 원본 다운로드 URL·SHA256·관측일·각 도시 GeoNames ID·원본 지명·국가·시간대·출처 링크를 보존한다. 앱의 기존 IANA 시간대 계약은 바꾸지 않는다.
- 좌표는 도시의 행정 경계나 숙소 위치가 아닌 **도시 기준점 반경3km**이다. 숙소·사용자 좌표·주소·방문일·사용자 ID는 외부 요청에 넣지 않는다. 이름을 가진 `amenity=restaurant|cafe` node/way/relation의 좌표 또는 중심점을 읽으며 반경을 다시 검증한다. 최대60개 응답 중 기준점 가까운12개까지 후보로 제공한다. 응답 제한 때문에 전체 장소 중 가장 가까운12곳이라고 보장하지 않는다.
- `© OpenStreetMap contributors`, ODbL1.0 원문 링크를 카드/상세/result metadata에 붙인다. GeoNames CC BY4.0 출처도 result metadata로 제공한다. OSM 객체 링크를 기본 링크로 제공하고, OSM의 웹사이트 태그는 **공개지도에 기록된 링크**로만 보존한다. 링크 대상 내용을 자동 요청하거나 공식 사이트 확인 완료로 표시하지 않는다.
- `provider=openstreetmap`, `identity_status=needs_confirmation`, pack/candidate=`public_data`, 모든 태그 사실=`provisional`. 지점 검수·공식 확인·영업·평점·리뷰 수·인원·가격·예약 가능 여부를 만들어내지 않는다. 좌표는 공개지도 관측치다. 엄격 리뷰 정책·지도 경로 공급자·유료 ProviderGateway는 활성화하지 않는다.
- 공개지도 후보는 항상 점수 null/방문 전 확인이다. 기존 확인된 조건 위반(사용자 제외·거리 등)은 제외한다. 엄격 리뷰 언어 필터가 켜져 있고 캐시가 없으면 쓸 수 없는 새 공개지도 호출을 생략하고 조건 수정 안내를 표시한다. 기존 평점 필터의 기본값을 바꾸지 않으며 평점 미확인은 통과로 바꾸지 않는다. 랜드마크 후보로 분류하지 않는다.

## 실행·저장·한도

- production에서는 기본 활성화, development에서는 기본 비활성화. `PUBLIC_DISCOVERY_ENABLED=1`/`true`는 명시 활성화, `0`/`false`는 새 호출 비활성화. 테스트는 conftest에서 비활성화하고 필요한 시험에만 fake fetcher를 명시 주입한다.
- 기존 추천 job의 인증·소유권·session·trip version·lease·취소 guard를 요청 전과 원자적 저장 전에 확인한다. 기존 GET, 자동완성, 키 입력, 보관함 저장, 새로고침에서는 네트워크를 호출하지 않는다. 캐시가 만료되면 이전 결과를 최신으로 제공하지 않는다.
- 고정 HTTPS `https://overpass-api.de/api/interpreter`만 사용. 사용자 URL·검색 문자열을 요청 URL에 사용하지 않는다. 기존 안전 fetcher의 public DNS/IP 검사·IP pinning·TLS 검증을 사용하고 redirect0, response body350,000bytes, 전체15초, 쿼리10초/16MiB, 응답60개 상한. SDK·전송 계층·애플리케이션 자동 재시도 없음.
- 도시별7일 SQL 캐시(결과0도 포함), 실패/중단15분 cooldown. 사용자당 UTC 하루5회, 전체 UTC 하루20회, 전역15초 간격. 최대 응답 본문 합계7MB/일. 성공/오류/미완료 호출 모두 건수 한도를 소비한다. 서로 다른 job/사용자의 동시 조회는 기존 `BEGIN IMMEDIATE`와 PostgreSQL advisory lock으로 예약을 직렬화한다. 외부 호출 중 DB write transaction을 유지하지 않는다.
- 무료 작업만 `provider=openstreetmap`, SKU=`public_city_restaurants`, policy=`osm-public-beta-v1`로 기존 usage_reservations/usage_ledger에 별도 명시 기록한다. calls/response_bytes 단가0, 비용0. 지불 가능한 공급자·사용자 지정 endpoint·모델 호출로 확장할 수 없다. 기존 USD halted/예산 정책을 수정하지 않는다. 운영 mode/external_enabled/disabled_providers의 새 외부 호출 중지 정책을 따른다.
- schema12 기존 테이블에 source/candidate/fact/cache를 저장하므로 DDL·migration 없음. 12개 카드의 source/fact 읽기는 bulk3회, trip_places와 public cache 저장은 executemany를 사용한다. 공개지도 후보의 리뷰 조회와 사진 승인 SQL 반복을 생략한다. 공유 공개 데이터와 소유 여행·메모·추천 snapshot의 경계를 유지한다.
- 빈 결과/서버 지연·오류/한도 소진은 별도 상태와 재시도 또는 직접 저장 동작을 제공한다. 공개 서버의 가용성 보장은 없다. 검수되지 않은 합성 장소 fallback은 없다.

## 검증

- SQLite: `PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q tests/test_public_discovery.py tests/test_recommendation_api.py tests/test_discovery_foundation.py tests/test_discovery_intents.py tests/test_destinations.py` →82passed/2warnings(23.09s), 이후 curated/strict 게이트 추가 검증은 실행 기록에 추가한다.
- PostgreSQL17 임시 loopback 컨테이너, 위5개 집합 + `-p tests.postgres_plugin` →81passed/1skipped/2warnings(64.57s). skip은 기존 SQLite 전용 시험이며 이번 공개 데이터 경로는 통과했다. 운영 Supabase에 시험 데이터를 쓰지 않았다.
- HTTP 소유 여행→추천 job→공개지도 상세→장소 저장→캐시 재사용→새로고침 복원. 외부 사용자404, 엄격 리뷰/거리 조건 보존, 읽기 시 호출0, 실제 paidhalt유지·0비용ledger, 취소 시 늦은 cache/result 저장 방지, 만료 시 stale 처리, 오류 cooldown·사용자5회 한도, 12개 카드 상한, 100도시 좌표/쿼리/표준 응답 정규화를 오프라인으로 검사했다.
- 실제 무료 공개 요청4개: Paris60개/32,833bytes/5.30s, London60개/30,577bytes/3.18s, Seoul60개/23,460bytes/2.52s. New York은10.32s 뒤 HTTP_UNAVAILABLE. 초기 transport 확인용 Paris 소규모 요청1개를 별도로 수행했다. 요청 사이15초 이상 간격을 두었으며 대량100도시 선조회는 하지 않았다. [실제 응답 관측 요약](public-discovery-smoke.json)은 이름·좌표·출처 표본이며 현재 영업 또는 품질 검증이 아니다.

## 공급자 원문과 운영 한계

- [Overpass 공식 commons](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html): 공유 공용 서버의 부하 제한과 앱이 이를 상시 backend로 의존하는 문제를 명시한다. 이 구현은 소규모 private beta의 낮은 호출 상한·캐시·가용성 저하 안내를 전제로 한다. 공개 서비스 규모 확대에는 별도 자체 instance 또는 사용 조건이 맞는 데이터 공급 경로를 먼저 준비해야 한다.
- [OSM Overpass API 안내](https://wiki.openstreetmap.org/wiki/Overpass_API): 반복 이용 시 더 낮은 일100회/10MB 수준 권고. 현재 상한은 이보다 낮다. 이는 공급자 SLA나 모든 호출의 허가·응답 보장이 아니다.
- [OpenStreetMap 저작권/ODbL 안내](https://www.openstreetmap.org/copyright), [ODbL1.0](https://opendatacommons.org/licenses/odbl/1-0/): 출처 및 라이선스 표시를 유지한다. 사진·리뷰 본문·지도 타일은 이 경로에서 수집하지 않는다.
- [GeoNames export와 조건](https://www.geonames.org/export/), [원본 dump 안내](https://download.geonames.org/export/dump/readme.txt): 무료 추출 데이터, CC BY attribution, 정확도/완전성 보증 없음. runtime geocoding/Nominatim 호출이나 새 계정은 사용하지 않는다.
