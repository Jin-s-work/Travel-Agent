# 고잉 v4 진행 기록

## 2026-10-08 — 콘텐츠·여행 조건 추천 모델

최신 기록이다. [모델 구조·수식·실제 평가 절차](RECOMMENDATION_MODEL.md), [구현 검증](reports/hybrid-v4-validation.md), [합성 개발 비교](reports/hybrid-v4-benchmark.md)를 함께 읽는다.

- 새 요청의 기본 모델을 `hybrid_v4`로 연결했다. TF-IDF 취향 유사도·같은 플랫폼의 표본 수 보정 평점·숙소 직선거리를 사용하며 v1/v2/v3 replay는 보존한다.
- 모델·프로필·특징·비중·미확인·후보 집단 해시를 저장한다. 기존 장소 총점과 순위 utility를 구분하고, 카드에서 계산 근거를 확인할 수 있다.
- 지점·언어·사용권·휴무·인원 등 필수 조건은 재사용한다. 출처 철회 시 후보 집단에 의존하는 파생 점수도 차단한다.
- 최종 **Python 1,156 passed / 17 skipped**, **JavaScript 177 passed**. 원본 디렉터리의 파일 읽기·의존성 문제로 해시 대조한 별도 native 사본에서 전체 검증했다. 자세한 실패·수정 이력은 보고서에 있다.
- 합성 개발 12질의 NDCG@3 0.4155→0.9954, Precision@3 0.3889→0.7778. **독립 holdout·실사용 정확도·만족도는 미측정**이다. 학습한 모델이라고 표현하지 않는다.
- 리뷰 라이브 검증·엄격 언어 OFF·브라우저 미검증은 이전과 같다. 이번 변경의 운영 배포와 GitHub push는 하지 않았다. 기존 사용자 Keynote 변경과 발표 PDF를 보존했다.

## 2026-10-07 — 2단계 구현·발표·README 갱신

당시 완료 기록이다. [2단계 상세 검증](reports/stage2-validation.md), [리뷰 계약/이관](reports/stage2-review-contracts.md), [추천 모델](reports/stage2-ranking.md), [UI](reports/stage2-ui-validation.md), [실제 12곳](reports/stage2-pilot-catalog.md)을 함께 읽는다.

- schema14 지점 연결·공급자 계약·실행 의존성·관리자 검증과 도시별 능력 상태 구현.
- 현지어/유명한 곳 일반 v3 모델, 독립 참고 구획, 선택 입력 생략, 사용자 수치 완화, 관련 근거만 철회 구현. 기존 v1/v2 replay 유지.
- 두 탭·분류·비교·근거·맥락 복원, 관리자 비용 preview→접수, 선호/분석 동의 분리.
- 실제 도쿄6·바르셀로나6 후보 출처 팩과 격리 정상 API import 검증. 운영 DB 미반영.
- PPTX/Keynote/대본은 새 `docs/presentation/v4/`로 작성. 기존 사용자 Keynote 변경 보존. README 최신 구조로 갱신.

| 범위 | 상태 |
| --- | --- |
| 최종 Python 전체 | 1,134 passed / 17 skipped / 0 failed / 164.31s |
| 최종 JavaScript 전체 | 175 passed / 0 failed |
| PostgreSQL 이관·canonical·파일럿 | 21 passed / 1 SQLite-only skip |
| PostgreSQL+mock Storage 내구성 | 13 passed |
| 실제 브라우저 최신 UI | blocked_by_saved_browser_permission / not_verified |
| 실제 Apify A/B 수집·도시별 실제 언어 평가 | not_run |
| 엄격 언어 / 학습 개인화 | OFF / OFF |
| 운영 배포·운영 새 팩 등록 | not_run |
| GitHub | `98d2d31` 구현·README·발표 자료를 main 및 codex/private-beta-launch에 push 완료 |

부분 시험을 합산하지 않으며 실제 품질·만족도는 미측정이다. 아래 1단계 기록은 당시 이력으로 보존하고 이 표로 최신 상태를 판단한다.

## 2026-10-07 — 1단계 구현 적용

기준 `e8ea36faadac4912a523ae7bea5f02368d7f1ba1`, branch `codex/private-beta-launch`의 미커밋 작업이다. [상세 검증](reports/stage1-validation.md), [후보/SQL 결과](reports/stage1-discovery-validation.md), [schema 13 이관](reports/stage1-migration.md)을 함께 본다. 아래 최초 감사 기록은 이력으로 보존한다.

### 적용한 코드와 화면

- 숙소 근처 13번째 공개 후보·100개 식당 뒤 카페 누락 수정. 카테고리/사용권/만료를 먼저 확인하고 bounded pool에서 거리·조건을 평가한 뒤 표시 개수를 제한한다.
- 장소·사실·출처·사진·리뷰 batch 읽기, 실제 참조 manifest, GET의 전역 purge/쓰기 제거. 무관한 변경에는 저장 결과를 보존하고 관련 철회 카드만 가린다.
- 공개 후보를 검수 승인으로 바꾸지 않고 일정 validator에 연결. strict 미배치와 명시적 잠정 배치를 구분한다. 철회된 장소·삭제 예약·연결 휴식의 좌표/이름도 안전 DTO에서 숨긴다.
- schema 13: 세션·여행별 workspace 초안, 비활성 첫 일정 미리보기, maintenance 상태, storage 삭제 영수증. version/TTL/소유권/늦은 저장 차단과 schema12 import 호환성을 포함한다.
- 짧은 날짜·인원·출발점 수정, 한국어 필수 조건, 예약 시각 변경안, 직접 상세 이동, 탐색 맥락 복원, 첫 일정 preview→apply, 기존 undo 연결.
- follower 승격을 관측하는 maintenance, side effect guard, 250개 object/import의 공정한 정리, 실패 재시도와 늦은 업로드 등록 차단.

### 상태 구분

| 범위 | 상태 |
| --- | --- |
| 1단계 코드·마이그레이션·화면 | implemented; 아래 자동 시험 및 보고서 참고 |
| 최종 Python 전체 | 새 native Python 3.13.5 환경: 1,089 passed / 17 skipped / 0 failed / 2 warnings / 202.30s, exit 0 |
| 신규 후보/SQL/철회 계약 | SQLite 16 / disposable PostgreSQL 16 passed |
| 신규 maintenance·삭제·backup·budget 계약 | SQLite 15 / disposable PostgreSQL 15 passed |
| 최종 이관·초안·일정 계약 | disposable PostgreSQL 14 passed |
| schema12/13 import와 클라우드 저장소 회귀 | disposable PostgreSQL + mocked Storage 13 passed |
| 최종 JavaScript 전체 | 155 passed / 실패 0, 자산 hash 확인 |
| 로그인 후 실제 브라우저 연결 흐름 | blocked_by_saved_browser_permission; 사용자 진행 승인은 받음 |
| 320/390/768/1440px·200%·실제 모바일 | not_verified |
| 숙소 원점의 공급자 수집 | limited: 고정 도심 검색; 외부 위치 전송 승인 범위 없음 |
| 실제 공급자·운영 부하·실제 Google 로그인 | not_run |
| 2단계 실제 리뷰/새 모델/품질 활성화 | not_started / 기존 OFF 상태 유지 |
| GitHub push·운영 배포·새 지출 | not_run |

후보 6/12/50/100개에서 신규 실행 SQL 209회, GET 32회·checkout 4회·쓰기 0회다. 100개 GET의 이전 1,639회와 같은 fixture로 비교했다. 시간 계측은 단일 표본이며 운영 p95/5명 부하 목표 충족을 뜻하지 않는다.

합성 브라우저 fixture는 준비했으나 `http://127.0.0.1:8767` 접근에 저장된 차단 설정이 남아 인증 후 화면을 검증하지 못했다. 권한을 우회하지 않았고 screenshot 증거도 만들지 않았다. Photon으로 숙소 유래 좌표를 전송하는 변경은 자동 승인 검토가 거절해 고정 도심 범위와 부족 사유 표시를 유지했다.

사용자 Keynote 수정·README 본문·실제 `.env`·운영 자료는 보존했다. 자동 시험의 최초 실패/환경 문제 및 재실행은 검증 보고서에 별도 기록한다. 코드 구현을 운영 출시 완료로 보고하지 않는다.

## 2026-10-07 — 코드 감사와 2단계 고도화 기획

### 완료

- 현재 저장소·진행 기록·실행/시험 구조 확인.
- 백엔드·탐색/일정·UX의 병렬 감사와 코드 근거 정리.
- 임시 데이터에서 후보 누락·카테고리 검색 생략·공개 후보 일정404·리뷰 ID 단절·선택 입력 순위0·결과 무효화 재현.
- SQL/checkout 계측, leader 승격 유지보수 누락, 객체 정리 진행성의 제한된 합성 재현.
- 전체 Python/JS 실행과 Python 실패 파일 단독·선행순서 재실행.
- 현지어 리뷰와 유명한 곳을 구분하는 PRD, 추천 모델 계약, 수용 시험, 상세 구현 프롬프트2개.
- 현재 공식 공급자/호스팅 문서 확인. 실제 단가는 실행 전에 재확인하도록 명시.

### 최초 기획 당시 미구현 상태 — 위 최신 기록으로 대체

| 범위 | 상태 |
| --- | --- |
| 1단계 앱 코드/마이그레이션/화면 수정 | not_started |
| 2단계 지점 매핑/추천 모델/소비자·관리자 화면 수정 | not_started |
| 실제 Apify 리뷰 수집 | not_run |
| 실제 음식점 리뷰의 도시별 언어 품질 검증 | not_run |
| 데이터 이용 범위와 계정 잔액 검증 | not_run |
| 엄격 언어 추천 활성화 | off / 기존 상태 유지 |
| 새로운 운영 배포·설정 변경 | not_run |
| 로그인 후 브라우저 전 흐름 | blocked_by_browser_access_denial |

### 실제 시험 결과

- Python 전체:1032 passed,7 failed,15 skipped,2 warnings,502.74초.
- JS:124 passed,0 failed,13.07초.
- 실패파일단독:39 passed,13.00초.
- 전체import·선행순서재현:220 passed,11 skipped,823 deselected,30.64초.
- 추가탐색감사:6개 결함존재 재현 통과. 수정 완료를 뜻하지 않는다.
- 최초7건 job timeout 원인 미확정. 재실행 통과를 전체 통과로 합산하지 않는다.

앱 코드는 변경하지 않았으며 기존 Keynote 수정은 보존했다. 신규 문서만 별도 디렉터리에 추가했다. GitHub push·배포는 이번 기획 범위에서 실행하지 않았다.

### 다음 구현 시 갱신할 것

기능별 code / synthetic_test / browser_test / live_provider / policy / quality / city_enabled 상태를 따로 기록한다. 위 미실행 항목을 이전 단계의 완료 기록만으로 통과시켜서는 안 된다. 각 단계 종료 시 사용한 commit·명령·실제 결과·잔여 제약을 추가한다.


### 2026-10-08 — README와 기능 중심 발표 개편

- README를 소개·실제 화면·추천 모델·실행 방법 중심으로 다시 작성했다. `hybrid_v4` 특징과 합성 개발 평가 12개 결과를 원본 JSON과 대조하고 실제 정확도·독립 평가 미측정을 명시했다.
- [v5 발표](../presentation/v5/README.md)는 10장·목표 8분 30초다. 개인적인 제작 계기, 주요 기능, 추천 모델과 비교, 앞으로의 개발 방향을 다룬다. [대본](../presentation/v5/SCRIPT.md)에 5분 축약과 예상 질문을 함께 넣었다.
- 기존 사용자 Keynote·PDF는 보존했다. 새 PPTX 10장 렌더링, Keynote 가져오기·메모·저장을 확인했다. 이번 문서 작업에서 운영 배포·실제 추천 품질·브라우저 기능 검증을 새로 수행한 것은 아니다.
