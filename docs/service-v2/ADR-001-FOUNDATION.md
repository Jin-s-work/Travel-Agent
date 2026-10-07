# ADR 001 — 초대 인증과 사용자별 예약 저장

- 결정일: 2026-10-01
- 상태: 채택·01단계 구현
- 기준: `aefee57` 이후 현재 미커밋 작업

## 인증

Authlib 1.8의 Starlette OIDC client를 사용한다. 기본 discovery URL은 Google이며 issuer·서명·audience·nonce·state·PKCE 검증을 라이브러리에 맡긴다. 구현 참고는 [Authlib Starlette 공식 문서](https://docs.authlib.org/en/v1.7.0/oauth2/client/web/starlette.html)다. 외부 로그인 성공 뒤 `email_verified=true`인 이메일에 대한 초대를 별도 검사한다. provider+subject가 계정 식별자다.

서비스 세션은 난수 토큰이며 해시만 SQLite에 저장한다. 사용자 status/session_epoch, session expiry를 매 요청 검사한다. 실제 서비스 쿠키와 OAuth 임시 상태 쿠키를 분리한다. 초대는 POST 본문으로 받고 임시 쿠키에도 초대의 해시만 둔다. 신규 가입과 초대 소비는 같은 SQL 쓰기 트랜잭션에서 처리한다. 기존 활성 계정의 재로그인에는 새 초대를 요구하지 않는다.

운영은 HTTPS와 안전한 `__Host-` 쿠키를 강제한다. HTTP는 명시적인 development와 loopback 주소에만 허용한다. 운영 인증이 없을 때 개인 API는 401이다. 합성 로그인은 `tests/browser_fixture.py`의 별도 테스트 프로세스에서만 제공하며 운영 환경변수로 인증을 우회할 수 없다.

브라우저 검사에서 전역 `Referrer-Policy: no-referrer`가 네이티브 POST 로그인 폼의 Origin을 `null`로 만들어 403을 유발했다. `strict-origin-when-cross-origin`으로 변경해 Origin 검사를 유지하면서 정상 로그인되도록 했다. 서버 예제의 access log를 끄며 운영 프록시도 OAuth callback query와 요청 본문을 로그에 남기지 않아야 한다.

## 저장과 권한

이번 수직 기능은 `src/foundation/`에 모았다. 계획서의 여러 폴더를 동시에 만들기보다 책임별 파일(auth, db, repository, documents, search, routes)로 분리해 현재 작은 단일 서비스에서 탐색하기 쉽게 했다. FastAPI·기존 PWA·SQLite·Chroma는 유지한다.

SQLite `user_version=1`의 원자적 초기 마이그레이션이 11개 핵심 테이블과 `processing_receipts`를 만든다. 연결마다 foreign keys, WAL, 5초 busy timeout을 적용한다. 새 버전 DB에 구 코드가 쓰지 않도록 막는다. 원문 파일명·예약번호는 기본키가 아니며 모든 저장 ID는 서버가 생성한다.

소유권은 활성 사용자→여행→예약/문서 순서의 SQL 범위에서 확인한다. HTTP는 legacy global store를 호출하지 않는다. 구 개인 API는 미인증 401, 인증 후 410으로 폐기하며 전역 삭제를 실행하지 않는다. 기존 CLI의 공용 인덱스와 helper는 과거 데이터 분석용으로만 남겼다.

## 추출과 검색

문서 하나는 여러 예약, 예약 하나는 여러 이벤트를 가진다. 추출 JSON·교정 이력·현재 유효 JSON을 구분한다. 이벤트에는 출발/도착 local time과 IANA zone 및 계산 가능한 UTC instant를 각각 보존한다. 날짜만 있는 입력과 시간대 미확인은 instant를 생성하지 않는다. 애매하거나 존재하지 않는 DST 시각은 사용자 확인을 요구한다.

추출 DTO 검증→모든 청크 임베딩→새 generation metadata로 Chroma 저장→SQL 활성화 순서다. 임베딩 전에 활성 예약을 삭제하지 않는다. 활성 generation만 검색한 뒤 검색 결과의 문서 ID를 현재 SQL 예약으로 다시 읽는다. 오래된 raw chunk를 사실로 답변하지 않는다. 직접 입력·날짜 전체 조회는 SQL이 담당한다. 긴 검색 텍스트는 기존 chunk 함수를 재사용한다.

일자 질문은 의미 검색 top-k와 무관하다. 날짜가 지정된 정책 질문도 SQL 범위를 먼저 적용한다. 대화의 ‘그 예약’은 현재 여행에 실제 존재하는 예약 ID로 해석하고 모호하면 되묻는다. history는 사실/권한을 대체하지 않는다. 질문 결과에 출처를 직접 포함하고 답변 직전 세션과 예약 버전을 다시 확인한다.

배경 작업은 활성화 트랜잭션 안에서도 요청 세션의 소유자·만료·epoch를 검사한다. 로그아웃 후 늦게 도착한 처리 결과가 활성화되지 않는다. 운영자 CLI 이관은 별도의 명시적 관리 작업으로 session 없이 활성 사용자 소유권을 검사한다.

## 01단계와 02단계의 경계

202 응답과 결과 조회용 SQL 영수증은 구현했으나 실행은 FastAPI BackgroundTasks와 단일 프로세스 lock이다. 강제 종료 후 자동 재개, lease, checkpoint, durable SSE replay, 전체 trip index generation 원자적 전환, 물리 삭제 재시도는 구현하지 않았다. 비활성 vector generation은 접근에서 제외되지만 자동 정리·장애 복구는 다음 단계에 남는다.

추천·리뷰 크롤링·장소 공급자·일정 최적화·대리 예약·공개 가입은 이번 기능에 포함하지 않았다. 기존 예약 기능의 웹 검색도 개인 정보를 공급자 검색어로 보내지 않도록 현재 웹 여행 질문에서는 사용하지 않는다.
