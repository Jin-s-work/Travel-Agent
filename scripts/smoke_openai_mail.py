#!/usr/bin/env python3
"""Explicit, metered live smoke using synthetic mail only; no key or mail logging.
PYTHON_DOTENV_DISABLED=1 .venv/bin/python scripts/smoke_openai_mail.py --run
Reads OPENAI_API_KEY from process or the gitignored root .env; never writes it.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument('--run',action='store_true',help='Authorize real OpenAI requests against the small configured cap')
    args.add_argument('--report',type=Path)
    opts=args.parse_args()
    if not opts.run: args.error('--run is required; this is a billable live smoke')
    from dotenv import dotenv_values
    key=os.environ.get('OPENAI_API_KEY') or dotenv_values(ROOT/'.env',interpolate=False).get('OPENAI_API_KEY')
    if not key: print(json.dumps({'ok':False,'error':'OPENAI_KEY_MISSING'})); return 2
    os.environ['OPENAI_API_KEY']=key
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    os.environ['STORAGE_BACKEND']='local'
    os.environ['DATABASE_URL']=''
    from src.foundation.db import Database
    from src.foundation.repository import Repository,utcnow
    from src.reliability.budget import Budget,BudgetPolicy,CallContext
    from src.reliability.providers import ProviderGateway,metered_context
    from src.parser import parse_document_reservations
    from src.embedder import embed_texts
    report={'synthetic_only':True,'models':['gpt-5-mini','text-embedding-3-small'],'ok':False}
    with tempfile.TemporaryDirectory(prefix='travel-openai-smoke-') as temp:
        db=Database(Path(temp)/'smoke.sqlite3')
        with db.connect() as con:
            con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',('smoke','synthetic@example.invalid','fixture','smoke','member',utcnow(),utcnow()))
        trip=Repository(db).create_trip('smoke',{'title':'Synthetic live check','start_date':'2026-11-06','end_date':'2026-11-09'})
        budget=Budget(db,BudgetPolicy.from_file(ROOT/'deploy/render-supabase/pricing-openai.json'))
        gateway=ProviderGateway(budget,Path(temp)/'receipts')
        ctx=CallContext('smoke',trip['id'],trip['id'])
        started=time.monotonic()
        try:
            mail='SYNTHETIC TEST ONLY. Tokyo walking tour confirmed. Provider: Synthetic Tokyo Tour. Reservation: TEST-ONLY-42. November 7, 2026 at 15:00 Japan local time (Asia/Tokyo). Ends 17:00 same day. Two adults. Meeting point: Shinjuku Station. Free cancellation until November 5, 2026. No real booking.'
            with metered_context(gateway,ctx,'live-extract'):
                records=parse_document_reservations(mail,model='gpt-5-mini')
            report['extracted_records']=len(records)
            report['correct_date_time']=any(r.get('date')=='2026-11-07' and r.get('time')=='15:00' for r in records)
            with metered_context(gateway,ctx,'live-embed'):
                vectors=embed_texts(['Synthetic Tokyo tour, 2026-11-07 15:00 Asia/Tokyo.'])
            report['embedding_dimensions']=[len(v) for v in vectors]
            report['ok']=report['correct_date_time'] and report['embedding_dimensions']==[1536]
        except Exception as exc:
            report['error_code']=getattr(exc,'code',type(exc).__name__)
        report['elapsed_seconds']=round(time.monotonic()-started,2)
        with db.connect() as con:
            report['usage']=[dict(row) for row in con.execute('SELECT provider,sku,state,estimated_cost_micros,actual_cost_micros FROM usage_reservations ORDER BY created_at').fetchall()]
    output=json.dumps(report,ensure_ascii=False,indent=2)
    if opts.report: opts.report.write_text(output+'\n')
    print(output)
    return 0 if report['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
