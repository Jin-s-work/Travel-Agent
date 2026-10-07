# 검수 장소 등록과 추천 자료 부족 처리

2026-10-06. 추천 품질 검증 완료와 장소 기초 자료 등록은 별개다.

## 이번 원인과 변경

기존 운영 DB에는 승인된 실제 후보팩이 없었다. 엔진의 후보가 0이므로 추천 요청을 반복해도 새 식당이 생기지 않았다. 또한 기존 엔진은 방문 조건이나 점수 성분이 미확인이면 `items`가 아닌 `needs_confirmation`/`insufficient_data`로 분리한다. 이 값을 합격 추천 수 0과 혼동해서는 안 된다.

엔진의 점수·언어·평점·인원·영업·거리 기준은 변경하지 않았다. 결과에 `summary`를 추가해 확인할 장소와 조건 충족 추천을 구분한다. `qualified_count`, `confirmation_count`, `reference_count`, `displayable_count`는 두 추천 구획의 중복 지점을 제외하며 우선순위는 합격 → 확인 필요 → 참고다. 실제 조건 위반 지점은 노출 가능한 장소 수에 포함하지 않는다.

- 자료 자체 없음: `CATALOG_EMPTY` → 이름/링크 저장, 조건 확인.
- 선택한 종류의 자료 없음: `CATEGORY_NO_CANDIDATES` → 종류 변경, 장소 저장.
- 확인된 조건 불충족: `CONDITIONS_NOT_MET` → 사용자가 조건을 수정한 새 요청.
- 미확인 자료가 있는 실제 장소: 카드에 확인 필요를 표시하며 합격 추천으로 세지 않음.

후보팩의 중복 키는 `(version, city)`다. 동일 내용 재등록은 원래 상태와 ID를 반환한다. 다른 내용의 동일 버전 또는 과거 원본 해시를 확인할 수 없는 중복은 `PACK_VERSION_CONFLICT`다. 내용 변경은 새 버전과 새 출처 검토가 필요하다. 철회 출처·중단 팩·삭제 지점은 재등록으로 부활하지 않는다.

## 실제 처리 단계

`candidate_snapshot` → `route_snapshot` → `constraints_and_scoring` → `source_revalidation` → `recommendation_complete`.

후보 저장은 0/1 → 1/1, 조건 검사는 실제 후보 수, 경로는 실제 요청/수신 element 수다. 제공자가 꺼졌거나 좌표가 없으면 경로가 0건이며 추정 진행률이나 인위적 지연을 추가하지 않는다. SSE 재연결은 기존 job 이벤트를 읽는다. 마지막 결과 저장까지 소유권·lease·여행 삭제·출처·출발점 버전을 재검증한다.

## 운영 도구

아래 dry-run은 DB를 열지 않고 파일 계약만 검사한다. `.env` 자동 로드를 하지 않는다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/register_discovery_catalog.py \
  --pack docs/service-v3/data/official-restaurants-2026-10-06.json
```

실제 적용은 기존 DB 설정을 프로세스 환경에 안전하게 공급한 뒤 수행한다. 관리자 ID는 기존 계정이어야 하며 만료되지 않은 기존 관리자 세션이 필요하다. 새 계정·세션을 만들지 않는다. 검토 메모 파일에는 비밀이 아니라 지점 확인 및 최소 사실의 이용 범위를 기록한다.

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/register_discovery_catalog.py \
  --pack docs/service-v3/data/official-restaurants-2026-10-06.json \
  --apply --admin-user-id EXISTING_ADMIN_ID \
  --approve-reviewed --review-evidence-file /private/path/catalog-review.txt
```

`--approve-reviewed`를 생략하면 `needs_review`로만 등록한다. 승인을 지정해도 `read_confirmed` 또는 `display_permitted`가 거짓인 출처는 활성화하지 않는다. 이미 활성인 출처와 승인 팩은 버전을 바꾸지 않는다. 중간 실패 후 같은 입력으로 재실행할 수 있다. 폐기된 출처는 새 출처 키와 별도 검토가 필요하다.

도구는 등록만 수행한다. 유료 API·URL 수집·메일 전송·개인 여행 수정·dispatcher 실행이 없으며 공급자 설정도 바꾸지 않는다. 3도시 각 3곳을 등록한다고 100도시 전체에서 식당 추천을 지원하는 것은 아니다. 소유권이 다른 계정의 개인 메모나 여행 데이터는 후보팩에 넣지 않는다.

## 검증

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest \
  tests/test_catalog_presentation.py tests/test_catalog_registration.py \
  tests/test_recommendation_api.py tests/test_discovery_foundation.py -q

PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m pytest \
  tests/test_catalog_registration.py tests/test_catalog_presentation.py \
  tests/test_recommendation_engine.py tests/test_stage2_recommendations.py -q
```

첫 실행 53 passed(12.55s), 두 번째 91 passed(5.19s), 각각 기존 Starlette/Authlib deprecation warning 2건. 두 집합은 일부 중복이므로 합산한 고유 시험 수가 아니다.

두 번째 집합에서 공식 자료 3도시 × 3곳을 임시 인증·SQL 서비스에 등록했다. 각 도시의 실제 API job 성공, `catalog_count=3`, `displayable_count=3`, `qualified_count=0`, `needs_confirmation`을 확인했다. 기존 평점 필터는 계속 켜져 있었고, 점수는 null, 좌표 없는 거리는 null, 실제 잔여석은 미확인이다. 사용량 ledger는 0이며 실공급자를 호출하지 않았다. 단계 SSE는 실제 후보 3건 및 정의된 순서를 확인했다.

이 보고서의 API 시험은 격리 환경의 코드·계약 시험이다. 운영 등록·브라우저 사용성·실제 장소 방문 품질·식당 응답·Google 리뷰 검증 완료를 뜻하지 않는다. 운영 반영 결과는 상위 진행 기록에 별도로 남긴다.
