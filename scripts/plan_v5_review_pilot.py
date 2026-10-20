#!/usr/bin/env python3
"""Offline capped pilot plan from read-only preflight/coverage. Never starts runs.

Execution remains the existing authenticated admin preview -> submit jobs flow.
A plan is not rights approval or a replacement for atomic per-call reservations.
"""
import argparse,json
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation
from pathlib import Path


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
        'execution':'Existing admin review-collection-preview and review-collection-runs APIs; each stage rechecks current rights/budget. No direct Actor start CLI.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preflight',type=Path,required=True);p.add_argument('--coverage',type=Path,required=True)
    p.add_argument('--approved-cap',default='0');p.add_argument('--configured-cap',default='0');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=plan(json.loads(a.preflight.read_text()),json.loads(a.coverage.read_text()),a.approved_cap,a.configured_cap)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':result['status'],'reason_codes':result['reason_codes'],'runs_started':0}))
