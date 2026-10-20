# 고잉 v6 진행 기록

> v6 단계별 구현의 단일 최신 상태표. 과거 보고서는 당시 사실을 보존하고, 이 표만 갱신한다.

최종 갱신 2026-10-20 · 1단계(기준선) 실행 후 · 기준 브랜치 `codex/private-beta-launch` 로컬 커밋, push·배포 없음

**세 줄 요약**

- v5 미커밋 작업을 로컬 커밋 11개로 보존했고, 1단계 작업(게시 점검 · CI · 이동시간 · PG 결함 · E2E 골격 · 보고)을 이어 커밋했다. 원격의 앞선 25커밋은 대표 지시로 병합했다(`27bdfaa`, 발표 자료 원격판 유지, 로컬 발표 파일 백업).
- 로컬에서 CI와 같은 명령과 브라우저 E2E(Chromium, 8개 통과)를 실행했다. GitHub Actions 실행·운영 배포는 하지 않았다.
- 실행 중 결함 4건을 고치고 회귀 시험을 붙였다: 이동시간 정책(G4), PG에서 실패하던 리뷰 계약 SQL, 낡은 schema 12 재현 시험, 로드 중 고른 탭이 초안 복원에 덮이는 문제. CI 시험 환경에서는 `ZERO_SPEND`를 뺐다.

## 단일 최신 상태

열의 뜻: code = 코드 존재 · synthetic = 합성 자료로 자동 시험 통과 · browser = 실제 브라우저에서 확인 · live = 실제 외부 공급자·운영 자료 · 배포 = 운영 URL 반영.

| 영역 | code | synthetic | browser | live | 배포 |
| --- | --- | --- | --- | --- | --- |
| A 작업 보존 | 로컬 커밋 11 + 1단계 커밋 + 원격 병합 `27bdfaa` | 해당 없음 | 해당 없음 | 해당 없음 | push 안 함 · fast-forward 가능 |
| A 게시 안전 점검 | `check_publish_safety.py` | 시험 22개 통과 · 저장소 비밀 0 | 해당 없음 | 해당 없음 | 해당 없음 |
| B CI | `ci.yml` 6 job · `make ci` | `make ci` 로컬 통과: Python 1302 통과 · 20 skip, JS 220, 자산·게시 점검 | 해당 없음 | Actions 미실행 | 해당 없음 |
| B PostgreSQL | 기존 plugin + CI job | 일회용 pgvector 0.8.7/PG17: plugin 282 통과 · 자체 schema 16 통과 | 해당 없음 | 운영 DB 미사용 | 해당 없음 |
| C E2E 서버·로그인 | 빈 포트 fixture · 합성 OIDC | HTTP 2개 통과 | Chromium에서 A·B 로그인 | 해당 없음 | 해당 없음 |
| C E2E 브라우저 E01~E05 | Playwright 1.63.0 · CI `e2e` job | 합성 fixture | E01~E05 + E01b 6개 통과, 스크린샷 10장 | 해당 없음 | Actions 미실행 |
| D 이동시간(G4) | 비활성 제공자 → 같은 도시 3km 이하 도보 추정 | 단위 1 · API 1 · JS 1 통과 | E01에서 "직선거리 기준 추정" 표시 확인 | 경로 공급자 호출 0 | 미배포 |
| D PG 리뷰 계약 SQL | `observation_profile` 번역 추가 | 번역 시험 2 + PG 계약 재실행 | 해당 없음 | 운영 미확인 | 미배포 |
| D 로드 중 탭 선택 (D4) | `navigate()` override | JS 1 통과 | E01 · E01b 통과 | 해당 없음 | 미배포 |
| E 메모리 | 측정 스크립트 | macOS · Docker 512MB 측정 | 해당 없음 | 실제 Render 미측정 | 해당 없음 |
| E 출시 체크리스트 | 문서 | 해당 없음 | 해당 없음 | 해당 없음 | 실행 주체 전부 사람 |

> [!IMPORTANT]
> "synthetic 통과"는 합성 사용자 A/B, 임시 DB, 가짜 공급자 기준이다. 실제 Google 로그인, 실제 경로 시간, Render 메모리, 운영 Supabase 동작은 확인하지 않았다.

## 결정 반영

| 항목 | 결정 | 반영 |
| --- | --- | --- |
| PRD 10절 결정 1 이동시간 | A (2026-10-20 대표) | 반영. strict는 기존대로 미확인 처리, 잠정안에서만 배치, 화면 "직선거리 기준 추정" |
| `ZERO_SPEND=1` 시험 환경 | 프롬프트 표의 값 | 반영하지 않음. 이유는 [STAGE1.md](reports/STAGE1.md#zero_spend를-ci-시험-환경에서-뺀-이유) |

## 다음 실행 조건

1. 디스크 여유 확보(현재 98%) 뒤 `make ci`·`make e2e` 재실행 — 병합 뒤 PG 자체 시험과 E2E는 iCloud 대기로 미완료
2. 대표: push → Actions 확인 ([출시 체크리스트](reports/RELEASE_CHECKLIST.md)). 병합은 완료
3. 대표: 새로 올라갈 절대경로 13줄 처리 (추적 대본은 병합으로 0개)
4. push 뒤 Actions `e2e` job이 Linux에서 통과하는지 확인
5. `test_v5_concurrent_workload`의 409 — 느린 입출력에서만 1회 재현. 원인 조사 필요
6. 2단계(메일 AI 신뢰성)는 1단계 push 뒤 시작

## 산출물

- [1단계 보고서](reports/STAGE1.md) — 바꾼 파일 · 실행 명령과 결과 · 미실행 · 대표에게 넘길 일
- [커밋 계획과 결과](reports/STAGE1_COMMIT_PLAN.md)
- [출시 체크리스트 · 배포 차이](reports/RELEASE_CHECKLIST.md)
