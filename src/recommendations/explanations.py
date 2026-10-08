"""Closed-vocabulary explanations. Optional model text must match server evidence.

The current product uses the deterministic path; there is no external LLM call.
"""
from copy import deepcopy


def render(candidate):
    if candidate.get('ranker_version','').endswith(('_hybrid_v4','_hybrid_v5')):
        base = render_general(candidate)
        diagnostics = candidate.get('ranking_diagnostics') or {}
        extra = []
        if diagnostics.get('matched_tags'):
            extra.append({'code': 'CONTENT_MATCH', 'text': '공개지도에 등록된 음식 종류가 취향과 맞아요. 실제 메뉴는 확인해 주세요.' if (diagnostics.get('tag_evidence') or {}).get('kind') == 'public_cuisine_provisional' else '선택한 취향과 장소 태그의 유사도를 반영했어요.', 'source_ids': (diagnostics.get('tag_evidence') or {}).get('source_ids', []), 'aggregate_refs': []})
        if diagnostics.get('proximity_reference') == 'city_center' and diagnostics.get('proximity_distance_m') is not None:
            extra.append({'code':'CITY_CENTER_DISTANCE','text':f"도심 기준 직선거리 약 {round(diagnostics['proximity_distance_m']):,}m예요. 숙소를 선택하면 숙소 기준으로 비교해요.",'source_ids':[],'aggregate_refs':[]})
        if diagnostics.get('rating'):
            extra.append({'code': 'RATING_SHRINKAGE', 'text': '같은 플랫폼·도시·분류의 평점을 평가 수와 함께 비교했어요.', 'source_ids': diagnostics['rating']['source_ids'], 'aggregate_refs': []})
        if diagnostics.get('soft_avoid_multiplier') == .5:
            extra.insert(0, {'code': 'EXPLICIT_SOFT_AVOID', 'text': '반영하도록 선택한 피드백으로 추천 우선순위를 낮췄어요.', 'source_ids': [], 'aggregate_refs': []})
        base = [r for r in base if r['code'] != 'POPULARITY_BASIS']
        return (base[:1] + extra + base[1:])[:3]
    if candidate.get('ranker_version','').endswith('_general_v3'):
        return render_general(candidate)
    reasons = []
    components = candidate.get("score_components", {})
    labels = {
        "local_evidence": "검토된 지역 자료가 이 장소를 소개합니다.",
        "iconic_evidence": "검토된 자료에 대표 명소로 소개되어 있습니다.",
        "preference": "선택한 취향 태그와 일치하는 항목이 있습니다.",
        "movement": "출발점과의 직선거리를 참고했습니다. 실제 이동 시간은 미확인입니다.",
        "price": "확인된 가격 범위가 입력한 예산 기준에 맞습니다.",
        "language": "자격을 확인한 관측 구간의 리뷰 언어 자료를 반영했습니다.",
        "quality": "같은 플랫폼의 평점과 전체 평가 수 조건을 확인했습니다.",
    }
    if 'VERIFIED_WALKING_ROUTE' in (components.get('movement') or {}).get('reason_codes',[]):
        labels['movement']='확인된 도보 경로의 이동시간을 가까운 곳 선호에 반영했습니다.'
    elif (candidate.get('movement') or {}).get('route',{}).get('status')=='ok':
        labels['movement']='출발점과의 직선거리를 참고했습니다. 이동시간은 별도 경로 근거에서 확인할 수 있습니다.'
    preferred = ("iconic_evidence", "local_evidence", "language", "preference", "price", "movement", "quality")
    for name in preferred:
        component = components.get(name) or {}
        if component.get("value") is None or component["value"] <= 0:
            continue
        reasons.append({"code": name.upper(), "text": labels[name],
                        "source_ids": sorted(set(component.get("source_ids", []))),
                        "aggregate_refs": [candidate["review_evidence"]["aggregate_id"]] if name == "language" and candidate.get("review_evidence", {}).get("aggregate_id") else []})
        if len(reasons) == 3:
            break
    if not reasons:
        reasons.append({"code": "EVIDENCE_PENDING", "text": "방문 조건과 추천 근거를 추가로 확인해야 합니다.", "source_ids": [], "aggregate_refs": []})
    return reasons


def _entry(candidate):
    return {"place_id": candidate["place_id"], "recommendation_type": candidate["recommendation_type"],
            "reason_sentences": [{k: v for k, v in reason.items() if k != "code"} for reason in render(candidate)]}


def validate(ordered_candidates, output):
    """Reject added/reordered IDs, arbitrary text/numbers, refs, scores, availability."""
    if isinstance(ordered_candidates, dict):
        ordered_candidates = [ordered_candidates]
    expected = {"recommendations": [_entry(candidate) for candidate in ordered_candidates]}
    return isinstance(output, dict) and output == expected


def fallback(ordered_candidates, proposed=None):
    if isinstance(ordered_candidates, dict):
        ordered_candidates = [ordered_candidates]
    if proposed is not None and validate(ordered_candidates, proposed):
        return {**deepcopy(proposed), "explanation_mode": "validated"}
    return {"recommendations": [_entry(candidate) for candidate in ordered_candidates], "explanation_mode": "server_template"}


def render_general(candidate):
    reasons=[]
    def add(code,text,refs=(),aggregates=()):
        reasons.append({'code':code,'text':text,'source_ids':sorted(set(refs)),'aggregate_refs':list(aggregates)})
    if candidate.get('language_qualified'):
        review=candidate.get('review_evidence') or {};c=review.get('counts') or {};aggregate=[review['aggregate_id']] if review.get('aggregate_id') else []
        add('OBSERVED_LOCAL_COUNT',f"판별한 리뷰 {c['classified_count']}건 중 현지어 원문 {c['local_count']}건을 확인했어요.",aggregates=aggregate)
        add('OBSERVED_SCOPE',f"관측 본문 {c['text_count']}건 · 한국어 {c['korean_count']}건 · 미판별 {c['unknown_count']}건이에요.",aggregates=aggregate)
    iconic=candidate.get('iconic_evidence') or {};kind=iconic.get('kind')
    labels={'official_landmark':'공식 자료가 소개하는 대표 장소예요.','editorial_recognition':'검토한 편집 자료가 선정한 장소예요.'}
    if kind in labels:add('ICONIC_'+kind.upper(),labels[kind],iconic['source_ids'])
    elif kind=='platform_popular':
        rating=iconic['platform_rating']
        add('PLATFORM_POPULAR',f"{rating['platform']}에서 전체 평가 {rating['total_rating_count']}건 · {rating['scale']}점 만점 {rating['rating']:g}점이에요.",iconic['source_ids'])
        add('POPULARITY_BASIS','동일 플랫폼·도시·분류 안에서 가까운 곳 우선으로 살펴봤어요.' if candidate.get('ordering_profile')=='nearby' else '동일 플랫폼·도시·분류 안에서 평가 수를 기준으로 살펴봤어요.',iconic['source_ids'])
    distance=((candidate.get('features') or {}).get('straight_distance') or {}).get('value')
    if distance is not None and len(reasons)<3:
        add('STRAIGHT_DISTANCE',f"선택한 출발점에서 직선거리 약 {round(distance):,}m예요. 실제 이동 경로와는 달라요.")
    if not reasons:add('REFERENCE_ONLY','지점 자료를 확인한 주변 장소예요. 리뷰 언어나 유명함을 보장하지 않아요.',[r['id'] for r in candidate.get('source_refs',[])])
    return reasons[:3]
