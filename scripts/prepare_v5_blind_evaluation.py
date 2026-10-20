#!/usr/bin/env python3
"""Write 60 unlabelled human work items. No venues, labels, or performance fabricated."""
import argparse,json
from pathlib import Path

def build():
    questions=[]
    for city in ('tokyo','barcelona'):
        for i in range(30):
            group=i//6;scenario=('origin','food','price','unspecified','local_language','famous')[i%6]
            questions.append({'id':f'{city}-{i+1:02}','city':city,'split':'development' if i<10 else 'holdout',
                'scenario':scenario,'query_group':f'{city}-query-{i+1:02}','travel_group':f'{city}-'+('development' if i<10 else 'holdout')+f'-{group}',
                'status':'unlabeled','human_query':None,'frozen_snapshot':None,'candidate_ids':None,
                'independent_relevant_place_ids':None,'judgments':None,'reviewer_id':None,
                'instructions':'모델 순위를 보지 않고 조건을 작성하고 지점을 0~3으로 평가하세요. 2 이상은 관련 있음입니다. 독립 정답 목록에는 후보에 없는 지점도 포함하세요.'})
    return {'schema_version':1,'kind':'blind_evaluation_work_plan','actual_queries_collected':0,
        'actual_human_labels':0,'target_queries':60,'development_target':20,'holdout_target':40,
        'place_review_leakage_policy':'분할 전 질의·여행·리뷰 그룹을 고정. 공통 지점은 보고하고 가능한 지점 분리 holdout 추가.',
        'label_scale':{'0':'부적합','1':'약간 관련','2':'적합','3':'매우 적합'},'items':questions}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'work_items':60,'actual_queries':0,'labels':0,'provider_calls':0}))
