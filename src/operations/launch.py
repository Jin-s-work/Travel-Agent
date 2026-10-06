"""Production entrypoint: fail before opening the HTTP socket."""
import logging
import os
import re
from collections import deque
from pathlib import Path
from .preflight import InstanceLock,StartupError,validate


_REPO_ROOT=Path(__file__).resolve().parents[2]


def startup_diagnostic(exc):
    """Bounded code metadata only: never render exception text or stack locals.

    Third-party frames and arbitrary filenames are excluded. SQLSTATE is the
    standardized five-character database status, not a server diagnostic string.
    """
    name=type(exc).__name__
    error_class=name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',name) else 'Exception'
    try:state=getattr(exc,'sqlstate',None)
    except Exception:state=None
    sqlstate=state if isinstance(state,str) and re.fullmatch(r'[0-9A-Z]{5}',state) else 'unknown'
    frames=deque(maxlen=8)
    traceback=exc.__traceback__
    while traceback is not None:
        code=traceback.tb_frame.f_code
        try:
            relative=Path(code.co_filename).resolve().relative_to(_REPO_ROOT)
        except (ValueError,OSError,RuntimeError):relative=None
        if relative is not None and (relative==Path('api.py') or relative.parts and relative.parts[0]=='src'):
            filename=relative.name
            if re.fullmatch(r'[A-Za-z0-9_-]+\.py',filename):
                function=code.co_name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}|<module>',code.co_name) else 'unknown'
                line=traceback.tb_lineno if 0<traceback.tb_lineno<=1000000 else 0
                frames.append(f'{filename}:{function}:{line}')
        traceback=traceback.tb_next
    return error_class,sqlstate,'|'.join(frames) or 'unavailable'


def _log_failure(kind,exc):
    logging.getLogger(__name__).error(kind+': class=%s sqlstate=%s frames=%s',*startup_diagnostic(exc))


def main():
    os.umask(0o077)
    from src.foundation.settings import Settings
    try:
        settings=Settings()
        validate(settings)
        lock_root=settings.database_path.parent if settings.storage_backend=='supabase' else Path(os.environ['PERSISTENT_STORAGE_ROOT'])
        with InstanceLock(lock_root/'.service.lock'):
            import uvicorn
            # api creates/migrates SQLite while this process owns the mount lock.
            from api import app
            uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','7860')),workers=1,
                        access_log=False,proxy_headers=False,timeout_graceful_shutdown=20)
    except StartupError as exc:
        _log_failure('startup_rejected',exc)
        raise SystemExit(78) from None
    except Exception as exc:
        _log_failure('startup_failed',exc)
        raise SystemExit(78) from None

if __name__=='__main__':main()
