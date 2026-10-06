# 실제 장소 후보 조사 기록 — 도쿄·바르셀로나

확인일: 2026-10-01 UTC. 팩 버전: `official-candidate-audit-2026-10-01-v1`.

실제 장소 **도쿄 6곳·바르셀로나 6곳**을 조사했다. 각 도시의 목표 약 15곳에는 미달한다. 음식점 2·카페 1·볼거리 3곳씩이며, 방문자 구성이나 리뷰 언어 분포를 조사한 결과가 아니다. 모든 후보는 운영자 검토 전이고 서비스 추천 준비 완료로 계산하지 않는다.

기계 입력은 [real-discovery-candidates.json](../examples/real-discovery-candidates.json)의 `packs[]`에 있다. 지점 내부 ID·Google Place ID·좌표를 만들어 넣지 않았다. `external_id`는 이 수작업 팩의 안정적인 지점 식별용 slug이며 서버 DB 기본키가 아니다. 관리자 importer가 각 `packs[i]`를 읽고 서버 ID를 생성해야 한다.

## 확인 범위와 사용 상태

- 공식 운영자, 공식 관광 안내, UNESCO 원문을 열어 최소 사실만 한국어로 정리했다. 웹 검색과 일반 문서 조회만 사용했고 유료 Places·리뷰·예약 API는 호출하지 않았다. 사진·리뷰·원문 HTML은 팩에 저장하지 않았다.
- 출처 28개 참조 중 27개는 원문 열람에 성공했다. Can Culleretes 스페인어 페이지 1개는 검색 결과에서 발견했지만 원문 조회가 실패해 `read_confirmed=false`다. 같은 출처의 언어판과 운영자 페이지를 독립 자료 여러 개로 세지 않는다.
- `verified`는 해당 공개 원문에서 확인한 사실이라는 뜻이다. 실제 방문일 적합성이나 이용 허락을 대신하지 않는다. 통상 운영시간·공시 가격은 `provisional`, 특정 방문일은 `null`이다. 검토용 재확인 기한을 확인일로부터 7일로 설정했으며 공급자가 보장한 유효기간이 아니다.
- 공개 페이지를 읽었다는 이유로 재사용 정책 승인을 만들어 넣지 않았다. 모든 source의 `display_permitted=false`이며, 팩 승인과 source별 이용 범위 검토는 분리한다. 자동 크롤링·원문 재배포 허용을 주장하지 않는다. 이 상태의 사실은 일반 사용자 추천 엔진에 사용되면 안 된다.
- 리뷰 수·평점·한국어 비율·현지인 비율은 수집하지 않았다. 관련 엄격 조건과 리뷰 기반 점수는 모두 미지원이다. 지역 문화 맥락과 대표성은 별도 출처 사실이며 주민 선호 확률이 아니다.

## 도쿄 후보

| 후보·원어명 | 지점 식별 | 구획·분류 | 확인한 범위 | 남은 확인 |
| --- | --- | --- | --- | --- |
| 간다 마쓰야 · 神田まつや | 千代田区神田須田町1-13 | 현지 탐색 · 음식점 | [치요다구 관광협회](https://visit-chiyoda.tokyo/app/spot/detail/359)의 주소·소바 업종·통상 시간표 | 식당 자체 사이트 조회 실패. 예약·가격·아동·그룹 인원 미확인 |
| 니혼바시 타마이 본점 · 日本橋 玉ゐ 本店 | 中央区日本橋2-9-9 | 현지 탐색 · 음식점 | [본점 공식 페이지](https://anago-tamai.com/nihonbashi/)의 점심/저녁 구분, 전화로 저녁 예약 접수, 시설 총 25석 | 25석은 한 예약 최대 인원이나 잔여석이 아님. 점심 예약·취소·보증금·인당 가격 미확인 |
| 카페 바흐 · カフェ・バッハ | 台東区日本堤1-23-9 | 현지 탐색 · 카페 | [운영자](https://www.bach-kaffee.co.jp/カフェ・バッハについて/)의 통상 시간·화요일/둘째·넷째 수요일 휴무; [도쿄 공식 미식 가이드](https://www.gourmet.gotokyo.org/article/33.html)의 킷사텐 문화 맥락 | 관광 홍보 출처를 독립 언론 취재로 표시하면 안 됨. 예약·단체·아동 조건 미확인 |
| 센소지 · 浅草寺 | 台東区浅草2-3-1 | 대표 명소 · 볼거리 | [사찰](https://www.senso-ji.jp/guide/)의 본당 계절 시간, [도쿄 공식 일정](https://www.gotokyo.org/en/story/walks-and-tours/tokyo-itinerary/index.html)의 대표 방문지 소개 | 본당 시간을 경내 전체나 기도 접수 시간에 적용하지 않음. 입장료·행사·예약 규칙 별도 확인 |
| 도쿄국립박물관 · 東京国立博物館 | 13-9 Ueno Park, Taito-ku | 대표 명소 · 볼거리 | [박물관](https://www.tnm.jp/modules/r_free_page/index.php?id=113&lang=en)의 소장품 전시 성인 JPY 1,000, 통상 시간·마지막 입장·휴관 예외; [도쿄 공식 일정](https://www.gotokyo.org/en/story/walks-and-tours/tokyo-itinerary/index.html) | 특별전 가격은 별도. 2026-12-22~25 일부 관만 개관 등 방문일·전시실 검증 필요 |
| 하마리큐 온시 정원 · 浜離宮恩賜庭園 | 中央区浜離宮庭園1-1 | 대표 명소 · 볼거리 | [공원 운영자](https://www.tokyo-park.or.jp/park/hama-rikyu/index.html)의 일반 JPY 300·입장 마감·연말 휴원, [역사 안내](https://www.tokyo-park.or.jp/teien/en/hama-rikyu/outline.html) | 행사 연장·임시휴원 확인. 20인 단체 할인은 예약 최대 인원이 아님. 찻집 정원과 정원 전체 인원을 합치지 않음 |

타마이의 본점 정보는 무로마치·긴자·코레도·해외 지점에 복사하면 안 된다. 바흐의 지역 맥락은 운영자와 도쿄 관광기관이라는 서로 다른 출처군으로 남기되, 관측 리뷰나 거주지 자료로 승격하지 않는다. 하마리큐 두 페이지는 같은 운영자 출처군이다.

## 바르셀로나 후보

| 후보·원어명 | 지점 식별 | 구획·분류 | 확인한 범위 | 남은 확인 |
| --- | --- | --- | --- | --- |
| 칸 쿨레레테스 · Can Culleretes | Carrer d’en Quintana 5, 08002 | 현지 탐색 · 음식점 | [공식 연락처](https://culleretes.com/en/contact/)의 주소·예약 전화/이메일, [예약 안내](https://culleretes.com/en/)의 통상 식사 시간 | 영어 안내의 6인 초과와 스페인어 검색 결과의 6인 이상 문구를 추가 대조해야 함. 이를 최대 인원으로 저장하지 않음 |
| 바 라 플라타 · La Plata | Carrer de la Mercè 28, 08002 | 현지 탐색 · 음식점 | [공식 연락처](https://barlaplata.com/ubicacion-y-contacto/)의 주소·점심/저녁 시간·일요일/공휴일 휴무 | 전화번호 존재만으로 예약 가능이라고 하지 않음. 수상·최고 맛집 문구의 독립 출처는 미확인 |
| 그란하 비아데르 · Granja Viader | Carrer Xuclà 4, 08001 | 현지 탐색 · 카페 | [연락처](https://granjaviader.cat/contacte/)의 화~토 시간·일/공휴일 휴무; [매장 소개](https://granjaviader.cat/el-local/)의 동일 지점 맥락 | 연락처 4번지와 매장 소개 4–6번지 표기 차이. 월요일 원문 미기재로 unknown; 자동 휴무 처리 금지 |
| 사그라다 파밀리아 · Basílica de la Sagrada Família | Mallorca 401; 방문 입구 Marina | 대표 명소 · 볼거리 | [상품](https://sagradafamilia.org/en/sagrada-familia-ticket?inheritRedirect=true)의 기본 EUR 26·아동 조건; [운영 안내](https://sagradafamilia.org/en/schedules-how-to-get?inheritRedirect=true)의 계절 시간·입구 | [구매약관](https://sagradafamilia.org/en/general-condition-purchase) 2.1의 10인과 상세 2.3의 최대 9인 표기. 현재 영업 여부·그룹 상품·탑 상품 별도 확인 |
| 구엘 공원 · Park Güell | Barcelona; Carretera del Carmel / Avinguda del Santuari de Sant Josep de la Muntanya 입구 | 대표 명소 · 볼거리 | [공식 안내](https://parkguell.barcelona/en/planning-your-visit/prices-and-times)의 일반 EUR 18·VAT 21% 포함·시간 지정 입장; [입구 안내](https://parkguell.barcelona/en/planning-your-visit/how-to-get-there) | 마지막 판매 시간대를 시설 폐장 시간으로 변환하지 않음. 거주자 전용 시간에 관광객 방문을 배치하지 않음 |
| 카탈루냐 음악당 · Palau de la Música Catalana | Carrer del Palau de la Música 4-6, 08003 | 대표 명소 · 볼거리 | [운영 안내](https://www.palaumusica.cat/en/practical-information_1642451)의 건축 방문 시간·셀프가이드 EUR 20·현장 추가 EUR 2; [소개](https://www.palaumusica.cat/en/discover-the-palau_1633301) | 건축 방문권과 공연권 별개. 매표소·카페 시간을 관람 시간에 쓰지 않음. 보관소 안내가 같은 페이지 안에서도 상충함 |

대표성의 별도 근거로 [UNESCO Works of Antoni Gaudí](https://whc.unesco.org/en/list/320/)와 [Palau de la Música Catalana 등재](https://whc.unesco.org/en/list/804/)를 읽었다. Sagrada의 등재 범위는 탄생 파사드·지하예배당을 포함한 구성 요소이며 성당 전체라고 확대하지 않는다. Park Güell과 Sagrada의 UNESCO 기록은 동일 출처군이다.

## 충돌·미열람·오염된 추출 사례

1. **Sagrada 인원:** 일반 소개의 10인 문구와 같은 약관 상세 구매 규칙의 9인이 다르다. 팩의 `max_party` 두 사실은 `conflict`이며 단일 적합 판정으로 사용하지 않는다. 어느 값도 특정 날짜 잔여석이 아니다.
2. **Sagrada 폐쇄:** 티켓 목록의 추출 본문에는 날짜 없는 임시 폐쇄 fallback 문구가 노출되지만 상품·시간표도 함께 읽힌다. 실제 표시 상태나 적용 기간을 확인하지 못해 `closed=null, status=conflict`다. 운영 안내의 Gaudí House Museum 임시 폐쇄는 별도 장소이므로 성당에 적용하지 않는다.
3. **Can Culleretes 인원:** 영어 페이지 첫 원문 조회는 성공했으나 이후 조회가 불안정했다. 스페인어 페이지는 검색 결과에서만 읽혀 확정 증거가 아니다. 따라서 최대 인원은 unknown이며 두 언어의 임계값은 감사 메모로만 보존한다.
4. **Granja 주소:** 4와 4–6은 같은 운영자·같은 거리의 매장 문맥이다. 이름 일치만으로 모든 외부 플랫폼 ID를 병합하지 말고 정확한 출입 지점과 지도 핀을 대조해야 한다.
5. **Palau 보관소:** 규정 부분은 보관소 없음, FAQ는 Petit Palau 로비 보관소를 언급한다. 장소·서비스 범위가 다를 수 있으므로 수하물 가능을 추천 사실로 추가하지 않았다.
6. **Museu Picasso:** 공식 사이트의 주소·가격 검색 결과는 발견했으나 여러 원문 조회가 실패했다. 언어판 검색 결과 가격도 달라 이번 import 팩에서는 제외했다. 검토 대기 링크: [교통 안내](https://museupicassobcn.cat/en/plan-your-visit/getting-here). 읽지 못한 URL을 verified 사실로 쓰지 않았다.
7. **지역 취재 대기:** Barcelona 시정부의 Can Culleretes·Granja Viader 수상 관련 검색 결과를 찾았지만 원문 PDF/게시물 읽기가 실패했다. 이번 팩의 독립 지역 근거 수에는 넣지 않았다. 향후 원문 검토 후보는 [2024 음식점 안내](https://ajuntament.barcelona.cat/comerc/sites/default/files/2024-06/guia-2024-premis-restauracio-barcelona.pdf), [2022 시정부 발표](https://ajuntament.barcelona.cat/premsa/2022/11/09/els-premis-comerc-de-barcelona-reconeixen-la-tasca-dels-millors-establiments-de-la-ciutat-aquest-2022/)다.

## 자료 부족률

분모는 12곳이다. 특정 여행 날짜·시간·인원 조건을 아직 받지 않은 조사다.

| 항목 | 확인/미확인 | 제품 처리 |
| --- | --- | --- |
| 공식 운영자 또는 공식 관광기관의 지점/주소 원문 | 12/12 확인; 이 중 식당 직접 사이트 미열람 1 | 운영자 검토 후 내부 지점 확정; 좌표·외부 지도 ID는 12/12 미확인 |
| 통상 시간표 | 12/12 조사, 특정 방문일 확정 0/12 | 모두 provisional. 월요일 미기재·계절·특수일·마감 시간 구분 |
| 공개 예약/입장 구매 경로 | 6/12 확인, 6/12 미확인 | 문의·접수·판매 경로만 표시. 완료·가능 인원으로 변환 금지 |
| 한 예약 최대 인원 | 11/12 미확인 + 1/12 충돌 | 확인된 적합 인원 0/12; 필수 조건이면 확인 필요 |
| 총 시설 정원 | 1/12 확인, 11/12 미확인 | 타마이 25석만 보존. 예약 인원에 대입 금지 |
| 예약 오픈 규칙 | 0/12 확인 | 날짜 계산 금지 |
| 현재 날짜·시간·인원의 잔여석 | 0/12 확인 | 전부 unknown, 0석 또는 예약 가능으로 표시 금지 |
| 기준 있는 공시 가격 | 5/12 확인, 7/12 미확인 | JPY/EUR 분리·인당 방문 기준·상품 범위 유지; 미래 결제금액 미확정 |
| 일부 아동 조건 | 5/12 확인, 7/12 미확인 | 나이·상품별 일부 규칙일 뿐 모든 아동 요구 충족 아님 |
| 독립적인 현재 주민 선호·리뷰 언어 증거 | 0/12 | 해당 주장·엄격 리뷰 기능 OFF |
| 사용자 화면에 사용할 출처 정책 승인 | 0/12 | source별 검토 전 소비자 추천 사용 금지 |

각 도시 6/15=40%의 후보 수만 확보했다. 운영자·관광기관 자기소개 위주의 자료이고 Barcelona 식당의 독립 지역 기사 검증이 부족하다. 추천 모델의 결측 성분을 임의 점수로 채우지 않았다. 현지 탐색은 현재 **조사할 음식 문화 후보**, 대표 명소는 **대표성 근거가 있는 검토 후보**다.

## RUNTIME_PROMPTS 적용 시 주의

- §5 지점 확인: 한 사이트의 여러 지점, Sagrada와 Gaudí House Museum, Palau와 Sant Pau를 분리한다. 주소 범위·출입구와 좌표가 없는 상태를 모델이 채우면 안 된다.
- §6 사실 추출: 읽기 성공 여부와 정책 허용을 서버가 검사한다. `opening_intervals`→DTO `opening_hours`, `max_party_per_booking`→`max_party`, `venue_capacity`→`facility_capacity`의 명시적 매핑이 필요하다. max_party와 capacity 간 매핑은 금지한다.
- §7 충돌: 언어판·본문과 FAQ·일반 문구와 세부 조항을 source scope와 함께 비교한다. 같은 페이지도 상충 값이 있을 수 있다. 가장 최근 URL 하나를 고르는 방식으로 해결하지 않는다.
- §9 추천 이유: source 두 개는 독립 지역 매체 두 곳이라는 뜻이 아니다. 관광기관은 관광 안내, 운영자 소개는 자체 설명이라고 표시한다. 이 팩으로 “현지인만 아는”, “한국어 거의 없음”을 생성할 근거가 없다.
- §14 오픈 규칙: 12곳 모두 오픈 규칙 미확인이다. 영업시간·현장 판매 시간·구매 홈페이지가 있다는 사실에서 예약 오픈 날짜를 계산하지 않는다.

## 입력 검증 기록과 다음 검수

JSON 파싱, 현재 `PackInput.model_validate`, 도시별 6곳, 중복 slug 없음, source_key 참조, timestamp 순서, null 가용성, 정책 미승인 보존을 로컬에서 확인했다. 임시 SQLite에서 실제 `DiscoveryService.import_pack`을 호출해 대기 상태인 팩 2개·후보 12개·출처 28개·사실 194개 저장, 같은 팩 재입력 시 duplicate 반환, 미승인 출처 상태의 팩 승인 시 `SOURCE_POLICY_UNVERIFIED` 차단을 확인했다. 실제 개인 데이터베이스에는 import하지 않았다. 이 하위 작업에서는 HTTP API·브라우저 검증을 실행하지 않았으며 서비스 함수 검증과 구분한다. 원문 확인 시각은 2026-10-01이며 이후 파일 검증만 다시 수행했다고 원문 확인일을 갱신하지 않았다.

다음 운영자 검수에서는 source 이용 범위, 지점 지도 핀, 방문일 적용, 파티·아동·식단 필수 조건, 동일 출처군, 상충 사실을 차례로 확인한다. 최소 자료를 갖춘 후보부터 승인하고 나머지를 참고/확인 필요로 남긴다. 15곳을 채우거나 점수표를 완성하려고 수치·리뷰·예약 조건을 추정하지 않는다.
