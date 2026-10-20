# 초대 없는 Google 로그인 — 2026-10-08

사용자 요청에 따라 서비스의 초대 코드 입력·가입 허용 검사를 제거했다. Google의 검증된 ID token과 인증된 이메일을 받은 첫 로그인에서 `member` 사용자를 만든다. 초대 없이 가입할 수 있어도 개인 API는 로그인해야 하며 사용자별 자료는 분리된다.

## 구현

- 로그인 화면은 Google 로그인 버튼으로 시작한다. 신규 계정 생성과 기기 간 같은 계정 사용을 안내한다.
- POST 로그인은 입력 body 없이 동작하며 Origin 검사와 Authlib state/nonce/PKCE 검증을 유지한다.
- 사용자 생성과 세션 발급은 기존 쓰기 트랜잭션에서 수행한다. 동일 identity의 동시 첫 로그인은 사용자 1개·독립 세션 2개를 만든다.
- 제공자+subject로 계정을 식별한다. 같은 이메일의 다른 identity를 합치거나 claims의 role/owner를 받아들이지 않는다. 기존 비활성 계정은 계속 거절한다.
- 이전 초대 테이블·CLI·호출 인자는 이전 자료와 테스트 도구 호환을 위해 보존하지만 로그인 허용 여부에 사용하지 않는다. DB migration 없음.
- 공개 앱 셸 캐시 v29와 JS 내용 해시를 갱신했다. README와 인증 안내도 현재 동작에 맞췄다.

## 실행한 검증

```bash
PYTHON_DOTENV_DISABLED=1 /private/tmp/going-stage1-native313/bin/python -m pytest tests/test_foundation_auth.py tests/test_foundation_api.py tests/test_request_failures.py -q
node --test tests/*.cjs
python3 scripts/version_web_assets.py --check
git diff --check
```

- Python **72 passed**, 기존 의존성 deprecation 경고2개. JavaScript **210 passed**.
- 첫 가입·미인증 이메일 거절·일반 역할·동시 로그인·기기별 세션·회수·CSRF/Origin·A/B 여행 격리 검증. 합성 claims는 Authlib 검증 경계에서만 주입하며 공개 우회 endpoint는 만들지 않았다.
- 초기 실행에서 이전 초대 정책을 기대하는 API 시험2개가 실패했고, 새 계약(이메일 검증·회수·동시 신규 계정 생성)을 검사하도록 수정한 후 위 결과를 확인했다.
- Google OAuth 제공자 자체의 대상 사용자 제한은 별도이며 이번 코드 변경으로 외부 콘솔 설정을 바꾸지 않는다.

## 운영 확인

- Render `dep-db3k527avr4c73a4uvb0`, 앱 커밋 `d154dc0`, 2026-10-08 16:14 KST **Live**. 서비스: https://travel-inbox-rag.onrender.com
- 배포 페이지에서 해당 커밋과 Deploy succeeded / Live 확인. 공개 HTML에 초대 입력 없음·새 JS 해시 일치, readiness 모든 검사 통과.
- 비로그인 여행 API401, 다른 Origin의 로그인 POST403. 기존 보호를 유지했다.
- 실제 Chrome에서 로그아웃 → 초대 없는 로그인 화면 → 기존 Google 계정 로그인 → 기존 여행 목록 조회 → 새로고침 후 유지 확인. 콘솔 error/warn0.
- 신규 사용자 가입·소유권 분리는 합성 A/B 시험 결과이며 새 실제 Google 계정 가입과 다른 물리 컴퓨터에서는 시험하지 않았다. Google OAuth 콘솔의 대상 사용자 설정은 변경하지 않았다.
- 화면 증거는 기기 임시 파일 `/private/tmp/going-no-invitation-2026-10-08.jpg`로 저장했다. 개인정보 없는 로그인 화면이다.
