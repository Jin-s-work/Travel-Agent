# v6 2~4단계 출시 체크리스트

> 운영(Render + Supabase)에 v6 1~4단계 코드를 배포하고, 4단계 라벨에 필요한 공개 지도 후보를 채우기까지의 순서다. 배포·환경값 변경·운영 화면 조작은 모두 사람이 한다(사규 ①). AI는 이 문서와 확인 명령까지만 준비했다.

2026-10-22 · 기준 커밋 `codex/private-beta-launch` HEAD · 운영에 마지막으로 배포된 커밋 `d154dc0`(docs/operations/OPEN_REGISTRATION_2026-10-08.md) · 1단계 체크리스트는 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)

**세 줄 요약**

- 운영은 아직 `d154dc0`이라, HEAD를 배포하면 v6 1~4단계가 한 번에 들어간다. 이미지 경로 108파일, schema 15 그대로, `Dockerfile`·`requirements.lock`·`render.yaml` 변경 없음.
- 새 유료 기능(에이전트·날씨·개인 토큰)은 운영 기본값이 꺼짐이다. 배포 즉시 바뀌는 사용자 동작은 질문 답변의 근거 검증과 유사도 하한(0.20)이다.
- 라벨 후보는 배포 뒤 **추천을 실제로 한 번 돌려야** 들어온다. 공개 지도 후보는 7일 뒤 만료되므로, 채운 뒤 7일 안에 카탈로그를 다시 내보내야 한다.

> **진행 상황:** 2026-10-23 대표가 `767711c` 배포 완료. 공개 자산 hash 14개 일치·`/health/ready` 200 확인(AI, 로그인 없이). 다음은 4절 로그인 확인과 5절 후보 채우기.

## 1. 배포 전 — 사람 (10분)

| 확인 | 기준 | 방법 |
| --- | --- | --- |
| 원격 CI | 배포할 커밋의 Actions 7/7 통과 | `gh run list --branch codex/private-beta-launch --limit 1` |
| 배포 대상 | Render 서비스가 `codex/private-beta-launch`에 연결, Auto-Deploy Off | docs/operations/DEPLOYMENT_HANDOFF.md |
| Supabase | 프로젝트 상태 Healthy (일시정지 중이면 기동 마이그레이션이 실패한다) | Supabase 대시보드 |
| 백업 | 배포 전 무료 백업 1회. 기동 마이그레이션과 겹치지 않게 **배포 전에** 한다 | docs/operations/RENDER_SUPABASE.md |

## 2. Render 환경값 — 사람

`.env.render`를 다시 만들 일이 있으면 반드시 `--profile openai`로 만든다(스크립트 기본은 `free`라 운영값을 덮어쓴다). 아래는 Render Environment에서 확인할 이름만 적었다. 값이 비어 있으면 코드 기본값을 쓴다.

| 이름 | 운영에서 둘 값 | 비워 두면 | 이유 |
| --- | --- | --- | --- |
| `TRIP_AGENT_ENABLED` | 비워 둠 | 꺼짐(`APP_ENV=production`) | 켜기 전 확인 항목이 남아 있다(6절) |
| `WEATHER_ENABLED` | `0` | 꺼짐 | Open-Meteo 비상업 조건·공유 IP 한도 미확인 |
| `PERSONAL_TOKENS_ENABLED` | `0` | 꺼짐 | 로컬 MCP 전용으로 결정(PRD 결정 3 = A) |
| `PUBLIC_DISCOVERY_ENABLED` | 비워 두거나 `true` | **켜짐** | 꺼 두면 5절 후보 보강이 안 된다 |
| `CANDIDATE_EXPANSION_ENABLED` | 비워 두거나 `true` | 켜짐 | 7개 지역 확장 조회. 후보 보강에 필요 |
| `PHOTO_COLLECTION_ENABLED` | 의도대로 | 켜짐 | 1단계에서 생긴 이름. 사진 수집을 원치 않으면 `false` |
| `ANSWER_MIN_SIMILARITY` | 비워 둠 | `0.20` | 2026-10-22 대표 결정 |
| `MAIL_EXTRACTION_CHAIN`·`ANSWER_PROVIDER`·`EMBEDDING_PROVIDER`·`AGENT_PROVIDER` | 비워 둠 | `openai` | `anthropic`으로 두면 안 된다. SDK가 이미지에 없다 |
| `OSM_TAG_ENRICHMENT_ENABLED` · `OSM_OVERPASS_ENDPOINT` | 무료로 시도하려면 `1`, 주소는 비워 둠 | 꺼짐 | `767711c` 이후 추가(배포 필요). 공개 Overpass 는 한가할 때만 태그를 채운다(2026-10-23 측정: 3곳 모두 과부하). 실패하면 그 프로세스는 15분 동안 Overpass 를 건너뛰므로, 느릴 때 지역 조회가 줄어드는 손해는 15분에 한 번 8초로 묶인다. 켠 뒤 카탈로그를 다시 내보내 `public_map/*` 후보에 태그가 생겼는지 확인한다 |

새 이름과 기본값은 `.env.example` 끝의 "v6 stages 2-4" 절에도 적었다. `scripts/prepare_render_env.py`는 이 이름들을 검증하지 않는다(`PERSONAL_TOKENS_ENABLED`·`WEATHER_ENABLED`만 `0`으로 넣는다).

## 3. 배포 — 사람

1. Render 대시보드에서 1절에서 확인한 커밋을 **수동 배포**한다(`render.yaml`은 `autoDeployTrigger: off`).
2. 기동 때 새 테이블 2개가 생긴다. 둘 다 `CREATE … IF NOT EXISTS`라 다시 기동해도 안전하다.

| 테이블 | 용도 | 비고 |
| --- | --- | --- |
| `llm_calls` (인덱스 2) | 유료 호출 지연·결과 계측, 관리자 p50/p95 | 값·원문 없음 |
| `personal_access_tokens` (인덱스 1) | 개인 토큰 hash | 토큰 기능이 꺼져 있으면 비어 있다 |

schema 버전은 15 그대로다. 기존 표에 열 추가·변경은 없다. 기동 마이그레이션이 실패하면 서비스가 뜨지 않는다. 그때는 8절 롤백으로 간다.

## 4. 배포 직후 확인 — 사람 (10분)

```bash
curl -fsS https://travel-inbox-rag.onrender.com/health/ready
```

| 확인 | 기대 |
| --- | --- |
| `/health/ready` | 200 (유료 API를 부르지 않는다) |
| 본인 Google 로그인 → 마지막 여행 | 다른 사용자 자료가 보이지 않음 |
| 질문하기에서 날짜로 묻기 ("11월 7일 일정 알려줘") | 기존처럼 구조화 답 |
| 질문하기에서 메일 내용 묻기 | 답 아래 "근거 N건 보기". 근거 검증을 통과하지 못한 생성 답은 예약 사실 요약으로 바뀌고, 근거가 약하면 "메일에서 확인하지 못했어요"로 거절할 수 있다(새 동작) |
| 관리자 계정으로 `GET /api/v2/admin/operations` | `observations.llm_calls`에 7일 호출 지연 p50/p95(호출이 없으면 빈 값). 화면에는 아직 표시하지 않는다 |
| 질문하기에 에이전트 미리보기 카드 | **나오지 않아야 한다**(에이전트 꺼짐) |

## 5. 라벨용 공개 지도 후보 채우기 — 사람 (도시당 5분, 이틀에 걸칠 수 있음)

공개 지도(OSM) 후보는 **로그인한 사용자의 추천 실행 중에만** 가져온다. 정기 작업이나 스크립트는 없다(src/recommendations/service.py). 그래서 배포만 해서는 카탈로그가 늘지 않는다.

| 한도 | 값 |
| --- | --- |
| 사용자당 하루 지도 호출 | 5회 (UTC 날짜 기준) |
| 전역 하루 지도 호출 | 20회 |
| 호출 간격 | 15초 |
| 한 번에 저장 | 최대 60곳 |
| 공개 지도 묶음 유효 기간 | 7일 |

1. 운영 화면에서 도쿄 여행을 열고, 탐색 조건에 **식당**과 **현지 발견**이 들어간 상태로 추천을 1번 실행한다(기본 조건이 이미 그렇다).
2. 추천 결과에 공개 지도 장소가 보이는지 확인한다. 한 지역만 성공해도 최대 60곳이 저장된다.
3. 바르셀로나는 **다음 UTC 날짜(한국 시간 오전 9시 이후)에** 같은 계정으로 하거나, 같은 날 다른 계정으로 한다. 한 도시가 하루 5회를 다 쓰면 두 번째 도시는 `PUBLIC_DISCOVERY_DAILY_LIMIT`로 막힌다.
4. 공급자 거절이 나오면 15분 이상 기다렸다 다시 한다.

## 6. 카탈로그 다시 내보내기 — 사람 → 그 뒤는 AI

5절을 마친 날로부터 **7일 안에** 실행한다. 7일이 지나면 공개 지도 후보가 만료돼 다시 0이 된다.

```bash
cd ~/Desktop/ChatGPT/travel-inbox-rag
```

```bash
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python scripts/export_v6_label_catalog.py --env deploy/render-supabase/.env --output data/recommendation-labels/catalog-$(date +%F).json
```

`counts`에서 도시별 후보가 **5곳 이상**이면 된다. 실패하면 출력의 `failure_kind`를 본다(`tenant_or_project`면 Supabase 일시정지). 결과를 알려 주면 AI가 질의 확정 → pool 동결 → AI 임시 라벨("사람 라벨 아님" 표시) → 평가 → 보고서까지 이어서 한다. 최종 결정은 사람 라벨이 들어온 뒤에 한다.

## 7. 이번에 켜지 않는 것 — 켜기 전 조건

| 기능 | 켜기 전에 할 일 | 근거 |
| --- | --- | --- |
| 여행 에이전트 | agent live 평가(OpenAI strict schema 수용 확인 포함), `provider_stopped` 화면 문구 확인, 질문당 비용 상한(`AGENT_MAX_COST_MICROS`, 지금 값은 [가설]) 결정 | STAGE3.md, IMPLEMENTATION_STATUS.md |
| 날씨 | Open-Meteo 비상업 해당 여부, Render 공유 IP 한도 확인. 실내·실외 태그가 없어 "확인된 야외 제외"는 운영에서 작동하지 않는다 | STAGE3.md |
| 개인 토큰·MCP | 토큰 허용 목록 재검토. 운영에서는 쓰지 않기로 결정됨 | PRD 결정 3 = A |
| 저장 기반 취향 | 화면 토글이 없고 요청 필드 기본값이 꺼짐. 라벨 비교 뒤 결정 | STAGE4.md |

알려진 경계: 429 재시도가 있으면 에이전트 비용 장부가 질문당 상한을 넘을 수 있고(합성 재현 25000 → 31020), 이미 나간 호출 하나는 최대 45초까지 기다린다.

## 8. 롤백 — 사람

| 상황 | 조치 |
| --- | --- |
| 기동 실패 · `/health/ready` 실패 | Render에서 직전 배포 이미지(`d154dc0`)로 되돌림. schema가 15로 같아 이미지 롤백이 된다. 새 테이블 2개는 남지만 이전 이미지는 읽지 않으므로 무해하다 |
| 질문 답변 동작 문제 | `ANSWER_MIN_SIMILARITY`를 낮추는 것만으로는 근거 검증이 꺼지지 않는다. 이미지 롤백이 확실한 방법이다 |
| 후보 보강·사진 문제 | `CANDIDATE_EXPANSION_ENABLED`·`PHOTO_COLLECTION_ENABLED`를 `false`로 두고 재시작 |
| 데이터 문제 | 이미지 롤백과 DB 복원은 별개. docs/operations/RENDER_SUPABASE.md 복원 절차 |

## 배포 차이 — `d154dc0` 대비

`git diff d154dc0 HEAD`로 계산했다(2026-10-22, 이 문서를 더하기 직전 HEAD `20d80d9` 기준).

| 항목 | 값 |
| --- | --- |
| 이미지 경로 (`api.py` · `src` · `web` · `Dockerfile` · `requirements.lock` · 가격·인증서) | 108파일 (추가 45 · 수정 63), +11,501 / −564줄 |
| `Dockerfile` · `requirements.lock` · `render.yaml` | 변경 없음 |
| schema 버전 | 15 → 15. 새 테이블 2개(`IF NOT EXISTS`) |
| 새 설정 이름 | 1단계 9개(RELEASE_CHECKLIST.md) + 2~4단계 서버 설정 11개(`.env.example` 끝 절). 로컬 MCP·평가 전용 이름(`GOING_*`·`LLM_REPLAY_DIR`·`ANTHROPIC_API_KEY` 등)은 서버와 무관해 빼고 셌다 |

## 확인하지 않은 것

- 실제 Render 환경값(지금 운영이 openai 프로필인지): 저장소의 `render.yaml`과 인계 문서는 openai 쪽을 가리키지만 대시보드는 보지 않았다 [미확인].
- Photon이 도쿄·바르셀로나에서 실제로 몇 곳을 돌려줄지 [미확인]. 2026-10-08 도쿄 공개 후보 12곳 기록이 있다(README).
- 운영 DB 계정의 `CREATE TABLE` 권한: 기존 마이그레이션과 같은 권한이라 문제없다고 추정 [미확인].
