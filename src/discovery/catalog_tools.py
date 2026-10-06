"""Explicit, repeatable operator import using the existing admin policy gates.

No network lookup, provider activation, personal trip write or fabricated fact is
performed. Approval is a separate explicit instruction with recorded evidence.
"""
from datetime import datetime,timezone
from .models import PackInput
from src.foundation.repository import DomainError


def inspect_pack(payload):
    pack=PackInput.model_validate(payload).model_dump(mode='json')
    if pack['synthetic']:
        raise DomainError('REAL_CATALOG_REQUIRED','운영 후보 등록은 실제 확인한 자료만 허용합니다.',422)
    if len({p['external_id'] for p in pack['places']})!=len(pack['places']):
        raise DomainError('DUPLICATE_BRANCH','같은 팩 안의 지점 ID가 중복됩니다.',422)
    current=datetime.now(timezone.utc)
    for place in pack['places']:
        sources={s['key']:s for s in place['sources']}
        if len(sources)!=len(place['sources']):
            raise DomainError('DUPLICATE_SOURCE','지점 안의 출처 키가 중복됩니다.',422)
        if not sources or not any(s['read_confirmed'] and s['display_permitted'] for s in sources.values()):
            raise DomainError('SOURCE_POLICY_UNVERIFIED','지점별 출처 읽기·표시 검토가 필요합니다.',422)
        for fact in place['facts']:
            if fact['status']=='verified' and (fact['source_key'] not in sources or not sources[fact['source_key']]['read_confirmed']):
                raise DomainError('SOURCE_NOT_READ','검증 사실의 출처를 확인해 주세요.',422)
            if datetime.fromisoformat(fact['expires_at'])<=current:
                raise DomainError('FACT_STALE','이미 만료된 사실이 있습니다. 새 확인 결과로 검토해 주세요.',422)
    return pack


def register_reviewed_pack(service,actor,payload,*,review_evidence=None):
    """Return a receipt; omission of evidence imports into needs_review only.

    A crash after import or source review is resumable. Replays never reactivate
    revoked sources or disabled packs. Duplicate review is a true no-op, including
    source versions and immutable recommendation snapshots.
    """
    pack=inspect_pack(payload)
    if review_evidence is not None and not 10<=len(review_evidence)<=1000:
        raise DomainError('REVIEW_EVIDENCE_REQUIRED','출처 이용 범위와 지점 확인 근거를 10~1000자로 남겨 주세요.',422)
    receipt=service.import_pack(actor,pack)
    if review_evidence is None:return {**receipt,'provider_calls':0,'approved_sources':0}
    stored=next(p for p in service.list_packs(actor) if p['id']==receipt['pack_id'])
    if stored['status']=='disabled':
        raise DomainError('PACK_DISABLED','중단한 후보팩은 새 버전으로 다시 검토해 주세요.',409)
    approved=0
    # Preflight every source before changing any source. A revoked source does
    # not partially reactivate another branch during a repeat registration.
    sources={s['id']:s for place in stored['places'] for s in place['sources']}
    for source in sources.values():
        if source['status']=='revoked':raise DomainError('SOURCE_REVOKED','철회 출처는 새 키와 새 검토가 필요합니다.',409)
        if not source['read_confirmed'] or not source['display_permitted']:
            raise DomainError('SOURCE_POLICY_UNVERIFIED','읽기·표시가 검토되지 않은 출처는 활성화하지 않습니다.',422)
        if source['policy_version']!=pack['version']:
            raise DomainError('SOURCE_VERSION_CONFLICT','이전 버전의 출처를 재사용했습니다. 새 확인에는 새 출처 키를 사용해 주세요.',409)
    for source in sources.values():
        if source['status']=='active':continue
        service.review_source(actor,source['id'],{'expected_version':source['version'],'status':'active',
            'read_confirmed':True,'display_permitted':True,'policy_version':pack['version'],'evidence':review_evidence})
        approved+=1
    if stored['status']!='approved':
        service.approve_pack(actor,stored['id'],{'status':'approved','evidence':review_evidence})
    return {**receipt,'status':'approved','approved_sources':approved,'provider_calls':0,'places':len(pack['places'])}
