# 2단계 리뷰 연결·공급자 계약·도시 품질 검증

2026-10-07 · 코드 구현 및 합성 검증 기록. 실제 리뷰 수집·데이터 이용 승인·도시별 언어 정확도 검증은 완료하지 않았다.

## 현재 상태

| 범위 | 상태 | 근거 |
| --- | --- | --- |
| canonical 지점 → 외부 ID 연결 → 수집 → 집계 | implemented / synthetic API E2E passed | 일반 관리자 API만으로 연결·수집한 집계가 기존 canonical ID에 저장됨 |
| 동일 카드 → 상세 → 일정 preview/apply | synthetic API E2E passed | `test_normal_canonical_link_collect_card_and_itinerary_flow` |
| 공급자 계약의 실행 경로 연결 | implemented | provider/build/adapter/만료/상태/권리 정책을 실제 호출 앞에서 확인 |
| Apify 실제 수집 | not_run | 계정 잔액·유료 실험 범위 미승인, 호출 0 |
| Apify 원천 페이지·연속성 검증 | unavailable | 현 어댑터는 dataset offset만 관측함 |
| 실제 음식점 리뷰 언어 평가 | not_run | 도시별 독립 실제 리뷰·원문 대조 자료 없음 |
| 운영 엄격 현지어 추천 | OFF | fake·미검증·미확인 원천 연속성은 ON 불가 |
| 100개 도시 입력과 언어 기능 | 분리 | 입력 registry 유지, 도쿄/바르셀로나만 초기 언어 프로필 있음 |

## 변경한 API와 저장

schema 14는 기존 ID를 교체하지 않고 `place_external_links`, `review_provider_contracts`, `review_run_dependencies`, `place_review_requests`를 추가한다. SQLite migration은 트랜잭션, PostgreSQL migration은 기존 writer/migration advisory lock을 재사용한다. 공용 장소에 개인 숙소·메모·여행 조건을 넣지 않는다.

- `GET /api/v2/admin/review-external-links`: canonical 후보와 허용 출처, 외부 연결 목록.
- `POST /api/v2/admin/review-external-links/preview`: 이름·주소·좌표·외부 ID·공식 출처를 대조하고 미리보기 저장.
- `POST /api/v2/admin/review-external-links/{id}/approve`: 버전과 네 가지 확인을 재검사한 승인/거절. 주소 불일치, 250m 초과 좌표 차이, 좌표 미확인, 기존 ID 충돌은 승인 불가. 이름 유사도 자동 병합 없음.
- `POST /api/v2/admin/review-external-links/{id}/revoke`: 연결 버전 변경·집계 무효화·복원용 tombstone.
- `GET/POST /api/v2/admin/review-provider-contracts`, `POST .../{id}/revoke`: build별 증거와 승인 상태.
- `POST /api/v2/admin/review-collection-preview`: 저장된 지점·정책·예산 상한 확인. 외부 호출·비용 예약 없음. 금액은 견적이 아니라 실행 상한.
- 기존 collection API는 `place_id`에 canonical ID를 받는다. 승인한 외부 연결이 있는 경우만 공급자 ID로 해석한다. 기존 직접 등록 ID는 별도 지점으로 보존하며 임의 병합하지 않는다.
- `GET/POST /api/v2/admin/review-quality-evaluations`: 판별 버전·도시·업종·build·정책별 독립 평가 기록.
- `GET /api/v2/review-capabilities`: 100도시의 입력 지원, 언어 프로필, 엄격 자료 준비 상태.
- `POST /api/v2/trips/{trip}/places/{place}/review-request`: 여행 소유권·장소 표시 권한 확인 후 공용 검토 큐에 중복 제거. 저장을 강요하지 않으며 유료 수집을 시작하지 않는다.

외부 ID/provider 활성 연결과 canonical/provider 활성 연결에 각각 partial unique index가 있다. 동시 승인 두 건 중 한 건만 성공한다. 진행 중인 수집은 연결 버전·현재 권리·build·당시 계약의 만료를 다시 확인하고, 철회 후 늦은 결과를 활성화하지 않는다. 철회는 해당 집계를 무효화하며 공식 장소의 사실·유명 근거를 지우지 않는다. 별도의 source-backed `rating` 사실이 없는 장소에는 평점·평가 수를 추정하여 채우지 않는다.

## 계약 증거와 어댑터의 실제 한계

계약에는 확인일, 문서 URL, 보고서 SHA256, schema 표본 수, 원문/번역 구분, `originalLanguage`의 의미, published/edited 기준, 원천 페이지 관측 범위, 내부 재시도/제한 확인, 권리 정책 ID, 검토자, 실패 유형, 가격 확인일·과금 단위·실행 상한·추가 비용을 보존한다. JSON의 비밀키나 공급자 원문 응답을 받는 API가 아니다.

실행 어댑터는 기본값을 `true`로 바꾸지 않는다. 활성 계약과 **정확히 같은 provider/build/adapter**에서만 확인된 원문 의미·정렬을 적용한다. 다른 build, 만료, 철회, 권리 정책 철회 시 미확인으로 복귀한다. 실행 응답의 build 번호가 요청과 다르면 결과 채택을 중단하고 이미 생성된 원격 자료를 삭제 대기열에 남긴다.

`apify-compass-v1`은 원천 Google 페이지를 관측하지 못한다. 관리자가 계약 JSON에 continuity=true를 넣어도 어댑터의 실제 능력을 바꾸지 않는다. `ADAPTER_SOURCE_PAGINATION_UNOBSERVABLE`로 엄격 준비 완료를 차단한다. 공식 문서만으로 원문·수정일·내부 페이지의 실제 품질을 증명하지 않는다. 별도 검증 가능한 어댑터/증거 프로토콜이 생기기 전까지 현재 공급자의 엄격 기능은 OFF다.

## 언어 평가

`review-language-quality-v2`는 도시 labels≥100, 원문 대조≥20, 한국어 정답≥50, 현지어 정답≥50, 현지어 판정≥50, 현지어 precision≥95%, 한국어 recall≥95%를 요구한다. 원문 대조 오류, 개발/평가 원문 중복, 같은 지점 유출이 있으면 통과하지 않는다. 모델 버전·라이선스·hash·메모리·시간을 기록하며 일반 문장/합성 자료는 제품 검증으로 쓰지 않는다.

`city-languages-v1`은 도쿄 ja, 바르셀로나 es+ca다. 서울 등 한국어와 현지어 집합이 겹치는 도시는 unavailable이다. 초기 지역 언어 프로필은 주민/방문자 구분이 아니며 도시 전체 리뷰의 분포도 아니다.

재실행:

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m src.research.evaluation \
  --input docs/service-v4/examples/language-evaluation-synthetic.json \
  --output /private/tmp/going-language-diagnostic.json
```

저장한 [진단](stage2-language-diagnostic.json)은 작성한 합성 일반 문장 12개를 실제 설치된 Lingua로 처리한 결과다. 한국어 정답4개·현지어 정답4개로 분모 부족이며, 실제 리뷰 정확도·출시 gate 통과의 증거가 아니다. 같은 도구에 허용된 실제 heldout 자료를 넣고 도시·build·업종·정책별 보고서를 별도로 검토해야 한다. 언어 평가용 한국어 문장은 장소 관측 분포에 합쳐지지 않는다.

## 검증

Python 3.13.5, 임시 환경 `/private/tmp/going-stage1-native313`; 실제 운영 secret·자료 사용 없음.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHON_DOTENV_DISABLED=1 \
.venv/bin/python -m pytest -p no:cacheprovider -q \
  tests/test_stage2_review_contracts.py tests/test_stage2_review_migration.py \
  tests/test_review_integration.py tests/test_review_language.py tests/test_review_metrics.py \
  tests/test_review_provider.py tests/test_review_normalization.py \
  tests/test_review_recovery.py tests/test_review_remote_cleanup.py
```

[SQLite 로그](stage2-review-tests.log): 200 passed, 2 기존 deprecation warnings, 18.14초. 원천 페이지 관측 한계의 어댑터 guard, 현재 언어 모델·품질 만료의 읽기 gate, 한국어/현지어 최소 분모, 평가 유출 검사까지 반영한 최종 회귀다.

[PostgreSQL 로그](stage2-review-postgres.log)는 20 passed / 1 skipped / 2 기존 warnings / 18.21초다. loopback 전용 pgvector/PostgreSQL17에 `-p tests.postgres_plugin`을 추가한 실제 계약 시험이다. SQLite 파일 dry-run은 이 경로에서 의도적으로 건너뛴다. 이후 현재 detector/품질 읽기 gate 보강은 SQLite 최종 회귀와 메인 전체 검증에서 확인했다. 클라우드 파일 저장소/실제 Supabase에 대한 신규 실험은 아니다.

API E2E에는 직접 DB ID 변경·집계 숫자 주입이 없다. 합성 인증 fixture의 관리자 role 설정 외에는 후보팩·출처 확인·승인·연결·계약·수집·추천·상세·일정을 정상 HTTP API로 수행한다. 합성 사용자 근거에서 숫자를 숨기고 운영 기능을 OFF로 유지하는 동작도 검증한다.

[100도시 API 응답](stage2-city-language-capabilities.json)은 합성 임시환경의 응답이다. 실제 운영 DB 현황처럼 제시하지 않는다. provider 호출은 fake/mock 또는 로컬 판별만이며 유료 실제 리뷰 호출은 0회, 비용 $0다. 로그인 후 실제 브라우저 여부는 메인 2단계 보고서에 별도로 기록한다.

## 이관·복구

1. 운영 서버를 유지보수 상태로 전환하고 일관된 DB/원문 backup 및 최신 삭제 checkpoint를 확보한다.
2. SQLite 원본은 수정하지 않는 dry-run을 실행한다.

```bash
.venv/bin/python -m src.research.migration \
  --database /private/existing/service.sqlite3 \
  --report /private/schema14-dry-run.json
```

3. 보고서의 기존 canonical 수, ID 보존, 이름/외부 ID가 같은 다른 지점의 주소 충돌을 검토한다. 이 명령은 후보를 자동 승인/병합하지 않는다.
4. PostgreSQL은 분리된 schema에서 migration 시험 후 운영 image를 배포한다. 실패하면 기존 개인 요청을 열지 않는다.
5. rollback은 schema14를 이해하는 호환 image로 기능을 OFF하는 방법을 우선한다. 테이블을 내려 schema13 표시만 바꾸지 않는다. DB 복원이 필요하면 최신 external_link/contract tombstone을 함께 merge하여 철회된 연결·집계가 살아나지 않게 한다.

현행 schema12/13 snapshot import를 유지하며, 원본을 바꾸지 않는 private clone에서14로 올린다. 공개자료 연결 이력은 기존 서버 ID와 FK를 보존한다. 계정·메시지 발송·새 지출·운영 배포는 이 검증 범위에 포함하지 않았다.
