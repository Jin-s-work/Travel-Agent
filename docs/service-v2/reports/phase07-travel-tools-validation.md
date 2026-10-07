# 07 여행 준비·Plan B·오늘/오프라인 검증

검증일: 2026-10-06. Git 기준 `aefee57` 위의 미커밋 변경이다. 앞 단계와 Render Free + Supabase Free 전환 작업을 보존했다. 이번 작업에서 실제 Render 배포, Supabase 운영 DB 이관, 실제 사용자 초대 발송은 실행하지 않았다. 실제 외부 유료 공급자 호출은 0회다.

## 1. 구현과 활성화 범위

| 묶음 | 구현 | 로컬 검증 | 실서비스 검증 | 설정 |
| --- | --- | --- | --- | --- |
| A 예약 준비 | complete | 자동 시험·합성 OIDC 브라우저 검증 | not_run | `PREPARATION_ENABLED=true` 기본값 |
| B Plan B | complete, 아래 보수적 v1 범위 | 자동 시험·브라우저 preview/apply/undo | not_run | `PLAN_B_ENABLED=true` 기본값 |
| C 오늘·오프라인 | complete | 서버 종료 후 새로고침·IndexedDB·두 탭·계정 변경 | not_run | 오늘은 조회 기능, 기기 저장은 `OFFLINE_ENABLED=false` 기본값 |

기능은 따로 끌 수 있다. A/B의 기본값이 true인 것은 로컬 설정 기본값이며 기존 Render에 반영되었다는 뜻이 아니다. 오프라인을 켜도 출처별 저장 허용, 개인 기기 선택, 저장 내용 미리보기, 여행별 저장 동작이 필요하다. 운영 출처에 저장 권한을 임의 부여하지 않았다. 공개 표시 허용은 오프라인 저장 허용을 대신하지 않는다.

## 2. 저장·API·화면

SQLite와 PostgreSQL 모두 schema **9**로 올렸다. `reservation_tasks`, `reservation_task_events`, `offline_source_permissions`를 추가했다. v8→v9는 기존 여행·예약·일정 행을 교체하지 않는 추가 마이그레이션이다. PostgreSQL private schema/RLS·서버 전용 접근을 재사용한다. 기존 7/8 버전 snapshot 이관 경로도 유지한다. 운영 DB에는 실행하지 않았다.

주요 파일:

- `src/travel_tools/{schema,models,rules,exports,preparation,alternatives,today,routes}.py`: 새 기능 계약, 계산, 상태 전이, 근거 검증, API.
- `src/foundation/{db,settings,models,repository,routes}.py`: migration9, 독립 flag, 예약 교정의 `place_id`/`party`, 로그아웃 204 응답.
- `src/storage/{postgres,transfer}.py`: PostgreSQL migration9 및 snapshot 버전 호환.
- `src/itineraries/service.py`: 기존 apply 직전 Plan B의 변경 범위·현재 시각 재검사.
- `src/reliability/handlers.py`, `src/operations/backup.py`: 여행 삭제/복원 시 새 개인 할 일과 이력의 삭제 경계 연결.
- `web/js/{travel-tools,offline-store,foundation}.js`, `web/index.html`, `web/css/foundation.css`: 새 화면, 입력 보존, 두 단계 다운로드, 계정/여행 변경 정리.
- `web/sw.js`, `scripts/version_web_assets.py`: 공용 셸만 캐시, 내용 해시가 일치하는 자산만 추가 허용.
- `package.json`, `package-lock.json`: 브라우저 저장 테스트 전용 `fake-indexeddb@6.2.5`. 운영 서버에 Node 실행 경로를 추가하지 않았다.

새 경로는 모두 `/api/v2` 아래다. 세션·Origin/CSRF·개인 응답 no-store·여행 소유권 검사는 기존 공통 계층을 재사용한다. 다른 사용자/여행 ID는 404, 오래된 version은 409다. client의 owner_id로 범위를 결정하지 않는다.

| 경로 | 동작 |
| --- | --- |
| `GET /travel-tools/features` | 로그인 사용자의 기능 상태 |
| `GET/POST /trips/{trip}/reservation-tasks` | 목록/서버 ID 생성, Idempotency-Key 재사용·충돌 검사 |
| `GET/PATCH /trips/{trip}/reservation-tasks/{task}` | 최신 근거 확인, 허용 command와 expected_version |
| `POST /trips/{trip}/reservation-tasks/{task}/revalidate` | 같은 command 계약의 명시적 재검증 |
| `GET .../{task}/calendar.ics` | 현재 버전의 인증된 ICS snapshot |
| `POST .../{task}/inquiry-drafts` | ja/es/ca 초안과 한국어 확인본 |
| `POST /trips/{trip}/itineraries/{itinerary}/alternatives` | 변경 없는 대안 preview |
| 기존 일정 preview/apply/undo 경로 | 같은 validator·권한·버전·원자적 revision 저장 |
| `GET /trips/{trip}/today` | 현지 날짜·다음 일정·근거가 있는 이동·준비 목록 |
| `POST /trips/{trip}/offline-bundles` | 서버 허용 목록으로 조립한 최소 DTO |
| `POST /trips/{trip}/offline-bundles/validate` | 재연결 후 권한·삭제·현재 manifest 검사 |
| `POST /admin/offline-source-permissions` | 관리자 출처별·필드별·기간별 기기 저장 허용 |

## 3. A 예약 준비의 계약

할 일은 booking과 분리했다. 상태는 확인 필요 → 오픈 대기/진행 중 → 사용자 완료 → 증빙 확인과 취소로 구분한다. 상태 문자열을 임의 PATCH할 수 없다. 공식 링크를 열어도 완료나 확정 예약이 생기지 않는다. 증빙 확인은 사용자가 메일을 읽고 현재 구조화된 날짜·검증 지점·성인/아동을 대조하는 방식이다. 같은 여행의 살아 있는 원문 문서가 없는 수동 예약은 메일 증빙으로 인정하지 않는다. 시설의 실시간 예약 시스템을 조회했다는 의미가 아니다.

여행/인원/규칙/출처/예약 버전이 바뀌면 다시 확인하게 한다. 교정 값은 booking override에 남고 원문 추출값은 유지한다. 교정 직후 trip version이 증가한 경우에도 UI가 최신 할 일 version을 받아 대조를 이어가도록 했다. 잘못된 증빙은 422, 다른 사용자의 증빙은 404다.

`rolling_days`, `monthly_release`, `fixed_datetime`을 코드로 계산한다. 30일은 30일이며 한 달로 치환하지 않는다. 없는 월 날짜, Barcelona DST gap/fold, 시간대 불일치는 미확인이다. 날짜만 있으면 due_at은 null이다. 지난 예정 시점은 미래로 이동하지 않으며 화면은 현재 확인이 필요하다고 안내한다. 당일 날짜만 아는 경우 실제 오픈 여부를 확정하지 않는다.

ICS는 고정 UID, 할 일 version 기반 SEQUENCE, DATE/UTC instant 구분, CRLF, 75 octet UTF-8 folding, 개행/구분자 escaping을 적용했다. 설명에 시설 현지 준비 시각·IANA zone·희망 방문일·인원·공식 링크를 포함한다. 링크의 query/fragment/credentials는 내보내지 않는다. 취소 파일도 같은 UID를 쓰지만 기존 파일을 회수하거나 외부 예약을 취소하지 않는다. 다운로드는 자동 갱신 구독이 아니다. 형식 기준은 [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545)다. 외부 캘린더 앱의 가져오기/중복 처리까지 검증하지 않았다.

문의는 비용 없는 고정 템플릿과 구조화 슬롯을 쓴다. 일본어·스페인어·카탈루냐어에 한국어 확인본을 붙인다. 희망 날짜·시간/성인·아동 나이/음식·알레르기·접근 조건을 보존한다. 계약 밖 모델 문장이나 바뀐 슬롯은 안전 템플릿으로 복구한다. 추가 자유 입력은 원문 그대로 보존하고 번역 확인 필요로 표시한다. 자유문 전체 자동 번역이나 원어민 품질 검수가 끝난 것으로 보고하지 않는다. 자동 발송·전화·예약·결제는 없다.

## 4. B Plan B의 v1 범위

전체 일정 재생성을 하지 않는다. **아직 시작하지 않은 선택 방문 한 항목**을 대체한다. 다른 항목의 시간과 고정 예약은 유지한다. 이미 시작한 항목·과거 항목·잠금 항목·확정 예약의 자동 대체는 거절한다. 고정 예약의 취소 검토는 별도 준비 업무이며 외부 취소가 아니다. 현재 진행 중인 방문의 잔여 시간을 잘라 재배치하는 기능은 이번 버전에 없다.

비·휴무·긴 대기·피로·예약 실패와 사용자 보고/가정/공급자 확인을 구분한다. 공급자 확인은 검증된 휴무 사실을 연결한 경우에만 가능하다. 실제 날씨/대기/잔여석 제공자는 연결하지 않았고 값을 만들지 않는다. 비는 승인된 실내 후보, 피로는 이동·체류가 더 늘지 않는 후보만 사용한다. 위치 권한 없이 저장 출발점 또는 사용자가 입력한 위치를 쓴다. 새 반경은 기존 필수 조건을 좁힐 수만 있다.

최대 12개 후보, 15초 탐색 한도, 대안 최대 3개다. 부족하면 1개/0개와 이유를 반환한다. 기존 경로 wrapper의 비용 예약·영수증을 사용하며 실패 후보의 조회 사용량도 기록한다. 변경 없는 경로 영수증을 재사용한다. 전후 시각/장소/양쪽 이동/가격 기준/다음 고정 예약을 표시한다. 비용 차이를 계산할 근거가 없으면 null과 미확인으로 남긴다.

preview는 기존 revision을 수정하지 않는다. apply는 기존 소유권·삭제·version·근거 신선도·영업·경로·고정 예약 검증에 현재 시각과 영향 범위 검사를 추가한다. 새 revision 저장과 undo는 기존 엔진을 그대로 거친다. 경로 실패를 0분으로 바꾸지 않는다.

## 5. C 저장·권한 경계

오늘의 기본 날짜는 선택 일정/여행 조건의 현지 시간대다. 이동 근거가 없으면 출발 권장 시각도 미확인이다. 서버에서 원문/예약번호/결제/세션/token/raw 리뷰/사진/지도 타일을 제외한 DTO를 만든다. 예약 항목은 일반 이름을 쓰고 booking ID를 오프라인 묶음에 내보내지 않는다. 사용자 메모는 별도 체크와 내용 미리보기 후에만 포함한다.

저장은 도시별 가장 최근 일정만 포함한다. 모든 과거 revision이나 대화 기록을 복사하지 않는다. schema version 1, 사용자 namespace, trip version, manifest, 허용 필드 목록, 생성·만료 시각을 보존한다. 상한은 512 KiB, 만료는 최대 24시간 또는 출처 허용 만료 중 빠른 시각이다. 오프라인 지도는 제공하지 않으며 외부 링크는 연결이 필요하다.

IndexedDB의 임시 저장 → checksum/필드/크기/namespace 검증 → 트랜잭션으로 활성 교체를 구현했다. 새 저장 실패/용량 부족이면 이전 유효본을 유지한다. epoch fence와 동기 revoke marker가 로그아웃 직전 시작한 늦은 저장의 부활을 차단한다. BroadcastChannel로 다른 탭 메모리도 정리한다. 재연결 시 인증·삭제·manifest를 확인하기 전 개인 snapshot을 표시하지 않는다. 401/403/404를 네트워크 실패로 취급해 fallback하지 않는다. 네트워크 단절 때만 선택한 유효 읽기 전용 저장본을 연다.

오프라인 기기에 서버의 원격 삭제가 즉시 반영될 수는 없다. TTL이나 로그아웃 정리는 악의적인 기기 소유자가 저장 파일을 복사하는 것을 막는 암호화 보호가 아니다. 공용 기기에서는 저장하지 않도록 명시했다. 일반 API는 서비스 워커에 넣지 않는다. IndexedDB 원자성 구현 기준은 [IDBTransaction](https://developer.mozilla.org/en-US/docs/Web/API/IDBTransaction)이다.

## 6. 실행한 시험

실행 출력은 [테스트 결과 원문](phase07-test-results.txt)에 보존했다.

### Python / PostgreSQL

```bash
.venv/bin/python -m pytest tests -q
# 711 passed, 11 skipped, 2 warnings / 72.59s

.venv/bin/python -m pytest tests/test_travel_preparation.py tests/test_travel_alternatives.py tests/test_travel_today.py -q
# 17 passed, 2 warnings / 8.25s
```

11 skip은 별도 PostgreSQL/pgvector 환경이 필요한 cloud 시험이다. 다음 시험에서는 실제 로컬 Docker PostgreSQL16/pgvector에 연결했다. Supabase Storage HTTP와 외부 공급자는 fake다. 운영 DSN을 사용하지 않았다.

```bash
# 일회용 loopback DB. 아래 DSN의 암호는 이 시험용 컨테이너의 폐기 가능한 값이다.
TRAVEL_TEST_POSTGRES_DSN=postgresql://postgres:phase07-disposable-test@127.0.0.1:55439/postgres .venv/bin/python -m pytest -p tests.postgres_plugin tests/test_travel_preparation.py tests/test_travel_alternatives.py tests/test_travel_today.py -q
# 17 passed, 2 warnings / 15.37s

TRAVEL_TEST_POSTGRES_DSN=postgresql://postgres:phase07-disposable-test@127.0.0.1:55439/postgres .venv/bin/python -m pytest tests/test_cloud_storage.py -q
# 11 passed, 2 warnings / 16.59s
```

새 시험은 월말/윤년/DST/날짜 정밀도, ICS 개행 주입·UTF-8·UID/SEQUENCE·취소, 문의 슬롯 변경 거절, 소유권·중복 요청·상태/버전 충돌, 증빙 불일치·교정·출처 회수, 영향 범위 preview/apply/undo, 후보 0개·잠금/확정 예약·과거 시각, DTO 민감 필드 배제·메모 opt-in·삭제 경합을 검사한다. 기존 전체 회귀가 고정 예약·동시 편집·삭제·비용·복구 경계도 검사한다. Starlette/Authlib 경고 2개는 기존 httpx 호환 API deprecation이다.

### JS / 저장 실패

```bash
node --test tests/test_travel_offline.cjs tests/test_service_worker.cjs tests/test_discovery_ui.cjs
# 24 passed, 0 failed
node --check web/js/travel-tools.js
.venv/bin/python scripts/version_web_assets.py --check
.venv/bin/python -m compileall -q src/travel_tools
git diff --check
# 모두 exit 0
```

새 IndexedDB 시험 5개는 실제 저장 API의 fake 구현에서 최종 put의 QuotaExceededError, 임시 저장 중 logout, namespace 교체, checksum/만료/민감필드 거절, 여행 삭제를 주입한다. 실제 iPhone의 저장공간을 고갈시킨 시험과는 구분한다. 전체 24개에는 기존 발견 화면 16개와 service worker 3개가 포함된다.

## 7. 직접 확인한 브라우저 흐름

`tests/browser_travel_tools_fixture.py`를 loopback 8765/8766에서 실행하고 **Codex 내장 브라우저**를 조작했다. 실제 Google 계정이 아닌 합성 A/B OIDC, 합성 도쿄/바르셀로나 후보, fake 경로다. state/nonce/PKCE/서명 검증은 기존 Authlib 경로를 통과했다. 이번 화면 흐름의 선택 여행/후보는 도쿄 합성 자료다.

1. A 로그인 → 도쿄 여행 → 예약 준비 추가 → 공식 규칙 선택. 2026-11-06 방문의 30일 전 10:00가 2026-10-07 10:00 Asia/Tokyo로 표시되었다.
2. ICS 다운로드로 실제 `preparation.ics` 파일 생성, 일본어/한국어 초안에 18:30·성인 4명·아동 0명과 미확정 문구 확인. 이후 ICS 현지 시각/공식 링크 보완은 자동 serializer 시험으로 추가 확인했다.
3. 오늘에서 11월 6일 선택 → 다음 장소/10분 경로와 09:50 출발 권장 표시. 자료 갱신으로 경로 근거가 불충분한 경우 미확인 표시도 확인했다.
4. 휴무를 사용자 보고로 선택 → 가능한 대안 **1개** → preview 전 원 일정 유지 → apply → 기존 일정 undo 미리보기/적용 → 새로고침 → version 3에서 원래 장소 복원.
5. 개인 기기 미선택은 저장 거절. 개인 기기 선택 → 허용된 장소/시각/주소와 메모 0개를 미리보기 → 명시적 저장 성공.
6. 앱 서버 프로세스 종료 → 브라우저 새로고침 → 캐시된 공개 앱 셸 → 선택 저장본 열기. 서버에 접속할 수 없는 상태에서 원래 장소/시각과 읽기 전용·현장 재확인 안내를 실제로 읽었다. OS 비행기 모드 스위치를 켠 시험은 아니다.
7. 재연결 후 fixture가 갱신한 출처 version과 manifest가 달라진 이전 저장본이 먼저 제거되었다. 이전 개인 내용을 권한 확인 전에 표시하지 않았다. fixture가 시작할 때 합성 출처를 갱신하므로, 이 무효화는 서버 재시작 자체로 데이터가 사라지는 현상이 아니다.
8. 두 탭에서 A 세션 → 로그아웃 시 양쪽 개인 화면/저장본 제거. B 로그인은 빈 여행 목록이며 A의 내용 없음. 이후 로그아웃 정상 문구 확인. 이 과정에서 기존 로그아웃 API의 303 HTML 응답을 204로 고쳐 성공을 오류로 표시하던 문제를 해결했다.
9. 390×844 폭, 200% 큰 글자, 긴 일본어 이름, 키보드 Space/Enter, 모달 초점 확인. 글자 크기를 되돌리고 임시 viewport override도 해제했다.
10. 마지막 수정본의 두 단계 저장 → 서버 종료 → 새로고침/읽기를 다시 확인하고 [화면](phase07-offline.png)을 저장했다. 검사한 임시 저장본과 두 검증 탭을 정리했다.

![서버가 종료된 상태의 읽기 전용 저장본](phase07-offline.png)

미검증: 실제 iOS Safari/Android Chrome/PWA 설치·OS 저장 공간 회수·백그라운드 탭 종료, 외부 캘린더 앱, 실제 OIDC/HTTPS/Render cold start에서 이 단계의 화면, 실제 날씨/대기/예약/경로, 실제 출처의 오프라인 이용권, 원어민 문의문 검수. 증빙 교정 후 완료 연결의 마지막 버전 처리 수정은 API 시험으로 검증했으며 해당 교정 UI를 실브라우저에서 다시 실행했다고 주장하지 않는다.

## 8. 운영 반영과 다음 단계

현재 실제 Render URL은 이전 배포다. 14절의 DB 비밀·서버 Storage key·OIDC·Git 반영·HTTPS/백업 복원 검증이 남아 있으며 이번 기능을 위해 새 유료 리소스는 만들지 않았다. 이번 단계에서 기존 후보의 운영 사용 승인이나 엄격 리뷰 기능을 켜지 않았다. 운영 사용 승인 후보 수가 없는 도시를 합성 자료로 지원한다고 표시하지 않는다.

관리자가 검토한 규칙/출처·기기 저장 정책과 실제 계정이 준비되면 A → B → C를 각각 켜서 배포 시험할 수 있다. 오프라인 미검증은 A/B의 출시를 막는 필수 조건이 아니다. **08 피드백 단계의 로컬 개발은 진행 가능**하며 이번 작업에서 08을 실행하거나 완료한 것은 아니다.
