# 05 일정 API·영구 저장·복구 검증

검증일: 2026-10-05. Git 기준 revision: `aefee57`. 이 문서의 구현은 기존 사용자 변경을 포함한 미커밋 작업 트리에서 실행했다. 커밋·push·배포·실제 외부 예약 변경은 하지 않았다.

## 구현 범위

`src/itineraries/schema.py`의 migration 6은 `itineraries`, `itinerary_revisions`, `itinerary_previews`를 추가한다. 일정은 사용자·여행에 귀속되고, 현재 revision 포인터와 version을 갖는다. 원래 조건 snapshot, 현재 예약 이벤트·장소 사실의 manifest, 작업 ID를 서버에서 생성·연결한다. 원문 메일과 예약번호는 일정 snapshot·job payload에 복사하지 않는다.

생성은 기존 SQLite job dispatcher의 `itinerary_generate` 작업을 사용한다. 동일 실행자·여행·operation·Idempotency-Key·payload는 같은 일정과 작업을 반환한다. 같은 키의 다른 입력은 409다. job에는 일정 ID만 보관한다. 엔진이 결과를 반환한 후에도 여행 삭제, 세션, fencing token, 여행/조건 version, 예약·장소·경로 정책 manifest와 이동 근거 만료를 검사한 뒤 revision을 활성화한다.

한 생성 요청은 지원하는 한 도시 체류 구간 안의 1~14일, 고유 장소 최대 30곳이다. 체류시간은 5~720분이고 사용자 선택인지 기본 계획값인지 구분한다. 일일 활동창은 같은 현지 날짜에서 시작보다 끝이 늦어야 한다. 영업시간 자체의 자정 넘는 구간은 별도 순수 엔진에서 처리한다. 미지원 도시의 기존 예약은 원본을 수정하지 않고 유지한다.

출발점은 저장한 조건의 명시적 좌표를 사용할 수 있다. 사용자가 보낸 미검증 `origin.place_id`만으로 같은 장소라고 판단해 이동을 0분으로 만들지 않는다. 예약 메일의 위치 문자열을 임의 좌표로 바꾸지 않는다. 경로와 장소 정보 부족은 엄격 모드에서 미배치하며, 사용자가 잠정 초안을 허용한 경우에도 실제 이동시간 `null`과 별도 계획 여유시간을 구분한다.

## HTTP 계약

모든 경로의 공통 prefix는 `/api/v2/trips/{trip_id}/itineraries`다. 인증·초대·Origin/CSRF·소유권은 기존 계층을 재사용하며 개인 응답은 `private, no-store`다.

| 메서드·경로 | 입력·결과 |
| --- | --- |
| `POST /` | 여행·조건 version, 날짜, 선택 장소·체류시간·우선순위, 선택적 추천 run, 활동창·버퍼·휴식·잠정 허용 → 202, itinerary/job/status/events 참조 |
| `GET /` | 소유 여행의 저장 일정 목록과 pagination |
| `GET /{id}` | 현재 revision, items/legs/conflicts/unplaced/unresolved_conditions, 조건 snapshot, input/data 상태, revision 이력 |
| `POST /{id}/edit-previews` | expected_version + add/remove/move/lock/unlock whitelist → 서버 저장 preview와 diff·영향 날짜·충돌·미확인·10분 만료·can_apply |
| `PATCH /{id}` | preview_id + expected_version → 검증 후 새 revision 및 포인터/version 원자 갱신 |
| `POST /{id}/undo-previews` | expected_version + steps 1~10 → 같은 preview/apply 흐름의 되돌리기 제안 |
| `POST /{id}/edit-intents` | 선택 항목과 짧은 텍스트 → 제한된 명령 또는 명확화 요청. 자동 적용 없음 |

자연어 입력은 `삭제`, `잠금`, `잠금 해제`, 명시적인 `YYYY-MM-DD HH:MM` 이동 등의 제한된 표현만 코드로 변환한다. 모호한 목표나 잘못된 날짜는 `needs_clarification`이다. 임의 URL·코드·SQL 실행, 외부 예약 변경, 자연어 최적화는 제공하지 않는다.

`buffers`는 `general_minutes`, `booking_before_minutes`, `booking_after_minutes`, `airport_before_minutes`, `airport_after_minutes`, `unknown_travel_allowance_minutes`를 구분한다. 미입력 공항 버퍼는 null이며 일반 이동 버퍼로 대체하지 않는다. `rest_preferences`는 minutes/after_visits/required를 갖고, 식사창은 선택적인 `{meal,start,end}` 목록이다.

## 편집·철회·복구 규칙

- preview는 원 일정과 예약을 바꾸지 않는다. 허용된 읽기 전용 경로 조사 비용과 preview 저장만 발생할 수 있다.
- 예약에서 온 잠금 항목은 일정 API로 이동·삭제·잠금 해제할 수 없다. 기존 예약 교정 화면을 이용한다. 사용자 잠금은 명시적인 unlock 후 변경할 수 있다.
- apply는 preview의 소유자·여행·일정·기준 version·만료와 현재 여행/조건 version을 다시 검사한다. preview 단계에서 거절한 명령을 이후 validator가 정상 원본 항목을 검사했다는 이유로 통과시키지 않는다.
- 같은 성공 preview의 재적용은 같은 revision을 반환한다. 다른 탭의 편집은 409이며 원래 입력을 서버가 임의로 덮어쓰지 않는다.
- undo는 포인터를 과거 version으로 되돌리는 대신 새 revision을 만든다. 최근 사용자 편집 최대 100개의 되돌리기 참조를 유지하며, 한 번에 1~10단계를 제안할 수 있다. 현재 예약·정책·사실로 검증하며 철회·삭제 자료를 복원하지 않는다.
- 예약 교정이나 여행/조건 변경 후 기존 일정은 `input_status=stale`다. 예약 근거 변경이면 `data_status=stale`, 항목의 원래 시간과 현재 예약 정보를 구분하여 표시한다. 편집은 `INPUT_SNAPSHOT_STALE`로 거절하고 현재 조건으로 재생성하도록 안내한다.
- 출처나 경로 정책이 바뀐 저장 일정은 검증 완료로 계속 표시하지 않는다. 사용자의 배치 의도는 남기되 폐기된 source_refs·거리·이동시간을 숨기고 다시 확인할 상태로 표시한다.
- 여행 삭제는 즉시 접근을 차단한다. 기존 삭제 작업이 일정 snapshot·revision payload·preview를 정리하며 늦게 끝난 작업이 다시 활성화하지 못한다.

## 실제 실행 결과

```bash
.venv/bin/python -m pytest tests/test_itinerary_api.py tests/test_itinerary_recovery.py -q
# 23 passed, 2 warnings in 11.96s

.venv/bin/python -m py_compile src/itineraries/schema.py src/itineraries/models.py src/itineraries/service.py src/itineraries/routes.py
# exit 0

git diff --check
# exit 0
```

두 경고는 기존 Starlette/Authlib의 httpx 호환 API deprecation이다. 테스트는 임시 SQLite와 초대 기반 합성 인증을 사용하며, 실제 route provider 대신 사용량을 반환하는 가짜 공급자를 공통 예산 wrapper로 통과시켰다.

| 시험 | 확인한 결과 |
| --- | --- |
| 저장·새로고침 | snapshot과 item ID/revision 유지, 상태 조회, 개인 응답 no-store |
| A/B 및 같은 사용자의 다른 여행 | 일정·preview·undo·자연어·job·SSE 접근 404 |
| 동시 생성 | 같은 키 4개 요청 → 일정·job 1개; 다른 입력 409 |
| preview/apply | preview 후 원 일정·예약 행 불변; 적용 후 version 증가; 성공 적용 재전송 중복 revision 없음 |
| 잠금·예약 | 고정 예약 이동/삭제/잠금 해제 거절; 외부 예약 기록 불변 |
| 10회 편집·10회 undo | 총 21개 순차 revision; version 역행 없음; 원 사용자 잠금 상태로 복원 |
| 두 탭·만료 | 동시 apply 200/409; 만료 preview 409; 원본 보존 |
| 근거 변경 | 출처 철회·경로 정책 변경·조건 수정·예약 교정 후 이전 preview 적용 거절 |
| 생성 중 변경 | 취소·여행 삭제·조건 수정 뒤 늦게 반환한 결과 활성화 0건 |
| 정보 부족 | 실제 route 기본 미설정: 엄격 모드 미배치, 명시적 잠정 모드는 provisional |
| 명령 검증 | 잘못된 날짜는 명확화, 비허용 명령 422, 중복 장소 add preview 거절을 apply가 무시하지 못함 |
| 비공개 원문 | itinerary/revision/job/event 저장에 시험 예약번호·메일 본문 문자열 없음 |

`test_itinerary_recovery.py`는 실제 자식 프로세스에 `SIGKILL`을 보냈다. 첫 번째는 경로 응답이 예산 receipt에 저장된 직후, 두 번째는 일정 revision 활성화 직후이며 job 완료 기록 전이다. lease를 만료시킨 다음 새 Python 인터프리터가 같은 DB에서 fencing token 2로 재개했다. 공급자는 DB 밖 별도 파일에 요청 hash를 fsync했다. 두 경우 모두 중복 provider 요청 hash가 없었고 최종 revision은 정확히 한 개, 조회 version은 1이었다. 과금 예약 행 수는 실제 가짜 공급자 호출 수와 일치했다. 활성화 직후 중단의 재개는 공급자를 다시 호출하지 않았다.

## 검증하지 않은 범위

실제 지도 경로 정확도·실시간 교통·예약 슬롯·운영 API 요금·운영 서버 장애 복구는 검증하지 않았다. 외부 예약·취소 요청은 보내지 않았다. 순수 일정 엔진의 시간대/영업/고정 예약 시나리오와 브라우저 타임라인·모바일 검증은 별도 담당 시험 및 최종 IMPLEMENTATION_STATUS 기록과 함께 판단해야 한다. Plan B는 07단계 범위이며 이 보고서가 PLAN-08의 전체 항목 통과를 의미하지 않는다.
