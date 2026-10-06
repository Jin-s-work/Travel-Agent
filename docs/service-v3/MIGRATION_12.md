# V3 2단계 schema 12 이관과 복구

작성일: 2026-10-06. **구현·격리 이관·사전 백업 검증 완료, 운영 배포는 대기 중**이다. 이 문서는 운영 Supabase에 schema 12가 적용됐다는 증거가 아니다. 배포 결과는 [진행 기록](IMPLEMENTATION_STATUS.md)에 별도로 기록한다.

## 1. 변경 범위와 보존 계약

`src/accommodations/schema.py`는 다음 두 테이블과 세 인덱스를 추가한다.

| 대상 | 역할과 제약 |
| --- | --- |
| `trip_accommodations` | 서버 생성 숙소 ID, owner/trip/stop/booking 참조, 양의 version, 삭제 시각, 개인 입력·메모, 지점 상태·후보, 시설 timezone, 체크인/체크아웃 날짜·선택 시각, 출처·보관 계약 |
| `accommodation_resolutions` | job별 정규화된 비공개 지점 확인 영수증. job ID는 UNIQUE이며 숙소 입력 version을 보존한다. |
| `accommodations_owner_trip` | owner·trip·삭제 상태 범위 조회 |
| `accommodations_stop` | 여행·도시 구간·삭제 상태·숙박 날짜 조회 |
| `accommodation_resolution_owner` | 확인 영수증의 owner·trip 조회 |

숙박 날짜 범위는 `[checkin_date, checkout_date)`이다. 양쪽 날짜를 알 때 체크아웃은 체크인보다 뒤여야 한다. 날짜만 있는 자료에 임의 시각을 넣지 않는다. `stop_id`와 `booking_id`의 `ON DELETE SET NULL`은 삭제된 참조를 재생성하지 않으면서 숙소 자체의 원본 입력을 보존한다. API는 참조가 현재 사용자의 같은 여행에 속하는지 별도로 검사한다.

기존 여행·구간 ID, 예약 원문 추출값, 사용자 교정값, 현재 유효값, 과거 추천·일정 snapshot은 이관으로 재작성하지 않는다. 숙소를 예약에 연결하거나 지점을 선택해도 외부 예약 상태는 바뀌지 않는다. 확인 영수증은 일반 로그·공용 장소 테이블·예약 검색 인덱스에 넣지 않는다.

### 기존 숙소 이름 이관

`migrate_legacy(con)`은 삭제되지 않은 여행의 비어 있지 않은 `trip_stops.base_location`을 이름만 있는 숙소로 복사한다.

- `identity_state=unresolved`, 좌표·지점 ID 없음, `dates_confirmed=false`로 시작한다.
- 구간의 시작일과 종료일이 다르면 숙박 날짜의 **제안값**으로 저장한다. 같은 날짜인 구간에는 0박 범위를 만들지 않는다.
- 원래 `base_location`은 덮어쓰지 않는다. `source_json.kind=legacy_stop_label`과 날짜가 제안이라는 사실을 기록한다.
- `legacy_stop_id UNIQUE`와 기존 행 확인으로 반복 실행해도 중복 숙소를 만들지 않는다. 이미 삭제한 이관 숙소도 같은 구간으로 다시 만들지 않는다.
- 기존 수동 출발점 좌표는 사용자 입력 provenance를 유지한다. 호텔 이름으로 공급자 좌표·예약 확정을 추측하지 않는다.
- 소유자 미확인 자료는 조사 대상으로 남긴다. 임의 계정으로 배정하지 않는다.

숙소의 수정·삭제·지점 version·좌표 만료는 새 추천과 일정의 기준점 검증에 반영한다. 과거 snapshot을 현재값으로 덮어쓰지 않으며, 만료되거나 변경된 근거로 일정 편집을 적용하지 않는다.

## 2. SQLite와 PostgreSQL 적용 방식

SQLite는 schema 11에서 `BEGIN IMMEDIATE` → 두 테이블·인덱스 생성 → `migrate_legacy` → `PRAGMA user_version=12` → commit 순서다. 실패하면 같은 트랜잭션을 rollback한다. 기존 파일 migration lock도 유지한다.

PostgreSQL은 기존 advisory transaction lock 안에서 DDL, label-only 이관, `schema_version.version=12`를 함께 저장한다. 새 테이블에도 private schema, PUBLIC/브라우저 역할 권한 회수, RLS가 적용된다. 새 설치와 11→12 업그레이드를 모두 지원한다. migration 실패 시 startup/readiness를 정상으로 열지 않는다.

이관 자체는 지도·LLM·임베딩 제공자를 호출하지 않는다. SQLite와 PostgreSQL 모두 원본 메일·예약 삭제를 이관의 선행 작업으로 요구하지 않는다.

## 3. 쓰기 없는 사전 조사

저장소 루트에서 실행한다. 아래 `/absolute/private/...`는 운영자가 정한 **비공개 절대 경로**로 바꾼다. `DATABASE_URL` 등의 값은 명령줄이나 문서에 붙이지 않고 승인된 운영 secret 환경에 이미 주입되어 있어야 한다. 개발 루트 `.env` 자동 로드는 끈다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/accommodation_inventory.py --database /absolute/private/database.sqlite3
```

현재 운영인 Supabase는 다음 명령을 사용한다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/accommodation_inventory.py --postgres-url-env DATABASE_URL --schema travel
```

SQLite는 `mode=ro`, PostgreSQL은 `SET TRANSACTION READ ONLY`로 접속한다. 도구는 DB version, 이관 대상 수, 미확인 수, 소유자 미배정 수만 출력하며 숙소 이름·주소·좌표·메일·비밀값을 출력하지 않는다. `writes=0`, `provider_calls=0`을 확인한다.

`pending_legacy_labels`는 아직 이관하지 않은 이름의 수, `unresolved_after_label_migration`은 이관 후 예상 미확인 수다. 미확인 숙소는 정상적인 초기 상태다. 공급자나 지점 선택이 없는데 confirmed로 바꾸지 않는다.

**주의:** 애플리케이션의 `Database(...)` 생성은 migration을 실행할 수 있다. schema 11의 사전 조사·백업 전에 새 schema 12 코드로 일반 운영 CLI를 실행하지 않는다. 위 inventory 도구는 애플리케이션 DB 생성자를 사용하지 않는다.

## 4. 백업부터 배포까지의 순서

### 운영 환경과 비밀 준비

현재 서비스와 같은 운영 설정만 주입한다. Supabase session pooler, private schema/bucket, TLS `verify-full` 및 CA 검증을 유지한다. `BACKUP_ENCRYPTION_KEY`는 base64로 인코딩한 32바이트 키이며 비공개 secret 저장소에 보관한다. 키·DB URL·서버 키·백업 본문은 Git, 채팅, 일반 로그에 남기지 않는다. 백업 파일은 운영 디스크와 다른 장애 범위에, 복호화 키는 백업 파일과 분리해 보관한다.

아래 `python`은 승인된 가상환경 인터프리터다. **운영 DB가 schema 11인 동안에는 현재 운영 버전 `28269c9`의 보관된 코드 디렉터리에서 실행한다.** 이 코드의 DB 생성자가 schema 11을 이해한다. `.env`를 자동 로드하지 않도록 모든 예시에 `PYTHON_DOTENV_DISABLED=1`을 붙였다.

### 운영 쓰기·외부 호출 차단과 기존 자료 확인

```sh
PYTHON_DOTENV_DISABLED=1 python -m src.operations.cli control --mode read_only --external off --reason PRE_SCHEMA12_BACKUP
PYTHON_DOTENV_DISABLED=1 python -m src.operations.cli status
```

운영 제어 변경은 DB에 기록된다. 새 쓰기를 차단한 다음 진행 중인 작업이 정리됐는지 확인한다. 강제로 종료한 작업을 과금 취소로 해석하지 않는다. 이미 전송한 호출의 unknown 비용은 별도 확인 전까지 유지한다.

### 암호화 Supabase 백업과 독립 checkpoint

```sh
PYTHON_DOTENV_DISABLED=1 python -m src.storage.transfer snapshot --output /absolute/private/pre-schema12.enc
PYTHON_DOTENV_DISABLED=1 python -m src.storage.transfer checkpoint --output /absolute/private/latest-checkpoint.enc
```

`src.storage.transfer`가 현재 PostgreSQL/Supabase용 경로다. `src.operations.cli snapshot`은 로컬 SQLite용이므로 운영 Supabase 백업에 대신 사용하지 않는다.

Cloud snapshot의 데이터 읽기는 MVCC의 `REPEATABLE READ READ ONLY` 트랜잭션을 사용한다. 원문 object와 SQL을 휴대 가능한 암호화 archive로 묶는다. `checkpoint`에는 삭제·계정 회수·비용 상태 등이 담긴다. archive와 checkpoint가 별개이므로 오래된 archive를 복원할 때도 **복원 시점의 최신 checkpoint**를 다시 받아야 한다.

### 빈 격리 경로에 복원

```sh
PYTHON_DOTENV_DISABLED=1 python -m src.storage.transfer restore-local --archive /absolute/private/pre-schema12.enc --checkpoint /absolute/private/latest-checkpoint.enc --destination /absolute/private/isolated-schema11-restore
```

대상 디렉터리는 새 경로여야 한다. 같은 복호화 키를 secret 환경으로 제공한다. 현재 복원 구현의 checkpoint 기본 허용 나이는 600초이며, 오래된 checkpoint나 archive보다 먼저 만들어진 checkpoint는 거절한다. 시간을 임의로 바꾸어 검사를 우회하지 않고 현재 checkpoint를 다시 확보한다.

복원 결과는 `restored_closed_for_validation`이다. 세션을 무효화하고 외부 호출을 끈 상태로 남긴다. `RESTORE_PENDING.json` 또는 `RESTORE_INCOMPLETE.json`을 검사 없이 지우지 않는다. 다음 항목을 검증한다.

- SQLite `integrity_check=ok`, 외래키 위반 0, 소유자 불명 자료 0.
- 기존 여행·구간·예약 및 사용자 교정값이 보존됐는지 확인.
- archive 이후의 여행·문서·예약·숙소 tombstone과 계정 회수가 반영됐는지 확인.
- 삭제 숙소의 지점 후보·개인 입력·확인 영수증이 남거나 재생성되지 않는지 확인.
- 원문 파일 manifest와 현재 활성 참조를 확인. 검색은 재구축 필요 또는 복구 중으로 표시하되 SQL 조회를 유지.
- 현재 비용 예약·unknown 과금·외부 OFF를 확인. 복원 작업은 유료 API 호출 시험이 아니다.

schema 11 원본 복원을 검증한 뒤 **격리 복원본의 별도 사본**에 schema 12 이관 시험을 적용한다. 되돌릴 원본 archive를 수정하지 않는다. 복원 전후 값과 이관 후 unresolved 상태를 비교한다.

### 배포 승인된 기존 환경에 반영

사전 검증을 통과한 schema 12 코드만 기존 Render Free 서비스에 적용한다. 단일 인스턴스·uvicorn worker 1·dispatcher 1을 유지하며 추가 과금 계정을 만들지 않는다. 배포 직후 아래 항목이 확인되기 전에는 성공으로 기록하지 않는다.

1. 실제 실행 commit, schema 12, `/health/live`, `/health/ready` 응답.
2. 인증 없는 개인 API 차단, 기존 계정의 자료·원문 소유권.
3. 기존 자료 수와 예약 교정값 보존, label-only 숙소의 unresolved 상태.
4. 지도 제공자가 OFF인 경우 저장·조회 가능, 좌표 없는 숙소의 위치 미확인 표시, 실제 외부 호출 0.
5. 새로고침·서버 재시작 후 자료 유지, 숙소 변경 후 이전 추천·일정의 재확인 표시.

현재 문서 작성 시 이 운영 배포 단계는 대기 상태다.

## 5. 기능 OFF, 이미지 변경, DB 복원은 다른 작업

| 조치 | 무엇을 바꾸는가 | 데이터·비용 계약 |
| --- | --- | --- |
| 지도 제공자 OFF | `LOCATION_PROVIDER_CONFIG`를 비워 기본 disabled 제공자를 사용한다. 키만 있다고 활성화되지 않는다. 필요하면 `ZERO_SPEND=1`과 기존 운영 `--external off`로 전송도 차단한다. | 기존 숙소·여행은 보존한다. 지점 확인·실제 경로는 unavailable/unknown이며 직선거리는 사용 가능한 좌표에서만 계산한다. |
| 쓰기 임시 차단 | 기존 운영 CLI의 `--mode read_only --external off` | 저장 자료를 읽으면서 신규 변경·외부 작업을 차단한다. 새 숙소 UI만 끄는 별도 미구현 환경변수를 가정하지 않는다. |
| 호환 이미지로 교체 | schema 12를 이해하는 수정 이미지 또는 검증된 호환 이미지 | DB를 되감지 않는다. UI 문제는 schema 12 호환 코드를 통한 전진 수정을 우선한다. |
| DB 복원 | archive와 최신 checkpoint로 별도 대상에 상태를 복구한다. | archive 이후 생성·수정의 손실 범위를 확인한다. 최신 삭제·회수는 반드시 재적용하며 비용 ledger를 삭제해 호출을 재실행하지 않는다. |

**schema 11 이미지로 단순 rollback하면 schema 12 DB를 지원하지 않아 기동을 거부할 수 있다.** `PRAGMA user_version`이나 `schema_version` 숫자만 낮추거나 운영의 새 테이블을 삭제해 호환되는 것처럼 만들지 않는다. 구버전 이미지 사용이 불가피하면 대응하는 DB 복원·최신 삭제 checkpoint 반영·접근 검증을 별도 복구 작업으로 수행해야 한다.

지도 기능 OFF는 이미 시작한 외부 요청을 취소하거나 청구를 취소한다는 뜻이 아니다. `--external on` 또는 정상 운영 모드 복귀는 복구 완료 후 별도 판단이며 위 명령에 자동으로 포함하지 않았다.

## 6. Supabase 재주입이 필요할 때

현재 운영 DB를 덮어쓰는 명령은 제공하지 않는다. 먼저 격리 복원본을 검증하고 현재 코드가 지원하는 schema 12 복원본에서 dry-run한다.

```sh
PYTHON_DOTENV_DISABLED=1 python -m src.storage.transfer import-sqlite --source /absolute/private/validated-schema12-restore/database.sqlite3 --documents-root /absolute/private/validated-schema12-restore/documents
```

이 명령에는 `--apply`가 없으므로 원본 무결성·문서 hash·소유 자료 수만 검사한다. 현재 코드의 import 검사에 schema 11 원본을 그대로 넣지 않는다. schema 12로 검증 이관한 복원본을 사용한다.

실제 재주입은 원본 writer를 정지하고, 운영자가 승인한 **비어 있는 별도 대상 schema/bucket 설정**을 사용한 경우에만 다음 계약으로 실행한다.

```sh
PYTHON_DOTENV_DISABLED=1 python -m src.storage.transfer import-sqlite --source /absolute/private/validated-schema12-restore/database.sqlite3 --documents-root /absolute/private/validated-schema12-restore/documents --apply --offline
```

대상에 이미 users가 있으면 import는 거절한다. 명령의 `--offline`은 source writer 정지를 확인하는 옵션이며 실행만으로 다른 서버를 자동 정지시키지 않는다. 재주입 결과도 읽기 전용·외부 OFF·세션 회수·검색 재구축 필요 상태다. 대상 교체와 정상 운영 재개는 별도 검증 후 시행한다. 이번 작업에서 운영 Supabase 전체 재주입은 실행하지 않았다.

## 7. 실제 확보한 증거와 한계

### 2026-10-06 운영 사전 백업

schema 11 운영 코드 `28269c9`를 보관한 checkout과 운영 읽기 전용 연결을 사용했다. 비밀 파일의 본문·위치·암호화 키는 이 문서에 포함하지 않는다.

| 항목 | 실제 결과 |
| --- | --- |
| 암호화 full archive | 788,524 bytes |
| 독립 checkpoint | 1,008 bytes |
| 백업 시간 | 13.12초 |
| 격리 복원 시간 | 0.04초 |
| 복원 무결성 | `integrity_check=ok`, 외래키 위반 0 |
| 소유자 검사 | 소유자 불명 자료 0 |
| 복원 세션·운영 상태 | 세션 0, 읽기 전용, 외부 호출 OFF |
| 복원 전후 행 수 | users 1, trips 2, trip_stops 2, bookings 1, source_documents 0, recommendation_runs 0, itineraries 0 — 모두 동일 |
| 실제 데이터의 tombstone | 0 |

실제 운영 archive에는 삭제 tombstone이 없었다. 따라서 이 결과를 **운영의 삭제 자료 복원 시험 성공**으로 표현하지 않는다. 운영 자료 백업·격리 복원·무결성·소유자·기존 예약 보존을 확인한 결과다. 0.04초는 이 작은 자료량의 로컬 격리 복원 측정이며 전체 장애 대응·검수·재배포의 RTO가 아니다. 자동 백업 주기나 RPO를 보장하지 않는다.

운영 사전 inventory 결과는 `eligible_legacy_labels=1`, `pending_legacy_labels=1`, `unresolved_after_label_migration=1`, `unowned_not_assigned=0`, `writes=0`, `provider_calls=0`이다. 숙소 이름 한 건을 미확인 숙소로 이관할 대상으로 확인했으며 실제 지점 확인 완료를 뜻하지 않는다.

### 합성 환경의 이관·삭제·복구 검증

- `tests/test_stage2_postgres_migration.py`: 실제 schema 11 구조에서 PostgreSQL 11→12 실행 **1 passed**. 기존 소유자·구간·예약 교정·원본 값 동일, RLS·인덱스·반복 이관 검증.
- `tests/test_accommodations_api.py`: SQLite 11→12, label-only 반복 이관, 삭제한 이관 숙소 재생성 차단, 개인 제공자 영수증 제거, 오래된 archive 이후 숙소 삭제 checkpoint를 격리 복원에 적용하는 시험.
- 같은 API 시험의 작업 복구 항목: 공급자 응답 후 checkpoint 재사용, 후보 활성화 후 job 완료 전 중단 복구, lease fencing, 삭제와 완료 경쟁, 응답 유실 과금 unknown.
- `tests/test_stage2_itinerary.py`: 숙소 수정·삭제에 따른 일정 stale/preview·apply·undo 충돌 보호. 과거 snapshot과 고정 예약을 덮어쓰지 않음.
- 위 합성 시험은 실제 지도 지점 식별 정확도나 라이브 도보시간 검증의 증거가 아니다.

재실행 예시:

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_accommodations_api.py tests/test_accommodations_origin.py tests/test_stage2_itinerary.py tests/test_operations.py -q
```

PostgreSQL 시험에는 기존 `tests.postgres_plugin`과 임시 loopback PostgreSQL을 사용한다. `TRAVEL_TEST_POSTGRES_DSN`은 운영 DB가 아닌 폐기 가능한 테스트 DSN이어야 한다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -p tests.postgres_plugin tests/test_accommodations_api.py tests/test_stage2_itinerary.py tests/test_itinerary_api.py -q
```

전체 실제 실행 결과·기기 검증·배포 상태는 [진행 기록](IMPLEMENTATION_STATUS.md)의 마지막 실행 기록을 기준으로 한다. 이 문서는 운영 배포 대기 상태에서 작성했으며 배포 성공·라이브 지도 검증·운영 DB 재주입을 주장하지 않는다.
