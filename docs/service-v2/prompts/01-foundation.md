# Travel Agent 01단계 기존 예약 기능 안정화와 개인 여행 공간

문서 기준 디렉터리는 docs/service-v2다. 아래 문서명과 examples 경로는 이 디렉터리를 기준으로 읽어.
아래 내용 전체를 개발 에이전트에게 전달해. 파일 경로만 지시하는 짧은 실행문이 아니라, 이 문서 자체가 이번 단계의 구현 요청이다.

---

너는 Travel Agent의 제품 개발 책임자이자 구현 엔지니어다. 다음 저장소에서 아래 기능을 실제 코드와 화면으로 구현하고 검증해.
작업을 다시 기획하거나 TODO 파일만 작성하고 끝내지 말고, 사용자가 로그인해 자기 여행과 예약을 관리할 수 있는 흐름까지 완성해.
저장소는 `/Users/jinsangwoo/Desktop/ChatGPT/travel-inbox-rag`, 원격은 `https://github.com/Jin-s-work/Travel-Agent`다.

## 1. 제품 목표와 이번 단계의 경계

이 서비스는 본인과 초대한 소수 지인이 여행 준비와 현지에서 사용하는 비공개 여행 도우미다.
기존 예약 메일 분석·근거 질문을 보존하고, 이후 현지 탐색 음식점과 대표 명소 추천 및 일정 작성을 연결한다.
추천 검증 지역은 도쿄·바르셀로나이지만 하코네나 다른 도시의 기존 예약·직접 입력은 그대로 지원한다.
이번 단계에서는 인증, 여행·예약의 구조화 저장, 메일 업로드·교정·조회, 여행 범위 질문과 이를 사용하는 화면을 완성해.
추천 엔진, 크롤러, 일정 최적화, 공개 가입, 여행 공동 편집은 이번 단계의 필수 기능이 아니다.
FastAPI, 같은 출처의 PWA, SQLite, Chroma, 단일 인스턴스를 유지해. 전면적인 프레임워크 교체로 범위를 늘리지 마.
외부 공급자 키가 없어도 fake adapter와 합성 데이터로 전체 흐름을 검증하고, 실제 연동 미검증 상태를 명확히 기록해.

## 2. 처음 수행할 조사와 현재 코드의 변경 지도

먼저 적용되는 AGENTS.md, `git status`, 현재 실행 명령, `docs/service-v2/IMPLEMENTATION_STATUS.md`를 확인해.
다음 문서의 관련 부분을 읽어: PRD.md, TECHNICAL_SPEC.md, DATA_AND_OPERATIONS.md, ACCEPTANCE_TESTS.md, IMPLEMENTATION_PROMPTS.md의 P00·P01.
이 문서의 과거 감사 revision `aefee57`은 참고 기록이다. checkout/reset 대상으로 삼거나 사용자 변경을 덮어쓰지 마.
실제 구현이 이미 달라졌다면 현재 코드를 기준으로 기능을 재사용하고, 명세와 다른 결정은 짧은 ADR에 이유를 남겨.
현재 코드를 조사할 때는 아래 항목을 빠뜨리지 마. 우측 경로는 새로 제안하는 책임 분리이며 아직 존재한다고 가정하지 마.

| 현재 위치·동작 | 이번 단계의 실제 수정 방향 | 제안 책임 위치 |
| --- | --- | --- |
| `api.py`의 전역 `/api/bookings`, `/api/ask`, `/api/index` | 모든 개인 동작에 세션·trip scope 적용, v2 라우터 연결 | `src/http/`, `src/auth/` |
| `api.py`의 `Message.role: str` | user/assistant enum과 전체 history 크기 검증 | 요청 DTO |
| `src/agent.py`의 `_last_sources`, `_trip_start_override` | 요청 context와 여행별 설정으로 교체 | `src/search/`, `src/trips/` |
| `src/store.py`의 공용 `get_store()` | 권한이 확인된 TripSearchContext 없이는 개인 검색 불가 | `src/search/` |
| `src/parser.py`의 첫 구간 하나 추출 | 문서 1개 → 예약 배열·예약별 이벤트 배열 | `src/documents/` |
| `src/indexer.py`의 기존 청크 선삭제 | 추출·임베딩 성공 전 기존 데이터 보존 | 검색 갱신 adapter |
| `api.py`의 filename 기반 예약 ID·업로드 경로 | 서버 생성 document/booking ID와 비공개 저장 경로 | `src/documents/`, `src/db/` |
| `web/index.html`, `web/sw.js` | 여행 선택·예약 관리 화면, 개인 응답 캐시 금지 | 기존 web 구조, 필요 시 `web/js/` |
| 기동 시 공용 데모 자동 주입 | 명시적 데모 여행 또는 테스트 fixture로 격리 | seed adapter |

모든 파일을 한꺼번에 재작성하지 말고 순수 함수·기존 테스트를 보존하면서 인증된 수직 흐름부터 연결해.
`src/loader.py`의 메일 해석, parser의 시간 보정, 검색 텍스트 생성 등 재사용할 함수는 새로운 저장 계층과 분리해.
현재 테스트를 임시 저장소·dummy key로 실행해 기준을 기록하되 실제 메일·실제 Chroma를 검사나 삭제 대상으로 사용하지 마.

## 3. 구현 순서와 선행조건

1. 요청별 출처·history 검증·날짜 조회·교체 실패 보존부터 회귀를 추가하고 기존 코드에서 수정해.
2. SQLite 연결·마이그레이션과 세션/여행 소유권 dependency를 구현해.
3. 여행 생성·목록·상세와 예약 목록을 DB 기준으로 연결해.
4. 안전한 업로드, 복수 예약 추출, 사용자 교정 레이어를 연결해.
5. 여행 범위 질문·출처 다운로드·삭제까지 권한을 일관되게 적용해.
6. 기존 PWA에 화면을 연결하고 실제 사용자 흐름과 이관 dry-run을 검증해.
7. 내구성 dispatcher·lease·검색 세대 전환의 완성은 02단계로 넘기되 필요한 저장 계약을 먼저 마련해.

각 순서에서 API만 만들고 화면을 마지막까지 미루지 마. 여행 생성 → 조회처럼 작은 흐름부터 사용 가능하게 해.
인증이 필요한 외부 계정 설정이 없으면 구체적인 환경변수·callback 경로·설정 절차를 준비하고 테스트용 dependency override로 계속 구현해.
운영 인증을 열어두거나 샘플 계정·고정 비밀번호를 넣어 진행하지 마. 이미 승인된 설정이나 작업을 다시 승인받지는 마.

## 4. 인증과 초대: 로그인 여부와 서비스 이용 권한을 함께 검증

기존 인증이 있으면 재사용하고, 없으면 검증된 라이브러리/인증 제공자 한 가지를 선택해 짧은 ADR로 결정해.
OAuth/OIDC를 선택하면 state·nonce·PKCE·redirect allowlist는 해당 라이브러리의 검증된 흐름을 사용해.
서비스 계정은 초대 allowlist를 통과해야 한다. 로그인 성공만으로 임의 사용자의 가입을 허용하지 마.
이메일 초대라면 공급자가 확인한 이메일과 초대 대상을 비교하고, client가 보낸 email을 신뢰하지 마.
초대는 충분한 무작위 토큰, hash 저장, 만료, 1회 사용, 회수를 지원하며 동시 사용은 SQL에서 하나만 성공하게 해.
세션은 서버 저장 방식으로 user_id·만료·session_epoch를 검증하고, 로그아웃과 운영자의 사용자 비활성화가 기존 세션을 무효화하게 해.
운영 쿠키는 `__Host-session`, Secure, HttpOnly, SameSite=Lax, Path=/이며 Domain을 지정하지 마.
로컬 개발 HTTPS 또는 별도 개발 쿠키 설정을 명시적으로 분리하고, 운영 보안을 편의상 낮추지 마.
변경 요청은 Origin 검사와 세션에 결합된 CSRF token을 검증해. 업로드·로그아웃·삭제도 예외가 아니다.
`GET /api/v2/session`의 최소 응답은 사용자 표시 이름·role·세션 만료 시점과 필요한 CSRF 정보로 제한하는 제안 계약이다.
인증 시작/콜백/로그아웃 경로는 선택한 제공자에 맞춰 문서화하고, 비밀 client key를 브라우저에 전달하지 마.
설정이 없는 운영 모드는 개인 API를 닫아. 테스트 우회는 FastAPI dependency override로만 두고 운영 env flag로 열리지 않게 해.

모든 개인 repository 메서드는 명시적인 user/trip scope를 요구해. 먼저 ID로 자료를 읽고 UI에서 가리는 방식은 금지해.
다른 사용자에게 속한 여행·예약·원문은 404로 통일하고, client의 owner_id로 조회 범위를 변경하지 마.
관리자는 별도 role로 판정하되 일반 지인에게 모든 여행을 볼 수 있는 권한을 자동 부여하지 마.
기존 `/api/*`에도 같은 검사를 적용하거나 안전하게 폐기해. v2만 보호하고 구 경로를 남겨두면 미완료다.
질문·다운로드 응답 직전에도 세션 회수와 여행 삭제를 재확인해. 이후 단계의 job/SSE도 같은 dependency를 사용하게 해.

## 5. 데이터 모델과 불변 조건

SQLite는 구조화 사실의 기준 저장소이며 Chroma는 재생성할 수 있는 검색용 파생 저장소다.
연결마다 foreign keys를 켜고 WAL·제한된 busy timeout·짧은 트랜잭션을 사용해. 네트워크 호출 중 쓰기 잠금을 유지하지 마.
모든 ID는 서버가 생성하며 파일명·예약번호·이메일을 기본키로 쓰지 마. created_at/updated_at은 UTC로 기록해.
아래 필드 이름은 구현 제안이다. 같은 의미의 기존 필드가 있다면 중복 생성하지 말고 매핑을 문서화해.

| 테이블 | 필수 정보 | 불변 조건·인덱스 |
| --- | --- | --- |
| users | id, auth_provider, auth_subject, role, status, session_epoch | `(auth_provider, auth_subject)` unique |
| invitations | id, 대상, token_hash, expires_at, used_at, revoked_at | token_hash unique, 소비·사용자 연결을 같은 트랜잭션에서 수행 |
| sessions | id, user_id, token_hash, expires_at, epoch | token_hash unique, `(user_id, expires_at)` |
| trips | id, owner_id, title, start_date, end_date, conditions_json, version, active_index_id, deleted_at | `(owner_id, deleted_at, updated_at)` |
| trip_stops | id, trip_id, sequence, city, date range, IANA zone, 숙소 기준점, 추천 지원 상태 | `(trip_id, sequence)` unique |
| source_documents | id, trip_id, display_filename, opaque_path, content_hash, active_generation_id, deleted_at | `(trip_id, content_hash)` 조회, filename unique 금지 |
| document_generations | id, document_id, generation_no, parse_version, status, extracted_json | `(document_id, generation_no)` unique |
| bookings | id, trip_id, document_id nullable, stable_item_key, kind, status, version, deleted_at | 여행별 목록 인덱스, 한 문서에 복수 행 허용 |
| booking_events | id, booking_id, trip_id, event_type, 양단 local time/zone/instant, 장소 | `(trip_id, start_instant, end_instant)` |
| booking_overrides | id, booking_id, field_path, value_json, editor_id, revision, 유효 상태 | 활성 field_path unique, 과거 교정 이력 보존 |
| deletion_tombstones | target_type, target_id, trip_id, deletion_epoch, requested_at, completed_at | target unique, 이후 결과 활성화보다 우선 |

원문 추출값, 사용자 교정값, 현재 유효값을 구분하고 유효값을 만들 때 provenance를 함께 반환해.
날짜만 있으면 date로 저장하고 임의의 00:00 instant를 만들지 마. 시각이 있지만 시간대를 모르면 확인 필요로 남겨.
항공의 출발·도착 timezone은 각각 보관하고, 숙박은 숙박 기간과 체크인/체크아웃 이벤트를 구분해.
도쿄는 Asia/Tokyo, 바르셀로나는 Europe/Madrid를 사용하되 모든 여행 구간에 이 두 값만 강제하지 마.
금액은 currency와 정수 최소 단위 또는 Decimal 문자열로 저장해. JPY와 EUR의 소수 자릿수를 같다고 가정하지 마.
예약 확인 상태는 메일 근거 확인, 사용자가 직접 확인했다고 표시, 추출 내용 확인 필요를 구분해.
예약 목록에 표시되는 행과 일정에서 고정할 수 있는 확정 이벤트의 조건도 구분해. 불명확한 시각을 확정 이벤트로 만들지 마.

## 6. API 계약과 입력 검증

개인 API prefix는 `/api/v2`로 통일해. 아래 경로는 이 prefix를 생략한 표기다.
개인 응답은 `Cache-Control: private, no-store`를 적용하고, 목록은 cursor pagination과 서버 최대 page size를 사용해.
수정 요청은 `expected_version`으로 낙관적 동시성을 적용해. stale version에 조용히 덮어쓰지 마.

| method/path | 요청·핵심 동작 | 성공·주요 실패 |
| --- | --- | --- |
| GET /trips | 세션 소유 여행 cursor 목록 | 200, 인증 없음 401 |
| POST /trips | title, 날짜, party, stops, 초기 조건 | 201 + id/version, 날짜·시간대 오류 422 |
| GET /trips/{id} | 소유 여행 DTO와 확인 필요 항목 | 200 또는 404 |
| PATCH /trips/{id} | expected_version + 허용 필드 patch | 200, 버전 충돌 409 |
| DELETE /trips/{id} | 접근 차단·tombstone·정리 요청 | 202, 물리 삭제 완료와 구분 |
| POST /trips/{id}/documents | multipart files + Idempotency-Key | 202 + 파일별 accepted/rejected 및 job_id |
| GET /trips/{id}/bookings | date_from/date_to/kind/status, cursor | SQL의 현재 사실 200 |
| POST /trips/{id}/bookings | 제안 추가 경로: 수동 예약·이벤트 입력 | 201 + booking/version, source는 manual |
| PATCH /trips/{id}/bookings/{bid} | expected_version + 명시적 field patch | 200, 수정 provenance 보존 |
| DELETE /trips/{id}/bookings/{bid} | 해당 예약 숨김·tombstone·검색 정리 | 202, 같은 문서의 다른 예약은 유지 |
| GET /trips/{id}/documents/{did}/content | 인증된 원문 다운로드 | attachment, 404; HTML 실행 금지 |
| POST /trips/{id}/ask | question, history, 필요 시 stream | 200, 역할·크기 오류 422 |

여행 생성의 예시 입력은 아래와 같다. DTO 명칭 변경 시 OpenAPI와 프런트엔드를 동시에 맞춰.

```json
{"title":"도쿄 여행","start_date":"2026-11-06","end_date":"2026-11-09","party":{"adults":4,"children":[]},"stops":[{"city":"Tokyo","sequence":1,"start_date":"2026-11-06","end_date":"2026-11-09","timezone":"Asia/Tokyo"}]}
```

예약 교정은 전체 추출 JSON을 client가 교체하는 방식이 아니라 허용 필드만 수정하는 방식으로 구현해.

```json
{"expected_version":3,"changes":[{"field_path":"events.evt_example.start_local","value":"2026-11-07T19:30:00"}],"reason":"예약 변경 메일 확인"}
```

오류 DTO는 `error.code/message/request_id/retryable/details`로 통일해.
401 AUTH_REQUIRED, 404 NOT_FOUND, 422 VALIDATION_FAILED, 409 VERSION_CONFLICT를 사용하고 공급자 오류 원문을 노출하지 마.
413은 요청 크기 제한, 415는 지원하지 않는 미디어 형식처럼 transport 오류일 때 사용하고 문서화해.
날짜 필터는 각 이벤트의 timezone과 [start,end) 경계를 정의해. 숙박일·체크아웃일 표시 규칙을 테스트로 고정해.
범위 조회에 pagination이 있더라도 ‘하루 전체’ 답변 조립은 그날의 모든 결과 페이지를 읽어야 해.

## 7. 업로드·복수 예약 추출·교정의 구체적인 처리

현재 기본 1MiB/file 제한을 유지하고 요청당 파일 수와 총량을 별도 설정으로 제한해. 무제한 read 후 크기 검사하지 마.
`.txt/.eml` 허용 정책에 맞게 확장자·MIME·내용 디코딩을 함께 확인하고, 메일 내부 링크·첨부·스크립트는 실행하지 마.
브라우저가 보낸 파일명은 화면 표시 전용이다. path traversal, 동일 이름, Unicode 이름에도 서버 ID 경로를 사용해.
임시 파일 저장 → 실제 크기/해시 검증 → 비공개 원문 경로 이동 → source row 저장 순서를 정의해.
전역 content hash 중복 여부를 사용자에게 알려주지 마. 동일 여행 안의 중복만 조용히 재사용하거나 명시적 결과로 반환해.
모든 파일이 거절되면 job을 만들지 않고 422와 파일별 사유를 반환해. 일부 정상 파일은 202로 처리하고 거절 사유를 함께 보여줘.
파서 출력은 예약 배열로 변경하고 각 예약의 event 배열, 근거 snippet, unknown 필드를 엄격한 schema로 검증해.
기존 단일 예약 fixture는 adapter로 배열 한 항목으로 바꾸되 기존 테스트를 무조건 제거하지 마.
왕복 항공은 양쪽 구간을 보존하고 한 메일의 숙소+투어는 별개 예약으로 만들 수 있어야 해.
재추출 시 stable item key·예약번호·구간을 이용해 기존 행을 연결하고, 애매하면 검토 대상으로 남겨 중복 생성하지 마.
교정 overlay는 재추출 후에도 유지해. 원문이 바뀌어 사용자 교정과 충돌하면 두 값을 나란히 제시해.
파싱·임베딩 실패 시 기존 활성 예약과 검색 청크를 선삭제하지 마. 완전한 원자적 세대 교체는 02단계의 책임이다.
이번 단계에서 202 계약을 위해 SQL 작업 영수증·결과 조회를 만들 수 있지만 내구성 복구를 구현했다고 주장하지 마.
02단계가 이를 확장할 수 있도록 `enqueue_document_processing(context, document_ids, key)` 같은 명시적 경계를 둬.
소유권을 검증하지 않은 전체 data/emails 디렉터리 일괄 재색인이나 사용자 간 공용 자동 seed는 차단해.

## 8. 날짜 질문과 근거 답변

질문 처리 함수는 question뿐 아니라 user_id, trip_id, trip_version, request_id를 가진 검증된 context를 받아야 해.
history 역할은 user/assistant만 허용하고 현재 question 1,000자·history 최대 20개 제한을 유지해.
history 메시지별/전체 문자 예산을 추가하고, 초과 입력은 어떤 제한을 넘었는지 개인정보 없이 422로 반환해.
절대 날짜·N일차의 ‘하루 전체’ 요청은 SQL 범위 조회로 처리하고 의미 검색 top-k에 의존하지 마.
N일차 기준은 해당 여행의 start_date다. 다른 사용자가 설정한 시작일이나 전역 메모리 값을 읽지 마.
모호한 ‘그날’은 검증된 대화 맥락으로 해결하고 근거가 없으면 날짜 확인을 요청하는 응답을 반환해.
단일 예약번호·정확한 예약 항목 조회도 먼저 SQL 정확 조회를 사용하고, 의미 검색은 설명·정책 탐색에 보조적으로 써.
LLM에 전달하는 예약 사실은 SQL 현재 교정값으로 조립하고 오래된 청크의 시간으로 교정을 덮어쓰지 마.
출처는 답변 결과 객체에 포함해 반환해. 전역 last_sources나 공용 tool result 배열은 제거해.
일반 답변과 스트림의 최종 source DTO는 같은 계약이며 실제 사용한 여행·문서·예약 ID만 포함해야 해.
메일/웹페이지 안의 ‘이전 지시 무시’ 같은 텍스트는 자료로 구분하고 권한·도구 인수로 승격하지 마.
외부 검색에 개인 메일·예약번호·알레르기를 그대로 보내지 마. 일반 정보 질의에 필요한 도시 등만 최소 전달해.

## 9. 사용자가 보는 화면과 상태

로그인 전 화면에는 서비스 목적, 로그인, 초대 필요 상태만 보여줘. 개인 여행 제목을 미리 렌더링하지 마.
여행 목록에는 제목·기간·도시와 여행 만들기 버튼을 제공하고, 빈 상태는 예약 메일 없이도 시작할 수 있게 안내해.
여행 생성은 제목·날짜·도시·인원 정도로 시작하고 상세 취향 입력 때문에 첫 사용을 막지 마.
여행 상세 상단에 현재 여행과 기간을 고정해 다른 여행으로 전환했음을 명확히 보여줘.
예약 탭은 날짜별 목록, 종류 필터, 확인 필요 항목을 제공하고 한 문서의 복수 예약을 각각 표시해.
예약 상세는 유효값·원문 추출값·사용자 수정 여부·출처 보기·수정·해당 예약 삭제를 제공해.
교정 화면에서는 필드 오류를 입력 옆에 보여주고 취소 시 원값을 유지하며 저장 중 중복 제출을 막아.
여행 날짜 수정으로 예약이 범위를 벗어나면 영향 목록을 보여주되 예약을 자동 삭제하지 마.
업로드는 파일별 대기/처리/중복/성공/확인 필요/실패 상태와 실패 파일만 재시도하는 행동을 제공해.
질문 화면은 현재 여행 범위를 표시하고 loading/근거 없음/실패/완료를 구분해. 오류 때 사용자의 질문을 지우지 마.
세션이 만료되면 개인 DOM과 in-memory 데이터를 제거하고 로그인 복귀 경로를 제공하되 민감 입력을 URL에 넣지 마.
`web/sw.js`는 앱 셸만 캐시해. 개인 JSON·원문 다운로드·동적 개인 HTML·예약번호는 cache나 localStorage에 보관하지 마.
모바일 약 360px 폭, 긴 일본어/스페인어 이름, 키보드 tab/escape, 폼 label, 오류 focus, 삭제 dialog를 확인해.
진행 상태는 실제 처리 단계를 보여주고 아직 없는 추천·예약·배포 기능의 가짜 성공 버튼을 넣지 마.

## 10. 기존 데이터 이관과 실패 복구

원본을 읽기 전용으로 조사하는 dry-run을 만들고 파일 수·예약 수·중복·소유자 미확정·미해석 항목을 출력해.
소유자 불명 공용 자료를 첫 로그인 사용자에게 자동 배정하지 마. 명시적인 owner/trip 매핑 입력을 요구하는 도구로 준비해.
이관 전에 SQLite·원문·검색 저장소의 복구 가능한 사본을 만들고 테스트에는 합성 사본만 사용해.
이관은 버전·실행 ID를 기록해 재실행해도 중복 예약을 만들지 않게 해. 실패 항목을 건너뛰어 성공으로 숨기지 마.
구 API 호환이 필요하면 인증된 adapter로만 유지하고, 전역 삭제 endpoint는 사용자의 여행 범위 없이 실행되지 않게 해.
마이그레이션 실패 시 기존 읽기 경로를 보존하거나 안전하게 중단해. 버전 불일치 DB에 쓰기를 계속하지 마.
되돌리기는 새 사용자 데이터를 지우는 down migration보다 검증된 백업 복원과 코드 버전 복귀 절차를 우선 문서화해.

## 11. 필수 검증과 완료 판정

사용자 A/B와 각각 도쿄·바르셀로나 여행, 왕복 메일, 수동 예약, 교정 예약, 하루 8개 예약을 합성 fixture로 준비해.
AUTH-01~08, DATA-01~03, UI-01~03·UI-05·UI-07의 이번 구현 범위를 실제 테스트 경로에 연결해.
다른 사용자 여행/예약/원문 ID를 모든 구·신규 API에서 요청하면 404이며 외부 호출이 0회인지 확인해.
history system/tool, CSRF 누락, 다른 Origin, 만료/중복 초대, 세션 회수 중 답변을 각각 검사해.
동일 파일명·경로 이동 문자열·크기 초과·MIME 불일치·일부 업로드 실패의 DB와 디스크 부작용을 확인해.
복수 예약 재추출 시 교정이 유지되고 잘못된 매칭은 검토 상태로 남는지 확인해.
동시 질문 두 개의 출처·여행 시작일이 섞이지 않고 날짜 질문은 top-k=3이어도 8개와 수동 예약을 모두 보는지 확인해.
교정 저장 경쟁은 한 요청만 성공하고 다른 요청은 409이며, 실패한 파싱/임베딩은 기존 자료를 유지해야 해.
브라우저에서 로그인 → 여행 생성 → 업로드 → 상세 교정 → 질문 → 출처 → 새로고침 → 여행 전환을 실행해.
01단계에서 미완성인 job/SSE 내구성은 02단계 재검증 목록으로 남겨. 기존 테스트 통과를 신규 기능 통과로 대신하지 마.

산출물은 수정 코드, schema migration, 연결된 화면, 의미 있는 회귀 테스트, 이관 dry-run·복구 절차다.
`docs/service-v2/IMPLEMENTATION_STATUS.md`에 구현 상태와 실환경 검증 상태를 따로 기록해.
구현 상태는 not_started/partial/complete/blocked, 실환경 검증은 not_run/partial/verified/not_required로 기록해.
기록에는 날짜, code revision·미커밋 변경 여부, 주요 파일, 명령·실제 결과, 관련 인수 ID, 미실행 이유를 넣어.
실제 로그인 제공자 연동이 미검증이면 합성 E2E 통과와 분리해 보고하고 활성 기능·꺼진 기능을 명시해.
최종 답변은 사용 가능한 행동과 실행 방법, 주요 결정, 검증 결과, 남은 제약, 02단계 진입 가능 여부 순서로 작성해.
다음 단계는 `docs/service-v2/prompts/02-reliability.md`이며 이번 요청에서 후속 단계를 자동으로 전부 구현하지 마.
