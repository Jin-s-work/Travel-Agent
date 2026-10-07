# 실제 식당 기초 자료 검수 기록

확인일: 2026-10-06. 공식 식당·공식 관광/상인 단체의 공개 페이지에서 지점명·주소·음식 분류·안내 링크와 통상 운영표를 대조했다. 직접 방문, 전화, 예약 슬롯 조회 또는 리뷰 품질 검증은 수행하지 않았다.

## 출처와 범위

| 도시 | 지점 | 확인 출처 |
|---|---|---|
| madrid | 카사 시리아코 / Casa Ciriaco | [출처 1](https://www.esmadrid.com/restaurantes/casa-ciriaco) |
| madrid | 카사 루시오 / Casa Lucio | [출처 1](https://casalucio.es/) |
| madrid | 보틴 / Sobrino de Botín | [출처 1](https://www.esmadrid.com/restaurantes/botin) |
| barcelona | 칸 쿨레레테스 / Can Culleretes | [출처 1](https://culleretes.com/en/contact/) · [출처 2](https://culleretes.com/en/) |
| barcelona | 바 라 플라타 / Bar La Plata | [출처 1](https://barlaplata.com/ubicacion-y-contacto/) |
| barcelona | 세븐 포르테스 / 7 Portes | [출처 1](https://7portes.com/en/) |
| tokyo | 간다 마쓰야 / 神田まつや | [출처 1](https://visit-chiyoda.tokyo/app/spot/detail/359) |
| tokyo | 니혼바시 타마이 본점 / 日本橋 玉ゐ 本店 | [출처 1](https://anago-tamai.com/nihonbashi/) |
| tokyo | 다이코쿠야 본점 / 大黒家 | [출처 1](https://asakusa-shoren.jp/stores/819/) |

## 기록한 사실의 한계

- 3도시 × 3곳, 실제 지점 9곳. 도시 메타데이터 100개 중 나머지 97개 도시의 실제 카탈로그 확보를 뜻하지 않는다.
- 공개 페이지의 최소 사실과 자체 한국어 요약, 출처 링크만 저장했다. 페이지 전문·리뷰 본문·작성자 정보·사진·지도 타일은 저장하지 않았다. 이 검토는 해당 최소 사실 범위에 한정하며 포괄적 콘텐츠 이용 허가 또는 자동 수집 허가를 뜻하지 않는다.
- 좌표·도보 시간·평점·전체 평가 수·가격·실시간 잔여석은 검증하지 않았다. null을 0으로 바꾸지 않는다.
- 통상 영업시간은 provisional이며 7일 확인 기한을 둔다. 미래 방문일에 영업함을 확정하지 않는다. 그 밖의 최소 사실 확인 기한은 30일이다. 만료 뒤 새 확인 없이 시각만 갱신하지 않는다.
- 공식 소개만으로 현지 주민 비율이나 엄격 언어 조건을 충족했다고 판단하지 않는다. 지역 관광/상인 단체 자료와 식당 자체 홈페이지를 구분했다.
- 예약 접수 링크, 총좌석, 특정 인원 전화 안내는 서로 다르다. Can Culleretes 6명 초과 문의 안내를 최대 예약 인원 6명으로 바꾸지 않았고, Tamai 전체 좌석 25개를 1예약 최대 인원으로 바꾸지 않았다.
- Daikokuya 자체 사이트는 응답 지연으로 확인하지 못했다. 실제 읽은 아사쿠사 상인 단체의 지점 페이지를 사용했다. Casa Labra는 식당 공간 휴업 안내가 있어 이번 음식점 팩에서 제외했다.

## 매번 새 데이터를 받아올 수 있는가

가능하지만 현재 구현은 검수 SQL 자료 재사용이다. 추천 중 새 식당을 외부 검색하거나 영업 정보를 자동 갱신하는 기능은 아직 연결되지 않았다. Google Text Search 어댑터는 현재 숙소 지점 식별 용도다.

다음 연결은 저장 자료 먼저 표시 → 없는 도시/분류만 bounded 검색 job → 정확한 지점 대조 → 필요한 사실만 수집 → 별도 결과 갱신 흐름이 적합하다. 동일 요청은 합치고, 최대 장소 수·호출 수·시간·예산을 제한한다. 이미 보이는 목록의 순위를 사용자의 확인 없이 갑자기 바꾸지 않는다. 리뷰 언어 수집은 별도 job으로 수행한다. 이 절은 후속 계약이며 완료 보고가 아니다.

[Google Places 저장/표시 정책](https://developers.google.com/maps/documentation/places/web-service/policies)은 콘텐츠 보관에 제한을 두고 place ID를 예외로 다룬다. Google 응답을 현행 공개 사실 팩에 무기한 복사하지 않고 공급자별 저장 정책과 출처 표시를 별도로 적용해야 한다. [Apify 리뷰 수집기 공식 설명](https://apify.com/compass/google-maps-reviews-scraper)은 URL/ID 기반 별도 수집 작업을 제공한다. 수집량에 따른 시간·비용은 실제 실행 전 확정할 수 없다. 현재 무료 운영의 유료 검색·지도·리뷰 설정은 변경하지 않았다.
