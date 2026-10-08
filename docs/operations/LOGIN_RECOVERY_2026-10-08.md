# 다른 기기 로그인 복구 — 2026-10-08

사용자 보고: 기존 Google 계정으로 다른 컴퓨터에서 로그인되지 않음. 다른 컴퓨터의 오류 화면/네트워크/쿠키 설정은 직접 확인하지 못했다. 기기별 실패의 단일 원인을 확정한 보고서는 아니다.

## 확인한 사실과 수정

- 운영 관리자 계정의 Google 선택→콜백→저장된 여행 조회는 현재 Chrome에서 정상 작동했다. 서버 readiness도200이었다.
- 기존 콜백은 모든 예외를 `login_denied`로 바꾸고, 프런트는 그 URL 파라미터를 표시하지 않았다. 이제 쿠키 누락·state 만료·가입 계정 불일치·권한 회수·취소·제공자 설정/통신 실패를 고정 문구와 요청 번호로 안내한다.
- 로그에는 단계·고정 코드·요청 번호·제한된 예외 종류/코드 위치만 남긴다. Google code/token/claims·초대 코드·예외 메시지는 기록하지 않는다.
- 기존 세션 확인 GET에 기한이 없었고, 새 기기의 IndexedDB 초기화를 무기한 기다릴 수 있었다. 세션65초/다른 읽기30초, 기기 저장소 open/transaction 각5초 기한을 두었다. 늦게 열린 저장소는 닫고 지연 트랜잭션은 abort한다. 저장 요청은 자동 재전송하지 않는다.
- 인증 후 여행 조회 실패가 `clearPrivate()`와 로그인 버튼 비활성화로 이어졌다. 이제 검증한 세션을 유지하며 ‘여행 다시 불러오기’를 제공한다. 인증 설정 미완료와 단순 통신 실패를 구분한다.
- 다른 창의 로그아웃이 기기 저장소 초기화 중 도착하면 이전 세션의 응답을 다시 활성화하지 않는다. 계정 변경 시 이전 자료를 정리한다.
- 처음/다른 기기의 Google 로그인 안내, 서버 기동 대기 안내, asset hash와 service worker 버전을 갱신했다.

초대 허용 범위, OIDC state/nonce/PKCE 검증, Secure/HttpOnly/SameSite 쿠키, CSRF/Origin, 사용자별 자료 격리는 유지한다. 신규 공개 가입이나 OAuth 권한 확대는 하지 않았다. DB 마이그레이션·유료 호출 없음.

## 로컬 검증

환경: Python3.13 기존 검증 venv, Node, 합성 데이터/임시 DB, `PYTHON_DOTENV_DISABLED=1`.

```sh
python -m pytest tests/test_foundation_auth.py tests/test_foundation_api.py tests/test_request_failures.py -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
git diff --check
```

- Python65건 통과. Starlette/Authlib의 httpx 이전 관련 기존 deprecation warning2건.
- JS209건 통과. auth code 처리, 세션/저장소 대기, 계정 전환, 늦은 응답, 기존 추천·일정·오프라인 회귀 포함.
- 독립 TestClient2개에 기존 계정의 새 세션을 발급: 초대 코드 재입력 없음, 서로 다른 세션/CSRF, 한 기기 로그아웃 후 다른 기기 세션 유지. Google의 검증된 claims 경계만 fake이며 실제 Google/다른 물리 기기 시험과 구분한다.
- asset hash 일치 및 diff whitespace 확인.

## 운영 확인

배포 후 실제 커밋·배포 ID·HTTPS/오류 안내/Google 로그인 결과를 아래에 추가한다. 사용자 쪽 다른 컴퓨터의 확인은 아직 필요하다.

## 재현·복구

1. 다른 컴퓨터에서 운영 URL을 열고 이전과 동일한 Google 계정을 선택한다. 기존 가입자는 초대 코드를 다시 넣지 않는다.
2. 실패 시 화면의 고정 안내와 오류 번호로 Render의 `login_failed` 로그를 조회한다. 비밀값이나 Google 로그인 code가 포함된 URL을 공유하지 않는다.
3. 쿠키 누락이면 해당 브라우저의 고잉 사이트 쿠키 허용 여부, state 만료면 동일 창에서 새 로그인, 제공자 설정 오류면 기존 OAuth callback/등록값을 확인한다. 쿠키 검증이나 초대 정책을 우회하지 않는다.
4. 로그인 성공 후 조회 실패는 여행 재조회만 한다. 네트워크 장애를 이유로 기존 예약이나 계정을 재생성하지 않는다.
5. 필요 시 이전 앱 이미지2da85c0으로 rollback 가능하다. DB 복원이나 인증 비밀 변경은 이 수정의 rollback에 필요하지 않다.
