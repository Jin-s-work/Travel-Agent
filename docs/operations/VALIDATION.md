> **이후 변경:** 아래는 이전 SQLite 운영 검증 이력이다. 현재 사용자 선택은 Render Free + Supabase Free이며 [cloud 전환 검증](../service-v2/reports/render-supabase-validation.md)을 우선 확인한다. 기존 Render 접근 확인과 hii private 버킷 생성까지 진행했으며 새 서비스 배포는 미실행이다.

# 06 운영 준비 실행 결과

2026-10-06 KST. 코드 기준 `aefee57` + 기존01~05와 이번06 미커밋 변경. **운영 구현·로컬 합성 검증 complete, 실제 호스팅 검증 not_run, 전체 비공개 베타 출시 blocked.** 운영 URL은 이번 작업에서 발급/배포/검증하지 않았다. 과거 README의 onrender 데모 주소는 새 비공개 서비스 검증 주소가 아니다. 계정·요금 변경, 초대 메시지, commit/push/PR은 실행하지 않았다.07~08은 선행조건으로 추가하지 않았다.

Render Dashboard는 Sign In 화면이고 준비된 서비스 ID/API 인증을 찾지 못했다. 로컬 설정은 이름/존재만 조사했고 실제 OIDC·SESSION_SECRET·외부 백업 credential이 미설정이다. 실제 OpenAI key 존재 여부와 이번 과금 승인은 별개다. 실제 key를 테스트나 이미지에 넣지 않았다.

## 구현

- Docker Python3.13.12 digest/의존성 lock, UID1000, 운영 seed OFF, tests/data/.env 제외. 영구 mount 아래 SQL/문서/vector 별도 경로. 단일 인스턴스·worker·dispatcher, 프로세스 및 migration flock, 잘못된 설정/권한/schema면 listen 전에 종료.
- migration7: 운영 identity·controls·감사. 외부 호출 전체/공급자별 OFF, read_only/maintenance. 새 job claim 차단과 이미 전송된 작업/과금의 경계를 분리한다.
- live/ready 분리, 유료 호출 없는 health, 관리자만 비용/큐/오류/재시도/불명과금/디스크/메모리/백업 관측. 일반 API no-store와 access-log OFF 유지.
- SQLite online backup + 원문 hash/활성검색 manifest, AES256-GCM, independent tombstone/권한/비용 checkpoint, S3 호환 encrypted transport 및 daily/5분 scheduler. 복원은 새 디렉터리·검증 전 접근 차단·세션 회수·최신 삭제/정책 반영·SQL 재색인. 실제 remote transport는 mock 검증이다.
- 운영 합성팩 활성화/카드/상세/지점 매칭 거절, 도시별 실제 후보 없음 배너. 일정 partial 안내가 업로드 성공 문구로 잘못 나오는 부분도 수정했다.

## 자동 실행

저장소 루트에서 아래 명령을 실제 실행했다. tests는 임시 경로, dummy credential과 fake provider를 사용한다.

```bash
OPS_RESTORE_REPORT=docs/service-v2/reports/operations-restore.json .venv/bin/python -m pytest tests -q
# 692 passed, 2 warnings in 59.40s
node --test tests/test_discovery_ui.cjs tests/test_service_worker.cjs
# 19 passed, 0 failed
python3 scripts/version_web_assets.py --check
# Public asset content hashes match index.html
git diff --check
# exit 0
docker build -t travel-agent:beta-local .
# successfully built Linux arm64 image
.venv/bin/python scripts/check_beta_image.py --report docs/service-v2/reports/operations-image-startup.json
# passed=true; UID1000; schema/identity failure exit78; second instance blocked
.venv/bin/python scripts/measure_beta.py --container travel-beta-final --url http://127.0.0.1:8781 --report docs/service-v2/reports/operations-load-512mb.json
# 5 users, 1200 reads, 0 errors, restart persistence=true
```

Starlette/Authlib httpx API deprecation2건은 남아 있다. 실제 외부 provider/host 신뢰성은 이 회귀 통과로 확정하지 않는다. macOS local venv 전체 시험과 Linux arm64 Docker 실행을 구분한다. Render 아키텍처 빌드·실제 mount 소유권은 배포 시 확인한다.

## 5명 부하

[원본 JSON](../service-v2/reports/operations-load-512mb.json). CPU0.5/memory512m Docker 제한, 영구 named volume, loopback port8781, 현재 production launcher·secure-cookie 설정. 테스트용 `tests/operations_fixture.py`만 readonly bind하여 검증된 합성 identity를 주입했다. 운영 이미지에는 우회 모듈이 없고 기본기동 미인증 API는401이다.

| 측정 | 결과 |
| --- | --- |
| 사용자/읽기 | 5명, 1,200회 GET |
| 시험 시간 | 재시작 포함15.811초 |
| client read p50 / p95 | 46.23 / 90.42ms |
| idle / 측정 말 RSS | 85,200,896 / 150,687,744 bytes |
| process peak RSS | 150,556,672 bytes (별도 시점 rusage) |
| cgroup idle / peak | 71,110,656 / 110,264,320 bytes |
| cgroup CPU 증가 | 6.600173초 |
| disk 증가 | 485,939 bytes (fixture 준비 후 기준) |
| 문서 큐 대기 / 실행 | 0.076 / 4.321초 |
| 추천 큐 대기 / 실행 | 4.384 / 0.692초 |
| 요청/작업 오류, OOM, retry | 0 / 0 / 0 / 0 |
| provider call | fake2회 settled, 실제0회, unknown0 |
| 같은 파일 재요청/SSE | 같은 job 재사용, Last-Event-ID 이어받기, 추가 provider 호출 없음 |
| 실제 docker restart | 동일 volume·기존 세션으로 예약9개 보존, B의 A여행 요청404 |

RSS와 cgroup은 공유 page/계측 방법이 달라 동일한 값이 아니다. 부하 컨테이너는 다른 기존 컨테이너가 있는 Colima VM에서 실행했다. 16초 시험은 장기 안정성·최대 파일·실제 LLM latency·Lingua 상주 메모리·Render 성능을 증명하지 않는다. 512MB는 초기 제한 가설이며 확대 전 실제 소량 API 및 지속 부하 측정이 필요하다. 증설하지 않았다.

재현용 컨테이너 설정은 `docker run --memory=512m --cpus=.5 -p 127.0.0.1:8781:7860 -v <새 테스트 volume>:/var/data -v "$PWD/tests:/fixture:ro"`에 테스트 전용 PUBLIC_BASE_URL/SESSION_SECRET/OIDC 설정과 dummy OPENAI_API_KEY, PYTHON_DOTENV_DISABLED=1을 지정하고 `travel-agent:beta-local python /fixture/operations_fixture.py`로 시작한다. 실제 secret이나 운영 volume을 사용하지 않는다. 이미지 기동 검사 스크립트는 자체 일회용 volume을 생성/정리한다.

## 백업·복원 실험

[복원 JSON](../service-v2/reports/operations-restore.json). 합성 A/B, 문서·예약·사용자 교정11:30·일정v1·ledger를 online backup한 뒤 B여행을 삭제했다. 오래된 snapshot과 최신 별도 checkpoint를 새 디렉터리에 복원했다. 무결성/소유권/세션무효화/권한/비용 중지/원문 삭제를 확인하고 surviving SQL로 실제 임시 Chroma를 재생성했다. B여행은 API404이며 재색인에 없고 A교정·일정은 유지됐다.

- 복원0.042초, 재색인0.198초, fake embedding1회, 실제 비용0. 이 값은 작은 로컬 자료 기준이며 다운로드·운영자 판단·실제 API 소요시간 제외다.
- 일반 데이터는 snapshot 시점까지만 보존한다. 이후 생성·수정은 복구되지 않는다. 삭제/권한/비용은 checkpoint시점까지 반영하며 이후5분 사이 내역은 외부 기록과 대조해야 한다.
- checkpoint없음/오래됨, 잘못된 key/변조, 기존 파일 덮어쓰기, unsafe archive path는 거절한다. 복원 후 SQL read-only/외부 OFF, pending marker가 남는다.
- 외부 bucket mock에서 암호화 upload→download/sha 길이를 확인했다. 실제 다른 장애 범위에 저장하고 새 호스트에서 복구하는 훈련은 **not_run**이다. 이 부분 때문에 실제 출시 gate는 아직 닫힌다.

## 실제 브라우저 흐름

`tests/browser_itinerary_fixture.py`, loopback8765/8766의 합성 OIDC(Authlib 서명/state/nonce/PKCE), fake 추출/embedding/경로로 실행했다. 실제 Google 로그인이나 실제 지도·리뷰를 검증한 것은 아니다.

1. 로그인→도쿄 여행 생성(11월6~9일)→왕복 항공/투어가 함께 든 메일 업로드→새로고침 후2예약 확인.
2. 투어 시작15:00→15:30 교정·종료16:00 저장→1일차 전체 질문에서 현재 교정값과 왕복 시간대 확인→원문 출처 열기.
3. 개인 장소명/메모 저장→조건 입력→합성 추천→상세→2곳 비교. 실제 후보 없음, 리뷰OFF, 잔여석미확인 표기를 확인.
4. 11월6일은 시간대 없는 고정 투어 주변 장소를 자동 배치하지 않았다. 현재 구간이 없는 예약에는 기존 교정폼으로 새 event를 추가할 수 없다는 제한이 있다. 수동 예약 생성 시에는 구간 입력이 가능하다. 원문을 임의로 고치거나 확정 예약을 움직여 통과시키지 않았다.
5. 11월7일 잠정 일정 생성→09:20–10:20을10:00–10:45로 preview→apply v2→새로고침 유지→undo preview/apply로 새v3 확인. 원래 예약은 변경하지 않았다. 잠정 운영시간을 검증 완료로 표시하지 않는다.
6. 390×844에서 scrollWidth=clientWidth390, 긴 일본어 이름 줄바꿈·하단 메뉴 확인, 콘솔 error0. 외부 HTTPS mobile/실제 기기PWA 설치는 not_run.

화면: [최종 일정](../service-v2/reports/operations-browser.png), [모바일](../service-v2/reports/operations-mobile.png). 새로고침 후 선택 후보 이름이 ‘저장된 선택 장소’로 보일 수 있으나 실제 타임라인에는 저장한 이름이 유지된다. 다음 수정에서 후보 표시 복원을 보완할 수 있으며 데이터 유실로 보지 않는다.

## 인수 기준 대응

| 기준 | 결과 | 근거/경계 |
| --- | --- | --- |
| AUTH-01~07, DATA-01~03 | passed 합성 | 기존 foundation/API/동시 질문 회귀 포함, 외부 OIDC not_run |
| AUTH-08 / UI-05 | passed 로컬 | 안전 오류·access log OFF·개인 캐시 제외; 실제 hosting proxy log 미검증 |
| DATA-04~08, REC-01~06, BOOK-01/02/05 | passed 합성 / 실제 partial | discovery/review/recommendation 전체 회귀, 실제팩 표시 승인0 |
| PLAN-01~08 | passed 합성 | 시간·고정예약·DST·실패경로·편집·undo 회귀,05 SIGKILL 포함;07 PlanB는 범위 밖 |
| OPS-01/03/04/05/06 | passed 합성 | lease/fence/receipt/삭제/예산 경쟁/429·timeout/공급자OFF,692개 전체 회귀 |
| OPS-02 | passed 격리 / live not_run | AES/일관백업/이후삭제/새Chroma 복원; offhost 실환경 대기 |
| OPS-07 | passed 로컬 / live not_run | 이미지 UID/쓰기/migration fail/단일 프로세스/health·SQL degrade; 실제 host OOM 훈련 없음 |
| OPS-08 | passed 제한 부하 / live not_run | 5명/1,200읽기/큐 직렬/중복/SSE/restart; 실계정 청구/장기부하 미검증 |
| REVIEW 전체 production gate | blocked / OFF | 실제수집·이용·대상리뷰 언어품질 미통과 |
| BOOK-03/04/06 및07~08 | not_applicable 이번단계 | 부가 기능을 첫 배포 선행조건으로 추가하지 않음 |

## 실제 개방 전 남은 일

Render 또는 기존 host 선택/접근과 새 지출의 월 상한, OIDC client/callback/초대 계정, S3 backup destination/key가 필요하다. 값은 호스팅 Secrets에 입력한다. [RUNBOOK](RUNBOOK.md)의 관리자→2명→5명 검증, actual HTTPS cookie/CSRF·30분 유휴·restart·offhost restore를 통과해야 한다. [도시 보고서](CITY_READINESS.md)의 실제 후보 검수 전에는 두 도시의 추천 서비스 R1 완료로 보고할 수 없다. 실제 URL을 받기 전07로 넘어가 배포가 끝난 것처럼 표시하지 않는다.

검증 후 이번 작업의 loopback fixture 서버와 `travel-beta-load`/`travel-beta-final` 컨테이너·전용 volume을 정리했다. 기존 다른 컨테이너는 유지했다. 스크린샷과 비밀 없는 측정 JSON은 저장소에 남겼다.
