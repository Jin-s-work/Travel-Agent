# 2단계 지도·경로 어댑터 조사와 검증

확인일: 2026-10-06. 실제 계정의 Maps API를 호출하지 않았고 새 결제·서비스 활성화도 하지 않았다. 단가 조사, 합성 transport 검증, 실제 제공자 품질 검증은 별개다.

## 구현과 기본 상태

`src/location`은 `GeocodingProvider.resolve(query, city, limits)`와 `RouteProvider.matrix(origins, destinations, mode, departure, limits)`를 제공한다. `Disabled*`, `Fake*`, `GooglePlacesGeocodingProvider`, `GoogleRoutesProvider`가 같은 반환 계약을 사용한다. 생성 팩토리는 설정이 없으면 disabled를 반환하며, fake는 명시적 시험 주입에만 쓰인다.

Google 어댑터는 자격증명만 있어도 켜지지 않는다. 사용·개인 입력 전송·지도 없는 표시·영구 저장·변경 불가 이력 보존을 검토한 permission reference와 버전이 있어야 한다. 현재 운영에는 이 검토가 없으므로 OFF다. `LOCATION_PROVIDER_CONFIG`는 하나의 선택적 정책 JSON 경로이고, 비밀은 선택적 `GOOGLE_MAPS_API_KEY`뿐이다. 일반 사용자가 새 환경변수를 입력해야 무료 기능을 이용하는 구조가 아니다.

무료 동작은 사용자가 허용한 유효 좌표의 직선거리 계산, 좌표가 없는 위치 미확인, 기존 저장 자료 조회다. 운영의 disabled 제공자는 직선거리와 이동시간 null을 반환한다. 명시적인 레거시 `TravelTime(provider=None)`의 도보 추정만 `basis=estimate`인 잠정 가정으로 유지한다. 실제 도보 필터는 이 추정을 검증된 경로로 쓰지 않는다.

## 공식 계약 확인

| 항목 | 확인한 계약과 적용 |
|---|---|
| 지점 후보 | Places Text Search (New), 고정 `places:searchText` endpoint. 응답 필드 마스크는 ID·이름·주소·좌표·지도 링크·도시 구성 요소·출처만 요청한다. wildcard를 사용하지 않는다. 기본 5개·한 페이지로 제한하고 어느 후보도 자동 선택하지 않는다. API 자체의 페이지 크기는 1~20이다. [Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search), [field masks](https://developers.google.com/maps/documentation/places/web-service/choose-fields) |
| 경로 | Routes `computeRouteMatrix`; `originIndex,destinationIndex,status,condition,distanceMeters,duration,fallbackInfo`. HTTP 200이어도 각 element를 검사한다. 상태 오류·구간 없음·중복·누락·fallback·잘못된 수치는 null이다. API 한도는 일반 625 elements, TRANSIT/TRAFFIC_AWARE_OPTIMAL 100, Place ID/주소 waypoint 합계 50이다. 앱은 그보다 작은 후보 10개·30 elements·20초를 기본 상한으로 쓴다. [REST 계약](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRouteMatrix) |
| 경로 시간 | RFC3339 출발 시각, 수단, 방향, ID·version을 보존한다. car는 TRAFFIC_UNAWARE로 요청하며 실시간 교통이라고 표시하지 않는다. 제공자 자체가 wheelchair 충족을 확인하지 않으므로 접근성은 unknown이다. [REST 계약](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRouteMatrix) |
| 표시·보관 | Places/Routes의 ID 저장 예외가 모든 본문·거리·시간의 영구 저장 허가는 아니다. 지도 없이 표시할 때 Google Maps 출처 표시와 공개 약관·개인정보 정책을 확인해야 한다. [Places 정책](https://developers.google.com/maps/documentation/places/web-service/policies), [Routes 정책](https://developers.google.com/maps/documentation/routes/policies) |

현행 서비스별 약관은 Places와 Routes의 좌표 임시 캐시를 30일로 다룬다. Geocoding API의 사용자별 직접 기능용 주소·좌표 보관 예외는 별도 조항이며, 현재 구현한 Places Text Search의 모든 데이터나 Routes의 거리·시간 영구 저장으로 확대 해석하지 않았다. 앱의 변경 불가 추천/일정 이력과 durable receipt까지 포괄할 권한이 확인되기 전에는 실제 어댑터를 활성화하지 않는다. 짧은 TTL을 설정하는 것만으로 이용 허가를 대신하지 않는다. [서비스별 약관 6·14·19](https://cloud.google.com/maps-platform/terms/maps-service-terms)

## 현재 공시 가격과 비용 경계

2026-10-06 공식 글로벌 USD 가격표의 첫 유료 구간(월 무료 범위 초과~100,000건): Text Search Pro **$32/1,000요청**, 월 무료 범위 **5,000**. Compute Route Matrix Essentials **$5/1,000 elements**, 월 무료 범위 **10,000**. 비교 후보인 Geocoding API는 **$5/1,000요청**, 무료 범위 **10,000**이다. 지역·세금·계정 전체 합산 사용량·요청 필드/SKU 변경은 별도 확인이 필요하며, 무료 범위를 이 앱의 무조건 무과금 보장으로 계산하지 않는다. [공식 가격표](https://developers.google.com/maps/billing-and-pricing/pricing)

단가는 코드 상수가 아니다. 기존 `PRICING_CONFIG`에서 provider/sku에 `google_maps/text_search_pro`(`requests`) 또는 `google_maps/route_matrix_essentials`(`matrix_elements`)의 확인일·단가·상한을 별도로 승인해야 한다. 계정 무료 크레딧을 빼고 단가를 0으로 넣어 자동 호출하지 않는다. 현재 설정은 두 어댑터 OFF, 신규 실제 호출 **0**이다.

`MatrixService`는 후보 축소가 끝난 입력만 받으며 상한 초과를 유료 호출 전에 거절한다. 비용은 기존 `ProviderGateway`의 범위 검사·원자 예약·실사용 정산·실패 unknown 처리를 사용한다. SDK 재시도는 없고 요청당 시도는 1이다. 응답을 잃으면 reserved 비용을 환불하지 않고 자동 재호출을 중단한다. 이미 기록된 job receipt는 새 서비스 인스턴스에서도 재사용한다. 만료된 성공 receipt를 최신 경로로 표시하거나 자동 재결제하지 않는다.

메모리 캐시는 사용자·여행 namespace, 방향, 수단, 출발 exact instant/분 bucket, 양쪽 ID/version/coordinate version, provider/adapter/policy, 접근성 입력으로 분리한다. TTL과 권한을 매번 검증한다. 캐시 키는 해시이며 일반 로그에 숙소 주소·좌표를 출력하지 않는다. durable receipt에는 정규화된 근거만 넣고 원 응답 전체를 저장하지 않는다.

## Nominatim 판단

공용 Nominatim은 앱 전체 최대 1요청/초, 식별 User-Agent/Referer와 출처 표시가 필요하다. 자동완성과 개인·기밀 정보 전송은 정책에 맞지 않으며, 책임 있는 운영자의 명시적인 용도 선택이 요구된다. 따라서 기본 무료 자동완성·숙소 검색 fallback으로 연결하지 않았다. 이 제품의 개인 숙소 입력을 전달하는 용도는 현재 승인하지 않았다. [공식 사용 정책](https://operations.osmfoundation.org/policies/nominatim/)

## 합성 시험과 실제 검증 구분

실행 명령:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_location_providers.py tests/test_itinerary_travel.py -q
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_itinerary_recovery.py tests/test_itinerary_api.py -q
```

최종 제공자·이동 회귀 **38 passed (3.62초)**, 일정 API·강제 종료 복구 회귀 **24 passed (15.11초)**. 0도 좌표/범위·NaN/Infinity, 같은 좌표와 같은 지점의 구분, HTTP 200 내 element 오류·누락, candidate/matrix 상한, disabled/예산0의 호출0, 방향·수단·version·접근성 캐시 분리, 사용자 A/B 각 두 여행의 캐시 분리, 삭제된 여행의 캐시 접근 차단·삭제 후 늦은 완료의 게시 차단, 새 서비스 인스턴스 receipt 재사용, 만료 receipt, unknown 비용·마지막 예산 동시성, 기존 TravelTime 호환을 검증했다. 이후 통합 회귀 결과는 `stage2-validation.md`에 기록한다.

Google transport 시험은 `httpx.MockTransport`와 가짜 키만 사용했다. 실제 지점 매칭률·Google 도보시간·wheelchair 적합성·실제 과금 명세와 운영자의 이용 권한은 **미검증**이다. 저장할 수 있는 normalized 어댑터 코드가 있다는 이유로 실제 공급자 운영 준비 완료를 뜻하지 않는다.
