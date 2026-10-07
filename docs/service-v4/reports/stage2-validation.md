# 2단계 구현·검증 기록

2026-10-07. 기준 commit `e8ea36faadac4912a523ae7bea5f02368d7f1ba1`, 작업 branch `codex/private-beta-launch`. 1단계 미커밋 변경을 보존하여 확장했다. 사용자 수정이 있던 기존 `docs/presentation/going-class-presentation.key`는 변경·업로드 대상에서 제외하고 새 발표는 `docs/presentation/v4/`에 만들었다.

## 완료 범위

- schema14의 canonical 지점 연결, 관리자 preview→approve/revoke, 공급자 build별 계약·실행 의존성·공용 검토 요청 큐. 기존 ID 보존 및 schema12/13 import, 철회 tombstone 복원 경계.
- 언어 프로필과 100도시 capability API, 최소 표본과 개발/평가 유출 검사를 포함한 로컬 Lingua 평가 도구. 현재 품질 기록의 모델·정책·도시·업종·만료를 조회 때도 검사한다.
- 현지어 리뷰/유명한 곳/주변 참고의 독립 자격과 v3 일반 모델. 취향을 비워도 자격 있는 후보를 정렬하고, 점수 결측과 순위 가능 여부를 분리한다. 허용된 네 수치 완화는 새 run의 직접 설정 결과다.
- 소비자 두 탭·독립 분류·근거 상세·조건/상태 복원. 관리자의 연결·계약·품질·도시 상태·수집 비용 미리보기와 명시 접수. 기존 사진·저장·비교·일정 preview/apply/undo 재사용.
- 개인 선호 반영과 분석 동의 분리. 학습 개인화는 OFF, 실사용 성과 미측정.
- 고정 입력 모델 평가 CLI와 JSON/Markdown, 실제 후보 출처 팩, 단계별 보고서, README, 최신 10장 발표와 대본.

## 최종 실제 실행 결과

| 범위 | 결과 | 원본 |
| --- | --- | --- |
| Python 전체, native Python 3.13.5 | **1,134 passed / 17 skipped / 0 failed / 2 warnings / 164.31s** | [전체 최종 로그](stage2-full-python-final.log) |
| JavaScript 전체 | **175 passed / 0 failed / 0 skipped** | [UI 최종 로그](stage2-js-final.log) |
| PostgreSQL14 이관·canonical API·파일럿 | **21 passed / 1 skipped / 2 warnings / 19.72s** | [PG 최종 로그](stage2-postgres-final.log) |
| PostgreSQL+mocked Storage 복구·삭제·예산 | **13 passed / 2 warnings / 13.66s** | [저장소 최종 로그](stage2-cloud-postgres-final.log) |
| 리뷰 세부 회귀 | 200 passed / 2 warnings / 18.14s | [리뷰 로그](stage2-review-tests.log) |
| 추천·실제 파일럿 추가 감사 | 77 passed / 2 warnings / 14.63s | [모델 감사 로그](stage2-ranking-audit-tests.log) |
| 모델 비교 | 외부 호출 0, 동일 입력 반복 결과 일치 | [평가 JSON](stage2-model-evaluation.json), [해석](stage2-model-evaluation.md) |

표의 부분 집합은 서로 중복되므로 합산하지 않는다. 전체 17 skips는 외부 환경 의존 PostgreSQL 등이며 통과가 아니다. PG의 1 skip은 SQLite 전용 파일 dry-run이다. 두 경고는 기존 Starlette/Authlib의 httpx deprecation이다.

첫 전체 실행은 1,127 passed / 2 failed / 17 skipped / 152.61s였다. 실패 두 건은 기존 도쿄·바르셀로나 팩의 공개/편집 fallback을 `needs_confirmation`으로 기대하던 시험이었다. 새 계약에 따라 유명함 근거 없는 후보는 `reference_only`다. 테스트의 영업·인원 미확인, 현지어 결과0, 비용0 검사는 유지하고 구획 기대만 갱신했다. [첫 로그](stage2-full-python-tests.log), [관련 7개 재실행](stage2-catalog-registration-tests.log). 이후 최신 소스로 전체를 다시 실행한 것이 위 최종 결과다.

시험은 기존 `.venv`를 바꾸지 않고 동일 locked requirements를 설치한 임시 native 환경을 사용했다. Docker VM의 시간 문제로 이전 단계에서 관측한 지연을 우회한 실행 환경이며 timeout·lease 기준을 느슨하게 하지 않았다.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
TRAVEL_TEST_POSTGRES_DSN= HTTP_PROXY=http://127.0.0.1:9 \
HTTPS_PROXY=http://127.0.0.1:9 ALL_PROXY=http://127.0.0.1:9 \
/private/tmp/going-stage1-native313/bin/python -m pytest tests -q -p no:cacheprovider --tb=short

node --test tests/*.cjs
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/version_web_assets.py --check

# 폐기 가능한 loopback PostgreSQL17/pgvector만 사용. 운영 DSN 사용 금지.
# TRAVEL_TEST_POSTGRES_DSN을 해당 임시 DB로 설정한 뒤:
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest -p tests.postgres_plugin \
  tests/test_stage2_review_contracts.py tests/test_stage2_review_migration.py \
  tests/test_stage2_pilot_catalog.py -q
# Storage mock fixture는 별도 내부 DB를 구성하므로 postgres_plugin 없이 실행
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest tests/test_cloud_storage.py -q
```

## 실제 데이터와 기능 상태

[실제 후보 팩 검증](stage2-pilot-catalog.md): 도쿄6곳·바르셀로나6곳, 각각 식당3·볼거리3이다. root가 공식/편집 페이지를 확인한 뒤 JSON 팩으로 구성했다. 격리 관리자 API import/approve→추천→상세에서는 도시별6곳이 방문 조건 확인 필요로 노출되며, 방문 조건 확인 완료0·엄격 현지어0이다. 미검증 좌표·영업·가격·인원·평점·잔여석·사진을 만들어 넣지 않았다. 운영 DB에는 아직 등록하지 않았다. 기존 팩과 같은 이름이 있어도 외부 ID만 보고 자동 합치지 않는다.

[공급자 조사](stage2-provider-verification.md): Apify 공개 metadata·가격·입출력 문서 확인은 완료했다. 실제 actor 실행 A(6×100), B(10×200)는 **둘 다 not_run**, 실제 지출0이다. 키·계정 잔액·새 공급자 지출 승인이 없으며 기존 OpenAI 허용을 확장하지 않았다. 특히 현재 어댑터는 dataset offset을 볼 뿐 Google 원천 페이지 연속성을 입증하지 못한다. 관리자 checkbox로 이 능력을 만들 수 없다.

[언어 진단](stage2-language-diagnostic.json)은 로컬 Lingua가 합성 일반 문장12개를 판별한 결과다. 한국어/현지어 표본 각4개는 최소50에 미달하고 실제 음식점 리뷰도 아니므로 품질 통과가 아니다. 실제 precision·recall·NDCG·저장률·만족도는 미측정이다.

| 상태 | 결론 |
| --- | --- |
| 코드·SQL/API·순수 집계·UI 계약 | implemented / tested |
| 정상 관리자 연결→fake 수집→동일 카드→상세→첫 일정 API | synthetic E2E passed |
| 실제 리뷰 수집·원문 대조·이용 승인·독립 언어 평가 | not_run / unverified |
| 운영 엄격 언어 기능 | **OFF 유지** |
| 100도시 | 입력 registry 100, 초기 언어 프로필 Tokyo ja / Barcelona es+ca, 실제 품질 통과 도시0 |
| 학습 기반 개인화 | OFF / insufficient_evidence |
| 최신 기능 운영 배포 | not_run; GitHub 코드 업로드와 구분 |

## 브라우저와 외부 위치 전송 제한

사용자는 앞 단계에서 합성 사용자 A/B 로그인을 허용했지만 `http://127.0.0.1:8767`에는 도구의 저장된 차단이 남았다. 이번에도 해제를 요청했으나 확인 답변이 없었다. 주소·포트·브라우저·직접 쿠키 주입 등으로 우회하지 않았다. 새 화면의 로그인 후 전체 흐름, 모바일 viewport, 키보드·포커스·200% 실물 확인은 **not_verified**다. API/DOM 계약 시험이나 기존 screenshot으로 이를 대체하지 않는다.

숙소 유래 좌표를 Photon으로 보내는 변경도 이전 자동 승인 검토에서 목적지와 위치 payload에 대한 승인이 없다는 이유로 거절되었다. 기존 도시 중심 수집과 한계 안내를 유지한다. 실제 숙소 위치가 확인된 기존 후보의 서버 거리 계산은 별개다.

## 다음 실행 순서

1. 브라우저 권한 해제 후 새 소비자·관리자 전체 흐름을 확인한다.
2. 공식 장소 팩의 중복 지점과 미확인 좌표를 운영 관리자가 대조하고 등록한다.
3. 현재 계정·단가·이용 범위를 확인한 승인 한도에서 리뷰 A→검토→B를 진행한다. 원천 연속성을 확인할 수 있는 adapter/protocol이 없으면 strict OFF를 유지한다.
4. 실제 도시별 heldout 품질을 통과한 후에만 제품 gate를 검토한다. 필요 설정, dry-run, 복구는 [리뷰 계약 보고서](stage2-review-contracts.md)에 있다.
5. 소수 사용자의 두 탐색 선택·저장·일정 채택·방문 경험을 건수와 이유로 측정한다.
