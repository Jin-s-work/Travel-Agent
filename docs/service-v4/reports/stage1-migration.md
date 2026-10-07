# 1단계 schema 13 이관과 복구

2026-10-07 · 운영 실행과 분리된 코드/합성 검증 절차

## 변경 범위

SQLite `PRAGMA user_version` 및 PostgreSQL `schema_version.version`: 12 → 13.
기존 사용자·여행·예약·교정값을 다시 쓰거나 임의 소유자에게 배정하지 않는다.

| 추가 테이블 | 목적 | 수명/접근 |
| --- | --- | --- |
| workspace_drafts | 여행별 입력, 선택, 화면 맥락 | 사용자+여행+세션, 7일 TTL, 낙관적 version |
| itinerary_generation_drafts | 최초 일정의 비활성 미리보기 | 여행 소유자, 완성 뒤 10분 적용 기한 |
| maintenance_status | 현재 유지보수 소유자와 마지막 실행 | 운영 요약, 원문/키/개인 좌표 없음 |
| storage_deletion_receipts | 공정한 삭제 재확인과 늦은 업로드 차단 | 서버 전용, 원문 본문 없음 |

SQLite는 schema 추가를 한 트랜잭션으로 적용한다. PostgreSQL은 기존 WRITE → MIGRATION advisory lock 순서와 schema 접근 회수/RLS를 유지한다. 새로 시작하는 서비스는 DB 이관에 실패하면 초기화가 실패하며 개인 요청을 받지 않는다.

## 실행 전 dry-run

운영 서버·운영 DB에서 downgrade를 시험하지 않는다. 테스트는 임시 DB에 기존 schema 12를 재현한 뒤 13으로 재개하고 이전 여행 행의 불변과 재시작의 멱등성을 검사한다.

```sh
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest tests/test_stage1_migration.py -q -p no:cacheprovider
```

PostgreSQL은 `TRAVEL_TEST_POSTGRES_DSN`의 호스트가 localhost/127.0.0.1일 때만 테스트 플러그인이 실행된다. 임의 schema를 만들고 테스트 종료 시 해당 schema만 정리한다. 실제 배포 비밀번호를 넣지 않는다.

```sh
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  TRAVEL_TEST_POSTGRES_DSN='postgresql://postgres:going-disposable-test@127.0.0.1:55437/going_stage1' \
  .venv/bin/python -m pytest -p tests.postgres_plugin \
  tests/test_stage1_migration.py tests/test_stage1_workspace.py \
  tests/test_stage1_itinerary.py -q -p no:cacheprovider
```

의존 패키지의 온라인 전용 파일 때문에 macOS 읽기가 대기하는 경우 앱 테스트 실패로 합산하지 않는다. 파일이 로컬에 다시 내려온 뒤 새 프로세스로 실행한다. 불필요한 pytest plugin 자동 로딩은 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`로 끌 수 있다. PostgreSQL 플러그인은 `-p tests.postgres_plugin`로 명시한다.

## 복구와 rollback 구분

1. **기능 문제:** 새 일정 미리보기 진입을 중지해도 이미 적용한 일정·예약의 조회를 유지한다. 최초 미리보기는 활성 revision이 없으므로 기존 일정으로 잘못 선택되지 않는다.
2. **동일 schema 이미지 rollback:** schema 13을 이해하는 직전 검증 이미지로만 되돌린다. schema 12만 이해하는 구 이미지로 13 DB를 열면 버전 검사에서 중단된다.
3. **DB 복원:** 서비스 쓰기를 중지하고 기존 운영 백업 절차로 12 시점의 일관된 DB/원문 manifest를 격리 위치에 복원한다. 이후 삭제 tombstone을 별도 적용한 뒤 소유권·활성 세대·삭제 자료 비부활을 확인한다. 이관 이후 사용자의 신규 변경 손실 범위를 먼저 산정한다.
4. 새 테이블을 무조건 DROP하거나 schema 숫자만 낮추는 운영 downgrade는 제공하지 않는다. 삭제 영수증을 잃으면 늦은 업로드가 부활할 수 있다.

이 문서는 복구 절차이며 운영 백업·운영 rollback을 실행했다는 증거가 아니다. 실제 실행 결과는 `stage1-validation.md`를 따른다.
