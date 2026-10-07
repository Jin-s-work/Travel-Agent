# Stage 1 테스트 실패 조사 — 2026-10-07

현재 확인 가능한 결론은 **전체 실행의 7건은 실제 실패로 남겨 보고하되, 해당 파일 단독과 선행 순서 재현에서는 모두 통과한 비결정적 대기시간 초과**다. 일시적 환경 부하가 원인이라고 확정할 근거는 부족하다. 재실행 성공이 최초 실패를 취소하거나 전체 테스트 통과를 뜻하지 않는다.

## 실행 결과

| 실행 | 결과 | 시간 | 근거 |
|---|---|---:|---|
| 최초 전체 pytest — 메인 감사 실행 | 7 failed / 1032 passed / 15 skipped | 502.74초 | [pytest.log](pytest.log) |
| 실패가 모인 `tests/test_foundation_api.py` 단독 | 39 passed / 2 warnings | 13.00초 | [foundation-api-rerun.log](foundation-api-rerun.log) |
| 전체 collection을 유지하고 해당 파일까지 선행 순서 재현 | 220 passed / 11 skipped / 823 deselected / 2 warnings | 30.64초 | [foundation-prefix-rerun.log](foundation-prefix-rerun.log) |

앱·저장소 테스트 파일·10초 timeout은 변경하지 않았다. `PYTHON_DOTENV_DISABLED=1`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`를 사용했고 기존 `tests/conftest.py`의 격리 경로/가짜 key/외부 discovery 비활성화를 유지했다. 사용자 fixture 서버나 운영 DB를 재사용하지 않았다. 저장소 git status는 조사 전후 기존 `.key` 수정 한 건만 남았다.

## 실패가 실제로 의미하는 것

7건 모두 `tests/test_foundation_api.py:105–114`의 `_job`에서 최초 업로드 작업이 10초 내 terminal state에 도달하지 않아 실패했다. HTTP 실패, 권한 위반, 예약 내용 불일치, 추가 provider 호출 검증 실패는 관측되지 않았다.

| 최초 실패 테스트 | 실패 위치 | 순서 재현 시 call 시간 |
|---|---|---:|
| `test_two_users_two_trips_enforce_every_private_id_before_external_calls` | `:127` 최초 `_upload` | 1.2646초 |
| `test_upload_same_filename_is_opaque_and_duplicate_detection_is_trip_local` | `:167` 최초 `_upload` | 0.5417초 |
| `test_mixed_upload_errors_and_idempotency_have_no_extra_provider_calls` | `:198` 최초 작업 대기 | 0.2388초 |
| `test_day_query_contains_eight_source_bookings_plus_manual_even_with_page_limit` | `:220` 최초 `_upload` | 0.2770초 |
| `test_reprocess_failure_preserves_corrected_booking_and_active_generation[parse]` | `:245` 최초 `_upload` | 0.3162초 |
| 같은 테스트 `[embed]` | 동일 | 0.3391초 |
| 같은 테스트 `[vector]` | 동일 | 0.3382초 |

특히 마지막 3건은 parse/embed/vector 장애를 주입하는 `:254–259`에 도달하기 전 실패했다. 따라서 이 결과를 '재분석 장애 시 사용자 수정본 유실'의 재현 증거로 사용해서는 안 된다. 첫 테스트 역시 교차 사용자 ID 접근 검증 전에 실패했으므로 격리 취약점 발견으로 해석할 수 없다.

최초 assertion 출력은 job dict를 생략해 `state`, `stage`, 마지막 checkpoint가 로그에 없다. `running` 지연인지 `queued` 지연인지, 특정 lock/Chroma 호출 중인지 이 로그만으로 판별할 수 없다. teardown 뒤 fixture 상태를 현재 상태로 읽는 것만으로 최초 10초 시점의 stack을 복원할 수도 없다.

## 순서 의존성 확인 방법

단독 실행만으로는 선행 테스트 오염을 배제할 수 없어 두 번째 실행에서는 `tests` 전체를 collection/import했다. 외부 진단 plugin의 `pytest_collection_modifyitems`에서 `tests/test_foundation_api.py`보다 뒤의 node만 제외했다. 따라서 원래 파일에 앞서는 다음 12개 파일의 실제 실행 순서와 전체 collection 단계 import를 유지했다.

`test_accommodations_api`, `test_accommodations_origin`, `test_api`, `test_booking_time_conflicts`, `test_catalog_presentation`, `test_catalog_registration`, `test_cloud_configuration`, `test_cloud_storage`, `test_destinations`, `test_discovery_fetch`, `test_discovery_foundation`, `test_discovery_intents`.

결과는 모두 통과했다. 현재 조건에서 단순한 선행 실행 순서나 전체 collection import만으로 7건을 재현하지 못했다. 특정 resource 압력·스케줄링·스레드/라이브러리 초기화와 결합된 문제까지 배제한 것은 아니다.

진단 plugin은 테스트/앱 동작과 timeout을 변경하지 않는다. 실패 시 job의 상태·단계·횟수·시간과 스레드의 파일/함수/줄만 기록하고, 원문·예약 payload·환경변수·비밀 값은 저장하지 않도록 했다. 이번 재현에는 실패가 없어 stack은 수집되지 않았다. [plugin](going_test_probe.py), [개별 node 결과 JSON](foundation-prefix-diagnostics.json).

## 판단과 다음 구현에서 요구할 검증

- **확인:** 최초 실행의 실제 10초 timeout 7건. 단독39개와 선행순서 포함220개 재실행 성공.
- **미확인:** 일시적 머신 부하, Chroma 초기화/내부 작업, event-loop/worker scheduling, DB 경합 중 어떤 것이 최초 지연을 만들었는지. 최초 시점의 자원/스레드/작업 단계 기록이 없으므로 환경 원인 확정이나 앱 결함 없음 선언은 불가하다.
- **운영과 구분:** 이 fixture는 로컬 SQLite+Chroma이며 가짜 parser/embedder를 쓴다. 현재 배포 선언은 Supabase/PostgreSQL/pgvector다(`src/reliability/generations.py:76–86`). 로컬 테스트 초기화 지연을 곧바로 운영 추천45–53초의 원인으로 연결하지 않는다.
- **수정 범위 제안:** timeout을 늘려 통과시키지 않는다. fixture timeout 실패 시 마지막 안전한 job 상태/단계와 worker stack을 남기고, 주요 stage 시간과 queue 지연을 분리한다. code change 후 동일10초 기준으로 전체 pytest를 단독 실행하고 PostgreSQL 적용 범위는 별도 disposable DB에서 검증한다. 실패가 재발하면 stage/stack 근거를 확보한 뒤 해당 원인을 수정한다.
- **release 표현:** '전체1039개 통과'로 합산해 쓰지 않는다. '전체 실행7실패·1032통과·15건skip; 실패 파일 재실행39통과, 선행순서 재현220통과; 최초 timeout 원인 미확정'이 현재 증거에 맞다.

## 재실행 명령

저장소 cwd: `/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`.

```sh
PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_foundation_api.py -q -p no:cacheprovider --durations=20 --tb=short
```

순서 재현은 이 보고서와 같은 디렉터리의 `going_test_probe.py`를 import 경로에 포함한다. 최초 실행 때는 동일 파일을 `/private/tmp/going_test_probe.py`에 두었다.

```sh
PYTHONPATH=docs/service-v4/reports:. PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests -q -p no:cacheprovider -p going_test_probe --durations=20 --tb=short
```
