# 리뷰 관측 계약 결정 기록

2026-10-08 · 2단계 코드 준비 완료 · 실제 수집/공개 승인 미완료

## 결정

기존 `strict_source_v1`과 연구용 `bounded_observation_v1`을 별도 계약으로 유지한다. 현재 Apify 어댑터는 Google 원천 페이지를 관측하지 못한다. dataset offset을 원천 cursor로 바꾸거나 `source_pagination_observable=False`를 True로 바꾸지 않았다. **기존 strict도 새 bounded도 이번 작업으로 소비자 공개하지 않는다.**

| 항목 | A: 기존 strict | B: bounded_observation_v1 |
| --- | --- | --- |
| 설명 대상 | 검증된 정렬·연속성을 갖는 공급자 관측 창 | 실제 반환되어 개별 발행일을 확인한 기간 내 고유 기록 |
| 원천 페이지/내부 재시도 | 검증 필요 | 관측 불가이면 null. dataset 읽기 제한과 별도 |
| 기간 경계 | 검증된 발행/수정 정렬 필드 사용 | 정확한 발행일로 포함 여부만 판정. 순서 불명에서는 날짜 경계로 조기 종료하지 않음 |
| 날짜 불명 | 품질 미통과 사유 | 기간 집계에서 제외하고 ambiguous_date_count 기록. 수신 고유 기록 상한에는 포함 |
| 설명하면 안 되는 것 | Google 전체 전수, 현지 주민 비율 | 앞의 두 주장과 ‘최신 200개’, 원천 연속성 보장 |
| 소비자 자격 | 기존 strict gate 전부 필요 | 별도 관리자 정책·이용·원문·언어 평가 승인 필요. 이번 버전은 연구 출력만 제공 |
| 현재 판단 | Apify 원천 관측 불가로 공개 불가 | 수집·진단·승인 입력 준비, 실제 증거 부족으로 OFF |

B는 strict를 우회하는 이름 변경이 아니다. B 평가에는 `strict_pass=False`, `production_enabled=False`, `scope=provider_observation`이 명시된다. 연구 플래그나 공급자 계약 하나를 켜도 소비자 배지로 승격되지 않는다. `research_qualified`도 정책·품질·산술 조건이 모두 필요하다. 플랫폼 평점 조건은 별도 자격이다.

## 공급자 능력과 계약

2026-10-08 공식 문서를 확인했다.

- [Apify Actor 입력](https://apify.com/compass/google-maps-reviews-scraper/input-schema): 검증된 placeIds, maxReviews, newest, reviewsStartDate의 UTC 경계, Google origin, 개인 정보 제외 옵션을 명시한다. `language`는 표시 언어이며 원문 언어 증거로 복사하지 않는다.
- [Actor 및 요금](https://apify.com/compass/google-maps-reviews-scraper): 계정 API의 당시 Free 요금은 review-scraped $0.0006/건, 시작 이벤트 $0.00005였다. 다른 이벤트·플랫폼 저장/요청 비용까지 포함한 총 견적은 아니다.
- [Actor 실행 API](https://docs.apify.com/api/v2/actors-runs-post): numeric build, timeout, maxTotalChargeUsd, restartOnError=false를 명시한다. 로컬 ledger는 원격 run 상한뿐 아니라 poll/abort/delete/storage 관련 비용도 예약한다. SDK나 원격 내부 재시도 수를 2회로 보장하지 않는다.
- [계정 run 조회](https://docs.apify.com/api/v2/actor-runs-get): READY/RUNNING/TIMING-OUT/ABORTING을 계정 범위로 읽고 첫 페이지 길이가 아닌 total을 검사한다. 이 읽기 시점 후 다른 사용자가 실행할 가능성까지 제거하는 원격 락은 아니다.

계정 확인 시각 **08:46:32 UTC**: Free, 월 크레딧 $5, 월 상한 $5, 보고 사용량 $0.0000035267323255538942, active runs 0. latest build는 `0.0.528`이지만 기존 pin `0.0.527`을 자동 변경하지 않았다. build 변경은 새로운 계약 검토 대상이다. 스냅샷은 5분이 지나면 실행 계획에서 만료 처리한다.

## 저장·계산 계약

- request JSON에 관측 프로필을 저장한다. 과거 JSON의 기본값은 strict_source_v1이다. strict의 기존 request hash는 유지하며 B에는 별도 hash를 사용한다.
- 계약 자동 선택은 관측 프로필별이다. 새 B 계약이 과거 strict 계약을 밀어내지 않는다. 이미 시작한 run은 저장된 contract ID/build/어댑터/언어 버전으로 재개하고 철회 여부를 검사한다.
- `received_count`: dataset에서 수신한 기록 수. `considered_count`: 중복 제거 후 레코드 상한을 소비한 수. `unique_count`: 기간에 포함되어 집계한 고유 기록 수. 날짜 불명·기간 밖·초과·중복 수는 각각 분리한다.
- 별점 전용, 날짜 불명도 B의 최대 200개 고유 수신 기록 상한을 소비한다. 번역문만 있지만 본문 존재가 확인되면 T와 U에 포함한다. 본문 존재도 불명확하면 extraction_unknown이며 rating-only가 아니다.
- Apify `source_page_count/source_page_limit/source_attempt_limit`은 null. `dataset_read_page_count`와 로컬 dataset 페이지/재시도 상한은 별도로 기록한다.
- 반복 cursor·정렬 역전·중간 실패·time/budget cap은 partial이며 관리자 진단용이다. 정상 record_cap/date_boundary/exhausted도 전수 수집을 뜻하지 않는다.
- T=C+U, L+K≤C와 전체 합계를 검증한다. 표시 L/C·K/C, 보수 L/T·(K+U)/T, 분모 0은 null이다. 보수 경계는 언어 불명 자료에 대한 경계이며 통계적 신뢰구간과 다르다.
- 초기 조건 C≥100, U/T≤10%, L/T≥60%, (K+U)/T≤10%를 유지한다. 평점≥4.2/5·평가≥200은 같은 플랫폼에서 따로 확인한다. ‘관측 본문 모두 현지어’는 L=T=C, K=U=0, 추출 불명0과 모든 gate가 필요하다.
- 지점 삭제·정정, 정책/외부 ID 연결/계약 철회는 해당 aggregate와 의존 run을 무효화한다. 일반 추천 조회는 리뷰를 재수집하지 않는다.
- 작성자 이름·사진·프로필·방문 이력·원문 응답을 일반 로그/예약 Chroma에 저장하지 않는다. 원격 dataset은 별도 보관/삭제 정책과 cleanup outbox로 처리한다.

## 현재 실행 자격과 파일럿

운영 DB 읽기 전용 확인: **active Apify 정책0, 승인된 공급자 계약0, 승인된 리뷰 지점 연결0, 실제 품질 평가0, research/production 모두 OFF**. 배포 가격 설정에 Apify 호출 단가/상한도 구성되지 않았다. 키가 있다는 이유만으로 이를 승인하지 않았다.

`plan_v5_review_pilot.py`는 계정 잔여 무료 크레딧, 월 상한, 기존 승인 상한, 현재 설정 상한, 제안 $1 중 최솟값을 사용한다. 현재 유효 상한은 **$0**이다. 실제 원격 run은 0회다.

| 단계 | 계획 | 리뷰 이벤트만 계산한 금액 | 실행 |
| --- | --- | ---: | --- |
| 계약 smoke | 1곳×최대40건 | $0.024 | 미실행: 이용 정책·지점 연결·호출 가격/상한 미설정 |
| 기간/원문 확인 | 3곳×최대200건 | $0.36 | 미실행: 앞 단계 미통과 |
| 두 도시 검증 | 10곳×최대200건 | $1.20 | 미실행: 앞 단계 미통과, 새로 전부 수집하면 리뷰 이벤트만으로도 제안 $1 초과 |

40건 smoke는 C≥100의 추천 자격 근거가 아니다. 앞선 유효 관측을 재사용하고, 배치 분할도 기존 총 승인·잔여량 안에서만 가능하다. 이 문서는 추가 결제나 상한 증액 승인에 해당하지 않는다.

실행 준비 순서: 실제 지점 ID 대조 → 데이터 이용/원격 보관·삭제 범위 기록 → pinned build의 실제 필드 계약 검토 → 현재 요금으로 공통 예산 설정 → 계정 동시 사용량 재확인 → 기존 관리자 preview → 기존 admin_research job 제출. 실행 결과가 불명확하면 기존 remote run/checkpoint를 확인하며 새 Actor를 바로 시작하지 않는다. 실행한 뒤 raw 삭제 실패도 성공으로 표시하지 않는다.

언어 모델의 실제 원문·독립 라벨 평가가 완료되고 B 공개 정책이 별도 승인되기 전에는 연구 결과만 볼 수 있다. 향후 B를 소비자에게 공개하려면 별도 노출 계약과 승인 기록을 연결하는 작업이 남아 있다.
