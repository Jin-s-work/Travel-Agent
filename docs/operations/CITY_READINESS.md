# 도시별 실제 자료 출시 판정

2026-10-06, `aefee57` 위 미커밋01~06, 실제 후보팩 `official-candidate-audit-2026-10-01-v1`. 수집 공급자는 공식 시설/지자체 웹사이트이며 Google/Apify 실제 API 수집은0회다. [기계 집계](../service-v2/reports/operations-city-readiness.json), [지점/필드 원본 검수](../service-v2/reports/DISCOVERY_CANDIDATE_AUDIT.md), [후보팩](../service-v2/examples/real-discovery-candidates.json)을 함께 본다.

| 항목 | 도쿄 | 바르셀로나 |
| --- | ---: | ---: |
| 실제 후보 | 6 | 6 |
| 현지 탐색 / 대표 명소 | 3 / 3 | 3 / 3 |
| 상호·주소·공식/관광 출처 대조 | 6 | 6 |
| 사실 verified / provisional / unknown / conflict | 19 / 13 / 64 / 0 | 23 / 17 / 55 / 3 |
| 운영 유효 후보 | **0** | **0** |
| 자료 사용·팩 승인 대기 | 6 | 6 |
| 허용 지도 좌표 없음 | 6 | 6 |
| 현재 시점 만료 사실 | 0 | 0 |
| 방문일별 실시간 잔여석 확인 | 0 | 0 |
| 실제 조건 추천→비교→일정 E2E | not_run | not_run |

현재 시점에 만료되지 않았다는 것은 미래 방문일 운영 확정과 다르다. 원본 사실 확인일은10월1일, 다수 expires_at은10월8일이다. 이번에 페이지가 열렸다는 이유로 모든 필드 checked_at이나 만료일을 연장하지 않았다. 각 도시30곳 목표에 미달하며, 정책상 표시 승인0건인 자료를 서비스에 자동 등록하지 않는다. 따라서 현재 두 도시 모두 실제 추천 지원 완료라고 표시하지 않는다.

## 재확인한 공개 출처

| 지점 | 대조 자료와 이번 확인 범위 | 남은 조건 |
| --- | --- | --- |
| 神田まつや, 神田須田町1-13 | [치요다 관광협회](https://visit-chiyoda.tokyo/app/spot/detail/359), 지점 출처 페이지 접근 | 메뉴 가격·방문일 운영·인원·예약 조건 검수 대기 |
| 日本橋 玉ゐ 本店, 日本橋2-9-9 | [본점 공식](https://anago-tamai.com/nihonbashi/), 주소와 요일별 점심/저녁·LO 구분 | 야간 전화 접수와 실제 가능 인원/슬롯은 별도. 총좌석은 잔여석 아님 |
| カフェ・バッハ, 日本堤1-23-9 | [공식 소개](https://www.bach-kaffee.co.jp/カフェ・バッハについて/), 주소·10:30–18:30·LO18:15·휴무 표기 | 미래 예외 영업/최대 인원 미확인 |
| 浅草寺 | [공식 안내](https://www.senso-ji.jp/guide/), 이번 재열람 실패 | 기존10월1일 근거 보존, 재검수 전 신선도 갱신 안 함 |
| 東京国立博物館 | [공식 방문 안내](https://www.tnm.jp/modules/r_free_page/index.php?id=113&lang=en), 요일별 시간·입장 마감·통상 가격과2026년 말 휴관 | 전시별 별도 요금·방문일 예외 확인 필요 |
| 浜離宮恩賜庭園 | [공원 공식](https://www.tokyo-park.or.jp/park/hama-rikyu/index.html), 공식 페이지 접근 | 출입구·미래 운영 예외·가격 필드별 검수 대기 |
| Can Culleretes, Quintana5 | [공식 연락처](https://culleretes.com/en/contact/), 열람 후 재열람 불안정 | 기존 최대 인원 해석 충돌 보존 |
| Bar La Plata, Mercè28 | [공식 위치](https://barlaplata.com/ubicacion-y-contacto/), 지점·점심/저녁·일요일/공휴일 휴무 | 전화번호가 있다는 이유로 예약 가능 확정 안 함 |
| Granja M. Viader, Xuclà4 | [공식 연락처](https://granjaviader.cat/contacte/), 지점·화~토 시간표·일/공휴일 휴무 | 월요일 및 미래 방문일 예외 미확인 |
| Basílica de la Sagrada Família, Mallorca401 | [공식 방문 안내](https://sagradafamilia.org/en/schedules-how-to-get?inheritRedirect=true), 시설 공식 출처 | 9/10명 구분 해석 충돌·타워/입장 상품·잔여석 미확인 |
| Park Güell | [공식 가격/시간](https://parkguell.barcelona/en/planning-your-visit/prices-and-times), 공식 방문 자료 | 출입구/상품·미래 날짜·잔여석 미확인 |
| Palau de la Música Catalana, Palau de la Música4–6 | [공식 안내](https://www.palaumusica.cat/en/practical-information_1642451), 주소·방문 시간·가이드/자율/현장 가격 구분 | 공연과 투어 상품 차이·변경 가능 시간·인원/잔여석 미확인 |

표는 이번 열람 범위를 설명한다. 모든 출처의 이용 범위·번역·가공·표시 권한을 승인한 기록이나 실제 예약 문의 결과가 아니다. 일본어/스페인어/카탈루냐어 이름과 주소는 원본에 보존한다. 실제 카드 E2E 대신 합성 다국어 긴 이름의 UI 시험만 완료했다.

## 기능별 공개 경계

여행·예약·개인 보관함은 도시에 관계없이 사용할 수 있는 기반이다. 실제 팩이 없는 도시는 화면에 ‘실제 추천 자료를 준비 중’으로 표시한다. 운영에서는 합성팩 활성화, 합성 catalog·상세·지점 매칭을 차단한다. 개발 fixture는 합성임을 명시한다.

엄격 언어 기능은 두 도시 모두 OFF다. 도쿄ja, 바르셀로나es/ca 언어 집합 구현과 합성 계산 검증은 있지만 실제 리뷰6×100 및10×200, 이용 정책, 원문 대조, 음식점 리뷰 독립 언어 평가를 통과하지 않았다. 현지 주민 비율·전체 리뷰 비율·한국어0%를 대신 표시하지 않는다.

기본 개인 서비스의 배포와 전체 추천 제품 R1 출시는 별도 판정이다. 도시 공개 전에는 source별 display 권한, 지점, 방문일 운영·가격·인원 조건, 허용 좌표와 실제 경로, 추천 부족 사유를 검수하고 실제 후보로 상세·비교·일정까지 확인한다. 이 문서의0을 합성 데이터로 채우지 않는다.
