"""Side-effect-free shadow ranks on exactly the serving run's frozen input."""
from copy import deepcopy
from datetime import datetime
from time import perf_counter

def shadow_compare(snapshot,candidates,baseline):
    from .registry import model_snapshot
    from .engine import recommend
    other=deepcopy(snapshot);other.update(model_snapshot(other,version='hybrid_v6'))
    start=perf_counter();output=recommend(other,deepcopy(candidates),now=datetime.fromisoformat(snapshot['evaluation_at']))
    elapsed=(perf_counter()-start)*1000
    comparison={}
    for section,groups in baseline['sections'].items():
        left=[p['place_id'] for p in groups['items']+groups['needs_confirmation']]
        right=[p['place_id'] for p in output['sections'][section]['items']+output['sections'][section]['needs_confirmation']]
        comparison[section]={'baseline':left,'shadow':right,'changed_positions':sum(a!=b for a,b in zip(left,right))+abs(len(left)-len(right))}
    return {'baseline':snapshot['recommendation_model_version'],'shadow':'hybrid_v6','serving_changed':False,
        'same_candidate_ids':True,'sections':comparison,'shadow_elapsed_ms':round(elapsed,3),'provider_calls':0,
        'accuracy':'unmeasured','feature_version':'content_context_v3'}
