# 03 리뷰 데이터 운영·검증 안내

이 단계는 리뷰 수집과 관측 언어 근거의 기반이다. 장소 종합 추천·이동 동선·일정 생성은 04단계에서 연결한다. 실제 여행·메일 자료와 리뷰를 합쳐 Chroma에 넣지 않는다.

## 실행과 기본 상태

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

기존 OIDC·초대 인증이 선행한다. 운영 인증 미설정 상태를 여는 임시 로그인 API는 없다. `APIFY_TOKEN` 없이도 예약 서비스는 시작되며 리뷰의 실제 호출만 차단한다. Lingua는 설치 패키지에 포함된 오프라인 모델을 사용한다. 실행 중 모델 다운로드·리뷰별 LLM 전송은 없다. 설치 용량/메모리 결과는 [언어 평가](reports/LANGUAGE_EVALUATION.md)를 참고한다.

SQLite migration 3이 `review_controls`, `provider_policies`, `place_identities`, `trip_places`, `review_collection_runs`, `review_call_receipts`, `review_remote_cleanup`, `review_aggregates`, `review_quality_evaluations`, `research_tombstones`, `research_audit`를 추가한다. 기존 v1/v2 테이블과 데이터를 유지한다. 연구와 운영 플래그는 모두 OFF로 시작한다. 실제 서버 반영 전 기존 백업 명령으로 백업한다.

최초 관리자 지정은 로그인한 사용자 ID를 확인하고 서버를 정지한 상태에서 로컬 운영자 CLI로 수행한다. 공개 API로 role을 변경할 수 없다. 변경하면 해당 사용자의 세션을 회수하므로 다시 로그인한다.

```bash
.venv/bin/python -m src.foundation.cli --database /private/service.sqlite3 set-role --user-id USER_ID --role admin --offline
```

## 관리자 조사 순서

1. 관리자 계정으로 **리뷰 근거 → 지점 등록**에서 이름·주소·도시·Maps 지점 URL·외부 ID를 기록한다. URL은 검증용으로 저장하며 서버가 임의 URL을 가져오지 않는다. 단축 URL·다른 origin·비HTTP·인증 포함 URL은 거절한다. 지점 검색/확인은 수집과 분리된 수동 과정이며 유료 장소 검색 API는 아직 연결하지 않았다.
2. 같은 상호의 다른 지점, 이전·폐업 여부를 확인하고 근거와 함께 `verified`로 변경한다. 모호하면 수집을 시작하지 않는다.
3. **이용 범위 기록**에서 접근·수집·로컬 계산·원문 저장·집계 저장·ID 보관·LLM 전달·사용자 표시를 각각 기록한다. 권한을 자동 전파하지 않는다. 분석 작업은 접근/수집/계산/집계/ID 보관이 모두 확인되어야 한다. 수집만 허용된 정책으로 분석을 실행하지 않는다.
4. Apify는 원격 dataset에 원문을 저장하므로 `raw_store=true`와 공급자 측 보관 기한(최대 86,400초)을 따로 검토해야 한다. 앱은 허용 여부와 관계없이 리뷰 본문/번역문/작성자/프로필/본문 hash를 로컬 DB·로그·Chroma·작업 영수증·SSE에 저장하지 않는다. 표본 원문 대조는 실제 이용 가능한 공급자/원천 화면에서 수행해야 한다.
5. 공식 가격과 계정 조건을 확인하고 [가격 template](examples/review-pricing.template.json)의 미확정 값을 채워 `PRICING_CONFIG`에 연결한다. 기존 LLM SKU와 함께 사용하는 파일로 합쳐야 한다. null 가격과 미확정 timestamp는 유료 호출을 막는다. 예시 `usd_micros:1`은 USD 백만분의 1의 단위 환산이며 과거 Actor 단가가 아니다.
6. **기능 상태 관리 → 관리자 조사 허용**을 켜고 장소별 **범위를 정해 수집**한다. 최신순/180일/최대 200고유 기록(별점-only 포함)/20페이지/페이지당 2시도는 서버 상한이다. Actor 실행 timeout·maxTotalChargeUsd를 고정한다. 공급자 내부 원천 페이지 수와 dataset offset 페이지를 같은 것으로 주장하지 않는다.
7. 작업 목록은 실제 단계·처리 수·수신/고유 수·T/C/U/L/K·기간·비용·종료 사유를 표시한다. 새로고침은 GET으로 기존 작업을 복원하며 수집을 다시 시작하지 않는다. 같은 Idempotency-Key/입력은 기존 작업, 다른 입력은 409다.
8. 부분/실패 작업은 이유와 상한을 확인한 뒤 명시적으로 새 관측을 요청한다. 이번 버전은 **범위가 제한된 전체 재관측**을 선택한다. 검증하지 않은 증분 병합/첫 기존 ID에서 중단/누락 리뷰 삭제 추정은 하지 않는다. 정상 재관측의 `last_full_reconciliation_at`을 저장한다. 실패한 갱신은 기존 유효 집계의 포인터와 확인 시각을 바꾸지 않는다.

## 비용·재시작·취소·삭제

모든 호출은 기존 원자적 사용자·전역 일/월 예산과 동시성 1의 external slot을 사용한다. 실행별 USD 상한도 적용한다. Apify Actor 실행 한도는 전체 요청 상한에서 최대 poll(150회), page(20×2회), 취소/삭제와 삭제 확인 비용을 뺀 값이다. 취소·dataset 삭제·run 삭제 및 최대 3회 중단 확인 비용을 Actor 시작 전에 예약한다. 실제 읽기·보관 비용을 확인할 수 없으면 잠정 응답만으로 비용을 확정하지 않고 pending/unknown으로 유지한다.

공급자 시작 ID와 페이지별 최소 영수증을 SQL에 저장한다. 페이지는 원문 분류와 민감 필드 제거 후에만 저장한다. 재시작 시 이미 처리한 페이지와 결과를 재사용한다. 응답 유실은 새 Actor 시작이나 같은 paid call 자동 재실행으로 보충하지 않는다. 최종 집계가 저장된 뒤 프로세스가 종료되어도 기존 aggregate를 다시 반환한다. 원문이 없는 상태에서 판별기 버전이 바뀌면 `DETECTOR_VERSION_CHANGED`로 중단하고 새 관측을 요구한다.

취소 요청은 이미 발생한 외부 과금을 취소하지 않는다. 실행을 취소/실패/만료한 경우에도 수신한 최소 원격 ID를 삭제 전용 outbox에 기록한다. 원격 Actor가 끝났거나 실제 중단을 확인한 뒤 dataset과 run을 삭제한다. lifespan maintenance가 30초마다 만료와 삭제 대기를 확인한다. 원격 삭제는 최대 3회 유지보수 시도이며 불명확한 paid 응답을 자동 중복 호출하지 않는다. 실패하면 outbox의 ID·오류·기한을 유지하고 관리자에게 별도 확인을 요구한다. 운영자 회수 등으로 원래 비용 actor가 비활성화되었거나 실제 시작 응답을 잃어 원격 ID가 없는 경우에는 공급자 콘솔에서 실행·과금·삭제를 확인해야 한다. 이를 삭제 완료라고 표시하지 않는다.

정책 철회/지점 삭제/ID·집계 보관 만료 시 접근 차단과 tombstone을 먼저 적용한다. 늦은 완료는 집계를 활성화하지 못한다. 일반 job/SSE는 철회된 범위를 404로 차단하고, 관리자 run 화면은 수치 없는 최소 상태를 보여준다. 원격 삭제에 필요한 최소 참조는 확인될 때까지 삭제용으로만 보존한다. TTL은 이용 허가를 새로 만드는 설정이 아니다.

## 백업 복원

서버를 정지하고 기존 `src.foundation.cli backup/restore`를 사용한다. `restore --deletions-from-db CURRENT_DB`가 현재 연구 tombstone도 가져와 과거 집계·영수증을 지우고 모든 세션을 무효화한다. 과거 백업에 없는 이후 철회 내역을 재생성할 수 없으므로 현재 DB 또는 별도 삭제 manifest를 보존해야 한다.

```bash
.venv/bin/python -m src.research.maintenance export --database /private/current.sqlite3 --manifest /private/research-deletions.json
# 새 위치로 복원하고, 서비스 시작 전에 적용
.venv/bin/python -m src.research.maintenance merge --database /private/restored/database.sqlite3 --manifest /private/research-deletions.json
```

원격 dataset 삭제는 SQLite 복원으로 되돌릴 수 없다. 따라서 복원 후 중간 수집 작업은 새 세션/권한 확인과 원격 상태 대조가 필요할 수 있다. 정지한 서버 상태에서 백업·복원하는 계약을 유지한다.

## 품질과 사용자 표시

`T=C+U`, `R=T+rating_only+extraction_unknown`, `L+K≤C`를 순수 함수에서 검증한다. 비율은 표시용 `L/C`, `K/C`, 엄격 조건은 `L/T`, `(K+U)/T`이고 분모 0은 null이다. 초기 기준은 버전 관리된 가설값 C≥100, U/T≤10%, L/T≥60%, (K+U)/T≤10%다. 평점·전체 평가 수는 별도로 보관하며 언어 분모에 섞지 않는다. 종합 장소 추천 판정은 이 단계에서 구현하지 않는다.

현지어 집합은 Tokyo=ja, Barcelona=es+ca다. 번역만 있으면 T와 U에 포함하고 본문 존재 자체를 모르면 추출 불명으로 남긴다. 페이지 locale/hl은 원문 언어가 아니다. 오래된/부분/선택/불명확한 수집은 엄격 조건을 통과하지 않는다. `population_estimate`, 거주지 추정과 최신순 표본에 대한 통계적 전체 비율 추정은 꺼져 있다.

`POST /api/v2/admin/review-quality-evaluations`는 관리자 검토한 보고서 hash/출처/도시/판별 버전/독립 라벨 수/TP·FP·FN/원문 대조 수를 기록한다. 일반 문장 corpus, 합성 자료, 한국어 정답 0건, 목표 미달은 passed가 아니다. 도시별 실제 음식점 리뷰 100건 이상·원문 대조 20건 이상·오역 원문 혼동 0·현지어 precision/한국어 recall 95% 이상이 초기 목표다. 원문/정렬/연속성/이용권/신선도도 수집별로 따로 통과해야 한다. production은 실제 공급자와 도시별 평가 및 실제 집계 근거 없이는 켜지지 않는다.

여행 소유자가 검증된 장소 ID를 여행에 연결하면 `/api/v2/trips/{trip_id}/places/{place_id}/review-evidence`에서 저장된 DTO를 받는다. 타인 여행/연결 안 된 장소는 404다. 사용자 근거 화면은 관측 범위·T/C/U·분모·확인일을 표시할 준비가 되어 있으며, 정책·품질/기능 미통과일 때는 unavailable 사유를 반환한다. 엄격 기능이 꺼져 있으면 대체 언어 비율을 만들지 않는다.

## 실험 A/B와 재현

[파일럿 실행 계획](examples/review-pilot-plan.json)은 실행 설정이며 실제 장소 ID는 아직 비어 있다. A(도쿄3/바르셀로나3×100) 검증 후 B(각5×200)를 수행한다. $3는 전체 실험 제안 상한이며 결제 승인이 아니다. 무료 크레딧 잔여량·카드/최소 결제·dataset 읽기/보관·부가 비용을 계정에서 확인한다. 계정/권한/지점 확인 없이 빈 target를 임의로 채워 실행하지 않는다. 실제 B에서는 10곳 중 9곳 이상 해석 가능한 결과 또는 부족 사유, 100개 이상 공개 리뷰 후보 중 8곳 이상에서 원문 5개 초과 여부를 따로 기록한다.

```bash
OPENAI_API_KEY=test SEED_ON_EMPTY=0 .venv/bin/python -m pytest -q
node --test tests/test_service_worker.cjs
node --check web/js/foundation.js
# 로컬 합성 브라우저: 별도 임시 DB, 합성 OIDC 8766 / 서비스 8765
.venv/bin/python tests/review_browser_fixture.py
# 실제 로컬 모델 평가: 명시적 corpus 다운로드 후 재실행 가능
.venv/bin/python scripts/review_language_eval.py --fetch-corpus
```

실제 결과와 미검증 항목은 [파일럿 보고서](reports/review-pilot-2026-10-01.md), 공급자 공식 자료는 [Apify 확인 기록](reports/APIFY_PROVIDER_VERIFICATION.md), 언어 모델 결과는 [평가 보고서](reports/LANGUAGE_EVALUATION.md)에 기록한다.
