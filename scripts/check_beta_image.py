"""Exercise the actual production entrypoint on an isolated disposable volume."""
import argparse,json,subprocess,time
from pathlib import Path
import httpx
p=argparse.ArgumentParser();p.add_argument('--image',default='travel-agent:beta-local');p.add_argument('--report',type=Path,required=True);a=p.parse_args()
name='travel-beta-startup';volume='travel-beta-startup-data';url='http://127.0.0.1:8782'
def cmd(*args,check=True):return subprocess.run(['docker',*args],capture_output=True,text=True,check=check)
# Deliberately fake identity configuration. No external identity or AI request.
env=['-e','PUBLIC_BASE_URL=https://beta.example.test','-e','OIDC_CLIENT_ID=fixture-client','-e','OIDC_CLIENT_SECRET=fixture-secret','-e','OIDC_SERVER_METADATA_URL=https://fixture.example.test/.well-known/openid-configuration','-e','SESSION_SECRET=synthetic-session-secret-more-than-thirty-two-characters','-e','PYTHON_DOTENV_DISABLED=1','-e','OPENAI_API_KEY=test-placeholder']
result={'synthetic':True,'external_calls':0}
try:
    cmd('run','-d','--name',name,'-p','127.0.0.1:8782:7860','-v',volume+':/var/data',*env,a.image)
    for _ in range(40):
        try:
            if httpx.get(url+'/health/ready').status_code==200:break
        except httpx.TransportError:pass
        time.sleep(.25)
    else:raise AssertionError('Production startup failed')
    result['readiness']=httpx.get(url+'/health/ready').json()
    assert httpx.get(url+'/api/v2/trips').status_code==401
    result['unauthenticated_personal_api']=401
    info=json.loads(cmd('exec',name,'python','-c',"import json,os;from pathlib import Path;print(json.dumps({'uid':os.getuid(),'private_write':all(os.access(p,os.W_OK) for p in ['/var/data/sql','/var/data/documents','/var/data/vectors']),'no_seed':not Path('/app/seed').exists(),'no_env':not Path('/app/.env').exists(),'no_tests':not Path('/app/tests').exists()}))").stdout)
    assert info['uid']==1000 and all(v for k,v in info.items() if k!='uid');result['image']=info
    second=cmd('exec',name,'python','-m','src.operations.launch',check=False)
    assert second.returncode==78 and 'INSTANCE_LOCK' in second.stderr;result['second_process_blocked']=True
    cmd('exec',name,'python','-c',"import sqlite3;c=sqlite3.connect('/var/data/sql/service.sqlite3');c.execute('PRAGMA user_version=999');c.close()")
    assert httpx.get(url+'/health/ready').status_code==503
    cmd('restart',name)
    for _ in range(40):
        state=json.loads(cmd('inspect',name,'--format','{{json .State}}').stdout)
        if not state['Running']:break
        time.sleep(.25)
    assert state['ExitCode']==78;result['future_migration_exit_code']=78
    logs=cmd('logs',name).stderr
    assert 'fixture-secret' not in logs and 'test-placeholder' not in logs
    result['sensitive_config_not_logged']=True
    # Image default must refuse to start with no identity configuration.
    noauth=cmd('run','--rm','-v',volume+':/var/data',a.image,check=False)
    assert noauth.returncode==78;result['missing_identity_exit_code']=78
    result['passed']=True
finally:
    cmd('rm','-f',name,check=False);cmd('volume','rm',volume,check=False)
a.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
