# 무료 비공개 배포 입력 — 2026-10-06

대상은 기존 Render `travel-inbox-rag`와 기존 Supabase `hii`다. 기존 `main` 서비스는 이전 버전이며, 새 코드 준비와 실제 개방을 구분한다.

## 비밀값 입력

로컬 `deploy/render-supabase/.env`는 Git 제외·권한600인 입력 파일이다. 값은 채팅이나 보고서에 적지 않는다. 기존 루트 `.env`의 유료 API 키를 옮기지 않는다.

| 이름 | 입력 위치/뜻 |
| --- | --- |
| `SUPABASE_DB_PASSWORD` | hii의 DB 비밀번호. 모르면 소유자가 Supabase Connect/Database에서 직접 재설정한 뒤 입력 |
| `SUPABASE_SECRET_KEY` | hii Settings → API Keys의 서버용 secret 또는 legacy service_role. publishable/anon과 다름 |
| `OIDC_CLIENT_ID` | Google Auth Platform → Clients → Web application |
| `OIDC_CLIENT_SECRET` | 같은 Google 웹 클라이언트의 보안 비밀 |
| `DEPLOY_ADMIN_EMAIL` | Google 로그인에 사용할 본인 이메일. 개인 초대 생성용이며 Render 설정에 전송하지 않음 |

`SESSION_SECRET`은 입력 파일에 준비했다. 직접 입력하는 5개와 이 세션 키만 `.env`에 남기고, 무료/운영 모드·URL·저장 경로·job 설정은 `scripts/prepare_render_env.py`의 기본값에서 채운다. 루트 `.env`는 기존 로컬 개발용이며 배포에 읽지 않는다. 파일은 Render가 자동으로 읽지 않는다. 값 입력 후 다음 검사/변환을 실행한다.

```sh
.venv/bin/python scripts/prepare_render_env.py --check
.venv/bin/python scripts/prepare_render_env.py
```

DB의 `verify-full`에는 이미지에 포함한 Supabase 공식 CA(`/app/deploy/render-supabase/prod-ca-2021.crt`)를 사용한다. 일반 OS CA bundle만 사용하면 현재 pooler의 인증서 검증이 실패한다.

누락은 변수 이름만 표시한다. 변환은 비밀번호를 URL 인코딩하고 Session pooler5432·TLS 검증·고정 hii 프로젝트·production·zero spend를 검사한다. `.env.render`를 권한600으로 저장하며 비밀값을 stdout에 출력하지 않는다. Render Environment의 Import .env에 사용하는 파일이다. 원문 비밀번호, 관리자 이메일, 선택적인 로컬 Render API 키는 제외한다. 유료 OpenAI/Tavily/Apify 키는 빈 값으로 덮어쓰도록 구성한다.

## Google 웹 클라이언트

- Google Cloud에서 해당 프로젝트 선택 → Google Auth Platform → 시작하기(처음만).
- 앱 이름 `Travel Inbox`, 지원/연락 이메일은 소유자, 개인 Google 계정 사용자는 대상 ‘외부’.
- Clients → Create client → Web application, 이름 `Travel Inbox Web`.
- Authorized redirect URIs: **`https://travel-inbox-rag.onrender.com/api/v2/auth/callback`**.
- 현재 Authlib 서버 리디렉션 흐름에는 JavaScript origin을 요구하지 않는다.
- 발급 ID/Secret은 위 로컬 입력 파일에만 저장한다. 기존 로그인 범위는 openid/email/profile이다.

공식 안내: https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid

## 적용 순서

1. 코드/비밀 누락 검사와 Docker 빌드를 통과한 릴리스 사용.
2. 기존 Render는 `main` On Commit 상태다. 준비 중 브랜치는 `codex/private-beta-launch`이며 main/운영 서비스는 설정 준비 전에 갱신하지 않는다.
3. 인증·DB 설정이 준비되면 기존 Blueprint diff를 검토해 Free·instance1·autoDeploy off·health `/health/ready`·새 환경 설정을 적용한다. 유료 업그레이드와 중복 서비스를 생성하지 않는다.
4. 기동 migration 성공, liveness/readiness, 개인 API401을 확인한다. 운영 설정 누락을 개발 모드로 우회하지 않는다.
5. 본인 이메일에만 초대 생성 후 실제 Google 로그인·예약 CRUD·재시작 유지 검증. 초대 토큰은 로컬 개인 파일로 전달하고 공개 URL/로그에 넣지 않는다.
6. Supabase 원문 저장·영속 DB·격리 백업 복원 검증 후 지인 확대로 진행한다. 무료 sleep과 새 AI 호출OFF를 유지한다.

DB 비밀번호 재설정과 Google 자격 증명 생성은 계정 소유자가 직접 수행한다. 현재 인증/DB 비밀 없이 새 운영 앱은 기동할 수 없으며, 이전 Render URL을 새 버전 배포 완료로 보고하지 않는다.
