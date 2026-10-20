from datetime import datetime,timedelta,timezone
from decimal import Decimal
import httpx
import pytest
from scripts.plan_v5_review_pilot import plan
from scripts.check_apify_setup import inspect


def data(**changes):
    return {'plan':'FREE','monthly_credits_usd':5,'monthly_limit_usd':5,'used_credits_usd':4.7,
        'checked_at':datetime.now(timezone.utc).isoformat(),'account_active_runs':0,'shared_account_check':True,
        'event_prices_usd':{'review-scraped':.0006},**changes}

COVERAGE={'active_apify_policies':1,'approved_provider_contracts':1,'cities':{'tokyo':{'approved_review_links':1}}}


def test_plan_cannot_exceed_any_cap_and_never_executes():
    r=plan(data(),COVERAGE,'1','0.5')
    assert Decimal(r['effective_batch_cap_usd'])==Decimal('.3')
    assert r['stages'][0]['fits_review_events_only']
    assert not r['stages'][1]['fits_review_events_only']
    assert r['runs_started']==0 and r['production_enabled'] is False
    assert plan(data(),COVERAGE,'0','1')['status']=='blocked'


@pytest.mark.parametrize('changes,reason',[
    ({'account_active_runs':1},'SHARED_ACCOUNT_USAGE_UNRESOLVED'),
    ({'shared_account_check':False},'SHARED_ACCOUNT_USAGE_UNRESOLVED'),
    ({'checked_at':(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()},'PREFLIGHT_EXPIRED'),
    ({'plan':'PAID'},'FREE_PLAN_NOT_CONFIRMED'),
])
def test_pilot_requires_fresh_free_account_scope(changes,reason):
    result=plan(data(**changes),COVERAGE,'1','1')
    assert result['status']=='blocked' and reason in result['reason_codes']


def test_live_preflight_checks_account_total_active_not_first_page_length():
    calls=[]
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/actor-runs'):
            assert request.url.params['status']=='READY,RUNNING,TIMING-OUT,ABORTING'
            body={'total':17,'items':[{'id':'private-remote-id'}]}
        elif request.url.path.endswith('/limits'):body={'limits':{'maxMonthlyUsageUsd':5}}
        elif request.url.path.endswith('/monthly'):body={'totalUsageCreditsUsdAfterVolumeDiscount':.1}
        elif '/acts/' in request.url.path:body={'pricingInfos':[{'pricingModel':'PAY_PER_EVENT','pricingPerEvent':{}}]}
        else:body={'email':'PRIVATE','plan':{'id':'FREE','tier':'FREE','monthlyUsageCreditsUsd':5}}
        return httpx.Response(200,json={'data':body})
    with httpx.Client(base_url='https://api.apify.com/v2/',transport=httpx.MockTransport(respond)) as client:
        report=inspect('PRIVATE',client,include_active=True)
    assert len(calls)==5 and all(r.method=='GET' for r in calls)
    assert report['account_active_runs']==17 and report['shared_account_check']
    assert 'private' not in str(report).lower()
