"""Production entrypoint: fail before opening the HTTP socket."""
import logging
import os
from pathlib import Path
from .preflight import InstanceLock,StartupError,validate


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
        logging.getLogger(__name__).error('startup_rejected: %s',exc)
        raise SystemExit(78) from None
    except Exception:
        logging.getLogger(__name__).error('startup_failed: storage/schema/configuration validation failed')
        raise SystemExit(78) from None

if __name__=='__main__':main()
