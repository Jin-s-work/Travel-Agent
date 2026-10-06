"""Opt-in cross-backend contract suite. Only loopback disposable PostgreSQL.

TRAVEL_TEST_POSTGRES_DSN=... python -m pytest -p tests.postgres_plugin ...
No production Settings or credential defaults are changed by this plugin.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import pytest

@pytest.fixture(autouse=True)
def postgres_contract(monkeypatch):
    from src.foundation.db import Database
    from src.storage.postgres import PostgresDatabase, Connection
    import psycopg
    from psycopg import sql
    dsn=os.environ['TRAVEL_TEST_POSTGRES_DSN']
    if urlsplit(dsn).hostname not in {'127.0.0.1','localhost'}: raise ValueError('Tests require disposable loopback PostgreSQL')
    instances={}; schemas=set()
    original_execute=Connection.execute
    def checked_execute(self,statement,parameters=None):
        try:return original_execute(self,statement,parameters)
        except Exception as exc:
            print('POSTGRES TEST SQL ERROR',type(exc).__name__,str(exc),statement)
            raise
    monkeypatch.setattr(Connection,'execute',checked_execute)
    def iterdump(self):
        import json
        names=self.raw.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()').fetchall()
        for row in names:
            for record in self.raw.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(row[0]))):
                yield json.dumps(dict(record),ensure_ascii=False,default=str)
    monkeypatch.setattr(Connection,'iterdump',iterdump,raising=False)
    def factory(cls,path,*,url=None):
        key=str(Path(path).resolve())
        if key in instances and instances[key].pool.closed:
            instances[key]=PostgresDatabase(dsn,path,schema=instances[key].schema)
        if key not in instances:
            schema='travel_test_'+uuid4().hex
            instances[key]=PostgresDatabase(dsn,path,schema=schema)
            schemas.add(schema)
        return instances[key]
    monkeypatch.setattr(Database,'__new__',staticmethod(factory))
    yield
    for db in instances.values(): db.close()
    with psycopg.connect(dsn,autocommit=True) as con:
        for schema in schemas: con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
