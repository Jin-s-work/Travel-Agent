# 1단계 출시 체크리스트

> 로컬 v5 + 1단계 커밋을 GitHub에 올리고 Render에 수동 배포할 때 사람이 따라 할 순서. 이 문서를 만든 세션은 아무 단계도 실행하지 않았다.

2026-10-20 · 묶음 E · 모든 줄의 실행 주체는 **사람(대표)**

**세 줄 요약**

- push 전에 원격 브랜치와 로컬 브랜치가 갈라진 상태를 먼저 정리해야 한다. 원격 `codex/private-beta-launch`는 `df01153`, 로컬은 `5da54dd` 위에 1단계 커밋을 쌓았다.
- 운영 기록 커밋 `d154dc0`과 현재 모두 schema 15이고 `src/foundation/db.py`가 같아 migration 변경이 없다. 새 설정 이름 9개는 모두 코드 기본값이 있으나, 그중 2개는 기본이 켜짐(`true`)이다.
- 되돌리기는 이전 이미지 재배포다. 같은 schema 15를 이해하는 이미지라 DB 복원 없이 가능하다고 판단하지만 실제 롤백은 해 본 적이 없다.

## 1. push 전 점검 — 사람

| # | 할 일 | 통과 기준 |
| --- | --- | --- |
| 1 | `git status --short`로 남은 변경이 발표 자료·비밀·데이터뿐인지 확인 | [STAGE1_COMMIT_PLAN.md](STAGE1_COMMIT_PLAN.md)의 "남긴 것"과 일치 |
| 2 | `python3 scripts/check_publish_safety.py --ci` | `0 fail` |
| 3 | `python3 scripts/check_publish_safety.py --range origin/codex/private-beta-launch..HEAD` | 비밀 0. 절대경로 경고는 [처리안](STAGE1_COMMIT_PLAN.md#절대경로-처리안) 결정대로 |
| 4 | 원격과 갈라진 이력 정리 (아래 2절) | `git merge-base --is-ancestor origin/codex/private-beta-launch HEAD` 성공 |
| 5 | `make ci` (PG는 일회용 컨테이너 DSN을 줄 때) | 전부 통과 |
| 6 | `git config user.email`이 noreply 주소 | `…@users.noreply.github.com` |

## 2. 원격과 갈라진 이력 — 사람이 결정

2026-10-20 `git fetch`로 확인했다. 원격에는 로컬에 없던 커밋 25개(`5da54dd..df01153`, `d154dc0` 포함)가 있다. 내용 비교 결과 로컬 작업본이 원격 내용을 거의 모두 포함하고 그 위에서 더 고친 상태다.

| 비교 (발표 자료 제외 188경로) | 수 |
| --- | ---: |
| 로컬 = 원격 | 77 |
| 로컬만 변경 (원격은 `5da54dd` 그대로) | 86 |
| 원격만 변경 (로컬에 빠짐) | 0 |
| 양쪽 변경 · 3-way 병합 시 로컬이 원격을 이미 포함 | 5 |
| 양쪽 변경 · 3-way 병합 충돌 | 20 |

충돌 20파일은 표본 확인에서 로컬이 원격 줄을 고친 형태였다(예: 모델 목록에 `hybrid_v6` 추가, 자산 hash 갱신). 다만 `docs/service-v4/IMPLEMENTATION_STATUS.md`처럼 원격에만 있는 문단이 남은 파일도 있어 전수 검토는 하지 않았다.

> [!WARNING]
> 원격은 `docs/presentation/SCRIPT.md`·`v4/SCRIPT.md`를 이미 삭제했고 발표 자료 v5~v9를 공개했다. 로컬 발표 파일 대부분은 원격과 내용이 다르다. 병합 방식에 따라 공개된 발표 자료가 바뀌거나 지워질 수 있다.

권장 순서. 원격은 로컬에서 미추적인 `docs/presentation/v5~v9` 경로를 추적하므로, 그대로 병합하면 Git이 "untracked working tree files would be overwritten"로 멈춘다. 로컬 발표 파일을 먼저 iCloud 밖 다른 폴더에 복사해 두고, 병합 뒤 어느 쪽을 쓸지 비교한다.

```bash
git merge --no-ff origin/codex/private-beta-launch
# 충돌 20파일: 로컬(ours) 내용을 기준으로 해결하되 원격에만 있는 문단은 직접 확인
# 발표 자료: 원격(theirs)을 유지하는 것이 기존 '대본 비공개' 결정과 맞음
make ci
```

## 3. push — 사람

```bash
python3 scripts/check_publish_safety.py --range origin/codex/private-beta-launch..HEAD
git push origin codex/private-beta-launch
```

force-push는 금지다. 거부되면 2절로 돌아간다.

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

발표 자료 38파일 차이는 로컬 이력이 원격의 발표 정리 커밋을 아직 받지 않았기 때문이다. 2절 병합 결정에 따라 달라진다.
