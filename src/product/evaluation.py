"""Frozen-input comparisons. No retrieval, provider, learned winner or relevance claim."""
from collections import Counter
from copy import deepcopy
from datetime import datetime
from src.recommendations.engine import recommend,RankerConfig
from src.recommendations.service import digest
from src.recommendations.registry import model_snapshot

CONFIGS={'baseline':RankerConfig(),'diversity-v2':RankerConfig(version='diversity-v2',chain_limit=1,neighborhood_limit=2),'distance-v2':RankerConfig(version='distance-v2',distance_scale_m=3000),'movement-v2':RankerConfig(version='ranker-movement-v2',movement_version='v2'),'general-v3':RankerConfig(version='ranker-general-v3',movement_version='v2')}


def compare(snapshot,candidates,candidate_version='diversity-v2'):
    clock=datetime.fromisoformat(snapshot['evaluation_at']);frozen=deepcopy(candidates)
    left_input=deepcopy(snapshot);right_input=deepcopy(snapshot)
    if candidate_version=='general-v3':
        left_input.pop('recommendation_model_version',None);left_input.pop('section_models',None)
        right_input.update(model_snapshot(right_input, version='general_v3'))
    left=recommend(left_input,frozen,now=clock,config=CONFIGS['baseline'])
    right=recommend(right_input,frozen,now=clock,config=CONFIGS[candidate_version])
    output=[]
    for kind in dict.fromkeys([*left['sections'],*right['sections']]):
        ga=left['sections'].get(kind,{k:[] for k in ('items','needs_confirmation','insufficient_data','excluded')});gb=right['sections'].get(kind,{k:[] for k in ga})
        a=ga['items'];b=gb['items'];aa={p['place_id']:i+1 for i,p in enumerate(a)};bb={p['place_id']:i+1 for i,p in enumerate(b)}
        all_a={p['place_id']:p for values in ga.values() for p in values};all_b={p['place_id']:p for values in gb.values() for p in values}
        rows=[]
        for ident in sorted(set(aa)|set(bb)):
            before=all_a.get(ident,{});after=all_b.get(ident,{})
            rows.append({'place_id':ident,'place_name':after.get('name') or before.get('name') or '이름 미확인','before':aa.get(ident),'after':bb.get(ident),
                'before_components':before.get('score_components'),'after_components':after.get('score_components'),
                'before_features':before.get('features'),'after_features':after.get('features'),
                'before_missing':before.get('missing_components',[]),'after_missing':{k:v['missing_reason'] for k,v in after.get('features',{}).items() if v['value'] is None}})
        output.append({'type':kind,'top_overlap':len(set(aa)&set(bb)),'union_count':len(set(aa)|set(bb)),'rank_changes':rows,
            'top_k':{'before':[p['place_id'] for p in a],'after':[p['place_id'] for p in b]},
            'confirmation_groups':{'before':[p['place_id'] for p in ga['needs_confirmation']],'after':[p['place_id'] for p in gb['needs_confirmation']]},
            'hard_violations':{label:sum(any(x['state']=='failed' for x in p['visit_fit']['checks']) for p in items) for label,items in [('before',a+ga['needs_confirmation']),('after',b+gb['needs_confirmation'])]},
            'diversity':{label:{'places':len(items),'categories':dict(Counter(p['category'] for p in items)),'neighborhoods':dict(Counter(p['neighborhood'] for p in items if p.get('neighborhood'))),'chains':dict(Counter(p['chain_id'] for p in items if p.get('chain_id')))} for label,items in [('before',a),('after',b)]},
            'missing_data':{'before':len(ga['insufficient_data']),'after':right.get('section_status',{}).get(kind,{}).get('insufficient_count',len(gb['insufficient_data']))},
            'support':right.get('section_status',{}).get(kind,{'state':'legacy','reason_codes':[]})})
    minimal=deepcopy(right_input);minimal['conditions'].update(preferred={'tags':[]},origin=None,budget=None)
    # Optional-input regression only removes optional preferences/origin/budget.
    # Required distance/diet constraints remain and can still require evidence.
    minimal_result=recommend(minimal,frozen,now=clock,config=CONFIGS[candidate_version])
    deterministic=right==recommend(right_input,list(reversed(frozen)),now=clock,config=CONFIGS[candidate_version])
    return {'version':'frozen_ranker_evaluation_v3','input_hash':digest({'snapshot':snapshot,'candidates':frozen}),'clock':clock.isoformat(),
        'city':snapshot['conditions']['city'],'synthetic':any(p.get('synthetic') for p in candidates),'baseline':left['config'],'candidate':right['config'],
        'model_versions':{'before':left.get('section_models'),'after':right.get('section_models')},'comparison':output,
        'optional_inputs_omitted':{k:{'ranked':len(v['items']),'confirmation':len(v['needs_confirmation'])} for k,v in minimal_result['sections'].items()},
        'deterministic':deterministic,'language_gate_unchanged':candidate_version!='general-v3','language_gate_change':'strict_only_no_editorial_fallback' if candidate_version=='general-v3' else None,
        'automatic_winner':None,'learning_status':'insufficient_evidence','relevance_evaluation':{'ndcg':None,'satisfaction':None,'status':'unmeasured','reason':'NO_INDEPENDENT_RELEVANCE_LABELS'},
        'explanation_evaluation':{'mode':'separate_contract_test','live_llm':'not_run'},'retrieval_calls':0,'observed_user_outcome':'unmeasured'}


def markdown(result):
    lines=['# 고정 입력 추천 모델 비교','',f"- 도시: {result['city']} · 합성 자료: {result['synthetic']}",f"- 기준 시각: {result['clock']}",f"- 입력 해시: `{result['input_hash']}`",f"- 결정성: {result['deterministic']} · 외부 조회: 0회",'- NDCG·만족도·A/B 승자: 미측정 (실제 관련성 정답·실사용 자료 없음)','', '| 구획 | 기존 순위 | 비교 순위 | 확인 필요 | 필수 위반 | 상태 |','|---|---:|---:|---:|---:|---|']
    for section in result['comparison']:
        lines.append(f"| {section['type']} | {len(section['top_k']['before'])} | {len(section['top_k']['after'])} | {len(section['confirmation_groups']['after'])} | {section['hard_violations']['after']} | {section['support']['state']} |")
    for section in result['comparison']:
        lines.extend(['',f"{section['type']} 부족 사유: {', '.join(section['support'].get('reason_codes',[])) or '없음'}",''])
    lines+=['','JSON에 순위 이동·성분·결측·다양성·선택 입력 생략 결과를 기록했습니다. 설명 LLM 평가는 결정적 순위 평가와 별도이며 라이브 실행하지 않았습니다.','']
    return '\n'.join(lines)


def main():
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Frozen comparison, no retrieval/provider. Writes JSON and companion Markdown.')
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--candidate-version',choices=sorted(k for k in CONFIGS if k!='baseline'),default='general-v3')
    parser.add_argument('--output',required=True)
    args=parser.parse_args();source=Path(args.fixture)
    if source.stat().st_size>10*1024*1024:parser.error('Fixture exceeds 10 MiB')
    data=json.loads(source.read_text());result=compare(data['snapshot'],data['candidates'],args.candidate_version)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');output.chmod(0o600)
    companion=output.with_suffix('.md');companion.write_text(markdown(result));companion.chmod(0o600)
    print(json.dumps({'output_written':True,'markdown_written':True,'retrieval_calls':0,'user_outcome':'unmeasured'}))

if __name__=='__main__':main()
