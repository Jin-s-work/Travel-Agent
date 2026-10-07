"""Owner-scoped SQL facts, immutable extraction records and correction overlays.

No method performs network I/O. Parsing/embedding must succeed before activation.
Phase 1 receipts record outcomes but are deliberately not a durable worker queue.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from .db import Database
from .models import BookingCreate, BookingInput, BookingPatch, EventInput, TripCreate, TripPatch, local_to_instant, valid_date


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex}'


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _data(value):
    return value.model_dump(exclude_unset=True) if isinstance(value, BaseModel) else value


def _validate(model, value):
    try:
        return model.model_validate(_data(value)).model_dump()
    except ValidationError as exc:
        raise DomainError('VALIDATION_FAILED', '입력 내용을 확인해 주세요.', details=[
            {'field': '.'.join(map(str, error['loc'])), 'message': error['msg']}
            for error in exc.errors(include_input=False, include_context=False)
        ]) from exc


def _not_found():
    raise DomainError('NOT_FOUND', '자료를 찾을 수 없습니다.', 404)


def _version(actual, expected):
    if actual != expected:
        raise DomainError('VERSION_CONFLICT', '다른 변경 사항이 있습니다. 새로 불러온 뒤 다시 수정해 주세요.', 409, {'current_version': actual})


def _event_facts(event: dict, event_id: str) -> dict:
    event = EventInput.model_validate(event).model_dump()
    return {'id': event_id, **event,
            'start_instant': local_to_instant(event['start_local'], event['start_timezone']),
            'end_instant': local_to_instant(event['end_local'], event['end_timezone'])}


def _attach_event_ids(value: dict, previous: dict | None = None) -> dict:
    result = deepcopy(value)
    prior = (previous or {}).get('events', [])
    available = {event['id']: event for event in prior}
    events = []
    for incoming in result['events']:
        candidates = [event for event in available.values() if all(
            incoming.get(key) == event.get(key) for key in ('event_type', 'start_local', 'end_local', 'location'))]
        if not candidates:
            candidates = [event for event in available.values() if event['event_type'] == incoming['event_type']]
            # Two legs with the same event type cannot be matched by array position.
            if sum(item['event_type'] == incoming['event_type'] for item in result['events']) > 1:
                candidates = []
        selected = candidates[0] if len(candidates) == 1 else None
        event_id = selected['id'] if selected else new_id('evt')
        available.pop(event_id, None)
        events.append(_event_facts(incoming, event_id))
    result['events'] = events
    return result


BOOKING_EDIT_FIELDS = {'place_id', 'party', 'kind', 'provider', 'confirmation_number', 'date', 'date_end', 'time', 'time_end', 'location', 'refund_policy', 'status'}
EVENT_EDIT_FIELDS = {'event_type', 'start_local', 'end_local', 'start_timezone', 'end_timezone', 'location'}


def _get_field(value: dict, path: str):
    parts = path.split('.')
    if len(parts) == 1 and path in BOOKING_EDIT_FIELDS:
        return value.get(path)
    if len(parts) == 3 and parts[0] == 'events' and parts[2] in EVENT_EDIT_FIELDS:
        event = next((event for event in value.get('events', []) if event['id'] == parts[1]), None)
        if event:
            return event.get(parts[2])
    raise DomainError('VALIDATION_FAILED', '수정할 수 없는 필드입니다.', details={'field': path})


def _set_field(value: dict, path: str, changed):
    _get_field(value, path)
    parts = path.split('.')
    if len(parts) == 1:
        value[path] = changed
    else:
        next(event for event in value['events'] if event['id'] == parts[1])[parts[2]] = changed


def _validate_effective(value: dict) -> dict:
    candidate = deepcopy(value)
    ids = [event['id'] for event in candidate['events']]
    candidate['events'] = [{key: event[key] for key in EventInput.model_fields if key in event} for event in candidate['events']]
    validated = _validate(BookingInput, candidate)
    validated['events'] = [_event_facts(event, event_id) for event, event_id in zip(validated['events'], ids)]
    return validated


def _stable_key(value: dict) -> str:
    if value.get('stable_item_key'):
        return value['stable_item_key']
    identity = {key: value.get(key) for key in ('kind', 'provider', 'confirmation_number', 'date', 'date_end', 'location')}
    identity['events'] = [{key: event.get(key) for key in ('event_type', 'start_local', 'end_local', 'location')} for event in value.get('events', [])]
    return hashlib.sha256(dump(identity).encode()).hexdigest()


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def _trip(self, con, user_id, trip_id):
        row = con.execute('SELECT t.* FROM trips t JOIN users u ON u.id=t.owner_id WHERE t.id=? AND t.owner_id=? AND t.deleted_at IS NULL AND u.status=?', (trip_id, user_id, 'active')).fetchone()
        if row is None:
            _not_found()
        return row

    def _bump_trip(self, con, trip_id):
        # A collection build must not publish across any user fact mutation.
        con.execute('UPDATE trips SET version=version+1,updated_at=? WHERE id=?', (utcnow(), trip_id))

    def _trip_dto(self, con, row):
        conditions = json.loads(row['conditions_json'])
        stops = [dict(stop) for stop in con.execute('SELECT * FROM trip_stops WHERE trip_id=? ORDER BY sequence', (row['id'],))]
        for stop in stops:
            stop.pop('trip_id')
            from src.destinations import city_key
            stop['recommendation_supported'] = bool(city_key(stop['city']))
            stop['recommendation_coverage'] = 'checked_per_request'
        return {'id': row['id'], 'title': row['title'], 'start_date': row['start_date'], 'end_date': row['end_date'],
                'party': {**conditions.get('party', {'adults': 1, 'children': []}), 'children_status': conditions.get('party', {}).get('children_status', 'present' if conditions.get('party', {}).get('children') else 'unknown')}, 'stops': stops,
                'active_index_id': row['active_index_id'],
                'version': row['version'], 'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def _write_stops(self, con, trip_id, stops):
        from src.destinations import city_key
        old = {r['id']: dict(r) for r in con.execute('SELECT * FROM trip_stops WHERE trip_id=?', (trip_id,))}
        used = set()
        assignments = []
        for stop in stops:
            ident = stop.get('id')
            if ident and ident not in old: _not_found()
            if not ident:
                # Older clients omit IDs: preserve only an unambiguous same-city/same-sequence row.
                matches = [r['id'] for r in old.values() if r['id'] not in used and r['sequence'] == stop['sequence'] and (city_key(r['city']) or r['city']) == (city_key(stop['city']) or stop['city'])]
                ident = matches[0] if len(matches) == 1 else new_id('stop')
            if ident in used: raise DomainError('VALIDATION_FAILED', '같은 도시 구간을 중복 제출할 수 없습니다.')
            used.add(ident); assignments.append((ident, stop))
        # Preserve IDs while allowing reordering under the (trip,sequence) unique constraint.
        con.execute('UPDATE trip_stops SET sequence=sequence+1000 WHERE trip_id=?', (trip_id,))
        for ident, stop in assignments:
            con.execute('INSERT INTO trip_stops(id,trip_id,sequence,city,start_date,end_date,timezone,base_location,recommendation_supported) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sequence=excluded.sequence,city=excluded.city,start_date=excluded.start_date,end_date=excluded.end_date,timezone=excluded.timezone,base_location=excluded.base_location,recommendation_supported=excluded.recommendation_supported',
                (ident, trip_id, stop['sequence'], stop['city'], stop['start_date'], stop['end_date'], stop['timezone'], stop.get('base_location'), int(bool(city_key(stop['city'])))))
        for ident in old.keys() - used: con.execute('DELETE FROM trip_stops WHERE id=? AND trip_id=?', (ident, trip_id))

    def create_trip(self, user_id, payload):
        value = _validate(TripCreate, payload)
        trip_id, now = new_id('trip'), utcnow()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT id FROM users WHERE id=? AND status=?', (user_id, 'active')).fetchone() is None:
                _not_found()
            con.execute('INSERT INTO trips(id,owner_id,title,start_date,end_date,conditions_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                        (trip_id, user_id, value['title'], value['start_date'], value['end_date'], dump({'party': value['party']}), now, now))
            self._write_stops(con, trip_id, value['stops'])
            return self._trip_dto(con, self._trip(con, user_id, trip_id))

    def list_trips(self, user_id):
        with self.db.connect() as con:
            rows = con.execute('SELECT t.* FROM trips t JOIN users u ON u.id=t.owner_id WHERE t.owner_id=? AND t.deleted_at IS NULL AND u.status=? ORDER BY t.updated_at DESC,t.id', (user_id, 'active')).fetchall()
            return [self._trip_dto(con, row) for row in rows]

    def get_trip(self, user_id, trip_id):
        with self.db.connect() as con:
            return self._trip_dto(con, self._trip(con, user_id, trip_id))

    def update_trip(self, user_id, trip_id, payload):
        patch = _validate(TripPatch, payload)
        submitted = _data(payload)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = self._trip(con, user_id, trip_id)
            _version(row['version'], patch['expected_version'])
            current = self._trip_dto(con, row)
            candidate = {key: current[key] for key in TripCreate.model_fields}
            candidate['stops'] = [{key: stop[key] for key in ('id', 'city', 'sequence', 'start_date', 'end_date', 'timezone', 'base_location')} for stop in current['stops']]
            for key in TripCreate.model_fields:
                if key in submitted:
                    candidate[key] = patch[key]
            if 'stops' not in submitted and len(candidate['stops']) == 1:
                stop = candidate['stops'][0]
                if stop['start_date'] == current['start_date'] and stop['end_date'] == current['end_date']:
                    stop['start_date'], stop['end_date'] = candidate['start_date'], candidate['end_date']
                    if stop['start_date'] != current['start_date'] or stop['end_date'] != current['end_date']:
                        submitted['stops'] = candidate['stops']
            validated = _validate(TripCreate, candidate)
            con.execute('UPDATE trips SET title=?,start_date=?,end_date=?,conditions_json=?,version=version+1,updated_at=? WHERE id=?',
                        (validated['title'], validated['start_date'], validated['end_date'], dump({'party': validated['party']}), utcnow(), trip_id))
            if 'stops' in submitted:
                self._write_stops(con, trip_id, validated['stops'])
            result = self._trip_dto(con, self._trip(con, user_id, trip_id))
            result['out_of_range_booking_ids'] = [r['id'] for r in con.execute('SELECT id FROM bookings WHERE trip_id=? AND deleted_at IS NULL AND (date_start<? OR COALESCE(date_end,date_start)>?)', (trip_id, validated['start_date'], validated['end_date']))]
            return result

    def _tombstone(self, con, kind, target_id, trip_id):
        con.execute('INSERT OR IGNORE INTO deletion_tombstones(target_type,target_id,trip_id,requested_at) VALUES (?,?,?,?)', (kind, target_id, trip_id, utcnow()))

    def delete_trip(self, user_id, trip_id):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._trip(con, user_id, trip_id)
            now = utcnow()
            self._tombstone(con, 'trip', trip_id, trip_id)
            con.execute('UPDATE trips SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?', (now, now, trip_id))
            con.execute('UPDATE source_documents SET deleted_at=?,updated_at=? WHERE trip_id=? AND deleted_at IS NULL', (now, now, trip_id))
            con.execute('UPDATE bookings SET deleted_at=?,updated_at=?,version=version+1 WHERE trip_id=? AND deleted_at IS NULL', (now, now, trip_id))
            return {'id': trip_id, 'status': 'deletion_requested'}

    def _booking(self, con, user_id, trip_id, booking_id):
        self._trip(con, user_id, trip_id)
        row = con.execute('SELECT * FROM bookings WHERE id=? AND trip_id=? AND deleted_at IS NULL', (booking_id, trip_id)).fetchone()
        if row is None:
            _not_found()
        return row

    def _booking_dto(self, con, row):
        effective = json.loads(row['effective_json'])
        source = con.execute('SELECT display_filename FROM source_documents WHERE id=?', (row['document_id'],)).fetchone() if row['document_id'] else None
        overrides = {r['field_path']: json.loads(r['value_json']) for r in con.execute('SELECT * FROM booking_overrides WHERE booking_id=? AND active=1', (row['id'],))}
        from .booking_times import booking_time_conflicts
        time_conflicts = booking_time_conflicts(json.loads(row['extracted_json']), effective, overrides)
        return {**effective, 'time_conflicts':time_conflicts, 'id': row['id'], 'trip_id': row['trip_id'], 'document_id': row['document_id'],
                'source': 'document' if row['document_id'] else 'manual', 'source_file': source['display_filename'] if source else None,
                'version': row['version'], 'extracted': json.loads(row['extracted_json']), 'overrides': overrides,
                'conflicts': json.loads(row['conflicts_json']), 'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def _write_events(self, con, booking_id, trip_id, events):
        con.execute('DELETE FROM booking_events WHERE booking_id=?', (booking_id,))
        for event in events:
            con.execute('INSERT INTO booking_events(id,booking_id,trip_id,event_type,start_local,end_local,start_timezone,end_timezone,start_instant,end_instant,location) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (event['id'], booking_id, trip_id, *(event.get(key) for key in ('event_type', 'start_local', 'end_local', 'start_timezone', 'end_timezone', 'start_instant', 'end_instant', 'location'))))

    def _insert_booking(self, con, trip_id, extracted, document_id=None, generation_id=None):
        booking_id, now = new_id('book'), utcnow()
        con.execute('INSERT INTO bookings(id,trip_id,document_id,generation_id,stable_item_key,kind,status,extracted_json,effective_json,date_start,date_end,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (booking_id, trip_id, document_id, generation_id, _stable_key(extracted), extracted['kind'], extracted['status'], dump(extracted), dump(extracted), extracted['date'], extracted['date_end'], now, now))
        self._write_events(con, booking_id, trip_id, extracted['events'])
        return booking_id

    def create_booking(self, user_id, trip_id, payload):
        value = _attach_event_ids(_validate(BookingCreate, payload))
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._trip(con, user_id, trip_id)
            booking_id = self._insert_booking(con, trip_id, value)
            self._bump_trip(con, trip_id)
            return self._booking_dto(con, self._booking(con, user_id, trip_id, booking_id))

    def get_booking(self, user_id, trip_id, booking_id):
        with self.db.connect() as con:
            return self._booking_dto(con, self._booking(con, user_id, trip_id, booking_id))

    def list_bookings(self, user_id, trip_id, date_from=None, date_to=None, kind=None, status=None):
        try:
            valid_date(date_from)
            valid_date(date_to)
            if date_from and date_to and date_to < date_from:
                raise ValueError('date range')
        except (ValueError, TypeError) as exc:
            raise DomainError('VALIDATION_FAILED', '날짜 범위를 확인해 주세요.') from exc
        clauses, args = ['b.trip_id=?', 'b.deleted_at IS NULL'], [trip_id]
        if kind:
            clauses.append('b.kind=?')
            args.append(kind)
        if status:
            clauses.append('b.status=?')
            args.append(status)
        if date_from or date_to:
            # Civil dates use each event's own zone, not the server's date. The
            # summary date range includes checkout day; explicit events identify
            # checkout vs stay rather than inventing midnight reservations.
            lower, upper = date_from or '0001-01-01', date_to or '9999-12-31'
            clauses.append('(((b.kind IN (\'숙소\',\'hotel\',\'lodging\',\'accommodation\') OR NOT EXISTS (SELECT 1 FROM booking_events dated WHERE dated.booking_id=b.id AND (dated.start_local IS NOT NULL OR dated.end_local IS NOT NULL))) AND b.date_start<=? AND COALESCE(b.date_end,b.date_start)>=?) OR EXISTS (SELECT 1 FROM booking_events e WHERE e.booking_id=b.id AND ((substr(e.start_local,1,10)<=? AND COALESCE(substr(e.end_local,1,10),substr(e.start_local,1,10))>=?) OR (e.start_local IS NULL AND substr(e.end_local,1,10) BETWEEN ? AND ?))))')
            args.extend((upper, lower, upper, lower, lower, upper))
            # A conflicting summary-date correction must still be discoverable
            # on that date; present its conflict rather than only the old leg day.
            clauses[-1] = '(' + clauses[-1] + " OR EXISTS (SELECT 1 FROM booking_overrides o WHERE o.booking_id=b.id AND o.active=1 AND ((o.field_path='date' AND b.date_start BETWEEN ? AND ?) OR (o.field_path='date_end' AND b.date_end BETWEEN ? AND ?))))"
            args.extend((lower, upper, lower, upper))
        with self.db.connect() as con:
            self._trip(con, user_id, trip_id)
            rows = con.execute('SELECT b.* FROM bookings b WHERE ' + ' AND '.join(clauses) + ' ORDER BY COALESCE(b.date_start,\'9999\'),b.created_at,b.id', args).fetchall()
            return [self._booking_dto(con, row) for row in rows]

    def update_booking(self, user_id, trip_id, booking_id, payload):
        patch = _validate(BookingPatch, payload)
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = self._booking(con, user_id, trip_id, booking_id)
            _version(row['version'], patch['expected_version'])
            extracted = json.loads(row['extracted_json'])
            overrides = {r['field_path']: json.loads(r['value_json']) for r in con.execute('SELECT * FROM booking_overrides WHERE booking_id=? AND active=1', (booking_id,))}
            for change in patch['changes']:
                _get_field(extracted, change['field_path'])
                if change['remove_override']:
                    overrides.pop(change['field_path'], None)
                else:
                    overrides[change['field_path']] = change['value']
            effective = deepcopy(extracted)
            for path, value in overrides.items():
                _set_field(effective, path, value)
            effective = _validate_effective(effective)
            now = utcnow()
            for change in patch['changes']:
                con.execute('UPDATE booking_overrides SET active=0 WHERE booking_id=? AND field_path=? AND active=1', (booking_id, change['field_path']))
                if not change['remove_override']:
                    con.execute('INSERT INTO booking_overrides(id,booking_id,field_path,value_json,editor_id,revision,reason,created_at) VALUES (?,?,?,?,?,?,?,?)',
                                (new_id('over'), booking_id, change['field_path'], dump(change['value']), user_id, row['version'] + 1, patch['reason'], now))
            conflicts = [conflict for conflict in json.loads(row['conflicts_json']) if conflict.get('field_path') not in {change['field_path'] for change in patch['changes']}]
            con.execute('UPDATE bookings SET kind=?,status=?,effective_json=?,conflicts_json=?,date_start=?,date_end=?,version=version+1,updated_at=? WHERE id=?',
                        (effective['kind'], effective['status'], dump(effective), dump(conflicts), effective['date'], effective['date_end'], now, booking_id))
            self._write_events(con, booking_id, trip_id, effective['events'])
            self._bump_trip(con, trip_id)
            return self._booking_dto(con, self._booking(con, user_id, trip_id, booking_id))

    def delete_booking(self, user_id, trip_id, booking_id):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._booking(con, user_id, trip_id, booking_id)
            self._tombstone(con, 'booking', booking_id, trip_id)
            con.execute('UPDATE bookings SET deleted_at=?,version=version+1,updated_at=? WHERE id=?', (utcnow(), utcnow(), booking_id))
            self._bump_trip(con, trip_id)
            return {'id': booking_id, 'status': 'deletion_requested'}

    def _document(self, con, user_id, trip_id, document_id):
        self._trip(con, user_id, trip_id)
        row = con.execute('SELECT * FROM source_documents WHERE id=? AND trip_id=? AND deleted_at IS NULL', (document_id, trip_id)).fetchone()
        if row is None:
            _not_found()
        return row

    def _document_dto(self, con, row):
        result = dict(row)
        generation = con.execute('SELECT id,generation_no,parse_version,status,error_code,created_at,completed_at FROM document_generations WHERE document_id=? ORDER BY generation_no DESC LIMIT 1', (row['id'],)).fetchone()
        result['latest_generation'] = dict(generation) if generation else None
        return result

    def create_document(self, user_id, trip_id, display_filename, opaque_path, content_hash, *, connection=None):
        with (nullcontext(connection) if connection is not None else self.db.connect()) as con:
            if connection is None:
                con.execute('BEGIN IMMEDIATE')
            self._trip(con, user_id, trip_id)
            row = con.execute('SELECT * FROM source_documents WHERE trip_id=? AND content_hash=? AND deleted_at IS NULL', (trip_id, content_hash)).fetchone()
            if row:
                return {**self._document_dto(con, row), 'duplicate': True}
            document_id, now = new_id('doc'), utcnow()
            con.execute('INSERT INTO source_documents(id,trip_id,display_filename,opaque_path,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                        (document_id, trip_id, display_filename, str(opaque_path), content_hash, now, now))
            return {**self._document_dto(con, self._document(con, user_id, trip_id, document_id)), 'duplicate': False}

    def get_document(self, user_id, trip_id, document_id):
        with self.db.connect() as con:
            return self._document_dto(con, self._document(con, user_id, trip_id, document_id))

    def list_documents(self, user_id, trip_id):
        with self.db.connect() as con:
            self._trip(con, user_id, trip_id)
            return [self._document_dto(con, row) for row in con.execute('SELECT * FROM source_documents WHERE trip_id=? AND deleted_at IS NULL ORDER BY created_at,id', (trip_id,)).fetchall()]

    def create_generation(self, user_id, trip_id, document_id, parse_version='v1', *, connection=None):
        with (nullcontext(connection) if connection is not None else self.db.connect()) as con:
            if connection is None: con.execute('BEGIN IMMEDIATE')
            self._document(con, user_id, trip_id, document_id)
            number = con.execute('SELECT COALESCE(MAX(generation_no),0)+1 FROM document_generations WHERE document_id=?', (document_id,)).fetchone()[0]
            generation_id = new_id('gen')
            con.execute('INSERT INTO document_generations(id,document_id,generation_no,parse_version,created_at) VALUES (?,?,?,?,?)', (generation_id, document_id, number, parse_version, utcnow()))
            return dict(con.execute('SELECT * FROM document_generations WHERE id=?', (generation_id,)).fetchone())

    def fail_generation(self, user_id, trip_id, document_id, generation_id, error_code='PROCESSING_FAILED', *, connection=None):
        with (nullcontext(connection) if connection is not None else self.db.connect()) as con:
            if connection is None: con.execute('BEGIN IMMEDIATE')
            self._document(con, user_id, trip_id, document_id)
            changed = con.execute('UPDATE document_generations SET status=?,error_code=?,completed_at=? WHERE id=? AND document_id=? AND status=?', ('failed', error_code, utcnow(), generation_id, document_id, 'processing'))
            if not changed.rowcount:
                _not_found()
            con.execute('UPDATE source_documents SET status=CASE WHEN active_generation_id IS NULL THEN ? ELSE status END,updated_at=? WHERE id=?', ('failed', utcnow(), document_id))

    def activate_generation(self, user_id, trip_id, document_id, generation_id, bookings, *, session_id=None, connection=None, bump_trip_version=True):
        if not isinstance(bookings, list) or not bookings or len(bookings) > 100:
            raise DomainError('VALIDATION_FAILED', '문서에는 1~100개의 예약이 필요합니다.')
        values = [_validate(BookingInput, value) for value in bookings]
        # The index manager supplies its short transaction so document facts and
        # the trip's active search pointer commit together. It owns rollback.
        with (nullcontext(connection) if connection is not None else self.db.connect()) as con:
            if connection is None:
                con.execute('BEGIN IMMEDIATE')
            if session_id is not None:
                session = con.execute(
                    'SELECT s.id FROM sessions s JOIN users u ON u.id=s.user_id '
                    'WHERE s.id=? AND s.user_id=? AND s.expires_at>? '
                    'AND s.epoch=u.session_epoch AND u.status=?',
                    (session_id, user_id, utcnow(), 'active'),
                ).fetchone()
                if session is None:
                    raise DomainError('AUTH_REQUIRED', '세션이 만료되었거나 회수되어 결과를 저장할 수 없습니다.', 401)
            document = self._document(con, user_id, trip_id, document_id)
            generation = con.execute('SELECT * FROM document_generations WHERE id=? AND document_id=? AND status=?', (generation_id, document_id, 'processing')).fetchone()
            if generation is None:
                raise DomainError('VERSION_CONFLICT', '이미 처리되었거나 사용할 수 없는 추출 결과입니다.', 409)
            latest = con.execute('SELECT MAX(generation_no) FROM document_generations WHERE document_id=?', (document_id,)).fetchone()[0]
            if generation['generation_no'] != latest:
                raise DomainError('VERSION_CONFLICT', '더 최신 추출 작업이 있습니다.', 409)
            previous = con.execute('SELECT * FROM bookings WHERE document_id=?', (document_id,)).fetchall()
            keys = [_stable_key(value) for value in values]
            ambiguous = len(set(keys)) != len(keys)
            matched_ids, prepared = set(), []
            for value, key in zip(values, keys):
                matches = [row for row in previous if row['stable_item_key'] == key]
                if not matches and value.get('confirmation_number'):
                    matches = [row for row in previous if json.loads(row['extracted_json']).get('confirmation_number') == value['confirmation_number'] and row['kind'] == value['kind']]
                if len(matches) > 1:
                    ambiguous = True
                    continue
                match = matches[0] if matches else None
                if match and match['id'] in matched_ids:
                    ambiguous = True
                    continue
                if match:
                    matched_ids.add(match['id'])
                    # User-deleted bookings remain deleted across re-extraction.
                    if match['deleted_at'] is not None:
                        continue
                elif previous and not (value.get('confirmation_number') and all(json.loads(row['extracted_json']).get('confirmation_number') != value['confirmation_number'] for row in previous)):
                    ambiguous = True
                    continue
                old = json.loads(match['extracted_json']) if match else None
                extracted = _attach_event_ids(value, old)
                effective, conflicts = deepcopy(extracted), []
                if match:
                    overlays = con.execute('SELECT * FROM booking_overrides WHERE booking_id=? AND active=1', (match['id'],)).fetchall()
                    for overlay in overlays:
                        path, corrected = overlay['field_path'], json.loads(overlay['value_json'])
                        try:
                            new_fact = _get_field(extracted, path)
                            old_fact = _get_field(old, path)
                            _set_field(effective, path, corrected)
                        except DomainError:
                            ambiguous = True
                            break
                        if new_fact != old_fact and new_fact != corrected:
                            conflicts.append({'field_path': path, 'previous_extracted': old_fact, 'new_extracted': new_fact, 'override': corrected})
                    try:
                        effective = _validate_effective(effective)
                    except DomainError:
                        ambiguous = True
                prepared.append((match, key, extracted, effective, conflicts))
            if ambiguous:
                con.execute('UPDATE document_generations SET status=?,extracted_json=?,error_code=?,completed_at=? WHERE id=?', ('needs_review', dump(values), 'AMBIGUOUS_MATCH', utcnow(), generation_id))
                con.execute('UPDATE source_documents SET status=?,updated_at=? WHERE id=?', ('needs_review', utcnow(), document_id))
                return [self._booking_dto(con, row) for row in previous if row['deleted_at'] is None]
            result_ids = []
            for match, key, extracted, effective, conflicts in prepared:
                if match is None:
                    booking_id = self._insert_booking(con, trip_id, extracted, document_id, generation_id)
                else:
                    booking_id = match['id']
                    con.execute('UPDATE bookings SET generation_id=?,stable_item_key=?,kind=?,status=?,extracted_json=?,effective_json=?,conflicts_json=?,date_start=?,date_end=?,version=version+1,updated_at=? WHERE id=?',
                                (generation_id, key, effective['kind'], effective['status'], dump(extracted), dump(effective), dump(conflicts), effective['date'], effective['date_end'], utcnow(), booking_id))
                    self._write_events(con, booking_id, trip_id, effective['events'])
                result_ids.append(booking_id)
            for missing in previous:
                if missing['id'] not in matched_ids and missing['deleted_at'] is None:
                    effective = json.loads(missing['effective_json'])
                    effective['status'] = 'needs_review'
                    con.execute('UPDATE bookings SET status=?,effective_json=?,conflicts_json=?,version=version+1,updated_at=? WHERE id=?', ('needs_review', dump(effective), dump([{'reason': 'missing_in_new_extraction'}]), utcnow(), missing['id']))
                    result_ids.append(missing['id'])
            con.execute('UPDATE document_generations SET status=?,extracted_json=?,completed_at=? WHERE id=?', ('active', dump(values), utcnow(), generation_id))
            if document['active_generation_id']:
                con.execute('UPDATE document_generations SET status=? WHERE id=?', ('superseded', document['active_generation_id']))
            con.execute('UPDATE source_documents SET active_generation_id=?,status=?,updated_at=? WHERE id=?', (generation_id, 'ready', utcnow(), document_id))
            if bump_trip_version:
                self._bump_trip(con, trip_id)
            return [self._booking_dto(con, self._booking(con, user_id, trip_id, booking_id)) for booking_id in result_ids]

    def delete_document(self, user_id, trip_id, document_id):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._document(con, user_id, trip_id, document_id)
            self._tombstone(con, 'document', document_id, trip_id)
            for row in con.execute('SELECT id FROM bookings WHERE document_id=? AND deleted_at IS NULL', (document_id,)):
                self._tombstone(con, 'booking', row['id'], trip_id)
            now = utcnow()
            con.execute('UPDATE source_documents SET deleted_at=?,updated_at=? WHERE id=?', (now, now, document_id))
            con.execute('UPDATE bookings SET deleted_at=?,updated_at=?,version=version+1 WHERE document_id=? AND deleted_at IS NULL', (now, now, document_id))
            self._bump_trip(con, trip_id)
            return {'id': document_id, 'status': 'deletion_requested'}

    def _receipt_dto(self, row):
        result = dict(row)
        result['job_id'] = result['id']
        result['result'] = json.loads(result.pop('result_json'))
        result.pop('payload_hash')
        return result

    def create_receipt(self, user_id, trip_id, payload_hash, idempotency_key=None):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._trip(con, user_id, trip_id)
            if idempotency_key:
                existing = con.execute('SELECT * FROM processing_receipts WHERE user_id=? AND trip_id=? AND idempotency_key=?', (user_id, trip_id, idempotency_key)).fetchone()
                if existing:
                    if existing['payload_hash'] != payload_hash:
                        raise DomainError('VERSION_CONFLICT', '같은 요청 키에 다른 파일을 사용할 수 없습니다.', 409)
                    return {**self._receipt_dto(existing), 'reused': True}
            receipt_id, now = new_id('receipt'), utcnow()
            con.execute('INSERT INTO processing_receipts(id,user_id,trip_id,idempotency_key,payload_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', (receipt_id, user_id, trip_id, idempotency_key, payload_hash, now, now))
            return {**self._receipt_dto(con.execute('SELECT * FROM processing_receipts WHERE id=?', (receipt_id,)).fetchone()), 'reused': False}

    def get_receipt(self, user_id, trip_id, receipt_id):
        with self.db.connect() as con:
            self._trip(con, user_id, trip_id)
            row = con.execute('SELECT * FROM processing_receipts WHERE id=? AND user_id=? AND trip_id=?', (receipt_id, user_id, trip_id)).fetchone()
            if row is None:
                _not_found()
            return self._receipt_dto(row)

    def update_receipt(self, user_id, trip_id, receipt_id, status, result):
        if status not in ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled'):
            raise DomainError('VALIDATION_FAILED', '올바르지 않은 처리 상태입니다.')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._trip(con, user_id, trip_id)
            updated = con.execute('UPDATE processing_receipts SET status=?,result_json=?,updated_at=? WHERE id=? AND user_id=? AND trip_id=?', (status, dump(result), utcnow(), receipt_id, user_id, trip_id))
            if not updated.rowcount:
                _not_found()
            return self._receipt_dto(con.execute('SELECT * FROM processing_receipts WHERE id=?', (receipt_id,)).fetchone())
