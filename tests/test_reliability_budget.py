"""Synthetic providers only: no network, production prices, or personal data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from src.foundation.db import Database
from src.foundation.repository import Repository, DomainError, utcnow
from src.reliability.budget import Budget, BudgetPolicy, CallContext, BUDGET_SCHEMA
from src.reliability.providers import (ProviderGateway, ProviderResult, DefinitelyNotSent,
    ProviderRejected, metered_context, chat_create)


@pytest.fixture
def setup(tmp_path):
    db=Database(tmp_path/'data.sqlite3')
    with db.connect() as con:
        con.executescript(BUDGET_SCHEMA)
        for user in ('A','B','admin'):
            con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,role,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (user,user+'@example.invalid','fake',user,'admin' if user=='admin' else 'member',utcnow(),utcnow()))
    repo=Repository(db)
    trip=repo.create_trip('A',{'title':'Synthetic','start_date':'2026-11-01','end_date':'2026-11-03'})
    ctx=CallContext('A',trip['id'],trip['id'])
    budget=Budget(db,BudgetPolicy.for_tests(rate='1',limit=10))
    return db,repo,ctx,budget,ProviderGateway(budget,tmp_path/'receipts')


def reserve(budget,ctx,key='stage',units=6,attempt=1,request_hash='hash'):
    return budget.reserve(ctx,provider='fake',sku='extract',operation='extract',call_key=key,
        estimated_units={'calls':units},request_hash=request_hash,attempt=attempt)


def run(gateway,ctx,fn,key='stage',**kwargs):
    return gateway.run(ctx,'extract',key,{'calls':6},fn,provider='fake',request_hash=key,**kwargs)


def test_unconfigured_policy_fails_closed_without_row(setup):
    db,_,ctx,_,_=setup
    with pytest.raises(DomainError,match='설정') as exc:
        reserve(Budget(db),ctx)
    assert exc.value.code=='BUDGET_NOT_CONFIGURED'
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0]==0


def test_last_budget_atomic_threads(setup):
    _,_,ctx,budget,_=setup
    def worker(key):
        try:
            return reserve(budget,ctx,key)['state']
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(worker,('a','b')))==['BUDGET_EXHAUSTED','reserved']


def test_last_budget_atomic_independent_processes(setup):
    db,_,ctx,_,_=setup
    script='''
import sys
from src.foundation.db import Database
from src.foundation.repository import DomainError
from src.reliability.budget import Budget,BudgetPolicy,CallContext
b=Budget(Database(sys.argv[1]),BudgetPolicy.for_tests(rate='1',limit=10))
try:
 b.reserve(CallContext('A',sys.argv[2],sys.argv[2]),provider='fake',sku='extract',operation='extract',call_key=sys.argv[3],estimated_units={'calls':6},request_hash='h')
 print('allowed')
except DomainError as e: print(e.code)
'''
    procs=[subprocess.Popen([sys.executable,'-c',script,str(db.path),ctx.trip_id,key],stdout=subprocess.PIPE,text=True) for key in ('a','b')]
    results=[p.communicate(timeout=15)[0].strip() for p in procs]
    assert sorted(results)==['BUDGET_EXHAUSTED','allowed']
    assert all(p.returncode==0 for p in procs)


def test_idempotent_reservation_and_conflicting_hash(setup):
    _,_,ctx,budget,_=setup
    first=reserve(budget,ctx)
    assert reserve(budget,ctx)['call_id']==first['call_id']
    with pytest.raises(DomainError) as error:
        reserve(budget,ctx,request_hash='other')
    assert error.value.code=='IDEMPOTENCY_CONFLICT'


def test_new_explicit_job_has_new_call_but_recovery_reuses_old(setup):
    _,_,ctx,budget,_=setup
    a=CallContext('A',ctx.trip_id,ctx.trip_id,job_id='job_one')
    b=CallContext('A',ctx.trip_id,ctx.trip_id,job_id='job_two')
    one=reserve(budget,a,units=2)
    assert reserve(budget,a,units=2)['call_id']==one['call_id']
    assert reserve(budget,b,units=2)['call_id']!=one['call_id']


def test_replay_succeeds_after_policy_removed_without_new_calls(setup):
    _,_,ctx,budget,gateway=setup
    assert run(gateway,ctx,lambda:ProviderResult('existing',{'calls':2}))=='existing'
    budget.policy=BudgetPolicy()
    assert run(gateway,ctx,lambda:pytest.fail('must not re-call'))=='existing'


def test_success_response_replay_and_settle_once(setup):
    db,_,ctx,budget,gateway=setup
    calls=[]
    fn=lambda:(calls.append(1) or ProviderResult({'facts':['fake']},{'calls':2}))
    assert run(gateway,ctx,fn)=={'facts':['fake']}
    assert run(ProviderGateway(budget,gateway.result_dir),ctx,fn)=={'facts':['fake']}
    assert calls==[1]
    with db.connect() as con:
        row=con.execute('SELECT * FROM usage_reservations').fetchone()
        assert row['actual_cost_micros']==2 and row['state']=='settled'
        assert con.execute("SELECT COUNT(*) FROM usage_ledger WHERE entry_kind='settled'").fetchone()[0]==1


def test_timeout_keeps_budget_no_blind_retry_and_no_sensitive_error(setup):
    db,_,ctx,budget,gateway=setup
    calls=[]
    def fail():
        calls.append(1)
        raise TimeoutError('mail private@example.invalid credential=secret')
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,fail)
    assert 'secret' not in str(error.value)
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,fail)
    assert error.value.code=='CALL_OUTCOME_UNKNOWN' and calls==[1]
    with pytest.raises(DomainError) as error:
        reserve(budget,ctx,'next')
    assert error.value.code=='BUDGET_EXHAUSTED'
    with db.connect() as con:
        row=dict(con.execute('SELECT * FROM usage_reservations').fetchone())
        assert row['state']=='unknown' and row['estimated_cost_micros']==6
        assert 'secret' not in json.dumps(row)
    with pytest.raises(DomainError):
        budget.release_unsent(row['call_id'])


def test_actual_overestimate_records_debit_and_stops_followups(setup):
    db,_,ctx,budget,gateway=setup
    assert run(gateway,ctx,lambda:ProviderResult('ok',{'calls':7}))=='ok'
    with pytest.raises(DomainError) as error:
        reserve(budget,ctx,'next',units=1)
    assert error.value.code=='GLOBAL_BUDGET_STOPPED'
    with db.connect() as con:
        assert con.execute("SELECT amount_micros FROM usage_ledger WHERE entry_kind='estimate_exceeded'").fetchone()[0]==1
        call_id=con.execute('SELECT call_id FROM usage_reservations').fetchone()[0]
    with pytest.raises(DomainError):
        budget.resume_after_review(call_id,actor_id='A',reason_code='PRICE_REVIEWED')
    budget.resume_after_review(call_id,actor_id='admin',reason_code='PRICE_REVIEWED')
    assert reserve(budget,ctx,'afterreview',units=1)['state']=='reserved'
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM usage_ledger WHERE entry_kind LIKE 'control_resumed:%' AND actor_id='admin'").fetchone()[0]==1


def test_pending_usage_receipt_replayed_and_operator_reconciliation_audited(setup):
    db,_,ctx,budget,gateway=setup
    assert run(gateway,ctx,lambda:ProviderResult('ok',None))=='ok'
    assert run(gateway,ctx,lambda:pytest.fail('Do not re-call'))=='ok'
    with db.connect() as con:
        row=con.execute('SELECT * FROM usage_reservations').fetchone()
        assert row['state']=='pending_reconciliation'
    with pytest.raises(DomainError):
        budget.reconcile(row['call_id'],{'calls':1},actor_id='A',reason_code='PROVIDER_INVOICE')
    budget.reconcile(row['call_id'],{'calls':1},actor_id='admin',reason_code='PROVIDER_INVOICE')
    with db.connect() as con:
        audit=con.execute("SELECT * FROM usage_ledger WHERE entry_kind='settled'").fetchone()
        assert audit['actor_id']=='admin' and audit['reason_code']=='PROVIDER_INVOICE'


def test_provider_receipt_then_sql_crash_recovers_without_provider(setup,monkeypatch):
    _,_,ctx,budget,gateway=setup
    original=budget.settle
    def crash(*args,**kwargs):
        raise SystemExit('synthetic crash after fsync')
    monkeypatch.setattr(budget,'settle',crash)
    with pytest.raises(SystemExit):
        run(gateway,ctx,lambda:ProviderResult('survived',{'calls':2}))
    monkeypatch.setattr(budget,'settle',original)
    assert run(ProviderGateway(budget,gateway.result_dir),ctx,lambda:pytest.fail('must replay'))=='survived'


def test_process_exit_after_external_success_before_receipt_becomes_unknown(setup):
    db,_,ctx,budget,gateway=setup
    script='''
import os,sys
from src.foundation.db import Database
from src.reliability.budget import Budget,BudgetPolicy,CallContext
from src.reliability.providers import ProviderGateway,ProviderResult
b=Budget(Database(sys.argv[1]),BudgetPolicy.for_tests(rate='1',limit=10))
g=ProviderGateway(b,sys.argv[3])
g._persist=lambda *args:os._exit(73)
g.run(CallContext('A',sys.argv[2],sys.argv[2]),'extract','stage',{'calls':6},lambda:ProviderResult('remote success',{'calls':2}),provider='fake',request_hash='stage')
'''
    result=subprocess.run([sys.executable,'-c',script,str(db.path),ctx.trip_id,str(gateway.result_dir)],timeout=15)
    assert result.returncode==73
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,lambda:pytest.fail('Unknown must not re-call'))
    assert error.value.code=='CALL_OUTCOME_UNKNOWN'
    assert budget.summary('A')['currencies'][0]['state']=='unknown'


def test_delete_during_remote_call_discards_artifact_but_settles_bill(setup):
    db,repo,ctx,budget,gateway=setup
    def guard():
        repo.get_trip('A',ctx.trip_id)
    def late():
        repo.delete_trip('A',ctx.trip_id)
        return ProviderResult({'private':'result'},{'calls':2})
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,late,guard=guard)
    assert error.value.code=='NOT_FOUND'
    assert not list(gateway.result_dir.iterdir())
    assert budget.summary('A')['currencies'][0]['actual_micros']==2


def test_unsent_retry_has_new_attempt_and_bounded_retry_after(setup):
    db,_,ctx,_,gateway=setup
    calls=[]
    sleeps=[]
    gateway.sleeper=sleeps.append
    def provider():
        calls.append(1)
        if len(calls)==1:
            raise DefinitelyNotSent(retry_after=999)
        return ProviderResult('ok',{'calls':1})
    assert run(gateway,ctx,provider,max_attempts=2)=='ok'
    assert sleeps==[5] and len(calls)==2
    with db.connect() as con:
        assert [tuple(row) for row in con.execute('SELECT attempt,state FROM usage_reservations ORDER BY attempt')]==[(1,'released'),(2,'settled')]


def test_rate_limit_retry_retains_first_cost_and_requires_new_budget(setup):
    db,_,ctx,budget,gateway=setup
    gateway.sleeper=lambda _:None
    calls=[]
    def reject():
        calls.append(1)
        raise ProviderRejected('PROVIDER_RATE_LIMITED',retryable=True,retry_after=99)
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,reject,max_attempts=3)
    assert error.value.code=='BUDGET_EXHAUSTED' and calls==[1]
    with db.connect() as con:
        assert tuple(con.execute('SELECT state,estimated_cost_micros FROM usage_reservations').fetchone())==('pending_reconciliation',6)


def test_rate_limit_retry_and_terminal_auth_rejection_are_distinct(setup):
    db,_,ctx,_,gateway=setup
    gateway.budget=Budget(db,BudgetPolicy.for_tests(rate='1',limit=100))
    gateway.sleeper=lambda _:None
    calls=[]
    def temporary():
        calls.append(1)
        if len(calls)==1:
            raise ProviderRejected('PROVIDER_RATE_LIMITED',retryable=True)
        return ProviderResult('ok',{'calls':1})
    assert run(gateway,ctx,temporary,max_attempts=3)=='ok' and len(calls)==2
    def denied():
        calls.append(1)
        raise ProviderRejected('PROVIDER_AUTH_FAILED')
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,denied,key='auth',max_attempts=3)
    assert error.value.code=='PROVIDER_AUTH_FAILED' and len(calls)==3
    with db.connect() as con:
        assert [tuple(row) for row in con.execute('SELECT attempt,state FROM usage_reservations ORDER BY created_at')]==[(1,'pending_reconciliation'),(2,'settled'),(1,'pending_reconciliation')]


def test_session_revocation_after_provider_return_settles_without_artifact(setup):
    db,_,ctx,budget,gateway=setup
    def guard():
        with db.connect() as con:
            active=con.execute("SELECT 1 FROM users WHERE id='A' AND status='active'").fetchone()
        if not active:
            raise DomainError('AUTH_REQUIRED','synthetic revoked session',401)
    def late():
        with db.connect() as con:
            con.execute("UPDATE users SET status='revoked' WHERE id='A'")
        return ProviderResult('discarded',{'calls':2})
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,late,guard=guard)
    assert error.value.code=='AUTH_REQUIRED'
    assert not list(gateway.result_dir.rglob('*.json'))
    assert budget.summary('A')['currencies'][0]['actual_micros']==2


def test_interactive_and_job_calls_share_one_external_slot(setup):
    db,_,ctx,_,gateway=setup
    gateway.budget=Budget(db,BudgetPolicy.for_tests(rate='0',limit=100))
    started=threading.Event(); release=threading.Event(); running=0; peak=0
    mutex=threading.Lock()
    def first():
        nonlocal running,peak
        with mutex:
            running+=1; peak=max(peak,running)
        started.set(); assert release.wait(5)
        with mutex: running-=1
        return ProviderResult('one',{'calls':1})
    def second():
        nonlocal running,peak
        with mutex:
            running+=1; peak=max(peak,running); running-=1
        return ProviderResult('two',{'calls':1})
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(run,gateway,ctx,first,'interactive')
        assert started.wait(5)
        job_context=CallContext('A',ctx.trip_id,ctx.trip_id,job_id='job-example')
        b=pool.submit(run,gateway,job_context,second,'job')
        for _ in range(100):
            with db.connect() as con:
                count=con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0]
            if count==2: break
            time.sleep(.01)
        assert count==2 and not b.done()
        release.set()
        assert (a.result(5),b.result(5))==('one','two')
    assert peak==1


def test_cancelled_while_waiting_for_slot_releases_unsent_reservation(setup):
    db,_,ctx,_,gateway=setup
    gateway.budget=Budget(db,BudgetPolicy.for_tests(rate='1',limit=100))
    started=threading.Event(); release=threading.Event(); cancelled=threading.Event()
    def slow():
        started.set(); assert release.wait(5)
        return ProviderResult('ok',{'calls':1})
    def guard():
        if cancelled.is_set():
            raise DomainError('JOB_CANCELLED','synthetic cancelled',409)
    with ThreadPoolExecutor(2) as pool:
        one=pool.submit(run,gateway,ctx,slow,'one')
        assert started.wait(5)
        two=pool.submit(run,gateway,ctx,lambda:pytest.fail('Must not call cancelled provider'),'two',guard=guard)
        for _ in range(100):
            with db.connect() as con:
                count=con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0]
            if count==2: break
            time.sleep(.01)
        assert count==2
        cancelled.set(); release.set()
        assert one.result(5)=='ok'
        with pytest.raises(DomainError) as error:
            two.result(5)
        assert error.value.code=='JOB_CANCELLED'
    with db.connect() as con:
        assert con.execute("SELECT state FROM usage_reservations WHERE call_key='request:two'").fetchone()[0]=='released'


def test_scope_auth_and_admin_null_trip_uniqueness(setup):
    _,_,ctx,budget,_=setup
    for bad in (CallContext('B',ctx.trip_id,ctx.trip_id), CallContext('A','research',scope_kind='admin_research')):
        with pytest.raises(DomainError) as error:
            reserve(budget,bad)
        assert error.value.status==404
    admin=CallContext('admin','stable-place',scope_kind='admin_research')
    first=reserve(budget,admin)
    assert reserve(budget,admin)['call_id']==first['call_id']


def test_currencies_and_utc_months_not_combined(setup):
    db,_,ctx,_,_=setup
    config=deepcopy(BudgetPolicy.for_tests(rate='1',limit=6).config)
    config['prices']['fake/euro']={'currency':'EUR','rates_per_million':{'calls':'1'},'max_units':{'calls':100}}
    config['limits']['EUR']=dict(config['limits']['USD'])
    clock=[datetime(2026,1,31,23,59,tzinfo=timezone.utc)]
    budget=Budget(db,BudgetPolicy(config),clock=lambda:clock[0])
    reserve(budget,ctx)
    budget.reserve(ctx,provider='fake',sku='euro',operation='euro',call_key='euro',estimated_units={'calls':6},request_hash='h')
    clock[0]=datetime(2026,2,1,0,0,tzinfo=timezone.utc)
    reserve(budget,ctx,'newmonth')
    with db.connect() as con:
        assert [row[0] for row in con.execute('SELECT DISTINCT period_month FROM usage_reservations ORDER BY period_month')]==['2026-01','2026-02']


def test_historical_price_snapshotted_for_later_settlement(setup):
    db,_,ctx,budget,_=setup
    record=reserve(budget,ctx)
    budget.mark_sent(record['call_id'])
    budget.policy=BudgetPolicy.for_tests(rate='100',limit=10000)
    settled=budget.settle(record['call_id'],{'calls':2})
    assert settled['actual_cost_micros']==2


def test_explicit_bounds_rounding_and_invalid_policies(setup):
    _,_,ctx,budget,_=setup
    assert BudgetPolicy.calculate({'tokens':'0.3'},{'tokens':1})==1
    with pytest.raises(DomainError):
        reserve(budget,ctx,units=101)
    for bad in (-1,1.2,True):
        with pytest.raises(DomainError):
            reserve(budget,ctx,units=bad)
    config=deepcopy(BudgetPolicy.for_tests().config)
    config['prices']['fake/extract']['rates_per_million']['calls']='NaN'
    assert BudgetPolicy(config).valid is False


def test_sdk_internal_retries_disabled_and_usage_captured(setup):
    db,_,ctx,_,gateway=setup
    config=deepcopy(BudgetPolicy.for_tests().config)
    config['prices']['openai/synthetic-model']={'currency':'USD','rates_per_million':{'input_tokens':'1','output_tokens':'1'},'max_units':{'input_tokens':10000,'output_tokens':100}}
    gateway.budget=Budget(db,BudgetPolicy(config))
    options=[]
    calls=[]
    payload={'id':'fake','object':'chat.completion','created':0,'model':'synthetic-model','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'ok'}}],
        'usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6}}
    response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=4,completion_tokens=2),model_dump=lambda **_:payload)
    def create(**kwargs):
        calls.append(kwargs)
        return response
    client=SimpleNamespace(with_options=lambda **kwargs:(options.append(kwargs) or SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    with metered_context(gateway,ctx,'chat'):
        answer=chat_create(client,model='synthetic-model',messages=[{'role':'user','content':'hello'}])
    assert answer.choices[0].message.content=='ok'
    assert options==[{'max_retries':0,'timeout':45.0}]
    assert calls[0]['max_completion_tokens']==100
    with db.connect() as con:
        assert con.execute('SELECT actual_cost_micros FROM usage_reservations').fetchone()[0]==6


@pytest.mark.parametrize('setting',[{'external_enabled':False},{'disabled_providers':['fake']},{'mode':'read_only'}])
def test_operations_stop_blocks_new_call_and_releases_only_unsent(setup,setting):
    from src.operations.controls import update
    db,_,ctx,budget,gateway=setup
    update(db,**setting)
    with pytest.raises(DomainError) as error:
        run(gateway,ctx,lambda:pytest.fail('Provider must not run'))
    assert error.value.code=='EXTERNAL_CALLS_PAUSED'
    with db.connect() as con:
        row=con.execute('SELECT * FROM usage_reservations').fetchone()
        assert row['state']=='released' and row['actual_cost_micros'] is None
