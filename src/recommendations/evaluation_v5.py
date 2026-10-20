"""Independent retrieval/ranking measures. Never generates relevance labels."""
import random
from statistics import mean


def candidate_recall(pool,independent_relevant):
    if independent_relevant is None:return {'value':None,'reason':'INDEPENDENT_TRUTH_MISSING','denominator':None,'numerator':None}
    truth=set(independent_relevant);found=set(pool[:200]) & truth
    return {'value':len(found)/len(truth) if truth else None,'numerator':len(found),'denominator':len(truth),
            'reason':None if truth else 'NO_RELEVANT_TRUTH','k':200}


def bootstrap95(deltas,*,seed=20261008,iterations=2000):
    if not deltas:return {'mean':None,'interval95':None,'paired_queries':0}
    rng=random.Random(seed);n=len(deltas)
    samples=sorted(mean(rng.choices(deltas,k=n)) for _ in range(iterations))
    return {'mean':mean(deltas),'interval95':[samples[int(iterations*.025)],samples[min(iterations-1,int(iterations*.975))]],
            'paired_queries':n,'iterations':iterations,'seed':seed,'unit':'paired_query'}


def compare_report(rows,summary,*,label_source):
    held=[r for r in rows if r['split']=='holdout']
    pairs=[(r,r['models']['hybrid_v6']['metrics']['ndcg']-r['models']['hybrid_v5']['metrics']['ndcg']) for r in held
           if all(r['models'][v]['metrics']['ndcg'] is not None for v in ('hybrid_v5','hybrid_v6'))]
    effect=bootstrap95([d for _,d in pairs]);groups={}
    required_groups={(city,section) for city in ('tokyo','barcelona') for section in ('local_discovery','landmark')}
    for key in sorted(required_groups | {(r['city'],r['section']) for r in held}):
        ds=[d for r,d in pairs if (r['city'],r['section'])==key]
        groups['/'.join(key)]=bootstrap95(ds)
    a=summary['hybrid_v5']['latency_ms']['p95'];b=summary['hybrid_v6']['latency_ms']['p95']
    reasons=[]
    if label_source!='independent_human':reasons.append('NO_INDEPENDENT_HUMAN_LABELS')
    if len(pairs)!=len(held):reasons.append('UNJUDGED_HOLDOUT_QUERIES')
    if len(pairs)<40 or any(sum(r['city']==c for r,_ in pairs)<20 for c in ('tokyo','barcelona')):reasons.append('HOLDOUT_SUPPORT_INSUFFICIENT')
    if effect['mean'] is None or effect['mean']<.03:reasons.append('NDCG_GAIN_BELOW_0.03_OR_UNMEASURED')
    if effect['interval95'] is None or effect['interval95'][0]<=0:reasons.append('GAIN_INTERVAL_INCONCLUSIVE')
    if any(v['mean'] is None or v['mean']<-.02 for v in groups.values()):reasons.append('CITY_MODE_REGRESSION_OR_UNMEASURED')
    if summary['hybrid_v6']['hard_violations']:reasons.append('HARD_CONSTRAINT_VIOLATIONS')
    if any(summary[v].get('deterministic_queries')!=len(rows) for v in ('hybrid_v5','hybrid_v6')):reasons.append('NON_DETERMINISTIC_RANKING')
    if not a or b>a*1.2:reasons.append('WARM_ENGINE_P95_REGRESSION')
    return {'baseline':'hybrid_v5','challenger':'hybrid_v6','holdout_ndcg_delta':effect,'city_mode_deltas':groups,
            'warm_engine_p95_ratio':b/a if a else None,'engine_only_not_render_latency':True,
            'promotion_candidate':not reasons,'automatic_promotion':False,'production_model':'hybrid_v5',
            'reason_codes':reasons,'online_save_rate':None,'visit_satisfaction':None}


def leakage_audit(queries):
    report={'shared_places':[],'shared_review_groups':[],'shared_query_groups':[]}
    for field,target in (('query_group','shared_query_groups'),('travel_group','shared_query_groups'),('review_group','shared_review_groups')):
        groups={}
        for q in queries:
            if q.get(field):groups.setdefault(q[field],set()).add(q['split'])
        report[target].extend(k for k,v in groups.items() if len(v)>1)
    places={}
    for q in queries:
        for p in q['candidates']:places.setdefault(p['place_id'],set()).add(q['split'])
    report['shared_places']=sorted(k for k,v in places.items() if len(v)>1)
    report['split_unit']='declared_query_and_travel_groups; place overlap reported'
    return report
