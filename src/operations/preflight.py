"""No secrets or user values in startup errors. No provider/network calls."""
import fcntl
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, parse_qs


class StartupError(RuntimeError):pass


def writable(directory):
    try:
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.NamedTemporaryFile(prefix='.write-probe-',dir=directory) as f:
            f.write(b'1');f.flush();os.fsync(f.fileno())
        return True
    except OSError:return False


def storage_paths(settings):
    return [settings.database_path.parent,settings.documents_dir,settings.vectors_dir,settings.artifacts_dir]


def validate(settings,*,require_mount=True):
    errors=[]
    if settings.environment!='production':errors.append('APP_ENV: production required')
    if not settings.auth_configured:errors.append('OIDC/PUBLIC_BASE_URL/SESSION_SECRET: valid HTTPS identity configuration required')
    if os.getenv('SEED_ON_EMPTY','0').lower() not in {'0','false','no'}:errors.append('SEED_ON_EMPTY: must be 0')
    if os.getenv('WEB_CONCURRENCY','1')!='1' or os.getenv('UVICORN_WORKERS','1')!='1':errors.append('WORKERS: single process required')
    if not 0<settings.job_heartbeat_seconds<settings.job_lease_seconds/2:errors.append('JOB_HEARTBEAT_SECONDS: must be less than half of lease')
    if not 1<=settings.job_max_attempts<=10 or not 30<=settings.job_deadline_seconds<=86400:errors.append('JOB_LIMITS: invalid')
    if not 0<settings.job_shutdown_seconds<settings.job_lease_seconds:errors.append('JOB_SHUTDOWN_SECONDS: must be below lease')
    if settings.storage_backend=='supabase':
        try:
            db=urlsplit(settings.database_url);query=parse_qs(db.query)
            if db.scheme not in {'postgres','postgresql'} or not db.hostname or not db.hostname.endswith('.supabase.com') or db.port!=5432 or query.get('sslmode')!=['verify-full']:
                errors.append('DATABASE_URL: Supabase session pooler port5432 with sslmode=verify-full required')
        except ValueError:errors.append('DATABASE_URL: invalid')
        if not settings.supabase_url or not settings.supabase_secret_key:errors.append('SUPABASE_STORAGE: server settings required')
        if os.getenv('BACKUP_ENABLED','0')!='0':errors.append('BACKUP_ENABLED: SQLite scheduler unavailable; use cloud backup CLI')
        if os.getenv('ZERO_SPEND','0')=='1':
            from src.reliability.budget import BudgetPolicy
            policy=BudgetPolicy.from_file(settings.pricing_config)
            if not policy.valid or not policy.config.get('halted') or any(v!=0 for caps in policy.config.get('limits',{}).values() for v in caps.values()):
                errors.append('ZERO_SPEND: valid halted policy with every limit zero required')
        if hasattr(os,'geteuid') and os.geteuid()==0:errors.append('RUNTIME_USER: non-root required')
        public=Path(__file__).resolve().parents[2]/'web'
        for path in storage_paths(settings):
            if not path.is_absolute() or path.resolve().is_relative_to(public):errors.append('CACHE_PATH: private absolute path required')
        if errors:raise StartupError('; '.join(sorted(set(errors))))
        if not all(writable(path) for path in storage_paths(settings)):raise StartupError('CACHE_WRITE: runtime user cannot write')
        return {'configured':True,'storage_writable':True,'persistent_mount':False,'durable_backend':'supabase'}
    if settings.storage_backend!='local' or settings.database_url:errors.append('STORAGE_BACKEND: invalid combination')
    raw=os.getenv('PERSISTENT_STORAGE_ROOT','')
    root=Path(raw).resolve() if raw else None
    if not root or not Path(raw).is_absolute():errors.append('PERSISTENT_STORAGE_ROOT: absolute persistent mount required')
    elif require_mount and not os.path.ismount(root):errors.append('PERSISTENT_STORAGE_ROOT: mount not found')
    paths=[settings.database_path,settings.documents_dir,settings.vectors_dir]
    public=Path(__file__).resolve().parents[2]/'web'
    for path in paths:
        resolved=path.resolve()
        if not path.is_absolute() or root is None or not resolved.is_relative_to(root) or resolved==root:errors.append('STORAGE_PATH: must be below persistent mount')
        if resolved.is_relative_to(public.resolve()):errors.append('STORAGE_PATH: public directory forbidden')
        if any(part.is_symlink() for part in [path,*path.parents]):errors.append('STORAGE_PATH: symlink forbidden')
    docs,vectors=settings.documents_dir.resolve(),settings.vectors_dir.resolve()
    if docs.is_relative_to(vectors) or vectors.is_relative_to(docs) or settings.database_path.resolve().is_relative_to(docs) or settings.database_path.resolve().is_relative_to(vectors):errors.append('STORAGE_PATH: use separate SQL/documents/vectors paths')
    if hasattr(os,'geteuid') and os.geteuid()==0:errors.append('RUNTIME_USER: non-root required')
    for name in ('RESTORE_INCOMPLETE.json','RESTORE_PENDING.json'):
        if (settings.database_path.parent/name).exists():errors.append('RESTORE_GATE: offline validation required')
    if errors:raise StartupError('; '.join(sorted(set(errors))))
    if os.getenv('BACKUP_ENABLED','0')=='1':
        try:
            from .backup import key_bytes
            from .remote import ObjectStore
            key_bytes(os.getenv('BACKUP_ENCRYPTION_KEY',''));ObjectStore.from_env()
            interval=int(os.getenv('BACKUP_CHECKPOINT_SECONDS','300'));full=int(os.getenv('BACKUP_INTERVAL_SECONDS','86400'));retention=int(os.getenv('BACKUP_RETENTION_DAYS','7'))
            if not 300<=interval<=3600 or not interval<=full<=86400 or not 7<=retention<=90:raise ValueError()
        except Exception:raise StartupError('BACKUP_CONFIG: key, destination, credentials or intervals invalid') from None
    if not all(writable(path) for path in storage_paths(settings)):raise StartupError('STORAGE_WRITE: runtime user cannot write')
    return {'configured':True,'storage_writable':True,'persistent_mount':require_mount}


class InstanceLock:
    def __init__(self,path):self.path=Path(path);self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=open(self.path,'a')
        os.chmod(self.path,0o600)
        try:fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise StartupError('INSTANCE_LOCK: another service owns this disk') from None
        return self
    def __exit__(self,*exc):
        fcntl.flock(self.file,fcntl.LOCK_UN);self.file.close()
