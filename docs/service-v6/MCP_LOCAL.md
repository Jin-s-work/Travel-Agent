# 고잉 MCP 로컬 연결

> 내 컴퓨터에서 돌리는 고잉 앱을, 내 컴퓨터의 MCP 클라이언트가 **읽기와 여행 범위 질문**에만 쓰게 하는 방법입니다.
> 운영(Render)에서는 꺼져 있고(`PERSONAL_TOKENS_ENABLED=0`), 이 문서의 절차는 로컬 앱 기준입니다.
> PRD 10절 결정 3 = A(MCP 포함, 로컬 전용), 2026-10-21 작성.

## 한눈에 보기

| 항목 | 내용 |
| --- | --- |
| 전송 방식 | stdio (클라이언트가 `server.py`를 자식 프로세스로 띄움) |
| 인증 | 개인 접근 토큰(Bearer). 서버에는 SHA-256 해시만 저장, 원문은 만들 때 한 번만 보임 |
| 할 수 있는 것 | 여행 목록·여행·예약 목록·보관함·장소 상세·일정 목록/상세 읽기, 여행 범위 질문 |
| 할 수 없는 것 | 적용·되돌리기·삭제·발송·업로드·원문 메일 다운로드·토큰 관리·관리자 기능·오늘 보기(아래 '왜' 참고) |
| 운영 기본값 | OFF. 꺼져 있으면 Bearer 요청은 모두 401, 토큰 생성도 막힘 |

## 1. 로컬 앱 준비

로컬 앱을 평소처럼 띄우되, 토큰 기능만 켭니다.

```bash
APP_ENV=development PERSONAL_TOKENS_ENABLED=1 \
  ~/.venvs/travel-inbox-rag/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

- `PERSONAL_TOKENS_ENABLED`는 `1` 또는 `true`일 때만 켜집니다. 비어 있거나 `0`이면 꺼집니다.
- 로그인(OIDC) 설정이 되어 있어야 토큰을 만들 수 있습니다. 토큰은 로그인한 계정의 권한 범위를 넘지 않습니다.

## 2. 토큰 만들기

1. 브라우저로 로컬 앱에 로그인합니다.
2. **설정 → 개인 접근 토큰** 패널에서 이름과 만료(7일 또는 30일, 최대 30일)를 고르고 **토큰 만들기**를 누릅니다.
3. 화면에 한 번만 보이는 토큰을 복사해 파일에 저장합니다. 이 화면을 벗어나면 다시 볼 수 없습니다.

```bash
mkdir -p ~/.config/going && chmod 700 ~/.config/going
pbpaste > ~/.config/going/token && chmod 600 ~/.config/going/token
```

- 토큰은 계정당 사용 중 10개까지입니다. 안 쓰는 토큰은 같은 패널에서 **폐기**하세요(폐기 확인을 한 번 더 누름).
- 기능이 꺼진 서버에서도 목록 보기와 폐기는 됩니다.

## 3. MCP 서버 실행 확인

MCP SDK(`mcp` 2.3.0)는 앱과 분리된 가상환경 `~/.venvs/going-mcp`에만 설치되어 있습니다.

```bash
GOING_BASE_URL=http://127.0.0.1:8000 GOING_TOKEN_FILE=~/.config/going/token \
  ~/.venvs/going-mcp/bin/python tools/mcp_going/server.py
```

아무것도 출력하지 않고 stdin을 기다리면 정상입니다(Ctrl+C로 종료). 토큰은 출력하지 않습니다.

| 환경변수 | 뜻 | 기본값 |
| --- | --- | --- |
| `GOING_BASE_URL` | 로컬 앱 주소. `localhost`·`127.0.0.1`·`::1`만 허용 | `http://127.0.0.1:8000` |
| `GOING_TOKEN_FILE` | 토큰을 담은 파일 경로 | 없음 |
| `GOING_TOKEN` | 토큰 원문(파일 대신). 셸 기록에 남기 쉬워 파일을 권장 | 없음 |

## 4. 클라이언트 설정 예시

`<저장소>`는 이 저장소의 절대경로, `<홈>`은 홈 디렉터리로 바꿉니다.

**Claude Desktop** (`claude_desktop_config.json`의 `mcpServers`)

```json
{
  "mcpServers": {
    "going": {
      "command": "<홈>/.venvs/going-mcp/bin/python",
      "args": ["<저장소>/tools/mcp_going/server.py"],
      "env": {
        "GOING_BASE_URL": "http://127.0.0.1:8000",
        "GOING_TOKEN_FILE": "<홈>/.config/going/token"
      }
    }
  }
}
```

**Claude Code** [미확인 — 클라이언트 CLI 문법은 설치된 버전의 `claude mcp add --help`로 확인]

```bash
claude mcp add going \
  -e GOING_BASE_URL=http://127.0.0.1:8000 -e GOING_TOKEN_FILE=<홈>/.config/going/token \
  -- <홈>/.venvs/going-mcp/bin/python <저장소>/tools/mcp_going/server.py
```

## 5. 도구 목록

읽기 도구 7개는 `readOnlyHint=true`입니다. `ask_trip`은 여행 데이터를 바꾸지 않지만 답변 예산을 쓰고 재색인 작업을 예약할 수 있어 `readOnlyHint=false`, `idempotentHint=false`로 광고합니다(클라이언트가 확인 없이 자동 반복 실행하지 않도록). `ask_trip`의 응답 대기 시간은 120초(`ASK_TIMEOUT`)로, 에이전트 chat 경로의 최악 처리 시간(deadline 25초 + 공급자 호출 45초)보다 깁니다. 엄밀한 상한은 아닙니다: `search_mail`의 임베딩은 에이전트 deadline에 묶이지 않아(시도당 45초, 429면 최대 3회) 느린 호출이 겹치면 120초를 넘을 수 있습니다. 그래도 시간이 넘으면 `NETWORK_ERROR`(앱 미실행)가 아니라 `ASK_TIMEOUT`을 돌려줍니다 — 서버에서 답변이 계속 처리돼 비용이 들었을 수 있으니 같은 질문을 바로 다시 보내지 말고 앱에서 먼저 확인하세요.

광고하는 입력 스키마는 `going_tools.TOOLS`의 스키마 그대로입니다(날짜 `pattern`, 길이 상한, `additionalProperties: false`). 서버도 같은 스키마로 다시 검사합니다.

| 도구 | 호출하는 API |
| --- | --- |
| `list_trips` | `GET /api/v2/trips` |
| `get_trip` | `GET /api/v2/trips/{trip_id}` |
| `list_bookings` | `GET /api/v2/trips/{trip_id}/bookings` (날짜 범위 선택) |
| `list_saved_places` | `GET /api/v2/trips/{trip_id}/bookmarks` |
| `get_place` | `GET /api/v2/trips/{trip_id}/places/{place_id}/detail` |
| `list_itineraries` | `GET /api/v2/trips/{trip_id}/itineraries` |
| `get_itinerary` | `GET /api/v2/trips/{trip_id}/itineraries/{id}` — 항목·버전·검증 상태·충돌 코드만 돌려주고, 숙소·출발점 좌표와 라벨(`snapshot`, `legs`)은 보내지 않습니다 |
| `ask_trip` | `POST /api/v2/trips/{trip_id}/ask` |

질문 답변에 일정 변경 미리보기가 붙어도, MCP로는 적용할 수 없습니다. 적용은 앱 화면에서 사용자가 직접 누릅니다.

## 왜 이렇게 만들었나

- **허용 목록 방식**: 서버(`src/foundation/tokens.py`의 `ALLOWED_ROUTES`)가 메서드+경로 템플릿으로 허용한 읽기 GET 7개와 `POST ask`만 Bearer를 받습니다. 목록에 없는 경로는 쓰기든 읽기든 403 `TOKEN_SCOPE_REJECTED`입니다. 새 API가 생겨도 기본은 거부입니다.
- **오늘 보기(`GET /today`)를 뺀 이유**: 이 GET은 예약 준비 항목을 다시 검증하면서(`preparation.list` → `_refresh`) 근거가 낡았으면 `reservation_tasks`를 UPDATE하고 `reservation_task_events`에 INSERT합니다. 사용자 조작이 아닌 시스템 재검증이지만 '토큰 GET은 읽기만 한다'는 계약과 맞지 않아 허용 목록에서 뺐습니다. 허용된 GET 7개를 불러도 토큰 테이블 외 모든 테이블이 그대로인지는 `tests/test_v6_personal_tokens.py`가 고정합니다.
- **토큰 요청이 남기는 쓰기**: 토큰 테이블의 `last_used_at`(분당 최대 1회 갱신), 그리고 `ask`일 때 기존 질문 경로가 늘 하는 답변 예산·사용량 기록과 재색인 작업 예약. 토큰으로 묻는 질문의 에이전트는 읽기 도구만 받아 미리보기·초안 행을 만들지 않습니다.
- **프록시를 쓰지 않음**: MCP 클라이언트가 `HTTP_PROXY`/`HTTPS_PROXY` 환경변수를 넘겨도 `going_client.py`는 프록시를 거치지 않고 루프백 주소로 직접 연결합니다(그렇지 않으면 토큰이 프록시로 나갑니다).
- **쓰기는 쿠키 + CSRF + Origin만**: 토큰 요청은 `write_guard`를 통과할 수 없고, 쿠키와 Bearer를 같이 보내도 Bearer가 우선해 쓰기가 막힙니다. CSRF 값이 빈 문자열이면 항상 거부합니다(`compare_digest('', '')` 통과 구멍 차단).
- **내부 세션 행**: 토큰마다 쿠키로는 쓸 수 없는 내부 `sessions` 행을 둡니다. 서비스 계층의 세션 확인(`jobs._user`, `documents.ensure_active_user`)을 바꾸지 않아도 되고, 사용자 비활성화·권한 변경·복원처럼 세션을 회수하는 기존 동작이 토큰도 즉시 끊습니다.
- **토큰별 분당 상한**: 모든 요청 60회/분, 질문 10회/분(메모리 슬라이딩 윈도우). 넘으면 429 `TOKEN_RATE_LIMITED`.
- **로직과 SDK 분리**: `going_client.py`·`going_tools.py`는 `mcp`를 import하지 않아 앱 가상환경에서 가짜 API로 시험합니다. `server.py`만 SDK를 씁니다.

## 한계와 알려진 문제

- **로컬 전용**: `GOING_BASE_URL`은 루프백만 받습니다. 원격 서버 연결, HTTP(SSE·streamable-http) 전송은 만들지 않았습니다.
- **분당 상한은 프로세스 메모리**: 앱을 재시작하면 초기화되고, 여러 워커로 띄우면 워커마다 따로 셉니다(운영은 1 워커 전제).
- **질문은 완전한 읽기가 아님**: `ask`는 답변 예산을 쓰고, 검색 인덱스가 낡았으면 재색인 작업을 예약할 수 있습니다(사용자 데이터는 바꾸지 않음).
- **세션 회수 후 복구 없음**: 사용자 비활성화·권한 변경·백업 복원 뒤에는 기존 토큰이 '세션 회수로 중지' 상태가 되며 다시 살아나지 않습니다. 새로 만들어야 합니다. 이렇게 멈춘 토큰은 '사용 중 10개' 개수에 들어가지 않습니다.
- **응답 크기**: 도구 결과는 20,000자에서 자르고, 메일 발췌·환불 규정 같은 긴 글은 600자로 줄입니다.
- **프로토콜 버전 [미확인]**: 설치된 SDK(`mcp` 2.3.0, `mcp_types.version`)는 핸드셰이크 버전 `2024-11-05`~`2025-11-25`와 `2026-07-28`을 압니다. 로컬 확인(2026-10-21)에서는 `initialize`에 `2025-11-25`를 보내 같은 값을 받았습니다. 공식 스펙 저장소의 최신 버전과 클라이언트별 지원 범위는 확인하지 않았습니다.
- PostgreSQL 경로의 토큰 테이블 생성은 코드만 넣었고, 실제 PostgreSQL에서는 시험하지 않았습니다.
