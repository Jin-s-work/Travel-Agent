# V3 1단계 schema11 이관과 복구

## 변경 범위

`src/discovery/intent_schema.py`의 DDL은 discovery_contexts/ discovery_intents를 추가한다. 기존 테이블 열/예약/문서/추천·일정 snapshot은 변경하지 않는다. `discovery_conditions` 행이 있지만 새 context가 없으면 기존 JSON 전체를 명시 override로 읽는다. 기본값이었다고 추측해 지우지 않는다. 이후 사용자가 단일 추천을 제출한 경우에만 새 context/intent 행을 쓴다.

SQLite는 version10에서 BEGIN IMMEDIATE→두 테이블→PRAGMA user_version11→COMMIT. PostgreSQL은 기존 migration advisory lock/transaction 안에서 두 테이블과 schema_version11을 저장한다. migration 실패 시 startup/readiness가 실패하며 개인 요청을 열지 않는다. 새 PG 테이블에도 기존 private schema/브라우저 역할 권한 회수/RLS 정책을 적용한다.

stop_id는 현재 여행 구간 선택의 참조다. context에는 의도적으로 stop FK를 강제하지 않는다. 구간이 삭제된 경우 선택 기록을 재확인 상태로 보여주기 위해서다. 제출 API는 현재 소유 여행 안의 stop만 받으며 삭제된 구간을 재생성하지 않는다. trip/owner/job/run에는 FK와 서버 소유권 검증이 유지된다.

## 이관 전 읽기 전용 조사

```sh
.venv/bin/python scripts/discovery_context_inventory.py --database /absolute/private/database.sqlite3
# DATABASE_URL을 안전하게 주입한 운영 환경에서만 실행. URL을 명령줄 값으로 쓰지 않는다.
.venv/bin/python scripts/discovery_context_inventory.py --postgres-url-env DATABASE_URL --schema travel
```

SQLite mode=ro, PostgreSQL READ ONLY transaction으로 version/행 수/미배정 소유자 수만 출력한다. owner 미확인 자료는 임의 사용자에게 배정하지 않는다. schema10/11 이외 버전이나 unassigned_trips>0이면 배포 전에 조사한다. 실행 중 요청의 snapshot을 재작성하는 데이터 이관은 없다.

## 백업과 배포 순서

1. 현재 운영 image/schema에 맞는 코드로 백업한다. 새 Database 클래스를 미리 생성하면 migration이 실행되므로 preflight에는 직접 readonly 연결/위 조사 도구를 사용한다.
2. 운영용 환경 변수만 사용한다. 개발 루트 `.env` 로드를 끄고 DATABASE_URL TLS verify-full/검증 CA를 유지한다. `BACKUP_ENCRYPTION_KEY`는 secret으로 주입한다.
3. 기존 `python -m src.storage.transfer snapshot --output /private/pre-migration.enc`, `checkpoint --output /private/latest-checkpoint.enc` 경로로 암호화 cloud snapshot/삭제 checkpoint를 만든다. 실행 코드는 배포 전 schema10 revision이어야 한다.
4. `python -m src.storage.transfer restore-local --archive /private/pre-migration.enc --checkpoint /private/latest-checkpoint.enc --destination /private/isolated-restore`로 빈 격리 경로에 복원한다. 최신 checkpoint가 너무 오래됐다면 checkpoint를 다시 받는다. integrity/owner/삭제 scrub/기존 자료 수/원문 manifest를 검증한다. RESTORE_PENDING을 무조건 지우지 않는다.
5. feature branch를 기존 Render Free 서비스에 수동 배포한다. 한 worker/dispatcher를 유지한다. schema11과 health/ready, 인증 없는 개인 API401, 로그인 후 저장 자료 읽기, 새 shell을 확인한다.
6. 새 context가 아직0개라도 정상이다. 기존 사용자 자료는 사용자의 다음 행동 전에 억지로 변환하지 않는다.

## 실패와 복구

- DDL 실패: 전체 transaction rollback. health 실패 상태에서 원인 조사/전진 수정한다. 원문이나 예약을 지우고 재시도하지 않는다.
- UI 문제: schema11과 호환되는 코드로 전진 수정한다. public shell만 이전 기능으로 되돌려도 개인 인증/DB 계약은 유지한다.
- schema10 image로 단순 Rollback은 지원하지 않는다. 새 schema를 감지해 기동을 거부할 수 있다. PRAGMA/schema_version 숫자만 낮추거나 두 새 테이블을 운영에서 삭제하지 않는다.
- 불가피한 DB 복원은 쓰기/외부 작업을 차단하고 현재본도 백업한 뒤 별도 대상에 수행한다. 기존 full archive + **복원 시점 최신 삭제 checkpoint**를 적용하고 삭제·revocation·세션을 재검증한다. 이전 백업 이후 생성/수정 자료의 손실 범위를 먼저 확인한다.
- cloud 재주입은 기존 `src.storage.transfer import-sqlite` dry-run으로 manifest와 대상 빈 상태/설정을 검사한 뒤 검증된 별도 대상으로만 적용한다. 현재 live DB에 덮어쓰는 일반 명령으로 취급하지 않는다. 검증 전 복원본에서 유료 호출·개인 요청을 허용하지 않는다.

2026-10-06 검증: schema10→11 SQLite/격리 PostgreSQL 계약 통과. 실제 운영 schema10 snapshot+checkpoint→격리 SQLite 복원/무결성 통과. 운영 Supabase 전체 재주입은 실시하지 않았다. [상세 결과](reports/stage1-validation.md).
