# 1단계 출시 체크리스트

> 로컬 v5 + 1단계 커밋을 GitHub에 올리고 Render에 수동 배포할 때 사람이 따라 할 순서. 이 문서를 만든 세션은 아무 단계도 실행하지 않았다.

2026-10-20 · 묶음 E · 모든 줄의 실행 주체는 **사람(대표)**

**세 줄 요약**

- 원격 `df01153`과의 병합을 2026-10-20 대표 지시로 마쳤다(병합 커밋 `27bdfaa`). 이제 원격이 로컬의 조상이라 push는 fast-forward다. push 자체는 아직 하지 않았다.
- 운영 기록 커밋 `d154dc0`과 현재 모두 schema 15이고 `src/foundation/db.py`가 같아 migration 변경이 없다. 새 설정 이름 9개는 모두 코드 기본값이 있으나, 그중 2개는 기본이 켜짐(`true`)이다.
- 되돌리기는 이전 이미지 재배포다. 같은 schema 15를 이해하는 이미지라 DB 복원 없이 가능하다고 판단하지만 실제 롤백은 해 본 적이 없다.

## 1. push 전 점검 — 사람

| # | 할 일 | 통과 기준 |
| --- | --- | --- |
| 1 | `git status --short`로 남은 변경이 발표 자료·비밀·데이터뿐인지 확인 | [STAGE1_COMMIT_PLAN.md](STAGE1_COMMIT_PLAN.md)의 "남긴 것"과 일치 |
| 2 | `python3 scripts/check_publish_safety.py --ci` | `0 fail` |
| 3 | `python3 scripts/check_publish_safety.py --range origin/codex/private-beta-launch..HEAD` | 비밀 0. 절대경로 경고는 [처리안](STAGE1_COMMIT_PLAN.md#절대경로-처리안) 결정대로 |
| 4 | 원격과 갈라진 이력 정리 (아래 2절, **완료**) | `git merge-base --is-ancestor origin/codex/private-beta-launch HEAD` 성공 — 2026-10-20 확인 |
| 5 | `make ci` (PG는 일회용 컨테이너 DSN을 줄 때) | 전부 통과 |
| 6 | `git config user.email`이 noreply 주소 | `…@users.noreply.github.com` |

## 2. 원격과 갈라진 이력 — 병합 완료

원격에는 로컬에 없던 커밋 25개(`5da54dd..df01153`, 운영 `d154dc0` 포함)가 있었다. 대표 지시(2026-10-20, "RELEASE_CHECKLIST 2절 권장안대로 병합해줘. 발표 자료는 원격 유지, 충돌은 로컬 기준으로 풀되 원격에만 있는 문단은 확인하고, 로컬 발표 파일은 먼저 iCloud 밖에 백업해. 병합 후 make ci 돌리고 push는 하지 마.")로 병합했다.

### 백업 — 병합 전

| 항목 | 값 |
| --- | --- |
| 위치 | `~/Backups/going-presentation-2026-10-20/` (iCloud 동기화 밖) |
| 대상 | `docs/presentation/` 전체 54파일(추적·미추적·무시 파일 포함), 26MB |
| 대조 | 복사 전 54개 모두 iCloud에서 내려받고 "읽은 바이트 = 표시 크기" 확인 → 원본·사본 sha256 목록 54줄 일치 (`source.sha256` = `copy.sha256`) |
| 병합 전 상태 기록 | `git-status-before.txt` |
| 작업 폴더에서 비킨 것 | 미추적 `v5~v9/` 32파일은 `moved-from-worktree/`로 이동. 추적 파일 3개(`README.md`·`.key`·`v4/SCRIPT.md`)는 대조 뒤 HEAD로 복원 |

### 병합과 충돌 해결

`git merge --no-ff --no-commit origin/codex/private-beta-launch` → 충돌 21파일(이전 분석 20 + 1단계에서 고친 `.gitignore`). 발표 자료 38경로는 충돌 없이 원격판으로 들어왔다.

| 파일 | 원격 전용 줄 | 결정 · 이유 |
| --- | ---: | --- |
| `docs/service-v4/IMPLEMENTATION_STATUS.md` | 13 | **블록 2는 원격** — 원격에만 있는 10/8 "리뷰 조건·메일 연습·공개 발표 정리" 기록과 배포 기록(`2da85c0`, `dep-db3iiutg…`), 비공개 대본 링크를 "로컬 별도 보관"으로 바꾼 문장. 블록 1은 빈 줄만 달라 로컬 |
| `web/js/foundation.js` | 15 | 로컬 — 원격 줄은 `setExploreView`·`ensureDiscoveryDraft`·카드 저장(`run_id` 없는 판)의 이전 형태 |
| `web/js/discovery-experience.js` | 10 | 로컬 — 원격은 '둘러보기'를 탭으로 둔 이전 3탭 구조 |
| `web/js/home.js` | 10 | 로컬 — 같은 필터(부적격·합성·상한 제외)를 구획별 라운드로빈으로 다시 쓴 판이 로컬에 있음 |
| `src/recommendations/benchmark.py` | 10 | 로컬 — 재구성된 평가기. 원격 줄은 이전 반환 형태 |
| `web/index.html` · `web/sw.js` | 9 · 2 | 로컬 — 자산 hash·캐시 버전. 로컬 JS 내용과 맞아야 함 |
| `src/recommendations/{service,registry,general,engine}.py` | 5 · 4 · 4 · 2 | 로컬 — 원격 줄은 `hybrid_v6` 추가 전 모델 목록 |
| `src/discovery/photos.py` · `photo_provider.py` | 4 · 1 | 로컬 — 사진 거절 사유를 더 잘게 나눈 판 |
| `scripts/check_apify_setup.py` · `version_web_assets.py` | 4 · 1 | 로컬 — 이전 함수 형태·자산 목록 |
| `tests/test_stage2_discovery_ui.cjs` · `test_recommendation_progress_ui.cjs` | 2 · 1 | 로컬 — 같은 이름의 시험이 로컬에 새 구조로 있음 |
| `api.py` | 1 | 로컬 — `photo_collection_enabled` 조건이 붙은 판 |
| `.gitignore` · `web/css/design-system.css` · `web/js/discovery-flow.js` | 0 | 로컬 |

충돌 밖 원격 변경은 21파일 모두 이미 로컬에 있었다. 로컬 기준 해결안을 만들어 보니 20파일이 로컬 내용과 바이트 단위로 같았다. 결과적으로 발표 자료를 뺀 병합 변경은 v4 진행 기록 1파일(+18/−1)뿐이다.

### 발표 자료 — 병합 뒤

| 구분 | 수 | 처리 |
| --- | ---: | --- |
| 원격과 같음 | 24 | 그대로 |
| 원격과 다름 (v4~v8 `README.md`·`.key`·`.pptx`·`slides-content.json`, 루트 4개) | 24 | **원격판 유지**. 원격판은 발표자 메모·대본을 뺀 공개판이다. 로컬판은 백업에 있음 |
| 병합 트리에 없음 (비공개 대본 `SCRIPT.md` 6개: 루트·v4~v8) | 6 | 백업에서 같은 위치로 복원. `.gitignore` 대상이라 추적되지 않음 |

추적 중인 발표 대본은 병합으로 0개가 됐다(원격이 삭제한 2개를 따름). 게시 점검의 `presentation_script_tracked` 경고도 0이다.

## 3. push — 사람

```bash
python3 scripts/check_publish_safety.py --range origin/codex/private-beta-launch..HEAD
git push origin codex/private-beta-launch
```

force-push는 금지다. 병합 이후 원격이 다시 바뀌었으면 거부되므로, `git fetch` 후 같은 방식으로 다시 병합한다.

## 4. Actions 확인 — 사람

push 직후 GitHub Actions `ci` 워크플로의 6개 job(python · js · assets · postgres · safety · e2e)이 통과하는지 본다. 이 세션은 Actions를 실행할 수 없어 로컬 명령으로만 대조했다. `e2e`가 실패하면 artifact `e2e-traces`의 trace를 `python -m playwright show-trace`로 연다.

## 5. Render 수동 배포 — 사람

`render.yaml`은 `autoDeployTrigger: off`다. 대시보드에서 해당 커밋을 수동 배포한다.

| 확인 | 기준 |
| --- | --- |
| 배포 대상 커밋 | push한 HEAD와 같음 |
| 새 설정 이름 9개 | 코드 기본값이 있음. 그중 `CANDIDATE_EXPANSION_ENABLED`·`PHOTO_COLLECTION_ENABLED`가 기본 `true`라, 설정하지 않으면 공개 지도 후보 확장과 사진 수집이 켜진다. 의도와 다르면 Render에 `false`로 먼저 넣는다 |
| 비용 | `ZERO_SPEND`·예산 설정은 기존 운영값 유지. 새 유료 리소스 없음 |

## 6. `/health/ready` — 사람

```bash
curl -fsS https://travel-inbox-rag.onrender.com/health/ready
```

`/health/live`·`/health/ready`는 유료 API를 호출하지 않는다(`docs/operations/RENDER_SUPABASE.md`). Free 인스턴스는 유휴 뒤 첫 요청이 느릴 수 있다.

## 7. 본인 계정 로그인 확인 — 사람

Google 로그인 → 마지막 여행 → 추천 한 번 → 일정 미리보기까지 본다. 일정 이동 구간은 이번 변경으로 "직선거리 기준 추정"이 보일 수 있다(잠정안에서만). 다른 사용자 자료가 보이지 않는지 확인한다.

## 8. 롤백 — 사람

| 상황 | 조치 |
| --- | --- |
| 기동 실패 · `/health/ready` 실패 | Render에서 직전 배포(이전 이미지)로 되돌림 |
| 기능 문제 | `.env.example`의 v5 스위치(`SIMPLE_DISCOVERY_ENABLED` 등)를 끄고 재시작 |
| 데이터 문제 | 이미지 롤백과 DB 복원은 별개. `docs/operations/RENDER_SUPABASE.md` 무료 백업·복원 절차 |

이미지 롤백은 같은 cloud schema를 이해하는 이미지에서만 가능하다. `d154dc0`과 현재 모두 schema 15라 조건은 맞는다.

## 배포 차이 — `d154dc0` 대비

`git diff d154dc0 HEAD`로 계산했다(2026-10-20, 1단계 코드 커밋 `d4a4e8b` 기준). 설정은 `.env.example`의 이름만 비교했다.

| 항목 | 값 |
| --- | --- |
| 이미지에 들어가는 경로 (`api.py` · `src` · `web` · `Dockerfile` · `requirements.lock` · 가격·인증서) | 51파일 (추가 8 · 수정 43), +1,239 / −222줄 |
| `Dockerfile` · `requirements.lock` · `render.yaml` | 변경 없음 |
| schema 버전 | 15 → 15. `src/foundation/db.py`는 `d154dc0`과 같음. `src/storage/postgres.py`는 PG 번역 1줄 추가(D2) |
| 새 설정 이름 (`.env.example`) | 9개: `SIMPLE_DISCOVERY_ENABLED` · `CANDIDATE_EXPANSION_ENABLED` · `CANDIDATE_POOL_LIMIT` · `CANDIDATE_RAW_RECORD_LIMIT` · `CANDIDATE_QUERY_LIMIT` · `CANDIDATE_DEADLINE_SECONDS` · `BOUNDED_REVIEW_ENABLED` · `PHOTO_COLLECTION_ENABLED` · `RANKER_SHADOW_ENABLED` |
| 빠진 설정 이름 | 0 |
| 전체 저장소 | 156파일. 그중 문서 73(발표 자료 38 포함) · 시험 23 |

발표 자료 38파일 차이는 병합 전 계산이다. 병합(`27bdfaa`)으로 발표 자료는 원격판이 됐다.
