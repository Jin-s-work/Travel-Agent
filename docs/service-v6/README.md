# 고잉 v6 문서

> v6 고도화 기획과 단계별 구현 프롬프트. 2026-10-20 작성, 구현 전.

| 파일 | 내용 |
| --- | --- |
| [PRD.md](PRD.md) | 근거 표, 4단계 범위, 설계 결정, 측정 계획, 대표 결정 사항 |
| [prompts/01-baseline-ci-e2e.md](prompts/01-baseline-ci-e2e.md) | 1단계: 작업 보존, CI, 로그인 이후 E2E, 이동시간 결함, 출시 준비 |
| [prompts/02-mail-ai-reliability.md](prompts/02-mail-ai-reliability.md) | 2단계: 정답셋, 평가기, 프롬프트 레지스트리, 벤더 중립 gateway, 근거 검증 |
| [prompts/03-trip-agent.md](prompts/03-trip-agent.md) | 3단계: tool-calling 에이전트, 미리보기 연결, 날씨·Plan B, 선택 MCP |
| [prompts/04-recommendation-eval-loop.md](prompts/04-recommendation-eval-loop.md) | 4단계: 무료 후보 보강, 라벨 도구, v5/v6 평가, 피드백 루프 |

## 사용 순서

1. PRD 10절의 결정 5개를 적는다.
2. 새 Claude Code 세션에서 1단계 프롬프트를 실행한다. `docs/handoff/CLAUDE_CONTINUE_PROMPT.md`는 1단계가 대신하므로 따로 실행하지 않는다.
3. 1단계 보고서를 보고 사람이 push하고 GitHub Actions 결과를 확인한다.
4. 2단계 → 3단계 순서로 진행한다. 4단계는 1단계 뒤 언제든 나란히 시작할 수 있다.

> [!WARNING]
> 프롬프트는 설치·유료 호출·push·배포 지점에서 멈추도록 쓰여 있다. 멈춘 지점의 승인은 채팅에서 직접 한다.
