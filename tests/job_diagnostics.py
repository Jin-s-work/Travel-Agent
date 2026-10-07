"""Failure-only synthetic job diagnostics; never payloads, sessions or tokens."""
from datetime import datetime
import time


def snapshot(jobs, *, clock_samples=()):
    now=jobs.now()
    with jobs.db.connect() as con:
        rows=con.execute('SELECT * FROM jobs ORDER BY created_at,id').fetchall()
        leader=con.execute("SELECT owner,heartbeat_at,lease_expires_at FROM dispatcher_leases WHERE name='main'").fetchone()
        mode=con.execute('SELECT mode FROM operations_controls').fetchone()
        result=[]
        for row in rows:
            safe={key:row[key] for key in ('operation','state','stage','attempt','error_code','available_at','deadline_at','created_at','updated_at','lease_expires_at')}
            safe['available_delta_seconds']=(datetime.fromisoformat(row['available_at'])-datetime.fromisoformat(now)).total_seconds()
            try:jobs._validate_scope_for_worker(con,row)
            except Exception as error:safe['scope_error']=getattr(error,'code',type(error).__name__)
            else:safe['scope_error']=None
            result.append(safe)
    return {'now':now,'monotonic_ns':time.monotonic_ns(),'accepting':jobs.accepting,
            'controls_mode':mode['mode'] if mode else None,'dispatcher':dict(leader) if leader else None,
            'jobs':result,'clock_samples':list(clock_samples)}


def claim_required(jobs, owner):
    """Exactly one claim, with its original clock samples retained on failure."""
    original=jobs.clock
    samples=[]
    def sampled_clock():
        value=original()
        samples.append({'wall':value.isoformat(),'monotonic_ns':time.monotonic_ns()})
        return value
    jobs.clock=sampled_clock
    try:job=jobs.claim(owner)
    finally:jobs.clock=original
    assert job is not None, snapshot(jobs,clock_samples=samples)
    return job
