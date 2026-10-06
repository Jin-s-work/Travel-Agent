# Travel Agent 03단계 리뷰 수집과 원문 언어 분석 검증

문서 기준 디렉터리는 docs/service-v2다. 아래 문서명과 examples 경로는 이 디렉터리를 기준으로 읽어.
너는 Travel Agent의 데이터 제품 책임자이자 백엔드·프런트엔드 구현 엔지니어다.
아래 요구사항을 실행 가능한 코드, 관리자 검증 화면, 자동 테스트, 재현 보고서로 만들어줘.
기획을 요약하는 답변으로 끝내지 말고 이번 단계의 구현과 검증을 진행해.
외부 계정이 준비되지 않아도 코드·합성 검증·실행 준비를 끝내고 라이브 검증만 분리해 기록해.

## 1. 작업 위치와 이번 단계의 결과

- 저장소: `/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`. GitHub: `https://github.com/Jin-s-work/Travel-Agent`.
- 제품은 본인과 초대한 소수 지인용 여행 서비스이며 첫 추천 검증 도시는 도쿄와 바르셀로나다.
- 목표는 평점·리뷰 규모가 충분하면서 최근 관측 리뷰에서 현지어 비중이 높고 한국어 비중이 낮은 후보를 찾는 것이다.
- 이를 주민 비율로 바꾸지 말고 근거 범위가 명확한 `최근 리뷰 언어` 기능으로 구현해.
- 결과물은 장소별 수집 실행, 정규화, 언어 판별, 집계, 품질 판정, 근거 조회가 연결된 기능이어야 한다.
- 추천 전체 화면·예약·일정은 다음 단계 범위다. 이번에는 다음 단계가 실제 호출할 데이터 계약을 제공해.
- 기존 메일 업로드·예약 분석·검색·질문 기능과 다른 도시의 예약 데이터를 보존해.

## 2. 시작 순서와 변경 경계

1. 적용되는 AGENTS.md, `git status`, 기존 코드, `docs/service-v2/IMPLEMENTATION_STATUS.md`를 확인해.
2. 상태 파일이 없으면 실제 조사 결과로 생성해. 문서의 계획을 구현 완료로 기록하지 마.
3. `REVIEW_DATA_SPEC.md`, `CRAWLING_FEASIBILITY.md`, `TECHNICAL_SPEC.md`의 관련 절을 읽어.
4. `ACCEPTANCE_TESTS.md`, `examples/review-language-fixtures.json`의 24개 사례, `IMPLEMENTATION_PROMPTS.md` P03·P04를 대조해.
5. 01단계 인증·여행 격리와 02단계 작업·예산 기능을 확인해 재사용하고 필요한 최소 선행 수정만 포함해.
6. FastAPI·동일 출처 PWA·SQLite·단일 인스턴스를 유지해. 새 큐 서비스나 프런트엔드 전면 교체는 범위 밖이다.
7. 아래 DTO·API 경로는 구현 목표다. 이미 존재한다고 가정하지 말고 현재 구조와의 매핑을 기록해.
8. 옛 감사 커밋으로 checkout/reset하지 마. 사용자 변경을 보존하고 비밀키·실제 메일·예약번호를 출력하지 마.

## 3. 네 종류의 상태를 분리해

- 기술 실행 상태: `queued/running/succeeded/partial/failed/cancelled`. 02단계 jobs 계약을 재사용해.
- 데이터 이용 상태: 접근·수집·계산·저장·LLM 전달·표시를 각각 확인해.
- 품질 상태: 지점 식별, 원문 구분, 정렬·페이지 연결, 언어 평가, 신선도를 각각 기록해.
- 제품 상태: `discovered → access_reviewed → sampled → quality_reviewed → production_enabled`.
- 호출 성공만으로 품질·이용 조건까지 통과했다고 판단하지 마.
- 근거가 부족한 장소와 실제 언어 조건을 만족하지 못한 장소를 같은 “탈락”으로 합치지 마.
- `crawl_research_enabled`와 `crawl_production_enabled`는 기본 off로 두고 변경 주체·시각·근거를 남겨.

## 4. 교체 가능한 공급자 어댑터

- 제안 위치는 `src/providers/`의 `ReviewCollectionProvider`와 `src/research/`의 정규화·평가 모듈이다.
- 조사 문서의 `ReviewProvider`는 같은 책임의 설명용 명칭이다. 중복 추상화를 만들지 마.
- 먼저 `FakeReviewCollectionProvider`를 만들고 실제 공급자 한 개를 같은 계약으로 연결해.
- Apify를 첫 평가 후보로 삼되 공식 문서의 입력·출력·가격·이용 조건을 실행 시 다시 확인해.
- 다른 공급자가 적합하면 근거를 남겨 선택해. 여러 유료 공급자를 한꺼번에 구현하지 마.
- 직접 브라우저 수집이 필요하면 제한된 별도 환경을 설계하고 작은 웹 서버에 브라우저를 상주시켜서는 안 된다.
- Places API의 최대 5개 제한을 Maps 웹 수집의 기술적 상한으로 취급하지 마.
- 공급자가 많은 리뷰를 지원해도 모든 장소의 전체 리뷰 수집을 보장한다고 해석하지 마.

공통 요청에는 다음 필드를 타입과 검증 규칙까지 정의해:
- `place_identity_id/external_place_id/provider/adapter_version`과 검증한 지점·원천 식별자.
- `collection_mode="observed_window"`, `sort="newest"`, `requested_start/requested_end`, `lookback_days=180`.
- `max_review_records=200`, `max_pages=20`, `max_attempts_per_page=2`, 유한한 실행 시간·응답 크기 한도.
- `locale_filter=null/keyword_filter=null/rating_filter=null`, 별도 `website_locale`.
- `policy_version/budget_reservation_id/idempotency_key`, 인증 실행 주체와 작업 참조.
- 사용자 요청으로 상한을 임의로 늘릴 수 없게 서버 설정과 요청 허용 범위를 교차 검증해.

공통 응답은 `data/provenance/coverage/freshness/usage/limitations/policy_version`으로 분리해:
- data: 정규화할 레코드와 원문·번역·날짜·식별 지원 여부인 `capabilities`.
- provenance: 공급자·어댑터 버전·원천·수집 시각·요청 해시·공급자 실행 참조.
- coverage: 요청 수·수신 수·고유 수·실제 관측 기간·페이지 수·정렬 기준·종료 사유.
- usage: 예상 예약액·실제액·미확정액·통화·가격 확인 시각.
- 미지원 값은 null과 사유로 반환해. raw 응답을 제품 DTO로 그대로 보내지 마.

## 5. 지점 식별과 입력 준비

- 수집 입력은 이름 검색 결과가 아니라 지점이 확인된 외부 ID 또는 검증한 Maps 장소 참조여야 한다.
- 동일 상호의 다른 지점, 이전 주소, 영구 폐업, 같은 건물의 다른 매장을 구분할 이름·주소 대조를 구현해.
- 모호하면 `needs_confirmation`으로 두고 유료 수집 전에 해소해.
- URL 해석은 origin allowlist를 적용하고 모든 redirect·DNS 해석에서 private·loopback·link-local·metadata 목적지를 차단해.
- 지도 링크 저장 권한과 서버의 자동 fetch 권한을 분리하고 file·비HTTP scheme을 거절해.
- 공급자에 개인 메일·예약번호·사용자 세션 쿠키를 보내지 마.
- 실제 장소는 실행 시 선정하고 fixture의 가상 지점을 실제 분석 결과처럼 표시하지 마.

## 6. 정규화와 원문·번역 구분

`ReviewObservation`에는 다음 개념을 보존해:
- `run_id/place_id/provider_review_id` 또는 허용된 opaque `dedupe_key`, 원천.
- `original_text_ref/original_language/translated_text_present`, 원문·번역 구분 근거.
- `published_at/edited_at/fetched_at`, 날짜 정밀도와 원래 시간 표현의 허용된 참조.
- `rating`과 척도, `text_presence`, 판별 `language/detector_version/model_confidence`.
- `disagreement_reason/classification_checked_at`, 정책 버전·보관 만료·삭제 상태.
- 작성자 이름·사진·프로필 URL·방문 이력·다른 가게 리뷰를 포함하지 마.

`text_presence`는 세 가지로 구분해:
- `present`: 본문 존재 확인. 원문 없이 번역문만 있어도 본문 존재는 확인되므로 T와 U에 포함해.
- `rating_only`: 구조나 검수로 본문 없는 별점임이 확인됨. 언어 분모 T에서 제외해.
- `unextractable`: 본문 존재 또는 추출 성공을 판단할 수 없음. 별도 E로 세고 엄격 판정을 차단해.
- 합쳐진 원문·번역은 검증한 구조로 분리할 때만 원문으로 사용해. 한국어 표시만으로 한국어 원문이라 하지 마.
- 원문 HTML은 표시 전에 escape/sanitize하고 안에 있는 명령·URL·도구 지시는 실행하지 마.
- 동일 ID의 원문·번역·편집본은 한 리뷰다. 다른 ID의 짧은 동일 문장은 임의로 합치지 마.
- stable ID가 없으면 허용된 필드로 중복 가능성을 검출하고 충돌·불확실성을 품질 상태에 남겨.
- 공급자의 `language/hl`과 리뷰 원문 언어를 혼동하지 않는 contract test를 만들어.

## 7. 최신순·기간·페이지 실행 알고리즘

1. run 생성 시 기준 시각을 고정하고 그 시각에서 180일을 뺀 요청 범위를 저장해.
2. 정책·지점·예산·플래그를 검사한 후 비용을 예약하고 durable job을 접수해.
3. 페이지 또는 공급자 실행을 호출하고 수신량·cursor·요청 해시·비용 참조를 기록해.
4. 중복을 제거하되 실제 수신·과금 수와 분석에 사용한 고유 수를 각각 유지해.
5. 고유 레코드 200개에는 rating-only도 포함해. 본문 200개를 채우려고 계속 수집하지 마.
6. 마지막 페이지가 상한을 넘으면 관측은 정렬상 첫 200개까지만 포함하고 초과 수신·비용은 별도 기록해.
7. 정렬 의미를 `published_at/edited_at/unknown`으로 보존하고 날짜 경계는 같은 필드에서만 판단해.
8. edited_at 정렬에 published_at 경계를 적용하거나 “3개월 전”을 정확한 날짜로 만들지 마.
9. cursor·페이지 반복, 중간 누락·실패, 정렬 역전을 탐지하고 불확실성을 숨기지 마.
10. 기록·날짜·페이지·시간·비용 한도 중 하나에 도달하면 종료하고 취소·lease 만료도 확인해.

정상 범위 종료는 `exhausted/date_boundary/record_cap`이며 각각의 근거를 검증해:
- exhausted는 공급자 다음 페이지가 없다는 뜻이지 플랫폼 전수라는 뜻이 아니다.
- date_boundary는 같은 정렬 시간 기준으로 경계를 넘었고 연결이 확인될 때만 사용해.
- record_cap은 공급자 최신순 반환 구간의 K건 확보다. 플랫폼 전체 최신 K건 완전 수집이라고 표시하지 마.
- `page_cap/time_cap/budget_cap/provider_blocked/cursor_expired/parse_error`는 partial이다.
- 정렬·시간 의미 unknown 또는 연결 미검증이면 개수가 충분해도 엄격 조건을 통과시키지 마.

## 8. 재시도·취소·비용·재시작

- attempts=2는 최초 호출을 포함한 총 2회다. 재시도 2회를 추가하는 의미로 쓰지 마.
- transient 오류만 제한적으로 재시도하고 429의 Retry-After를 실행 기한 안에서 존중해.
- CAPTCHA·로그인·접근 차단이면 종료해. 쿠키 전달·보호 우회·프록시 회전으로 이어가지 마.
- 비동기 공급자 job의 원격 ID를 먼저 저장하고 응답 유실 뒤 새 유료 실행 전에 기존 상태를 조회해.
- 상태 확인·결과 회수에도 실제 비용과 호출 제한을 적용해.
- 불명확한 과금은 `unknown-charge`로 남기고 예약액을 무조건 환급하지 마.
- 네트워크 중 SQLite 쓰기 잠금을 잡지 말고 02단계 lease·heartbeat·fencing token을 재사용해.
- 취소·권한 회수·정책 철회·대상 삭제 후 도착한 결과가 활성 집계를 되살리지 못하게 해.
- 같은 idempotency key·입력은 기존 작업, 같은 key·다른 입력은 409를 반환해.
- 추천 버튼마다 재수집하지 마. 유효한 집계를 조회하고 갱신 필요 여부를 별도 반환해.

## 9. 로컬 언어 판별

- 현지어는 도쿄 `ja`, 바르셀로나 `es/ca`이며 `ko`와 겹치지 않도록 검증해.
- BCP 47 원래 태그와 집계용 기본 언어를 보존해. es-419 집계는 거주지 증거가 아니다.
- 공급자 원문 언어도 필드 의미와 표본을 검증하고 로컬 모델 불일치 이유를 남겨.
- 원문만 있으면 가벼운 로컬 모델을 우선 평가하고 모델 버전·파일 해시·라이선스를 기록해.
- 운영 시작마다 모델을 다시 다운로드하지 않게 준비하고 모델 부재·실패 상태도 명시해.
- 이모지·짧은 메뉴명·숫자·혼합 언어는 기준이 부족하면 unknown으로 남겨.
- 모델 confidence를 정답 확률·현지인일 확률로 표시하지 마.
- 모든 리뷰에 LLM을 호출하지 마. 보조 판별은 별도 권한·비용·평가가 필요한 후속 선택지다.

## 10. 집계 불변식과 순수 함수

- R은 고유 관측, T는 본문 존재 확인, C는 언어 판별, U는 본문은 있지만 언어 미판별 수다.
- B는 rating-only, E는 extraction-unknown이며 `R=T+B+E`, `T=C+U`를 강제해.
- L은 현지어, K는 한국어이며 `0≤L+K≤C`이고 모든 개수는 0 이상의 정수다.
- counts_by_language의 합은 C다. 플랫폼 `total_rating_count`를 R·T와 같다고 가정하지 마.
- `classified_local_share=L/C`, `classified_korean_share=K/C`, `unknown_share=U/T`.
- `local_share_lower_bound=L/T`, `local_share_upper_bound=(L+U)/T`.
- `korean_share_lower_bound=K/T`, `korean_share_upper_bound=(K+U)/T`.
- 분모 0은 null이다. NaN·Infinity·0%로 바꾸지 마. 반올림은 표시에서만 해.
- 상하한은 미판별 언어의 범위다. 95% 신뢰구간이나 분류 오류 보정치가 아니다.
- 음수·합계 불일치·현지어에 ko 포함은 `invalid_input`으로 반환하고 조용히 보정하지 마.
- `scope_mode/inference_method/computed_at/expires_at`에 run·모델·설정 버전을 연결해.

## 11. 엄격 판정과 정보 표시

- 반환값은 `decision=pass/fail/unsupported/invalid_input`, `strict_pass/reason_codes/display_mode/metrics`다.
- 이용 권한·지점·신선도·최신순·무필터·공급자 구간 연결·언어 평가를 먼저 검사해.
- E>0, partial, 관련도순, 언어·키워드·평점 선택 수집이면 엄격 조건은 unsupported다.
- 품질 조건은 `C≥100`, `U/T≤0.10`이다. 부족한 자료와 실제 조건 실패를 구분해.
- 이후 `L/T≥0.60`, `(K+U)/T≤0.10`이면 엄격 언어 조건 pass다.
- 평점·평가 규모는 다음 추천 단계의 별도 조건이다. 언어 pass를 최종 추천 통과라고 하지 마.
- 평점 기준을 바꿔도 언어 분모·품질 조건을 함께 낮추지 마. 설정은 버전으로 관리해.
- fixture의 `LOCAL_SHARE_BELOW_MIN/KOREAN_SHARE_ABOVE_MAX/TOO_MANY_UNKNOWN/PARTIAL_COLLECTION` 등을 재사용해.
- population_estimate는 초기 제품에서 비활성이다. 최신순 200건에 전체 추정·Wilson 구간을 붙이지 마.
- unavailable을 0점이나 한국어 0%로 바꿔 추천에 흘려보내지 마.

합성 예시 A를 API·UI·테스트에서 같은 의미로 검증해:
- T=200, C=190, U=10, L=150, K=4 → 판별 가능 원문 중 현지어 78.9%, 한국어 2.1%.
- 엄격용 현지어 하한 75%, 한국어 상한 7%. 다른 gate도 통과해야 엄격 언어 pass다.
- T=200, C=130, U=70, L=120, K=0 → 한국어 0건이어도 미판별 35%라 unsupported다.
- 0/5를 “한국어 리뷰 없는 가게”로 표시하지 마. 주민 비율·국적·민족을 추정하지 마.

## 12. 저장·표시·삭제 권한

- provider_policies에 버전·검토 시각·origin·목적·출처 근거·미해결 조건을 저장해.
- 수집·로컬 계산·원문 저장·집계 저장·review ID/hash 보관·LLM 전달·표시를 각각 정의해.
- `allowed_uses/retention_until/redistribution_allowed`를 원문·집계에 별도로 적용해.
- 처리 권한이 없으면 메모리 처리도 자동 허용하지 마. 해당 gate를 대기 상태로 남겨.
- 원문 TTL은 허용 기간·실험 필요 기간·앱 상한의 최솟값이다. 연구 상한 24시간은 허가가 아니다.
- 금지 원문을 디스크·벡터·로그·SSE·분석 이벤트·백업에 남기지 마.
- 반복 가능한 만료·삭제 작업과 파생 집계·추천의 stale/unsupported 전환을 구현해.
- 화면 snapshot과 백업 복원에도 tombstone을 적용하고 hash를 익명화·자유 보관 허가로 간주하지 마.
- 검토 기록을 “법적으로 완전히 안전함”이라는 배지로 바꾸지 마.

## 13. 갱신과 기존 결과

- 제한된 도시 후보팩과 명시적 관리자 갱신부터 시작해. 모든 장소를 매일 수집하지 마.
- 새 집계 검증 뒤 짧은 트랜잭션으로 활성 결과를 교체해.
- 새 run 실패 시 이전 결과가 유효하면 확인일을 유지해 제공하고 만료된 결과는 stale로 표시해.
- 증분 수집은 겹치는 구간을 확보한 뒤 ID·수정시각으로 병합해. 기존 ID 하나로 바로 종료하지 마.
- 겹침·최신순을 검증할 수 없으면 제한된 재수집으로 전환하고 넓은 완료 범위를 주장하지 마.
- 현재 페이지에 없다는 이유만으로 과거 리뷰 삭제를 확정하지 마.
- 제한된 재대조의 `last_full_reconciliation_at`을 남기고 비용도 계산해.

## 14. 관리자 화면과 소비자 조회

- 관리자에게 공급자 준비, 정책·가격 확인일, 남은 예산, 연구/운영 플래그를 보여줘.
- 제안 API: `POST /api/v2/admin/review-collection-runs`, `GET /api/v2/admin/review-collection-runs/{run_id}`.
- 생성은 202와 job/run ID·상태 URL을 반환하고 일반 초대 사용자의 실행·정책 변경을 막아.
- 공용 장소 조사 job은 02단계의 scope_kind=admin_research, actor_id,
  안정적인 scope_id를 사용하고 trip_id는 null로 둬. 임의 개인 여행에 배정하지 마.
- 관리자 role·정책·대상 장소·실행자 권한 회수를 조회와 결과 활성화에서 검사해.
  일반 사용자의 job 조회·SSE는 차단하고 개인 여행 삭제로 공용 장소 원문을 무조건 지우지 마.
  개인 추천 연결과 공용 자료의 정책상 삭제는 각각 처리해.
- 진행 표에는 장소, 수신/고유/본문/판별/미판별/추출불명 수, 비용, 종료 사유를 표시해.
- 가짜 진행률 대신 현재 단계·페이지 수·처리 건수를 표시해.
- 상세에서는 정렬 시간 기준·관측 기간·continuity·detector/config/policy 버전·사유를 확인하게 해.
- 취소·재시도·새 실행을 구분하고 재시도 입력과 추가 최대 비용을 알려줘.
- 소비자 제안 API: `GET /api/v2/trips/{trip_id}/places/{place_id}/review-evidence`. 소유권·장소 연결·표시 권한을 검사해.
- 소비자 DTO는 기간·개수·분모·판정·확인일·제약만 담고 raw·프로필·비밀 참조를 제외해.
- “최근 확보한 텍스트 200건 · 원문 언어 판별 190건 · 일본어 150건 · 한국어 4건 · 미판별 10건”으로 설명해.
- loading/partial/unavailable/stale/blocked/empty를 설계하고 색상만으로 상태를 전달하지 마.
- 모바일은 핵심 수치·범위·사유를 먼저 보여주고 기술 디버그는 관리자 상세에 둬.
- 오류에 비밀키·토큰 URL·원문을 노출하지 말고 request/run ID로 연결해.

## 15. 라이브 실험과 비용 범위

- 실행 시 현재 공식 가격·최소 결제·잔여 무료량·별도 비용을 확인해. 과거 단가를 확정값으로 쓰지 마.
- A는 도쿄 3곳·바르셀로나 3곳 × 최대 100레코드로 동작과 원문 필드를 검증해.
- A 통과 뒤 B는 도쿄 5곳·바르셀로나 5곳 × 최대 200레코드로 적합성을 평가해.
- A+B 전체 사용액 제안 상한은 $3이다. 장소별 상한이나 기존에 승인된 결제액이 아니다.
- 기존 권한·설정 범위에서 실행하고 새 유료 구독·카드 등록을 자동으로 하지 마.
- 비용 확인이 필요하면 대상·입력·현재 단가·최대액·중단 조건이 있는 실행안을 먼저 완성해.
- 공급자 잔액을 강제 지출 한도로 가정하지 말고 앱 예산·공급자 한도의 포함 항목을 대조해.
- 키가 없으면 단위·통합 완료와 라이브 미검증을 분리해. fake 결과를 live로 바꾸지 마.
- 도시·대상·정렬·기간을 실행 전에 고정하고 결과가 좋은 리뷰만 골라 집계하지 마.

## 16. 라벨 평가와 기능 활성화

- A의 최소 30건으로 원문·번역·날짜 구조를 대조하되 서비스 전체 정확도라고 주장하지 마.
- B는 각 도시 원문 대조 최소 20건과 별도의 도시별 최소 100건 언어 라벨 평가셋을 준비해.
- 허용 원문을 수작업 검토하고 ja/ko/es/ca/기타/혼합·판별불가 기준을 먼저 작성해.
- 개발·임계값 조정 자료와 최종 평가 자료를 분리하고 같은 자료로 튜닝한 성능을 독립 평가라 하지 마.
- 적은 언어는 진단용 표본을 추가하되 관측 비율 집계에 섞지 마.
- 현지어 precision·한국어 recall 각각 95%를 초기 목표로 두고 한국어 precision·false negative·confusion matrix도 보고해.
- 한국어 정답 0건이면 recall 통과가 아니다. TP/FP/FN·언어별 분모·unknown 수를 함께 남겨.
- 부족하면 `CLASSIFICATION_QUALITY_UNVERIFIED`와 추가 평가 필요 사유를 기록해.
- B의 10곳 중 9곳 이상은 해석 가능한 결과 또는 명확한 부족 사유가 있어야 한다. 모두 엄격 통과할 필요는 없다.
- 100건 이상 공개 리뷰가 있는 후보 중 최소 8곳에서 5개 초과 원문 확보 여부도 기술 지표로 확인해.
- 수집 성공률·원문 품질·분류 정확도·조건 통과 장소 수를 하나의 성공률로 합치지 마.

## 17. 자동 테스트와 실패 주입

- 24개 JSON 사례는 defaults와 input_overrides를 객체 deep merge·배열 replace 방식으로 합성해.
- decision·reason_codes·display_mode·metrics와 population/residency inference false를 모두 검사해.
- 허용 오차는 fixture를 따르고 정확히 60%·10% 경계가 표시 반올림으로 바뀌지 않게 해.
- REVIEW-01~12, DATA-06~08, AUTH-06·08의 관련 흐름을 실제 테스트에 연결해.
- 번역문만 있음·혼합문·rating-only·추출 실패·절단·locale 불일치 parser fixture를 만들어.
- 반복 cursor·역순·빈 페이지와 다음 cursor·중간 오류·시간 기준 불일치·상한 초과 응답을 재현해.
- 같은 ID 편집본·번역본, 다른 ID 같은 문구, ID 없는 충돌을 구분해 검사해.
- 예산 부족·응답 유실·unknown-charge·취소 직후 결과·lease 만료·재시작을 통합 검증해.
- 정책 철회·TTL·삭제 뒤 late completion·복원으로 데이터가 다시 표시되지 않는지 확인해.
- 관리자 권한·다른 여행 evidence·SSE·오류·로그의 정보 누출을 검사해.
- 기본 테스트와 CI는 유료 키·네트워크 없이 실행돼야 한다. live 테스트는 명시적으로 분리해.
- 실행 명령·결과·미실행 사유와 결함 수정 후 재검증 근거를 남겨.

## 18. 구현 순서와 다음 단계 인계

1. 선행 구현 점검 → DTO·정책·설정 → migration·순수 집계 함수부터 작성해.
2. fake·정규화 → 페이지·작업·예산 → fixture·실패 주입 테스트를 완성해.
3. 로컬 판별기·평가 도구 → 실제 어댑터 한 개 → 관리자·소비자 조회를 연결해.
4. 허용된 live A/B → 삭제·만료 검증 → 04단계 인계 자료를 완성해.
5. 코드·화면·계약·테스트·실제 호출/비용·품질·미확정 조건을 각각 보고해.

- 보고서는 `docs/service-v2/reports/review-pilot-<날짜>.md`를 제안해. 실제 실행이 없으면 준비/합성 검증임을 밝혀.
- `IMPLEMENTATION_STATUS.md`에 코드·fake·live·도시별 언어 평가·운영 활성화를 따로 기록해.
- 코드 준비만으로 production_enabled라 기록하지 마. 실제 권한·품질·실행 결과에 맞춰 표시해.
- 최종 답변은 파일·검증·실제 수집 여부·비용·남은 제한·04단계 연결 방법을 포함해.
- 04단계에는 장소 ID·DTO·reason codes·유효기간·도시별 지원·실제/합성 구분을 인계해.
- 부족한 데이터를 한국어 0%로 해석하거나 모든 관광지에 엄격 배지를 붙일 수 없도록 예제를 남겨.
