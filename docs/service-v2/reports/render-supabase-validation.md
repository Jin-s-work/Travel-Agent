# Render Free + Supabase Free 구현 검증

2026-10-06. 기존 `aefee57`와 사용자01~06 미커밋 변경을 보존했다. 추가 cloud 저장 구현도 미커밋이다. 실제 공급자 호출/결제0, Git push0, Render 재배포0. Supabase `hii`의 Healthy 상태를 확인하고 private `travel-private` 버킷(1MB, public OFF, policies0)을 생성했다. 기존 `sta-saju`는 변경하지 않았다.

## 결과와 경계

- `STORAGE_BACKEND=supabase`: PostgreSQL schema8/cloud1, Supabase private objects, pgvector immutable collections, SQL checkpoint/receipt. 로컬 SQLite/Chroma 모드 유지.
- 실제 **로컬 PostgreSQL17/pgvector**로 검증했다. Supabase Storage HTTP는 fake, OIDC는 합성 IDP/검증된 합성 claim, LLM·embedding은 fake다. 실 Supabase pooler/TLS/API key/Storage 왕복은 미검증이다.
- cache 삭제 후 새 앱: 로그인·예약8개·원문·job 성공·pgvector 결과 유지, 추출/임베딩 중복 호출0, 타인 원문404.
- 실제 SIGKILL 이후 lease 만료/fence 교체: DB checkpoint와 provider receipt 재사용, 외부 단계 재호출0, 비용 예약1건.
- public bucket 거절, object outage/손상 시 SQL 읽기200, same filename·trip별 중복 격리, 마지막 예산 두 요청 중 한 건만 허용, browser role 접근 거절.
- 다른 프로세스가 가진 generation session pin은 reclaim을 막는다. 삭제 도중 업로드가 늦게 끝나도 여행/문서 재활성화 불가, durable manifest로 고아 정리.
- PostgreSQL consistent snapshot → AES-GCM 백업 → 이후 여행 삭제 checkpoint → 격리 SQLite 복원 → 빈 PostgreSQL로 import: 삭제 여행 미복구, 살아 있는 예약8개·원문 보존, 세션 회수/read-only/검색 재구축 필요. 원본 파일은 바꾸지 않았다. 실제 offhost 전송/정기 백업은 미설정이다.

## 실행 명령

```bash
# 694 passed, 11 skipped, 2 warnings in76.42s
# 11 skipped는 아래 별도 PostgreSQL suite에서 실행한다.
.venv/bin/python -m pytest tests -q

# 11 passed, 2 warnings in11.16s
TRAVEL_TEST_POSTGRES_DSN=postgresql://postgres:travel-test-only@127.0.0.1:55439/postgres \
  .venv/bin/python -m pytest tests/test_cloud_storage.py -q

# 158 passed, 3 deselected, 2 warnings in140.56s
# 실제 PG로 기존 API/소유권/추천/일정/리뷰/작업 계약 재실행
TRAVEL_TEST_POSTGRES_DSN=postgresql://postgres:travel-test-only@127.0.0.1:55439/postgres \
 .venv/bin/python -m pytest -p tests.postgres_plugin \
 tests/test_foundation_auth.py tests/test_foundation_api.py tests/test_reliability_api.py \
 tests/test_itinerary_api.py tests/test_recommendation_api.py tests/test_discovery_foundation.py \
 tests/test_review_integration.py tests/test_review_remote_cleanup.py tests/test_reliability_jobs.py \
 -k 'not version_two_migrates and not real_sigkill and not process_restart and not restoring_older_backup' -q

# 19 passed, 0 failed
node --test tests/test_discovery_ui.cjs tests/test_service_worker.cjs
.venv/bin/python scripts/version_web_assets.py --check
.venv/bin/python -m compileall -q src api.py
git diff --check
# 모두 exit0

docker build -t travel-agent:supabase-local .
# Linux arm64 빌드 성공. UID1000, zero policy 포함, .env 없음,
# 메모리512MiB/network none 컨테이너에서 cloud source import 성공.
```

PostgreSQL 계약 plugin은 임시 `travel_test_*` 스키마에 실제 쿼리를 실행하고 종료 후 자기 스키마만 삭제한다. SQLite 파일을 직접 여는 이관/복원/하위 프로세스3항목은 교차 suite에서 제외하고 원래 SQLite suite 및 별도 cloud 복원/SIGKILL로 검사했다. 삭제 테스트의300ms lease가 네트워크 SQL의 선행 읽기 도중 만료되는 불안정성을 발견해 **삭제 시험에만10초 lease**를 사용했다. lease 만료 시험은 짧은 lease 그대로 유지한다. 초기 실패와 보완을 숨기지 않는다. Python warnings2건은 기존 Starlette/Authlib httpx deprecation이다.

## 512MiB, 0.5 CPU, 5명

[측정 JSON](render-supabase-load.json). 실제 production launcher와 원격 DB 경로, synthetic identity·Storage·LLM만 test-only 주입했다. fixture는 이미지에 복사하지 않고 readonly mount했다. 같은 Docker host의 PostgreSQL에 연결하므로 인터넷 지연은 없다. localhost만 노출했다.

| 측정 | 값 |
| --- | --- |
| 읽기 요청 | 5명, 1,200회 |
| 재시작 포함 시험 | 11.578초 |
| HTTP p50 / p95 | 15.14 / 51.92ms |
| idle / 측정말 process RSS | 97,738,752 / 106,729,472 bytes |
| cgroup peak | 85,815,296 bytes |
| 오류 / OOM / job retry | 0 / 0 / 0 |
| 문서 큐 / 실행 | 2.197 / 3.300초 |
| 추천 큐 / 실행 | 5.517 / 1.271초 |
| 실제 공급자 / fake 호출 | 0 / 2 settled |
| app 디스크 증가 | 0 bytes |
| 재시작 / 중복 / 소유권 / SSE | 예약9개 보존 / 같은 job / 타인404 / 이벤트 재연결 성공 |

RSS/cgroup은 서로 다른 메모리 집계다. LLM·리뷰 언어 모델을 실부하로 실행하지 않았으며 긴 기간/많은 벡터/최대 파일/실 Render 인스턴스 성능을 증명하지 않는다. DB500MB/프로젝트 전체 egress는 별도 관측이 필요하고 자동 증설은 없다. 테스트용 identity 정보는 일회용 volume에만 있으며 보고서에는 토큰을 넣지 않았다.

재현: `pgvector/pgvector:pg17` 로컬 disposable container를 준비하고 `travel-cloud-validation` network에 연결한다. 앱에는 `--memory 512m --cpus .5`, 새 `/var/data` volume, `tests:/app/tests:ro`, 테스트용 HTTPS OIDC 설정을 전달한다. `TRAVEL_TEST_POSTGRES_DSN`은 해당 disposable DB, 표면 `DATABASE_URL`은 synthetic Supabase 형식, `SUPABASE_URL=https://fixture.supabase.co`, `SUPABASE_SECRET_KEY=sb_secret_fixture`로 둔다. `python /app/tests/cloud_operations_fixture.py`가 실제 SQL과 fake Storage를 주입하고 production launcher를 부른다.

```bash
.venv/bin/python scripts/measure_beta.py --container travel-cloud-load \
 --url http://127.0.0.1:8783 --report docs/service-v2/reports/render-supabase-load.json
```

## 브라우저

`tests/browser_cloud_fixture.py` + PostgreSQL에서 합성 OIDC 로그인 → 여행 생성 → 수동 음식점 예약12:30 → 새로고침 → 서버 SIGTERM/재기동 → 기존 session/여행/예약1개 유지. [실제 화면](../screenshots/cloud-restart.jpg). 이 회귀는 실제 Google 로그인 검증과 다르다. 브라우저 메일 업로드는 Chrome extension의 Allow access to file URLs가 꺼져 중단됐다. 권한을 변경하거나 우회하지 않았다. 원문 업로드·복구는 위 HTTP integration tests로 검증했다. 이전06 브라우저의 전체 탐색/비교/일정 편집 검증은 기존 기록에 남으며 이번 cloud 브라우저에서 모두 재수행했다고 주장하지 않는다.

## 실제 계정·배포

기존 Render 서비스 ID `srv-d9oq7hmgekts73ejjts0`, Free/Docker/Blueprint/main, 마지막 배포aefee57, URL https://travel-inbox-rag.onrender.com 확인. 실제 새 코드/환경 변경은 미실행이다. Render에는 OpenAI/Tavily 키만 있으며 값은 열지 않았다. 이전 배포의 유료 기능이 이번 zero policy로 바뀌었다고 주장하지 않는다.

사용자 선택: Supabase `hii` 재사용. Healthy와 Tokyo region/session pooler `aws-0-ap-northeast-1.pooler.supabase.com:5432` 확인. private 버킷만 새로 생성했으며 실제 여행/메일 업로드·DB 스키마 배포는 아직 하지 않았다. DB 비밀번호와 서버 secret, OIDC client/secret, session secret의 Render 직접 입력이 필요하다. 비밀번호를 임의 재설정하지 않았다. 신규 결제/계정/지인 메시지는 없다.

운영 첫 연결/HTTPS 로그인/실제 bucket 왕복/배포 재시작/외부 백업 복원이 끝나기 전 출시 상태는 blocked다. 도쿄·바르셀로나 승인 운영 후보 각각0, 엄격 리뷰OFF. 무료 sleep/pause와 비동기 작업 지연을 허용하는 소규모 서비스이며 무중단 SLA는 없다. 다음 설정은 [RENDER_SUPABASE](../../operations/RENDER_SUPABASE.md)를 따른다.
