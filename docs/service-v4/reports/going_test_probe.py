import json, threading, sys, traceback, os, tempfile
from pathlib import Path
import pytest

ROOT=Path(os.environ.get('GOING_AUDIT_OUTPUT_DIR') or tempfile.mkdtemp(prefix='going-test-probe-'))
ROOT.mkdir(parents=True,exist_ok=True)
records=[]

def pytest_collection_modifyitems(config, items):
    keep=[];drop=[]
    for item in items:
        target=keep if item.nodeid.split('::',1)[0] <= 'tests/test_foundation_api.py' else drop
        target.append(item)
    items[:]=keep
    config.hook.pytest_deselected(items=drop)

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome=yield
    report=outcome.get_result()
    if report.when!='call': return
    row={'nodeid':item.nodeid,'outcome':report.outcome,'seconds':round(report.duration,4)}
    if call.excinfo:
        for entry in call.excinfo.traceback:
            value=entry.frame.f_locals.get('value')
            if isinstance(value,dict) and 'job_id' in value:
                row['job']={k:value.get(k) for k in ('state','stage','attempt','error_code','retryable','done_count','total_count','created_at','started_at','updated_at','finished_at')}
        frames=sys._current_frames()
        row['threads']=[{'name':t.name,'stack':[{'file':f.filename,'line':f.lineno,'function':f.name} for f in traceback.extract_stack(frames[t.ident])[-16:]]} for t in threading.enumerate() if t.ident in frames]
    records.append(row)
    (ROOT/'foundation-prefix-diagnostics.json').write_text(json.dumps(records,indent=2,ensure_ascii=False)+'\n')
