# V3 진행 기록

## 2026-10-06 — 체류일 오류·100도시·입력 간소화·메일 체험팩

이 기록은 V3 전체 재설계 완료 보고가 아니다. 상세 PRD 및 두 단계 프롬프트 작성과 아래 한정된 구현/검증을 완료했다. 숙소 자동 지점 확인·도보 경로·전체 메뉴 재편·단일 탐색 intent API는 후속 단계다.

### 구현

- 100개 도시 registry(`world-100-v1`): 한국어/영문/현지 별칭, 국가, IANA timezone, 기본 통화. city/zone/currency 검증과 소유 여행·탐색 조건·팩 등록에 연결. 운영 보고서 도시 선택과 예약 준비 timezone도 등록부 사용. 기존 여행에는 읽기 시 등록부 적용하므로 Madrid 자료를 고치거나 다시 만들 필요 없음.
- 등록된 도시의 탐색 범위를 실제 후보 확보와 구분. 실제 자료 없는 도시는0곳. 엄격 리뷰 수집/언어 정책은 기존 도쿄·바르셀로나 검증 범위에서 임의 확대하지 않음. 새 유료 공급자 호출 없음.
- `stay_options`·`context_state`·필드별 오류 추가. 도시를 바꾸면 해당 도시 체류일을 제안. 비연속 재방문 공백은 검증 거절. 저장된 조건도 여행 수정 후 재검증하며 버전 경쟁 시 stale snapshot job을 만들지 않음.
- 여행 생성에서 도시부터 입력하고 이름은 미입력 시 자동 생성. 이름/아동·도시별 날짜/시간대는 펼치기. 도시 입력 시 시간대 자동 적용. 접힌 invalid 필드는 펼쳐서 수정 가능. 서버의 안전 제약 유지.
- 공통 date/datetime-local에 0001~9999 범위, 날짜 문자열 helper에0000 거절. native 달력 유지. 정적 예약 날짜 필터도 같은 범위.
- 합성 eml12개·기대 의미 JSON·한국어 사용법·다운로드ZIP 제공. 메일 화면에 ‘테스트 메일12개 받기’. 실제 예약/업체가 아니며 운영에서 fake 추출 우회를 추가하지 않음.
- public shell v13, content hashes 갱신. 개인 API/ZIP 다운로드는 일반 service-worker 캐시 대상이 아님.
- DB DDL/migration 없음. 기존 데이터 삭제/예약시간 변경/비밀 변경 없음. 신규 도시 설정은 배포 코드에 포함됨.

### 시험 결과

1. `PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q`: **748 passed / 11 skipped / 2 warnings, 82.73s**. 11건은 기존 외부 환경 의존 skip이며 실운영 Postgres 검증으로 간주하지 않는다.
2. 이후 운영 보고서/예약 준비 도시 연결에 대한 관련 회귀:
   `PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_destinations.py tests/test_discovery_foundation.py tests/test_recommendation_api.py tests/test_travel_preparation.py tests/test_product_feedback.py tests/test_product_expense_api.py -q`
   **73 passed / 2 warnings, 25.75s**.
3. `node --test tests/*.cjs`: **36 passed**. 최종 asset hash/JS syntax/diff check를 수행했다. 잘못 지정한 nonexistent test file로 한 차례 ‘no tests ran’이 있었고 위 실제 파일 그룹으로 재실행했다. 그 시도를 통과로 세지 않음.
4. 100도시 각각 HTTP 여행 생성→조건 조회→조건 저장, city/zone/currency validation, 별칭 중복, 모든 ZoneInfo 존재, 잘못된 도시-timezone 조합 거절.
5. Madrid 후보0에서도 기본 조건 ready, bookmark 저장 가능, 비용 없는 추천 요청 접수. 비연속 체류일422+필드 오류, 여행 수정 후 기존 조건409+job 생성0. 테스트 idempotency key 최소 길이를 보완 후 통과.
6. 12개 eml 실제 로더 정상, synthetic 표시·expected manifest·ZIP 일치. 파일별 추출 정확도는 실제 AI를 호출하지 않았으므로 미검증.

### 실제 브라우저

Chrome/macOS, 로컬 합성 OIDC/공급자 서버(`tests/browser_discovery_fixture.py`)를 사용했다. 실제 계정의 여행을 시험용으로 수정하지 않았다.

- 도시Madrid·시작/종료일만 입력→시간대 자동·기본 여행이름으로 생성.
- 탐색에서 마드리드·방문일·인원 상속. 조건 저장을 먼저 요구하지 않고 ‘추천 보기’ 요청→후보0 결과. 별도 조건 편집/저장도 체류일 오류 없이 성공.
- 새로고침 후 여행과 저장한 추천 결과 복원. 수정한 조건은 이전 결과와 구분됨.
- native 날짜 입력 max9999 확인. 연속 숫자 입력에서 연도는4자리이고 다음 날짜 segment로 이동함. 일반2026날짜 입력/저장 통과. 모든 기기에서 동일한 date UI라는 의미는 아님.
- 메일 다운로드 링크에서 ZIP 실제 다운로드, 저장소 원본과 SHA256 동일.
- 실제 CSS viewport390px에서 문서 폭390px/모달scrollWidth=clientWidth353px, 가로 넘침 없음. 데스크톱 폼과 접힌 조건 확인. [화면](reports/simple-trip.png).
- 실물 iOS/Android/Safari, 큰 글자200%, 모든 키보드 흐름, 실제 지도/메일 AI는 이번 변경에서 미검증. 일반 브라우저 console error 관측0.

### 남은 범위

- PRD와 프롬프트1/2에 따른 전체 UX 개편, 숙소 구조화/지점 선택/날짜별 기준점, 실제 도보 경로, 단일 intent 접수는 계획 상태.
- 도시100개는 여행·탐색 입력과 카탈로그 수용 범위. 실제 검수 장소100도시 확보/엄격 리뷰 활성화가 아님. 운영 추천 자료0 상태를 유지한다.
- 무료 운영은 AI 추출/임베딩·유료 지도/검색/리뷰 OFF. 새 메일의 자동 분석 성공을 이 체험팩 제공만으로 보장하지 않음.
- 일부 부가 기능의 기존 범위는 유지: 실제 리뷰 수집 도시 정책은2도시, 예상 지출 수동 가격 입력은JPY/EUR. 전세계 통화의 비용 회계까지 완료했다고 보고하지 않음.
- 운영 배포 결과는 아래에 별도로 추가한다.

### 운영 반영 완료

- 실제 URL: https://travel-inbox-rag.onrender.com . 기존 Render Free/Supabase hii와 기존 비밀·0원 정책을 그대로 사용.
- 커밋 `4c33f2c0748f4c746626cfd5551b30a7b8fab27e`, feature branch `codex/private-beta-launch`. main 변경 없음. 수동 배포 `dep-db29ta3bc2fs73fr64f0`, 2026-10-06 **16:10:43 KST** 서비스Live 로그 확인.
- HTTPS `/`200·새 asset hash 확인, `/health/ready`200(schema/storage/dispatcher/identity/restore 전부true). 운영 ZIP200·로컬 원본SHA256일치.
- 기존 실제 인증 세션으로 운영 탐색 화면에서 등록된 Madrid 조건/기간/인원 상속, 자료0 안내, 추천 버튼을 확인. 운영 여행 생성 모달 datalist100개, date min0001-01-01/max9999-12-31 확인. 사용자의 실제 여행/예약을 임의 수정하거나 테스트 예약을 추가하지 않음.
- 무료 호스트의 기동 대기 화면을 실제 관측한 뒤 정상으로 회복. 무중단·즉시 응답 서비스라고 주장하지 않음.
- SMTP 형식 eml의 의도된CRLF는 `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check`로 검사했다. 일반 whitespace 검사가 CRLF를 trailing whitespace로 표시한 점은 코드 오류와 구분했다.


## 2026-10-06 — V3 1단계: 여행에서 장소 저장까지

앞의 ‘후속 1단계’ 계획을 실제 저장/API/화면으로 구현했다. **2단계 숙소 지점·거리/경로는 미구현**이다. 상세 변경/Before·After·Why/실행 명령/브라우저 범위는 [검증 보고서](reports/stage1-validation.md), 이관은 [MIGRATION_11](MIGRATION_11.md)에 기록했다.

- 홈·탐색·일정·예약4메뉴, 더보기의 기존 기능, 탐색 안의 보관함. 도시/시작/종료일3필수입력,100도시 검색, adult1/아동미확인, 한 도시 기간 상속, 날짜 칩, 필터 모달에서 한 번 추천.
- 여행 기본값과 sparse override/provenance 분리. 기존 명시 조건 보존, stop ID 유지/재방문 구분, 도시·날짜 변경 시 낡은 좌표 무효화. 기존 예약을 자동 변경하지 않음.
- 인증된 discovery-intents POST202/GET, 조건·snapshot·job·receipt 원자적 저장. 중복key 재사용, 버전409, 타인/삭제404. 새로고침 복구/늦은 응답 보호.
- SQLite/Postgres schema11 additive migration. 새 두 테이블 삭제 scrub 포함. 별도 조건 저장 없는 일정 version0 생성·preview·apply·undo 연결.
- 최종 Python **770 passed/11 skipped/2 warnings(107.45s)**, Node **38 passed**, 별도 PostgreSQL **42 passed/1 skipped/2 warnings(48.29s)**. PG skip1인 실제 SQLite kill 시험은 SQLite에서 통과. Asset/JS/diff 검사 통과.
- Chrome/macOS 합성 계정으로 여행→추천→상세→저장→새로고침→서버 재시작 복원. Madrid0후보/이름 저장,390px 및319/321px·글자200%·키보드/포커스 확인. OS 달력 팝업 내부 선택과 실물 모바일/실공급자 품질은 미검증.
- 배포 전 실제 schema10 암호화 snapshot+checkpoint 및 격리 복원 확인. 운영 디스크 밖 로컬 사본, integrity ok, 여행/예약 건수 일치. 비밀과 백업본문은 저장소에서 제외.
- 현행0원/엄격 리뷰OFF 유지.100도시 메타데이터 지원과 실제 추천 자료 확보는 구분한다.

### 이번 운영 반영

- URL: https://travel-inbox-rag.onrender.com . 기존 Render Free/Supabase hii 유지, 유료 설정 변경 없음.
- runtime commit `a3c1285c7ac8688308bf8d4c5e70c5e155703be0`, branch `codex/private-beta-launch`; 수동 배포 `dep-db2aqve0tbcc738imafg`. Render 로그 **2026-10-06 17:13:57 KST Your service is live**, 화면 Deploy succeeded|Live 확인.
- 실제 HTTPS `/health/ready`200: schema/storage/dispatcher/identity/restore 모두true, `/health/live`200, `/`200. 새 JS `7799ae6dcd8e`, CSS `3b1c57cc38c1` 확인. 로그인 없는 `/api/v2/trips`, `/api/v2/cities`401.
- 운영 Supabase READ ONLY 조사: schema11, 기존 users/trips/stops/bookings 수가 백업 전과 일치, 신규 context/intent0개. 새 두 테이블 RLS 활성화. 기존 명시값/개인 자료를 배포 과정에서 바꾸지 않았다.
- 실제 기존 로그인 세션에서 4메뉴→탐색→Madrid 체류일 칩/성인2명 상속, 후보0 안내, 새 여행 모달 New York 검색→뉴욕 선택, min/max 날짜 범위를 확인했다. 모달은 저장하지 않고 닫았으며 새로고침 후 선택 여행/탐색 화면 복원을 확인했다. 관측 console error0.
- 기존 ‘바르셀로나’ 제목의 여행은 등록 도시가 Madrid다. 제목만 보고 도시를 자동 바꾸지 않았다. 실제 희망 도시가 다르면 여행 수정에서 사용자가 변경해야 한다.
- 공개 health의 첫 시스템 Python 시도는 URLError로 실패했으며 이를 서비스 성공으로 세지 않았다. 프로젝트 httpx의 정상 TLS 검증으로 위 결과를 재확인했다.
- 운영 Google OAuth 신규 로그인/로그아웃 왕복을 이번 배포에서 다시 수행하지 않았다. 기존 실세션 인증과 보존 데이터 조회, 로컬 합성 OIDC 전체 흐름을 구분한다.
- Render Free의 기동 대기 화면을 관측했다. 항상 즉시 응답/무중단 보장은 아니다. 전체 운영용 Supabase 복원 재주입과 실제 공급자 품질은 미검증.
- 운영 화면 캡처는 개인 여행이 포함되어 로컬 출력 폴더에만 보관했다. 저장소 보고서 스크린샷은 합성 로컬 자료만 포함한다.


## 2026-10-06 — V3 2단계: 숙소에서 거리 추천·일정 편집까지

앞 절의 ‘2단계 미구현’은 당시 기록이다. 현재는 개인 숙소 저장/지점 후보 선택, 날짜별 출발점, 거리와 경로 분리, v2 추천, 일정 preview/apply/undo 연결을 구현했다. [전체 검증/Before·After·Why](reports/stage2-validation.md), [schema12 이관·복구](MIGRATION_12.md), [공급자 공식 계약 조사](reports/stage2-provider-research.md).

- SQLite/PostgreSQL schema12 additive migration, label-only 기존 숙소 이관, private payload/receipt 삭제 및 restore tombstone 재적용. 임시 PostgreSQL 실제11→12시험에서 기존 행/교정/소유권 보존, RLS·인덱스·반복이관 확인.
- 숙소 이름/링크 한 필드, 후보 직접 선택, 체크아웃/겹침/공백, 숙소 근처 저녁과 명시적 거리 필터, 오래된 결과 안내. 새로고침 완료 job 경쟁과 초기 탭 복원 경쟁도 수정.
- 거리 계산 v2/immutable snapshot, provider OFF/fake/Google 어댑터, 개인 캐시/비용 wrapper/receipt, itinerary 양끝 이동·출발점 version·아동 unknown·통화별 가격 변화 검증. 운영 fake fallback 없음.
- 최종 전체 Python **878 passed/12 skipped/2 warnings(127.81s)**, Node **44 passed**. 임시 PostgreSQL **144 passed/3 skipped(116.39s)**, 마지막 보완 후 일정 PG **17 passed(9.25s)**. 스킵/경고/명령은 보고서에 구분.
- Chrome/macOS 합성 로그인→숙소 저장→후보 선택→저녁 추천→상세/비교→일정 생성/preview/apply→새로고침→undo 새version3, 다른 탭의 숙소 변경 후 apply409 원본/입력 보존.390px와320경계 양쪽,200%글자·Escape/포커스 확인. 실물 모바일과 실제 공급자 품질 미검증.
- 합성 geocoding1회·route8회10elements, 시험ledger11microUSD settled. **실제 Maps/신규 유료 호출0**.
- 실제 schema11 운영 백업을 READ ONLY로 암호화해 운영 디스크 밖에 보관,13.12초. 격리복원0.04초, integrity/FK/소유권 정상, 기존 건수 동일. 실제 운영 복원 재주입은 하지 않았다.
- 등록100도시의 실제 검수 후보는 각각0곳. 0원·지도/경로·엄격 리뷰OFF 유지. 기본 기능 구현과 공급자 활성화/실제 자료 확보는 별도다.
- 실제 운영 반영 결과는 아래와 같다.

### V3 2단계 운영 반영

- URL: https://travel-inbox-rag.onrender.com . 기존 Render Free/Supabase hii 유지, 새 지출·secret·공급자 활성화 없음.
- 실행 commit `9a23999b81c6e587d4854852d228922744b9af4c`, branch `codex/private-beta-launch`. 수동 배포 `dep-db2bkm0ae00c739p61u0`, **2026-10-06 18:09:02 KST** Live 로그, Render **Deploy succeeded|Live**, 소요1분26초 확인.
- HTTPS `/health/live`200, `/health/ready`200(schema/storage/dispatcher/identity/restore 모두true), `/`200, 운영 `accommodations.js` hash가 로컬 구현과 일치. 비로그인 `/api/v2/trips`, `/api/v2/cities`401. [집계 검증 JSON](reports/stage2-live-verification.json).
- 운영 Supabase READ ONLY: schema12, 새 두 테이블 RLS true. 기존 users1/trips2/stops2/bookings1/docs0/recs0/itineraries0 유지. label-only 숙소1개 unresolved, identity 비어 있음·날짜 제안·resolution0. [원본 값 비교](reports/stage2-live-preservation.json)에서 여행·구간·예약·교정·문서의 모든 기존 행 값이 배포 전 격리 백업과 동일했다.
- 실제 기존 로그인 세션으로 홈→숙소 관리→탐색→새로고침 확인. 위치 미확인, 지도 제공자OFF, 지점찾기 버튼disabled, 자료 없는 도시0후보 안내. 과금 job/합성 장소/실제 여행 변경 없이 조회했다. 새로고침 이후 선택 여행과 숙소 출발점 상태 복원, console error0.
- Google OAuth 신규 로그인 왕복, 실제 Google 도보/지점 품질, 운영 Supabase 전체 복원 재주입은 이번 배포에서 미검증이다. 합성 로컬 흐름의 성공과 구분한다.
- Render Free의 기동 대기 화면을 실제 관측했다. 항상 즉시 접속·무중단을 보장하지 않는다. 개인 여행이 포함된 운영 화면은 로컬 출력 폴더에만 저장하고 Git 보고서에는 합성 캡처만 포함했다.
- 작업용 임시 PostgreSQL 컨테이너는 검증 후 제거했다. 운영 DB·다른 컨테이너에는 해당 정리를 적용하지 않았다.


## 2026-10-06 — 식당 후보 표시와 초록 디자인 시스템

- 운영 실제 후보팩 0개였던 원인을 확인하고 공식 출처·지점을 검수한 식당 3도시 × 3곳을 기존 Supabase에 등록했다. 실제 SQL 자료 재사용이며 실시간 외부 식당 발굴을 구현했다고 보고하지 않는다. [출처·범위](reports/official-restaurant-sources.md), [등록·회수 운영 절차](reports/catalog-operations.md).
- 결과 수는 서로 다른 지점 기준으로 조건 충족 추천 / 방문 전 확인 / 참고 후보를 구분한다. 점수 null·영업·인원·가격·거리 미확인을 억지로 채우거나 필터를 완화하지 않는다. 3도시의 이번 기초 자료는 각 3곳 표시 가능·조건 충족 추천 0곳이다.
- 추천 클릭 즉시 상태, 실제 job 단계·처리 수, SSE/새로고침 복원, 이전 결과 보존, 연결·취소·예산·실패별 행동을 적용했다. 가짜 진행률·운영 지연은 없다. 로컬 UI fixture만 실제 단계 기록 직후 테스트용 지연을 넣어 관측했다.
- toss.md를 주 시각 참고, yeogi.md를 여행 문맥 참고로 적용했다. [초록 디자인 시스템과 Before/After/Why](DESIGN_SYSTEM.md). 숙소/거리 선택은 접힌 옵션, 핵심 미확인은 카드에 유지하고 추가 항목만 접는다. 영업시간은 한국어 요일·다음 날 종료로 표시한다.
- 전체 Python **889 passed / 12 skipped / 2 warnings / 98.34s**. 임시 PostgreSQL **114 passed / 1 skipped / 2 warnings / 54.94s**. 마지막 UI·asset 검증은 별도 보고서에 기록한다. 이 집합은 중복이므로 합산하지 않는다. 기본 실행 skip12는 PostgreSQL cloud11개·upgrade1개, PostgreSQL 집합 skip1은 SQLite 프로세스 종료 시험이다.
- Chrome/macOS 로컬 합성 로그인·여행 + 실제 공개 식당 사실로 추천/진행/새로고침/상세를 확인했다. 390px와319px·200%글자에서 가로 넘침0, 상세 모달252px 안에 내용 유지, Escape 포커스 복귀, 실제 다크 테마 초록 토큰 확인. 실물 모바일/Safari는 미검증.
- 운영 schema12를 읽기 전용으로 암호화 백업(17.06초)하고 격리 복원(0.05초)했다. users1/trips2/stops2/docs6/bookings1/recommendations5/itineraries0, 무결성·FK 정상, 삭제 부활0, 복원 세션0. 저장소에 백업·비밀을 넣지 않았다.
- 실제 카탈로그 등록은 승인 팩3·지점9·출처10·공급자 호출0. 위치/경로·엄격 리뷰·유료 외부 발굴은OFF이며 100개 도시 입력 지원과 실제3도시 기초자료를 구분한다. 다른97도시를 합성 카드로 채우지 않는다.
- 실행 커밋·배포·운영 화면 결과는 배포 확인 후 아래에 추가한다.


### 식당·디자인 운영 반영 완료

- 실행 commit `3a78fdf12c65d845e3c5a3de0364ca9de05f9bae`, 수동 배포 `dep-db2c8e4s728c73bubcj0`. **2026-10-06 18:50:50 KST Live**, Render Deploy succeeded|Live(1분05초). 주요 구현 commit3037ada와 후속 UI 보완을 포함한다.
- HTTPS ready/live200, schema/storage/dispatcher/identity/restore 모두true, 비로그인 개인API401, 실제 JS/CSS/SW5자산 해시 일치. [검증 보고서](reports/green-recommendation-validation.md).
- 운영 실제 세션에서 기존 여행의 식당 추천 → 마드리드3곳(조건충족0/확인필요3) → 공식 상세 → 재배포·새로고침 결과 유지. 마지막 코드의 새 요청 시작 시 이전 취소 버튼/오류가 없는 것까지 확인했다. 실제 Google 로그인 신규 왕복은 이번에 수행하지 않았다.
- 현재 제목은 바르셀로나지만 여행 도시가Madrid인 입력 상태를 안내했다. 제목·숙소 주소만으로 사용자의 여행 도시를 자동 변경하지 않았다.
- 마지막 Node **62 passed**, 배포 전 백엔드889/임시PG114 결과 유지. 실제 외부 식당 검색은미구현, 기본자료3도시9곳, 나머지97도시는 자료없음이며 유료 지도/엄격언어/검색OFF 유지.
- 보존 비교에서 여행·구간·예약·교정은 모든 값 동일. 문서6건은 원본해시·메타데이터 동일하고 백업의격리파일경로로변환되는 opaque_path만 별개다. 원문변경으로보고하지않았다. [비교JSON](reports/green-live-preservation.json).


## 2026-10-06 — Airbnb 참고 디자인과 실제 식당 사진

- DESIGN.md와 emil-design-eng 기준으로 흰 배경/잉크 선택/코랄CTA, 사진 우선 카드·여백·다크모드·모바일을 정리했다. PWA아이콘/theme/cachev18까지 일치. [디자인 계약·Before/After/Why](DESIGN_SYSTEM.md).
- 실제9곳중7곳13장, 카드·상세 최대2장. 독립된 지점/라이선스검수, 출처·저작자·촬영일·잘림표시, 실패/없음 상태. 나머지2곳 사진은미확보. [사진 출처](reports/restaurant-photo-sources.md).
- 기존추천을읽을때 현재 허용사진만붙인다. 사진이 순위/snapshot/job/Chroma/오프라인에혼입되지않는다. DBschema12·개인여행자료·유료설정변경없음.
- 카드 중복문구와0/3내부표현을줄이고 검증된음식태그·자료확인일과 상세/저장/비교를앞에둔다. 필수미확인은유지. 홈/상단100도시한국어표시.
- 최종Python922passed/12skipped/2warnings,Node77passed,임시PG85passed/2warnings. 초기배포파일경로실패1개를수정후전체재검증. [명령·검증범위·복구](reports/airbnb-photo-validation.md).
- Chrome합성로그인→추천진행→사진전환/키보드→상세→저장/비교→새로고침확인.390/319px가로넘침0,200%글자·다크·모달Escape포커스확인. 실물모바일/Safari미검증.
- 기존 무료 환경에 최종 배포 완료. 최초 기동 실패와 후속 진단·수정 결과를 아래에 구분해 기록한다.

### 디자인·사진 최종 운영 반영 — 2026-10-07

- 실행 commit `801922dfcce6db0513f1023a689931a971cc2f25`, 수동 배포 `dep-db2h59p42hec73aol2og`. **00:26:13 KST Deploy succeeded|Live**. [실제 서비스](https://travel-inbox-rag.onrender.com). Render Free/Supabase Free 및 유료 공급자 OFF 유지.
- HTTPS live/ready200, 비로그인 개인API401, 실제 JS/CSS/SW5자산 해시 일치. schema12 유지, 모든 비공개 테이블 RLS 활성, anon/authenticated의 스키마 USAGE 없음. [검증 JSON](reports/airbnb-photo-live-verification.json).
- 사용자1·여행2·구간2·원문6·예약1·숙소3·삭제tombstone2·후보9 유지. 운영 검증용 추천1회로 run8→9만 증가했다. 여행 제목·도시·숙소를 임의 변경하지 않았다.
- 기존 실제 세션에서 추천 완료→마드리드3곳→사진1/2(naturalWidth960)→상세의 사진·원출처→재배포·새로고침 후 같은 결과 복원 확인. 브라우저 console error0. 신규 Google 로그인 왕복과 실물 모바일/Safari는 이번에 검증하지 않았다. 저장/비교·390/319px·큰 글자·다크 모드는 앞선 합성 브라우저 흐름으로 검증했다.
- 완료job과 결과 조회 시점이 엇갈리는 경우 같은run GET만 최대3회 자동 복구한다. 복구 중 진행 표시·중복 제출 차단, 자료 만료·여행/탭/요청 변경 중단. 최종 Node82통과.
- 진단을 통해 후속 배포의 기존 RLS 재적용 DDL 교착(40P01)을 확인했다. 모든 DDL 전에 WRITE→MIGRATION 잠금을 확보하고 이미 활성화된 RLS의 불필요한 ALTER를 생략했다. 새/누락 테이블 RLS 및 권한 회수는 유지한다. 실제 PostgreSQL 동시성·저장·이관·사진59시험 통과. 최초 두 기동 실패의 상세 원인은 당시 진단이 없어 동일 원인으로 단정하지 않는다.
- 사진은 3도시9곳 중7곳13장부터 적용하며 미확보2곳은 빈 상태로 안내한다. 나머지97도시의 실제 후보, 실시간 식당 검색, 엄격 리뷰 검증은 별도 범위다. 운영 추천 처리1회49.7초로 추가 속도 개선 여지가 있다.


## 2026-10-07 — 여정 디자인·사진3장·무료100도시 탐색

제품명을 여정으로 바꾸고 SUIT 글꼴·따뜻한 흰색/짙은 청색·간결한 화면을 적용했다. 실제 식당7곳19장(5곳3장) 음식·실내 우선 갤러리와100도시 공개지도 보조 검색을 구현했다. 실제 공식 검수 후보는3도시9곳이며 공개 후보를 검증된 맛집으로 승격하지 않는다. 엄격 리뷰/유료공급자OFF, 개인데이터·schema12 보존.

전체Python최종962passed/15skipped, Node최종99passed, 사진/추천 임시PG94passed. 최종 필터 순서 수정 후 공개검색SQLite/PG각20passed·기존추천45passed. Chrome 음식/실내/3장/출처·모바일폭·파리 실제 조회/상세/저장 검증. 실제전체100도시·사진전도시·실기기 미검증. 배포 결과는 [검증 보고서](reports/yeojeong-design-validation.md)를 따른다.


### 여정 최종 운영 반영 — 2026-10-07

- 실제 URL: https://travel-inbox-rag.onrender.com . 실행revision `9c97e86`, 수동배포 `dep-db2i79mgekts73cfodrg`,01:36:55 KST 시작·1분02초 후Live. 여정 이름/SUIT/청색·흰색/사진/100도시 보조 탐색 반영.
- 완료 신호가 결과 조회보다 빨리 도착할 때 복구 안내를 너무 일찍 보여주던 경합을 수정했다. 같은run GET 중복과 늦은 running응답을 차단하고 결과 대기중 busy를 유지한다. 최대3회 GET복구·기존요청 재사용. Node최종99passed, 백엔드전체962passed/15skipped.
- 최종 운영 검증·자료 범위·배포 중간 이력은 [검증 보고서](reports/yeojeong-design-validation.md), [검증 JSON](reports/yeojeong-live-verification.json). 무료 공개지도는 도심3km/식당·카페 보조 탐색이며 모든 도시 맛집·영업·사진 검수를 뜻하지 않는다. 엄격 리뷰/유료 API/실시간 잔여석은OFF.

- 최종HTTPS/live/ready/개인API차단·8자산해시·RLS/권한유지 확인. 기존여행/원문/예약/숙소건수유지,검증추천2회만증가. 실제새추천54.92초 후 결과조회중busy/중복방지→3카드자동표시·console error0. 처리는 무료환경에서여전히대기가있다.

## 2026-10-07 — 무료 메일 복구 · Pretendard · 명시적 필터

무료 배포에서 OpenAI 키/예산이 꺼졌지만 기존 메일 처리가 유료 추출·임베딩을 필수로 요구하던 문제를 수정했다. 무료 기본 분석과 SQL 근거 질문을 연결하고 실패 원문·사용자 교정·삭제/lease 방어를 유지했다. `MAIL_ANALYSIS_MODE` 및 인증된 분석 방식 API 추가. 모호한 정책/결제일·변경 요청·DST·미제공 주소는 확정하지 않는다.

Pretendard Variable v1.3.9를 자체 호스팅하고 굵기/자간/여백/버튼을 정돈했다. 필터를 방문·인원/취향·예산/이동·필수/리뷰 기준 탭으로 통합하고 취소 시 기존값, 적용 시 새 추천을 유지한다. 메일 화면은 업로드→파일별 결과→예약→원문 순서로 연결했다. 대표/구간 시각의 교정 불일치를 비교 표시하고 일정 확정을 차단한다.

최종 Python 1020 passed/15 skipped/2 warnings, Node119 passed, 임시 PostgreSQL58 passed. Chrome 실제 업로드/교정/재분석/날짜질문/새로고침/실패안내, 390px/960px/큰글자200%/다크/키보드 필터를 검증했다. 합성 로그인과 운영 로그인은 구분한다. DB schema 변경·비밀·플랜·유료 API 활성화 없음.

상세: [메일·디자인 검증 보고서](reports/mail-recovery-pretendard-validation.md). 운영 배포 결과는 이 보고서의 배포 표를 따른다.

최종 무료 운영 반영: `64ee184`, Render `dep-db2pu9om7kps73bsa17g`(2026-10-07 10:23:51 KST 시작·1m06s 후 Deploy succeeded). 기존 실패 메일6개→14예약 복구, 운영 재시작 후 총15예약 유지, 유료 usage0. 최종 UI119/Python1020/PostgreSQL58 통과, Python15skip. HTTPS·익명 개인API 차단·8개 자산해시·Pretendard/음식점 필터를 실제 확인했다. 상세 결과는 메일·디자인 검증 보고서와 연결된 운영 JSON에 기록한다.

## 2026-10-07 — 주요 탐색 구조 개편·OpenAI 복구

상단 여행 메뉴, 모바일 하단 메뉴, 직접 선택하는 종류/추천 관점/숙소 거리, 3/2/1열 카드, 바로 보이는 일정 담기, 연속 상세 정보, 별도 작업 dialog를 구현했다. 기존 무료 운영 지침 중 AI OFF는 사용자의 이번 명시적 승인으로 OpenAI 두 모델만 소액 한도 내 활성화하는 것으로 변경한다. 서버·DB Free와 다른 유료 공급자 OFF는 유지한다.

전체 Python 1023 pass / 15 skip, JS 119 pass. 실제 OpenAI 합성 메일·임베딩 2호출 성공, $0.002523 정산. 비용 상한과 브라우저 범위 및 운영 반영 상태는 [상세 검증](reports/journey-layout-openai-validation.md)에 기록한다. README 수정 없음. 이전 단계의 미검증 항목을 이번 변경으로 완료 처리하지 않는다.

후속 운영 시험에서 무료 분석 자료의 검색 세대 누락을 발견해 수정했다. 누락된 벡터만 기존 SQL 추출값으로 보충하며 다른 예약·교정은 유지하고 실패 작업의 완료된 유료 호출을 재사용한다. 신규 전환/실패/재시도 회귀를 포함해 Python1026 passed/15 skipped. 실제 운영 재시도 결과는 연결된 검증 보고서의 최종 운영 절을 따른다.

최종 운영 **d7a9fbf / dep-db2qt1d9fdbs738uqlb0**(2026-10-07 11:29:25 KST 시작,1m25s 후Live). OpenAI 메일8예약+근거8건·원문 확인 성공, 재시작 후 전체15예약/2여행/6메일 및 검색 활성 세대 유지. 운영 합성 분석·복구 총 $0.006753, GPT재시도 추가0회. Python1026 pass/15 skip, Node122 pass. 자세한 시간·비용·화면·제약은 위 검증 보고서 및 `reports/journey-layout-live-verification.json` 참조. README 수정 없음.

## 2026-10-07 — 고잉 브랜드·여백·공개지도 연결 개선

사용자 지정 이름 고잉과 G 아이콘으로 교체. 모바일 중복 하단 패딩·큰 홈 카드·숙소 없는 거리 입력·중복 검색 오류를 정리했다. DNS 연결 복구, HTTP 거절/연결 오류 분리, 공급자 공통 Retry-After·정확한 재시도 시각을 구현했다. 로컬 회귀와 운영 검증은 [실행 보고서](reports/going-design-search-validation.md)에 구분해 기록한다. README 미변경, OpenAI 예산과 개인정보 계약 유지.
