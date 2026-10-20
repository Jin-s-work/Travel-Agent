# 01단계 실행·인증·이관·복구 안내

이 문서는 구현된 01단계의 실행 방법이다. 계정·실제 메일을 자동 이관하지 않으며, 실제 외부 로그인이 성공했다고 대신 주장하지 않는다. 구현·테스트 결과는 [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)에 별도로 기록한다.

## 1. 실행 환경과 구성

Python 3.13과 기존 `.venv`를 사용한다. 3.14에서는 기존 Chroma 의존성 호환 문제가 있으므로 검증 전 전환하지 않는다.

```bash
cd /Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --no-access-log
```

운영과 개발 모두 웹앱과 API는 같은 출처를 사용한다. SQLite와 Chroma를 함께 사용하는 01단계는 **프로세스 한 개·uvicorn worker 한 개**로 실행한다. `--workers`를 늘려 작업 처리량을 높이지 않는다.

`.env.example`을 참고해 다음 값을 `.env` 또는 서버의 비밀 설정에 넣는다. 이미 있는 `.env`를 덮어쓰지 않는다.

| 설정 | 로컬 개발 예 | 의미 |
| --- | --- | --- |
| APP_ENV | development | 로컬 HTTP 쿠키만 별도 허용. 운영은 production |
| PUBLIC_BASE_URL | http://localhost:8000 | 경로 없는 정확한 Origin. 브라우저 접속 주소와 일치 |
| SESSION_SECRET | 충분히 긴 새 난수 | 최소 32자. OIDC 임시 상태 쿠키 서명 |
| OIDC_CLIENT_ID | 제공자에서 받은 값 | 브라우저·공개 문서에 비밀을 넣지 않음 |
| OIDC_CLIENT_SECRET | 제공자에서 받은 값 | 서버 전용 |
| OIDC_SERVER_METADATA_URL | https://accounts.google.com/.well-known/openid-configuration | 기본 제공자는 Google OIDC |
| DATABASE_PATH | data/service.sqlite3 | 구조화 사실과 세션의 기준 저장소 |
| DOCUMENTS_DIR | data/private-documents | 서버 ID로 저장하는 비공개 원문 |
| VECTORS_DIR | data/private-vectors | 여행별 검색 저장소 |
| OPENAI_API_KEY | 별도 비밀 설정 | 실제 추출·임베딩·답변 실행에 사용 |

운영의 `PUBLIC_BASE_URL`은 HTTPS여야 한다. 운영 쿠키는 `__Host-session`, Secure, HttpOnly, SameSite=Lax, Path=/이며 Domain을 지정하지 않는다. 로컬 개발 쿠키 이름은 `travel_dev_session`이다. 운영 설정을 개발 모드로 바꿔 로그인 검사를 피하지 않는다.

프록시·호스팅에서도 인증 callback query와 요청 body를 로그에 남기지 않도록 설정한다. 예제 서버 명령은 OIDC 일회성 코드가 access log에 남지 않도록 `--no-access-log`를 사용한다.

개인 원문·SQLite·검색 디렉터리를 `web/` 안에 두지 않는다. 공개 정적 경로에는 앱 셸만 둔다. 서비스 워커도 개인 API 응답을 저장하지 않는다.

## 2. OIDC 제공자 등록과 로그인

2026-10-08부터 서비스 초대 코드를 사용하지 않는다. Authlib가 OIDC state·nonce·PKCE와 ID token을 검증하고, 이메일 인증이 완료된 계정의 첫 로그인 시 일반 사용자(`member`)를 만든다. 기존 사용자는 제공자와 `sub`로 식별하므로 다른 기기에서도 같은 Google 계정을 사용한다. 이메일이 같다는 이유만으로 서로 다른 제공자·subject의 계정을 합치지 않는다.

1. OIDC 제공자 콘솔에서 웹 애플리케이션용 client를 만든다.
2. 실제 사용 Origin을 설정하고 callback을 정확히 등록한다.
   - 개발: `http://localhost:8000/api/v2/auth/callback`
   - 운영: `https://실제서비스도메인/api/v2/auth/callback`
3. 제공자 자체의 테스트 사용자/조직 제한은 서비스 초대 코드와 별개다. Google 측에서 접근을 거절하면 OAuth 대상 사용자 설정을 확인한다.
4. client ID, secret, metadata URL을 설정하고 서버를 재시작한다.
5. 로그인 화면에서 **Google 계정으로 로그인**을 누른다. 별도 초대 발급·입력은 필요 없다.

사용자·여행 소유권과 관리자 권한은 기존대로 분리한다. 비활성화된 계정의 로그인은 거절한다. 초대 테이블과 기존 유지보수 CLI는 이전 자료·복구 호환용으로 남아 있지만 현재 로그인 허용 여부를 결정하지 않는다. 새 가입자의 역할은 `member`이며 Google claims의 role/owner 값을 권한으로 사용하지 않는다.

```bash
# 이미 발급된 세션까지 회수하고 사용자 비활성화
.venv/bin/python -m src.foundation.cli disable-user --user-id 사용자_ID
```

`GET /api/v2/session`으로 현재 인증·설정 상태를 확인할 수 있다. 신규·기존 모든 개인 경로는 인증이 필요하며, 과거 공용 API는 인증 후에도 410으로 폐기된다. 인증이 미설정이면 개인 자료는 닫힌 상태다. 외부 계정이 준비되지 않은 테스트는 합성 identity claims 및 로컬 합성 OIDC 제공자로 검증하며, 운영 우회 계정이나 고정 비밀번호를 제공하지 않는다.

## 3. 사용 흐름과 01단계 제한

로그인 → 여행 생성 → `.txt/.eml` 예약 메일 업로드 → 파일별 처리 상태 확인 → 예약 상세·교정 → 날짜별 질문 → 원문 다운로드 → 새로고침·여행 전환 순서로 확인한다.

- 여행은 메일 없이 만들 수 있고 수동 예약도 추가할 수 있다.
- 원문 추출값과 교정값은 분리된다. 재추출 시 교정은 유지되고 충돌은 검토 대상으로 남는다.
- 한 메일의 여러 예약은 각각 저장된다. 한 예약을 삭제해도 다른 예약과 원문을 자동 삭제하지 않는다.
- 날짜 질문은 SQL의 현재 예약과 이벤트를 조회한다. 의미 검색의 top-k로 하루 전체 예약을 제한하지 않는다.
- 시각의 timezone이 없으면 instant는 미확인이다. 날짜만 있는 자료에 자정을 만들지 않는다.
- 숙박 날짜 목록은 숙박 기간과 체크아웃 날짜를 보여준다. 체크아웃은 실제 시각이 확인된 이벤트와 구분한다.
- 저장된 SQL 작업 영수증은 새로고침 후 조회할 수 있다. 그러나 서버 중단 후 자동 재개하는 내구성 worker·lease·checkpoint는 02단계 범위다.
- 작업 중 서버가 중단되면 영수증이 running으로 남을 수 있다. 원본·현재 예약을 확인하고 명시적인 재처리를 사용한다. 작업이 자동 복구됐다고 표시하지 않는다.
- 공급자 키가 없거나 추출·임베딩이 실패하면 해당 파일은 실패 상태다. 기존 활성 예약을 먼저 지우지 않는다.

## 4. 기존 공용 메일의 읽기 전용 조사

먼저 서버를 중단하지 않아도 수행 가능한 inventory를 실행한다. 이 명령은 파일을 읽고 해시·크기·중복을 계산하지만 DB를 만들거나 모델·검색 API를 호출하지 않는다.

```bash
.venv/bin/python -m src.foundation.cli legacy-inventory \
  --source-root /기존메일디렉터리 \
  --output /비공개경로/legacy-inventory.json
```

결과에는 다음이 포함된다.

- `.txt/.eml` 파일 수와 상대 경로, 바이트 수, SHA-256
- 내용이 같은 파일 그룹, 미지원 파일과 symlink 목록
- 소유자 미확정 수
- `booking_count: null`, `parse_status: not_run`

파일 수와 예약 수를 같다고 가정하지 않는다. 한 메일에 여러 예약이 있을 수 있기 때문이다. 메일 본문·예약번호를 보고서에 출력하지 않는다. symlink를 따라 외부 파일을 수집하지 않는다.

## 5. 백업 후 명시적인 소유자·여행 매핑 이관

API와 다른 쓰기 명령을 먼저 중단한다. 아래 `--offline`은 운영자가 중단 사실을 확인했다는 명시적 입력이며 자동 프로세스 종료 기능은 아니다.

```bash
.venv/bin/python -m src.foundation.cli backup \
  --offline --destination /안전한별도위치/foundation-before-import
```

백업 명령은 SQLite backup API로 DB를 복사하고 비공개 원문·검색 디렉터리와 체크섬 manifest를 만든다. 새 경로만 허용하고 기존 백업을 덮어쓰지 않는다. 원본 저장소 내부로 백업해 재귀 복사를 만들지 않는다. 환경변수·`.env`는 포함하지 않는다. 실패한 백업에는 `FAILED.json`을 남기고 이관·복원에 사용할 수 없게 한다.

원본 파일과 inventory를 확인한 소유자가 해당 여행을 직접 지정한다. 사용자 ID는 로그인한 `/api/v2/session`의 `user.id`, 여행 ID는 해당 여행 응답의 `id`다. 아래 JSON은 예시이며 실제 ID와 inventory의 SHA-256으로 교체한다.

```json
{
  "schema_version": 1,
  "run_id": "legacy-2026-10-01-first",
  "source_root": "/기존메일디렉터리",
  "files": [
    {
      "relative_path": "reservation.eml",
      "sha256": "inventory에 기록된 SHA-256",
      "user_id": "실제사용자ID",
      "trip_id": "실제여행ID"
    }
  ]
}
```

```bash
# 원문을 비공개 여행 저장소에 복사하고 기록만 생성: 모델 호출 없음
.venv/bin/python -m src.foundation.cli legacy-import \
  --offline --mapping /비공개경로/mapping.json \
  --backup /안전한별도위치/foundation-before-import

# 같은 매핑과 run_id로 명시적인 추출·임베딩 실행: 외부 API 사용·과금 가능
.venv/bin/python -m src.foundation.cli legacy-import \
  --offline --mapping /비공개경로/mapping.json \
  --backup /안전한별도위치/foundation-before-import --process
```

이관 전에 전체 매핑의 소유권, 해시, 경로, 크기와 형식을 검사한다. 소유자와 여행이 맞지 않거나 조사 이후 파일이 바뀌면 중단한다. 매핑하지 않은 파일을 첫 로그인 사용자에게 넣지 않는다.

동일 여행의 내용 중복은 재사용한다. 같은 run ID는 최초 매핑에 결합되며 다른 매핑으로 바꿀 수 없다. 실행 상태는 비공개 `DOCUMENTS_DIR/.migration-runs/`와 SQL 영수증에 기록된다. 같은 매핑으로 성공한 처리를 재실행해 중복 예약을 만들거나 다시 모델을 호출하지 않는다.

`imported_unprocessed`는 원문 저장만 된 상태이며 예약 추출 성공이 아니다. `--process` 후 파일별 오류·검토 필요 상태를 확인한다. 실패·미처리 항목은 같은 매핑으로 재실행할 수 있고 기존 원본은 그대로 보존한다. 소유자 배정을 바꾸려면 이전 이관을 검토하고 새 run ID를 사용한다. 조용히 이전 소유권을 덮어쓰지 않는다.

기존 공용 `CHROMA_DIR`은 새 사용자에게 통째로 붙이지 않는다. 이관된 각 여행의 원문으로 새 검색 데이터를 만든다.

## 6. 복원과 실제 복구 확인

복원은 현재 디렉터리를 덮어쓰지 않고 새 디렉터리에 생성한다. API와 쓰기 작업을 중단한 상태에서 실행한다.

```bash
.venv/bin/python -m src.foundation.cli restore \
  --offline \
  --backup /안전한별도위치/foundation-before-import \
  --destination /새로운복원위치/foundation-restored \
  --deletions-from-db /현재저장위치/service.sqlite3
```

명령은 체크섬·파일 목록·SQLite 무결성을 검사하고 다음을 수행한다.

- 원문 경로를 새 `documents/` 위치로 변경한다.
- 모든 복원된 세션을 지우고 session epoch를 증가시킨다.
- 미사용 과거 초대 기록을 회수한다. 현재 로그인에는 초대 발급이 필요 없다.
- 현재 DB가 제공되면 이후의 삭제 tombstone과 사용자 비활성 상태도 반영한다.
- 여행·문서·예약의 삭제 자료가 다시 활성 조회에 등장하지 않게 한다.

최신 DB가 없으면 `deletion_history: backup_only_requires_review`로 반환한다. 백업 이후의 삭제·권한 회수를 자동으로 알 수 없으므로 복원본을 운영에 연결하기 전에 이를 확인한다. 백업 자체의 오래된 세션을 재사용하지 않는다.

복원 도중 오류가 나면 새 디렉터리에 `RESTORE_INCOMPLETE.json`이 남는다. 이 디렉터리를 운영 저장소로 연결하지 않는다.

성공한 복원 출력의 `database_path`, `documents_dir`, `vectors_dir`로 **격리된 검증 서버**를 실행한다. 로그인, 본인 여행 조회, 타인 자료 404, 원문 다운로드, 현재 교정값, 삭제 자료 부재를 확인한 뒤 설정을 전환한다. 코드 rollback은 호환 DB 백업과 함께 검토하고 새 사용자 자료를 지우는 down migration으로 대체하지 않는다.

백업은 개인 원문을 포함한다. 실제 보관 위치의 접근 권한·암호화·보관 기간은 운영자가 관리한다. 01단계 CLI는 자동 백업 일정이나 원격 백업 업로드를 설정하지 않는다.

## 7. 검증과 이번 단계의 경계

```bash
.venv/bin/python -m pytest tests/test_foundation_storage.py -q
.venv/bin/python -m src.foundation.cli --help
```

합성 테스트는 임시 SQLite·원문·fake vector를 사용한다. 두 사용자×두 여행, 복수 예약, 사용자 교정, 모호한 재추출, 동시 수정, 삭제·늦은 활성화, 날짜 조회, read-only inventory, 명시적 매핑, 백업 checksum, 새 위치 복원과 최신 삭제 반영을 검증한다.

실제 사용자 메일 조사·이관, 외부 OIDC 로그인, 실제 유료 추출·임베딩, 운영 서버 배포는 각각 실행 결과가 있을 때만 검증 완료로 기록한다. 내구성 큐·자동 복구·비용 원자 예약·검색 세대 전환과 정기 백업 운영은 [02단계](prompts/02-reliability.md)의 후속 범위다.
