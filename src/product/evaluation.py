"""Frozen-input ranker comparison. No retrieval, provider, or automatic winner."""
from copy import deepcopy
from datetime import datetime
from src.recommendations.engine import recommend,RankerConfig
from src.recommendations.service import digest

CONFIGS={'baseline':RankerConfig(),'diversity-v2':RankerConfig(version='diversity-v2',chain_limit=1,neighborhood_limit=2),'distance-v2':RankerConfig(version='distance-v2',distance_scale_m=3000)}

def compare(snapshot,candidates,candidate_version='diversity-v2'):
    clock=datetime.fromisoformat(snapshot['evaluation_at']);frozen=deepcopy(candidates)
    left=recommend(snapshot,frozen,now=clock,config=CONFIGS['baseline']);right=recommend(snapshot,frozen,now=clock,config=CONFIGS[candidate_version])
    output=[]
    for kind in snapshot['conditions']['recommendation_types']:
        a=left['sections'][kind]['items'];b=right['sections'][kind]['items'];aa={p['place_id']:i+1 for i,p in enumerate(a)};bb={p['place_id']:i+1 for i,p in enumerate(b)}
        all_a={p['place_id']:p for values in left['sections'][kind].values() for p in values};all_b={p['place_id']:p for values in right['sections'][kind].values() for p in values}
        output.append({'type':kind,'top_overlap':len(set(aa)&set(bb)),'union_count':len(set(aa)|set(bb)),
            'rank_changes':[{'place_id':p,'place_name':all_a[p].get('name') or '이름 미확인','before':aa.get(p),'after':bb.get(p),'before_components':all_a[p]['score_components'],'after_components':all_b[p]['score_components']} for p in sorted(set(aa)|set(bb))],
            'hard_violations':{'before':sum(any(x['state']=='failed' for x in p['visit_fit']['checks']) for p in a),'after':sum(any(x['state']=='failed' for x in p['visit_fit']['checks']) for p in b)},
            'diversity':{label:{'places':len(items),'categories':len({p['category'] for p in items}),'neighborhoods':len({p['neighborhood'] for p in items if p.get('neighborhood')})} for label,items in [('before',a),('after',b)]},
            'missing_data':{'before':len(left['sections'][kind]['insufficient_data']),'after':len(right['sections'][kind]['insufficient_data'])}})
    return {'version':'frozen_ranker_evaluation_v1','input_hash':digest({'snapshot':snapshot,'candidates':frozen}),'clock':clock.isoformat(),'city':snapshot['conditions']['city'],'synthetic':any(p.get('synthetic') for p in candidates),'baseline':left['config'],'candidate':right['config'],'comparison':output,'language_gate_unchanged':True,'automatic_winner':None,'explanation_evaluation':{'mode':'separate_contract_test','live_llm':'not_run'},'retrieval_calls':0}
