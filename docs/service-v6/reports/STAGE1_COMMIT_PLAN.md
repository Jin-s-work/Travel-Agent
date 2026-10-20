# 1단계 커밋 계획 — v5 작업 보존

> 로컬에만 있던 v5 변경을 의미 단위 로컬 커밋으로 묶어, push 뒤에는 GitHub clone만으로 복원되게 하는 계획과 실행 결과.

2026-10-20 · 묶음 A · 브랜치 `codex/private-beta-launch` · push는 대표가 한다

**세 줄 요약**

- 시작 시점 변경 212경로(수정 88 · 미추적 파일 124)를 11개 v5 커밋으로 나눴다. 발표 자료 31경로와 비밀·데이터·캐시는 커밋하지 않는다.
- 게시 전 점검(`scripts/check_publish_safety.py`)에서 비밀 규칙 위반은 0건이다. 절대경로 76줄 · 루프백 시험 DSN 5줄 · 추적 중인 대본 2개는 경고로 보고만 한다.
- 커밋은 파일 단위로 나눴다. 중간 커밋 하나만 checkout하면 import가 맞지 않을 수 있고, 마지막 커밋에서만 전체 시험을 돌렸다.

## 출발점

인수인계 기록과 같은 HEAD·같은 개수에서 시작했다. 미추적이 65 → 66으로 하나 늘어난 것은 같은 날 기획 세션이 만든 `docs/service-v6/`이다.

| 항목 | 인수인계(2026-10-08) | 이번 확인(2026-10-20) |
| --- | --- | --- |
| 브랜치 | `codex/private-beta-launch` | 같음 |
| HEAD | `5da54dd` | `5da54ddc1db5…` 같음 |
| 수정 | 88 | 88 |
| 미추적(최상위 항목) | 65 | 66 (`docs/service-v6/` 추가) |
| Python · Node | 3.13 · 24 | 3.13.5 · v24.20.0 |
| 커밋 이메일 | — | GitHub noreply 확인 |
| 원격 `codex/private-beta-launch` | 로컬과 같다고 기록 | `df01153` — 로컬보다 25커밋 앞섬 |

> [!WARNING]
> 이 폴더는 iCloud Desktop 동기화 안에 있다. 시작 시점에 `.venv` 25,543개, `.git` 1,053개, 수정된 소스 43개가 iCloud로 내려가 있어(dataless) 첫 pytest가 멈췄다. 파일을 읽어 내려받은 뒤 시험했다. 설치나 재생성은 하지 않았다. 원인과 대응은 [STAGE1.md](STAGE1.md)에 있다.

## 분류

모든 변경 경로를 일곱 분류로 나눴다. 분류는 파일 단위이며, 한 파일 안에 여러 기능의 변경이 섞인 경우 주된 기능으로 넣었다.

| 분류 | 경로 수 | 처리 |
| --- | ---: | --- |
| 앱 코드 (`api.py`, `src/`, `web/`, 앱 스크립트) | 72 | 커밋 C1~C5 |
| 테스트 (`tests/`) | 39 | 커밋 C6 |
| 설정 예시·도구 (`.env.example` 등) | 7 | 커밋 C7 |
| v2~v5 문서·예시 메일 | 48 | 커밋 C8 |
| README·스크린샷 | 5 | 커밋 C9 (따로 빼기 쉽게 분리) |
| 인수인계 (`CLAUDE.md`, `docs/handoff/`) | 4 | 커밋 C10 |
| v6 기획 | 6 | 커밋 C11 |
| 발표 자료 (`docs/presentation/**`) | 31 | 커밋하지 않음 |
| 1단계 새 파일·수정 | 별도 | 커밋 S1~S7 |

## 커밋 순서

v5 보존 11개, 1단계 작업 7개다. 메시지는 영어 명령형 한 줄이고 본문에 범위를 적는다.

| # | 메시지 요지 | 파일 | 범위 |
| --- | --- | ---: | --- |
| C1 | Add v5 candidate pool, rankers and result pages | 26 | `src/discovery`(사진 제외) · `src/recommendations` · `src/http/recommendations.py` · `src/accommodations/origin.py` · 평가 스크립트 3 |
| C2 | Add v5 review observation contract and pilot planning | 10 | `src/research` · `src/providers/reviews.py` · Apify 점검·파일럿 계획 스크립트 |
| C3 | Add v5 place photo states | 3 | `src/discovery/photos.py` · `photo_provider.py` · `photo_schema.py` |
| C4 | Update shared services for v5 | 20 | `api.py` · `src/foundation` · `src/storage` · `src/reliability/jobs.py` · `src/operations/failures.py` · `src/product` · `src/itineraries`(이동시간 제외) · `src/travel_tools/exports.py` |
| C5 | Update web app for the v5 discovery flow | 13 | `web/**`. `foundation.js`·`index.html`·`sw.js`는 1단계 문구 변경 전 내용으로 stage |
| C6 | Add v5 regression tests | 39 | `tests/**`. `test_discovery_ui.cjs`는 1단계 추가 시험 전 내용으로 stage |
| C7 | Update config examples and release tooling for v5 | 7 | `.env.example` 2개 · `render.yaml` · `.gitignore` · 스크립트 3 |
| C8 | Add v5 records and backfill v2-v4 docs | 48 | `docs/service-v2~v5` · `docs/operations` · `examples/mail-time-pack` |
| C9 | Refresh README screenshots | 5 | `README.md` · `docs/screenshots/*-v7.jpg` 4장 |
| C10 | Add Claude handoff notes | 4 | `CLAUDE.md` · `docs/handoff/` |
| C11 | Add v6 plan and stage prompts | 6 | `docs/service-v6/PRD.md` · `README.md` · `prompts/` |
| S1 | Add publish safety scan for the public repository | 2 | `scripts/check_publish_safety.py` · 시험 22개 |
| S2 | Estimate short walks when the route provider is disabled | 7 | `travel_time.py` · 시험 3파일 · `foundation.js` 문구 · 자산 hash |
| S3 | Translate the review contract profile lookup for PostgreSQL | 2 | `src/storage/postgres.py` · 번역 시험 |
| S4 | Drop schema 15 tables when rebuilding schema 12 in the backup test | 1 | `tests/test_cloud_storage.py` |
| S5 | Add stage 1 E2E fixtures and browserless isolation checks | 5 | `tests/e2e/` · fixture 포트 환경변수 |
| S6 | Add CI workflow and make ci targets | 2 | `.github/workflows/ci.yml` · `Makefile` |
| S7 | Record stage 1 baseline | 5 | 측정 스크립트 · `docs/service-v6/reports/` · `IMPLEMENTATION_STATUS.md` |

C5·C6의 "1단계 전 내용으로 stage"는 `git hash-object -w` + `git update-index --cacheinfo`로 했다. 작업 폴더 파일은 건드리지 않고 색인에만 v5 시점 내용을 넣은 뒤, S2에서 1단계 변경분을 이어 커밋했다.

## 커밋하지 않는 것

아래는 의도적으로 남긴다. 이유가 있으므로 다음 세션도 `git add -A`·`git add .`를 쓰지 않는다.

| 대상 | 이유 |
| --- | --- |
| `docs/presentation/**` 31경로 (`.key`·`.pptx`·`.mp4`·`SCRIPT.md` 포함) | 발표 자료는 Git에 올리지 않는다는 기존 결정. 수정된 `README.md`·`.key`·`v4/SCRIPT.md`도 포함 |
| `.env` · `deploy/render-supabase/.env` · `.env.render` | 비밀 파일. 존재만 확인했고 열지 않았다. 모두 `.gitignore` 대상 |
| `data/` · `.venv/` · `node_modules/` · `__pycache__/` · `.pytest_cache/` | 개인 자료·재생성 가능한 환경·캐시 |
| `deploy/render-supabase/pricing-*.json` · `prod-ca-2021.crt` | 이미 추적 상태인지와 무관하게 이번 변경 범위가 아님 |

> [!IMPORTANT]
> `docs/presentation/v4/SCRIPT.md`와 `docs/presentation/SCRIPT.md`는 로컬 HEAD에서 추적 중이다. `.gitignore`는 과거 추적을 해제하지 않는다. 추적 해제(`git rm --cached`)는 하지 않았다. 원격 `df01153`에서는 두 파일이 이미 삭제됐으므로, 병합 방식과 함께 대표가 정한다.

## 게시 전 점검

`--untracked`로 추적 + 미추적 텍스트 파일 전체를 검사했다. 값은 출력하지 않고 `파일:줄 규칙`만 남긴다.

```bash
python3 scripts/check_publish_safety.py --ci --untracked
# 출력: publish safety: 0 fail, 83 warn (absolute_user_path=76, db_url_loopback_password=5, presentation_script_tracked=2)
```

| 규칙 | 건수 | 판단 |
| --- | ---: | --- |
| 키·개인키·토큰·외부 DB 비밀번호 | 0 | 통과 |
| 추적 중인 `.env`(예시 제외) | 0 | 통과 |
| `absolute_user_path` | 76줄 / 50파일 | 경고. 아래 처리안 |
| `db_url_loopback_password` | 5줄 / 3파일 | 경고. v2·v4 보고서의 일회용 loopback 컨테이너 DSN. 운영 비밀 아님 |
| `presentation_script_tracked` | 2 | 경고. 위 IMPORTANT 참고 |

### 절대경로 처리안

`/Users/<사용자명>/` 형태가 76줄 있다. 자동으로 고치지 않았다. 63줄은 이미 추적 중인(= 이미 GitHub에 올라간) 문서이고, 13줄이 이번 커밋으로 새로 올라간다.

| 위치 | 줄 | 상태 | 처리안 (권장) |
| --- | ---: | --- | --- |
| `CLAUDE.md` · `docs/handoff/` 4파일 | 5 | 새로 올라감 | 그대로 두기 — 인수인계는 로컬 폴더 위치 자체가 정보. 공개가 싫으면 `<repo-root>`로 치환 |
| `docs/service-v5/prompts` · `reports` | 4 | 새로 올라감 | 상대경로로 바꾸기 권장 |
| `docs/service-v6/prompts/*` | 4 | 새로 올라감 | 상대경로로 바꾸기 권장 |
| `docs/service-v2~v4/**` · 발표 검증 json (기존 추적) | 63 | 이미 공개 | 그대로 두기 — 과거 기록. 지워도 Git 이력에 남음 |

사용자명이 이미 공개 이력에 있으므로 새 13줄을 고쳐도 노출이 없어지지는 않는다. 고칠지는 대표가 정한다.

## 실행 결과

`5da54dd` 위에 로컬 커밋 18개를 만들었다. 모두 작성자 `Jin-s-work <Jin-s-work@users.noreply.github.com>`이다. push하지 않았다.

| # | 커밋 | 메시지 |
| --- | --- | --- |
| C1 | `9793b2e` | Add v5 candidate pool, rankers and result pages |
| C2 | `cc1c5cd` | Add v5 review observation contract and pilot planning |
| C3 | `0e88838` | Add v5 place photo states |
| C4 | `256c98b` | Update shared services for v5 |
| C5 | `354bb9b` | Update web app for the v5 discovery flow |
| C6 | `9d66fd4` | Add v5 regression tests |
| C7 | `f239eed` | Update config examples and release tooling for v5 |
| C8 | `614fa72` | Add v5 records and backfill v2-v4 docs |
| C9 | `9e14b98` | Refresh README screenshots |
| C10 | `35885b8` | Add Claude handoff notes |
| C11 | `eba9bb7` | Add v6 plan and stage prompts |
| S1 | `5d0d79e` | Add publish safety scan for the public repository |
| S2 | `a16da90` | Estimate short walks when the route provider is disabled |
| S3 | `1b97759` | Translate the review contract profile lookup for PostgreSQL |
| S4 | `5b062fd` | Drop schema 15 tables when rebuilding schema 12 in the backup test |
| S5 | `d4a4e8b` | Add stage 1 E2E fixtures and browserless isolation checks |
| S6 | `b67afb4` | Add CI workflow and make ci targets |
| S7 | 이 문서를 담은 커밋 | Record stage 1 baseline reports |

전체 시험(`make ci`)은 S6 직전 작업 트리, 즉 S7의 문서·측정 스크립트를 뺀 상태에서 실행했다. 중간 커밋 각각은 실행하지 않았다.

커밋 범위의 추가 줄만 다시 검사했다.

```bash
python3 scripts/check_publish_safety.py --range origin/codex/private-beta-launch..HEAD
# 출력: publish safety: 0 fail, 13 warn (absolute_user_path=13)
```

### 남긴 파일

S7 이후 `git status --short`에 남는 것은 발표 자료 8줄뿐이다. 이유는 위 "커밋하지 않는 것"과 같다.

```text
 M docs/presentation/README.md
 M docs/presentation/going-class-presentation.key
 M docs/presentation/v4/SCRIPT.md
?? docs/presentation/v5/
?? docs/presentation/v6/
?? docs/presentation/v7/
?? docs/presentation/v8/
?? docs/presentation/v9/
```

`.env` 3개와 `data/` 등은 `.gitignore` 대상이라 목록에 나오지 않는다.
