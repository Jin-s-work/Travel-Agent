# 후보·리뷰·사진 자료 범위

2026-10-08 · 실제 저장 자료와 합성 성능 시험을 분리한 보고서

## 실제 데이터

08:55:34 UTC, 기존 Supabase의 공용 장소 테이블을 **읽기 전용 트랜잭션**으로 조회했다. 개인 여행·메일·좌표·리뷰 원문을 내보내지 않았다. 조사 때문에 새 장소나 리뷰/사진을 수집하지 않았다.

| 도시 | 저장 canonical 지점 | 현재 카탈로그 분류 | 만료되지 않은 사실 보유 지점 | 활성 실제 리뷰 집계 | 승인 리뷰 지점 연결 | 현재 허용 사진 지점/자산 |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| 도쿄 | 50 | 식당50 | 50 | 0 | 0 | 2곳 / 5장 |
| 바르셀로나 | 3 | 식당3 | 3 | 0 | 0 | 2곳 / 6장 |

‘사실 보유’는 적어도 유효 사실이 있다는 뜻이다. 방문일 운영·인원·가격·현지어/유명함이 모두 검증되었다는 뜻이 아니다. 그 자격은 해당 여행 조건으로 별도 판정한다. 카페·볼거리 등 다른 유형의 충분한 실제 자료를 이번 조회로 입증하지 않았다.

도시 입력 카탈로그 지원과 도시별 추천 근거 지원을 구분한다. 이 수치로 100개 도시의 리뷰 지원이나 도시당 실제200곳 확보를 주장하지 않는다. **새 수집기를 실제 운영에 배포하거나 도시별200곳을 실수집한 상태는 아니다.**

## 후보 확보 변화

기존 Photon 응답당 최대60개 제한은 보존한다. [공식 Photon API](https://github.com/komoot/photon/blob/master/docs/api-v1.md)에 문서화된 공개 좌표/반경 조회를 사용해 최대7개 공개 지역의 고유 지점을 합친다. 원천 cursor는 문서화되지 않아 만들어 쓰지 않는다. [공유 demo의 사용 제약](https://github.com/komoot/photon)을 따라 동일 endpoint와 기존 전역20회/일·사용자5회/일·15초 cooldown을 유지한다.

- 후보 pool 최대200, raw 최대400, query 최대7, deadline 기본120초(검토된 최대180초), 한 응답 최대60.
- 기본 사용자5회/일 제한은 query7보다 우선한다. 새 호출5회로 중복이 많으면200곳에 도달하지 못한다. 유효한 공개 캐시와 저장 카탈로그는 함께 재사용한다.
- 공개 도시 중심과 인접 고정 지역을 샘플링한다. 숙소 좌표를 외부로 보내지 않는다. 숙소는 내부 거리 정렬에 사용한다. 도시 전체 또는 숙소 주변을 전수 수집한 결과라고 표시하지 않는다.
- query cache는 공급자/버전/도시/공개 지역/분류/반경/응답 상한에 묶인다. 요청 hash를 비용 호출 키에 포함해 같은 job의 다른 지역 조회를 구분한다.
- 완료 지역을 checkpoint로 저장한다. 재개 시 완료 조회를 다시 실행하지 않으며 조회 설정이 바뀌면 새 요청을 요구한다.
- 후보 전체를 먼저 평가하고 저장한 순서를12개씩 읽는다. 추가 보기/상세 GET은 새 후보 수집이나 재정렬을 하지 않는다.

실제 외부 지도 조회는 이번 단계0회다. 합성 HTTP/provider 경로에서는 **4회 조회→수신240→고유 pool200 평가**, **중복 자료185개 수신→고유37개**, **raw73 설정→60+13 두 호출**을 검증했다. 이는 실제 두 도시의 확보 수가 아니다.

## counts 의미

| 필드 | 단위/의미 | 중복·미확인 처리 |
| --- | --- | --- |
| requested_limit | 요청 snapshot의 canonical 후보 상한(기본200) | 확보 보장 수가 아님 |
| fetched_count | 이번 조회 계획의 실제 네트워크 수신 장소 레코드 | 캐시 읽기는0, 공급자가 수신 수를 입증하지 못하면 null |
| unique_count | 최종 최대200 pool의 서로 다른 canonical ID | 두 추천 구획에 있어도1곳 |
| deduped_count | 수집 응답 내부/지역 간 ID 중복으로 제거한 수 | 조건 탈락·범위 밖·잘못된 원문·200개 절단 수와 별도. 저장 카탈로그 전체의 중복 통계는 아님 |
| normalization_rejected_count | 공급자 정규화에서 제외한 레코드 | 관측 못하면 null, 중복 수에 합치지 않음 |
| eligible_count | 저장 결과에서 자격을 충족한 고유 ID | 구획 합산의 중복 제거, reference 자격과 리뷰 자격은 별도 |
| scored_count | ranking_diagnostics를 계산한 표시 가능한 고유 ID | 자격 탈락은 점수로 복구하지 않음 |
| evaluated_count | 실제 검증 파이프라인에 들어간 pool 수 | scored와 다를 수 있음 |
| displayable_count | 이 run에서 표시 가능한 고유 ID | 전체 페이지의 합집합 |
| displayed_count | 현재 반환 페이지에 담긴 고유 ID | 실제 화면 노출 analytics와 다름 |
| deferred_count | 전체 표시 가능 수 − 현재 응답 표시 수 | 사용자가 과거에 보았는지는 추적하지 않음 |
| coverage | 저장 카탈로그/공개 지리 샘플 영역·확인일·캐시·공급자 상태 | complete_city=false, 개인 원점 미전송 |
| truncation_reason | candidate/raw cap, deadline, 일일 한도, 공급자 종료 등 | 적은 결과를200으로 보충하지 않음 |

후보 선정은 `scope_distance_evidence_v2`: 범위와 자격/알려진 실패 → 출발점/도심 직선거리 → canonical ID의 안정적 tie-break다. 입력 응답 순서나 이름순으로 먼저60개를 자르지 않는다. `stored_candidates_considered`, 분류별 pool 수, 사전 제외 사유도 저장한다. 다른 공급자 ID를 자동으로 같은 지점으로 합치지 않고 기존 canonical 연결을 사용한다.

## 사진

사진 권한·출처·만료·정확한 지점 검사를 수행하는 실제 `for_places` 경로로 집계했다. raw manifest에 있다는 이유만으로 허용 자산으로 세지 않았다.

| 도시 | available | 현재 허용 사진 없음(no_photo) | 기록된 negative cache | 브라우저 img load 성공률 |
| --- | ---: | ---: | --- | --- |
| 도쿄 | 2/50 | 48/50 (96%) | no_photo12곳 | null: 미측정 |
| 바르셀로나 | 2/3 | 1/3 (33.3%) | 없음 | null: 미측정 |

나머지 no_photo는 현재 표시 가능한 자산이 없다는 뜻이며 해당 식당의 사진이 인터넷에 전혀 없다는 증거가 아니다. 실제 네트워크 실패율·실노출 지점 오류율도 미측정이다. 사진이 있는4곳만 골라 서비스 전체의 사진 성공률로 계산하지 않았다.

사진 상태 `available/loading/no_photo/provider_failed/expired/policy_blocked/identity_unresolved`를 분리했다. 로컬 loading은 브라우저 이벤트 상태다. 무사진 negative cache 기본7일, 공급자 실패 backoff15분은 기존 정책을 유지한다. 최대3장, 첫 상세 사진 우선/나머지 lazy load, 공간 예약, 현재 슬라이드만 로드, 실패 안내와 지도 링크를 연결했다. 음식·내부·외관이 명시된 경우에만 유형을 표시하며 unknown을 임의 분류하지 않는다.

새 사진 유료 공급자와 Google Place Photos는 연결하지 않았다. Apify 리뷰 키만으로 사진 권리/서비스가 마련되었다고 간주하지 않는다. 사진 수는 추천 점수에 사용하지 않는다.

## 재확인 명령

프로젝트 루트, 설치된 Python 환경에서 실행한다. 아래 환경 파일의 값은 출력하지 않는다.

```sh
PYTHON_DOTENV_DISABLED=1 python scripts/v5_data_coverage.py \
  --env deploy/render-supabase/.env --output /tmp/going-v5-coverage.json
PYTHON_DOTENV_DISABLED=1 python scripts/check_apify_setup.py \
  --env deploy/render-supabase/.env --include-active --output /tmp/going-v5-apify.json
PYTHON_DOTENV_DISABLED=1 python scripts/plan_v5_review_pilot.py \
  --preflight /tmp/going-v5-apify.json --coverage /tmp/going-v5-coverage.json \
  --output /tmp/going-v5-pilot-plan.json
```

세 번째 명령의 승인/설정 상한 기본값은0이다. 관리자 기록에 이미 승인된 금액이 있을 때만 그 범위의 `--approved-cap`, `--configured-cap`을 사용한다. 명령은 계획만 만들며 원격 run을 시작하지 않는다. 실제 결과의 안전한 요약은 [stage2-measurements.json](stage2-measurements.json)에 보관했다.
