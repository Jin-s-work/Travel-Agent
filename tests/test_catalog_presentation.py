from src.recommendations.presentation import summarize


def sample(groups,candidates=None,categories=None):
    sections={kind:{name:[] for name in ('items','needs_confirmation','insufficient_data','excluded')} for kind in ('local_discovery','landmark')}
    sections['local_discovery'].update(groups)
    snapshot={'conditions':{'categories':categories or ['restaurant'],'recommendation_types':['local_discovery','landmark']}}
    return summarize(snapshot,candidates or [],{'sections':sections})


def candidate(ident='a',category='restaurant'):
    return {'place_id':ident,'category':category,'recommendation_types':['local_discovery']}


def test_empty_catalog_is_not_reported_as_failed_user_conditions():
    value=sample({})
    assert value['state']=='no_catalog' and value['empty_state']['code']=='CATALOG_EMPTY'
    assert value['displayable_count']==value['qualified_count']==0
    assert 'save_place' in value['empty_state']['actions']


def test_missing_category_differs_from_known_condition_failure():
    assert sample({},[candidate(category='cafe')])['empty_state']['code']=='CATEGORY_NO_CANDIDATES'
    assert sample({'excluded':[candidate()]},[candidate()])['empty_state']['code']=='CONDITIONS_NOT_MET'


def test_unknown_candidates_remain_visible_without_becoming_qualified():
    groups={'needs_confirmation':[candidate('a')],'insufficient_data':[candidate('b')]}
    value=sample(groups,[candidate('a'),candidate('b')])
    assert value['displayable_count']==2 and value['qualified_count']==0
    assert value['state']=='needs_confirmation' and value['empty_state'] is None
    assert value['confirmation_count']==value['reference_count']==1
    assert groups['needs_confirmation'][0]==candidate('a')


def test_different_sections_and_unknowns_do_not_double_count_same_branch():
    value=sample({'items':[candidate()], 'needs_confirmation':[candidate()], 'excluded':[candidate()]},[candidate()])
    assert value['qualified_count']==value['displayable_count']==1
    assert value['confirmation_count']==value['excluded_count']==0
