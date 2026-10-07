# 무료 비공개 베타 실제 배포 확인 — 2026-10-06

운영 URL: https://travel-inbox-rag.onrender.com . 기존 Render Free + Supabase hii Free. 코드 `b90c449`, branch `codex/private-beta-launch`, Auto-Deploy Off/Blueprint Auto Sync No. 공개 main 변경 없음.

## 실행과 결과

1. `git diff --check` → 통과. Free에서 미지원인 maxShutdownDelaySeconds만 제거하고 배포 브랜치에 push.
2. Render Blueprint Manual sync → 기존 서비스 healthCheckPath `/health/ready` 한 항목 변경을 검토/적용. 자동 생성된 이전 이미지 재배포 취소. Manual Deploy → Deploy latest commit. 콘솔 Live 및 새로운 앱을 실제 HTTP로 확인.
3. 다음 공개/비로그인 요청을 httpx로 실행했다. 외부 유료 API 호출 없음.

| 요청 | 실제 결과 |
| --- | --- |
| GET / |200·신규 PWA|
| GET /health/live |200|
| GET /health/ready |200·5개 검사true|
| GET /api/v2/session |200·auth_configured true·미로그인|
| GET /api/v2/trips |401|
| GET /api/bookings |401|
| POST /api/v2/auth/login, 잘못된 Origin |403|
| GET /api/v2/auth/login |302·Google·정확한 callback·S256|

4. Chrome/macOS 실제 브라우저에서 본인 초대→Google 계정 선택→여행 화면 성공. 지정한 본인 계정만 운영 CLI로 admin 역할 적용 후 재로그인 확인. 이메일/초대/token/cookie 비밀값은 이 보고서에 저장하지 않는다.
5. 합성 여행1·수동 음식점 예약1 생성. 날짜만 있는 예약은 시각 미확인으로 유지. `1일차 예약을 전부 알려줘` → 총1개·직접 입력 근거 표시. 새로고침 유지 확인.
6. Render Restart service 실행(15:04 KST). 새 프로세스 15:05:25 기동, 이전 프로세스15:05:29 종료. dispatcher 인계 중 readiness503 후200 회복. 같은 여행·예약1건 재조회 성공.
7. `cloud_snapshot`→`write_checkpoint`→`restore_archive`를 실제 hii에서 로컬 별도 경로로 실행. 로그인 전 거의 빈 schema 백업16.29초·복원0.04초·integrity ok·FK0. 운영 DB 복원/덮어쓰기 없음. 첫 시도는 배포 migration과 겹쳐 deadlock으로 중단됐으며, 후속 기동 완료 후 성공본만 인정했다.

## 재실행

```sh
.venv/bin/python scripts/prepare_render_env.py --check
curl -fsS https://travel-inbox-rag.onrender.com/health/live
curl -fsS https://travel-inbox-rag.onrender.com/health/ready
curl -s -o /dev/null -w '%{http_code}\n' https://travel-inbox-rag.onrender.com/api/v2/trips
```

브라우저에서는 같은 Google 계정으로 로그인하고, 합성 여행에서 날짜 질문과 새로고침을 반복한다. 재시작은 Render의 기존 서비스 Manual Deploy → Restart service로 실행하고 Events/Logs에서 완료를 확인한 뒤 조회한다. 백업은 배포/기동 작업 완료 뒤 `docs/operations/RENDER_SUPABASE.md` 절차를 따른다. 키/DB URL을 셸 히스토리나 stdout에 넣지 않는다.

## 활성 범위·남은 검증

- 본인이 시험 사용할 기본 비공개 웹 서비스가 실제 배포됨. PC를 꺼도 외부 서버로 접속 가능. Free sleep/cold start가 있어 항상 즉시 응답한다는 보장은 없음.
- 유료 LLM/임베딩/검색/지도/리뷰: OFF. 메일 저장과 AI 분석 성공은 구분한다. 수동 예약·날짜 SQL 질문 사용 가능.
- 운영 승인 후보 도쿄0/바르셀로나0. 실제 추천 지원·엄격 언어 조건 통과를 주장하지 않음.
- 이번 실환경 검증은 데스크톱 Chrome, 소유자1명·합성 여행1/예약1. 실제 휴대폰,5명 동시성, 원문 업로드/추출, 일정 preview/apply/undo 전체의 실환경 재검증은 미수행. 이전 로컬 합성 테스트 결과와 구분.
- 자동 offhost 백업·삭제 checkpoint 전송·실사용 자료를 포함한 복원·백업/DDL 동시성 보강은 후속 운영 과제. 소수 지인 확대 전 확인 필요.
- 테스트 여행은 `배포 확인용 도쿄 여행`, 예약은 `저장 확인용 점심 · 실제 예약 아님`이며 사용자 계정에 남김. 외부 예약/메시지/결제는 실행하지 않음.
