# 02 작업 내구성 실행·복구 안내

이 단계는 한 대의 서버, 한 개의 uvicorn 프로세스, SQLite WAL, 로컬 영구 Chroma/파일 디스크를 전제로 한다. 01의 Authlib 초대 로그인·소유권·예약 교정 모델을 재사용한다. 실제 외부 계정과 공급자 과금 검증은 별도이며, 03 리뷰 수집기를 실행하지 않는다.

## 시작과 설정

```bash
cd /Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag
.venv/bin/python -m pytest tests -q
node --test tests/test_service_worker.cjs
.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

`.env.example`의 인증·영구 경로를 설정한다. 실제 단가와 한도는 [BUDGET_RUNBOOK.md](BUDGET_RUNBOOK.md), [설정 템플릿](examples/pricing-policy.template.json)을 따른다. `PRICING_CONFIG`가 없거나 유효하지 않으면 외부 요청 전에 중단한다. 단가·무료 크레딧을 추측하지 않는다. 사용자·전역 일/월 상한은 통화별 micro 단위 정수이며 기간은 UTC다.

`DATABASE_PATH`, `DOCUMENTS_DIR`, `VECTORS_DIR`, DB 옆 `private-job-artifacts`는 공개 web 디렉터리 밖의 영구 디스크에 둔다. 네 경로를 함께 백업한다. 기본 job lease 90초, heartbeat 20초, 실행 기한 900초, 최대 시도 3회다. 공급자 SDK는 내부 재시도 0·45초 제한을 사용한다. dispatcher 작업 실행과 실제 공급자 호출 동시성은 각각 기본 1이다. HTTP의 SQL 읽기와 상태 확인은 작업 스레드를 기다리지 않는다.

`GET /api/health`는 공개 생존 확인이다. 로그인한 `GET /api/v2/readiness`는 스키마, 경로 쓰기 가능 여부, dispatcher heartbeat, 본인 검색 세대 상태, 외부 비용 설정을 반환한다. 일반 회원에게 전체 사용자의 작업 목록·원문·provider 응답을 제공하지 않는다. 설정 화면의 사용량은 예상·정산·미확정을 구별한다.

## 실제 이용 흐름

1. 여행을 선택하고 `.txt/.eml`을 올리면 202와 `job_id`, `status_url`, `events_url`, 파일별 접수/중복/거절 내역을 받는다. 긴 추출은 응답 이후 dispatcher가 처리한다.
2. 여행 작업 패널에서 실행 단계와 처리한 파일 개수를 본다. 단계의 전체 수를 모르면 백분율을 만들지 않는다.
3. 새로고침하거나 여행을 바꿨다가 돌아오면 `GET /trips/{tid}/jobs`에서 상태를 복원한다. 서버가 재시작되면 이전 lease가 만료된 후 체크포인트부터 재개한다.
4. SSE는 저장된 sequence를 `Last-Event-ID` 다음부터 재생한다. 연결이 반복해서 끊기면 snapshot polling으로 전환한다. 상태를 보기 위해 업로드 요청을 다시 보내지 않는다.
5. 부분 성공이면 성공한 예약은 바로 보존된다. 실패 항목만 재시도하며 완료된 추출·임베딩 중 호환되는 저장 결과는 재사용한다. 같은 부모 실패 작업의 중복 재시도는 같은 후속 작업을 가리킨다.
6. 취소는 요청과 완료를 구별한다. 이미 공급자에 전달된 요청은 끝날 수 있고 과금은 별도로 정산된다. 브라우저를 닫거나 SSE가 끊겼다는 이유로 취소하지 않는다.
7. 예산 소진·미확정 과금이면 무조건 다시 실행하지 않는다. 예약 목록과 날짜 전체 질문은 계속 사용할 수 있다. 일반 검색이 고장 나면 `SEARCH_REBUILDING`을 표시하고 중복 없는 복구 작업을 만든다.

업로드/재분석/수동 재시도는 `Idempotency-Key`를 보낸다. 서버는 인증한 실행자, scope, operation, canonical 입력 hash에 묶는다. 같은 key에 다른 입력은 409다. 동일 업로드가 완료되어 여행 버전이 증가한 뒤에도 같은 원래 요청은 원래 job을 반환한다. 클라이언트는 전송 결과가 불명확하면 같은 입력의 key를 메모리에 보존한다. 개인 데이터와 세션은 localStorage에 저장하지 않는다.

## 저장·복구 경계

- `jobs`, `job_events`, `idempotency_keys`, `dispatcher_leases`, `trip_index_writers`는 실행 소유권과 진행 상태를 SQLite에 보존한다. `queued → running → succeeded/partial/failed/cancelled`; 안전한 재시도와 lease 회수는 running에서 queued로 돌아간다.
- 개인 작업은 active user/session·trip 소유권이 필요하다. 관리자 공용 조사에는 `admin_research`와 별도 non-null scope가 필요하다. 이번 단계에 공용 조사 실행기·크롤러를 열지 않았으며 임의 개인 여행을 공용 작업에 사용하지 않는다.
- 원문은 서버 UUID 임시 파일에 저장하고 fsync·rename·SQL 참조 등록을 수행한다. 같은 DB 쓰기 잠금으로 삭제·고아 정리와의 경쟁을 막는다. 원문 저장 직후 crash는 참조 없는 파일을 남길 수 있다.
- 기동 정리는 현재 dispatcher leader만 수행한다. 기본 24시간이 지난 서버 ID 형식의 고아 원문·임시 파일만 삭제하며 모든 SQL 참조와 실행 중 작업을 보호한다. 정상 JSON provider 영수증/체크포인트를 나이만으로 버리지 않는다.
- 추출·임베딩 응답은 비공개 영수증과 체크포인트로 남긴다. 모델·분할/추출 규칙이 변경된 체크포인트는 `CHECKPOINT_INCOMPATIBLE`로 멈춘다. 변경된 설정을 적용하려면 별도 새 분석 요청을 명시적으로 만든다.
- 새 여행 collection은 staged→ready 검증 후에만 활성화한다. 문서 예약 활성화와 SQL 포인터를 한 트랜잭션에서 바꾼다. Chroma 쓰기가 SQL rollback에 포함된다고 가정하지 않는다.
- 오래된 worker는 SQL fencing을 통과할 수 없다. 이전 fence가 쓰던 staged collection은 새 worker의 컬렉션으로 재사용하지 않고 검증된 청크만 새 이름에 복사한다. ready collection은 변경하지 않는다.
- reader는 정확한 활성 collection과 SQL 참조를 함께 잡고 답변을 마칠 때 해제한다. 그전에는 이전 세대를 회수하지 않는다. 답변의 예약 사실·시간은 현재 SQL 교정값으로 재조립한다.
- 삭제는 tombstone과 접근 차단이 우선이다. 일반 job/SSE는 404가 되고 별도 삭제 영수증으로 최소 상태만 확인한다. 완료 뒤 돌아온 공급자 응답은 비용만 정산하고 예약/원문/인덱스를 되살리지 않는다. cleanup job은 당시 승인된 삭제의 시스템 후속 작업이므로 로그아웃 뒤에도 정리를 계속할 수 있다.

## 장애별 운영 행동

| 관측 | 처리 |
| --- | --- |
| running 상태에서 서버 종료 | 서버 재시작. lease 만료와 checkpoint 복구를 기다린다. 같은 업로드를 새 key로 반복하지 않는다. |
| ready 이후 중단 | 이전 active는 유효하다. 재실행자가 권한·버전·fence를 다시 확인한 후 전환한다. |
| 포인터 전환 후 완료 이벤트 누락 | 문서/인덱스 active 포인터로 성공을 복원한다. SSE 마지막 이벤트를 못 받았어도 GET snapshot을 읽는다. |
| 세션 만료/회수 | 작업이 결과를 활성화하지 못한다. 로그인 후 저장된 상태를 확인하고 필요한 파일만 새 작업으로 실행한다. |
| 예산 소진 | 저장된 예약을 사용한다. 운영자가 단가/상한과 사용량을 확인하기 전 반복하지 않는다. |
| unknown/pending 과금 | 공급자 확인 자료로 관리자 정산을 수행한다. 에러라는 이유만으로 비용을 0으로 만들지 않는다. |
| 검색 collection 손상 | SQL 날짜 조회는 계속한다. 복구 job 실패 원인을 해결한 뒤 검색 복구를 재시도한다. |
| 삭제 정리 대기 | reader/늦은 worker의 종료를 기다린다. 영수증 실패면 최소 error code로 원인을 조사한다. |
| `RECOVERY_REQUIRED` | v1의 메모리 작업은 성공으로 추정하지 않는다. 기존 파일/활성 자료를 확인한 뒤 인증된 화면에서 재분석한다. |

실패·미확정 호출의 실제 응답 내용이나 토큰을 로그에 출력하지 않는다. 정산·가격 변경은 [비용 운영 안내](BUDGET_RUNBOOK.md)를 따른다. HTTP용 관리자 가격/정산 편집 화면은 이 단계에 포함하지 않는다.

## 마이그레이션·롤백

v1 DB는 데이터 삭제 없이 버전 2로 업그레이드한다. 기존 활성 예약·사용자 교정·세션은 유지한다. v1의 미완료 processing receipt는 `RECOVERY_REQUIRED`로 기록한다. 새 작업은 전부 dispatcher로 실행하며 기존 BackgroundTasks/seed loop를 함께 실행하지 않는다. 실제 이관 CLI `--process`는 중단하고 이관한 문서를 인증된 화면에서 재분석한다.

롤백 전에 API/dispatcher를 중지하고 SQLite·원문·Chroma·private-job-artifacts를 일관된 시점으로 보존한다. `python -m src.foundation.cli backup --help`와 `restore --help`로 실제 옵션을 확인한다. v2 DB를 v1 바이너리에 그대로 물리지 않는다. v1 코드는 더 높은 스키마를 거절한다. v2 이후 생성한 자료는 별도 보존·이관 또는 v2 호환 수정본이 필요하며 조용히 버리지 않는다. 복원 시 세션 무효화·최신 tombstone 우선 규칙을 유지한다. 전체 운영 백업·재해 복구 훈련은 06 단계에서 별도 수행한다.

## 검증 범위와 제약

`tests/test_reliability_*.py`는 임시 SQLite, 실제 임시 Chroma, fake provider, 합성 단가를 사용한다. 실제 자식 프로세스 SIGKILL, lease 만료, 오래된 worker 완료, 마지막 예산 경쟁, 원격 성공 직후 응답 유실, SSE 재연결·권한 회수, 삭제와 완료 경쟁을 검사한다. `tests/browser_fixture.py`는 loopback 합성 OIDC와 fake provider를 가진 별도 테스트 실행기다. 이 파일을 실제 운영 앱으로 배포하지 않는다.

서버가 완전히 내려간 시간에는 작업이 진행되지 않는다. 항상 켜진 호스팅·persistent disk 배포는 별도다. 외부 exactly-once, 공급자 청구의 절대 hard cap, 여러 API 인스턴스/여러 uvicorn worker 운영을 보장하지 않는다. 실제 OIDC·OpenAI 계정·실제 가격/청구·프로덕션 장애 복구는 아직 검증하지 않았다. 03 단계 개발은 이 계약 위에서 진행할 수 있지만 사용자 공개 출시 판정은 아니다.
