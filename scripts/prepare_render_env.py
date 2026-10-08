#!/usr/bin/env python3
"""Build a private Render import file. Never print secret values or contact a provider."""
import argparse
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DB_CA_PATH = '/app/deploy/render-supabase/prod-ca-2021.crt'
REQUIRED = ('SUPABASE_SECRET_KEY', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'SESSION_SECRET', 'DEPLOY_ADMIN_EMAIL')
LOCAL_ONLY = {'SUPABASE_DB_PASSWORD', 'DEPLOY_ADMIN_EMAIL', 'RENDER_API_KEY'}

# Fixed settings for the existing free service. The private input file only
# contains the five account inputs and the already-generated session secret.
RUNTIME_DEFAULTS = {
    'STORAGE_BACKEND': 'supabase',
    'SUPABASE_URL': 'https://whudlguhvmrbxudybnme.supabase.co',
    'SUPABASE_STORAGE_BUCKET': 'travel-private',
    'APP_ENV': 'production',
    'PUBLIC_BASE_URL': 'https://travel-inbox-rag.onrender.com',
    'OIDC_SERVER_METADATA_URL': 'https://accounts.google.com/.well-known/openid-configuration',
    'PYTHON_DOTENV_DISABLED': '1', 'SEED_ON_EMPTY': '0', 'WEB_CONCURRENCY': '1',
    'DATABASE_PATH': '/tmp/travel-cache/sql/unused.sqlite3',
    'DOCUMENTS_DIR': '/tmp/travel-cache/documents',
    'VECTORS_DIR': '/tmp/travel-cache/vectors',
    'EMAILS_DIR': '/tmp/travel-cache/legacy-emails',
    'CHROMA_DIR': '/tmp/travel-cache/legacy-chroma',
    'JOB_POLL_SECONDS': '3', 'JOB_LEASE_SECONDS': '90',
    'JOB_HEARTBEAT_SECONDS': '20', 'JOB_SHUTDOWN_SECONDS': '5',
    'JOB_MAX_ATTEMPTS': '3', 'JOB_DEADLINE_SECONDS': '900',
    'BACKUP_ENABLED': '0', 'ZERO_SPEND': '1',
    'PRICING_CONFIG': '/app/deploy/render-supabase/pricing-zero.json',
    'OPENAI_API_KEY': '', 'TAVILY_API_KEY': '', 'APIFY_TOKEN': '',
}


def render_values(source, profile="free", *, with_apify=False):
    if profile not in {"free", "openai"}:
        raise ValueError("알 수 없는 배포 프로필")
    values = {**RUNTIME_DEFAULTS, **{key: value or '' for key, value in source.items()}}
    if profile == 'openai':
        values.update(ZERO_SPEND='0', MAIL_ANALYSIS_MODE='ai',
            PRICING_CONFIG='/app/deploy/render-supabase/pricing-openai.json',
            EXTRACTION_MODEL='gpt-5-mini', ANSWER_MODEL='gpt-5-mini', AGENT_MODEL='gpt-5-mini',
            EMBEDDING_MODEL='text-embedding-3-small')
        if not values.get('OPENAI_API_KEY'):
            raise ValueError('입력 필요: OPENAI_API_KEY (openai 프로필)')
    missing = [key for key in REQUIRED if not values.get(key)]
    if not values.get('DATABASE_URL'):
        if not values.get('SUPABASE_DB_PASSWORD'):
            missing.append('SUPABASE_DB_PASSWORD (또는 DATABASE_URL)')
        else:
            password = quote(values['SUPABASE_DB_PASSWORD'], safe='')
            values['DATABASE_URL'] = ('postgresql://postgres.whudlguhvmrbxudybnme:' + password +
                '@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
                '?sslmode=verify-full&sslrootcert=' + DB_CA_PATH)
    if missing:
        raise ValueError('입력 필요: ' + ', '.join(missing))
    if len(values['SESSION_SECRET']) < 32:
        raise ValueError('SESSION_SECRET: 최소 32자 필요')
    try:
        db = urlsplit(values['DATABASE_URL'])
        valid = db.scheme in {'postgres','postgresql'} and db.hostname == 'aws-0-ap-northeast-1.pooler.supabase.com' and db.port == 5432 and db.username == 'postgres.whudlguhvmrbxudybnme' and bool(db.password) and parse_qs(db.query).get('sslmode') == ['verify-full'] and parse_qs(db.query).get('sslrootcert') == [DB_CA_PATH]
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('DATABASE_URL: hii Session pooler 5432/TLS 설정을 확인하세요')
    for key, expected in {'APP_ENV':'production','STORAGE_BACKEND':'supabase','ZERO_SPEND':'0' if profile=='openai' else '1','SEED_ON_EMPTY':'0','WEB_CONCURRENCY':'1','PUBLIC_BASE_URL':'https://travel-inbox-rag.onrender.com','SUPABASE_URL':'https://whudlguhvmrbxudybnme.supabase.co'}.items():
        if values.get(key) != expected:
            raise ValueError(key + ': 기존 무료 비공개 배포 설정을 유지하세요')
    # Existing paid credentials on Render must be overridden when importing this profile.
    values.update(TAVILY_API_KEY='')
    if with_apify:
        if profile != 'openai':
            raise ValueError('Apify 연결은 openai 프로필과 함께 준비하세요. free 프로필은 외부 호출 OFF입니다.')
        if not values.get('APIFY_TOKEN'):
            raise ValueError('입력 필요: APIFY_TOKEN')
        # Credential preparation does not enable research/production or change
        # pricing. Those remain separately gated by reviewed server settings.
    else:
        values['APIFY_TOKEN']=''
    if profile == 'free':
        values.update(OPENAI_API_KEY='', MAIL_ANALYSIS_MODE='local',
            PRICING_CONFIG=RUNTIME_DEFAULTS['PRICING_CONFIG'])
    return {key:value for key,value in values.items() if key not in LOCAL_ONLY}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='값을 노출하거나 파일을 생성하지 않고 검사')
    parser.add_argument('--profile', choices=['free','openai'], default='free', help='free: 외부 과금 OFF / openai: 월 3 USD 상한으로 메일 AI 활성화')
    parser.add_argument('--with-apify', action='store_true', help='입력한 Apify 토큰도 전달. 수집·예산·운영 기능은 별도 검토 후 활성화')
    args = parser.parse_args()
    path = ROOT / 'deploy/render-supabase/.env'
    if not path.exists():
        print('입력 필요: deploy/render-supabase/.env'); return 2
    try:
        values = render_values(dotenv_values(path, interpolate=False), args.profile, with_apify=args.with_apify)
    except ValueError as exc:
        print(str(exc)); return 2
    if args.check:
        print('설정 형식 확인 완료. 외부 연결/로그인 검증은 별도입니다.'); return 0
    target = path.with_name('.env.render')
    # dotenv double-quote escaping preserves literal passwords without shell evaluation.
    def escaped(value):
        return value.replace('\\','\\\\').replace('"','\\"').replace('\r','\\r').replace('\n','\\n')
    content = '\n'.join(key + '="' + escaped(value) + '"' for key,value in values.items()) + '\n'
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(),0o600); stream.write(content)
    print('생성 완료: deploy/render-supabase/.env.render (권한 600, Git 제외). Render Environment의 Import .env에 사용하세요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
