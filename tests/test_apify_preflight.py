from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import httpx
import pytest

spec = spec_from_file_location('apify_preflight', Path(__file__).parents[1] / 'scripts/check_apify_setup.py')
module = module_from_spec(spec)
spec.loader.exec_module(module)


def test_preflight_is_read_only_and_excludes_account_identity():
    calls = []
    def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith('/limits'):
            data = {'limits': {'maxMonthlyUsageUsd': 5}}
        elif request.url.path.endswith('/monthly'):
            data = {'totalUsageCreditsUsdAfterVolumeDiscount': .1}
        elif '/acts/' in request.url.path:
            data = {'taggedBuilds': {'latest': {'buildNumber': '0.0.528'}}, 'pricingInfos': [{'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents': {'review-scraped': {'eventPriceUsd': .0006}}}}]}
        else:
            data = {'email': 'private@example.test', 'token': 'private-token', 'plan': {'id': 'FREE', 'tier': 'FREE', 'monthlyUsageCreditsUsd': 5}}
        return httpx.Response(200, json={'data': data})
    with httpx.Client(base_url='https://api.apify.com/v2/', transport=httpx.MockTransport(respond)) as client:
        report = module.inspect('private-token', client)
    assert len(calls) == 4 and all(method == 'GET' for method, _ in calls)
    assert report['runs_started'] == 0 and report['production_enabled'] is False
    assert report['monthly_limit_usd'] == 5
    assert 'private' not in str(report)


def test_preflight_error_does_not_echo_provider_response():
    with httpx.Client(base_url='https://api.apify.com/v2/', transport=httpx.MockTransport(lambda _: httpx.Response(401, text='private-token'))) as client:
        with pytest.raises(ValueError, match='401') as error:
            module.inspect('private-token', client)
    assert 'private-token' not in str(error.value)
