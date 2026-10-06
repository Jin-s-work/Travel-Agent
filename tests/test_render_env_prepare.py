from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
from urllib.parse import urlsplit,unquote
import pytest

spec=spec_from_file_location('prepare_render_env',Path(__file__).parents[1]/'scripts/prepare_render_env.py')
module=module_from_spec(spec);spec.loader.exec_module(module)

def config():
    return dict(SUPABASE_DB_PASSWORD='fixture:@ /#한글',SUPABASE_SECRET_KEY='fixture-storage',OIDC_CLIENT_ID='fixture-id',OIDC_CLIENT_SECRET='fixture-oauth',SESSION_SECRET='x'*48,APP_ENV='production',STORAGE_BACKEND='supabase',ZERO_SPEND='1',SEED_ON_EMPTY='0',WEB_CONCURRENCY='1',PUBLIC_BASE_URL='https://travel-inbox-rag.onrender.com',SUPABASE_URL='https://whudlguhvmrbxudybnme.supabase.co',DEPLOY_ADMIN_EMAIL='fixture@example.test',RENDER_API_KEY='fixture-render',OPENAI_API_KEY='fixture-paid')

def test_handoff_encodes_password_and_excludes_local_credentials():
    data=config();values=module.render_values(data)
    assert unquote(urlsplit(values['DATABASE_URL']).password)==data['SUPABASE_DB_PASSWORD']
    assert not {'SUPABASE_DB_PASSWORD','DEPLOY_ADMIN_EMAIL','RENDER_API_KEY'} & values.keys()
    assert all(values[key]=='' for key in ['OPENAI_API_KEY','TAVILY_API_KEY','APIFY_TOKEN'])

def test_handoff_errors_do_not_contain_values():
    data=config();data['OIDC_CLIENT_SECRET']='';data['SUPABASE_DB_PASSWORD']=''
    with pytest.raises(ValueError) as error:module.render_values(data)
    assert 'OIDC_CLIENT_SECRET' in str(error.value)
    assert 'fixture-storage' not in str(error.value)

@pytest.mark.parametrize('value',['postgresql://postgres.fixture:secret@other.supabase.com:5432/postgres?sslmode=verify-full','postgresql://postgres.whudlguhvmrbxudybnme:secret@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=verify-full'])
def test_handoff_rejects_wrong_project_or_transaction_pooler(value):
    data=config();data['DATABASE_URL']=value
    with pytest.raises(ValueError,match='DATABASE_URL'):module.render_values(data)

def test_handoff_rejects_production_bypass():
    data=config();data['APP_ENV']='development'
    with pytest.raises(ValueError,match='APP_ENV'):module.render_values(data)
