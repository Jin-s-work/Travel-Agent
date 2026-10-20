"""Preparation of a separate bounded observation policy; no production promotion.

Returned records describe themselves, never Google's review population or local
residents. Source continuity is explicitly not part of this contract. Enabling
research or approving a provider contract alone cannot enable consumer output.
"""

VERSION='bounded_observation_v1'
REQUIRED=('schema_verified','original_separation_verified','original_language_verified',
          'unfiltered_observation_verified','individual_publication_dates_verified',
          'remote_cost_cap_verified','bounded_policy_approved')


def contract_reasons(body,*,status,expired):
    reasons=[]
    if body.get('observation_profile')!=VERSION:reasons.append('BOUNDED_POLICY_NOT_SELECTED')
    if status!='approved' or expired:reasons.append('BOUNDED_POLICY_UNAPPROVED_OR_EXPIRED')
    if body.get('synthetic'):reasons.append('SYNTHETIC_EVIDENCE')
    reasons += [k.upper()+'_UNVERIFIED' for k in REQUIRED if body.get(k) is not True]
    return reasons


def evaluate_bounded(stats,coverage,*,contract=None,quality_verified=False,rights_verified=False,synthetic=False):
    counts=stats['counts']
    # Aggregate invariants do not depend on city: local_count already came from
    # the run's frozen language profile. Validate arithmetic without reclassifying.
    values=list(counts.values())
    if any(type(v) is not int or v<0 for v in values):raise ValueError('INVALID_COUNT')
    t,c,u,l,k=(counts[x] for x in ('text_count','classified_count','unknown_count','local_count','korean_count'))
    if t!=c+u or l+k>c or counts['observed_records']!=t+counts['rating_only_count']+counts['extraction_unknown_count']:
        raise ValueError('COUNT_INVARIANT_VIOLATION')
    ratio=lambda n,d:n/d if d else None
    reasons=[]
    if coverage.get('observation_profile')!=VERSION:reasons.append('OBSERVATION_PROFILE_MISMATCH')
    if not contract or not contract.get('bounded_contract_ready'):reasons.append('BOUNDED_POLICY_UNAPPROVED')
    if not rights_verified:reasons.append('RIGHTS_UNVERIFIED')
    if not quality_verified:reasons.append('CLASSIFICATION_QUALITY_UNVERIFIED')
    if synthetic:reasons.append('SYNTHETIC_PROVIDER')
    if coverage.get('partial') or not coverage.get('collection_complete'):reasons.append('PARTIAL_COLLECTION')
    if counts['extraction_unknown_count']:reasons.append('EXTRACTION_UNKNOWN')
    threshold=bool(t and c>=100 and u/t<=.10 and l/t>=.60 and (k+u)/t<=.10)
    if not threshold:reasons.append('LANGUAGE_THRESHOLD_NOT_MET')
    return {'profile':VERSION,'scope':'provider_observation','strict_pass':False,
        'research_qualified':not reasons,'production_enabled':False,'status':'research_only',
        'latest_records_claim':False,'population_inference_allowed':False,'residency_inference_allowed':False,
        'metrics':{'classified_local_share':ratio(l,c),'classified_korean_share':ratio(k,c),
                   'local_share_lower_bound':ratio(l,t),'korean_share_upper_bound':ratio(k+u,t),'unknown_share':ratio(u,t)},
        'all_observed_text_local':bool(not reasons and l==t==c and k==u==0),
        'denominators':{'text':t,'classified':c},'reason_codes':reasons,
        'platform_quality_gate':'separate_same_platform_rating_4.2_of_5_count_200_required'}
