"""Read-only pages of an immutable run. Cursors cannot change actor, trip or order."""
from copy import deepcopy
import base64
import hashlib
import hmac
import json
from src.foundation.repository import DomainError

VERSION='discovery_result_v1'
CANDIDATE_LIMIT=200
PAGE_SIZE=12
KINDS=('local_discovery','landmark','reference')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def ordered(section):
    seen=set();result=[]
    for group in ('items','needs_confirmation'):
        for item in section.get(group,[]):
            if item['place_id'] not in seen:
                seen.add(item['place_id']);result.append((group,item))
    return result


def counts(snapshot,candidates,result):
    unique={c['place_id'] for c in candidates}
    visible={i['place_id'] for s in result['sections'].values() for _,i in ordered(s)}
    eligible={i['place_id'] for s in result['sections'].values() for i in s['items']}
    scored={i['place_id'] for s in result['sections'].values() for _,i in ordered(s) if i.get('ranking_diagnostics')}
    selection=snapshot.get('candidate_selection',{});external=snapshot.get('public_discovery',{})
    return dict(requested_limit=snapshot.get('candidate_pool_limit',CANDIDATE_LIMIT),fetched_count=external.get('fetched_count'),unique_count=len(unique),deduped_count=external.get('deduped_count',len(candidates)-len(unique)),normalization_rejected_count=external.get('rejected_count'),
        eligible_count=len(eligible),scored_count=len(scored),displayable_count=len(visible),displayed_count=0,deferred_count=len(visible),
        evaluated_count=len(candidates),stored_candidates_considered=selection.get('stored_candidates_considered',len(candidates)),
        coverage={'kind':'stored_catalog','provider_limit':external.get('provider_limit',60),'provider_state':external.get('state'),'provider_coverage':external.get('coverage'),'actual_provider_fetched_count':external.get('fetched_count')},
        truncation_reason='candidate_cap' if selection.get('selection_truncated') else external.get('truncation_reason') or ('provider_limit' if external.get('truncated') else None))


def _encode(payload,secret):
    data=base64.urlsafe_b64encode(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).rstrip(b'=')
    return data.decode()+'.'+hmac.new(secret.encode(),data,hashlib.sha256).hexdigest()


def _decode(token,secret):
    try:
        if len(token)>2500:raise ValueError()
        data,signature=token.split('.')
        if not hmac.compare_digest(signature,hmac.new(secret.encode(),data.encode(),hashlib.sha256).hexdigest()):raise ValueError()
        return json.loads(base64.urlsafe_b64decode(data+'='*(-len(data)%4)))
    except (ValueError,TypeError,UnicodeError):raise DomainError('CURSOR_INVALID','이어보기 정보가 유효하지 않아요. 저장된 결과를 다시 열어 주세요.',409)


def page(run,actor_id,trip_id,secret,section=None,cursor=None):
    value=deepcopy(run);result=value.get('result')
    value.update(result_contract=VERSION,snapshot_version=digest(run['snapshot']),job_state=run['state'],
        freshness_state='stale' if run.get('data_status')=='stale' or run.get('input_status')=='stale' else 'current',
        availability_state='pending' if not result else 'available',result_count=0,blocking_reason=run.get('error_code'),warnings=run.get('reason_codes',[]),next_actions=['read_run'] if not result else [])
    if not result:
        code=run.get('error_code') or ''
        value['freshness_state']='unknown'
        if run['state'] in ('failed','cancelled'):
            value['availability_state']='budget_exhausted' if any(s in code for s in ('BUDGET','COST_LIMIT')) else 'provider_failed' if 'PROVIDER' in code else 'unavailable'
            value['next_actions']=['read_saved_results','save_place'] if value['availability_state']=='budget_exhausted' else ['retry_request','read_saved_results']
        return value
    if section is not None and section not in KINDS:raise DomainError('CURSOR_INVALID','추천 구획을 확인해 주세요.',422)
    basis=dict(actor=actor_id,trip=trip_id,run=run['run_id'],snapshot=value['snapshot_version'],version=run['conditions_version'],contract=VERSION,
               view=digest({k:[i['place_id'] for _,i in ordered(result['sections'].get(k,{}))] for k in KINDS}))
    offset=0
    if cursor:
        saved=_decode(cursor,secret)
        if any(saved.get(k)!=v for k,v in basis.items()) or saved.get('section')!=section or type(saved.get('offset')) is not int or saved['offset']<0:
            raise DomainError('CURSOR_SCOPE_CHANGED','다른 여행·조건의 이어보기예요. 저장된 결과를 다시 열어 주세요.',409)
        offset=saved['offset']
    pages={};shown=set();all_ids=set()
    for kind in KINDS:
        groups=result['sections'].get(kind,{})
        rows=ordered(groups);all_ids.update(item['place_id'] for _,item in rows)
        start=offset if section==kind else 0
        chunk=rows[start:start+PAGE_SIZE] if not section or section==kind else []
        next_offset=start+len(chunk)
        pages[kind]={'total':len(rows),'offset':start,'returned':len(chunk),'page_size':PAGE_SIZE,
                     'next_cursor':_encode({**basis,'section':kind,'offset':next_offset},secret) if next_offset<len(rows) and (not section or section==kind) else None}
        for name in groups:groups[name]=[item for group,item in chunk if group==name]
        shown.update(item['place_id'] for _,item in chunk)
    value['result_count']=len(all_ids);value['pages']=pages
    value['counts']={**result.get('candidate_counts',{}),'displayed_count':len(shown),'deferred_count':len(all_ids)-len(shown),'displayable_count':len(all_ids)}
    result['candidate_counts']=value['counts']
    states=result.get('section_status',{})
    for kind,meta in states.items():
        meta.update(availability_state=meta['state'],freshness_state='stale' if meta['state'] in ('stale','expired') else value['freshness_state'],
            result_count=pages.get(kind,{}).get('total',0),blocking_reason=meta['reason_codes'][0] if meta.get('reason_codes') and not meta.get('displayable_count') else None,
            warnings=meta.get('reason_codes',[]),next_actions=[] if meta.get('displayable_count') else ['change_section','edit_conditions','save_place'])
    if not all_ids:
        value['availability_state']='unavailable';value['next_actions']=['change_section','edit_conditions','save_place']
    return value


def legacy_view(run):
    """Preserve the unpaged HTTP contract while SQL retains the complete pool."""
    value=deepcopy(run)
    if not value.get('result') or value['snapshot'].get('result_contract')!=VERSION:return value
    result=value['result']
    for kind,groups in result['sections'].items():
        limit=12 if kind=='reference' else value['snapshot'].get('limit',6)
        for name in ('items','needs_confirmation'):
            tail=groups[name][limit:];groups[name]=groups[name][:limit]
            for item in tail:
                item['selection_status']='limit_deferred';item['reason_codes']=list(dict.fromkeys(item.get('reason_codes',[])+['RESULT_LIMIT']))
            groups['excluded'].extend(tail)
        if kind in result.get('section_status',{}):result['section_status'][kind]['displayable_count']=len(ordered(groups))
    result['counters']={k:{g:len(rows) for g,rows in groups.items()} for k,groups in result['sections'].items()}
    primary=[result['sections'][k] for k in ('local_discovery','landmark')]
    qualified={i['place_id'] for s in primary for i in s['items']}
    confirmation={i['place_id'] for s in primary for i in s['needs_confirmation']}-qualified
    references={i['place_id'] for _,i in ordered(result['sections']['reference'])}-qualified-confirmation
    shown=qualified|confirmation|references
    result.setdefault('summary',{}).update(displayable_count=len(shown),qualified_count=len(qualified),confirmation_count=len(confirmation),reference_count=len(references))
    if 'candidate_counts' in result:result['candidate_counts'].update(displayed_count=len(shown),deferred_count=max(0,result['candidate_counts']['displayable_count']-len(shown)))
    if 'candidate_selection' in result:result['candidate_selection']['displayed']=sum(len(ordered(s)) for s in result['sections'].values())
    return value
