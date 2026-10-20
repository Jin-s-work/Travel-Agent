"""Actual schema 11→12 upgrade on an isolated loopback PostgreSQL schema.

Run with tests.postgres_plugin. No existing application or production DSN is used.
"""
import json
import os
from urllib.parse import urlsplit
import pytest
from src.foundation.db import Database
from src.foundation.repository import new_id, dump

pytestmark=pytest.mark.skipif(not os.environ.get('TRAVEL_TEST_POSTGRES_DSN'),reason='Disposable PostgreSQL upgrade test; run with tests.postgres_plugin')


def test_real_schema11_upgrade_keeps_owners_stops_booking_override_and_original_values(tmp_path):
    assert urlsplit(os.environ['TRAVEL_TEST_POSTGRES_DSN']).hostname in {'127.0.0.1','localhost'}
    path=tmp_path/'isolated-upgrade.sqlite3';db=Database(path)
    assert db.backend=='postgres','Enable -p tests.postgres_plugin; never use a real application database'
    stamp='2026-10-06T00:00:00+00:00';users=[new_id('user'),new_id('user')]
    trips=[new_id('trip') for _ in range(4)];stops=[new_id('stop') for _ in range(4)]
    document,generation,booking,override=[new_id(prefix) for prefix in ('doc','gen','booking','override')]
    tables=('users','sessions','trips','trip_stops','source_documents','document_generations','bookings','booking_overrides','discovery_conditions')
    def contents(connection):
        return {table:[dict(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY 1')] for table in tables}
    with db.connect() as con:
        for n,owner in enumerate(users):
            con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,display_name,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(owner,f'synthetic-{n}@example.test','synthetic',str(n),'Synthetic owner',stamp,stamp))
            con.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',(new_id('session'),owner,'synthetic-token-hash-'+str(n),'synthetic-csrf','2030-01-01T00:00:00+00:00',0,stamp))
        for n,(trip,stop) in enumerate(zip(trips,stops)):
            con.execute('INSERT INTO trips(id,owner_id,title,start_date,end_date,conditions_json,version,deleted_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(trip,users[n//2],'Same private title','2026-11-06','2026-11-09',dump({'party':{'adults':2,'children_status':'unknown','children':[]}}),7,stamp if n==3 else None,stamp,stamp))
            con.execute('INSERT INTO trip_stops VALUES(?,?,?,?,?,?,?,?,?)',(stop,trip,1,'Tokyo','2026-11-06','2026-11-09','Asia/Tokyo',None if n==1 else 'Private synthetic label '+str(n),1))
        con.execute('INSERT INTO source_documents(id,trip_id,display_filename,opaque_path,content_hash,active_generation_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(document,trips[0],'same.eml','opaque/server-id.eml','synthetic-digest',generation,'active',stamp,stamp))
        original=dump({'provider':'Synthetic original hotel','confirmation_number':'SYNTHETIC-ONLY','date':'2026-11-06','kind':'hotel'})
        effective=dump({'provider':'Synthetic user correction','confirmation_number':'SYNTHETIC-ONLY','date':'2026-11-06','kind':'hotel'})
        con.execute('INSERT INTO document_generations(id,document_id,generation_no,parse_version,status,extracted_json,created_at) VALUES(?,?,?,?,?,?,?)',(generation,document,1,'fixture-v1','active',original,stamp))
        con.execute('INSERT INTO bookings(id,trip_id,document_id,generation_id,stable_item_key,kind,status,version,extracted_json,effective_json,date_start,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(booking,trips[0],document,generation,'synthetic-booking-item','hotel','user_confirmed',5,original,effective,'2026-11-06',stamp,stamp))
        con.execute('INSERT INTO booking_overrides(id,booking_id,field_path,value_json,editor_id,revision,created_at) VALUES(?,?,?,?,?,?,?)',(override,booking,'provider',json.dumps('Synthetic user correction'),users[0],4,stamp))
        con.execute('INSERT INTO discovery_conditions VALUES(?,?,?,?,?,?,?)',(trips[0],users[0],3,7,dump({'id':trips[0],'version':7}),dump({'origin':{'label':'Old explicit point','latitude':0,'longitude':0},'party':{'adults':2}}),stamp))
        # Simulate precisely the deployed schema boundary, preserving all old rows.
        for table in ('place_photo_cache','review_run_dependencies','place_review_requests','place_external_links','review_provider_contracts','workspace_drafts','itinerary_generation_drafts','maintenance_status','storage_deletion_receipts'):con.execute('DROP TABLE '+table)
        con.execute('DROP TABLE accommodation_resolutions');con.execute('DROP TABLE trip_accommodations')
        con.execute('UPDATE schema_version SET version=11')
        before=contents(con)
    schema=db.schema;db.close()
    upgraded=Database(path)
    assert upgraded.schema==schema and upgraded.schema_version()==15
    with upgraded.connect() as con:
        assert contents(con)==before
        rows=[dict(row) for row in con.execute('SELECT * FROM trip_accommodations ORDER BY legacy_stop_id')]
        assert len(rows)==2
        by_stop={row['legacy_stop_id']:row for row in rows}
        for n in (0,2):
            row=by_stop[stops[n]]
            assert row['owner_id']==users[n//2] and row['trip_id']==trips[n]
            assert row['input_value']=='Private synthetic label '+str(n)
            assert row['identity_state']=='unresolved' and json.loads(row['identity_json'])=={}
            assert row['dates_confirmed']==0 and row['booking_id'] is None
        assert stops[3] not in by_stop  # deleted travel never becomes a new stay
        for table in ('trip_accommodations','accommodation_resolutions'):
            assert con.execute('SELECT rowsecurity FROM pg_tables WHERE schemaname=? AND tablename=?',(schema,table)).fetchone()[0] is True
        indexes={r[0] for r in con.execute('SELECT indexname FROM pg_indexes WHERE schemaname=?',(schema,))}
        assert {'accommodations_owner_trip','accommodations_stop','accommodation_resolution_owner'}<=indexes
        roles=[r[0] for r in con.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")]
        for role in roles:assert con.execute("SELECT has_schema_privilege(?,?, 'USAGE')",(role,schema)).fetchone()[0] is False
        ids={row['id'] for row in rows}
    upgraded.close();again=Database(path)
    with again.connect() as con:
        assert {r[0] for r in con.execute('SELECT id FROM trip_accommodations')}==ids
        assert contents(con)==before
