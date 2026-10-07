from datetime import datetime, timezone
import json
from src.foundation.repository import DomainError


def read(con):
    row=con.execute('SELECT * FROM operations_controls WHERE singleton=1').fetchone()
    return {**dict(row),'disabled_providers':json.loads(row['disabled_providers_json'])}


def external_guard(db,provider):
    with db.connect() as con:
        value=read(con)
    if value['mode']!='normal' or not value['external_enabled'] or provider in value['disabled_providers']:
        raise DomainError('EXTERNAL_CALLS_PAUSED','운영자가 새 외부 호출을 중지했습니다. 저장된 자료는 계속 확인할 수 있습니다.',503)


def update(db,*,mode=None,external_enabled=None,disabled_providers=None,reason_code='OPERATOR_CHANGE'):
    if mode is not None and mode not in {'normal','read_only','maintenance'}:raise ValueError('Invalid mode')
    if not reason_code or len(reason_code)>80 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in reason_code):raise ValueError('Use a non-sensitive reason code')
    if disabled_providers is not None and (len(disabled_providers)>20 or any(not isinstance(p,str) or not p.isidentifier() for p in disabled_providers)):raise ValueError('Invalid provider names')
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE');old=read(con)
        current={'mode':mode if mode is not None else old['mode'], 'external_enabled':int(external_enabled if external_enabled is not None else old['external_enabled']),
                 'disabled_providers':disabled_providers if disabled_providers is not None else old['disabled_providers']}
        stamp=datetime.now(timezone.utc).isoformat()
        con.execute('UPDATE operations_controls SET mode=?,external_enabled=?,disabled_providers_json=?,updated_at=?,reason_code=? WHERE singleton=1',
                    (current['mode'],current['external_enabled'],json.dumps(current['disabled_providers']),stamp,reason_code))
        con.execute('INSERT INTO operations_audit(action,created_at,details_json) VALUES(?,?,?)',('controls_changed',stamp,json.dumps({**current,'reason_code':reason_code})))
    return current
