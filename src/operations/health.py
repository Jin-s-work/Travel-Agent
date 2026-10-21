"""Local-only checks and bounded aggregate observations. Never calls a provider."""
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
import resource
import sys
import threading
import shutil
from src.foundation.db import SCHEMA_VERSION
from .preflight import writable,storage_paths


class Metrics:
    def __init__(self):self.lock=threading.Lock();self.samples=deque(maxlen=2000);self.status=Counter()
    def record(self,status,duration):
        with self.lock:self.samples.append(duration);self.status[str(status//100)+'xx']+=1
    def snapshot(self):
        with self.lock:values=sorted(self.samples);counts=dict(self.status)
        def percentile(p):return round(values[min(len(values)-1,int((len(values)-1)*p))]*1000,2) if values else None
        memory=resource.getrusage(resource.RUSAGE_SELF)
        return {'window_samples':len(values),'requests_by_status':counts,'readiness_excluded':True,
                'http_ms_p50':percentile(.5),'http_ms_p95':percentile(.95),
                'peak_rss_bytes':int(memory.ru_maxrss*(1 if sys.platform=='darwin' else 1024)),
                'cpu_seconds':round(memory.ru_utime+memory.ru_stime,3)}


def ready(app):
    settings=app.state.settings
    try:
        with app.state.db.connect() as con:
            schema=app.state.db.schema_version()==SCHEMA_VERSION
            con.execute('SELECT 1').fetchone()
        storage=all(writable(p) for p in storage_paths(settings))
        dispatcher=app.state.jobs.health()['dispatcher_alive']
        restore=not any((settings.database_path.parent/p).exists() for p in ('RESTORE_PENDING.json','RESTORE_INCOMPLETE.json'))
        ok=schema and storage and dispatcher and restore and settings.auth_configured
        return {'ready':ok,'checks':{'schema':schema,'storage':storage,'dispatcher':dispatcher,'identity':settings.auth_configured,'restore':restore}}
    except Exception:return {'ready':False,'checks':{'local_storage':False}}


def summary(app):
    now=datetime.now(timezone.utc)
    with app.state.db.connect() as con:
        counts={row['state']:row['n'] for row in con.execute('SELECT state,count(*) n FROM jobs GROUP BY state')}
        row=con.execute("SELECT MIN(created_at) stamp FROM jobs WHERE state='queued'").fetchone()
        pending=con.execute("SELECT count(*) FROM usage_reservations WHERE state IN ('unknown','pending_reconciliation')").fetchone()[0]
        calls=[dict(r) for r in con.execute('SELECT provider,state,count(*) count FROM usage_reservations GROUP BY provider,state')]
        errors=[dict(r) for r in con.execute("SELECT error_code,count(*) count FROM jobs WHERE error_code IS NOT NULL GROUP BY error_code")]
        retries=con.execute('SELECT COALESCE(SUM(MAX(attempt-1,0)),0) FROM jobs').fetchone()[0]
        provider_errors=[dict(r) for r in con.execute("SELECT provider,error_code,count(*) count FROM usage_reservations WHERE error_code IS NOT NULL GROUP BY provider,error_code")]
        budgets=[]
        policy=app.state.budget.policy
        if policy.valid:
            for currency,caps in policy.config['limits'].items():
                for label,column,period in [('global_daily','period_day',now.date().isoformat()),('global_monthly','period_month',now.strftime('%Y-%m'))]:
                    used=con.execute(f"SELECT COALESCE(SUM(CASE WHEN state='released' THEN 0 WHEN state='settled' THEN actual_cost_micros ELSE estimated_cost_micros END),0) FROM usage_reservations WHERE currency=? AND {column}=?",(currency,period)).fetchone()[0]
                    budgets.append({'currency':currency,'period':label,'used_or_reserved_micros':used,'limit_micros':caps[label],'remaining_micros':max(0,caps[label]-used)})
        backups=[dict(r) for r in con.execute("SELECT action,created_at FROM operations_audit WHERE action IN ('offhost_backup','backup_failed') ORDER BY id DESC LIMIT 3")]
    oldest=max(0,(now-datetime.fromisoformat(row['stamp'])).total_seconds()) if row['stamp'] else 0
    disk=shutil.disk_usage(app.state.settings.database_path.parent)
    maintenance=app.state.maintenance.snapshot() if hasattr(app.state,'maintenance') else {'state':'not_started','healthy':False}
    # Per provider/model/operation aggregates only: no owner, trip, prompt or response text.
    llm_calls=llm_call_summary(app.state.db,days=7)
    return {**app.state.metrics.snapshot(),'maintenance':maintenance,'disk':{'total_bytes':disk.total,'free_bytes':disk.free},'backup_events':backups,'queue':counts,'oldest_queue_seconds':round(oldest,3),
            'budgets':budgets,'billing_outside_app_observed':False,'provider_errors':provider_errors,'job_retries':retries,'job_errors':errors,'provider_attempts':calls,'uncertain_charges':pending,'llm_calls':llm_calls}


# Outcomes raised before anything reaches a provider (budget refusal, idempotency,
# re-run of an already-sent/unsent key, bad receipt). Released reservations and
# rows with no reservation are also "not sent" and are matched in SQL below.
_NOT_SENT_OUTCOMES=('BUDGET_EXHAUSTED','GLOBAL_BUDGET_STOPPED','COST_BOUND_REQUIRED','COST_BOUND_EXCEEDED',
                    'CALL_INVALID','CALL_INPUT_HASH_REQUIRED','IDEMPOTENCY_CONFLICT','CALL_OUTCOME_UNKNOWN',
                    'CALL_NOT_SENT','PROVIDER_RECEIPT_INVALID','PROVIDER_CONTEXT_REQUIRED')
LLM_SAMPLE_CAP=1000


def llm_call_summary(db,*,days=7,now=None,sample_cap=LLM_SAMPLE_CAP):
    """Admin LLM view that matches the cost ledger.

    calls = attempts actually sent to a provider. Receipt replays (`replayed`)
    and attempts stopped before sending (`blocked`) are counted separately and
    never add cost, latency samples or failures. Cost comes from the linked
    reservation once settled. Counts and sums are SQL aggregates; percentiles
    use at most `sample_cap` most recent sent calls per group (nearest rank).
    """
    from src.llm.telemetry import percentile
    since=((now or datetime.now(timezone.utc))-timedelta(days=days)).isoformat()
    marks=','.join('?' for _ in _NOT_SENT_OUTCOMES)
    base=("SELECT c.provider,c.model,c.operation,c.latency_ms,c.outcome,c.schema_valid,c.repair_attempts,c.created_at,"
          f"CASE WHEN c.call_id IS NULL OR r.state='released' OR c.outcome IN ({marks}) THEN 'blocked' "
          "WHEN c.replayed<>0 THEN 'replayed' ELSE 'sent' END kind,"
          "CASE WHEN r.call_id IS NULL THEN COALESCE(c.cost_micros,0) WHEN r.state='settled' THEN COALESCE(r.actual_cost_micros,0) ELSE 0 END cost "
          "FROM llm_calls c LEFT JOIN usage_reservations r ON r.call_id=c.call_id WHERE c.created_at>=?")
    args=(*_NOT_SENT_OUTCOMES,since)
    sent="CASE WHEN kind='sent' THEN 1 ELSE 0 END"
    try:
        with db.connect() as con:
            groups=[dict(r) for r in con.execute(
                f"SELECT provider,model,operation,SUM({sent}) calls,"
                "SUM(CASE WHEN kind='replayed' THEN 1 ELSE 0 END) replayed,SUM(CASE WHEN kind='blocked' THEN 1 ELSE 0 END) blocked,"
                f"SUM(CASE WHEN kind='sent' AND outcome NOT IN ('ok','repaired') THEN 1 ELSE 0 END) failures,"
                "SUM(CASE WHEN kind='sent' AND schema_valid IS NOT NULL THEN 1 ELSE 0 END) validated,"
                "SUM(CASE WHEN kind='sent' AND schema_valid=1 THEN 1 ELSE 0 END) valid,"
                "SUM(CASE WHEN kind='sent' THEN COALESCE(repair_attempts,0) ELSE 0 END) repairs,"
                "SUM(CASE WHEN kind='sent' THEN cost ELSE 0 END) cost_micros "
                f"FROM ({base}) b GROUP BY provider,model,operation ORDER BY provider,model,operation",args)]
            blocked_by=[dict(r) for r in con.execute(
                f"SELECT outcome,count(*) n FROM ({base}) b WHERE kind='blocked' GROUP BY outcome ORDER BY outcome",args)]
            samples=con.execute(
                "SELECT provider,model,operation,latency_ms FROM (SELECT provider,model,operation,latency_ms,"
                "ROW_NUMBER() OVER (PARTITION BY provider,model,operation ORDER BY created_at DESC) rn "
                f"FROM ({base}) b WHERE kind='sent' AND latency_ms IS NOT NULL) s WHERE rn<=?",(*args,int(sample_cap))).fetchall()
    except Exception:  # noqa: BLE001 - table absent on an old snapshot
        return {'window_days':days,'calls':0,'by_operation':[],'available':False}
    latencies={}
    for row in samples:latencies.setdefault((row['provider'],row['model'],row['operation']),[]).append(row['latency_ms'])
    result=[]
    for g in groups:
        calls,values=int(g['calls'] or 0),latencies.get((g['provider'],g['model'],g['operation']),[])
        result.append({'provider':g['provider'],'model':g['model'],'operation':g['operation'],'calls':calls,
                       'replayed':int(g['replayed'] or 0),'blocked':int(g['blocked'] or 0),'latency_samples':len(values),
                       'p50_ms':percentile(values,.5),'p95_ms':percentile(values,.95),
                       'failure_rate':round(int(g['failures'] or 0)/calls,4) if calls else None,
                       'schema_valid_rate':round(int(g['valid'] or 0)/int(g['validated']),4) if g['validated'] else None,
                       'repairs':int(g['repairs'] or 0),'cost_micros':int(g['cost_micros'] or 0)})
    return {'window_days':days,'calls':sum(g['calls'] for g in result),'replayed':sum(g['replayed'] for g in result),
            'blocked':sum(g['blocked'] for g in result),'blocked_by_outcome':{r['outcome']:int(r['n']) for r in blocked_by},
            'sample_cap':int(sample_cap),'by_operation':result,'available':True}
