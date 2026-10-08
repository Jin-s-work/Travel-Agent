#!/usr/bin/env python3
"""Read-only Apify connection, allowance and Actor pricing preflight. Never starts a run."""
import argparse
import json
import os
from pathlib import Path
import httpx
from dotenv import dotenv_values


def inspect(token, client=None):
    if not token:raise ValueError('APIFY_TOKEN is missing')
    own=client is None
    client=client or httpx.Client(base_url='https://api.apify.com/v2/',headers={'Authorization':'Bearer '+token},timeout=25)
    try:
        def get(path):
            response=client.get(path)
            if response.status_code!=200:raise ValueError(f'Apify status {response.status_code}; check token and connection')
            return response.json()['data']
        user=get('users/me');limits=get('users/me/limits');usage=get('users/me/usage/monthly');actor=get('acts/compass~google-maps-reviews-scraper')
        plan=user.get('plan',{});tier=plan.get('tier');pricing=actor.get('pricingInfos',[])[-1]
        events=pricing.get('pricingPerEvent',{}).get('actorChargeEvents',{})
        prices={key:value.get('eventTieredPricingUsd',{}).get(tier,{}).get('tieredEventPriceUsd',value.get('eventPriceUsd')) for key,value in events.items()}
        return {'connection':'ok','plan':plan.get('id'),'monthly_credits_usd':plan.get('monthlyUsageCreditsUsd'),
                'monthly_limit_usd':limits.get('limits',{}).get('maxMonthlyUsageUsd'),
                'used_credits_usd':usage.get('totalUsageCreditsUsdAfterVolumeDiscount'),
                'actor':'compass/google-maps-reviews-scraper','latest_build':actor.get('taggedBuilds',{}).get('latest',{}).get('buildNumber'),
                'pricing_model':pricing.get('pricingModel'),'event_prices_usd':prices,'runs_started':0,
                'production_enabled':False,'next_step':'Verify branches, provider fields, language quality and capped pilot before enabling recommendations'}
    finally:
        if own:client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();values=dotenv_values(args.env,interpolate=False) if args.env else os.environ
    try:report=inspect(values.get('APIFY_TOKEN'))
    except (ValueError,httpx.HTTPError):
        print('Apify 연결을 확인하지 못했습니다. 토큰과 네트워크를 확인해 주세요.');return 1
    text=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    print(text,end='');return 0

if __name__=='__main__':raise SystemExit(main())
