"""Free-host production gates, without remote credentials or network calls."""
from dataclasses import replace
from pathlib import Path
import os
import pytest
from src.foundation.settings import Settings
from src.operations.preflight import validate,StartupError


def test_cloud_gate_requires_tls_session_pooler_and_zero_cost(tmp_path,monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:1000)
    monkeypatch.setenv('ZERO_SPEND','1');monkeypatch.setenv('BACKUP_ENABLED','0')
    monkeypatch.setenv('WEB_CONCURRENCY','1');monkeypatch.setenv('UVICORN_WORKERS','1')
    monkeypatch.setenv('SEED_ON_EMPTY','0')
    settings=Settings(storage_backend='supabase',database_url='postgresql://postgres.fixture:example@aws-1-ap-northeast-1.pooler.supabase.com:5432/postgres?sslmode=verify-full',supabase_url='https://fixture.supabase.co',supabase_secret_key='sb_secret_fixture',database_path=tmp_path/'sql/unused.sqlite3',documents_dir=tmp_path/'documents',vectors_dir=tmp_path/'vectors',environment='production',public_base_url='https://beta.example.test',oidc_client_id='fixture',oidc_client_secret='fixture',session_secret='x'*48,pricing_config=str(Path(__file__).parents[1]/'deploy/render-supabase/pricing-zero.json'))
    assert validate(settings)['durable_backend']=='supabase'
    for bad in [settings.database_url.replace(':5432',':6543'),settings.database_url.replace('verify-full','require'),settings.database_url.replace('.supabase.com','.example.test')]:
        with pytest.raises(StartupError,match='DATABASE_URL'):validate(replace(settings,database_url=bad))
    with pytest.raises(StartupError,match='ZERO_SPEND'):validate(replace(settings,pricing_config=''))
    with pytest.raises(StartupError,match='identity'):validate(replace(settings,oidc_client_id=''))
    monkeypatch.setenv('BACKUP_ENABLED','1')
    with pytest.raises(StartupError,match='BACKUP_ENABLED'):validate(settings)


def test_mismatched_cloud_settings_never_create_local_database(tmp_path):
    from api import create_app
    settings=Settings(database_path=tmp_path/'must-not-exist.sqlite3',storage_backend='local',database_url='postgresql://invalid')
    with pytest.raises(ValueError):create_app(settings)
    assert not settings.database_path.exists()
