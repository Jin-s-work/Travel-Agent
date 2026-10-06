"""Local-only checks and bounded aggregate observations. Never calls a provider."""
from collections import Counter, deque
from datetime import datetime, timezone
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
    return {**app.state.metrics.snapshot(),'disk':{'total_bytes':disk.total,'free_bytes':disk.free},'backup_events':backups,'queue':counts,'oldest_queue_seconds':round(oldest,3),
            'budgets':budgets,'billing_outside_app_observed':False,'provider_errors':provider_errors,'job_retries':retries,'job_errors':errors,'provider_attempts':calls,'uncertain_charges':pending}
