"""Explain sparse catalogs without changing eligibility, ranking, or filters.

Counts are distinct branch IDs across both recommendation sections. A reference
card is an invitation to verify a place, never a passed recommendation or a
promise that a future reservation is available.
"""


def summarize(snapshot, candidates, result):
    sections=result['sections']
    def ids(group):
        return {item['place_id'] for section in sections.values() for item in section.get(group,[])}
    qualified=ids('items')
    confirmation=ids('needs_confirmation')-qualified
    references=ids('insufficient_data')-qualified-confirmation
    displayable=qualified|confirmation|references
    excluded=ids('excluded')-displayable
    catalog={p['place_id'] for p in candidates}
    relevant={p['place_id'] for p in candidates if p.get('category') in snapshot['conditions']['categories'] and set(p.get('recommendation_types',[]))&set(snapshot['conditions']['recommendation_types'])}
    empty=None
    if not catalog:
        state='no_catalog'
        empty={'code':'CATALOG_EMPTY','title':'이 도시의 장소를 준비하고 있어요',
               'description':'아직 출처와 지점을 확인한 장소가 없어요. 원하는 장소의 이름이나 링크를 먼저 저장할 수 있어요.',
               'actions':['save_place','edit_conditions']}
    elif not relevant:
        state='no_matching_candidates'
        empty={'code':'CATEGORY_NO_CANDIDATES','title':'선택한 종류의 장소가 아직 없어요',
               'description':'이 도시의 다른 종류를 살펴보거나, 가고 싶은 장소를 보관함에 저장해 주세요.',
               'actions':['edit_conditions','save_place']}
    elif not displayable:
        state='no_results'
        empty={'code':'CONDITIONS_NOT_MET','title':'선택한 조건에 맞는 장소를 찾지 못했어요',
               'description':'휴무·인원·거리 등 확인된 조건을 지키며 찾았어요. 조건을 직접 바꿔 다시 찾거나 원하는 장소를 저장해 주세요.',
               'actions':['edit_conditions','save_place']}
    else:
        state='ready' if qualified else 'needs_confirmation'
    return {'version':'catalog_presentation_v1','count_basis':'distinct_place_ids',
            'catalog_count':len(catalog),'matching_category_count':len(relevant),
            'displayable_count':len(displayable),'qualified_count':len(qualified),
            'confirmation_count':len(confirmation),'reference_count':len(references),
            'excluded_count':len(excluded),'state':state,
            'reason_codes':[empty['code']] if empty else [],'empty_state':empty,
            'confirmation_notice':'아래 장소는 일부 조건의 확인이 필요해요. 평점·영업·예약 가능 여부가 확인됐다는 뜻은 아니에요.' if confirmation or references else None}
