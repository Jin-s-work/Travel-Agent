"""Per-call LLM telemetry linked to the cost ledger. No mail or question text.

A row is written after the gateway call returns or fails, in its own short
transaction, never while the external-call slot or a SQLite write lock is held.
Telemetry is best-effort: a failure to record never changes the product result
or the billing state, which the ledger already owns.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from uuid import uuid4

log = logging.getLogger(__name__)

from src.reliability.llm_schema import TELEMETRY_SCHEMA  # noqa: F401 - re-exported
_COLUMNS = ('id', 'call_id', 'owner_id', 'trip_id', 'job_id', 'scope_kind', 'provider', 'model', 'operation',
            'prompt_id', 'prompt_version', 'prompt_hash', 'latency_ms', 'input_tokens', 'output_tokens',
            'cost_micros', 'currency', 'schema_valid', 'repair_attempts', 'outcome', 'replayed', 'created_at')


def _now():
    return datetime.now(timezone.utc).isoformat()


def record(db, context, *, call_key, provider, model, operation, prompt=None, latency_ms=None,
           outcome='ok', replayed=False, schema_valid=None, repair_attempts=0):
    """Insert one row; returns its id or None when telemetry is unavailable."""
    try:
        reserved_key = ('job:' + context.job_id + ':' if context.job_id else 'request:') + call_key
        with db.connect() as con:
            reservation = con.execute(
                'SELECT call_id,state,currency,actual_units_json,actual_cost_micros FROM usage_reservations '
                'WHERE owner_id=? AND scope_kind=? AND scope_id=? AND call_key=? ORDER BY attempt DESC LIMIT 1',
                (context.owner_id, context.scope_kind, context.scope_id, reserved_key)).fetchone()
            units = json.loads(reservation['actual_units_json']) if reservation and reservation['actual_units_json'] else {}
            settled = bool(reservation and reservation['state'] == 'settled')
            row = {
                'id': 'llm_' + uuid4().hex, 'call_id': reservation['call_id'] if reservation else None,
                'owner_id': context.owner_id, 'trip_id': context.trip_id, 'job_id': context.job_id,
                'scope_kind': context.scope_kind, 'provider': provider, 'model': model, 'operation': operation,
                'prompt_id': (prompt or {}).get('id'), 'prompt_version': (prompt or {}).get('version'),
                'prompt_hash': (prompt or {}).get('hash'), 'latency_ms': latency_ms,
                'input_tokens': units.get('input_tokens'), 'output_tokens': units.get('output_tokens'),
                'cost_micros': reservation['actual_cost_micros'] if settled else None,
                'currency': reservation['currency'] if reservation else None,
                'schema_valid': None if schema_valid is None else int(bool(schema_valid)),
                'repair_attempts': int(repair_attempts), 'outcome': outcome, 'replayed': int(bool(replayed)),
                'created_at': _now(),
            }
            con.execute('INSERT INTO llm_calls (' + ','.join(_COLUMNS) + ') VALUES (' + ','.join('?' for _ in _COLUMNS) + ')',
                        tuple(row[column] for column in _COLUMNS))
        return row['id']
    except Exception:  # noqa: BLE001 - telemetry must never alter the call outcome
        log.warning('llm telemetry record skipped', exc_info=False)
        return None


def annotate(db, row_id, **fields):
    """Add validation results known only after parsing (schema_valid, outcome, repairs)."""
    allowed = {'schema_valid', 'repair_attempts', 'outcome'}
    if not row_id or not fields or set(fields) - allowed:
        return
    if 'schema_valid' in fields and fields['schema_valid'] is not None:
        fields['schema_valid'] = int(bool(fields['schema_valid']))
    try:
        with db.connect() as con:
            con.execute('UPDATE llm_calls SET ' + ','.join(f'{key}=?' for key in fields) + ' WHERE id=?',
                        (*fields.values(), row_id))
    except Exception:  # noqa: BLE001
        log.warning('llm telemetry annotate skipped', exc_info=False)


def percentile(values, fraction):
    """Nearest-rank percentile of sorted integers; None for no data."""
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    rank = max(1, -(-len(ordered) * fraction // 1))
    return int(ordered[int(rank) - 1])


def summary(db, *, days=7, now=None):
    """Kept for callers of the first version; the admin view's ledger-consistent
    counting (replays and pre-send blocks excluded from latency and failure
    rate) lives in one place: src.operations.health.llm_call_summary."""
    from src.operations.health import llm_call_summary
    return llm_call_summary(db, days=days, now=now)
