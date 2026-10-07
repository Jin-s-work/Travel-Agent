"""Synthetic, temporary SQL fixtures for ownership, corrections and activation."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest
from pydantic import ValidationError

from src.foundation.db import Database
from src.foundation.models import BookingCreate, EventInput, TripCreate
from src.foundation.repository import DomainError, Repository, utcnow


@pytest.fixture
def storage(tmp_path):
    db = Database(tmp_path / 'service' / 'private.sqlite3')
    now = utcnow()
    with db.connect() as con:
        for user in ('A', 'B'):
            con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES (?,?,?,?,?,?)', (user, f'{user}@example.invalid', 'test', user, now, now))
    repo = Repository(db)
    trips = {(user, city): repo.create_trip(user, {
        'title': f'{city} 여행', 'start_date': '2026-11-06', 'end_date': '2026-11-09',
        'party': {'adults': 2, 'children': [{'age': None}]},
        'stops': [{'city': city, 'sequence': 1, 'start_date': '2026-11-06', 'end_date': '2026-11-09',
                   'timezone': 'Asia/Tokyo' if city == 'Tokyo' else 'Europe/Madrid'}],
    }) for user in ('A', 'B') for city in ('Tokyo', 'Barcelona')}
    return db, repo, trips


def document(repo, user, trip, content_hash='hash'):
    return repo.create_document(user, trip, 'same-name.eml', '/private/synthetic/server-id.eml', content_hash)


def activate(repo, user, trip, doc, bookings):
    generation = repo.create_generation(user, trip, doc)
    return repo.activate_generation(user, trip, doc, generation['id'], bookings)


def flight(code, start, end, provider='Synthetic Air'):
    return {'kind': '항공', 'provider': provider, 'confirmation_number': code,
            'date': start[:10], 'date_end': end[:10], 'status': 'source_verified',
            'events': [{'event_type': 'flight', 'start_local': start, 'end_local': end,
                        'start_timezone': 'Asia/Seoul', 'end_timezone': 'Asia/Tokyo', 'location': 'ICN → HND'}]}


def test_versioned_schema_and_connection_enforces_foreign_keys(storage):
    db, _, _ = storage
    with db.connect() as con:
        from src.foundation.db import SCHEMA_VERSION
        assert con.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert con.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES ('s','missing','h','c','x',0,'x')")
    Database(db.path)


def test_trip_and_document_isolation_two_users_two_trips(storage):
    _, repo, trips = storage
    at, ab, bt = (trips[key]['id'] for key in [('A', 'Tokyo'), ('A', 'Barcelona'), ('B', 'Tokyo')])
    assert len(repo.list_trips('A')) == len(repo.list_trips('B')) == 2
    first = document(repo, 'A', at)
    assert document(repo, 'A', at)['duplicate'] is True
    second, third = document(repo, 'A', ab), document(repo, 'B', bt)
    assert len({first['id'], second['id'], third['id']}) == 3
    assert first['display_filename'] == second['display_filename']
    for operation in (lambda: repo.get_trip('B', at), lambda: repo.list_bookings('B', at),
                      lambda: repo.get_document('B', at, first['id']), lambda: repo.get_document('A', ab, first['id']),
                      lambda: repo.delete_trip('B', at)):
        with pytest.raises(DomainError) as error:
            operation()
        assert error.value.status == 404


def test_extra_owner_and_invalid_dates_rejected_without_write(storage):
    _, repo, _ = storage
    before = len(repo.list_trips('A'))
    with pytest.raises(DomainError):
        repo.create_trip('A', {'title': 'spoof', 'owner_id': 'B', 'start_date': '2026-11-06', 'end_date': '2026-11-09'})
    with pytest.raises(ValidationError):
        TripCreate(title='bad', start_date='2026-11-09', end_date='2026-11-06')
    assert len(repo.list_trips('A')) == before


def test_date_only_and_unknown_zone_are_not_midnight_instants(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    booking = repo.create_booking('A', tid, {'date': '2026-11-07', 'provider': 'Day Ticket',
        'events': [{'start_local': '2026-11-07', 'start_timezone': 'Asia/Tokyo'},
                   {'event_type': 'meeting', 'start_local': '2026-11-07T19:30:00'}]})
    assert all(event['start_instant'] is None for event in booking['events'])
    assert len(repo.list_bookings('A', tid, date_from='2026-11-07', date_to='2026-11-07')) == 1
    assert booking['source'] == 'manual' and booking['document_id'] is None


@pytest.mark.parametrize('local', ['2026-03-29T02:30:00', '2026-10-25T02:30:00'])
def test_madrid_nonexistent_and_ambiguous_time_require_confirmation(local):
    with pytest.raises(ValidationError):
        EventInput(start_local=local, start_timezone='Europe/Madrid')


def test_eight_extracted_and_one_manual_all_returned_by_sql(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    parsed = [{'kind': '투어', 'date': '2026-11-07', 'provider': f'Visit {i}', 'stable_item_key': f'stable{i}'} for i in range(8)]
    assert len(activate(repo, 'A', tid, doc['id'], parsed)) == 8
    repo.create_booking('A', tid, {'date': '2026-11-07', 'provider': 'Manual dinner', 'status': 'user_confirmed'})
    records = repo.list_bookings('A', tid, date_from='2026-11-07', date_to='2026-11-07')
    assert len(records) == 9
    assert len(repo.list_bookings('A', tid, date_from='2026-11-08', date_to='2026-11-08')) == 0
    assert repo.list_bookings('A', trips['A', 'Barcelona']['id']) == []


def test_roundtrip_reextract_keeps_ids_user_corrections_and_conflict(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    parsed = [flight('OUT', '2026-11-06T09:00:00', '2026-11-06T11:00:00'),
              flight('RETURN', '2026-11-09T18:00:00', '2026-11-09T20:00:00')]
    initial = activate(repo, 'A', tid, doc['id'], parsed)
    eid = initial[0]['events'][0]['id']
    changed = repo.update_booking('A', tid, initial[0]['id'], {'expected_version': 1, 'reason': 'Rebooked',
        'changes': [{'field_path': f'events.{eid}.start_local', 'value': '2026-11-06T09:30:00'},
                    {'field_path': 'provider', 'value': 'Corrected Air'}]})
    parsed[0]['provider'] = 'New extracted Air'
    parsed[0]['events'][0]['start_local'] = '2026-11-06T09:15:00'
    again = activate(repo, 'A', tid, doc['id'], parsed)
    assert {b['id'] for b in initial} == {b['id'] for b in again}
    out = next(b for b in again if b['confirmation_number'] == 'OUT')
    assert out['provider'] == 'Corrected Air'
    assert out['extracted']['provider'] == 'New extracted Air'
    assert out['events'][0]['id'] == eid
    assert out['events'][0]['start_local'] == '2026-11-06T09:30:00'
    assert out['events'][0]['start_instant'] == '2026-11-06T00:30:00+00:00'
    assert len(out['conflicts']) == 2 and out['version'] > changed['version']


def test_invalid_correction_rolls_back_and_versions_conflict(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    booking = repo.create_booking('A', tid, {'date': '2026-11-07'})
    with pytest.raises(DomainError):
        repo.update_booking('A', tid, booking['id'], {'expected_version': 1, 'changes': [{'field_path': 'date', 'value': 'not-date'}]})
    assert repo.get_booking('A', tid, booking['id'])['version'] == 1
    def edit(name):
        try:
            return repo.update_booking('A', tid, booking['id'], {'expected_version': 1, 'changes': [{'field_path': 'provider', 'value': name}]})
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(edit, ('First', 'Second')))
    assert sum(result == 409 for result in results) == 1
    assert repo.get_booking('A', tid, booking['id'])['version'] == 2


def test_parser_failure_and_invalid_activation_preserve_active_generation(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    initial = activate(repo, 'A', tid, doc['id'], [{'provider': 'Original', 'date': '2026-11-07'}])
    active_id = repo.get_document('A', tid, doc['id'])['active_generation_id']
    failed = repo.create_generation('A', tid, doc['id'])
    repo.fail_generation('A', tid, doc['id'], failed['id'], 'EMBEDDING_FAILED')
    assert repo.get_document('A', tid, doc['id'])['active_generation_id'] == active_id
    details = repo.get_document('A', tid, doc['id'])
    assert details['status'] == 'ready'
    assert details['latest_generation']['status'] == 'failed'
    assert details['latest_generation']['error_code'] == 'EMBEDDING_FAILED'
    assert 'extracted_json' not in details['latest_generation']
    assert repo.list_documents('A', tid)[0]['latest_generation']['id'] == failed['id']
    invalid = repo.create_generation('A', tid, doc['id'])
    with pytest.raises(DomainError):
        repo.activate_generation('A', tid, doc['id'], invalid['id'], [{'date': 'invalid'}])
    assert repo.list_bookings('A', tid)[0]['id'] == initial[0]['id']
    assert repo.list_bookings('A', tid)[0]['provider'] == 'Original'


def test_ambiguous_reextraction_keeps_old_without_duplicate(storage):
    db, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    old = activate(repo, 'A', tid, doc['id'], [{'provider': 'A', 'date': '2026-11-07'}, {'provider': 'B', 'date': '2026-11-08'}])
    generation = repo.create_generation('A', tid, doc['id'])
    result = repo.activate_generation('A', tid, doc['id'], generation['id'], [{'provider': 'Changed', 'date': '2026-11-07'}])
    assert {b['id'] for b in result} == {b['id'] for b in old}
    assert len(repo.list_bookings('A', tid)) == 2
    assert repo.get_document('A', tid, doc['id'])['status'] == 'needs_review'
    with db.connect() as con:
        assert con.execute('SELECT status FROM document_generations WHERE id=?', (generation['id'],)).fetchone()[0] == 'needs_review'


def test_single_booking_delete_preserves_document_sibling_and_does_not_resurrect(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    parsed = [{'provider': 'A', 'stable_item_key': 'a'}, {'provider': 'B', 'stable_item_key': 'b'}]
    records = activate(repo, 'A', tid, doc['id'], parsed)
    repo.delete_booking('A', tid, records[0]['id'])
    assert repo.get_document('A', tid, doc['id'])['id'] == doc['id']
    again = activate(repo, 'A', tid, doc['id'], parsed)
    assert len(again) == len(repo.list_bookings('A', tid)) == 1
    assert again[0]['id'] == records[1]['id']


def test_deleted_trip_blocks_late_activation(storage):
    db, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    generation = repo.create_generation('A', tid, doc['id'])
    repo.delete_trip('A', tid)
    with pytest.raises(DomainError) as error:
        repo.activate_generation('A', tid, doc['id'], generation['id'], [{'provider': 'Late result'}])
    assert error.value.status == 404
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM bookings WHERE trip_id=?', (tid,)).fetchone()[0] == 0
        assert con.execute('SELECT target_type FROM deletion_tombstones WHERE target_id=?', (tid,)).fetchone()[0] == 'trip'


def test_trip_date_change_preserves_bookings_and_reports_impact(storage):
    _, repo, trips = storage
    trip = trips['A', 'Tokyo']
    booking = repo.create_booking('A', trip['id'], {'date': '2026-11-09'})
    changed = repo.update_trip('A', trip['id'], {'expected_version': repo.get_trip('A', trip['id'])['version'], 'end_date': '2026-11-08',
        'stops': [{'city': 'Tokyo', 'sequence': 1, 'start_date': '2026-11-06', 'end_date': '2026-11-08', 'timezone': 'Asia/Tokyo'}]})
    assert changed['out_of_range_booking_ids'] == [booking['id']]
    assert repo.get_booking('A', trip['id'], booking['id'])['date'] == '2026-11-09'


def test_receipts_are_scoped_idempotent_and_survive_new_repository(storage):
    db, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    first = repo.create_receipt('A', tid, 'payload', 'request-key')
    assert not first['reused']
    assert repo.create_receipt('A', tid, 'payload', 'request-key')['reused']
    with pytest.raises(DomainError) as conflict:
        repo.create_receipt('A', tid, 'different', 'request-key')
    assert conflict.value.status == 409
    repo.update_receipt('A', tid, first['id'], 'succeeded', {'files': [{'status': 'ready'}]})
    reloaded = Repository(Database(db.path)).get_receipt('A', tid, first['id'])
    assert reloaded['status'] == 'succeeded'
    with pytest.raises(DomainError) as forbidden:
        repo.get_receipt('B', tid, first['id'])
    assert forbidden.value.status == 404


def test_override_removal_restores_original_and_keeps_history(storage):
    db, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    booking = repo.create_booking('A', tid, {'provider': 'Original'})
    changed = repo.update_booking('A', tid, booking['id'], {'expected_version': 1, 'changes': [{'field_path': 'provider', 'value': 'Changed'}]})
    restored = repo.update_booking('A', tid, booking['id'], {'expected_version': changed['version'], 'changes': [{'field_path': 'provider', 'value': None, 'remove_override': True}]})
    assert restored['provider'] == 'Original' and restored['overrides'] == {}
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM booking_overrides WHERE booking_id=?', (booking['id'],)).fetchone()[0] == 1


def test_booking_events_include_cross_zone_dates_and_checkout_day(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    booking = repo.create_booking('A', tid, {'kind': '숙소', 'date': '2026-11-06', 'date_end': '2026-11-08',
        'events': [{'event_type': 'checkout', 'start_local': '2026-11-08T11:00:00', 'start_timezone': 'Asia/Tokyo'}]})
    assert repo.list_bookings('A', tid, date_from='2026-11-08', date_to='2026-11-08')[0]['id'] == booking['id']
    assert repo.list_bookings('A', tid, date_from='2026-11-09', date_to='2026-11-09') == []


def test_corrected_event_date_overrides_stale_extracted_summary_for_day_query(storage):
    _, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    booking = repo.create_booking('A', tid, {'kind': '투어', 'date': '2026-11-07',
        'events': [{'event_type': 'visit', 'start_local': '2026-11-07T11:00:00', 'start_timezone': 'Asia/Tokyo'}]})
    event_id = booking['events'][0]['id']
    repo.update_booking('A', tid, booking['id'], {'expected_version': 1,
        'changes': [{'field_path': f'events.{event_id}.start_local', 'value': '2026-11-08T11:00:00'}]})
    assert repo.list_bookings('A', tid, date_from='2026-11-07', date_to='2026-11-07') == []
    assert repo.list_bookings('A', tid, date_from='2026-11-08', date_to='2026-11-08')[0]['id'] == booking['id']


def test_legacy_inventory_is_read_only_and_never_infers_ownership(tmp_path):
    from src.foundation.cli import inventory
    root = tmp_path / 'legacy'
    root.mkdir()
    (root / 'one.txt').write_text('Synthetic receipt with PRIVATE-BODY')
    (root / 'two.txt').write_text('Synthetic receipt with PRIVATE-BODY')
    (root / 'unsupported.html').write_text('<p>not a mail</p>')
    (root / 'outside.txt').symlink_to(tmp_path / 'absent.txt')
    before = {p.name: p.stat().st_mtime_ns for p in root.iterdir() if not p.is_symlink()}
    result = inventory(root)
    assert result['file_count'] == result['unresolved_owner_count'] == 2
    assert result['booking_count'] is None
    assert len(result['duplicates']) == 1
    assert 'PRIVATE-BODY' not in json.dumps(result)
    assert all(item['parse_status'] == 'not_run' for item in result['files'])
    assert before == {p.name: p.stat().st_mtime_ns for p in root.iterdir() if not p.is_symlink()}


def test_backup_restore_checksums_new_paths_and_later_deletions(storage, tmp_path):
    from src.foundation.cli import backup, restore, verify_backup
    from src.foundation.documents import DocumentService
    from src.foundation.settings import Settings
    db, repo, trips = storage
    settings = Settings(database_path=db.path, documents_dir=tmp_path / 'documents', vectors_dir=tmp_path / 'vectors')
    service = DocumentService(repo, settings)
    tid = trips['A', 'Tokyo']['id']
    doc = service.save('A', tid, 'legacy.txt', b'Synthetic document')
    activate(repo, 'A', tid, doc['id'], [{'provider': 'Saved', 'date': '2026-11-07'}])
    settings.vectors_dir.mkdir()
    (settings.vectors_dir / 'synthetic-vector.txt').write_text('fake index')
    backup_dir = tmp_path / 'backup'
    assert backup(settings, backup_dir)['state'] == 'complete'
    verify_backup(backup_dir)
    repo.delete_trip('A', tid)
    with db.connect() as con:
        con.execute("UPDATE users SET status='disabled' WHERE id='B'")
    result = restore(backup_dir, tmp_path / 'restored', db.path)
    restored_repo = Repository(Database(result['database_path']))
    assert result['sessions_invalidated'] is True
    with pytest.raises(DomainError) as error:
        restored_repo.get_trip('A', tid)
    assert error.value.status == 404
    with restored_repo.db.connect() as con:
        path = con.execute('SELECT opaque_path FROM source_documents WHERE id=?', (doc['id'],)).fetchone()[0]
        assert path.startswith(str(tmp_path / 'restored' / 'documents'))
        assert con.execute("SELECT status FROM users WHERE id='B'").fetchone()[0] == 'disabled'
    with pytest.raises(ValueError):
        restore(backup_dir, tmp_path / 'restored', db.path)
    (backup_dir / 'vectors' / 'synthetic-vector.txt').write_text('tampered')
    with pytest.raises(ValueError, match='checksum'):
        verify_backup(backup_dir)


def test_migration_requires_explicit_mapping_preserves_original_and_is_idempotent(storage, tmp_path):
    from src.foundation.cli import backup, import_legacy, inventory
    from src.foundation.documents import DocumentService
    from src.foundation.settings import Settings
    db, repo, trips = storage
    settings = Settings(database_path=db.path, documents_dir=tmp_path / 'documents', vectors_dir=tmp_path / 'vectors')
    backup_dir = tmp_path / 'before-import'
    backup(settings, backup_dir)
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    original = legacy / 'receipt.txt'
    original.write_text('Synthetic Tokyo reservation')
    record = inventory(legacy)['files'][0]
    mapping = {'schema_version': 1, 'run_id': 'synthetic-run-1', 'source_root': str(legacy), 'files': [{
        'relative_path': record['relative_path'], 'sha256': record['sha256'], 'user_id': 'A', 'trip_id': trips['A', 'Tokyo']['id']}]}
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps(mapping))
    calls = []
    class FakeVector:
        def add(self, **kwargs):
            calls.append('index')
    service = DocumentService(repo, settings, parser=lambda _: [{'provider': 'Imported', 'date': '2026-11-07'}],
                              embedder=lambda _: [[0.1, 0.2]], vector_factory=lambda _: FakeVector())
    first = import_legacy(settings, path, backup_dir, document_service=service)
    assert first['state'] == 'imported_unprocessed' and calls == []
    assert repo.list_bookings('A', trips['A', 'Tokyo']['id']) == []
    processed = import_legacy(settings, path, backup_dir, process=True, document_service=service)
    assert processed['state'] == 'complete'
    repeated = import_legacy(settings, path, backup_dir, process=True, document_service=service)
    assert repeated['state'] == 'complete' and calls == ['index']
    assert len(repo.list_bookings('A', trips['A', 'Tokyo']['id'])) == 1
    assert original.read_text() == 'Synthetic Tokyo reservation'
    assert repo.list_bookings('A', trips['A', 'Barcelona']['id']) == []
    mapping['files'][0]['trip_id'] = trips['A', 'Barcelona']['id']
    path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match='different immutable mapping'):
        import_legacy(settings, path, backup_dir, document_service=service)
    mapping['files'][0]['trip_id'] = trips['A', 'Tokyo']['id']
    path.write_text(json.dumps(mapping))
    repo.delete_document('A', trips['A', 'Tokyo']['id'], processed['files'][0]['document_id'])
    with pytest.raises(ValueError, match='previously imported document was deleted'):
        import_legacy(settings, path, backup_dir, process=True, document_service=service)
    assert repo.list_documents('A', trips['A', 'Tokyo']['id']) == []


@pytest.mark.parametrize('session_state', ['deleted', 'expired', 'wrong_owner', 'old_epoch', 'disabled'])
def test_background_activation_requires_original_live_session(storage, session_state):
    from datetime import datetime, timedelta, timezone
    db, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    original = activate(repo, 'A', tid, doc['id'], [{'provider': 'Original', 'stable_item_key': 'same'}])
    generation = repo.create_generation('A', tid, doc['id'])
    expiry = (datetime.now(timezone.utc) + timedelta(hours=-1 if session_state == 'expired' else 1)).isoformat()
    with db.connect() as con:
        if session_state != 'deleted':
            con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES (?,?,?,?,?,?,?)',
                        ('original-session', 'B' if session_state == 'wrong_owner' else 'A', 'hash', 'csrf', expiry, 0, utcnow()))
        if session_state == 'old_epoch':
            con.execute("UPDATE users SET session_epoch=1 WHERE id='A'")
        if session_state == 'disabled':
            con.execute("UPDATE users SET status='disabled' WHERE id='A'")
    with pytest.raises(DomainError) as error:
        repo.activate_generation('A', tid, doc['id'], generation['id'], [{'provider': 'New', 'stable_item_key': 'same'}], session_id='original-session')
    assert error.value.status == 401 and error.value.code == 'AUTH_REQUIRED'
    with db.connect() as con:
        record = con.execute('SELECT effective_json FROM bookings WHERE id=?', (original[0]['id'],)).fetchone()
        assert json.loads(record[0])['provider'] == 'Original'
        assert con.execute('SELECT status FROM document_generations WHERE id=?', (generation['id'],)).fetchone()[0] == 'processing'


def test_background_activation_accepts_live_matching_session(storage):
    from datetime import datetime, timedelta, timezone
    db, repo, trips = storage
    tid = trips['A', 'Tokyo']['id']
    doc = document(repo, 'A', tid)
    generation = repo.create_generation('A', tid, doc['id'])
    with db.connect() as con:
        con.execute('INSERT INTO sessions(id,user_id,token_hash,csrf_token,expires_at,epoch,created_at) VALUES (?,?,?,?,?,?,?)',
                    ('live', 'A', 'hash', 'csrf', (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), 0, utcnow()))
    result = repo.activate_generation('A', tid, doc['id'], generation['id'], [{'provider': 'New'}], session_id='live')
    assert result[0]['provider'] == 'New'
