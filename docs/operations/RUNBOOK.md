> **2026-10-06 적용 변경:** 사용자가 기존 Render Free + Supabase Free(`hii`)를 선택했다. 운영 DB는 PostgreSQL, 원문은 private Storage, 검색은 pgvector로 전환한다. 아래 SQLite/Chroma/영구 VM 설명은 로컬 모드와 이전 설계 이력이다. 현재 배포·비용·백업 계약은 [전환 안내](RENDER_SUPABASE.md)를 우선 적용한다. 새 버전 실제 배포는 별도 검증 상태다.

# 비공개 베타 운영 절차

> **현재 결정(2026-10-06): 비용0원 우선. 유료 Render 제안은 보류입니다. [무료 구성](FREE_HOSTING.md)을 먼저 확인하세요.**

2026-10-06, schema 7. 현재는 **로컬 코드·합성 시험 완료, 외부 배포 대기**다. `render.yaml`을 저장한 것만으로 서비스가 생성되거나 요금이 발생하지 않는다. [검증 기록](VALIDATION.md), [비용](COSTS.md), [도시 준비 상태](CITY_READINESS.md)를 먼저 확인한다. 07~08 단계는 이 배포의 선행조건이 아니다.

## 구성과 기동

FastAPI/PWA 한 컨테이너, UID/GID 1000, uvicorn 1 worker와 SQLite dispatcher 1개. Python 3.13.12 이미지 digest와 Python 의존성 `requirements.lock`을 고정했다. macOS 개발 환경이 아닌 Linux arm64 컨테이너에서도 빌드·읽기·작업·재시작을 검증했다. Render 실행 아키텍처에서의 최종 빌드는 별도 확인한다.

`python -m src.operations.launch`가 HTTPS 인증 설정, seed OFF, worker 수, 실제 mount, 경로 격리, symlink, UID 및 쓰기 권한을 검사한 후 mount의 `.service.lock`을 독점한다. DB의 `.migration.lock`에서 migration을 직렬화한다. 실패하면 HTTP socket을 열지 않는다. `uvicorn api:app --workers 2` 등 우회 시작 명령을 호스팅에 지정하지 않는다.

| 저장 종류 | 운영 경로 | 복구 기준 |
| --- | --- | --- |
| SQLite + WAL | `/var/data/sql/service.sqlite3` | 온라인 SQLite backup API |
| 원문 | `/var/data/documents/<server trip ID>/<opaque ID>` | manifest 해시가 맞는 살아 있는 문서 |
| Chroma | `/var/data/vectors` | SQL 현재 예약·허용 원문에서 재생성 |
| 공급자/작업 receipt | `/var/data/sql/private-job-artifacts` | 재시작 시 유지, 재해 복원에서는 replay 차단 |
| 구 자료 격리 | `/var/data/legacy-emails`, `/var/data/legacy-chroma` | 별도 명시 이관, 자동 계정 배정 금지 |

`web/` 밖이다. 운영 이미지에 `.env`, data, seed, tests, 문서는 복사되지 않는다. mount는 빌드/pre-deploy에서 접근하지 못하므로 migration은 시작 잠금 안에서 수행한다. 호스팅 디스크 UID가 다르면 관리자가 **빈 mount만** UID 1000에 맞춘다. 기존 데이터에 무조건 `chmod -R 777`을 실행하지 않는다.

## 호스팅 secret와 설정

Render Dashboard의 해당 서비스 → Environment / Secret Files에 입력한다. 채팅·Git·Dockerfile에는 값이나 실제 초대 코드를 넣지 않는다.

| 이름 | 목적·설정 |
| --- | --- |
| `APP_ENV` / `SEED_ON_EMPTY` / `WEB_CONCURRENCY` | production / 0 / 1 |
| `PERSISTENT_STORAGE_ROOT` / `DATABASE_PATH` / `DOCUMENTS_DIR` / `VECTORS_DIR` | 위 mount 경로 |
| `EMAILS_DIR` / `CHROMA_DIR` | 구 자료 전용 경로, 신 API 데이터와 분리 |
| `PUBLIC_BASE_URL` | 실제 `https://<service>.onrender.com` 또는 사용자 도메인, trailing path 없음 |
| `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | 준비된 OIDC web client |
| `OIDC_SERVER_METADATA_URL` | 기본 Google discovery URL. callback은 `<PUBLIC_BASE_URL>/api/v2/auth/callback` |
| `SESSION_SECRET` | blueprint 생성값 또는 32자 이상 무작위 값 |
| `OPENAI_API_KEY` | 전용 프로젝트 key. 실제 호출 검증 전 비용 OFF 유지 |
| `EXTRACTION_MODEL`, `ANSWER_MODEL`, `AGENT_MODEL` | 기존 `gpt-5-mini`; 현재 deprecated 표기 때문에 계정 사용 가능·종료일 확인 필요 |
| `EMBEDDING_MODEL`, `REASONING_EFFORT` | text-embedding-3-small / low |
| `PRICING_CONFIG` | `/etc/secrets/pricing-policy.json`, [비활성 시작 설정](pricing-policy.disabled.json)을 secret file로 등록 |
| `JOB_LEASE_SECONDS`, `JOB_HEARTBEAT_SECONDS` | 90 / 20. heartbeat는 lease의 절반 미만 |
| `JOB_MAX_ATTEMPTS`, `JOB_DEADLINE_SECONDS`, `JOB_SHUTDOWN_SECONDS` | 3 / 900 / 5. SDK retry=0, 외부 동시성 1은 코드 계약 |
| `BACKUP_ENABLED` | 초기 인프라 확인 0; 초대 개방 전 1로 바꾸고 외부 왕복 복원 검증 필수 |
| `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`, `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX` | 별도 장애 범위의 비공개 S3 호환 bucket; HTTPS 필수 |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | 이 bucket/prefix만 읽기·쓰기·목록·보관기한 삭제 가능한 전용 credential |
| `BACKUP_ENCRYPTION_KEY` | 별도 비밀 관리자에도 보관한 base64 32-byte AES key. 디스크와 함께 유실되면 복구 불가 |
| `BACKUP_INTERVAL_SECONDS`, `BACKUP_CHECKPOINT_SECONDS`, `BACKUP_RETENTION_DAYS` | 86400 / 300 / 7; 체크포인트는 가장 오래된 일일 백업을 덮도록 추가 하루 보관 |

Tavily/Apify/Places/Routes는 첫 운영 blueprint의 필수 key가 아니다. 관련 공급자 단가·권한·품질이 없으면 OFF. 키가 있다는 사실만으로 활성화하지 않는다. OIDC 설정 성공은 allowlist 가입 허가와 다르다. 초대와 활성 계정이 모두 필요하다. 계정 최대 5명 개방은 아래 단계별 운영 절차로 제한한다.

## 관리자 개방 → 지인 2명 → 최대 5명

1. 계정과 월 비용 상한 승인 후 paid blueprint를 연결한다. 자동배포 OFF, 디스크 1GB, 512MB/0.5 CPU를 제안한다. 유료 disk 서비스는 배포 중 짧은 정지가 있으므로 무중단으로 안내하지 않는다.
2. 첫 관리자의 이메일만 초대한다. 서버 shell에서 `python -m src.foundation.cli invite --email <관리자이메일>` 실행 후 나온 코드는 본인에게만 수동 전달한다. 이메일 자동 발송은 없다. CLI 출력은 인증 자료이므로 public 빌드 로그에서 실행하지 않는다.
3. OIDC 로그인·가입 허가·쿠키·Origin/CSRF·구 API 401/503·다른 owner 404를 실제 HTTPS에서 확인한다. 관리자 승격은 서비스 정지 후 `python -m src.foundation.cli set-role --user-id <ID> --role admin --offline`; 기존 세션이 회수되므로 재로그인한다.
4. `GET /health/live`: 프로세스만. `GET /health/ready`: schema, 쓰기, dispatcher, 인증 설정, 복원 gate. 외부 유료 요청 없음. 설정만으로 실제 OIDC 통신 성공을 주장하지 않는다.
5. 외부 백업을 연결하고 `python -m src.operations.cli offhost --full` 실행, 내려받아 격리 복원까지 확인한다. 정기 `BACKUP_ENABLED=1` 전환 후 관리자 `/api/v2/admin/operations`에서 `backup_events`의 성공 확인. 실패는 저장 조회를 닫지 않고 운영 로그에 code만 남긴다.
6. 단가·상한·계정 잔여금액 검토 후 필요한 AI만 활성화한다. disabled 가격 파일의 `halted`를 false로 바꾸는 것은 비용 승인 후 호스팅 secret 편집이다. 실제 모델별 메일 1건·질문 1건부터 시험하고 ledger/공급자 대시보드를 대조한다. 월 $5는 제안 내부 한도이며 승인/결제 실적이 아니다.
7. 준비된 초대 계정 2명으로 소유권·메일·교정·보관함·일정을 확인하고 재시작한다. 30분 미접속 뒤 HTTPS 응답 및 저장 자료 유지까지 확인한다. 이후 최대 5명으로 확대한다. 미준비 도시의 추천과 엄격 리뷰는 OFF 상태를 안내한다.

## 장애 격리와 관측

```bash
python -m src.operations.cli status
python -m src.operations.cli control --external off --reason PROVIDER_INCIDENT
python -m src.operations.cli control --disabled-providers openai --reason PROVIDER_INCIDENT
python -m src.operations.cli control --mode read_only --reason STORAGE_INCIDENT
python -m src.operations.cli control --mode maintenance --reason RESTORE_PREPARATION
```

external OFF는 **새 외부 호출**만 차단한다. 이미 전송한 요청과 외부 과금은 취소된 것이 아니다. read_only는 새 mutation과 job claim을 중지하지만 이미 실행 중인 worker는 SQL fence에 따라 끝날 수 있다. 완전한 정지는 호스트에서 프로세스를 종료하고 lease 만료를 확인한다. 공급자/Chroma 장애라도 SQL 여행·예약·일정 조회는 계속 가능하다. 마지막 budget 예약은 unknown을 포함해 보수적으로 집계한다.

`GET /api/v2/admin/operations`는 관리자만 사용할 수 있고 다른 사용자는404다. p50/p95(최근 최대2000 HTTP 요청), RSS 최고값, CPU, 디스크 여유, queue age, job 오류·재시도, 공급자 state별 호출 수, 불명 과금, 최근 백업 결과를 제공한다. metric은 진단값이며 지연이 긴 SSE와 일반 HTTP를 별도 실험으로 측정한다. 일반 access log는 OFF; 원문·질문·예약번호·토큰·provider response를 에러 로그에 쓰지 않는다. 메트릭 수집은 별도 관리자 요청이므로 실패가 정상 사용자 조회를 차단하지 않는다.

## 온라인 백업과 격리 복원

```bash
# key는 환경/secret에서 읽는다. 커맨드라인에 넣지 않는다.
python -m src.operations.cli snapshot --output /tmp/travel-snapshot.enc
python -m src.operations.cli checkpoint --output /tmp/travel-checkpoint.enc
python -m src.operations.cli offhost --full
python -m src.operations.cli restore \
  --archive /secure-download/snapshot.enc \
  --checkpoint /secure-download/checkpoint.enc \
  --destination /var/data/restore-validation
```

정기 scheduler는 lifespan에서 단일 leader로 실행하며 full 하루 1회, 삭제/권한/비용 checkpoint 5분마다를 제안한다. SQLite `BEGIN IMMEDIATE`로 문서 저장·삭제와 snapshot 원문 복사 사이의 경쟁을 막고 SQLite online backup API를 쓴다. WAL 읽기는 가능하지만 복사 중 쓰기는 잠깐 대기한다. 1GB 원문 상한의 소규모 방식이며 큰 데이터셋은 별도 object storage로 이관해야 한다. 현재 애플리케이션 config값 자체 대신 schema/app revision·가격 설정 해시·활성 generation manifest·원문 해시를 기록한다.

AES-256-GCM으로 header까지 인증한다. 복호화는 tag 검증이 끝난 뒤에만 tar 해제하며 경로 탈출·symlink·특수 파일·크기·manifest 누락·hash·SQLite integrity를 검사한다. 평문은 0700 임시 디렉터리에 두고 정상/예외 시 정리한다. S3는 encrypted object만 업로드하고 HEAD 크기/hash metadata를 대조한다. 실제 왕복 다운로드와 복원 훈련이 없는 bucket을 검증 완료라고 부르지 않는다. 디스크 snapshot은 이 백업의 대체가 아니다.

최근 삭제 checkpoint가 없거나 다른 instance이거나 기본 600초보다 오래되면 복원이 거절된다. 운영 정지 시간이 길면 **마지막 성공 checkpoint와 이후 삭제 요청이 없음을 관리자가 입증한 경우**에만 `--max-checkpoint-age-seconds`를 늘린다. 5분 checkpoint 사이의 삭제를 완전히 보장하지 못하며 외부 접수한 삭제 기록도 추가 대조해야 한다. 이용 범위가 제한된 리뷰 receipt/집계·작업 중간 원문은 백업에서 제외/비활성화한다.

복원은 새 디렉터리만 허용한다. 최신 tombstone과 권한 회수를 적용하고 삭제 원문·예약 이벤트·교정·보관함·추천·일정·작업/SSE payload를 제거한다. 모든 세션/사용 전 초대를 회수한다. 백업 이후 생성된 사용자/여행은 비용 회계 FK를 보존하는 비활성/삭제 placeholder로만 남기며 여행 내용을 복원하지 않는다. 최신 비용 예약을 병합하고 미확정 전송은 unknown, 비용과 외부 호출은 OFF다. 미완료 job은 `RESTORE_REVIEW_REQUIRED`로 취소해 비용을 자동 재발생시키지 않는다. **일반 재시작의 checkpoint 재개와 재해 복원은 다른 동작**이다.

`RESTORE_PENDING.json`이 있으면 production launcher와 개인 API가 닫힌다. 격리 주소에서 integrity/owner/deletion/예약 교정/일정버전/검색재생성을 확인한다. 일단 SQL 읽기만 열려면 아래 모든 검증값을 실제로 확인한 보고서를 만들어 실행한다.

```json
{"integrity":true,"owner_isolation":true,"deletion_scrub":true,"saved_itinerary":true,"search_rebuild_or_degraded":true}
```

```bash
# DATABASE_PATH/DOCUMENTS_DIR/VECTORS_DIR를 복원 디렉터리로 명시한 shell에서 실행
python -m src.operations.cli release-restored-reads --verified-report /secure-review/verified.json
```

이 명령은 읽기 전용·외부 OFF를 유지한다. 활성 검색 collection은 복원하지 않아 SEARCH_REBUILDING일 수 있다. 최신 결제 사용량/unknown을 `Budget.reconcile`로 근거와 함께 정산한 뒤 비용 중지 해제를 별도로 검토한다. 현재 SQL 예약 기반 검색 복구는 인증된 질문의 자동 복구 job 또는 관리자 내부 `Jobs.enqueue(... operation='reindex' ...)` 경로를 사용한다. 실제 embedding 비용과 소요시간을 별도 계측한다. 복원으로 현재 요금 계정의 다른 앱 사용까지 되돌릴 수 없다.

제안 RPO: 일반 데이터24시간, 삭제·권한·비용5분. 제안 RTO4시간. 로컬 소량 합성 복원 속도는 실제 재난 RTO 보장이 아니다. 복원용 key는 bucket/운영 disk와 독립적으로 보관한다. 키 교체 때 이전 백업 보관기한이 끝날 때까지 이전 key도 유지한다.

## 되돌리기 종류

- **기능 OFF**: controls로 외부 요청/개인 mutation을 차단한다. DB 버전과 데이터는 바꾸지 않는다. 개인 조회를 유지할 수 있다.
- **이미지 rollback**: schema7을 읽는 호환 이미지로만 교체한다. 과거 schema6 이미지는 신규 DB를 거절하므로 곧바로 되돌리지 않는다. forward fix 또는 백업/복원 검증이 필요하다. 자동배포 OFF에서 수동 선택한다.
- **DB 복원**: 서비스 정지, 현재 tombstone/비용 checkpoint 확보, 새 디렉터리 격리 복원, 무결성/소유권/검색 검증, 운영 경로 교체다. 중간 신규 데이터 손실을 수반하며 이미지 rollback과 다르다.

`SESSION_SECRET` 교체는 진행 중 OAuth state 쿠키를 무효화하지만 SQL session token을 자동 회수하지 않는다. 전체 세션 회수가 필요하면 maintenance 상태에서 세션 삭제와 사용자 session_epoch 증가를 한 트랜잭션으로 수행한 후 재로그인시킨다. OIDC client secret/key 교체 뒤 실제 로그인을 다시 확인한다. 알 수 없는 과금은 호출 실패만으로 환불 처리하지 않는다.

## 외부 백업 가져오기

bucket의 관리 화면 또는 아래 CLI로 암호화된 파일을 가져온다. 새 복구 호스트에서도 기존 DB 없이 실행할 수 있다. snapshot과 checkpoint는 같은 instance_id여야 한다. `remote-list`는 가장 최근30개를 보여주므로 오래된 full snapshot은 bucket 목록에서 정확한 이름을 확인한다.

```bash
python -m src.operations.cli remote-list
python -m src.operations.cli remote-fetch --object snapshot-실제이름.enc --output /secure-review/snapshot.enc
python -m src.operations.cli remote-fetch --object checkpoint-실제이름.enc --output /secure-review/checkpoint.enc
```

기존 출력 파일을 덮어쓰지 않는다. S3 SHA metadata/길이와 archive AES-GCM 인증을 각각 확인한다. 가져오기 실패는 운영 disk 백업 성공을 뜻하지 않는다. 실제 offhost 업로드→다운로드→새 호스트 복원까지 통과하기 전에는 지인 초대를 확대하지 않는다.
