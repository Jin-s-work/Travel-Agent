"""Closed-vocabulary explanations. Optional model text must match server evidence.

The current product uses the deterministic path; there is no external LLM call.
"""
from copy import deepcopy


def render(candidate):
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
