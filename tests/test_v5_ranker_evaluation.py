from copy import deepcopy
from src.recommendations.hybrid_v6 import terms
from src.recommendations.registry import model_snapshot,VERSION
from src.recommendations.engine import recommend
from src.recommendations.benchmark import evaluate,ranking_metrics
from src.recommendations.evaluation_v5 import candidate_recall,bootstrap95,leakage_audit
from scripts.build_recommender_benchmark import build
from tests.test_hybrid_recommender import rows,request
from tests.test_recommendation_engine import NOW
import pytest


def test_v6_explicit_food_aliases_without_dietary_inference():
    assert terms(['야키토리','焼き鳥','marisc','海鮮','tapes','타파스'])=={'yakitori','seafood','tapas'}
    assert terms(['not seafood','gluten free'])=={'not seafood','gluten free'}


def test_v6_determinism_photo_invariance_and_v5_stays_default():
    pool=rows();snap=request('hybrid_v6');snap.update(result_contract='discovery_result_v1')
    first=recommend(snap,pool,now=NOW)
    assert first==recommend(snap,list(reversed(pool)),now=NOW)
    for p in pool:p['photos']=[{'id':'ignored'}];p['photo_status']={'state':'available'}
    assert first==recommend(snap,pool,now=NOW)
    assert VERSION=='hybrid_v5' and model_snapshot(snap)['recommendation_model_version']=='hybrid_v5'


def test_retrieval_recall_includes_missing_truth_and_zero_is_unmeasured():
    assert candidate_recall(['a','b'],['a','b','missing'])['value']==pytest.approx(2/3)
    assert candidate_recall(['a'],[])['value'] is None
    assert candidate_recall(['a'],None)['value'] is None
    assert ranking_metrics(['a','b'],{'a':3,'b':2},['a','b'],5)['precision']==.4
    assert ranking_metrics(['a'],{},['a'],5)['ndcg'] is None


def test_same_pool_model_experiment_cannot_promote_without_human_holdout():
    data=build();data['k']=5
    result=evaluate(data,repeats=1,models=('hybrid_v5','hybrid_v6'))
    assert result['model_comparison']['production_model']=='hybrid_v5'
    assert not result['model_comparison']['promotion_candidate']
    assert result['model_comparison']['holdout_ndcg_delta']['mean'] is None
    assert all(q['models']['hybrid_v6']['hard_violations']==0 for q in result['queries'])
    assert all(q['candidate_recall_at_200']['value'] is None for q in result['queries'])
    assert bootstrap95([.1,.2,-.1])==bootstrap95([.1,.2,-.1])


def test_query_travel_group_leakage_is_auditable():
    qs=[{'split':split,'query_group':'same','candidates':[{'place_id':'p'}]} for split in ('development','holdout')]
    audit=leakage_audit(qs)
    assert audit['shared_query_groups']==['same'] and audit['shared_places']==['p']


def test_missing_city_mode_cannot_be_hidden_by_good_overall_holdout():
    from src.recommendations.evaluation_v5 import compare_report
    evaluated=[{'city':city,'section':'landmark','split':'holdout',
        'models':{'hybrid_v5':{'metrics':{'ndcg':.8}},'hybrid_v6':{'metrics':{'ndcg':.9}}}}
        for city in ('tokyo','barcelona') for _ in range(20)]
    summary={v:{'latency_ms':{'p95':1},'hard_violations':0,'deterministic_queries':40} for v in ('hybrid_v5','hybrid_v6')}
    result=compare_report(evaluated,summary,label_source='independent_human')
    assert not result['promotion_candidate']
    assert 'CITY_MODE_REGRESSION_OR_UNMEASURED' in result['reason_codes']
    assert result['city_mode_deltas']['tokyo/local_discovery']['mean'] is None
