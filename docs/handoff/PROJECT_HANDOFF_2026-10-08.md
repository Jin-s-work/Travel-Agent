# 고잉 개발 인수인계 — Codex → Claude

기준일: **2026-10-08, Asia/Seoul**. 기획 의도, 구현, 실제 확인, 남은 일을 한곳에 정리했다. 사용자의 최신 요청이 우선하며, 아래의 과거 측정은 이후 상태를 보장하지 않는다.

## 1. 처음 5분에 알아야 할 것

고잉은 **여행에서 먹고 갈 곳을 찾고, 예약 메일을 정리하고, 확정 예약을 지키며 일정을 만드는 서비스**다. 사용자는 여행과 음식점 방문을 좋아하지만 매번 식당을 찾고 리뷰를 읽고 예약 메일을 뒤지는 반복이 번거로워 만들었다.

가장 강조하고 싶은 차별점은 **관측한 원문 리뷰에서 현지어가 많고 한국어가 적은 장소를 찾는 것**이다. 동시에 유명한 식당·대표 명소를 찾는 사람도 있으므로 두 추천 경험을 분리한다. 리뷰 언어를 현지 주민 여부로 바꾸어 말하지 않는다.

현재는 기능을 새로 더 만드는 것보다 **실제로 쓸 수 있는 후보·사진·리뷰를 확보하고, 적은 입력으로 이어지는 흐름을 검증하는 일**이 중요하다. 사용자에게 보여줄 수 있는 데이터와 모델의 실제 성과가 아직 충분하지 않다.

핵심 상태:

- 최신 v5 Stage1/2 구현은 로컬 원본에 반영되어 있다. GitHub에 최신 변경 전체가 올라간 상태는 아니다.
- 기본 모델은 `hybrid_v5`; `hybrid_v6`는 실험/optional shadow다. 실제 사람 평가0개여서 성능 향상은 미측정이다.
- 200곳 후보 처리 코드/회귀 시험은 완료. 도시마다 실제200곳을 확보했다는 뜻은 아니다.
- Apify 키는 준비된 이력이 있지만 소비자 리뷰 기능은 OFF다. 키 설정·수집 성공·이용 범위·품질 승인은 다른 상태다.
- 최신 브라우저 E2E는 도구 접근 차단 때문에 미완료다. API/DOM 시험 통과를 실제 화면 완료로 바꾸지 않는다.
- 다음 일을 고를 때 `docs/service-v5/IMPLEMENTATION_STATUS.md`의 단일 최신 표를 먼저 읽는다.

## 2. 프로젝트를 잃지 않고 넘기는 방법

### 같은 컴퓨터에서 이어갈 때

**아래 폴더를 그대로 Claude의 작업 대상으로 연다.** GitHub를 다시 clone한 폴더나 `/private/tmp` 폴더로 바꾸지 않는다.

```text
~/Desktop/ChatGPT/travel-inbox-rag
```

- 저장소: https://github.com/Jin-s-work/Travel-Agent
- 작업 브랜치: `codex/private-beta-launch`
- 인수인계 시 HEAD: `5da54ddc1db56168fff735755813aef7587b313b`
- 최신 변경에 미커밋/미추적 파일이 많이 포함되어 있다. 이는 오류가 아니라 현재 작업 상태다.
- 루트 [CLAUDE.md](../../CLAUDE.md)를 입구로 사용한다.
- [시작 프롬프트](CLAUDE_CONTINUE_PROMPT.md)를 붙여 넣어 읽기/검증부터 진행한다.

`git reset --hard`, `git clean -fd`, 강제 checkout, 무조건 stash, ‘깨끗한 최신 main으로 초기화’를 하지 않는다. 특히 untracked인 v5 코드·문서·테스트를 누락하면 기능이 사라진다. 현재 tracked/untracked 목록과 주요 코드 hash는 [WORKSPACE_SNAPSHOT_2026-10-08.json](WORKSPACE_SNAPSHOT_2026-10-08.json)에 있다.

### 다른 컴퓨터/Claude 웹 대화로 옮길 때

이 인수인계와 시작 프롬프트만 첨부하면 제품 맥락은 전달되지만 **실제 최신 코드까지 전달되는 것은 아니다.** 웹 대화에서는 로컬 경로가 자동으로 읽히지 않으므로 사용 중인 도구에서 파일 접근/프로젝트 첨부를 별도로 준비한다.

코드는 현재 작업 폴더의 tracked 및 untracked 소스/테스트/문서/자산을 보존해야 한다. Git push를 선택하면 변경 파일을 검토해 명시적으로 stage한다. 현재 HEAD와 원격의 차이를 확인하지 않고 강제 push하지 않는다. 인수인계 요청 자체로 push/배포는 실행하지 않았다.

전달 대상에서 제외: `.env*`의 실제 비밀 파일, DB/개인 메일 원문, `data/`의 실자료, 세션·공급자 응답, 발표 대본, `.venv/`, `node_modules/`, 개인 브라우저 프로필. 비밀은 새 기기의 안전한 설정 저장소에서 따로 구성한다. `.env.example`은 이름/사용법 템플릿이라 포함할 수 있다.

**주의:** `docs/presentation/v4/SCRIPT.md`는 현재 Git 추적 중이다. ignore 규칙은 추가되어 있지만 이미 추적 중인 파일을 자동 해제하지 않는다. 사용자는 발표 대본을 GitHub에 올리지 말라고 했다. 다음 게시 전 index/기존 공개 여부를 확인하고 대본을 새 commit에 포함하지 않는다. 이번 문서 작업에서는 Git 추적 상태를 변경하지 않았다.

### 임시 환경 의존 제거

이전 native 시험 폴더 `/private/tmp/going-hybrid-verify.uPktYX`와 Python `/private/tmp/going-stage1-native313/bin/python`은 macOS 임시 경로다. 재부팅/청소로 없어질 수 있다. 최신 작업은 원본으로 동기화했고 최종 byte hash54파일을 대조했다. 필요한 결과 로그는 `docs/service-v5/reports/`에 보존했다. 새 작업자는 이 임시 경로를 필수 실행 환경으로 삼지 않는다.

## 3. 요구사항이 바뀐 부분

| 주제 | 예전 방향 | 현재 기준 |
| --- | --- | --- |
| 서비스 이름 | Travel Inbox → 여정 | **고잉(Going)** |
| 가입 | 본인/초대된 친구 allowlist | **초대 코드 없는 Google 로그인**, 개인 데이터는 인증·소유권으로 격리 |
| 호스팅 | 영구 디스크를 둔 SQLite 단일 서버 검토 | 기존 **Render Free + Supabase Free hii** 재사용 |
| 메일 AI | 무료 전환 과정에서 외부 호출 OFF | **gpt-5-mini / text-embedding-3-small**, 설정된 예산 내 사용을 사용자 선택 |
| 추천 대상 | 도쿄/바르셀로나 우선 | 도시 입력100개, 도시별 실제 자료/언어 정책 지원은 별도 |
| 후보 상한 | 단일 공급자 응답60 | **고유 pool 최대200**, raw400·quota·deadline 별도 |
| 디자인 | 초록 Toss 참고, 이후 Airbnb 참고 | 현재 Pretendard·절제된 petrol 색 UI 유지. 대규모 재디자인보다 흐름 개선 |
| AI 진입 | 더보기 내부 | 메인 입력과 **여행에 물어보기** |
| 기능 설명 | 미확인 목록을 모든 카드에 반복 | 핵심 이유와 중요한 미확인만 간단하게, 상세에 근거/분모 |
| 발표 | 자료/대본/영상 반복 제작 | 발표 종료. 현재 우선순위는 서비스. 대본 GitHub 금지 |

옛 PRD를 읽고 초대코드·유료 서버·60개 제한·과도한 입력 UI를 다시 넣지 않는다. 과거 사용자가 ‘진짜 데이터처럼’ 보이기를 원했어도 합성/미검증을 실제로 포장하는 구현은 하지 않는다. 자연스럽고 간결하게 설명하되 사실을 바꾸지 않는다.

## 4. 기능별로 무엇이 있는가

‘구현’은 코드가 있다는 뜻이다. 최근 실제 배포/모든 기기 검증까지 뜻하지 않는다.

| 기능 | 구현 내용 | 현재 주의점 |
| --- | --- | --- |
| 인증/여행 | Authlib Google OIDC, 세션/회수, 여행 생성·전환, 도시 구간·인원·날짜 | 초대 없음. 타인 ID404, Origin/CSRF 유지. 신규 실제 Google 계정/다른 물리 컴퓨터 최종 검증 별도 |
| 메일/예약 | txt/eml 검사·업로드, 복수 예약/왕복 항공, 구조화 사건, 사용자 교정·출처, 수동 예약 | 실패 전에 기존 활성 예약 삭제 금지. 무료 규칙 파서와 AI 분석은 구분 |
| 질문 | 날짜 전체 예약 SQL 조회, 본문 검색/근거 답변, 메인 질문 진입 | 하루8개+수동1개 모두 조회. 질문/출처 전역 공유 금지 |
| 작업 신뢰성 | SQL job/events, SSE 이어받기, idempotency, 단일 dispatcher, lease/fencing/checkpoint | 새로고침은 기존 job 조회. unknown 과금 무조건 환불/재호출 금지 |
| 저장/검색 | 여행별 활성 세대, SQL 예약/교정이 기준, 원문 private storage | Chroma/PostgreSQL 장애를 ‘예약 없음’으로 숨기지 않음 |
| 여행 맥락 | 도시/방문일/인원/숙소 상속, 조건 preview,4자리 연도, 선택 입력 | 모호한 체류/중복 숙소만 추가 확인. 별도 필수 질문 남발 금지 |
| 장소 보관함 | 이름/지도·공식 URL/메모, 저장과 지점 식별 상태 분리 | 같은 이름 다른 지점 자동 병합 금지. URL SSRF 검사 |
| 장소 사실/비교 | 운영/예약/가격/인원 등 필드별 근거, 같은 조건에서 최대3곳 비교 | 총좌석/예약 링크를 특정 날짜 잔여석으로 추론 금지 |
| 추천 | 현지어/유명한 곳/일반 참고 구획, 후보200 검증, 저장된12개 페이지, 설명 | 실제 자료 부족이 가장 큰 제약. 기준 완화는 사용자 새 요청 |
| 숙소/거리 | 선택 원점→확인 숙소→선택 동네→도심, 내부 Haversine 거리 | 숙소 근접 정렬과 숙소 주변 신규 수집은 다름. 실제 경로 unknown≠0분 |
| 사진 | 정확한 지점의 허용 사진 최대3, 음식/내부/외관/unknown, 상태/TTL/lazy load | 자료가 없는 식당이 많음. 실제 img load 최신 검증 미완료 |
| 리뷰 연구 | fake/Apify adapter, 정규화, T/C/U/L/K, 로컬 언어 판별·평가, admin 계약/연결/수집 | 실제 이용 정책·지점 연결·품질 미통과. consumer OFF |
| 일정 | 고정 예약 우선, 영업·양쪽 이동·체류·시간대/DST, 미배치 이유 | strict unknown 미배치, 잠정 허용은 명시 선택. 일정 성공과 validated 구분 |
| 일정 편집 | add/remove/move/lock/unlock preview→apply, version409,10회 이상undo | 원 일정/외부 예약을 preview에서 변경하지 않음. undo도 새 revision |
| 예약 준비 | 할 일/실제 예약 구분, 오픈일 규칙, ICS, 현지어 문의 초안 | 링크 클릭≠완료, 사용자 완료≠증빙 확인. 자동 문의/예약/결제 없음 |
| Plan B/오늘 | 비/휴무/피로 등 구간 대안, 다음 예약 유지, 오늘 현지 날짜/이동 | 실제 날씨/대기/잔여석을 만들지 않음. 원 일정 전체 재생성 아님 |
| 오프라인 | 여행별 최소 읽기 snapshot, 검증 후 활성화, 계정 namespace/로그아웃 정리 | 기본OFF, 기기 검증/저장 허용 필드 필요. 개인 API 통째로 SW 캐시 금지 |
| 피드백/분석/지출 | 관심 없음/방문 경험 구분, 사실 신고 검토, 최소 이벤트, 통화별 비용 | 동의 전 분석OFF, 메모/개인정보 제외, 미측정≠0%, JPY/EUR 임의 합산 금지 |
| 운영/복원 | health live/ready, launch preflight, 백업/삭제 checkpoint/격리 복원 도구 | 실제 off-host 자동 백업 연결은 남음. Free의 상시가동/자동 백업을 보장하지 않음 |

## 5. 아키텍처와 수정할 위치

백엔드는 Python/FastAPI, 프런트는 HTML/CSS/vanilla JavaScript PWA다. React/Next.js 프로젝트로 전제하지 않는다. 기존 모듈을 재사용하는 편이 현재 복구·소유권 계약을 지키기 쉽다.

| 영역 | 주요 경로 | 역할 |
| --- | --- | --- |
| 앱 조립 | `api.py` | create_app/lifespan, 공급자·서비스·dispatcher 연결 |
| 설정/인증/모델 | `src/foundation/settings.py`, `auth.py`, `models.py`, `repository.py`, `routes.py` | 개인 데이터의 기준 모델/접근 검사 |
| 메일 | `src/foundation/documents.py`, `local_mail.py`, `src/reliability/handlers.py`, `providers.py` | 원문/추출/임베딩/교정 흐름 |
| 질문 | `src/foundation/search.py`, `src/agent.py`, `src/rag.py` | SQL 날짜 범위와 검색/생성 답변 |
| 내구성/비용 | `src/reliability/` | jobs, dispatcher, budget, generations, maintenance |
| cloud 저장 | `src/storage/postgres.py`, `objects.py`, `vectors.py`, `artifacts.py`, `transfer.py` | PostgreSQL/private object/pgvector/checkpoint/이관 |
| 탐색 | `src/discovery/context.py`, `intents.py`, `service.py`, `routes.py` | 여행 맥락·후보·조건·저장 연결 |
| 공용 후보 | `src/discovery/public_places.py`, `candidate_pool.py` | Photon 공개 지역 조회/캐시/제한/200pool |
| 숙소 | `src/accommodations/` | 숙소/원점 선택과 버전 의존성 |
| 사진 | `src/discovery/photos.py`, `photo_provider.py`, `photo_schema.py`, `safe_fetch.py` | 지점/권리/manifest/네트워크 검사 |
| 리뷰 공급자 | `src/providers/reviews.py`, `apify_reviews.py`, `fake_reviews.py` | 교체 가능한 provider 계약 |
| 리뷰 정책/언어 | `src/research/` | contracts/collection/normalization/metrics/language/evaluation/observation |
| 추천 | `src/recommendations/` | engine/general/service/pages/registry/hybrid_v5/v6/benchmark |
| 일정 | `src/itineraries/` | scheduler/constraints/travel_time/edits/service |
| 여행 도구 | `src/travel_tools/` | preparation/rules/exports/alternatives/today |
| 피드백/지출 | `src/product/` | feedback/events/reporting/prices/evaluation |
| 운영 | `src/operations/`, `Dockerfile`, `render.yaml` | launcher/health/복원·제어/배포 |
| 메인 화면 | `web/index.html`, `web/js/foundation.js`, `home.js`, `workspace.js` | 앱 상태/탭/대화/공통 UI |
| 탐색 화면 | `web/js/simple-discovery.js`, `discovery-flow.js`, `discovery-experience.js`, `recommendation-pages.js` | 최소 입력/오래된 응답 차단/카드/페이지 |
| 기타 화면 | `accommodations.js`, `review-lab.js`, `travel-tools.js`, `product.js`, `offline-store.js` | 해당 기능의 실제 화면 연결 |
| 디자인/PWA | `web/css/design-system.css`, `foundation.css`, `web/fonts/`, `web/sw.js` | Pretendard·토큰·반응형·캐시 |

### 저장 구조

로컬: SQLite + 비공개 파일 경로 + Chroma. 운영: Supabase PostgreSQL `travel` 스키마 + 비공개 `travel-private` 버킷 + pgvector. Render `/tmp/travel-cache`는 임시 작업용이며 원본 데이터 저장소가 아니다.

users/sessions/trips/trip_stops, source_documents/document_generations, bookings/booking_events/booking_overrides, jobs/job_events, 세대/예산 ledger, 장소/근거/recommendation_runs, 일정/revisions 등의 기존 모델을 유지한다. 구체 migration은 현재 schema 코드가 기준이다. v5 Stage1/2에서 파괴적 새 migration은 도입하지 않았다.

서버가 각 개인 API에서 소유권을 검사한다. Supabase RLS만으로 완료라고 설명하지 않는다. 익명/브라우저가 server secret로 DB/Storage에 직접 접근하게 만들지 않는다. 관리자 공용 리뷰 job을 임의 개인 여행 안에 넣지 않는다.

## 6. 최신 v5에서 추가된 것

### Stage1: 입력을 줄이고 여행에서 저장·일정까지 연결

- `inherited_context_v2`: 현지 오늘/여행 기간/도시 구간/숙소를 재사용하는 서버 맥락.
- 쓰기 없는 context preview와 간단한 ‘조건 수정’, 인원 미정/null, 날짜 검증.
- 현지어/유명한 곳 상태를 독립 표시하고 필터 변경 중 기존 결과 유지.
- `discovery_result_v1`: 저장된 run의12개 페이지, 서명 cursor, counts/freshness/availability.
- 저장 실패/성공 후 조회 실패 구분, 저장 성공 유지, 선택 장소/날짜를 기존 일정 preview/apply로 연결.

### Stage2: 후보·관측·사진·평가

- `ExpandedPublicDiscovery`: 응답당60 제한을 유지하면서 최대7공개 지역을 합침. 고유200/raw400/기본120초/기존일일한도 별도.
- 공개 영역별 cache/query hash·건수·중단 이유·checkpoint. 완료 조회 재호출 방지, 조회 설정이 바뀌면 새 요청 요구.
- pool 전체 평가 후 순위 저장, 추가 보기 외부 호출0. 200번째 최적 후보가 상위에 들어오는 시험 완료.
- `bounded_observation_v1`: source continuity를 주장하지 않는 별도 연구 계약. 날짜 불명은 기간 집계 제외, 고유 수신 상한에는 포함.
- 사진7상태, 만료/지점 미일치를 일반 무사진과 구분.
- v6 음식 별칭/cuisine 근거·유명함 유형/cohort interleaving, shadow 비교.
- 독립 정답 recall/고정분모 precision/NDCG/paired bootstrap/누락 도시·방식/미라벨 gate 및60개 블라인드 작업 양식.
- `stay_origin_v2`: 일반 메일 분석이 trip.version을 올려도 실제 숙소 원점이 그대로면 추천을 잘못 중단하지 않게 수정. 과거 v1 snapshot은 기존 방식으로 재검증.

자세한 요청과 구현 차이는 [v5 PRD](../service-v5/PRD.md), [Stage1](../service-v5/reports/STAGE1_IMPLEMENTATION.md), [Stage2](../service-v5/reports/STAGE2_IMPLEMENTATION.md)를 읽는다. 기존 v5 구현 프롬프트를 처음부터 재실행하지 않는다.

## 7. 추천·리뷰에서 정확히 지켜야 할 의미

### 후보200은 추천200/도시 전체/리뷰200과 다르다

- 최대 고유 장소 pool200, raw400, 초기/페이지12, 지점별 고유 리뷰200, 관측180일.
- 현재 무료지도 기본 quota: 전역20회/일·사용자5회/일·15초 간격. query7보다 quota가 우선한다.
- 공개 도시 중심과 인접 지역 샘플링이다. 개인 숙소 좌표를 보내지 않고 내부에서 거리 비교한다. 숙소 주변을 새로 전수 조사한 것처럼 말하지 않는다.
- 합성 검증은4조회240수신→200평가,185수신→37고유/148중복. 실제 도시별 수집 성과가 아니다.
- 같은 이름만으로 같은 지점이라고 결정하지 않는다. canonical ID·주소·좌표·외부 ID·출처 연결을 확인한다.

### 리뷰 소비자 공개는 아직 OFF

Apify dataset offset은 Google 원천 page/cursor가 아니다. 현재 어댑터의 `source_pagination_observable=False`를 바꿔 strict를 통과시키면 안 된다.

B는 반환된 표본의 검증된 개별 날짜/원문/언어만 설명한다. ‘최신200개’, 전수/주민 비율을 주장하지 않는다. 연구 플래그만 켜도 공개되는 구조가 아니다. B의 소비자 공개는 별도 승인/노출 계약 연결이 남아 있다.

T=본문 존재 확인, C=언어 판별, U=미판별, L=현지어, K=한국어다. **T=C+U, L+K≤C**. 표시 L/C·K/C, 보수 L/T·(K+U)/T. 분모0은null. 전체 플랫폼 평가 수를 언어 분모로 쓰지 않는다. 번역만 있어도 본문 존재가 확인되면 T/U, 추출 자체가 불명확하면 extraction_unknown이다.

초기 기준 C≥100, U/T≤10%, L/T≥60%, (K+U)/T≤10%; 동일 플랫폼4.2/5·평가200 이상은 별도 조건. ‘관측 본문 모두 현지어’는 L=T=C, K=U=0과 추출 불명0/나머지gate가 필요하다. 도쿄ja,바르셀로나es/ca 정책을 다른100개 도시에 자동 복사하지 않는다.

로컬 Lingua2.2.0을 사용하며 모든 리뷰를 LLM에 보내지 않는다. 원문을 사람이 검토한 실제 언어 정답이 없어 precision/recall은 미측정이다. 한국어 정답이 없으면 recall=null이고 통과가 아니다.

### 모델은 추가했지만 실제 향상을 입증한 것은 아니다

v5는 결정적 비학습 content/context 모델이다. TF-IDF cosine, 같은 플랫폼/도시/유형의 평균 수축 평점, Haversine 거리, 구획별 근거를 쓴다. 미확인 feature는null이며 가중치를 몰래 재분배하지 않는다. 사진 개수는 점수에 없다.

v6는 다국어 음식 별칭/cuisine, 유명함 유형/cohort 내부 정렬 후 interleaving을 실험한다. 기본v5를 자동 전환하지 않는다. 실제 blind holdout40(도시당20)과 paired 평가 등 충분한 근거가 필요하다. 현재 12개 합성 개발질의에서 두 모델 점수가 같았고 **실제 사람 라벨/holdout0**이다. 과거 높은 합성 NDCG를 정확도99%로 홍보하지 않는다.

## 8. 실제 데이터·배포·검증 상태

### 실제 자료 감사: 2026-10-08 17:55 KST

| 도시 | 현재 식당 후보 | 활성 실제 리뷰 집계 | 승인 리뷰 지점 연결 | 허용 사진 |
| --- | ---: | ---: | ---: | --- |
| 도쿄 | 50 | 0 | 0 | 2곳/5장 |
| 바르셀로나 | 3 | 0 | 0 | 2곳/6장 |

이 수치는 읽기 전용 Supabase 감사다. 여행 조건 전체 통과 수/실제 사진 네트워크 성공률이 아니다. 다른 도시 카탈로그와 최신 자료는 다음 작업에서 재확인한다.

Apify 당시 Free 잔여 크레딧은 거의$5였으나 유효 이용 정책/공급자 계약/지점 연결/실제 품질평가0, 연구/공개OFF, 현재 준비 도구의 유효 승인/설정 상한$0였다. 실수집 run0회다. 당시가격/계정잔액은 다음 실행 전 재조회한다. 제안 배치$1을 새로운 지출 승인으로 해석하지 않는다.

### 배포

기록된 서비스 주소: **https://travel-inbox-rag.onrender.com**.

마지막 확인된 인증 변경 기록은 2026-10-08 **16:14 KST**, Render 앱 커밋 `d154dc0`의 초대 없는 Google 로그인이다. 기존 Google 로그인/여행/새로고침을 당시 확인했다. 이 인수인계 작업에서 사이트를 다시 로그인하거나 배포 상태를 재검증하지 않았다. **로컬 v5 코드가 이 운영 URL에 반영된 상태가 아니다.** 로컬 HEAD·native HEAD·배포 앱 commit이 서로 다르므로 다음 release 전에 실제 차이를 대조해야 한다.

### 시험

| 검증 | 결과 | 제한 |
| --- | --- | --- |
| native 전체 Python |1264 passed/17 skipped | 마지막 소규모 보완 전; 별도 PostgreSQL 환경 일부 미실행 |
| 마지막 영향 범위 |89 passed +64 passed | 별도 실행들. 합쳐서 전체 고유 테스트 수로 쓰지 않음 |
| 원본 핵심 Python |138 passed,128.80초 | 최신 반영 후. 원본 전체 Python 재실행 아님 |
| native/원본 JS |각219 passed | DOM harness. 실제 브라우저 화면 검증과 다름 |
| 자산 hash/수정 범위 공백 |통과 | 관련 로그/검증 보고서 참조 |
| 합성5명·메일5+추천5 |오류0/재시도0 | fake provider·네트워크 미모사·cooldown 가속 |
| 로그인 이후 최신 브라우저 |미완료 | Codex 도구에8767 접근 차단 저장 |
| 실제 신규 리뷰/최종 사람 평가 |미실행/0 | 데이터 이용/검수/비용·정답 자료 부족 |

합성 부하: 저장 결과 GET p95 614.35ms,큐p95 3378.73ms,peakRSS약336.7MiB. Render/Supabase 운영 성능 보장이 아니다. 과거 별도 cloud 시험 통과 기록도 있지만 최신 v5의 모든 PostgreSQL 검증을 대신하지 않는다.

보고서/로그는 [최신 진행 기록](../service-v5/IMPLEMENTATION_STATUS.md), [자료 범위](../service-v5/reports/DATA_COVERAGE.md), [관측 ADR](../service-v5/reports/REVIEW_OBSERVATION_ADR.md), [평가](../service-v5/reports/RECOMMENDATION_EVALUATION.md), [실측 JSON](../service-v5/reports/stage2-measurements.json)에 있다.

## 9. 실행 환경과 안전한 시작

현재 확인한 도구는 `/opt/homebrew/bin/python3.13`, Node/npm이다. 당시 검증은 Python3.13.5,Node24.1.0이었다. Python3.14에서 Chroma import가 멈춘 이력이 있어3.13을 기준으로 삼는다. Claude CLI는 이 인수인계 시 shell PATH에서 확인되지 않았다. 별도 도구를 설치하거나 계정에 로그인하지는 않았다.

### 기존 폴더/환경 먼저 확인

```sh
cd ~/Desktop/ChatGPT/travel-inbox-rag
git status --short
git branch --show-current
git rev-parse HEAD
python3.13 --version
node --version
# 기존 .venv가 있을 때만 버전 확인
# .venv/bin/python --version
```

이미 있는 가상환경을 무조건 덮어쓰지 않는다. `.venv`가 없을 때만 생성한다. 새 환경의 설치 예시:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.txt
npm ci --ignore-scripts --no-audit --no-fund
```

이는 재현 절차이며 **이번 인수인계 작업에서 새 설치를 실행한 것은 아니다.** Docker는 `requirements.lock`을 사용한다. 설치가 기존 환경과 맞지 않으면 오류를 기록하고 새 패키지 버전을 무작정 올리지 않는다.

### 외부 키 없이 화면 확인

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/stage1_browser_fixture.py
```

- 앱 `http://127.0.0.1:8766`
- 합성 OIDC `http://127.0.0.1:8767`
- 사용자 A/B, 도쿄/바르셀로나 합성 여행/예약/장소. 외부 유료 provider를 사용하지 않는 fixture.
- 실제 Google 계정이나 운영 개인정보를 쓰지 않는다. 임시 자료와 운영 자료를 혼동하지 않는다.
- Codex에서는8767에 저장된 도구 접근 차단이 있어 로그인 이후 검증을 못 했다. **앱이 모든 브라우저에서 로그인 불가하다는 진단은 아니다.** Claude의 브라우저 환경에서도 권한을 확인하고, 저장된 차단이 있으면 다른 포트/쿠키 주입 등으로 우회하지 않는다. 사용자에게 정확한 도구 설정을 설명한다.
- 기존8766/8767 프로세스가 있으면 소유 프로세스를 확인한다. 사용자가 켜둔 서버를 임의 종료하지 않는다.

확인 순서: 여행 선택→방문일/숙소 상속→현지어/유명한 곳 전환→사진 상태→저장→상세/비교→일정 preview→apply→새로고침→undo. A/B 소유권,390px/200%확대/키보드/긴 다국어명/모달 포커스도 확인한다. 권한이 없어 못 한 시험은 미완료로 남긴다.

### 코드 시험

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests -q
node --test tests/*.cjs
.venv/bin/python scripts/version_web_assets.py --check
```

`tests/conftest.py`가 임시 경로/가짜 공급자/개인 설정 격리를 만든다. 테스트에 운영 DATABASE_URL을 연결하지 않는다. `PYTHON_DOTENV_DISABLED=1`은 .env 자동 로딩을 막지만 이미 shell에 export된 모든 값까지 지우지는 않으므로 fixture의 명시 설정과 격리를 유지한다.

최근 v5 핵심 시험:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest \
  tests/test_v5_candidate_retrieval.py tests/test_v5_review_observation.py \
  tests/test_v5_photo_states.py tests/test_v5_ranker_evaluation.py \
  tests/test_v5_concurrent_workload.py tests/test_v5_pilot_plan.py \
  tests/test_v5_context.py tests/test_v5_result_pages.py \
  tests/test_stage2_review_contracts.py tests/test_review_integration.py \
  tests/test_restaurant_photos.py -q
```

UI 코드를 바꾼 경우 먼저 `python scripts/version_web_assets.py`로 public asset hash를 갱신한 뒤 `--check`와 관련 JS 시험을 실행한다. 새 UI 해시가 없으면 이전 PWA 자산 때문에 코드가 반영되지 않은 것처럼 보일 수 있다.

별도 PostgreSQL: `tests/postgres_plugin.py`는 **localhost disposable DB만** 허용하며 각 테스트 schema를 만들고 제거한다. `TRAVEL_TEST_POSTGRES_DSN`을 준비한 뒤 `python -m pytest -p tests.postgres_plugin ...` 경로를 사용한다. 운영 Supabase DSN을 넣거나 localhost 검사를 제거하지 않는다. cloud schema/객체/pgvector 시험 목록은 `tests/test_cloud_storage.py`, `test_postgres_migration_concurrency.py`, `test_stage2_postgres_migration.py`와 기존 runbook을 확인한다.

### 직접 올릴 메일 샘플

- [메일 테스트 팩](../../examples/mail-test-pack/README.md): 왕복항공,숙소,복수예약,동일파일명,날짜만 있음,하루8건+수동1건,HTML,DST,미확정 변경.
- [시작·종료 시각 팩](../../examples/mail-time-pack/README.md): 시작/종료 현지 시각과 자정 넘김. 각 디렉터리 `expected-results.json`과 비교.

모두 합성이다. ZIP 자체를 메일로 업로드하지 않고 `.eml`을 올린다. 기본 규칙 파서가 목표 의미를 모두 추출하는 것은 아니다. 확인 필요/실패가 맞는 경계 사례와 실제 AI 분석 품질 시험을 구분한다.

## 10. 설정 파일과 운영에서 주의할 것

### 파일 위치만 공유한다

| 파일/위치 | 용도 |
| --- | --- |
| 루트 `.env.example` | 로컬 설정 이름/설명/기본값. 비밀 없음 |
| 루트 `.env` | 로컬 실제 설정이 있을 수 있음. Git 제외 |
| `deploy/render-supabase/.env.example` | 필요한 계정 입력을 줄인 템플릿 |
| `deploy/render-supabase/.env` | 운영 준비용 실제 비밀 입력. **값을 출력하지 않음** |
| `deploy/render-supabase/.env.render` | 준비 스크립트가 생성하는 Render 가져오기용 비밀 파일. Git 제외 |
| Render Environment | 실제 호스팅 secret. 로컬 env가 운영에 자동 반영되지 않음 |
| `deploy/render-supabase/pricing-openai.json` | 코드의 OpenAI 단가/예산 정책, 실가격은 다시 확인 |
| `deploy/render-supabase/pricing-zero.json` | 외부 유료 호출 OFF 정책 |
| `deploy/render-supabase/prod-ca-2021.crt` | Supabase 검증 TLS 인증서. 비밀키가 아님 |

주요 변수: OIDC_CLIENT_ID/SECRET,SESSION_SECRET,PUBLIC_BASE_URL,OPENAI_API_KEY,APIFY_TOKEN,DATABASE_URL 또는 SUPABASE_DB_PASSWORD,SUPABASE_SECRET_KEY,DEPLOY_ADMIN_EMAIL. `DEPLOY_ADMIN_EMAIL`은 운영 준비값이며 일반 사용자의 초대코드가 아니다.

`SESSION_SECRET`을 바꾸면 기존 세션에 영향이 있으므로 단순 편의로 재생성하지 않는다. DB 비밀번호를 임의로 초기화하지 않는다. key 존재 확인은 이름/존재 여부만 출력하고 값을 인수인계에 붙이지 않는다. dotenv 파일을 shell `source`로 실행하지 않는다.

### 현재 배포 프로필

- 기존 Render 서비스 `travel-inbox-rag`,Free/Docker/instance1/자동배포OFF.
- Supabase `hii`,전용 스키마travel,private 버킷travel-private. `sta-saju`는 별개 프로젝트다.
- Session pooler5432/TLS verify-full을 사용한다. 기존 reader/session 계약 때문에 transaction pooler6543으로 임의 변경하지 않는다.
- 공개 URL `https://travel-inbox-rag.onrender.com`
- Google callback `https://travel-inbox-rag.onrender.com/api/v2/auth/callback`
- 단일 uvicorn worker/dispatcher, 운영 seedOFF. cloud 설정이 잘못되면 기동 실패/보호된 상태여야 한다.
- `/health/live`, `/health/ready`는 유료 API를 호출하지 않는다. 공급자 장애가 저장된 SQL 예약 조회까지 막으면 안 된다.

설정 **형식만** 확인하는 명령:

```sh
.venv/bin/python scripts/prepare_render_env.py --profile openai --with-apify --check
```

`--check`는 연결·로그인·권리 승인을 대신하지 않는다. `--check`를 빼면 private `.env.render`를 생성한다. 새 호스팅 배포는 아니지만 비밀 파일을 쓰므로 목적을 확인하고 실행한다.

특히 기본 `--profile free`는 OpenAI 키를 비우고 외부 호출을 OFF로 만든다. 사용자 요청은 호스팅 Free를 유지하면서 소액 OpenAI 메일 분석을 쓰는 방향이므로 **무료 서버라는 이유로 free 프로필을 덮어쓰지 않는다.** 기존 코드 예산은 전역$0.50/일·$3/월,사용자$0.25/일·$1/월(UTC)이다. 이는 서비스 내부 한도이며 공급자 계정 전체 비용·최신 요금 보장이 아니다.

Apify 토큰을 Render에 전달하는 것과 리뷰 연구/소비자 공개/단가 설정은 별도다. 최신 build 자동 추적 대신 현재 pin/계약을 확인한다. 현재 pin0.0.527,당시 API latest0.0.528이었다.

Free Render는 sleep/cold start가 있고 Supabase Free도 공유 사용량·pause 등 제약이 있다. 최신 가격/제약은 실제 배포 전 공식 문서와 콘솔에서 확인한다. 가짜 keepalive ping·자동 증설·auto top-up을 만들지 않는다. 백업/복원 도구는 있지만 **실제 외부 자동 백업 연결은 미완료**다. DB복원/이미지rollback/기능OFF는 서로 다른 절차다.

## 11. 다음 작업 우선순위

### P0 — 인수인계와 남은 검증

목표: 현 코드를 잃지 않고 실제 사용 흐름의 상태를 정확히 확정.

1. 문서/코드/Git 차이를 확인하고 최신 로컬 소스가 포함되었는지 검사한다.
2. 기존 시험 결과를 재사용하되 환경 재구성·실제 수정 영향에 맞게 회귀를 실행한다. PG skip은 disposable 환경으로 보충한다.
3. 허용된 합성 브라우저로 로그인 이후 흐름·사진·접근성을 검증하고 오류를 수정한다.
4. 비밀/대본/개인자료 제외와 stage 대상/운영 image의 차이를 검토해 release 준비본을 만든다.
5. 현재 사용자 요청에 배포/push가 포함된 경우에만 준비된 범위에서 실행하고 운영 버전·테스트를 기록한다.

완료 증거: 입력→추천→저장→일정→재조회,파일별 메일 처리,A/B404,실제 브라우저/PG 시험 결과. ‘코드 시험 통과’만으로 배포 완료 표시 금지.

### P1 — 실제 식당/리뷰/사진 자료

목표: 예쁜 빈 화면보다 실제 고를 수 있는 자료를 늘림.

1. 실제 도시/유형별 후보 수·미확인/제외 원인을 재측정한다. 바르셀로나3곳의 부족을 먼저 검토한다.
2. 합법적으로 쓸 수 있는 자료와 정확한 canonical 지점 연결을 준비한다. 공개지도 cache/검수 카탈로그를 재사용한다.
3. Apify 정책/원격 보관·삭제/실제 필드/지점/가격/기존 승인·무료잔액을 확인한다.
4. 준비된 범위에서1곳×40→3곳×200→두도시10곳×200. 앞 단계 유효 자료 재사용. 조건 부족이면 계획/도구를 남기고 미실행으로 기록한다.
5. 실제 원문 언어를 사람이 독립 라벨하고 개발/holdout 분리·한국어 recall을 평가한다.
6. B 공개가 필요하면 별도 승인/소비자 설명/자격 경로를 구현한다. strict 우회는 하지 않는다.
7. 사진은 무사진/실패를 먼저 분리해 실측하고 권리/지점이 확인된 공급만 보강한다. 허용 사진 실제 img load를 검사한다.

권리 승인이나 필요한 새 지출이 없다면 소비자 리뷰 기능을 OFF로 유지한다. 가상의 수집 결과나 이용 승인 기록을 만들지 않는다.

### P2 — 추천 모델 품질 비교

목표: 새 모델을 만들었다는 사실보다 실제 선택 품질의 개선 증거 확보.

- [블라인드 양식](../service-v5/fixtures/blind-evaluation-plan.json)60개는 아직 빈 작업칸이다. 실제 데이터/라벨로 채운다.
- 후보 확대+v5 효과와 같은 후보+v6 효과를 분리한다.
- Candidate Recall@200의 독립 정답에는 수집에서 누락된 지점도 포함한다.
- NDCG@5/Precision@5/결과5개 충족/다양성/필수 위반/실행시간을 같은 frozen input으로 평가한다.
- label≥2,Precision분모5,gain2^label−1,paired bootstrap 및 도시/추천 방식 퇴행을 확인한다.
- 근거가 부족하면v5 유지. 모델 변경 때문에 필수조건·언어품질을 완화하지 않는다.

### P3 — 실사용을 보면서 하나씩 개선

메일 실제 형식 지원, 저장한 장소 재사용, 숙소 위치 확인, 사진 자료 보강, Plan B/오늘 사용성, 백업/운영 비용을 관측 근거·사용자 가치·비용·데이터 의존성으로 비교한다. 기존 기능이 많은 상태이므로 새로운 화면/폼을 늘리는 것을 목표로 삼지 않는다. 실사용 로그/피드백이 없으면 성과는 미측정으로 둔다.

## 12. 자주 보였던 문제와 조사 순서

| 증상 | 먼저 확인할 것 | 피해야 할 임시 해결 |
| --- | --- | --- |
| 다른 기기 로그인 실패 | 실제 오류단계, Google 대상사용자/redirect, PUBLIC_BASE_URL, secure cookie, state/nonce/세션, cold-start 응답 | 인증/CSRF 삭제, 계정별 데이터 공개, 초대코드 재도입 |
| ‘체류 구간 없음’ | 서버 context preview, 방문일/도시 stop 범위, 겹침/삭제 구간 | 다른 도시로 자동 바꾸기, 예약 날짜 임의 보정 |
| 메일 계속 진행/실패 | job state/error/event, 분석mode, key+단가+예산+외부제어, lease/checkpoint | 반복새job/무조건paidretry, 기존 예약 삭제 |
| 추천0건 | 실제 후보수, 종류/지리/날짜, 언어gateOFF/자료부족, public provider quota/health | hard조건 자동 완화, 합성/다른 도시로 수 채우기 |
| 사진 없음 | no_photo vs failed/expired/policy/identity, 실제 허용manifest, 네트워크 MIME/URL | 인터넷 일반사진/다른 지점/생성사진을 실제사진으로 쓰기 |
| 숙소와 먼 후보 | 검색범위와 정렬원점, 좌표/숙소선택, 공개샘플범위 | 도심 검색을 숙소 주변 실수집이라고 말하기 |
| 메일 처리 중 추천 ORIGIN_CHANGED | 실제 숙소 의존성 변경인지, snapshot resolver v1/v2 | 버전/삭제 검사를 통째로 제거 |
| 새 UI가 안 보임 | asset hash/SW cache/현재 배포commit | 개인 API 전체 cache, 실제배포 안 했는데 반영완료 보고 |
| 배포 뒤 자료 없음 | STORAGE_BACKEND, Supabase/private bucket/DB연결, 현재schema/health | cloud 실패를 임시SQLite로 대체, 임의이관/seed |

## 13. 문서 지도와 읽는 순서

- **현재 사실:** [v5 IMPLEMENTATION_STATUS](../service-v5/IMPLEMENTATION_STATUS.md),Stage1/2 보고서,원본 로그.
- **현재 기획:** [v5 PRD](../service-v5/PRD.md),[AUDIT](../service-v5/AUDIT.md),[Stage1 prompt](../service-v5/prompts/01-simple-discovery.md),[Stage2 prompt](../service-v5/prompts/02-recommendation-quality.md). 이미 구현된 요구가 많으므로 새 구현 목록으로 그대로 읽지 않는다.
- **추천/리뷰 현재 경계:** [관측 ADR](../service-v5/reports/REVIEW_OBSERVATION_ADR.md),[자료 범위](../service-v5/reports/DATA_COVERAGE.md),[평가](../service-v5/reports/RECOMMENDATION_EVALUATION.md),[Apify 설정](../operations/APIFY_SETUP.md).
- **인증 최신 변경:** [초대 없는 가입](../operations/OPEN_REGISTRATION_2026-10-08.md),[기기 로그인 복구](../operations/LOGIN_RECOVERY_2026-10-08.md).
- **구조/오래된 계약:** [v2 TECHNICAL_SPEC](../service-v2/TECHNICAL_SPEC.md),[REVIEW_DATA_SPEC](../service-v2/REVIEW_DATA_SPEC.md),[DATA_AND_OPERATIONS](../service-v2/DATA_AND_OPERATIONS.md),[RUNTIME_PROMPTS](../service-v2/RUNTIME_PROMPTS.md). 초대/저장소/초기 UI 등은 이후 결정으로 대체된 부분이 있다.
- **운영:** [Render/Supabase](../operations/RENDER_SUPABASE.md),[신뢰성](../service-v2/RELIABILITY_RUNBOOK.md),[예산](../service-v2/BUDGET_RUNBOOK.md),[일정](../service-v2/ITINERARY_RUNBOOK.md). 이전문서의 당시 배포commit/후보0/AI OFF는 현재값으로 복사하지 않는다.
- **UI:** [디자인 시스템](../service-v3/DESIGN_SYSTEM.md),`web/css/design-system.css`와 실제 현재UI. CSS의 일부 역사 주석 ‘여정’보다 화면/최신브랜드 고잉이 우선한다.
- **이전 고도화:** [v3 상태](../service-v3/IMPLEMENTATION_STATUS.md),[v4 상태](../service-v4/IMPLEMENTATION_STATUS.md),[v4 모델V3](../service-v4/RECOMMENDATION_MODEL_V3.md). 수치/완료 문장은 당시 범위를 따른다.
- **발표:** `docs/presentation/`,루트README. 발표는 끝났으며 별도요청 전 수정우선순위가 아니다. 대본은 게시금지.

## 14. 다음 작업자가 남겨야 할 결과

매 작업 후 무엇을 바꿨고 왜 필요한지, 코드/화면 위치, 실행한 명령과 결과, 실제 공급자/브라우저/운영 확인 범위, 미측정과 다음 행동을 기록한다. 최신 상태 표는 한곳에 유지한다.

코드 완료 / 합성시험 / 브라우저 / 실수집 / 이용승인 / 언어·추천품질 / 도시 활성화 / 운영배포를 별도로 표시한다. 공개 API에 사람의 비밀/원문/정밀위치를 남기지 않는다. 기존 자료 삭제·타인노출·고정예약 자동변경·무제한과금·복원불가를 단순 UX 개선을 이유로 허용하지 않는다.

인수인계 작성 작업 자체는 문서와 파일지문만 추가했다. 앱 코드 수정·새 라이브시험·DB변경·실수집·추가결제·Git commit/push·배포는 수행하지 않았다.
