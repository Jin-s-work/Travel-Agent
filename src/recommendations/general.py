"""General v3: explicit gate/visit/ranking separation with legacy replay intact."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import json
from .registry import section_models, MODELS

GROUPS = ('items', 'needs_confirmation', 'insufficient_data', 'excluded')
DEFAULTS = {'min_local_share': .6, 'max_korean_share': .1, 'min_rating': 4.2, 'min_count': 200}
LABELS = {'local_discovery': '현지어 리뷰로 찾기', 'landmark': '유명한 곳', 'reference': '주변 장소 참고하기'}


def thresholds(snapshot):
    language = snapshot.get('review_language_filter') or {}
    rating = snapshot.get('rating_filter') or {}
    return {**{k:language.get(k,v) for k,v in DEFAULTS.items() if k in ('min_local_share','max_korean_share')},
            **{k:rating.get(k,v) for k,v in DEFAULTS.items() if k in ('min_rating','min_count')}}


def relaxed(values):
    return values['min_local_share'] < .6 or values['max_korean_share'] > .1 or values['min_rating'] < 4.2 or values['min_count'] < 200


def _feature(value, refs=(), *, reason=None, current=None):
    return {'value':value,'status':'verified' if value is not None else 'unspecified' if reason=='INPUT_UNSPECIFIED' else 'unknown',
            'source_ids':sorted(set(refs)), 'as_of':current.isoformat() if current else None,
            'version':'evidence_features_v3','missing_reason':reason if value is None else None}


def _language(candidate, current, limits):
    from .engine import _review
    review, qualified, reasons = _review(candidate, current)
    original_reasons=(candidate.get('review_evidence') or {}).get('evaluation',{}).get('reason_codes') or []
    if review.get('state')!='available':
        return review,False,list(dict.fromkeys(original_reasons+reasons)),{}
    counts = review.get('counts') or {}
    names = ('text_count','classified_count','unknown_count','local_count','korean_count')
    values = [counts.get(k) for k in names]
    if any(type(n) is not int or n < 0 for n in values):
        return review, False, ['INVALID_REVIEW_COUNTS'], {}
    t,c,u,l,k = values
    record_count=counts.get('observed_records',counts.get('observed_record_count'))
    extra_counts=(counts.get('rating_only_count'),counts.get('extraction_unknown_count'))
    if record_count is not None and (type(record_count) is not int or record_count<t):
        return review, False, ['COUNT_INVARIANT_VIOLATION'], {}
    if all(v is not None for v in extra_counts) and (any(type(v) is not int or v<0 for v in extra_counts) or record_count!=t+sum(extra_counts)):
        return review, False, ['COUNT_INVARIANT_VIOLATION'], {}
    if t != c+u or l+k > c:
        return review, False, ['COUNT_INVARIANT_VIOLATION'], {}
    # Only numeric distribution thresholds may be relaxed. Every other gate
    # remains the verified evaluation's decision; failed/unknown quality is closed.
    ev = review.get('evaluation') or {}
    numeric = {'LOCAL_SHARE_BELOW_MIN','KOREAN_SHARE_ABOVE_MAX'}
    immutable = [r for r in ev.get('reason_codes',[]) if r not in numeric]
    gate = review.get('state') == 'available' and ev.get('decision') in ('pass','fail') and ev.get('quality_pass', True) is True and not immutable
    immutable.extend(ev.get('quality_reason_codes') or [])
    if c < 100: immutable.append('INSUFFICIENT_CLASSIFIED_TEXTS')
    if not t: immutable.append('NO_TEXT_REVIEWS')
    elif Fraction(u,t) > Fraction('0.1'): immutable.append('TOO_MANY_UNKNOWN')
    metrics = {'local_lower':l/t if t else None, 'korean_upper':(k+u)/t if t else None, 'language_unknown':u/t if t else None}
    errors=list(dict.fromkeys(immutable or ([] if gate else reasons or ['REVIEW_REQUIRED_UNSUPPORTED'])))
    if t and Fraction(l,t) < Fraction(str(limits['min_local_share'])):errors.append('LOCAL_SHARE_BELOW_MIN')
    if t and Fraction(k+u,t) > Fraction(str(limits['max_korean_share'])):errors.append('KOREAN_SHARE_ABOVE_MAX')
    return review, gate and not errors, list(dict.fromkeys(errors)), metrics


def _iconic(facts, candidate):
    value, rows, reason = facts.get('iconic_evidence')
    if not isinstance(value,dict):return None,[],None
    refs = facts.refs(rows)
    actual = [facts.sources[r] for r in refs if r in facts.sources]
    synthetic = candidate.get('synthetic') is True
    typ = value.get('evidence_type')
    if (typ in ('official_landmark','heritage') or typ is None and value.get('level') in ('city','national','world')) and any(s.get('source_type') in ('official','tourism','government') or synthetic and s.get('source_type')=='synthetic' for s in actual):
        return 'official_landmark',refs,None
    if typ == 'editorial_recognition' and any(s.get('source_type') in ('editorial','tourism') or synthetic and s.get('source_type')=='synthetic' for s in actual):
        return 'editorial_recognition',refs,None
    if typ == 'platform_popular':
        from .engine import _rating
        rating,state,_,rating_refs = _rating(facts,{'min_rating':0,'min_count':1})
        if state=='confirmed' and value.get('platform') == rating['platform']:
            return 'platform_popular',sorted(set(refs+rating_refs)),rating
    return None,refs,None


def candidate(snapshot, source, kind, current, config):
    from .engine import _candidate,Facts,_rating
    # Reuse all booking/closure/diet/route constraints, but separate the section
    # evidence gate from those checks. No editorial model can qualify a local card.
    base=deepcopy(snapshot)
    base['review_language_filter']={'required':False,'apply_only_if_qualified':False,'apply_to':[]}
    base['rating_filter']={'enabled':False,'apply_to':[]}
    item=_candidate(base,source,'landmark' if kind=='landmark' else 'local_discovery',current,config)
    facts=Facts(source,snapshot['conditions']['visit'],current)
    limits=thresholds(snapshot)
    gate_reasons=[];iconic=None;iconic_refs=[];iconic_rating=None;metrics={};language_ok=False;rating=None;rating_refs=[]
    if not facts.sources:gate_reasons.append('SOURCE_POLICY_UNAVAILABLE')
    # Public-map identity is sufficient only for the labelled reference section.
    if kind != 'reference' and (source.get('identity_status')!='verified' or source.get('pack_status')!='approved'):
        gate_reasons.append('PLACE_IDENTITY_UNVERIFIED')
    if kind=='local_discovery':
        review,language_ok,errors,metrics=_language(source,current,limits)
        gate_reasons.extend(errors)
        rating,state,code,rating_refs=_rating(facts,limits,review)
        if state!='confirmed':gate_reasons.append(code)
        item['review_evidence']=deepcopy(review)
    elif kind=='landmark':
        iconic,iconic_refs,iconic_rating=_iconic(facts,source)
        if iconic is None:gate_reasons.append('ICONIC_EVIDENCE_UNKNOWN')
    gate_reasons=list(dict.fromkeys(gate_reasons))
    checks=item['visit_fit']['checks']
    # public source identity uncertainty is still shown, but does not masquerade
    # as verified editorial review evidence.
    visit='violated' if any(c['state']=='failed' for c in checks) else 'unknown' if any(c['state']=='unknown' for c in checks) else 'satisfied'
    rankable=not gate_reasons and visit!='violated'
    eligible='ineligible' if visit=='violated' else 'needs_confirmation' if visit=='unknown' else 'eligible'
    distance=(item.get('movement') or {}).get('straight_line_m',(item.get('movement') or {}).get('distance_m'))
    features={k:_feature(v,current=current) for k,v in metrics.items()}
    features['straight_distance']=_feature(distance,reason='INPUT_UNSPECIFIED' if not snapshot['conditions'].get('origin') else 'COORDINATES_UNKNOWN',current=current)
    features['iconic_evidence']=_feature(iconic,iconic_refs,reason='ICONIC_EVIDENCE_UNKNOWN',current=current)
    platform_rating=rating or iconic_rating
    for key,field in [('rating','rating'),('rating_count','total_rating_count')]:
        features[key]=_feature(platform_rating.get(field) if platform_rating else None,rating_refs or iconic_refs,reason='RATING_UNSUPPORTED',current=current)
    features['visit_fit']=_feature(visit,[ref for check in checks for ref in check['source_ids']],current=current)
    preference=(snapshot['conditions'].get('preferred') or {}).get('tags') or []
    features['preference_match']=_feature(len(set(preference)&set(source.get('tags') or []))/len(set(preference)) if preference else None,reason='INPUT_UNSPECIFIED',current=current)
    custom=kind=='local_discovery' and relaxed(limits)
    item.update(recommendation_type=kind,ranker_version=section_models(snapshot)[kind],score=None,score_complete=False,
        rankable=rankable,ordering_kind=MODELS[section_models(snapshot)[kind]]['ordering_kind'],ordering_profile=snapshot.get('ordering_profile','evidence'),
        language_qualified=language_ok if kind=='local_discovery' else None,eligibility=eligible,section_qualified=not gate_reasons,
        section_reason_codes=gate_reasons,features=features,personalized=False,iconic_kind=iconic,
        iconic_evidence={'kind':iconic,'source_ids':iconic_refs,'platform_rating':iconic_rating} if iconic else None,
        criteria_group='custom_criteria' if custom else 'default_strict' if kind=='local_discovery' else kind,
        strict_badge=kind=='local_discovery' and language_ok and not gate_reasons and not custom,
        reason_codes=list(dict.fromkeys([c['reason_code'] for c in checks if c['state']!='confirmed']+gate_reasons)),applied_thresholds=limits if kind=='local_discovery' else None)
    # v1/v2 components remain diagnostic only; no v3 total/reweight is calculated.
    item['score_components']={k:{**v,'weight':None} for k,v in item['score_components'].items()}
    for ident in iconic_refs:
        if not any(r['id']==ident for r in item['source_refs']):item['source_refs'].append(deepcopy(facts.sources[ident]))
    item['sources']=deepcopy(item['source_refs'])
    from .explanations import render
    item['supported_reasons']=render(item)
    item['reason_sentences']=[{k:v for k,v in r.items() if k!='code'} for r in item['supported_reasons']]
    return item


def order_key(item):
    if item.get('ranking_diagnostics'):
        from .hybrid import order_key as hybrid_order
        return hybrid_order(item)
    features=item['features'];ident=item['place_id'];nearby=item['ordering_profile']=='nearby'
    d=features['straight_distance']['value']
    distance=(d is None,d if d is not None else 0) if nearby else ()
    if item['recommendation_type']=='local_discovery':
        return (*distance,-features['local_lower']['value'],features['korean_upper']['value'],features['language_unknown']['value'],ident)
    if item['recommendation_type']=='landmark':
        kind=item['iconic_kind'];rating=(item.get('iconic_evidence') or {}).get('platform_rating') or {}
        cohort=(rating.get('platform',''),item.get('city',''),item.get('category',''),str(rating.get('scale',''))) if kind=='platform_popular' else ('','','','')
        return ({'official_landmark':0,'platform_popular':1,'editorial_recognition':2}[kind],*cohort,*distance,-rating.get('total_rating_count',0),-rating.get('rating',0),ident)
    return (*distance,ident)


def _select(items,limit,config):
    selected=[];deferred=[];counts=[Counter(),Counter(),Counter()]
    for item in sorted(items,key=order_key):
        fields=[('chain_id',config.chain_limit),('neighborhood',config.neighborhood_limit),('category',config.category_limit)]
        reason=next((field.upper()+'_LIMIT' for index,(field,cap) in enumerate(fields) if item.get(field) and counts[index][item[field]]>=cap),None)
        if len(selected)>=limit or reason:
            item['reason_codes'].append(reason or 'RESULT_LIMIT');item['selection_status']='diversity_deferred' if reason else 'limit_deferred';deferred.append(item);continue
        selected.append(item);item['selection_status']='selected'
        for index,(field,_) in enumerate(fields):
            if item.get(field):counts[index][item[field]]+=1
    return selected,deferred



def _state(count,reasons):
    if count:return 'ready'
    if not reasons:return 'empty'
    joined=' '.join(reasons)
    if any(word in joined for word in ('EXPIRED','STALE')):return 'expired'
    if any(word in joined for word in ('BUDGET','COST_LIMIT')):return 'budget_exhausted'
    if any(word in joined for word in ('PROVIDER_FAILED','PROVIDER_UNAVAILABLE')):return 'provider_failed'
    if any(word in joined for word in ('QUALITY','CLASSIFICATION','UNKNOWN','INSUFFICIENT_CLASSIFIED')):return 'quality_insufficient'
    if any(word in joined for word in ('BELOW_MIN','ABOVE_MAX','BELOW_THRESHOLD')):return 'criteria_not_met'
    if any(word in joined for word in ('PRODUCTION_DISABLED','PRODUCTION_OFF','FEATURE_DISABLED')):return 'disabled'
    return 'unavailable'

def recommend(snapshot,candidates,current,config):
    from .engine import ENGINE_VERSION
    sections={kind:{group:[] for group in GROUPS} for kind in LABELS}
    unique={}
    for row in candidates:
        ident=row.get('place_id')
        if not ident:continue
        if ident in unique and unique[ident]!=row:
            chosen=min((unique[ident],row),key=lambda p:json.dumps(p,sort_keys=True,ensure_ascii=False))
            unique[ident]={**deepcopy(chosen),'identity_status':'needs_confirmation'}
        else:unique[ident]=deepcopy(row)
    for kind in LABELS:
        if kind!='reference' and kind not in snapshot['conditions']['recommendation_types']:continue
        for ident in sorted(unique):
            source=unique[ident]
            if kind!='reference' and kind not in source.get('recommendation_types',[]):continue
            item=candidate(snapshot,source,kind,current,config)
            target='excluded' if item['eligibility']=='ineligible' or kind=='reference' and not item['section_qualified'] else 'insufficient_data' if not item['section_qualified'] else 'needs_confirmation' if item['eligibility']=='needs_confirmation' else 'items'
            sections[kind][target].append(item)
        if snapshot.get('recommendation_model_version') == 'hybrid_v4':
            from .hybrid import prepare
            prepare(sections[kind]['items'] + sections[kind]['needs_confirmation'],
                    list(unique.values()), snapshot, current)
        for group in ('items','needs_confirmation'):
            if kind=='reference':
                ordered=sorted(sections[kind][group],key=order_key)
                selected,deferred=ordered[:12],ordered[12:]
                for row in selected:row['selection_status']='selected'
                for row in deferred:row['selection_status']='limit_deferred';row['reason_codes'].append('RESULT_LIMIT')
            else:selected,deferred=_select(sections[kind][group],snapshot.get('limit',6),config)
            sections[kind][group]=selected;sections[kind]['excluded'].extend(deferred)
    # A strict failure is never rendered as a weak local suggestion. These cards
    # are available only in the independent reference section when permitted.
    status={}
    for kind,groups in sections.items():
        reasons=sorted({code for row in groups['insufficient_data'] for code in row['section_reason_codes']})
        count=len(groups['items'])+len(groups['needs_confirmation'])
        status[kind]={'label':LABELS[kind],'model':section_models(snapshot)[kind],'state':_state(count,reasons),
            'reason_codes':reasons,'displayable_count':count,'criteria_group':'custom_criteria' if kind=='local_discovery' and relaxed(thresholds(snapshot)) else 'default_strict' if kind=='local_discovery' else kind,
            'personalized':False,'ordering_profile':snapshot.get('ordering_profile','evidence')}
    # Insufficient evidence is reported as reason counts, not misleading cards.
    for kind in ('local_discovery','landmark'):
        status[kind]['insufficient_count']=len(sections[kind]['insufficient_data'])
        sections[kind]['insufficient_data']=[]
    counters={kind:{group:len(rows) for group,rows in groups.items()} for kind,groups in sections.items()}
    applied={k:deepcopy(snapshot.get(k)) for k in ('conditions','rating_filter','review_language_filter','ordering_profile')}
    applied['review_language_filter']={**(applied.get('review_language_filter') or {}),'required':True,'apply_to':['local_discovery'],'min_classified_texts':100,'max_unknown_share':.1}
    applied['rating_filter']={**(applied.get('rating_filter') or {}),'enabled':True,'apply_to':['local_discovery']}
    return {'sections':sections,'section_status':status,'section_models':section_models(snapshot),'counters':counters,
        'engine_version':'recommendation-engine-v4' if snapshot.get('recommendation_model_version') == 'hybrid_v4' else 'recommendation-engine-v3','config_version':config.version,'config':asdict(config),
        'computed_at':current.isoformat(),'requested_constraints':deepcopy(snapshot),
        'applied_constraints':applied,
        'reason_codes':['INSUFFICIENT_QUALIFIED_'+k.upper() for k in snapshot['conditions']['recommendation_types'] if len(sections[k]['items'])<snapshot.get('limit',6)],
        'unsupported_constraints':sorted({c for state in status.values() for c in state['reason_codes']}),
        'learning_status':'insufficient_evidence','personalization_status':'explicit_context_model' if snapshot.get('recommendation_model_version') == 'hybrid_v4' else 'general_model'}
