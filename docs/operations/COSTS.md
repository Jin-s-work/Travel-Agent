> **2026-10-06 적용 변경:** 사용자가 기존 Render Free + Supabase Free(`hii`)를 선택했다. 운영 DB는 PostgreSQL, 원문은 private Storage, 검색은 pgvector로 전환한다. 아래 SQLite/Chroma/영구 VM 설명은 로컬 모드와 이전 설계 이력이다. 현재 배포·비용·백업 계약은 [전환 안내](RENDER_SUPABASE.md)를 우선 적용한다. 새 버전 실제 배포는 별도 검증 상태다.

# 비용과 용량 검토

> **현재 결정(2026-10-06): 비용0원 우선. 유료 Render 제안은 보류입니다. [무료 구성](FREE_HOSTING.md)을 먼저 확인하세요.**

확인일 2026-10-06. USD, 세전. 이번 실행의 실제 유료 API 호출은 **0회**다. 아래 금액은 결제 승인·계정 잔액·청구서가 아니라 공식 공개 단가와 명시적 가정에 따른 견적이다. 이미 사용하는 계정의 다른 앱 사용량·무료 잔여량은 확인하지 못했다.

## 호스팅 선택

| 선택 | 월 비용·제약 | 판단 |
| --- | --- | --- |
| Render 0.5c-512mb + 1GB disk | compute $7 + disk $0.25 = $7.25 | 기존 Blueprint 유지. 5명 합성 시험을 통과한 초기 제한 베타 후보 |
| Render 1c-2g + 1GB disk | $25 + $0.25 = $25.25 | 메모리/지연이 실제 한도를 넘을 때 검토. 차액 $18, 자동 증설 안 함 |
| Render free | 유휴 후 sleep, 영구 디스크 미지원 | 이번 상시 접속·영속성 요구에 부적합 |
| Fly Machines + volume | region/CPU/RAM별 compute + volume $0.15/GB-month, snapshot 별도 | 기존 계정 확인 안 됨. 이관/운영 비용을 추가할 근거가 없어 우선 선택 안 함 |

[Render 가격](https://render.com/pricing), [공식 $7/$25 설명](https://render.com/articles/production-rails-hosting-guide), [새 compute ID와 사양](https://render.com/docs/compute-plans), [disk](https://render.com/docs/disks), [free 제약](https://render.com/docs/free), [Fly 가격](https://fly.io/pricing). Render Hobby workspace 요금과 앱 compute는 별개이며 workspace의 개발자 수 제한은 이 앱의 초대 사용자 수가 아니다. 디스크는 단일 인스턴스에 붙고 배포 시 짧은 중단이 있다. 자동 수평 확장과 zero-downtime을 약속하지 않는다.

외부 백업은 S3 호환 Backblaze B2를 비용 후보로 검토했다. 공개 요금은 $6.95/TB-month, 첫10GB storage 무료, 월평균 저장량의3배까지 egress 무료, 초과 $0.01/GB다. API Class A/B/C 무료, D는 첫 일2,500회 후 $0.004/10,000회다. 다른 계정 사용량과 무료 한도를 공유한다. 최소 파일 크기/보관 기간 요금이 없다는 설명과 계정의 최소 결제액은 다르다. 실제 카드/계정 최소 청구 조건은 미확인이다. [공식 B2 가격](https://www.backblaze.com/cloud-storage/pricing)

백업은 24시간 full + 5분 checkpoint, 7일 목표 보관이며 안전 여유1일을 더 유지한다. 원문만이 아니라 SQL·비용·삭제 checkpoint가 누적되는 용량을 계산해야 한다. Render outbound도 백업 전송에 사용된다. [Render 현행 bandwidth 정책](https://render.com/blog/better-pricing-for-fast-growing-teams)의 초과 전송 요금과 계정 사용량을 배포 전에 확인한다. 무료 크레딧을 영구 할인처럼 계산하지 않는다.

## 외부 작업

| 공급자·SKU | 공개 단가 | 초기 활성화 |
| --- | --- | --- |
| gpt-5-mini | 입력 $0.25, cached 입력 $0.025, 출력 $2 / 1M tokens | 가격 템플릿만 작성, halted=true |
| text-embedding-3-small | $0.02 / 1M tokens | 같은 비용 gate 적용 |
| Google Places Text Search Pro | 무료 월5,000건 후 첫 유료 tier $32/1,000 | OFF, field mask에 따른 SKU 확인 필요 |
| Places Details Enterprise | 무료 월1,000건 후 $20/1,000 | OFF, 평점/평가 수 등 필드 조합 확인 필요 |
| Details Enterprise + Atmosphere | 무료 월1,000건 후 $25/1,000 | OFF |
| Routes matrix | 조회 element 기준, 실제 선택 SKU/지역/월 사용량 확인 필요 | OFF, 실경로 검증 전 사용 금지 |
| Apify | Free credits $5/month; Starter $19/month prepaid + 초과 사용 | OFF, 실제 최소 결제·공유 잔액은 계정 확인 필요 |
| Maps Reviews Actor | 시작 표시 $0.30/1,000 reviews | OFF, actor start/events/platform/스토리지/실제 플랜을 합산해야 함 |
| Tavily/기타 검색 | 실제 선택 플랜 미확인 | 가격 설정 없음 → 차단 |

[모델 가격/지원 상태](https://developers.openai.com/api/docs/models/gpt-5-mini), [임베딩](https://developers.openai.com/api/docs/models/text-embedding-3-small), [Maps SKU](https://developers.google.com/maps/billing-and-pricing/pricing), [Apify 가격](https://apify.com/pricing), [Actor 가격](https://apify.com/compass/google-maps-reviews-scraper). gpt-5-mini 페이지에 Deprecated 표시가 있어 실제 계정의 사용 가능 여부·종료 일정을 배포 전에 확인해야 한다. 이번 단계에서 더 비싼 모델로 자동 교체하지 않았다. API 최소 충전액·세금·환율·남은 credits는 공개 토큰 단가로부터 추론하지 않는다.

`pricing-policy.disabled.json`은 현재 단가·버전·상한을 입력한 **중지 상태** 예제다. 마이크로USD 단위로 사용자 일 $0.25/월$2, 전역 일$1/월$5다. 결제 상한에 대한 사용자 동의를 대신하지 않는다. 검색·지도·리뷰 가격이 없으므로 해당 호출은 거절된다. cached input 할인은 보수적으로 반영하지 않았다. 호출 전 예약, SDK retry0, 재시도별 예약, 응답 유실 unknown 유지가 적용된다. 앱 밖 공유 계정 과금, hosting/disk/backup/bandwidth는 이 ledger가 제한하지 않는다.

## 시나리오

| 상황 | 명시적 입력 가정 | 예상 월액 |
| --- | --- | --- |
| 저장 자료 중심 | 512MB, disk1GB, 외부 API OFF, B2 무료 잔여10GB 이내 | $7.25 + 적용되는 전송/세금 |
| 소규모 사용 예 | 위 사양 + 월250 LLM호출, 회당 입력8k/출력1k, embeddings100k tokens | AI $1.002, 합계 $8.252 + 백업/전송/세금 |
| 제안 API 상한 근접 | 위 사양 + 전역 API월$5 | $12.25 + 백업/전송/세금; 전체 청구 hard cap 아님 |
| 증설 검토 | 2GB서버 + disk1GB + API월$5 | $30.25 + 백업/전송/세금 |

250회 예제는 질문200개/추출50개가 정확히1회씩 성공한다는 계산 가정이다. 실제 토큰·reasoning output·retry가 다르면 비용도 달라진다. 업로드1MiB 제한은 입력 token100k 한도와 다르며 초과 입력은 비용 guard에서 차단될 수 있다. 파일당 최대 길이 시험과 실제 공급자 소량시험을 먼저 한다.

## 측정값과 한계

[512MB 부하 원본](../service-v2/reports/operations-load-512mb.json)에 idle/peak RSS·cgroup·CPU·disk·지연·큐 대기를 기록했다. 5명의 1,200회 읽기, 문서1개와 추천1개, fake 공급자2회, 실제 외부0회다. 단일 dispatcher 때문에 추천은 문서 완료 후 실행된다. 로컬 Colima Linux arm64의 CPU0.5/memory512m 제한이며 같은 VM의 다른 컨테이너 부하가 있을 수 있다. 짧은 합성 시험이 Render 장시간 SLA나 대용량 문서·실제 모델·Lingua 메모리를 증명하지 않는다.

실제 확대 전에 30분 이상 동일 조건·큰 문서·실경로·restore 중 읽기를 측정한다. RSS가 limit의80%를 넘거나 읽기p95>500ms가 지속되면 batch 축소·지연 import·후보 수/입력 길이 제한을 먼저 검토한다. 가격/성능이 확인되기 전 크롤링 브라우저·언어 모델 상주·자동 증설을 켜지 않는다.
