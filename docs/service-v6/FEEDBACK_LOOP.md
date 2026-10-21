# 피드백 루프 (4단계 묶음 D)

2026-10-21 작성, 같은 날 검수 반영. 코드와 합성 시험까지 완료했다. 실제 사용자 효과는 측정하지 않았다(미측정).

## 한눈에

| 항목 | 기본값 | 켜는 방법 | 되돌리는 방법 |
| --- | --- | --- | --- |
| D1 덜 보기 / 제외 | 기록만 하고 반영 안 함 | 관심 없음 기록에서 ‘취향 추천에 반영’을 직접 선택 | 선택 해제 또는 기록 철회 |
| D1 적용 범위 | 이 여행만 | 반영 방식에서 ‘다음 여행에도’ 선택 | 기록 철회, 또는 기록한 여행 삭제. 둘 다 이후 실행부터 적용되고, 이미 다른 여행 실행 기록에 복사된 장소 ID는 남는다(‘알려진 한계’) |
| D2 저장 기반 취향 | OFF | 추천 요청에 `saved_taste: true` (API 전용, 화면 토글 없음) | 요청에서 빼면 됨 |
| D3 저장한 곳 가까운 순서 | 최근 저장 순 | `GET /api/v2/trips/{trip}/bookmarks?order=nearest[&anchor=<bookmark id>]` | `order` 생략 |

어느 기능도 외부 호출을 하지 않고, 사용자 행동을 학습하지 않는다. v5 점수식·가중치·`soft_avoid` 0.5배·승격 규칙·기본 모델(hybrid_v5)은 바꾸지 않았다.

## D1. 이번 여행에서 덜 보기 / 제외

기존 피드백 모델(`visit_feedback`, `Feedback`)에 필드 두 개를 더했다. 새 테이블은 만들지 않았다.

- `preference_action`: `less`(기본, 기존 덜 보기 ×0.5) 또는 `exclude`(이번 추천에서 제외)
- `preference_scope`: `this_trip`(기본) 또는 `future`(다음 여행에도)

규칙:

- `exclude`와 `future`는 `reflect_preference=true`일 때만 받는다. 아니면 422. 클릭 한 번으로는 효과가 넓어지지 않는다.
- 제외는 기존 `USER_EXCLUDED` 판정을 쓴다. 채점용 복사본에만 표시하므로 저장된 후보와 자료 변경 검사는 그대로다.
- `future` 기록은 같은 사용자의 다른 여행(삭제되지 않은 여행) 추천에도 쓴다. 다른 여행 ID는 스냅숏에 남기지 않고 건수만 남긴다. 단 반영한 장소 ID(`place_id`)는 그 실행의 스냅숏에 들어간다(아래 ‘알려진 한계’).
- 철회하면 이후 모든 여행의 실행에서 반영을 멈춘다. 기록한 여행을 지우면 기존 여행 삭제 정리(`scrub_trip`)로 기록도 함께 지워진다. 어느 쪽도 이미 끝난 실행의 스냅숏은 고치지 않는다.
- 피드백 목록 응답에 `carried_from_other_trips`가 붙는다. 다른 여행에서 ‘다음 여행에도’로 고른 선택을 보여 주고, 그 자리에서 철회할 수 있다. 개인 메모는 넣지 않는다.
- 이 목록은 추천에 반영하는 집합과 같은 함수(`feedback_loop.carried_rows`)로 만든다. 따로 자르는 개수 제한이 없어서, 다른 여행에 방문 기록이 많아도 반영 중인 선택이 목록에서 빠지지 않는다.
- 스냅숏 키: `soft_avoid_place_ids`, `feedback_excluded_place_ids`, `feedback_inputs`(건수), `feedback_scope_policy_version=trip_feedback_scope_v1`. 기존 `feedback_policy_version=soft_avoid_half_v1`은 유지한다.
- 두 제출 경로(추천 요청, discovery intent)는 같은 함수 `src/recommendations/feedback_loop.py:apply_to_snapshot`을 쓴다.
- 새 필드의 기본값은 멱등 키 해시에서 뺀다. 그래서 배포 전에 보낸 요청을 다시 보내도 충돌하지 않는다.

화면:

- 관심 없음 기록 창: 기존 ‘취향 추천에 반영’ 체크박스는 그대로 두고, ‘반영 방식’ 선택 칸을 하나 더했다. 선택지는 이 여행에서 덜 보기 / 이 여행 추천에서 제외 / 다음 여행에도 덜 보기 / 다음 여행에도 제외 4가지다. 새 메뉴나 토글은 없다.
- 추천 결과 구획 제목 아래에 ‘적용 중: 덜 보기 N곳 · 제외 N곳 …’을 보여 준다. 이 실행의 후보와 겹치는 선택만 센다(`result.feedback_match`). 예를 들어 바르셀로나 여행에 도쿄 장소의 ‘다음 여행에도’ 선택만 있으면 ‘적용 중’을 띄우지 않는다. 후보에 없는 선택이 있으면 ‘이번 후보에 없는 선택 N곳은 영향이 없어요’를 덧붙인다.
- 온라인 측정의 `feedback_applied` 플래그도 같은 겹침 수로 정한다. `feedback_match`가 없는 이전 실행은 예전 규칙(선택이 하나라도 있으면 참)을 그대로 쓴다.

## D2. 저장한 장소 기반 여행 범위 취향 (기본 OFF)

- 이 여행에서 저장했고 지점이 확인된 장소만 쓴다. 같은 도시, 최근 20곳까지다. 덜 보기·제외한 장소와 기존 추천 제외 장소는 빠진다.
- 태그는 hybrid_v5의 문서 규칙을 그대로 따른다. 순서는 검증된 `tags` 사실 → 승인 팩 태그 → 공개지도 잠정 요리 한 가지다. 이름·리뷰·행동으로 추정하지 않는다.
- 상한:
  - 새 태그는 최대 3개다. 저장 장소 수가 많은 태그부터 고르고, 같으면 이름순이다.
  - 사용자가 입력한 태그와 별칭이 같은 태그는 다시 넣지 않는다.
  - 취향 성분 가중치는 v5 프로필 값 그대로다(taste 0.45, taste_origin 0.40). 저장 기반 태그는 입력 태그와 그 한 성분을 나눠 쓰므로 영향이 그 값을 넘지 못한다. 이 값은 `preference_weight_cap`으로 기록한다.
  - 사용자가 취향을 입력하지 않은 경우, 켜면 v5 프로필이 evidence/origin에서 taste/taste_origin으로 바뀐다. 근거 성분 비중이 1.0에서 0.55로 줄어든다. `profile_before`·`profile_after`로 기록한다.
- 진단(`snapshot.saved_taste`):
  - 태그와 상태: `requested`, `applied`, `reason`, `typed_tags`, `added_tags`, `added_support`, `candidates_not_added`
  - 저장 장소 근거: `seed_evidence`(장소·근거 종류·출처 ID)
  - 상한 기록: `max_added_tags`, `max_seeds`, `formula=hybrid_v5_unchanged_input_only`
- 저장한 장소 자신의 취급(`saved_seed_rule=saved_places_kept_as_candidates`): 실제 실행에서는 저장한 장소를 후보에서 빼지 않는다. 그래서 저장한 장소가 자기 태그로 점수가 오를 수 있다(자기 강화 허용). 몇 곳이 그런지는 `result.feedback_match.saved_seed_places_in_candidates`에 남긴다. 차단으로 바꿀지는 대표 결정 5번이다.
- 요청 모델의 `saved_taste`는 `model_dump`에서 빠진다. 그래서 기존 요청 본문, 멱등 지문, 라벨 풀 스냅숏은 한 글자도 바뀌지 않는다. 켠 요청만 지문에 `saved_taste: true`가 들어가서, 같은 키로 ON/OFF를 바꾸면 409가 난다.

## D3. 저장한 곳 가까운 순서

- `order=nearest&anchor=<bookmark id>`: 고른 저장 장소에서 다른 저장 장소까지의 직선거리 순서다.
- `order=nearest`만 주면 이 여행의 *현재 해석된* 출발점을 기준으로 쓴다. 추천 경로와 같은 `get_conditions`(→ `enrich_origin`) 결과에서 `origin_context.status=ready`일 때만 쓴다. 저장된 `conditions_json`의 좌표를 직접 읽지 않는다. 그래서 숙소를 지우거나, 출발점이 무효화·만료되면 기준이 없어진다. 기준 좌표는 서버 안에서만 쓰고 응답에는 넣지 않는다. 기준이 없으면 `reason=ANCHOR_UNAVAILABLE`과 함께 최근 저장 순서를 그대로 둔다.
- 각 항목에 `straight_line_m`과 `distance_basis=straight_line_not_walking_time`이 붙는다. 좌표가 없는 장소는 뒤에 두고 거리를 `null`로 둔다.
- 응답의 `provider_calls=0`은 코드 구조상 고정값이다(이 경로는 공급자 클라이언트를 쓰지 않는다). 시험에서는 공개 지도 공급자와 지오코더를 ‘쓰면 실패하는 가짜’로 바꿔 끼워 실제로 호출이 없음을 확인한다.
- 화면 연결은 하지 않았다(아래 ‘대표 결정’ 3번).

## D4. 효과 측정 설계

### 오프라인: 홀드아웃 라벨로 ON/OFF 비교

`src/recommendations/saved_taste.py:offline_comparison(pool, labels, view=...)`

- 저장 시드: development 질의에서 사람이 2점 이상 준 장소만, 도시별로 쓴다. 홀드아웃 판정은 시드 선택에 쓰지 않는다. 시험으로 고정했다.
- 시드 장소는 두 순위와 판정 풀에서 모두 뺀다. development에서 관련 있다고 판정한 장소가 홀드아웃 질의에서도 판정되어 있을 수 있어서, 빼지 않으면 라벨이 새어 들어간다. 그래서 오프라인 추정은 **저장하지 않은 장소에 대한 효과만** 잰다(`estimate_scope=non_saved_places_only`). 실제 실행에서 저장한 장소가 자기 점수를 올리는 부분(D2의 `saved_seed_rule`)은 이 추정에 들어가지 않는다.
- 저장 기반 태그가 1개 이상 붙는 홀드아웃 질의만 비교한다.
- 지표는 pooled 모드 그대로다: NDCG@5, 미판정을 0으로 채우지 않음, top-5에 미판정이 있으면 coverage_insufficient. 질의 쌍 bootstrap은 2,000회, seed 20261008이다.
- 판정(`decision`). 아래 조건을 모두 넘겨야 `ndcg_delta_on_minus_off`에 값이 나온다. 넘지 못하면 그 값은 `null`이고, 참고 숫자는 `diagnostic_only`에만 둔다. 이 숫자를 ‘효과 0’으로 읽으면 안 된다.

  | 순서 | 조건 | decision |
  | --- | --- | --- |
  | 1 | 짝지은 질의가 0개 | `UNMEASURED` |
  | 2 | ON이 top-5를 바꾼 질의 중 커버리지 부족 비율 > 0.2 | `UNMEASURED_COVERAGE` |
  | 3 | 짝지은 질의 < 20 (홀드아웃 40개의 절반) | `UNMEASURED_SAMPLE` |
  | 4 | 위를 모두 통과 | `MEASURED_NOT_A_PROMOTION_RULE` |

  0.2와 20은 이번에 정한 잠정값이다. 함수 인자(`max_changed_unjudged_share`, `min_paired`)로 바꿀 수 있다.
- 보고 항목: `top5_changed_queries`, `top5_changed_paired`, `top5_changed_coverage_insufficient`, `top5_changed_unjudged_share`. 그리고 프로필이 바뀐 질의와 그대로인 질의를 나눈 `by_profile_change`(`same_profile` / `profile_switched`)를 따로 낸다. 취향을 입력하지 않은 질의는 켜는 순간 프로필이 evidence→taste로 바뀌므로, ‘태그 추가’ 효과와 ‘프로필 전환’ 효과가 섞이지 않게 하려는 것이다.
- **측정의 전제 조건: ON top-10 라벨.** 지금 라벨 풀은 OFF 순위만으로 만들었다. ON이 top-5를 바꾼 질의는 미판정 장소가 섞여 짝짓기에서 빠지고, top-5가 같은 질의만 남는다. 그러면 차이는 구조적으로 0이 된다. 합성 실행(실측 아님)에서 홀드아웃 40개 중 top-5가 바뀐 질의 12개는 모두 커버리지 부족이었고(비율 1.0), 결과는 `UNMEASURED_COVERAGE`였다. 그래서 순서는 이렇다:
  1. development 라벨을 끝낸다.
  2. `on_pool_gaps`로 ON top-10 중 미판정 홀드아웃 장소 목록을 뽑는다. 합성 실행에서는 31개 질의, 65곳이었다.
  3. 사람이 그 장소를 판정한다. 라벨 풀 규칙 자체(묶음 B)는 이번에 바꾸지 않았다. 목록만 뽑는다.
  4. `offline_comparison`을 돌린다.
- 이 결과는 승격 규칙이 아니고 기본값을 바꾸지 않는다.

### 온라인: 동의 사용자 이벤트만

`src/product/feedback_effect.py:online_effect`. 관리자 제품 보고서의 `feedback_loop_effect`로도 나온다.

- 분석에 동의한 사용자의 실행 지표(`product_run_metrics`)와 이벤트만 센다. 실행 요약에 `saved_taste_applied`·`feedback_applied` 불리언을 더했다. 장소 ID와 태그는 넣지 않는다.
- 묶음별로 같은 정의를 쓴다. 노출 후 저장률 = 먼저 본 뒤 저장한 (실행, 장소) 쌍 / 본 쌍. 일정 채택률 = 본 뒤 일정에 넣은 실행 / 본 실행.
- 노출 100건 미만이거나 노출된 실행이 30건 미만이면 `미측정`이고 비율은 `null`이다.
- 사용자가 스스로 켜는 옵션이라 무작위 배정이 아니다. 차이는 기술 통계일 뿐 인과나 승격의 근거가 아니다.

## 실행 예

```sh
# 합성 시험
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python -m pytest -q -p no:cacheprovider tests/test_v6_feedback_loop.py
node --test tests/test_v6_feedback_ui.cjs

# development 라벨 완료 후: ON top-10 중 아직 판정 안 된 홀드아웃 장소 목록 (라벨을 만들지 않음)
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python -c "import json,sys;from src.recommendations.saved_taste import on_pool_gaps as g;print(json.dumps(g(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])),view='displayed'),ensure_ascii=False,indent=2))" <pool.json> <labels.json>

# 위 장소까지 판정한 뒤 오프라인 ON/OFF (라벨 파일 경로는 LABELING.md 기준)
PYTHON_DOTENV_DISABLED=1 ~/.venvs/travel-inbox-rag/bin/python -c "import json,sys;from src.recommendations.saved_taste import offline_comparison as c;print(json.dumps(c(json.load(open(sys.argv[1])),json.load(open(sys.argv[2])),view='displayed'),ensure_ascii=False,indent=2))" <pool.json> <labels.json>
```

## 대표 결정이 필요한 것

1. **저장 기반 취향의 화면 노출.** 지금은 API 플래그로만 켤 수 있다. 상위 지시가 ‘새 메뉴·토글 추가 금지’라서 화면에 넣지 않았다. 넣는다면 조건 수정 창에 체크박스 한 개를 두는 안이다.
2. **ON top-10 추가 라벨 시간.** 선택 사항이 아니라 오프라인 측정의 전제 조건이다(위 D4). 정할 것은 추가 라벨 시간을 쓸지 여부다. 쓰지 않으면 오프라인 결과는 계속 `UNMEASURED_COVERAGE`다. 합성 기준 31개 질의, 65곳이 늘어난다(실측 아님). 풀 규칙(묶음 B)에 공식으로 넣을지도 함께 정해야 한다.
3. **가까운 순서 화면.** 보관함 화면(`web/js/foundation.js` renderBookmarks, `web/index.html` bookmarkFilter)은 3단계 에이전트가 동시에 고치는 파일이라 손대지 않았다. 3단계가 끝난 뒤 보관함 표시 선택지에 ‘저장한 곳 가까운 순서’를 더하고 위 API를 부르면 된다.
4. **‘다음 여행에도’의 수명.** 지금은 기록한 여행을 지우면 기록이 함께 사라진다(이미 다른 여행 실행 스냅숏에 복사된 장소 ID는 남는다. 아래 ‘알려진 한계’). 여행과 별개로 계정 단위로 보관하려면 새 테이블과 삭제 경로가 필요하다.
5. **저장한 장소의 자기 강화.** 지금은 허용한다(저장한 장소도 후보로 남고 자기 태그로 점수가 오를 수 있음). 차단하려면 실제 실행에서 시드 장소의 취향 성분만 빼는 규칙이 필요하다. 오프라인 비교는 어느 쪽이든 시드를 빼야 하므로 그대로다.

## 알려진 한계

- **다른 여행 실행 기록에 남는 장소 ID.** `preference_scope='future'`로 넘어간 선택은 다른 여행에서 추천을 실행할 때 그 실행의 `recommendation_runs.snapshot_json`(`soft_avoid_place_ids` / `feedback_excluded_place_ids`)에 그 시점 입력으로 복사된다. 스냅숏은 실행 입력을 얼린 기록이라 나중에 고치지 않는다.
  - 피드백을 철회해도, 기록한 원본 여행을 지워도 이 복사본은 지워지지 않는다. 철회·삭제는 이후 실행이 그 선택을 쓰지 않게 할 뿐이다.
  - 그 다른 여행을 지우면 그 여행의 여행 삭제 정리(`scrub_trip`)로 스냅숏이 함께 지워진다.
  - 남는 것은 장소 ID와 건수(`feedback_inputs`)다. 다른 여행 ID·사유·개인 메모는 스냅숏에 넣지 않는다.
  - 코드는 바꾸지 않았다. 철회·원본 삭제 때 다른 여행 스냅숏에서 장소 ID를 지우거나, 스냅숏에 건수만 남기는 방안은 검토 의견이 갈려 보류했다. `tests/test_v6_feedback_loop_limits.py`가 지금 동작(남는다)을 고정한다. 동작을 바꾸면 이 절과 그 시험을 함께 고친다.

## 미실행

- PostgreSQL 경로(Docker 미사용).
- 브라우저 확인.
- 실제 사용자 측정. 온라인 효과는 동의 사용자 표본이 없어 미측정이다.
- 실제 라벨로 한 오프라인 비교. 라벨 대기 중이다.
