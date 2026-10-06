# 08 피드백·최소 분석·예상 지출·평가 검증

- 날짜: 2026-10-06 (Asia/Seoul)
- 기준 Git revision: `aefee57` + 기존 단계의 미커밋 작업 및 이번 변경. 기존 변경 보존, commit/push/배포 없음.
- 구현: **complete (아래 v1 범위)**. 로컬 SQLite·PostgreSQL·합성 OIDC·브라우저: **verified**. 실제 Render/Supabase 배포·실사용 성과: **not_run / 미측정**.
- 실제 사용자 데이터·유료 공급자·새 크롤링·외부 메시지를 사용하지 않았다. 합성 결과는 제품 성과가 아니다.

## 사용할 수 있는 흐름

추천 카드의 **관심 없음**과 장소 상세/일정의 **방문 기록 남기기**에서 기록을 만들고, 새 **기록·지출** 메뉴에서 수정·철회한다. 방문 못함/아직 안 감에는 현장 경험 평가가 나타나지 않는다. 방문함에만 경험·가격·대기·재방문을 선택할 수 있다. 이유는 구조화하고 메모는 선택적 개인 데이터로 저장한다. 미래 방문 완료는 거절한다.

관심 없음의 **다음 추천에 반영**은 기본 꺼짐이다. 명시적으로 켠 장소만 해당 여행의 다음 요청에 `soft_avoid_half_v1`로 반영한다. 기존 조건·필수 제약·언어 품질 gate는 그대로이며 취향 성분이 있는 경우에만 0.5배로 낮춘다. 철회/선택 해제 후 새 요청에는 반영하지 않는다. 같은 맥락 중복 생성은 409, 재전송은 Idempotency-Key로 재사용, 수정은 version을 검사한다. 원 추천이 만료되거나 일정 항목이 제거돼도 이미 소유한 기록의 맥락을 바꾸지 않고 수정·철회할 수 있다.

장소 상세의 **정보가 달라요**는 해당 fact ID와 지점에 연결된다. 일반 사용자는 자기 신고 상태, 관리자는 운영 검토 목록을 본다. `reported → triaged / needs_evidence → resolved / dismissed`를 사용한다. 해결하려면 사용 가능한 공식 출처·직접 읽음 확인·동일 필드와 적용 기간·새 확인일이 필요하다. 신고만으로 가격·영업·예약은 바뀌지 않는다. 정정 시 기존 사실의 사용을 중지하고 새 사실을 만들며 관련 추천 payload를 stale 처리한다. 일정은 기존 fact/source manifest로 재확인이 필요해진다. 기존 확정 예약의 시각이나 원 revision을 자동 수정하지 않는다.

예상 지출은 도시별 최신 일정의 방문/예약/이동 항목을 계산하며, 값이 없으면 미확인이다. 출처가 허용되고 해당 방문일에 유효한 가격만 자동 사용한다. 직접 입력은 `user_entered`이며 공식 사실을 수정하지 않는다. 인당/일행/예약당, 참여 성인·아동 요금, 세금·별도 수수료, 일수, 선결제, 보증금 성격, 포함 관계를 보존한다. 무료 0과 null을 구분한다. 아동 요금 미확인이면 알려진 성인 비용을 보수적 하한으로 유지하고 상한은 미확인이다. 환급 예치금은 지출과 별도 현금 필요액, 최종 요금 일부 선결제는 중복 추가하지 않는다. 취소 수수료는 발생 가능 항목으로 합계에서 제외한다. 포함 관계 순환/자기 참조/다른 일정 참조는 거절한다. **JPY/EUR 별도 합계, 환율 기능 및 KRW 환산 OFF**다. 이동비가 미확인이면 일정 전체 비용을 확정 총액이라고 표시하지 않는다.

## 저장·권한·삭제

주요 파일:

- `src/product/{schema,models,service,routes,events,reporting,prices,evaluation,cli}.py`
- `web/js/product.js`, `web/js/foundation.js`, `web/css/foundation.css`, `web/index.html`, `web/sw.js`
- 기존 추천·보관함·일정 revision·예약 준비 서비스의 성공 트랜잭션에 이벤트 연결
- `src/foundation/db.py`, `src/storage/{postgres,transfer}.py`: SQLite/PG schema9→10
- `src/discovery/maintenance.py`, 기존 여행 삭제 및 `src/operations/backup.py`: 철회·분석 해제 checkpoint/복원 정리

schema10은 `product_preferences`, `product_run_metrics`, `visit_feedback`, `feedback_changes`, `fact_reports`, `fact_report_actions`, `expense_overrides`와 기존 `discovery_events`의 schema/version/제외 사유를 추가한다. 과거 event는 schema0이며 새로운 노출 성과에 섞지 않는다. PostgreSQL private 스키마·RLS·서버 세션·소유권·Origin/CSRF를 기존 계층에서 재사용한다. 클라이언트 owner_id는 받지 않는다. 다른 사용자/여행/run/place 참조는 404다. 운영자 API도 관리자 여부를 검사한다.

분석 기본값은 OFF다. 개인 기능은 분석 미참여 상태에서도 사용한다. 이벤트/완료 run 최소 요약은 기본 30일(`PRODUCT_EVENT_RETENTION_DAYS`, 1~90일)이며 기동/주기 유지보수/보고서/백업 전에 만료 자료를 지운다. 분석 해제는 이벤트·최소 요약을 삭제하고 개인 기록은 보존한다. 다시 켰다고 이전 개인 기록을 소급 집계하지 않는다. 해당 version의 동의된 생성·수정 event가 있어야 피드백 이유를 집계한다. 메모 전문은 분석 payload·보고서에 포함하지 않는다.

철회 시 본문과 메모를 지우고 최소 철회 상태만 남긴다. 여행 삭제는 즉시 조회/집계에서 제외하고 기존 삭제 작업으로 종속 자료를 제거한다. `discovery_tombstones`의 feedback/analytics_owner checkpoint를 오래된 백업에 적용하면 메모·분석 기록이 부활하지 않는다. 반복 동의 해제에서는 가장 최신 cutoff가 우선한다. 기기에 이미 다운로드한 관리자 집계 JSON을 원격 삭제하는 기능은 없으며 원문/메모/정밀 위치를 export하지 않는다. 계정 전체 삭제 기능을 새로 구현했다고 주장하지 않는다.

## 이벤트·지표 계약

| 이벤트 | 기록 시점 | 제외/구분 |
|---|---|---|
| recommendation_view | 활성 탭에서 카드 50% 이상이 연속 1초 노출 | modal/백그라운드/오프라인/다른 여행/제거된 카드 제외. 동일 run/place 중복 노출은 집계 시 1쌍 |
| recommendation_save | 실제 새 보관함 저장 SQL 성공 | 클릭·중복 저장 요청은 성공 이벤트 아님 |
| recommendation_reject | 관심 없음 생성 성공 | 방문 경험과 별개, 메모 제외 |
| itinerary_add | 새 장소를 포함한 revision 실제 활성화 | preview는 제외, 기존 장소 재편집은 신규 채택 아님 |
| source_open | 출처 링크 클릭 | 원문 읽음·예약 완료가 아님 |
| booking_task_complete | 사용자 완료/증빙 대조 전이 성공 | completion_kind를 분리하고 task/kind 중복 집계 안 함 |
| visit_feedback | 생성·수정·철회 성공 | ID/action만 기록, 철회 시 이전 분석 기여 삭제 |

클라이언트 이벤트는 UUID, 고정 schema1와 허용 필드만 받는다. 클라이언트가 서버 성공 이벤트를 만들어 보내는 것은 거절한다. 같은 ID/다른 의미는 409다. 메시지·이메일·예약번호·질문·정밀 위치·건강/식단 조건은 넣지 않는다. 클라이언트 시계가 서버와 5분 이상 다르면 CLOCK_SKEW로 측정 제외한다. 브라우저 계측은 best-effort이며 전송 실패를 오프라인 큐로 재전송하지 않는다.

관리자 **기록·지출 → 운영 검토·최소 보고서**에서 UTC `[start,end)`와 도시/추천 유형/언어 요청/추천 설정 버전/합성 여부를 선택한다. `ranker`는 저장된 결과의 config_version(기본 `ranker-hypotheses-v1`)이다. 유형 필터는 해당 유형을 포함한 run 단위이며 두 구획을 함께 요청한 run을 두 독립 실험처럼 취급하지 않는다.

- 후보 부족: 목표보다 적게 반환한 완료 run / 해당 완료 run.
- 언어 조건 미지원: unsupported가 있는 언어 조건 요청 run / 언어 조건 요청 run.
- 노출 후 저장: 서버 저장이 노출 이후인 고유 run/place / 실제 노출 고유 run/place.
- 일정 채택: 노출한 장소를 실제 적용한 run / 노출 run.
- 신고는 유형/현재 상태의 건수이며 검증된 오류율이 아니다. 추천 run 없는 방문 기록은 별도 피드백 cohort 인원/건수를 보여준다.
- 완료 run은 **완료 시각**, 그 run의 노출/행동은 **기간 내 서버 수신 시각**을 사용한다. 기간 밖 run의 행동은 해당 분모에서 제외한다. 피드백은 기간 내 마지막 수정 version의 현재 상태다.
- 요청 비용은 연결된 모든 usage reservation(재시도 포함)의 정산액·unknown charge 상한·예약 상한/호출 수를 통화별 표시한다. 실패/취소 요청은 후보의 합성/실데이터 귀속을 확인할 수 없어 별도 미귀속 집계로 표시하며, 기간 내 job 종료 갱신 시각 기준의 요청 수와 요청당 정산/unknown 상한을 제공한다. 실패를 0원 환불로 처리하지 않는다. 공용 장소 조사/계정 외 사용량을 개인 추천 비용에 임의 배분하지 않는다.
- 분모 0은 null/미측정. 5 미만은 `1/2건`처럼 표시하며 통계적 성과나 인과 효과로 해석하지 않는다. 분모 5 이상도 베타 표본의 일반화를 하지 않는다.

## 평가·회고 재실행

```bash
# 네트워크 호출 없이 도쿄/바르셀로나 × 언어 조건 켜짐/꺼짐 × 두 추천 유형
.venv/bin/python -m src.product.cli evaluate-synthetic \
  --output docs/service-v2/reports/phase08-synthetic-evaluation.json
# 대안 실험: --candidate distance-v2

# 미측정 실제 지표 + 가설/관찰/반례/우선순위 양식
.venv/bin/python -m src.product.cli retrospective-template \
  --output docs/service-v2/reports/phase08-retrospective.json

# 접근 가능한 로컬 DB의 기존 관리자 ID로 집계. 결과에 개인 메모는 없음.
.venv/bin/python -m src.product.cli report \
  --database /absolute/path/to/service.sqlite3 --admin-id EXISTING_ADMIN_ID \
  --start 2026-10-01T00:00:00Z --end 2026-10-07T00:00:00Z \
  --output /private/path/product-report.json
```

cloud DB에서는 관리자 UI/API로 동일 집계를 내려받는다. CLI는 존재하지 않는 DB를 생성하거나 운영 인증을 우회하지 않는다. JSON은 로컬 파일 권한0600으로 생성한다.

평가 API/화면은 현재 정책상 사용 가능한 저장 snapshot만 받아 조건·후보·사실·정책·clock을 고정하고 설정만 바꾼다. 순위 이동/상위 겹침/성분 점수/다양성/필수 위반/자료 부족을 반환하며 자동 승자를 선언하지 않는다. 만료/회수된 출처로 과거 원문을 복구하지 않는다. LLM 설명 검증은 기존 closed-vocabulary validator를 별도 실행하고 새 장소·순서·사실을 거절한다. 실제 LLM 설명 품질은 not_run이다. [합성 결과](phase08-synthetic-evaluation.json), [미측정 회고 양식](phase08-retrospective.json). 실제 CLI로 합성 DB의 opt-out 이후 [집계 export](phase08-post-optout-synthetic-report.json)도 생성했다. 분석 참여 해제 후 이 표본의 지표 분모는0/미측정이며 개인 피드백은 남아 있었다.

## 실제 실행 결과

```bash
.venv/bin/python -m pytest tests -q
# 732 passed, 11 skipped, 2 warnings / 83.36s

# 마지막 가격 null 표시와 실제 예약/일정 revision 불변 단언 보강 후
.venv/bin/python -m pytest tests/test_product_feedback.py tests/test_product_prices.py tests/test_product_expense_api.py -q
# 21 passed, 2 warnings / 8.29s

# 일회용 localhost PostgreSQL16/pgvector에서 API 계약 + schema9 upgrade
TRAVEL_TEST_POSTGRES_DSN=<disposable-loopback-dsn> .venv/bin/python -m pytest \
  -p tests.postgres_plugin tests/test_product_feedback.py \
  tests/test_product_prices.py tests/test_product_expense_api.py -q
# 21 passed, 2 warnings / 23.50s

# 별도 cloud suite는 cross-backend plugin 없이 실행: 내부 SQLite 백업 fixture도 사용함
TRAVEL_TEST_POSTGRES_DSN=<disposable-loopback-dsn> .venv/bin/python -m pytest tests/test_cloud_storage.py -q
# 11 passed, 2 warnings / 16.34s

node --test tests/*.cjs
# 27 passed, 0 failed
node --check web/js/product.js
node --check web/js/foundation.js
.venv/bin/python scripts/version_web_assets.py --check
.venv/bin/python -m compileall -q src/product

git diff --check
# exit 0
```

전체 skip11은 위 실제 로컬PG cloud 시험으로 별도 실행했다. 기존 Starlette/Authlib httpx 호환 경고2개다. 초기에 cloud suite와 SQLite→PG 치환 plugin을 한 명령에 합쳐 cloud suite 내부 SQLite 백업 fixture가 실패했다. 각 목적에 맞게 분리해 위 결과를 얻었다. 테스트 작성 중 잘못 쓴 preview 경로·fixture 반환 순서·schema_version 호출은 고쳤으며 미해결 테스트 실패는 없다.

새 시험은 UUID 중복/다른 payload·사용자/여행/run/source404·미참여/재참여·메모 제외·version 충돌·철회·trip 삭제·백업 이후 철회·반복 opt-out·0분모·cohort 분리·시계 차이·실제 저장/적용·preview 제외·완료 종류·가격/아동/세금/통화/보증금/선결제/포함 순환·평가 재현/만료·retry 비용/unknown charge·schema9 이관을 다룬다. 브라우저 상태 단위시험은 50%/연속1초·중복 구획·modal·숨김·offline·scope 교체를 검증한다. AUTH-02/06/08, DATA-06/07, BOOK-05, REC-02/03/05/06 관련 권한·삭제·계산 계약을 기존 회귀와 함께 확인했다.

## 실제 브라우저 확인과 한계

Codex 내장 브라우저, localhost 합성 OIDC/가짜 경로/도쿄 합성 후보를 사용했다.

1. A 로그인 → 일정에서 방문 못함 → 현장 평가 입력 없음 → 사유/개인 메모 저장.
2. 기록 수정 → 새로고침 후 유지 → 철회 → 목록 제거.
3. 4명 일행 전체 예상1,000~2,000JPY, 수수료0, 환급 예치금300, 선결제500 → 남은 현금800~1,800JPY. 이동비는 미확인 유지.
4. 분석 참여 → 새 추천 요청 → 실제 두 카드 노출 → 한 장소 저장 → 다른 장소 관심 없음.
5. 합성 보고서: 완료 run1/노출 run1/후보6, 저장1/노출2건, 이유 거리1건. **계측 시험값이며 실제 사용자 성과가 아니다.** 실제 보고서는 사용자0/run0·미측정.
6. 같은 입력 버전 비교 열기, 필수 위반0/0 확인. 가격 사실 신고 → 공식 근거 필요 처리 → 사용자 상태 반영. 공식 근거 정정·예약 불변은 API 시험으로 별도 확인.
7. 서버 재시작 후 기록·가격 유지. 분석 해제 후 지표 삭제와 개인 기록 유지. A 로그아웃 → B 초대 로그인 → 여행 없음, A 기록·금액·관리자 검토 미표시.
8. 390×844와 글자200% 가로 넘침0. 이유 체크박스 각각 터치 영역 분리. Escape로 모달 닫기 후 수정 버튼에 키보드 포커스 복귀. 브라우저 console error0. 테스트 탭·서버·DB 컨테이너는 정리했다.

[모바일 예상 지출 화면](phase08-mobile-cost.png). 실제 Safari/iOS/Android/PWA 설치/기기 저장 공간·실제 OAuth/HTTPS/Render cold start는 이번 단계에서 검증하지 않았다. 공식 현장 가격·실제 방문 만족도·라이브 LLM·신규 리뷰 수집의 정확도나 성과로 해석하지 않는다. 이번 단계는 현지 언어 기능을 켜지 않았고 승인 운영 후보는 계속 도쿄0/바르셀로나0이다.

## 관찰 → 다음 1순위

실사용 관찰은 **미측정**이다. 확인된 운영 병목은 기존 후보팩의 사용 승인/최신 지점별 근거가 준비되지 않아 유효 운영 후보가 각0곳인 점이다. 다음 작업은 **기존 도쿄·바르셀로나 후보 중 각3곳의 지점·영업·예약·인원·가격·표시 권한을 검수하고, 지정 방문일 기준의 사용 가능/미확인을 채우는 것** 하나를 권한다. 새 유료 API 없이 수동 검수부터 할 수 있고, 실제 선택 흐름을 시험할 데이터를 확보한다. 정정 가능한 자료와 허용 범위가 전제이며 자료 부족을 추정으로 채우지 않는다.

예약 알림은 실제 기한 누락 근거와 검증된 오픈 규칙·발송 동의가 필요하다. 추가 도시는 데이터 검수 비용이 크고 기존 도시 검증에 의존한다. 동행자 투표는 공유 요청 근거 및 멤버십·회수·동시 편집 설계가 필요하다. 이 세 기능은 이번에 구현하지 않았으며 사용자 문제/가치/비용/데이터 의존성 비교는 회고 JSON에 남겼다. 실시간 혼잡·잔여석·자동 예약은 별도 공급자/거래 검증 범위다.
