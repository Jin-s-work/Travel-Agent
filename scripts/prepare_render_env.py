#!/usr/bin/env python3
"""Build a private Render import file. Never print secret values or contact a provider."""
import argparse
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('SUPABASE_SECRET_KEY', 'OIDC_CLIENT_ID', 'OIDC_CLIENT_SECRET', 'SESSION_SECRET')
LOCAL_ONLY = {'SUPABASE_DB_PASSWORD', 'DEPLOY_ADMIN_EMAIL', 'RENDER_API_KEY'}


def render_values(source):
    values = {key: value or '' for key, value in source.items()}
    missing = [key for key in REQUIRED if not values.get(key)]
    if not values.get('DATABASE_URL'):
        if not values.get('SUPABASE_DB_PASSWORD'):
            missing.append('SUPABASE_DB_PASSWORD (또는 DATABASE_URL)')
        else:
            password = quote(values['SUPABASE_DB_PASSWORD'], safe='')
            values['DATABASE_URL'] = ('postgresql://postgres.whudlguhvmrbxudybnme:' + password +
                '@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
                '?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt')
    if missing:
        raise ValueError('입력 필요: ' + ', '.join(missing))
    if len(values['SESSION_SECRET']) < 32:
        raise ValueError('SESSION_SECRET: 최소 32자 필요')
    try:
        db = urlsplit(values['DATABASE_URL'])
        valid = db.scheme in {'postgres','postgresql'} and db.hostname == 'aws-0-ap-northeast-1.pooler.supabase.com' and db.port == 5432 and db.username == 'postgres.whudlguhvmrbxudybnme' and bool(db.password) and parse_qs(db.query).get('sslmode') == ['verify-full']
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('DATABASE_URL: hii Session pooler 5432/TLS 설정을 확인하세요')
    for key, expected in {'APP_ENV':'production','STORAGE_BACKEND':'supabase','ZERO_SPEND':'1','SEED_ON_EMPTY':'0','WEB_CONCURRENCY':'1','PUBLIC_BASE_URL':'https://travel-inbox-rag.onrender.com','SUPABASE_URL':'https://whudlguhvmrbxudybnme.supabase.co'}.items():
        if values.get(key) != expected:
            raise ValueError(key + ': 기존 무료 비공개 배포 설정을 유지하세요')
    # Existing paid credentials on Render must be overridden when importing this profile.
    values.update(OPENAI_API_KEY='', TAVILY_API_KEY='', APIFY_TOKEN='')
    return {key:value for key,value in values.items() if key not in LOCAL_ONLY}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='값을 노출하거나 파일을 생성하지 않고 검사')
    args = parser.parse_args()
    path = ROOT / 'deploy/render-supabase/.env'
    if not path.exists():
        print('입력 필요: deploy/render-supabase/.env'); return 2
    try:
        values = render_values(dotenv_values(path, interpolate=False))
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
