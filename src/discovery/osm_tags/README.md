# OSM 태그 표 (ODbL)

공개 Photon 은 음식 종류·영업시간을 주지 않아서, 공개 지도 후보를 저장할 때 이 표로 빈 태그만 채운다(`src/discovery/public_places.py` `tag_table`). 앱 실행 중 외부 호출은 없다.

- 자료: © OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) — [저작권 안내](https://www.openstreetmap.org/copyright). 이 표는 OSM 에서 파생한 데이터베이스라 같은 ODbL 이 적용된다.
- 원본: Geofabrik 지역 추출본(`download.geofabrik.de`). 파일 이름·sha256·생성 시각은 각 JSON 의 `source`·`generated_at` 에 있다.
- 범위: 앱이 조회하는 7개 공개 지역(`candidate_pool.public_areas`) 안의 식당·카페·볼거리. 남기는 태그는 `public_places.OVERPASS_KEYS` 뿐이다.
- 갱신: 새 추출본을 받아 `python scripts/build_osm_tag_table.py --city <도시> --pbf <파일>` 를 다시 돌린다(도쿄 = 관동 `asia/japan/kanto`, 바르셀로나 = 카탈루냐 `europe/spain/cataluna`).
