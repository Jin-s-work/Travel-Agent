"""SIGKILL after a metered route receipt and after itinerary activation.

The temporary service is reconstructed in a new interpreter. Provider calls are
fake, fsynced and counted separately from the application database.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.foundation.db import Database
from src.foundation.repository import Repository,dump
from src.discovery.service import DiscoveryService
from src.itineraries.service import Itineraries
from src.itineraries.travel_time import TravelTime
from src.reliability.budget import Budget,BudgetPolicy
from src.reliability.providers import ProviderGateway
from src.reliability.jobs import Jobs
from src.reliability.dispatcher import JobContext
from tests.test_foundation_api import service
from tests.test_discovery_foundation import discovery
from tests.test_itinerary_api import planning,prepare,submit,VerifiedRoutes


class CountedRoutes(VerifiedRoutes):
    def __init__(self,path):super().__init__();self.path=Path(path)
    def lookup(self,request,timeout_seconds):
        with self.path.open('a') as output:
            output.write(hashlib.sha256(dump(request).encode()).hexdigest()+'\n')
            output.flush();os.fsync(output.fileno())
        return super().lookup(request,timeout_seconds)


def worker(config_path,point):
    cfg=json.loads(Path(config_path).read_text())
    db=Database(Path(cfg['db']));repo=Repository(db);jobs=Jobs(db)
    budget=Budget(db,BudgetPolicy(cfg['budget']))
    gateway=ProviderGateway(budget,Path(cfg['artifacts']))
    provider=CountedRoutes(cfg['calls'])
    travel=TravelTime(gateway,jobs,provider)
    discovery=DiscoveryService(db,repo,jobs,None,allow_synthetic=True)
    service=Itineraries(db,repo,jobs,discovery,None,travel)
    if point=='route_receipt':
        original=travel.lookup
        def lookup(*args,**kwargs):
            actual=original(*args,**kwargs)
            class KillAfterReceipt:
                @property
                def stats(self):return actual.stats
                def __call__(self,*args,**kwargs):
                    value=actual(*args,**kwargs)
                    if value['basis']=='provider':os.kill(os.getpid(),signal.SIGKILL)
                    return value
            return KillAfterReceipt()
        travel.lookup=lookup
    job=jobs.claim('itinerary-recovery-'+point)
    assert job and job['id']==cfg['job_id']
    result=service.execute(job,JobContext(jobs,job,'itinerary-recovery-'+point))
    if point=='activation':os.kill(os.getpid(),signal.SIGKILL)
    jobs.finish(job['id'],job['fencing_token'],state=result.get('state','succeeded'),result=result.get('result',result))
    print(json.dumps({'job_id':job['id'],'fence':job['fencing_token'],'calls':provider.calls}))


def run(config,point):
    script='import sys\nfrom tests.test_itinerary_recovery import worker\nworker(sys.argv[1],sys.argv[2])\n'
    return subprocess.run([sys.executable,'-c',script,str(config),point],capture_output=True,text=True,timeout=30,
        env={**os.environ,'OPENAI_API_KEY':'test','APIFY_TOKEN':'','APIFY_API_TOKEN':''})


@pytest.mark.parametrize('point',['route_receipt','activation'])
def test_killed_process_reuses_route_receipts_and_activates_one_revision(planning,tmp_path,point):
    trip,_,selected,_=prepare(planning)
    # Hand dispatcher ownership to the child, without any active production job.
    planning.lifetime.portal.call(planning.app.state.dispatcher.stop)
    planning.app.state.jobs.accepting=True
    response=submit(planning,trip,selected)
    assert response.status_code==202,response.text
    receipt=response.json();config=tmp_path/'itinerary-recovery.json';calls=tmp_path/'route-calls.txt'
    config.write_text(json.dumps({'db':str(planning.settings.database_path),
        'budget':planning.app.state.budget.policy.config,'artifacts':str(tmp_path/'recovery-artifacts'),
        'calls':str(calls),'job_id':receipt['job_id']}))
    killed=run(config,point)
    assert killed.returncode==-signal.SIGKILL,killed.stderr
    before=calls.read_text().splitlines()
    with planning.app.state.db.connect() as con:
        saved=con.execute('SELECT active_revision_id FROM itineraries WHERE id=?',(receipt['itinerary_id'],)).fetchone()[0]
        assert bool(saved)==(point=='activation')
        con.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(receipt['job_id'],))
        con.execute("UPDATE dispatcher_leases SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    resumed=run(config,'resume')
    assert resumed.returncode==0,resumed.stderr
    assert json.loads(resumed.stdout)['fence']==2
    invoked=calls.read_text().splitlines()
    assert len(invoked)==len(set(invoked)),'A previously receipted route was called again'
    if point=='activation':assert invoked==before
    with planning.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM itinerary_revisions WHERE itinerary_id=?',(receipt['itinerary_id'],)).fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM usage_reservations WHERE operation='route_matrix'").fetchone()[0]==len(invoked)
        assert con.execute('SELECT state FROM jobs WHERE id=?',(receipt['job_id'],)).fetchone()[0] in ('succeeded','partial')
    result=planning.client.get(f"/api/v2/trips/{trip['id']}/itineraries/{receipt['itinerary_id']}")
    assert result.status_code==200 and result.json()['version']==1 and result.json()['items']
