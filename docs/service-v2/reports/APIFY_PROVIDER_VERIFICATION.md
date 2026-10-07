# Apify 리뷰 공급자 계약 검증

확인일: 2026-10-01. 이 문서는 공개 공식 자료 확인과 mock HTTP 시험 기록이다. 실제 Maps 리뷰를 수집하지 않았고 계정 잔액·수집 이용 권한·분류 정확도를 검증하지 않았다.

## 현재 공식 계약

후보는 `compass/google-maps-reviews-scraper`다. 공개 Actor metadata GET에서 ID `Xb8osYTtOjlsgI6k9`, 최신 build `0.0.527`, build 완료 `2026-09-30T09:11:10.421Z`를 확인했다. 어댑터는 `latest` 자동 추적을 금지하고 배포 설정에 숫자 build를 명시하게 한다. 다음 실행 때 다시 확인한다. [공식 Actor metadata](https://api.apify.com/v2/acts/compass~google-maps-reviews-scraper)

입력은 검증한 27자리 `ChIJ`/`GhIJ` ID 1개, `maxReviews` 100 또는 200, `reviewsSort=newest`, UTC `reviewsStartDate`, 빈 `reviewsFilterString`, `reviewsOrigin=google`, `personalData=false`다. 표시 언어 `language`는 리뷰 원문 언어가 아니다. 이름 검색·장소 선택은 이 실행 밖에서 끝낸다. [공식 입력 스키마](https://apify.com/compass/google-maps-reviews-scraper/input-schema)

출력 중 `reviewId`, `text`, `textTranslated`, `originalLanguage`, `publishedAtDate`, `scrapedAt`, `stars`, `reviewOrigin`, `placeId`만 요청·정규화한다. 작성자·프로필·사진·방문 이력·업주 답변을 결과 계약에서 제외한다. 원문/번역 의미는 공개 예제에 있지만 실제 표본 대조 전 `contract_verified=false`다. 날짜 수정 필드는 확인되지 않아 `edited_at=null`이다. [공식 출력 예제](https://apify.com/compass/google-maps-reviews-scraper)

시작은 비동기 `POST /v2/acts/{actor}/runs`, 이후 실행 ID로 조회한다. 원격 ID를 durable receipt에 저장한 뒤 결과를 회수한다. 실행에는 pinned build, 유한 timeout, `restartOnError=false`, `maxTotalChargeUsd`를 보낸다. 시작 응답 유실은 unknown-charge로 두고 새 실행을 자동 생성하지 않는다. [실행 API](https://docs.apify.com/api/v2/actors-runs-post)

완료된 dataset은 offset/limit로 최대 50개씩 읽는다. 필드 allowlist와 응답 2MB 기본 상한, MIME 검사, fixed HTTPS origin, redirect 금지를 적용했다. Dataset offset은 Google 원천 cursor가 아니므로 내부 페이지 상한·재시도·누락을 검증했다고 주장하지 않는다. [Dataset API](https://docs.apify.com/api/v2/dataset-items-get)

## 가격과 실행 예산

현재 적용 metadata의 PAY_PER_EVENT 시작일은 2026-03-13이다. Free 리뷰 이벤트는 건당 **$0.0006**, Bronze는 **$0.00045**이며 actor-start는 메모리 GB당 **$0.00005**, 최소 1이벤트다. 공개 `minimalMaxTotalChargeUsd`는 null이다. 이 값은 문서 확인 결과이며 코드 기본 단가로 넣지 않았다. [가격 metadata](https://api.apify.com/v2/acts/compass~google-maps-reviews-scraper)

Free는 카드 없이 월 $0, 포함 사용액 $5다. Starter는 월 $19 결제와 $19 포함 사용액이므로 리뷰 사용액이 작아도 월 결제가 별도다. Free의 실제 잔여량은 계정에서 확인해야 한다. [공식 요금표](https://apify.com/pricing)

실험 A 6×100 + B 10×200 = 최대 2,600개 리뷰 이벤트다. 확인한 Free 단가로 리뷰 부분은 **$1.56**, 장소별 1GB 실행 16회라면 시작 이벤트 $0.0008을 더한다. 이는 전체 청구 보장액이 아니다. 원격 actor 실행 상한 외에 완료 후 dataset 읽기·보관, 네트워크와 재시도 비용을 앱 예산에서 따로 예약한다. A+B $3은 명세의 제안 상한이며 결제 승인으로 해석하지 않는다. [과금 범위·실행 상한](https://docs.apify.com/actors/running/actors-in-store)

완료 직후의 `usageTotalUsd`도 잠정일 수 있어 어댑터는 실제 확인 전 `usage_final=false`다. 원격 조회 성공이나 취소를 환불의 증거로 쓰지 않는다. [실행 비용 확인 주의](https://docs.apify.com/api/v2/actor-run-get)

## 이용·품질·제품 gate

- 기술: 어댑터와 mock HTTP, 합성 페이지 수집은 구현·검증했다. 라이브 리뷰 수집은 `not_run`이다.
- 이용: 공개 접근·공급자 결제만으로 원문 수집·계산·보관·표시 권한을 부여하지 않는다. Google Maps 추가 약관의 복제·대량 다운로드·재배포 관련 제한을 해당 사용과 대조할 근거가 필요하다. [Google Maps 약관](https://www.google.com/help/terms_maps/)
- 품질: Apify의 newest가 발행일인지 최종 수정일인지, 내부 원천 페이지 연결이 연속인지 공개 계약만으로 확정하지 못했다. 기본 `sort_basis=unknown`, `continuity_verified=false`; 언어 통계 엄격 기능은 unavailable이다. Dataset 최신 offset 연결 성공을 원천 연결 검증으로 대체하지 않는다.
- 제품: 연구/운영 플래그와 정책·언어 평가 gate를 모두 통과해야 별도로 활성화한다. API의 최대 5개 제한은 웹 수집량의 상한이 아니다. 이번 구현은 더 많은 레코드를 받을 수 있는 경로를 제공하지만 플랫폼 전수 확보를 약속하지 않는다.

Apify는 수집 중 원문을 공급자 dataset에도 보관하므로 실제 실행 정책은 공급자 보관까지 포함해야 한다. `delete_dataset`와 `delete_run`은 별도 budget 호출로 구현했고 204/404를 반복 가능한 삭제 완료로 처리한다. 삭제 실패는 완료로 처리하지 않는다. 원문 만료 전에 삭제 결과를 확인하고, vendor 내부 백업·로그의 보관 조건은 별도로 검토한다. [Dataset 삭제](https://docs.apify.com/api/v2/dataset-delete), [실행 삭제](https://docs.apify.com/api/v2/actor-run-delete)

## 구현과 재현

- `src/providers/reviews.py`: 입력 상한·frozen window·capabilities·page 계약.
- `src/providers/apify_reviews.py`: 실제 HTTP 어댑터, 비동기 start/poll/page/abort와 원격 삭제.
- `src/providers/fake_reviews.py`: 입력이 고정된 합성 provider, 페이지/재시도/실패 주입.
- `src/research/collection.py`: 200개 고유 기록에 rating-only 포함, 중복 ID·반복 cursor/page·정렬 역전·날짜 모호성·기간 경계·부분 종료 감지, 재시작 checkpoint.
- `tests/test_review_provider.py`: 비용이 들지 않는 request/contract/algorithm 시험.

실행: `.venv/bin/python -m pytest tests/test_review_provider.py -q` → **48 passed**. 체크포인트 저장 전 호출자가 정책에 맞는 normalize+transform을 반드시 제공하며, 원문이 durable checkpoint에 없는 회복 시험을 포함한다. 로컬 언어 모델의 정확도 검증과는 별개의 결과다.
