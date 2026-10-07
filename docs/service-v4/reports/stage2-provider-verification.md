# 리뷰 공급자 확인과 실행 준비

확인일: 2026-10-07. 공개 문서와 인증 없는 metadata GET만 확인했다. 계정 잔액 조회, Actor 실행, 리뷰 수집, 결제는 하지 않았다. 로컬 설정 두 파일에 APIFY_TOKEN 항목은 없었다. 실제 값은 출력하지 않았다.

## 공급자와 비용

대상은 장소 검색용 Actor가 아닌 [`compass/google-maps-reviews-scraper`](https://apify.com/compass/google-maps-reviews-scraper)다. [공식 가격표](https://apify.com/compass/google-maps-reviews-scraper/pricing)의 Free 요금은 리뷰 1,000개당 USD 0.60, 실행 시작당 USD 0.00005이며 Actor 플랫폼 사용량은 포함으로 표시되어 있다. Starter는 월 USD 19와 리뷰 1,000개당 USD 0.45다. 최저 광고 가격 USD 0.30을 Free 단가로 사용하지 않는다.

[Apify 요금제](https://apify.com/pricing)는 Free 월 USD 5 크레딧을 안내한다. 이 사용자의 잔액·공유 계정 사용량·결제 수단·세금은 미확인이다. 유료 플랜 가입이나 추가 결제가 필요하다고 결정하지 않았다.

| 단계 | 요청 상한 | 현재 Free Actor 단가로 계산한 구성요소 추정 |
| --- | ---: | ---: |
| A | 6개 지점 × 100 records / 6 runs | USD 0.36030 |
| B | 10개 지점 × 200 records / 10 runs | USD 1.20050 |
| A+B | 2,600 records / 16 runs | USD 1.56080 |

이 값은 성공 1회씩의 리뷰·시작 이벤트 계산이며 총 청구 보장이 아니다. 별도 API·보관·재실행·응답 유실 비용과 계정 조건은 실행 전에 대조한다. 기존 USD 3 실험 상한 제안은 승인이나 현재 가격 설정이 아니다. OpenAI 허용을 Apify 허용으로 전환하지 않는다. `PRICING_CONFIG`에 현재 검토 단가를 넣고 cleanup 비용까지 예약한 뒤, 별도 승인된 전체 한도 안에서만 실행한다.

## build와 계약

인증 없는 [Actor metadata API](https://api.apify.com/v2/acts/compass~google-maps-reviews-scraper)에서 latest는 `0.0.528`, build ID `Thi4eHCCVpONALGyk`, 완료 시각 `2026-10-02T11:54:19.855Z`였다. latest 태그는 바뀔 수 있으므로 이 값은 런타임 승인으로 자동 입력하지 않는다. 공식 기본값 timeout 36,000초와 비용 상한 null도 복사하지 않는다.

[입력 계약](https://apify.com/compass/google-maps-reviews-scraper/input-schema)에서 최신순, 날짜 경계, 최대 기록 수, Google 원천 선택, 개인정보 제외 옵션을 확인했다. 서비스는 180일·200고유 기록·20개 dataset 페이지·페이지당 2시도를 고정 상한으로 사용한다. 언어·키워드·별점으로 골라 수집하지 않는다. 날짜 경계는 UTC이며 웹사이트 locale은 원문 언어가 아니다.

[공식 출력 예시](https://apify.com/compass/google-maps-reviews-scraper)에는 text/textTranslated/originalLanguage/publishedAtDate/reviewId가 있다. 이 문서만으로 실제 원문 분리 정확성, 발행일/수정일 중 newest 기준, Google 원천 페이지 연속성, 공급자 내부 재시도 상한을 검증할 수는 없다. dataset offset은 Google 원천 cursor가 아니다. API의 최대 5개 리뷰 제한도 웹 수집의 기술적 상한이 아니다.

따라서 **문서 확인 완료 / 실제 필드 대조 미실행 / 이용 범위 미승인 / 언어 품질 미검증 / strict OFF**다. 승인된 build·adapter·원문·정렬·연속성·기간·가격 증거를 런타임에 전달하는 코드는 구현하지만 default false를 true로 바꾸지 않는다.

## 실제 파일럿을 재개할 조건

1. 준비한 canonical 지점과 Google 외부 ID를 정상 관리자 연결 미리보기에서 대조한다. 이름만으로 합치지 않는다.
2. 현재 계정 잔액과 Actor 과금 화면, 허용 이용 범위, 원격 보관·삭제 범위를 기록한다.
3. build별 계약 보고서와 hash를 등록한다. 연속성/정렬을 입증하지 못하면 수집을 시험해도 strict를 켜지 않는다.
4. A의 결과·원문 대조·실제 비용을 검토한 뒤 B를 별도로 진행한다. 실패와 unknown charge는 예산을 환불한 것으로 처리하지 않는다.
5. 도시별 실제 리뷰 100개 이상, 원문 대조 20개 이상, 한국어 정답/현지어 정답/현지어 판정 각각 50개 이상과 독립 평가를 충족해야 품질을 검토한다. 한국어 보충 평가 자료는 장소 관측 분포에 넣지 않는다.
6. 실제 자료가 없으면 품질 및 제품 ON 상태는 unavailable/not_run을 유지한다. 이번 개발의 합성 통과 수를 실제 언어 정확도로 사용하지 않는다.

## 언어 프로필 출처

- 도쿄 ja는 [공식 일본어 관광 안내](https://www.gotokyo.org/book/wp-content/uploads/2026/03/AO2_2603_tg_low_JP.pdf)를 참고한 초기 제품 프로필이다. 모든 도쿄 거주자의 언어를 대표한다는 뜻이 아니다.
- 바르셀로나 es+ca는 [시청의 정착 안내](https://www.barcelona.cat/internationalwelcome/sites/default/files/10-tips_EN.pdf) 및 [카탈루냐어 사무국](https://ajuntament.barcelona.cat/presidencia-relacions-internacionals-educacio-salut-drets-humans-cicles-de-vida/es/oficina-del-catalan)을 참고한다.
- 100개 도시 선택 기능과 검증된 언어 추천 능력은 별개다. 현지어와 ko가 겹치는 프로필은 초기 계약에서 unavailable이다.
