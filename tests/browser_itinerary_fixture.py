"""Phase05 browser harness: author-created places and no-network fake routes."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import threading
import uvicorn
import browser_discovery_fixture as discovery
from src.reliability.providers import ProviderResult
from src.reliability.budget import BudgetPolicy

base,app=discovery.base,discovery.app
class BrowserRoutes:
    name='synthetic_routes';sku='matrix';adapter_version='browser-fixture-v1'
    policy_version='synthetic-test-only-v1';usage_permitted=True
    def lookup(self,request,timeout_seconds):
        now=datetime.now(timezone.utc)
        return ProviderResult({'duration_minutes':10,'distance_meters':500,
            'checked_at':(now-timedelta(seconds=1)).isoformat(),
            'expires_at':(now+timedelta(minutes=30)).isoformat()}, {'matrix_elements':1})
config=deepcopy(app.state.budget.policy.config)
config['prices']['synthetic_routes/matrix']={'currency':'USD','rates_per_million':{'matrix_elements':'0'},'max_units':{'matrix_elements':1}}
app.state.budget.policy=BudgetPolicy(config)
app.state.travel_time.provider=BrowserRoutes()

if __name__=='__main__':
    print(json.dumps({'base_url':base.BASE,'fixture_dir':str(base.SANDBOX),'synthetic':True,'route_provider':'fake-no-network'},ensure_ascii=False),flush=True)
    threading.Thread(target=lambda:uvicorn.run(base.idp,host='127.0.0.1',port=8766,log_level='warning',access_log=False),daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=8765,log_level='warning',access_log=False)
