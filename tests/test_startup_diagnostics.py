"""Production failures stay closed without logging connection strings or payloads."""
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.operations import launch
from src.operations.preflight import StartupError

SECRET='postgresql://private-user:do-not-log-password@private-host/db SELECT private_mail'


class DatabaseFailure(Exception):
    sqlstate='40P01'
    def __str__(self):
        raise AssertionError('Diagnostics must never render exception text')


def caught_in_repo(error,*,depth=1):
    # Deliberately include a secret in source text, locals, and exception args.
    # The traceback filename identifies the same repository code allowlist used
    # in production; line contents must never be read or formatted.
    source='def connect(n):\n    private_dsn=SECRET\n    if n: return connect(n-1)\n    raise error\nconnect(depth)'
    namespace={'error':error,'SECRET':SECRET,'depth':depth}
    try:exec(compile(source,str(launch._REPO_ROOT/'src/storage/postgres.py'),'exec'),namespace)
    except Exception as exc:return exc
    raise AssertionError('Expected failure')


def test_database_diagnostic_contains_only_class_state_and_bounded_repo_frames():
    error=caught_in_repo(DatabaseFailure(SECRET),depth=15)
    name,state,frames=launch.startup_diagnostic(error)
    assert name=='DatabaseFailure' and state=='40P01'
    assert len(frames.split('|'))==8
    assert all(frame.startswith('postgres.py:connect:') for frame in frames.split('|'))
    assert SECRET not in frames and '/Users/' not in frames and 'test_startup_diagnostics' not in frames


@pytest.mark.parametrize('state',[None,'',SECRET,'40P01\n'+SECRET,'40p01',12345,{'dsn':SECRET}])
def test_unvalidated_sqlstate_never_reaches_logs(state):
    error=DatabaseFailure(SECRET);error.sqlstate=state
    assert launch.startup_diagnostic(error)==('DatabaseFailure','unknown','unavailable')


def test_hostile_sqlstate_property_and_foreign_stack_are_safely_omitted():
    class Unreadable(Exception):
        @property
        def sqlstate(self):raise ValueError(SECRET)
    try:exec(compile('raise error','/tmp/private-data/account.py','exec'),{'error':Unreadable(SECRET)})
    except Exception as exc:
        assert launch.startup_diagnostic(exc)==('Unreadable','unknown','unavailable')


@pytest.mark.parametrize('error,kind',[(DatabaseFailure(SECRET),'startup_failed'),(StartupError(SECRET),'startup_rejected')])
def test_launch_still_exits_before_http_and_never_logs_exception_payload(monkeypatch,tmp_path,caplog,error,kind):
    from src.foundation import settings
    monkeypatch.setattr(settings,'Settings',lambda:SimpleNamespace(database_path=tmp_path/'db',storage_backend='supabase'))
    monkeypatch.setattr(launch.os,'umask',lambda _:None)
    def reject(_):raise caught_in_repo(error)
    monkeypatch.setattr(launch,'validate',reject)
    started=[]
    monkeypatch.setitem(sys.modules,'uvicorn',SimpleNamespace(run=lambda *a,**kw:started.append(True)))
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as failure:launch.main()
    assert failure.value.code==78 and not started
    assert len(caplog.records)==1
    record=caplog.records[0]
    message=record.getMessage()
    assert message.startswith(kind+': class=') and 'frames=' in message
    assert 'launch.py:main:' in message and 'postgres.py:connect:' in message
    assert SECRET not in message and 'private-user' not in message and 'SELECT' not in message
    assert record.exc_info is None and record.stack_info is None
    assert '/Users/' not in message and '/tmp/' not in message
