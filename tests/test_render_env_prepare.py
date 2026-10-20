from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path
from urllib.parse import urlsplit,unquote,parse_qs
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


def test_handoff_requires_bundled_supabase_ca():
    data=config();values=module.render_values(data)
    query=parse_qs(urlsplit(values['DATABASE_URL']).query)
    assert query['sslmode']==['verify-full']
    assert query['sslrootcert']==[module.DB_CA_PATH]
    data['DATABASE_URL']=values['DATABASE_URL'].replace(module.DB_CA_PATH,'/etc/ssl/certs/ca-certificates.crt')
    with pytest.raises(ValueError,match='DATABASE_URL'):module.render_values(data)

def test_handoff_requires_invitation_email_without_exporting_it():
    data=config();data['DEPLOY_ADMIN_EMAIL']=''
    with pytest.raises(ValueError,match='DEPLOY_ADMIN_EMAIL'):module.render_values(data)

def test_bundled_certificate_identity_expiry_and_image_inclusion():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from datetime import datetime,timezone
    root=Path(__file__).parents[1]
    rel='deploy/render-supabase/prod-ca-2021.crt'
    pem=(root/rel).read_bytes()
    cert=x509.load_pem_x509_certificate(pem)
    assert cert.fingerprint(hashes.SHA256()).hex()=='807025ad50d4ed219d2c9c7d299c004f824eb00cf7f65afef607d07b72e6cafa'
    assert cert.not_valid_before_utc < datetime.now(timezone.utc) < cert.not_valid_after_utc
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert b'PRIVATE KEY' not in pem
    assert f'!{rel}' in (root/'.dockerignore').read_text().splitlines()
    assert f'COPY --chown=user:user {rel} ./{rel}' in (root/'Dockerfile').read_text()


def test_minimal_six_value_input_generates_full_free_runtime():
    full=config()
    minimal={key:full[key] for key in ['OIDC_CLIENT_ID','OIDC_CLIENT_SECRET','SUPABASE_DB_PASSWORD','SUPABASE_SECRET_KEY','DEPLOY_ADMIN_EMAIL','SESSION_SECRET']}
    values=module.render_values(minimal)
    assert values['APP_ENV']=='production'
    assert values['ZERO_SPEND']=='1'
    assert values['SUPABASE_URL']=='https://whudlguhvmrbxudybnme.supabase.co'
    assert values['PUBLIC_BASE_URL']=='https://travel-inbox-rag.onrender.com'
    assert values['WEB_CONCURRENCY']=='1'
    assert values['OIDC_CLIENT_SECRET']==minimal['OIDC_CLIENT_SECRET']
    assert 'SUPABASE_DB_PASSWORD' not in values
    assert 'DEPLOY_ADMIN_EMAIL' not in values


def test_openai_profile_is_explicit_and_keeps_other_paid_providers_off():
    values=module.render_values(config(), 'openai')
    assert values['OPENAI_API_KEY']=='fixture-paid'
    assert values['ZERO_SPEND']=='0' and values['MAIL_ANALYSIS_MODE']=='ai'
    assert values['EXTRACTION_MODEL']==values['ANSWER_MODEL']==values['AGENT_MODEL']=='gpt-5-mini'
    assert values['EMBEDDING_MODEL']=='text-embedding-3-small'
    assert values['PRICING_CONFIG'].endswith('pricing-openai.json')
    assert values['TAVILY_API_KEY']==values['APIFY_TOKEN']==''

def test_openai_profile_requires_key_and_free_remains_default():
    data=config(); data['OPENAI_API_KEY']=''
    with pytest.raises(ValueError,match='OPENAI_API_KEY'): module.render_values(data,'openai')
    assert module.render_values(data)['ZERO_SPEND']=='1'

def test_openai_policy_caps_are_small_and_reservation_can_fit():
    import json
    from src.reliability.budget import BudgetPolicy
    policy=BudgetPolicy.from_file(Path(__file__).parents[1]/'deploy/render-supabase/pricing-openai.json')
    assert policy.valid and not policy.config['halted']
    assert policy.config['limits']['USD']['global_monthly']==3_000_000
    assert policy.config['limits']['USD']['global_daily']==500_000
    assert set(policy.config['prices'])=={'openai/gpt-5-mini','openai/text-embedding-3-small'}


def test_apify_credentials_are_explicit_without_enabling_collection():
    data=config();data['APIFY_TOKEN']='fixture-apify'
    assert module.render_values(data,'openai')['APIFY_TOKEN']==''
    values=module.render_values(data,'openai',with_apify=True)
    assert values['APIFY_TOKEN']=='fixture-apify'
    assert values['PRICING_CONFIG'].endswith('pricing-openai.json')
    assert 'REVIEW_PRODUCTION_ENABLED' not in values
    with pytest.raises(ValueError):module.render_values(data,'free',with_apify=True)
    data['APIFY_TOKEN']=''
    with pytest.raises(ValueError,match='APIFY_TOKEN'):module.render_values(data,'openai',with_apify=True)
