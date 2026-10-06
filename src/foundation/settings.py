"""Explicit deployment settings; absent identity configuration never opens APIs."""
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
import os

@dataclass
class Settings:
    preparation_enabled: bool = field(default_factory=lambda: os.getenv('PREPARATION_ENABLED', 'true').lower() == 'true')
    plan_b_enabled: bool = field(default_factory=lambda: os.getenv('PLAN_B_ENABLED', 'true').lower() == 'true')
    offline_enabled: bool = field(default_factory=lambda: os.getenv('OFFLINE_ENABLED', 'false').lower() == 'true')
    storage_backend: str = field(default_factory=lambda: os.getenv('STORAGE_BACKEND', 'local'))
    database_url: str = field(default_factory=lambda: os.getenv('DATABASE_URL', ''))
    supabase_url: str = field(default_factory=lambda: os.getenv('SUPABASE_URL', ''))
    supabase_secret_key: str = field(default_factory=lambda: os.getenv('SUPABASE_SECRET_KEY', ''))
    supabase_bucket: str = field(default_factory=lambda: os.getenv('SUPABASE_STORAGE_BUCKET', 'travel-private'))
    database_path: Path = field(default_factory=lambda: Path(os.getenv('DATABASE_PATH', 'data/service.sqlite3')))
    documents_dir: Path = field(default_factory=lambda: Path(os.getenv('DOCUMENTS_DIR', 'data/private-documents')))
    vectors_dir: Path = field(default_factory=lambda: Path(os.getenv('VECTORS_DIR', 'data/private-vectors')))
    environment: str = field(default_factory=lambda: os.getenv('APP_ENV', 'production'))
    public_base_url: str = field(default_factory=lambda: os.getenv('PUBLIC_BASE_URL', 'http://localhost:8000').rstrip('/'))
    oidc_client_id: str = field(default_factory=lambda: os.getenv('OIDC_CLIENT_ID', ''))
    oidc_client_secret: str = field(default_factory=lambda: os.getenv('OIDC_CLIENT_SECRET', ''))
    oidc_metadata_url: str = field(default_factory=lambda: os.getenv('OIDC_SERVER_METADATA_URL', 'https://accounts.google.com/.well-known/openid-configuration'))
    session_secret: str = field(default_factory=lambda: os.getenv('SESSION_SECRET', ''))
    session_hours: int = 24
    max_upload_bytes: int = 1024 * 1024
    max_upload_files: int = 10
    max_request_bytes: int = 11 * 1024 * 1024
    pricing_config: str = field(default_factory=lambda: os.getenv('PRICING_CONFIG', ''))
    job_lease_seconds: int = field(default_factory=lambda: int(os.getenv('JOB_LEASE_SECONDS', '90')))
    job_heartbeat_seconds: float = field(default_factory=lambda: float(os.getenv('JOB_HEARTBEAT_SECONDS', '20')))
    job_poll_seconds: float = field(default_factory=lambda: float(os.getenv('JOB_POLL_SECONDS', '0.25')))
    job_max_attempts: int = field(default_factory=lambda: int(os.getenv('JOB_MAX_ATTEMPTS', '3')))
    job_deadline_seconds: int = field(default_factory=lambda: int(os.getenv('JOB_DEADLINE_SECONDS', '900')))
    job_shutdown_seconds: float = field(default_factory=lambda: float(os.getenv('JOB_SHUTDOWN_SECONDS', '5')))
    location_provider_config: str = field(default_factory=lambda: os.getenv('LOCATION_PROVIDER_CONFIG', ''))
    google_maps_api_key: str = field(default_factory=lambda: os.getenv('GOOGLE_MAPS_API_KEY', ''))
    storage_orphan_grace_seconds: int = 86400

    @property
    def artifacts_dir(self):
        return self.database_path.parent / 'private-job-artifacts'

    @property
    def secure_cookie(self):
        return self.environment != 'development'

    @property
    def cookie_name(self):
        return '__Host-session' if self.secure_cookie else 'travel_dev_session'

    @property
    def auth_configured(self):
        url = urlsplit(self.public_base_url)
        metadata = urlsplit(self.oidc_metadata_url)
        valid_metadata = metadata.scheme == 'https' or (self.environment == 'development' and metadata.scheme == 'http' and metadata.hostname in {'localhost','127.0.0.1'})
        valid_origin = url.scheme == 'https' or (self.environment == 'development' and url.scheme == 'http' and url.hostname in {'localhost', '127.0.0.1', 'testserver'})
        return bool(valid_origin and valid_metadata and not url.username and not url.password and not url.path and not url.query and not url.fragment and self.oidc_client_id and self.oidc_client_secret and len(self.session_secret) >= 32)
