"""Local operator tools. Aggregate reports only; synthetic evaluation never calls providers."""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from .reporting import report,metric
from src.destinations import CITIES
from .evaluation import compare

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sample=sub.add_parser('evaluate-synthetic');sample.add_argument('--output',type=Path,required=True)
    sample.add_argument('--candidate',choices=['diversity-v2','distance-v2'],default='diversity-v2')
    empty=sub.add_parser('retrospective-template');empty.add_argument('--output',type=Path,required=True)
    r=sub.add_parser('report');r.add_argument('--database',type=Path,required=True);r.add_argument('--admin-id',required=True);r.add_argument('--start',required=True);r.add_argument('--end',required=True);r.add_argument('--city',choices=sorted(CITIES));r.add_argument('--synthetic',action='store_true');r.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='evaluate-synthetic':
        from tests.test_recommendation_engine import catalog,snapshot,NOW,review
        from src.recommendations.explanations import validate,fallback
        from src.recommendations.engine import recommend
        from copy import deepcopy
        results=[]
        for city in ('tokyo','barcelona'):
            for language in (False,True):
                candidates=catalog(city);snap=snapshot(city,evaluation_at=NOW.isoformat(),review_language_filter={'required':language,'apply_to':['local_discovery']})
                if language:
                    for c in candidates:review(c)
                results.append(compare(snap,candidates,args.candidate))
        ordered=recommend(snapshot(),catalog(),now=NOW)['sections']['local_discovery']['items']
        safe=fallback(ordered);safe.pop('explanation_mode');forged=deepcopy(safe)
        if forged['recommendations']:forged['recommendations'][0]['place_id']='invented'
        else:forged['recommendations']=[{'place_id':'invented'}]
        value={'synthetic':True,'behavioral_metrics':'not_measured','comparisons':results,'explanation_contract':{'safe_accepted':validate(ordered,safe),'invented_candidate_rejected':not validate(ordered,forged),'live_llm':'not_run'},'paid_calls':0}
    elif args.command=='report':
        if not args.database.is_file():parser.error('Existing local database required; no production fallback')
        from src.foundation.db import Database
        db=Database(args.database)
        with db.connect() as con:
            if not con.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND status='active'",(args.admin_id,)).fetchone():parser.error('Active operator ID required')
        value=report(db,start=args.start,end=args.end,city=args.city,synthetic=args.synthetic)
    else:
        value={'period':{'start':None,'end':None,'interval':'[start,end)','timezone':'UTC'},'cohort':{'synthetic':False,'users':0,'completed_runs':0,'candidate_count':0},'metrics':{k:metric(0,0) for k in ('candidate_shortage','language_unsupported','exposure_to_save','itinerary_adoption')},'observations':[],'counterexamples':[],'measurement_limits':['실사용 분석 미수집: 제품 성과는 미측정','합성 시험을 방문 경험이나 저장률 성과로 사용하지 않음'],
        'hypotheses':[{'hypothesis':'두 추천 관점이 선택을 돕는다','observe':'관점별 노출 후 저장 건수와 관심 없음 사유','ask':'어느 관점이 이번 여행 선택에 도움이 되었나?','counterexample':'유형/지역 자료 부족 때문에 구분이 무의미함'}, {'hypothesis':'언어·운영 근거가 불확실성을 줄인다','observe':'출처 열기 건수·정보 오류 유형','ask':'어떤 미확인이 선택을 막았나?','counterexample':'출처를 열었지만 예약 가능 여부는 여전히 알 수 없음'}, {'hypothesis':'일정 검증·예약 준비가 수정 부담을 줄인다','observe':'적용·사용자 완료·증빙 확인 건수','ask':'현장에서 다시 고쳐야 했던 부분은 무엇인가?','counterexample':'후보 부족 또는 최신 영업 자료 부재로 대안이 없음'}],
        'next_priority':{'task':'도쿄·바르셀로나의 기존 후보팩에서 운영 사용 가능한 근거를 지점별로 검수','evidence':'현재 진행 기록의 운영 승인 후보 0곳; 행동 지표는 아직 미측정','user_value':'추천 부족의 자료 원인을 줄이고 실제 장소로 검증할 기반 마련','cost':'운영자 수동 검수 중심, 새 유료 API 미도입','dependencies':'공식 지점/영업/가격/인원 근거와 표시 권한','validation':'각 도시 3곳부터 명시 방문 조건으로 자격·미확인·기한 확인','excluded':'추가 도시·혼잡/잔여석·자동 예약'},
        'alternatives':[{'task':'예약 알림','value':'기한 누락 감소 가설','cost':'중간: 발송/재시도/권한','dependencies':'검증한 오픈 규칙·알림 동의','priority':'보류: 실제 기한 누락 근거 없음'},{'task':'추가 도시','value':'지역 선택 확대','cost':'높음: 지점·언어·영업 자료 검수','dependencies':'기존 도시 운영 검증','priority':'보류: 기존 후보부터 검증'},{'task':'동행자 투표','value':'합의 지원 가설','cost':'높음: 멤버십·회수·동시 편집','dependencies':'공유 필요성 피드백·TripMember 모델','priority':'보류: 현재 개인 여행 권한 유지'}]}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');args.output.chmod(0o600)
    print(json.dumps({'output':str(args.output),'synthetic':value.get('synthetic',value.get('cohort',{}).get('synthetic',False)),'provider_calls':0}))
if __name__=='__main__':main()
