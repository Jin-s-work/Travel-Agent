# 고잉 v6 진행 기록

> v6 단계별 구현의 단일 최신 상태표. 과거 보고서는 당시 사실을 보존하고, 이 표만 갱신한다.

최종 갱신 2026-10-21 · 2·3·4단계 실행·최종 리뷰 반영 후(4단계는 라벨 대기에서 멈춤) · 기준 브랜치 `codex/private-beta-launch`. 이 기록 작성 시점 HEAD 5a4443a(1단계까지 커밋), **2·3·4단계 산출물은 이 기록 작성 시점(2026-10-21) 미커밋(작업 트리의 수정·미추적 파일)** · 같은 날 로컬 커밋 5개로 나눔(코드 4 + 문서 1, push 없음 — 목록은 IMPLEMENTATION_STATUS 「커밋」 절) · push·배포·live 유료 호출 없음 · 2026-10-22: 대표가 `f5b2641` 까지 push, 원격 Actions 7/7 통과(「원격 CI」)

**세 줄 요약**

- 1단계(보존·CI·E2E·결함)는 로컬 커밋까지 끝났다. 2단계는 합성 정답셋 35건·벤더 중립 어댑터(OpenAI 바이트 동일·Replay·Anthropic 가짜 시험)·수리 1회와 폴백·근거 검증 답변·`llm_calls` 계측·`make ci-eval` 게이트를 만들었다.
- 3단계는 읽기·미리보기·초안만 쓰는 tool-calling 에이전트(도구 10개, 상한 5단계·8호출·25초 — search_mail 임베딩도 25초 deadline을 이어받음(후속))·날씨·Plan B 개선·질문 화면 카드·E06·개인 토큰·로컬 MCP를 만들었다. 운영 기본값은 에이전트·날씨·토큰 모두 OFF다.
- 4단계는 영업시간 파서·볼거리·블라인드 라벨 CLI·pooled 평가·피드백 루프·리뷰 파일럿 준비까지 했고 **사람 라벨을 기다린다.** 모든 평가 숫자는 synthetic이며 live 평가·운영 DB 읽기·라벨은 대표가 실행한다. 최종 리뷰 지적 23건을 처리한 뒤 전체 시험은 실패 0이고, PostgreSQL 시험은 미실행이다.

## 단일 최신 상태

열의 뜻: code = 코드 존재 · synthetic = 합성 자료로 자동 시험 통과 · browser = 실제 브라우저에서 확인 · live = 실제 외부 공급자·운영 자료 · 배포 = 운영 URL 반영.

| 영역 | code | synthetic | browser | live | 배포 |
| --- | --- | --- | --- | --- | --- |
| A 작업 보존 | 로컬 커밋 11 + 1단계 커밋 + 원격 병합 `27bdfaa` | 해당 없음 | 해당 없음 | 해당 없음 | push 안 함 · fast-forward 가능 |
| A 게시 안전 점검 | `check_publish_safety.py` | 시험 22개 통과 · 저장소 비밀 0 | 해당 없음 | 해당 없음 | 해당 없음 |
| B CI | `ci.yml` 7 job(1단계 6 + 2단계 `eval`) · `make ci` | `make ci` 로컬 통과: Python 1302 통과 · 20 skip, JS 220, 자산·게시 점검 (1단계 당시. 최종: Python 1890 통과 · 30 skip, JS 233 — 「최종 전체 시험」) | 해당 없음 | Actions 미실행 | 해당 없음 |
| B PostgreSQL | 기존 plugin + CI job | 일회용 pgvector 0.8.7/PG17: plugin 282 통과 · 자체 schema 16 통과 (1단계 당시, 후속 3에서 같은 숫자로 재확인 — 「PostgreSQL 시험」) | 해당 없음 | 운영 DB 미사용 | 해당 없음 |
| C E2E 서버·로그인 | 빈 포트 fixture · 합성 OIDC | HTTP 2개 통과 | Chromium에서 A·B 로그인 | 해당 없음 | 해당 없음 |
| C E2E 브라우저 E01~E05 | Playwright 1.63.0 · CI `e2e` job | 합성 fixture | E01~E05 + E01b 6개 통과, 스크린샷 10장 (최종 `make e2e` 12개 통과 = HTTP 2 + E01~E05·E01b 6 + E06 4) | 해당 없음 | Actions 미실행 |
| D 이동시간(G4) | 비활성 제공자 → 같은 도시 3km 이하 도보 추정 | 단위 1 · API 1 · JS 1 통과 | E01에서 "직선거리 기준 추정" 표시 확인 | 경로 공급자 호출 0 | 미배포 |
| D PG 리뷰 계약 SQL | `observation_profile` 번역 추가 | 번역 시험 2 + PG 계약 재실행 (1단계 당시. 최종 리뷰 후 PG 미실행 — 「최종 전체 시험」) | 해당 없음 | 운영 미확인 | 미배포 |
| D 로드 중 탭 선택 (D4) | `navigate()` override | JS 1 통과 | E01 · E01b 통과 | 해당 없음 | 미배포 |
| E 메모리 | 측정 스크립트 | macOS · Docker 512MB 측정 | 해당 없음 | 실제 Render 미측정 | 해당 없음 |
| E 출시 체크리스트 | 문서 | 해당 없음 | 해당 없음 | 해당 없음 | 실행 주체 전부 사람 |
| 2-A 정답셋 `mail_gold_v1` | 35건 · 예약 45 · 검증기 | 전부 synthetic | 해당 없음 | 해당 없음 | 해당 없음 |
| 2-B 메일 평가기 | `src/evals/mail.py` local·replay·live · 정답 sha256 고정 | local F1 0.8018 · 예약 수 0.8571 · forbidden 0(정답셋 forbidden 14개 중 `deposit_as_total` 2건 측정 불가 exact 고정 → 측정 가능 12개, 그중 syn-04·syn-12는 추출기 오류 사례라 출력과 대조된 것은 10개) / replay F1 0.761(파이프라인 시험) · 시험 27 | 해당 없음 | live 미실행(대표, 상한 필수) | 해당 없음 |
| 2-C 프롬프트 레지스트리 | `src/prompts/` 4개 + 서명 v2 | 바이트 동일·v1 체크포인트 재개 시험 12 | 해당 없음 | 해당 없음 | 미배포 |
| 2-D 벤더 중립 gateway | `src/llm/` OpenAI·Replay·Anthropic | OpenAI 요청·영수증 HEAD와 바이트 동일, Anthropic 가짜 클라이언트 · 시험 36 | 해당 없음 | 실제 호출 0, Anthropic SDK 미설치 [미확인] | 미배포 |
| 2-E 수리·폴백 | 수리 1회 → auto 로컬 → 확인 필요 | 수리 13 · 폴백 10 시험(collect-only 확인), 기존 예약·교정값 보존 | 해당 없음 | 해당 없음 | 미배포 |
| 2-F 근거 검증 답변 | `grounding.py` · 하한 0.20 | 질문 53문항 근거 없는 원자 0 · 다른 여행 노출 0 (local 답변 가능 1.0 · 거절 재현율 0.45 / replay 0.9032 · 1.0) · 시험 근거 63 · qa 36 | 해당 없음 | 실제 임베딩 하한 미측정 | 미배포 |
| 2-H 계측·게이트 | `llm_calls` · 운영 요약 p50/p95 · `ci-eval` · eval job | `ci-eval` gate pass(2026-10-21 최종) · 정답·질문·fixture sha256 고정 · 고의 회귀에서 gate 실패 · 시험 gate 22 · 운영 집계 11(PG 1 skip) | 해당 없음 | PG 미실행, Actions 미실행 | 미배포 |
| 3-A·B 에이전트 | 도구 10 · 상한 루프 · 근거 검증 · 빠른 경로 유지 · 과금 불확정 오류는 `provider_stopped` 부분 답변 · 비용 상한을 보내기 전 검사 (최종 리뷰 후: 장부상 합계는 429 재시도 시 상한 초과 가능 · search_mail 임베딩은 25초 deadline 밖 — 「다음 실행 조건」) | 금지 도구 부재·AI OFF 동일·예산 동일 시험 · 루프 18 · 도구 9 · API 39 | 해당 없음 | live 시나리오 미실행 | 미배포, 운영 `TRIP_AGENT_ENABLED` 미설정=OFF |
| 3-C·F 미리보기 카드 | 질문 화면 카드 · 확인 범위 줄 | JS 시험 · 질문 전후 일정 불변 | E06·E06b·E06c·E06d 통과(합성) | 해당 없음 | 미배포 |
| 3-D 날씨 · Plan B | Open-Meteo(도시 공개 좌표) · 점수·거리 정렬 · `indoor_likely` | 가짜 fetcher 시험 · Plan B 회귀 · 날씨 32 · Plan B 11 | CC BY 4.0 링크 | Open-Meteo 실호출 0 | 미배포, 운영 `WEATHER_ENABLED=0` |
| 3-E 안전 평가 | `evals/agent/` 53 시나리오(후속 `ps-01~03` 포함) · `ci-eval` 게이트 | 금지 도구 실행(시도 12)·무단 적용·노출·주입(텍스트·링크 포함)·근거 없는 원자·단언 실패 0, 평균 2.708단계 (replay, 손으로 쓴 스크립트) · 시험 36 | 해당 없음 | live 미실행 | 해당 없음 |
| 3-G 개인 토큰 · MCP | hash 저장 토큰 · Bearer 허용 목록 · `tools/mcp_going/` · ask 120초(서버가 강제하는 상한은 아님) · get_itinerary 좌표 미노출 | 토큰 22 · MCP 10 시험 | 해당 없음 | 실제 MCP 클라이언트 연결 미실행 | 미배포, 운영 `PERSONAL_TOKENS_ENABLED=0` |
| 4-A 무료 후보 보강 | 영업시간 파서 · 볼거리(reference) · 범위 보고 스크립트 | 파서 95 · 볼거리 16 · 범위 5 시험 (2026-10-21 `pytest --collect-only`) | 해당 없음 | 실제 Photon·운영 DB 미실행(과거 값만) | 미배포 |
| 4-B 라벨 도구 | 블라인드 CLI · pooled · `judged@5` | 라벨 시험 21(collect-only 확인) · 합성 smoke | 해당 없음 | **사람 라벨 0건(라벨 대기)** | 해당 없음 |
| 4-C 평가·결정 | — | 라벨 전 미측정 | 해당 없음 | 라벨 후 | 기본 모델 hybrid_v5 그대로 |
| 4-D 피드백 루프 | 덜 보기·제외 범위·철회 · 저장 기반 취향(기본 OFF) · 가까운 순서 · 알려진 한계 1(privacy-2) | 피드백 시험 18 · 한계 2 · JS 시험 | 해당 없음 | 온라인 효과 미측정 | 미배포 |
| 4-E 리뷰 파일럿 준비 | 언어 라벨 CLI · Wilson 95% | 시험 27 | 해당 없음 | 원격 run 0, 상한 0 | 해당 없음 |

> [!IMPORTANT]
> 2~4단계 시험 수는 2026-10-21 `pytest --collect-only` 값이다(`tests/test_v6_*.py` 25개 파일 589개, 최종 실행 588 통과 · 1 skip).
> "synthetic 통과"는 합성 사용자 A/B, 임시 DB, 가짜 공급자 기준이다. 실제 Google 로그인, 실제 경로 시간, Render 메모리, 운영 Supabase 동작은 확인하지 않았다.

## 최종 리뷰

최종 리뷰 지적 23건은 확정 18 · 의견 갈림 4 · 반박 1로 갈렸고, 확정 18건은 모두 고쳤다(코드 9 + 문서 9). 6개 관점(billing·security·contracts·evaluation·privacy·honesty)에서 지적을 모은 뒤 지적마다 반박 검증 2표를 받았다. 수정마다 원래 재현 시나리오를 다시 돌리는 적대적 재검증을 통과했다.

| 단계 | 지적 (확정·갈림·반박) | id | 처리 |
| --- | --- | --- | --- |
| 3단계 | 6 (4·2·0) | B1 · B3 · B6 · SEC-1 · C1 · privacy-1 | 6건 모두 수정. B1은 과금 불확정 오류를 루프의 `provider_stopped` 부분 답변으로 멈춤, B3은 비용 상한을 보내기 전에 검사 |
| 4단계 | 1 (0·1·0) | privacy-2 | 코드 변경 없이 한계로 문서화([FEEDBACK_LOOP.md](FEEDBACK_LOOP.md) 알려진 한계) |
| 평가 | 6 (5·1·0) | B2 · B4 · EVAL-1~4 | 5건 수정, EVAL-3은 한계로 문서화(`evals/qa/README.md`) |
| 문서 | 9 (9·0·0) | H1~H9 | 보고서·README 숫자·주장 불일치 9건 정정 |
| 반박 | 1 (0·0·1) | B5 | 수정 안 함. 관리자 LLM 집계가 전송 전에 멈춘 시도를 blocked로 세는 것은 집계 정의와 맞다는 판정 |

## 최종 전체 시험 (2026-10-21)

최종 리뷰 반영 뒤 작업 트리에서 돌린 전체 시험은 실패 0이고, PostgreSQL 시험만 미실행이다. 모두 `PYTHON_DOTENV_DISABLED=1`, `TRAVEL_TEST_POSTGRES_DSN=`(SQLite), 로컬 가상환경에서 돌렸다.

| 명령 | 결과 | 시간 |
| --- | --- | --- |
| `make ci-python` | 1890 passed · 30 skipped · 0 failed (경고 3: 라이브러리 지원 중단 2, FastAPI 중복 Operation ID 1 — 기존) | 8분 5초 |
| `make ci-js` | 233 tests · 233 pass · 0 fail | 77초 |
| `make ci-assets` | Public asset content hashes match index.html | 1초 미만 |
| `make ci-eval` | gate pass · failures 0 · synthetic | 23초 |
| `scripts/check_publish_safety.py --untracked` | 0 fail · 68 warn (absolute_user_path 63, db_url_loopback_password 5) — 모두 이번에 바꾸지 않은 v2·v3·v4·발표 자료 문서·로그(이미 원격에 있는 추적 파일: service-v2 35 · service-v4 28 · service-v3 3 · presentation 2). 수정 추적 파일 48 + 미추적 210 만 넘기면 0 fail · 0 warn. 기본 모드는 경고만 있어도 rc=1 | 1초 |
| `make ci-safety` | 0 fail · 68 warn, rc 0 | 1초 미만 |
| `GOING_E2E=1 make e2e` | 12 passed | 28초 |
| `make ci-postgres` / PG 시험 | 미실행 — Docker(Colima) 꺼 둠(대표 지시). `test_v6_admin_llm_ops`의 PG 1건 skip | — |

- **PostgreSQL 미실행 이유:** 대표 지시로 Docker(Colima)를 꺼 두어 일회용 PG를 띄우지 않았다. 그래서 2~4단계의 PG 경로는 확인하지 못했다(같은 로직을 SQLite로만 시험했고, C1은 dict 행을 흉내 낸 시험).
- **v6 시험 묶음:** `tests/test_v6_*.py` 25개 파일 588 통과 · 1 skip(PG) · 0 실패(4분 21초).
- **같은 날 앞선 실행:** 최종 리뷰 전 `ci-python` 첫 실행에서 8건 실패. 7건은 iCloud가 내려받지 않은(dataless) 파일을 자식 프로세스가 읽다 시간 제한에 걸린 환경 원인, 1건은 기존 간헐 실패(`test_v5_concurrent_workload`, 409 vs 202). 파일을 내려받은 뒤 재실행 0 실패. 시험 기대값·시간 제한은 바꾸지 않았다.

## 결정 반영

| 항목 | 결정 | 반영 |
| --- | --- | --- |
| PRD 10절 결정 1 이동시간 | A (2026-10-20 대표) | 반영. strict는 기존대로 미확인 처리, 잠정안에서만 배치, 화면 "직선거리 기준 추정" |
| 결정 2 Anthropic | A | 인터페이스·가짜 응답 시험까지. 가격 파일에 없어 호출 전 차단 |
| 결정 3 MCP | A | 로컬 stdio, 운영 `PERSONAL_TOKENS_ENABLED=0`. MCP SDK 2.3.0은 별도 가상환경 `~/.venvs/going-mcp`에만 설치 |
| 결정 4 날씨 | A | 개발 ON / 운영 OFF(`WEATHER_ENABLED` 미설정=APP_ENV 기준) |
| 결정 5 라벨 | 직접 라벨 | 도구까지 완료, 라벨 대기 |
| `ZERO_SPEND=1` 시험 환경 | 프롬프트 표의 값 | 반영하지 않음. 이유는 [STAGE1.md](reports/STAGE1.md#zero_spend를-ci-시험-환경에서-뺀-이유) |
| 승인 위임 | 2026-10-21 대표 "승인이 필요하면 승인" | 패키지 설치(MCP 전용 가상환경)와 엔지니어링 기본값은 세션이 정함. push·배포·live 유료·운영 DB·사람 라벨은 대신하지 않음 |

## 다음 실행 조건

남은 경계 일곱 가지는 대표가 알고 있어야 한다. 앞의 셋은 실행 범위, 뒤의 넷은 최종 리뷰 지적 범위 밖에서 기록만 한 동작 경계다.

| 경계 | 지금 상태 | 아래 항목 |
| --- | --- | --- |
| PostgreSQL 시험 | 후속 3에서 실행(대표 승인으로 Colima 잠시 켬, 끝나고 끔). `make ci-postgres` 통과, v6 PG 경로는 「PostgreSQL 시험」 | 6 |
| live 평가·호출 | 미실행. 모든 평가 숫자는 synthetic | 2 |
| 사람 라벨 | 0건, 4단계 라벨 대기 | 3 |
| `provider_stopped` | 최종 리뷰 B1 뒤 에이전트 루프 첫 단계 이후 chat 공급자 오류가 HTTP 오류 대신 200 부분 답변으로 바뀜. 후속으로 평가 시나리오 `ps-01~03`(응답 유실·429·첫 단계 실패)을 추가해 게이트가 지킨다(replay) | 4 |
| B3 장부 초과 | 429 재시도가 있으면 정산 대기 행의 예상비용까지 장부에 잡혀 장부상 합계가 질문당 상한을 넘을 수 있음(합성 재현: 상한 25000, 장부 31020, 실제 확정 10340) | 4 |
| 시간 상한 | 후속 커밋으로 search_mail 임베딩도 에이전트 deadline 25초를 이어받음 — 25초 뒤 새 전송·재시도 없음(회귀 시험 `test_search_mail_embedding_keeps_the_agent_deadline`). 남은 것: 그 직전에 나간 호출 하나는 최대 45초까지 기다림. MCP ask 120초는 서버 강제 상한 아님 | 4 |
| 정산 뒤 오류 | 전송·정산 뒤 오류(parse_embed 실패)는 과금 확정이라 `provider_stopped`가 아닌 도구 오류로 남음 | 4 |

1. ~~대표: push → Actions 6+1 job(`eval` 추가) 확인~~ — 완료(2026-10-22, 「원격 CI」). 이후 커밋도 push 는 대표가 한다([출시 체크리스트](reports/RELEASE_CHECKLIST.md))
2. 대표: live 평가 — 메일 5건 ≤ 0.20 USD, 질문 10문항 ≤ 0.20 USD (명령은 [STAGE2.md](reports/STAGE2.md#대표에게-넘길-일)). 결과로 유사도 하한 확정
3. 대표: 4단계 라벨 — 운영 DB 범위 확인 → 카탈로그 내보내기 → 질의 확정 → pool 동결 → 라벨(holdout 먼저). 그 뒤 "라벨 완료, 묶음 C 진행" ([LABELING.md](LABELING.md))
4. 운영에서 에이전트·날씨·토큰을 켜기 전: `provider_stopped` 화면 문구 확인(replay 시나리오는 있음, live 미실행), 질문당 비용 상한 [가설] 확인, Open-Meteo 비상업 해당 여부, 토큰 허용 목록 재검토
5. 디스크 여유 확보 — iCloud가 받은 파일을 다시 내려놓아 시험이 멈추는 일이 반복됨(dataless 685 → 1,079 관측)
6. ~~PostgreSQL 시험~~ — 후속 3에서 실행. 남은 것: 평가 하네스(`src/evals/*`)와 일부 단위 시험(직접 SQLite schema 를 만드는 `test_v6_llm_gateway`·`test_v6_extraction_repair`)은 설계상 SQLite 전용이라 PG 로 돌리지 않는다
7. ~~`test_v5_concurrent_workload` 간헐 409~~ — 해결(후속 커밋). 원인: 메일 작업이 먼저 끝나면 `activate_generation`이 여행 버전을 올려, 같은 스레드가 뒤이어 보낸 추천 요청의 `trip_version`이 낡아 409 `VERSION_CONFLICT`(제품 동작은 맞음, 시험 순서 가정 문제). 강제 재현으로 확인(버전 1→2, 409). 시험이 추천을 먼저 보내도록 바꿨다(실행 중인 추천 작업은 버전을 다시 검사하지 않아 두 작업은 여전히 겹친다)

## 커밋

2·3·4단계를 로컬 커밋 5개로 나눴다(브랜치 `codex/private-beta-launch`, push 없음, 작성자 noreply). 커밋마다 그 시점 트리만 따로 꺼내 시험했고, 커밋된 트리와 시험한 트리가 바이트 단위로 같음을 확인했다. 모두 SQLite · synthetic, PostgreSQL 미실행.

| 커밋 | 내용 | 그 시점 트리 시험 |
| --- | --- | --- |
| `351f871` | 2단계 코어: 공급자 중립 LLM 계층·프롬프트 레지스트리·추출 수리·근거 검증 | pytest 1446 passed · 26 skipped · node 221/221 · 자산 hash 일치 |
| `63fe587` | 3단계: 여행 에이전트·날씨·Plan B·개인 토큰·로컬 MCP | pytest 1587 passed · 30 skipped · node 227/227 · 자산 hash 일치(이 커밋 기준 재계산) |
| `db11516` | 평가 하네스: mail·qa·agent 평가셋, 게이트, `make ci-eval`, CI eval job | pytest 1705 passed · 30 skipped · node 227/227 · ci-eval pass |
| `c3d752d` | 4단계: 영업시간 파서·볼거리·라벨 도구·피드백 루프(라벨 대기) | pytest 1887 passed · 30 skipped · node 233/233 · ci-eval pass |
| `0fcc038` | 보고서·운영 문서·E06 캡처 + 보고서 대조에서 찾은 숫자·문구 정정 3곳(`evals/thresholds.json` note, `evals/mail/README.md`, `evals/agent/README.md`, `src/evals/agent.py` 보고서 문장) | 작업 트리 전체: 「최종 전체 시험」 절 |
| `cf66ee3` | 후속: `provider_stopped` 평가 시나리오 `ps-01~03`(스크립트 단계 `provider_error`) · 간헐 409 시험 순서 수정 · 이 표·README 갱신 | 작업 트리 전체: 아래 「후속 시험」 |
| `80b3903` | `search_mail` 임베딩이 에이전트 deadline 을 이어받음(`handlers.embed`·`fake_call`) + 회귀 시험 · 문서·MCP 주석 갱신 | 아래 「후속 시험 2」 |
| `f5b2641` | PostgreSQL 시험 실행 · `v6_data_coverage` 행 형식 수정 · v6 시험의 `tuple(row)`·SQLite 전용 보조 함수 정리 | 「PostgreSQL 시험」 + SQLite `make ci-python` 1894 passed · 30 skipped · 0 failed |
| `c7689b5` | 상태표: push 와 원격 Actions 7/7 기록 | 원격 실행 `39702112410`: e2e 간헐 실패 1(아래) |
| 이 커밋(후속 4) | e2e 쓰기 감시에서 UI 초안 자동 저장 분리 | 로컬 e2e 12/12 반복 통과 |

한 커밋에 두 단계가 섞인 덩어리 3개(`src/foundation/db.py`·`src/storage/postgres.py`의 스키마 줄, `src/foundation/search.py`의 hook 줄)는 줄 단위로 나눴다. 평가 하네스는 2·3단계에 걸쳐 있어(agent 평가가 3단계 코드를 부른다) 3단계 뒤 별도 커밋으로 묶었다.

### 후속 시험 (2026-10-21, 후속 커밋 직전 작업 트리)

`make ci-python` 1893 passed · 30 skipped · 0 failed(6분 53초, 1890 + 검증기 시험 3) · `make ci-js` 233/233 · `make ci-eval` gate pass(agent_replay 53 시나리오, 단언 실패 0, synthetic). `test_v5_concurrent_workload` 는 수정 전 3개 병렬 × 15회 반복에서 0회 실패(자연 재현 안 됨) → 메일 작업 완료를 강제한 합성 재현으로 원인 확인, 수정 뒤 단독 5회 통과. PostgreSQL 미실행.

### 후속 시험 2 (2026-10-21, 후속 2 커밋 직전 작업 트리)

`make ci-python` 1894 passed · 30 skipped · 0 failed(6분 59초, 회귀 시험 1 추가) · `make ci-eval` gate pass(synthetic) · `GOING_E2E=1 make e2e` 12 passed. 새 시험은 옛 `handlers.py` 로 되돌리면 실패함을 확인. PostgreSQL 미실행.

### 원격 CI (2026-10-22)

대표가 `codex/private-beta-launch` 를 `df01153..f5b2641` 로 push 했다. GitHub Actions 실행 `39573172475`(커밋 `f5b2641`)의 job 7개가 모두 통과했다. 새 `eval` job 도 원격에서 처음 돌아 통과했다(키·유료 호출 없음, synthetic).

| job | 결과 |
| --- | --- |
| safety · assets · js | 통과 |
| eval | 통과 |
| e2e · postgres | 통과 |
| python | 통과 |

문서만 바꾼 `c7689b5` 의 실행 `39702112410` 은 `e2e` 1건 실패(나머지 6 job 통과)였다. 원인: `tests/e2e/test_trip_agent_browser.py` 의 쓰기 감시가 화면의 UI 상태 자동 저장(`PUT …/workspace-draft`, 탭·스크롤·선택 장소, UI 변경 700ms 뒤)을 데이터 쓰기로 셌다. 카드 스크롤이 저장을 일으켜 타이밍에 따라 단언 전에 들어갔다(일정 버전 단언은 그대로 통과 — 데이터 변경 없음). 로컬 재현 1/8. 이 끝점만 따로 기록(`writes.ui_drafts`)하고 데이터 쓰기 목록에서 뺐다. 수정 뒤 로컬 12/12 통과, `make e2e` 12 passed.

### PostgreSQL 시험 (2026-10-21, 후속 3)

대표 승인으로 Colima 를 잠시 켜고, CI 와 같은 고정 이미지(pgvector 0.8.7-pg17, loopback 일회용 컨테이너)로 돌린 뒤 컨테이너를 지우고 Colima 를 껐다.

| 실행 | 결과 |
| --- | --- |
| `make ci-postgres` | plugin 282 passed · 3 skipped · 자체 schema 16 passed · 0 failed |
| `test_v6_admin_llm_ops.py` (DSN 지정, 그동안 skip 이던 PG 1건 포함) | 11 passed |
| v6 시험 전체를 PG plugin 으로(설계상 PG 대상이 아닌 것 포함) | 처음 541 passed · 41 failed · 11 errors → 고친 뒤 실패는 SQLite 전용 하네스만 남음 |

PG 로 돌려서 찾은 것:
- **제품 코드 1건:** `scripts/v6_data_coverage.py` 가 행을 위치로 풀어(`for a, b, n in rows`) 앱의 PostgreSQL Row(dict)를 받으면 열 이름이 풀렸다(운영 `read()` 경로는 psycopg tuple 이라 영향 없었음). 행 형식과 무관한 `_values()` 로 고침 — PG·SQLite 모두 5 passed.
- **시험 코드:** `tuple(row)` 비교 4곳(`plan_state`·`itinerary_row`·미리보기 행·실행 행). PG 에서는 열 이름끼리 비교돼 "질문 전후 일정 그대로" 같은 단언이 늘 참이었다(최종 리뷰 C1 과 같은 패턴). 열 이름 기준 값 비교로 고쳤다. `sqlite_master` 를 읽던 표 스냅숏 2곳은 백엔드별 표 목록으로, `ORDER BY rowid` 1곳은 API 가 준 generation id 로 바꿨다. 해당 시험은 SQLite·PG 모두 통과.
- **남은 PG 실패 48건(설계상 SQLite 전용, 고친 뒤 재실행 49건에서 extraction_fallback 1건을 더 고친 값):** 평가 하네스 `src/evals/mail.py`·`agent.py`(임시 SQLite 세계, `rowid`·`sqlite_master`)를 쓰는 `test_v6_eval_*` 와, 예산 schema 를 `executescript` 로 직접 만드는 `test_v6_llm_gateway`·`test_v6_extraction_repair`. 앱 경로가 아니라 고치지 않았다.

## 산출물

- [1단계 보고서](reports/STAGE1.md) · [커밋 계획과 결과](reports/STAGE1_COMMIT_PLAN.md) · [출시 체크리스트](reports/RELEASE_CHECKLIST.md)
- [2단계 보고서](reports/STAGE2.md) — 정답셋·평가기·gateway·근거 검증·게이트
- [3단계 보고서](reports/STAGE3.md) — 에이전트·날씨·Plan B·화면·MCP
- [4단계 보고서](reports/STAGE4.md) — 후보 보강·라벨 도구·피드백 루프, 라벨 대기
- [라벨 사용법](LABELING.md) · [피드백 루프](FEEDBACK_LOOP.md) · [로컬 MCP 연결](MCP_LOCAL.md)
