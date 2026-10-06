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
