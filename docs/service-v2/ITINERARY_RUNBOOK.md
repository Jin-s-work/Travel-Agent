# 05 일정 생성과 편집 계약

## 실행 범위

기존 인증·여행·예약·장소·추천·SQLite 작업/예산 위에 migration 6을 추가한다. 한 번에 도쿄 또는 바르셀로나의 한 체류 구간, 1~14일, 선택 장소 최대30곳을 처리한다. 다른 도시 예약을 삭제하거나 변경하지 않는다. 지원 구간을 넘는 생성은 명시적으로 거절한다. 외부 예약 생성·변경·취소, 상황별 Plan B, 상시 호스팅은 이 단계의 기능이 아니다.

## 생성

탐색에서 저장한 조건과 선택 장소를 재사용한다. `POST /api/v2/trips/{trip_id}/itineraries`에 Idempotency-Key, 현재 여행/조건 version, 날짜, 선택 장소별 체류시간, 활동 시간, 버퍼와 잠정 허용 여부를 전달한다. 서버가 입력 snapshot·예약 이벤트·사실 버전과 job을 저장한다. 동일 key/입력은 같은 작업, 다른 입력은409다. 작업은 기존 dispatcher에서 실행하고 GET/작업 이벤트로 복원한다.

확정 예약은 원래 현지 시간과 출발/도착 시간대를 유지한다. 날짜만 있는 예약은 자정으로 변환하지 않는다. 예약 자체의 상태와 사용자가 설정한 잠금은 별개다. 숙박 기간 전체를 busy로 만들지 않는다. 시간 구간은 UTC [start,end)이며 화면에는 현지 시간과 IANA 시간대를 표시한다. DST 중복/존재하지 않는 시각은 명시적으로 해결해야 한다.

기본 엄격 모드는 확인된 조건만 배치한다. 사용자가 잠정 초안을 허용하면 미확인 영업·경로를 표시한 배치가 가능하지만, 알려진 예약 충돌·휴무·인원 위반은 허용하지 않는다. 작업 성공과 일정의 validated/provisional/conflicted는 다른 값이다. 배치되지 않은 장소와 이유를 반환한다.

## 경로·비용

`TravelTime`은 provider/estimate/unknown을 구분한다. 운영 기본값에는 실제 경로 공급자가 없다. 허용된 동일 도시 좌표의 3km 이하 도보는 직선거리×1.4÷80m/분을 올림한 **계획 가정**이다. 실제 도로·해협·계단·공사·신호를 검증하지 않았으므로 엄격 모드의 확인 경로로 취급하지 않는다. 대중교통/차량/좌표 미확인은 unknown이다. unknown을 0분으로 바꾸지 않는다. 동일 지점 ID/동일 확인 좌표만 이동0이 가능하다.

조회당 기본 상한은 120 matrix elements, 20초다. endpoint·이동수단·정확한 출발 instant·provider/adapter/정책 버전을 포함한 session cache를 사용한다. 캐시는 사용자/여행을 넘지 않는다. 실제 provider를 주입할 때는 timeout·SDK retry0·관측 과금 단위가 필요하며 모든 호출은 기존 ProviderGateway 예약/영수증/정산을 거친다. 응답 유실은 unknown charge를 유지하고 재실행하지 않는다. 실제 단가 설정이 없으면 유료 실행이 닫힌다.

`route_usage`에는 요청 수, matrix elements, 실제 공급자 호출, 영수증 재사용, cache hit, 추정/미확인 수, 상한과 비용을 기록한다. 비용이 확인되지 않았으면 actual_micros는 null이다. 사용자 화면에서 이미 저장한 일정 읽기는 예산 소진과 관계없이 유지한다.

## 편집과 복원

- `POST /{itinerary_id}/edit-previews`: expected_version와 add/remove/move/lock/unlock 명령. 서버가 미리보기 ID, 변경 전후·영향·충돌·미확인·만료를 저장한다. 원 일정과 예약을 변경하지 않는다.
- `PATCH /{itinerary_id}`: preview_id와 expected_version. 현재 사용자/여행 삭제/버전/예약/출처/경로 만료를 재검사하고 하나의 SQL 트랜잭션에서 새 revision과 version을 저장한다. 충돌은409이고 원본을 유지한다.
- `POST /{itinerary_id}/undo-previews`: expected_version, steps1~10. 최대100개의 사용자 편집 복원 대상을 보존하며 현재 근거로 다시 검증한다. 적용은 같은 PATCH다. 과거 포인터로 돌아가지 않고 새 revision을 만든다. 삭제되거나 철회된 데이터는 복원하지 않는다.
- 제한된 자연어 명령은 `/edit-intents`에서 명시적 명령으로 변환한 뒤 같은 preview/apply를 거친다. 해석 불명은 확인 필요로 반환하며 실행하지 않는다.

예약 참조 항목은 기존 예약 교정 화면을 사용한다. 일정에서 선택 장소를 제거하는 것은 외부 예약 취소가 아니다. 원 입력 여행/조건이 바뀌면 오래된 snapshot으로 편집을 확정하지 않고 재생성을 요구한다.

## 로컬 검증

```bash
.venv/bin/python -m pytest tests/test_itinerary_engine.py tests/test_itinerary_api.py tests/test_itinerary_travel.py -q
node --test tests/test_discovery_ui.cjs tests/test_service_worker.cjs
python3 scripts/version_web_assets.py --check
.venv/bin/python tests/browser_itinerary_fixture.py
```

브라우저 harness는 별도 임시 저장소·합성 OIDC·합성 장소·네트워크 없는 경로 제공자만 사용한다. 운영 앱은 이 파일을 import하지 않는다. 실제 명령 결과와 화면 증거는 IMPLEMENTATION_STATUS.md와 실행 보고서에 기록한다. 이 문서는 시험 실행 성공 자체의 증거가 아니다.
