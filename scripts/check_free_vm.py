"""Zero-paid-call deployment profile check, isolated local Docker only."""
import argparse,json,os,subprocess,tempfile,time,uuid
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--image',default='travel-agent:beta-local');p.add_argument('--report',type=Path,required=True);args=p.parse_args()
project='travel-free-check-'+uuid.uuid4().hex[:8]
report={'scope':'local_docker_only','cloud_resources_created':0,'paid_provider_calls':0}
(ROOT/'reports').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='.travel-free-check-',dir=ROOT/'reports') as directory:
    root=Path(directory);data=root/'data';data.mkdir();data.chmod(0o777) # temporary Mac bind fixture, never a host permission recommendation
    secrets=root/'fixture.env';secrets.write_text('\n'.join([
        'TRAVEL_ENV_FILE='+str(secrets),'HOST_DATA_DIR='+str(data),'PUBLIC_HOST=beta.example.test','PUBLIC_BASE_URL=https://beta.example.test',
        'SESSION_SECRET=synthetic-only-secret-more-than-thirty-two-characters',
        'OIDC_CLIENT_ID=synthetic-client','OIDC_CLIENT_SECRET=synthetic-secret',
        'OIDC_SERVER_METADATA_URL=https://fixture.example.test/.well-known/openid-configuration',
        'OPENAI_API_KEY=must-be-overridden','BACKUP_ENABLED=0'])+'\n');secrets.chmod(0o600)
    override=root/'test.yaml';override.write_text('services:\n  app:\n    image: '+args.image+'\n    restart: "no"\n    ports: ["127.0.0.1::7860"]\n')
    base=['docker','compose','--project-name',project,'--env-file',str(secrets),'-f',str(ROOT/'deploy/free-vm/compose.yaml'),'-f',str(override)]
    def run(*command):
        try:return subprocess.check_output([*base,*command],text=True,stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:raise RuntimeError(exc.stderr[-3000:]) from None
    try:
        config=json.loads(run('config','--format','json'))['services']['app']
        assert config['read_only'] and config['user']=='1000:1000'
        assert config['environment']['OPENAI_API_KEY']==''
        run('up','-d','--no-build','app')
        address=run('port','app','7860').strip();url='http://'+address
        for _ in range(60):
            try:
                if httpx.get(url+'/health/ready',timeout=2).status_code==200:break
            except httpx.TransportError:pass
            time.sleep(.25)
        else:raise AssertionError('Free profile did not become ready')
        assert httpx.get(url+'/api/v2/trips').status_code==401
        code="""import json,os
from src.foundation.db import Database
from src.foundation.repository import Repository,DomainError
from src.reliability.budget import Budget,BudgetPolicy,CallContext
from src.reliability.providers import ProviderGateway,ProviderResult
from src.foundation.auth import Auth
from src.foundation.settings import Settings
s=Settings();db=Database(s.database_path);auth=Auth(db,s)
token=auth.complete_identity({'iss':'https://fixture.example.test','sub':'free-check','email':'free@example.test','email_verified':True},auth.invite('free@example.test'))
with db.connect() as con:user=con.execute("SELECT id FROM users WHERE email='free@example.test'").fetchone()[0]
repo=Repository(db);trip=repo.create_trip(user,{'title':'Synthetic free persistence','start_date':'2026-11-01','end_date':'2026-11-02'})
repo.create_booking(user,trip['id'],{'kind':'tour','provider':'Synthetic manual','date':'2026-11-01','time':'13:00','status':'user_confirmed'})
gateway=ProviderGateway(Budget(db,BudgetPolicy.from_file(s.pricing_config)),s.artifacts_dir)
def forbidden():raise AssertionError('A paid callback ran')
try:gateway.run(CallContext(user,trip['id'],trip['id']),'extract','free-policy',{'input_tokens':1,'output_tokens':1},forbidden,provider='openai',sku='gpt-5-mini',request_hash='synthetic')
except DomainError as exc:assert exc.code=='GLOBAL_BUDGET_STOPPED'
else:raise AssertionError('Expected cost rejection')
assert os.environ['OPENAI_API_KEY']==''
print(json.dumps({'trip_id':trip['id'],'uid':os.getuid(),'zero_budget_blocks_call':True}))
"""
        result=json.loads(run('exec','-T','app','python','-c',code));assert result['uid']==1000
        run('restart','app')
        url='http://'+run('port','app','7860').strip()
        for _ in range(60):
            try:
                if httpx.get(url+'/health/ready',timeout=2).status_code==200:break
            except httpx.TransportError:pass
            time.sleep(.25)
        else:raise AssertionError('Restart readiness failed: '+run('logs','--tail','10','app'))
        code="import sqlite3,json;c=sqlite3.connect('/var/data/sql/service.sqlite3');print(json.dumps({'trips':c.execute('SELECT count(*) FROM trips WHERE deleted_at IS NULL').fetchone()[0],'bookings':c.execute('SELECT count(*) FROM bookings WHERE deleted_at IS NULL').fetchone()[0],'reservations':c.execute('SELECT count(*) FROM usage_reservations').fetchone()[0]}))"
        saved=json.loads(run('exec','-T','app','python','-c',code));assert saved=={'trips':1,'bookings':1,'reservations':0},saved
        report.update(read_only_image=True,nonroot_uid=1000,api_keys_forced_empty=True,zero_budget_rejected=True,restart_preserved=saved,passed=True)
    finally:
        run('down','--remove-orphans')
args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
