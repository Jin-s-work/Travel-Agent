"""Frozen-input ranker comparison. No retrieval, provider, or automatic winner."""
from copy import deepcopy
from datetime import datetime
from src.recommendations.engine import recommend,RankerConfig
from src.recommendations.service import digest

CONFIGS={'baseline':RankerConfig(),'diversity-v2':RankerConfig(version='diversity-v2',chain_limit=1,neighborhood_limit=2),'distance-v2':RankerConfig(version='distance-v2',distance_scale_m=3000),'movement-v2':RankerConfig(version='ranker-movement-v2',movement_version='v2')}

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


def main():
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Frozen recommendation comparison; never searches or calls a provider.')
    parser.add_argument('--fixture',required=True,help='Private JSON with snapshot and candidates from one frozen run')
    parser.add_argument('--candidate-version',choices=sorted(k for k in CONFIGS if k!='baseline'),default='movement-v2')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    source=Path(args.fixture)
    if source.stat().st_size>10*1024*1024:parser.error('Fixture exceeds 10 MiB')
    data=json.loads(source.read_text())
    result=compare(data['snapshot'],data['candidates'],args.candidate_version)
    result['observed_user_outcome']='unmeasured'
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');output.chmod(0o600)
    print(json.dumps({'output_written':True,'retrieval_calls':0,'user_outcome':'unmeasured'}))

if __name__=='__main__':main()
