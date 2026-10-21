#!/usr/bin/env python3
"""Offline capped pilot plan from read-only preflight/coverage. Never starts runs.

Execution remains the existing authenticated admin preview -> submit jobs flow.
A plan is not rights approval or a replacement for atomic per-call reservations.
"""
import argparse,json,sys
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.research.profiles import QUALITY_POLICY  # noqa: E402


def plan(account,coverage,approved_cap,configured_cap):
    def amount(value):
        try:v=Decimal(str(value))
        except InvalidOperation:raise ValueError('INVALID_AMOUNT') from None
        if not v.is_finite() or v<0:raise ValueError('INVALID_AMOUNT')
        return v
    credits=amount(account['monthly_credits_usd']);usage=amount(account['used_credits_usd'])
    cap=min(Decimal('1'),amount(approved_cap),amount(configured_cap),max(Decimal(0),credits-usage),max(Decimal(0),amount(account['monthly_limit_usd'])-usage))
    reasons=[]
    checked=datetime.fromisoformat(account['checked_at'])
    if checked.tzinfo is None or not datetime.now(timezone.utc)-timedelta(minutes=5)<=checked<=datetime.now(timezone.utc):reasons.append('PREFLIGHT_EXPIRED')
    if account.get('plan')!='FREE':reasons.append('FREE_PLAN_NOT_CONFIRMED')
    if not account.get('shared_account_check') or account.get('account_active_runs')!=0:reasons.append('SHARED_ACCOUNT_USAGE_UNRESOLVED')
    if not cap:reasons.append('NO_EXISTING_APPROVED_CONFIGURED_BUDGET')
    if not coverage.get('active_apify_policies'):reasons.append('DATA_RIGHTS_POLICY_MISSING')
    if not coverage.get('approved_provider_contracts'):reasons.append('PROVIDER_CONTRACT_NOT_VERIFIED')
    if not any(c.get('approved_review_links') for c in coverage.get('cities',{}).values()):reasons.append('VERIFIED_BRANCH_LINK_MISSING')
    prices=account.get('event_prices_usd',{});per=prices.get('review-scraped')
    stages=[]
    for name,places,records in [('contract_smoke',1,40),('window_check',3,200),('two_city_coverage',10,200)]:
        review_cost=amount(per)*places*records if per is not None else None
        stages.append({'stage':name,'places':places,'records_per_place':records,
            'review_events_only_usd':str(review_cost) if review_cost is not None else None,
            'not_total_quote':True,'requires_previous_pass':name!='contract_smoke',
            'reuse_valid_previous_runs':True,'batch_cap_usd':str(cap),
            'fits_review_events_only':review_cost<=cap if review_cost is not None else None})
    return {'status':'blocked' if reasons else 'ready_for_authenticated_preview','reason_codes':reasons,
        'effective_batch_cap_usd':str(cap),'stages':stages,'runs_started':0,'production_enabled':False,
        'restart':'Resume saved run ID/checkpoint; unknown start outcome must not create another remote run.',
        'stop':'Cancel requests remote abort. Billing may continue; unknown costs stay reserved. Cleanup uses the existing remote deadline.',
        'execution':'Existing admin review-collection-preview and review-collection-runs APIs; each stage rechecks current rights/budget. No direct Actor start CLI.',
        'language_labeling':language_label_plan(stages)}


# Minimum number of cities a stage's places span (two_city_coverage spans both pilot cities).
# How the places are actually split between cities is decided at preview time [미확인].
STAGE_MIN_CITIES={'contract_smoke':1,'window_check':1,'two_city_coverage':2}
HELDOUT_SHARE=0.7  # label tool default (src.research.language_labels.DEFAULT_HELDOUT_SHARE)
# [추정] seconds per typed answer; kept equal to language_labels.SECONDS_PER_ROW by a test,
# not imported, so this tracked script does not depend on the labeling modules.
SECONDS_PER_ROW_ASSUMED=[4,8]


def language_label_plan(stages):
    """What a capped pilot can supply to the human language labels (offline arithmetic only).

    Label sets are frozen per city and split by place, so the comparison with the policy
    minimums uses one city's heldout places: max(1, round(places_in_city * 0.7)) * records.
    places_in_city is at most places - (min_cities - 1) (all other places in the other city).
    These are review-record upper bounds: rating-only records and duplicates reduce them, and
    the Korean share of real reviews is unmeasured. For the sentence unit the number of rows
    per review is unknown, so no sentence-count bound is given (null).
    """
    need=QUALITY_POLICY
    rows=[]
    for stage in stages:
        records=stage['places']*stage['records_per_place']
        city_places=stage['places']-(STAGE_MIN_CITIES[stage['stage']]-1)
        heldout_places=max(1,round(city_places*HELDOUT_SHARE))
        heldout_reviews=heldout_places*stage['records_per_place']
        rows.append({'stage':stage['stage'],'max_review_records_total':records,
            'min_cities':STAGE_MIN_CITIES[stage['stage']],
            'max_places_one_city':city_places,'max_heldout_places_one_city':heldout_places,
            'max_heldout_review_records_one_city':heldout_reviews,
            'could_reach_min_labels_review_unit_upper_bound':heldout_reviews>=need['min_labels'],
            'could_reach_min_original_checks_upper_bound':heldout_reviews>=need['min_original_checks'],
            'max_heldout_rows_sentence_unit':None,
            'expected_korean_truth':None,'expected_local_truth':None})
    return {'tool':'scripts/label_review_language.py (prepare -> label -> evaluate)','unit_default':'sentence',
        'bound_unit':'review records, one city, heldout split; sentence-unit bounds unknown [미확인]',
        'quality_gate_unit':'review (sentence-unit evaluations are diagnostics; gate blocked by SENTENCE_UNIT_NOT_PRODUCTION_UNIT)',
        'answers_by':'human only; detector output is never shown or used as an answer',
        'raw_text_location':'data/ (gitignored); never committed or copied into reports',
        'policy_minimums':{k:need[k] for k in ('min_labels','min_korean_truth','min_local_truth','min_local_predictions','min_original_checks')},
        'stages':rows,'korean_share':'unmeasured [미확인]; korean recall stays null until >=1 Korean truth row',
        'interval':'Wilson 95% (src.research.language.wilson95) for local precision and Korean recall',
        'heldout_share_assumed':HELDOUT_SHARE,'seconds_per_row_assumed':SECONDS_PER_ROW_ASSUMED,'labels_started':0,
        'labeling_time_budget':'separate from PRD decision 5 (2.5~4 h, bundle B); needs its own approval'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preflight',type=Path,required=True);p.add_argument('--coverage',type=Path,required=True)
    p.add_argument('--approved-cap',default='0');p.add_argument('--configured-cap',default='0');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=plan(json.loads(a.preflight.read_text()),json.loads(a.coverage.read_text()),a.approved_cap,a.configured_cap)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':result['status'],'reason_codes':result['reason_codes'],'runs_started':0}))
