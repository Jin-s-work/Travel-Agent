# 메일 분석 복구 · Pretendard · 필터 사용성 검증

작성일: 2026-10-07. 무료 메일 분석과 화면 개선을 구현했다. 아래 기록은 실행한 검사와 실제 배포 확인을 구분한다.

## 해결할 문제와 구현 범위

기존 무료 배포에서 메일 업로드가 유료 AI·임베딩 설정과 함께 묶여 분석 흐름을 진행하기 어려웠다. 저장·권한·job·예약 교정 모델을 유지하면서 외부 API를 호출하지 않는 보수적인 기본 분석 경로를 추가한다. 명시된 날짜·현지 시각·장소·예약 필드를 구조화하고, 인식하지 못하거나 모호한 값은 자동 확정하지 않는다. AI 상세 분석은 기존 키·단가·예산·중단 제어를 계속 통과해야 한다.

- `MAIL_ANALYSIS_MODE=auto`: AI 설정과 안전 조건이 유효하면 상세 분석, 아니면 기본 분석.
- `local`: 외부 AI·임베딩 호출 없이 기본 규칙으로 분석.
- `ai`: AI 우선. 사용 불가 상태에서는 기본 분석으로 전환하며 과금 제어를 우회하지 않는다.
- `ZERO_SPEND=1`을 해제하거나 유료 공급자를 자동 활성화하지 않는다.
- 기본 분석 결과도 사용자 확인·교정 대상이다. 복잡한 원문 형식이나 불명확한 변경 요청을 일반 자연어 이해 성공으로 주장하지 않는다.
- 원문을 보존하고 수동 예약 입력·날짜별 SQL 조회·재분석·사용자 교정을 연결한다. 파싱 실패가 기존 활성 예약을 삭제해서는 안 된다.

운영 저장소의 읽기 전용 조사에서 문서 6개가 실패 상태였고 추출 세대 8개가 `PROCESSING_FAILED`였다. 당시 OpenAI 키는 없고 zero-spend가 활성화되어 있었다. 이전 경로는 이 상태에서도 유료 추출·임베딩을 요구했다. 원문은 남아 있었으며 무료 기본 분석 dry-run에서 6개 모두, 합계 14개 예약을 읽었다. 이 dry-run은 데이터를 수정하지 않았고 원문·예약번호·계정 식별자는 로그에 출력하지 않았다.

별도 검토에서 결제일·취소 기한을 방문일/시각으로 잘못 읽는 반례, 호텔 안내의 `flight number` 때문에 종류가 바뀌는 반례, 변경·취소 메일의 시각 오인, 일본어 미제공 주소 오인을 찾아 수정했다. 예약 취소·변경을 자동 실행하지 않는다.

대표 시각과 구간별 시각의 독립 교정으로 답변이 엇갈리는 기존 모델 문제도 발견했다. `time_conflicts`는 두 표현을 비교하고 질문에서는 사용자 교정값을 우선 안내하며, 일정 검증은 `BOOKING_TIME_CONFLICT`로 확정을 차단한다. 출국/귀국 시각을 자동 동기화하거나 기존 예약을 움직이지 않는다. 사용자가 두 값을 맞추면 충돌이 해제된다.

## 화면 변경

- 공식 Pretendard Variable v1.3.9로 교체. 본문 16px·필드 이름 14px·캡션 13px, 굵기 400/500/600으로 정리한다. 페트롤 청색·따뜻한 흰색·다크/큰 글자/동작 줄이기 지원을 유지한다.
- 필터 진입점을 한 곳으로 통합하고 방문·인원 / 취향·예산 / 이동·필수 / 리뷰 기준의 이름 있는 탭을 제공한다. 선택 의미를 설명하는 라디오 행을 사용한다.
- 편집은 임시 값으로 유지한다. 취소하면 기존 적용값을 보존하고 ‘이 조건으로 찾아보기’에서만 적용한다. 숨겨진 탭의 입력 오류는 해당 탭을 열어 표시한다.
- 메일 화면은 추가 → 분석 결과 → 예약 → 원문 순서다. 분석 방식과 테스트 메일 링크를 먼저 보여 준다. 예약 날짜·종류·확인 필요 필터는 펼침 없이 바로 보인다.

[디자인 시스템과 Before/After/Why](../DESIGN_SYSTEM.md)에 근거와 상세 규칙을 기록했다.

### 글꼴 출처 및 비용

[공식 Pretendard v1.3.9](https://github.com/orioncactus/pretendard/releases/tag/v1.3.9), [공식 OFL 라이선스](https://github.com/orioncactus/pretendard/blob/v1.3.9/LICENSE).

- 파일: `web/fonts/PretendardVariable-v1.3.9.woff2`, 2,057,688 bytes.
- SHA-256: `9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4`.
- 원본 폰트·라이선스 보존. 첫 다운로드는 기존 SUIT보다 약 1.37 MiB 크며, 폴백 표시 후 공개 자산 캐시를 사용한다. 유료 폰트나 외부 CDN은 사용하지 않는다.
- 정적 WOFF2 헤더·크기·해시 검증 완료. 실제 적용·로드 지연·레이아웃은 브라우저 결과에서 별도 기록한다.

## 검증 기록

| 범위 | 명령/확인 항목 | 실제 결과 |
| --- | --- | --- |
| 기본 메일 추출 | 다운로드팩 12개·기존 메일 7개, 다중 예약, 왕복, HTML, DST, 정책/결제일, 미확정 변경 | 신규 메일 시험 53개 통과 |
| 실패와 데이터 보존 | 재분석 실패, 사용자 교정, 중복, 소유권, 삭제/완료 경합 | 회귀 통과. 무료 경로에서 provider/build 호출 시 시험이 실패하도록 검증 |
| 무료 경로 | 외부 AI/임베딩 호출 0, 날짜별 예약 질문, 원문 근거 | 실제 로컬 브라우저 DB usage 0행. 하루 8개 + 수동 예약은 API 시험에서 9개 모두 반환 |
| 필터 | 취소·임시값·적용, 잘못된 입력 탭 이동, 키보드, 늦은 응답 | 신규 필터 9개 + 기존 흐름 + Chrome 실제 취소/적용/End 키 통과 |
| 전체 회귀 | 위 실행 명령 참조 | Python **1020 passed / 15 skipped / 2 warnings, 143.80s**. Node **119 passed, 0 failed** |
| PostgreSQL | 시간 충돌·무료 메일·외부 호출 없는 재색인 | 임시 PostgreSQL 58 passed, 15.79s. 시험 전용 컨테이너 정리 |
| 브라우저 | Chrome 실제 메일 업로드→예약 확인·교정→재분석→질문→새로고침, 필터 편집·취소 | 실제 무료 parser로 3메일/10예약. 날짜 질문 8방문+숙박=9근거. 미지원 메일 실패/원문/직접입력 안내, 기존10예약 보존 |
| 반응형/접근성 | Chrome 390px·960px, 200% 글자·다크, 키보드/초점, font-family/font ready | 390px scrollWidth=390, 200% 모달 clientWidth=scrollWidth=318. Pretendard 로드 확인. 실제 iOS/Android/Safari 미검증 |
| 배포 | 실행 revision·Render deploy ID·HTTPS·readiness·자산 대조 | `64ee184` / `dep-db2pu9om7kps73bsa17g`, 10:23:51 KST 시작, 1m06s 후 Deploy succeeded. HTTPS/live/ready 200, 개인 API 401, 자산 8개 해시 일치 |

### 실행 명령과 경계

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/mail_design_browser_fixture.py
# 임시 loopback PG에만 연결하며 운영 DSN은 사용하지 않는다.
PYTHON_DOTENV_DISABLED=1 PUBLIC_DISCOVERY_ENABLED=0 TRAVEL_TEST_POSTGRES_DSN='<disposable localhost DSN>' .venv/bin/python -m pytest -p tests.postgres_plugin -q tests/test_booking_time_conflicts.py tests/test_local_mail.py tests/test_operations.py::test_local_reindex_without_adapter_preserves_sql_and_never_invokes_provider
```

첫 전체 회귀는 1014 passed / 1 failed / 15 skipped였다. 무료 재색인 분기가 주입된 오프라인 embedder까지 건너뛰어 백업 복원 시험이 실패했다. 명시적 adapter 주입은 기존 비용·fencing 경로를 유지하도록 수정했으며 기존 복원 시험을 약화하지 않았다. 관련 61개를 통과한 뒤 최종 전체 회귀를 실행했다. 기존 Starlette/Authlib의 httpx deprecation 경고는 유지된다.

로컬 브라우저 fixture는 합성 OIDC의 실제 state/nonce/PKCE/JWT를 검증하고 **실제 기본 메일 parser**를 사용한다. production DB/키/메일은 사용하지 않는다. 로컬 3파일 작업은 0.145초, 재분석 0.055/0.064초, 미지원 메일 0.026초였다. 이는 브라우저 파일 선택·네트워크·Render cold start를 제외한 로컬 job 처리 시간이며 운영 성능 보장이 아니다.

## 직접 시험용 메일

[사용 방법](../../../examples/mail-test-pack/README.md), [다운로드용 ZIP](../../../web/examples/mail-test-pack.zip).

ZIP은 기존 파일명·경로 14개를 보존하여 다시 묶었다. 합성 메일·참고 의미 JSON과 최신 사용 안내만 포함한다. 개인정보·비밀키·실제 예약 데이터는 포함하지 않는다. `expected-results.json`은 목표 의미 설명이며 모든 필드의 무료 자동 추출을 보장하지 않는다. ZIP 자체를 메일로 업로드하지 않고 `.eml`을 선택한다.

ZIP 무결성·내부 경로/README 일치 검사는 완료했다. 실제 사용자 업로드는 위 브라우저 검증 결과에 기록한다.

## 실제 무료 서비스 복구 확인

- 최초 무료 분석 배포: 코드 `b3d2bb2`, Render `dep-db2ppb0m7kps73brodl0`, 2026-10-07 10:14:30 KST Live 로그 확인. 기존 Render Free/Supabase hii 재사용.
- 기존 사용자 계정에 남아 있던 실패 메일 6개의 ‘다시 분석’을 실제 Chrome에서 실행했다. 6개 모두 기본 분석 완료/내용 확인 필요로 바뀌고 선택 여행에서 14개 예약이 보였다. 새로고침 후 동일했다. 기존 다른 여행의 수동 예약 1개를 합쳐 DB 예약은 총 15개다. 새 합성 메일을 운영에 추가한 시험이 아니라 이미 업로드된 테스트팩의 복구다.
- 변경 요청 메일에는 확정 아님, DST 메일에는 시각 모호함 안내가 표시됐다. 사용자의 여행 기간·도시를 자동 변경하지 않았으며 기간 밖 예약은 그대로 표시한다.
- 운영 DB 읽기 전용 확인: 문서6 `needs_review`, 활성 `local-mail-v1` 세대6, schema12, usage 예약/정산0행. 모든 개인 테이블 RLS true, browser `anon`/`authenticated`의 travel schema USAGE false. 유료 AI·임베딩 호출과 플랜 변경 없음.
- HTTPS live/ready 200, 익명 trips/cities/mail-capabilities API 401. 운영 기존 세션으로 확인했으며 이번 수정에서 Google OAuth 신규 로그인 왕복은 반복하지 않았다.
- 운영 복구 중 이전 오류 문구가 성공 뒤 남는 화면 문제를 찾아 재시도별 결과 초기화를 보완했다. 같은 job의 부분 결과는 보존하고 새 시도/최종 결과에서만 오래된 안내·건수를 지운다. 영어 restaurant로 저장된 식당도 음식점 필터/상세/편집에 표시하며 저장값을 임의로 교정하지 않는다. 후속 최종 배포와 자산 해시 결과는 위 배포 표를 따른다.

최종 UI 보완 배포 뒤에도 운영 문서6/활성 무료 세대6/예약15가 유지됐고 최근 메일 job6개 모두 `succeeded`, attempt1, error null이었다. 실제 운영의 파일별 실행 시간은 약22.6~28.4초였으며 순차 작업6개는 첫 시작부터 마지막 완료까지 약159초였다. 이는 해당 테스트팩 실행의 관측값이며 메일 복잡도·무료 인스턴스 기동 대기에 따라 달라진다.

Chrome/macOS에서 최종 자산 `foundation.js?v=515b0d15ba0e`, Pretendard 실제 로드/계산 font-family, 음식점 필터 **2/14건**과 한글 종류, 전체14건 복귀를 확인했다. 브라우저 console error 관측0. 검증 JSON은 [운영 확인 결과](mail-recovery-live-verification.json)에 저장한다. 새로운 파일 업로드/교정·재분석/질문 전체 흐름은 앞서 기록한 로컬 합성 시험이며, 운영에서는 기존 6개 메일의 복구와 보존을 검증했다.

## 운영 경계와 복구

실제 메일의 언어 이해 정확도는 합성팩 통과율로 대신 보고하지 않는다. 미지원 형식은 원문 확인·교정·수동 입력으로 안내한다. 기본 분석은 최신 잔여석·예약 변경 확정·외부 취소를 수행하지 않는다. 기존 인증·사용자/여행 소유권·외부 과금 중단·검색과 SQL 분리·삭제 방지는 유지되어야 한다.

DB schema 변경·migration 없음. 비밀키·가격·유료 한도·플랜을 변경하지 않았다. rollback은 이전 Render image revision으로 가능하고, 새로 생성한 SQL 예약과 원문을 DB 복원으로 지울 필요가 없다. 이전 버전으로 돌리면 이번 무료 분석 기능은 다시 사용할 수 없으므로 메일 처리 회귀 여부를 별도로 확인한다. 실물 iOS/Android/Safari와 유료 AI 라이브 정확도는 미검증이다.
