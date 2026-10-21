"""Score mail extraction against the synthetic `mail_gold_v1` set.

    python -m src.evals.mail --mode local  --out data/eval_runs/mail-local.json
    python -m src.evals.mail --mode replay --out data/eval_runs/mail-replay.json
    python -m src.evals.mail --mode live --max-cost-usd 0.50 --max-cases 5 \
        --pricing deploy/render-supabase/pricing-openai.json --out data/eval_runs/mail-live.json

* local  — the offline rule parser (`parse_local_document`). No external call.
* replay — the real `parser.extract_document_reservations` path (metering,
  repair once) answered by hand-made replay records. No key, no network. Its
  numbers test the pipeline and the scorer; they are NOT model quality.
* live   — the same path against OpenAI through the metered budget gateway with
  a hard cost cap. Implemented for the owner to run; never run by CI or tests.

The JSON is for machines (gate); the `.md` next to it is for people. Every
output says `synthetic: true`. Scoring rules: evals/mail/README.md.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unicodedata

from src.config import EXTRACTION_MODEL, PROJECT_ROOT
from src.evals.gold import GOLD_DIR, GOLD_VERSION, SCORED_FIELDS, gold_content_sha256, load_gold

SUITE = GOLD_VERSION
DEFAULT_REPLAY_DIR = PROJECT_ROOT / 'evals' / 'replay' / 'mail'
TYPE_MAP = {'항공': 'flight', '숙소': 'stay', '렌터카': 'car_rental', '투어': 'tour',
            'restaurant': 'restaurant', '식당': 'restaurant'}
FLIGHT_EVENTS = ('flight', 'outbound', 'return')
REVIEW_FIELDS = {
    'DATE_NEEDS_REVIEW': ('start_local',),
    'AMBIGUOUS_LOCAL_TIME': ('start_local', 'end_local'),
    'EVENT_ORDER_UNCONFIRMED': ('end_local',),
    'REQUEST_NOT_CONFIRMATION': ('start_local', 'status'),
    'RESERVATION_CANCELLED_REVIEW': ('status',),
}
QUALITY_CLAIM = {
    'local': 'rule_parser_baseline_on_synthetic_gold',
    'replay': 'pipeline_test_not_model_quality',
    'live': 'live_model_on_synthetic_gold',
}
EVAL_OWNER = 'eval-user'
# Fields the AI document schema (parser.DOCUMENT_RESERVATIONS_SCHEMA) cannot
# produce. In AI mode (replay/live) they are always null, so a forbidden rule
# whose decoy sits on one of them can never fire and a must_review item on one
# of them would be satisfied for free: both are reported as unmeasurable instead.
AI_ABSENT_FIELDS = ('status', 'party.adults', 'party.children', 'amount', 'currency', 'deposit')
# Fields the rule parser (src.foundation.local_mail.parse_local_document) never
# emits: a local forbidden rule on one of them (deposit_as_total on `amount`)
# can never fire, so it is unmeasurable in local mode too, not "measured, 0".
# (A must_review item on them is still counted as missed in local mode.)
LOCAL_ABSENT_FIELDS = ('amount', 'currency', 'deposit')


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def _minute(value):
    """'YYYY-MM-DDTHH:MM:SS' → 'YYYY-MM-DDTHH:MM'; dates stay dates."""
    if not isinstance(value, str) or not value:
        return None
    return value[:16] if len(value) > 16 else value


def _join(day, clock):
    if not day:
        return None
    return f'{day}T{clock[:5]}' if clock else day


def _empty_unit():
    return {field: None for field in SCORED_FIELDS}


def gold_units(case):
    """Expected bookings flattened to scored fields (+ the unscored set)."""
    units = []
    for booking in case['expected_bookings']:
        unit = _empty_unit()
        for field in SCORED_FIELDS:
            if field.startswith('party.'):
                party = booking.get('party') or {}
                unit[field] = party.get(field.split('.', 1)[1])
            else:
                unit[field] = booking.get(field)
        unscored = set(booking.get('unscored') or [])
        if 'party' in unscored:
            unscored |= {'party.adults', 'party.children'}
        unit['unscored'] = unscored
        units.append(unit)
    return units


def _status(review_reasons, ai):
    if ai:
        return None  # The AI document schema carries no status.
    reasons = set(review_reasons or ())
    if 'RESERVATION_CANCELLED_REVIEW' in reasons:
        return 'cancelled'
    if 'REQUEST_NOT_CONFIRMATION' in reasons:
        return 'changed'
    return 'confirmed'


def _party(booking):
    party = booking.get('party')
    if not isinstance(party, dict):
        return None, None
    status = party.get('children_status')
    children = party.get('children')
    if status == 'none':
        kids = 0
    elif status == 'present':
        kids = len(children or [])
    else:
        kids = None
    return party.get('adults'), kids


def predicted_units(bookings, review_reasons=(), *, ai=False):
    """Extractor output (BookingCreate-shaped dicts) → gold-shaped units.

    A flight whose events hold two or more flight/outbound/return legs becomes
    one unit per leg, the same unit the gold set uses.
    """
    status = _status(review_reasons, ai)
    units = []
    for booking in bookings or []:
        adults, kids = _party(booking)
        base = _empty_unit()
        base.update({
            'type': TYPE_MAP.get(booking.get('kind') or booking.get('type')),
            'provider_name': booking.get('provider'),
            'party.adults': adults, 'party.children': kids,
            'amount': booking.get('amount'), 'currency': booking.get('currency'),
            'deposit': booking.get('deposit'),
            'confirmation_code': booking.get('confirmation_number'),
            'status': status,
        })
        events = [event for event in booking.get('events') or [] if isinstance(event, dict)]
        legs = [event for event in events if event.get('event_type') in FLIGHT_EVENTS]
        if base['type'] == 'flight' and len(legs) >= 2:
            for leg in legs:
                unit = dict(base)
                unit.update({'start_local': _minute(leg.get('start_local')), 'start_tz': leg.get('start_timezone'),
                             'end_local': _minute(leg.get('end_local')), 'end_tz': leg.get('end_timezone')})
                units.append(unit)
            continue
        unit = dict(base)
        if events:
            unit.update({'start_local': _minute(events[0].get('start_local')),
                         'start_tz': events[0].get('start_timezone'),
                         'end_local': _minute(events[-1].get('end_local')),
                         'end_tz': events[-1].get('end_timezone')})
        else:
            unit.update({'start_local': _minute(_join(booking.get('date'), booking.get('time'))),
                         'end_local': _minute(_join(booking.get('date_end'), booking.get('time_end')))})
        units.append(unit)
    return units


# --------------------------------------------------------------------------- #
# Field comparison
# --------------------------------------------------------------------------- #
_BRACKETS = re.compile(r'\([^)]*\)|（[^）]*）|\[[^\]]*\]|【[^】]*】')


def provider_key(value):
    text = unicodedata.normalize('NFKC', str(value)).casefold()
    text = _BRACKETS.sub('', text)
    return ''.join(ch for ch in text if ch.isalnum())


def provider_match(expected, predicted):
    a, b = provider_key(expected), provider_key(predicted)
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and (a in b or b in a)


def code_key(value):
    return re.sub(r'\s+', '', str(value)).casefold()


def _decimal_text(value):
    parts = str(value).strip().replace(',', '').split('-')
    try:
        return '-'.join(format(Decimal(part).normalize(), 'f') for part in parts)
    except (InvalidOperation, ValueError):
        return str(value).strip()


def same_value(field, expected, predicted):
    if expected is None or predicted is None:
        return False
    if field == 'provider_name':
        return provider_match(expected, predicted)
    if field == 'confirmation_code':
        return code_key(expected) == code_key(predicted)
    if field in ('amount', 'deposit'):
        return _decimal_text(expected) == _decimal_text(predicted)
    return expected == predicted


# --------------------------------------------------------------------------- #
# Matching (maximum-weight assignment, Hungarian method)
# --------------------------------------------------------------------------- #
def pair_score(gold, pred):
    same_day = bool(gold['start_local'] and pred['start_local'] and gold['start_local'][:10] == pred['start_local'][:10])
    same_type = bool(gold['type'] and pred['type'] and gold['type'] == pred['type'])
    if not same_day and not same_type:
        return 0.0  # Not matchable.
    score = 2.0 * same_day + 2.0 * same_type
    if gold['confirmation_code'] and pred['confirmation_code'] and code_key(gold['confirmation_code']) == code_key(pred['confirmation_code']):
        score += 1.0
    if gold['provider_name'] and pred['provider_name'] and provider_match(gold['provider_name'], pred['provider_name']):
        score += 0.5
    return score


def _hungarian(cost):
    """Minimum-cost assignment on a square matrix; returns column for each row."""
    n = len(cost)
    inf = float('inf')
    u, v, p, way = [0.0] * (n + 1), [0.0] * (n + 1), [0] * (n + 1), [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv, used = [inf] * (n + 1), [False] * (n + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [0] * n
    for j in range(1, n + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result


def match(gold, preds):
    """Return [(gold_index, pred_index)] maximising the summed pair score."""
    if not gold or not preds:
        return []
    n = max(len(gold), len(preds))
    weights = [[pair_score(gold[i], preds[j]) if i < len(gold) and j < len(preds) else 0.0
                for j in range(n)] for i in range(n)]
    top = max(max(row) for row in weights)
    columns = _hungarian([[top - w for w in row] for row in weights])
    return sorted((i, j) for i, j in enumerate(columns)
                  if i < len(gold) and j < len(preds) and weights[i][j] > 0)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _counter():
    return {field: {'tp': 0, 'fp': 0, 'fn': 0} for field in SCORED_FIELDS}


def prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if tp + fp + fn == 0:
        f1 = None
    elif tp == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': _round(precision), 'recall': _round(recall), 'f1': _round(f1)}


def _round(value):
    return None if value is None else round(value, 4)


def _decoy_hit(decoy, preds):
    if decoy['field'] == 'booking_count':
        return len(preds) > decoy['max']
    return any(isinstance(unit.get(decoy['field']), str) and unit[decoy['field']].startswith(tuple(decoy['values']))
               for unit in preds)


def _review_ok(field, gold, pred, review_reasons):
    if pred.get(field) is None:
        return True
    if field in ('start_local', 'end_local') and gold.get(field) and 'T' in gold[field] and 'T' not in pred[field]:
        return True
    return any(field in REVIEW_FIELDS.get(reason, ()) for reason in review_reasons or ())


def ungrounded_codes(preds, raw_text):
    """Distinct predicted confirmation codes that do not occur in the source."""
    haystack = code_key(raw_text or '')
    codes = {code_key(unit['confirmation_code']) for unit in preds if unit.get('confirmation_code')}
    return sorted(code for code in codes if code and code not in haystack)


def score_case(case, extraction, raw_text=None):
    """Score one case. `extraction` = {'bookings', 'review_reasons', 'ai', 'error', ...}."""
    gold = gold_units(case)
    error = extraction.get('error')
    preds = [] if error else predicted_units(extraction.get('bookings'), extraction.get('review_reasons'),
                                             ai=extraction.get('ai', False))
    reasons = extraction.get('review_reasons') or ()
    counts = _counter()
    failures = []
    if error:
        failures.append(f'extractor_error:{error}')
    pairs = match(gold, preds)
    matched_gold = {i: j for i, j in pairs}
    matched_pred = {j for _, j in pairs}
    for i, unit in enumerate(gold):
        j = matched_gold.get(i)
        for field in SCORED_FIELDS:
            if field in unit['unscored']:
                continue
            expected = unit[field]
            predicted = preds[j][field] if j is not None else None
            if expected is not None and same_value(field, expected, predicted):
                counts[field]['tp'] += 1
                continue
            if expected is not None:
                counts[field]['fn'] += 1
            if predicted is not None:
                counts[field]['fp'] += 1
            if j is not None and (expected is not None or predicted is not None):
                failures.append(f'field_mismatch:{field}')
    for j, unit in enumerate(preds):
        if j in matched_pred:
            continue
        for field in SCORED_FIELDS:
            if unit[field] is not None:
                counts[field]['fp'] += 1
    missed = len(gold) - len(pairs)
    extra = len(preds) - len(pairs)
    count_ok = len(gold) == len(preds)
    if not count_ok:
        failures.append('count_mismatch')
    if missed:
        failures.append('missed_booking')
    if extra:
        failures.append('false_positive_booking')
    ai = bool(extraction.get('ai', False))
    absent = AI_ABSENT_FIELDS if ai else LOCAL_ABSENT_FIELDS
    unmeasurable_rules = [rule for rule in case['forbidden'] if case['decoys'][rule]['field'] in absent]
    violations = [rule for rule in case['forbidden']
                  if rule not in unmeasurable_rules and _decoy_hit(case['decoys'][rule], preds)]
    failures += [f'forbidden:{rule}' for rule in violations]
    review_items = [item for item in case['must_review'] if not (ai and item['field'] in AI_ABSENT_FIELDS)]
    review_unmeasurable = [item['field'] for item in case['must_review'] if ai and item['field'] in AI_ABSENT_FIELDS]
    review_total, review_ok = len(review_items), 0
    for item in review_items:
        j = matched_gold.get(item['booking'])
        if j is not None and _review_ok(item['field'], gold[item['booking']], preds[j], reasons):
            review_ok += 1
        else:
            failures.append(f"missed_review:{item['field']}")
    ungrounded = ungrounded_codes(preds, raw_text) if raw_text is not None else []
    totals = {key: sum(c[key] for c in counts.values()) for key in ('tp', 'fp', 'fn')}
    return {
        'case_id': case['case_id'], 'language': case['language'], 'tags': case['tags'],
        'expected_bookings': len(gold), 'predicted_bookings': len(preds), 'booking_count_ok': count_ok,
        'matched': len(pairs), 'missed': missed, 'false_positive': extra,
        'field_counts': counts, 'totals': totals,
        'forbidden_violations': violations,
        'forbidden_unmeasurable': unmeasurable_rules,
        'must_review': {'satisfied': review_ok, 'total': review_total, 'unmeasurable': review_unmeasurable},
        'ungrounded_codes': len(ungrounded),
        'failures': list(dict.fromkeys(failures)),
        'extractor': {key: extraction.get(key) for key in ('error', 'repair_attempts', 'review_reasons', 'replay_sources')},
        'cost_usd': extraction.get('cost_usd'), 'latency_ms': extraction.get('latency_ms'),
        'llm_calls': extraction.get('llm_calls'),
    }


def aggregate(rows):
    fields = _counter()
    for row in rows:
        for field, counts in row['field_counts'].items():
            for key in ('tp', 'fp', 'fn'):
                fields[field][key] += counts[key]
    micro = {key: sum(c[key] for c in fields.values()) for key in ('tp', 'fp', 'fn')}
    review_total = sum(row['must_review']['total'] for row in rows)
    review_ok = sum(row['must_review']['satisfied'] for row in rows)
    by_rule, failure_types, unmeasurable_rules, review_unmeasurable = {}, {}, {}, {}
    for row in rows:
        for rule in row['forbidden_violations']:
            by_rule[rule] = by_rule.get(rule, 0) + 1
        for rule in row.get('forbidden_unmeasurable') or ():
            unmeasurable_rules[rule] = unmeasurable_rules.get(rule, 0) + 1
        for field in row['must_review'].get('unmeasurable') or ():
            review_unmeasurable[field] = review_unmeasurable.get(field, 0) + 1
        for failure in row['failures']:
            kind = failure.split(':', 1)[0] if failure.startswith(('field_mismatch', 'missed_review')) else failure
            failure_types[kind] = failure_types.get(kind, 0) + 1
    costs = [row['cost_usd'] for row in rows if row['cost_usd'] is not None]
    latencies = [row['latency_ms'] for row in rows if row['latency_ms'] is not None]
    from src.llm.telemetry import percentile
    return {
        'cases': len(rows),
        'expected_bookings': sum(row['expected_bookings'] for row in rows),
        'predicted_bookings': sum(row['predicted_bookings'] for row in rows),
        'matched_bookings': sum(row['matched'] for row in rows),
        'booking_count_accuracy': _round(sum(row['booking_count_ok'] for row in rows) / len(rows)) if rows else None,
        'micro': prf(**micro),
        'fields': {field: prf(**counts) for field, counts in fields.items()},
        'must_review': {'satisfied': review_ok, 'total': review_total,
                        'recall': _round(review_ok / review_total) if review_total else None,
                        'unmeasurable': sum(review_unmeasurable.values()),
                        'unmeasurable_by_field': dict(sorted(review_unmeasurable.items()))},
        'forbidden_violations': sum(by_rule.values()),
        'forbidden_by_rule': dict(sorted(by_rule.items())),
        'forbidden_unmeasurable': sum(unmeasurable_rules.values()),
        'forbidden_unmeasurable_by_rule': dict(sorted(unmeasurable_rules.items())),
        'ungrounded_atoms': sum(row['ungrounded_codes'] for row in rows),
        'extractor_errors': sum(1 for row in rows if row['extractor']['error']),
        'repair_attempts': sum(row['extractor']['repair_attempts'] or 0 for row in rows),
        'failure_types': dict(sorted(failure_types.items())),
        'cost_usd_total': round(sum(costs), 6) if costs else None,
        'latency_ms_p50': percentile(latencies, 0.5), 'latency_ms_p95': percentile(latencies, 0.95),
    }


# --------------------------------------------------------------------------- #
# Extractors
# --------------------------------------------------------------------------- #
def read_case_text(case, root: Path = PROJECT_ROOT) -> str:
    from src.loader import read_email_file
    return read_email_file(root / case['file'])


def extract_local(raw):
    from src.foundation.local_mail import parse_local_document
    from src.foundation.repository import DomainError
    try:
        bookings, reasons = parse_local_document(raw)
    except DomainError as exc:
        return {'bookings': [], 'review_reasons': [], 'ai': False, 'error': exc.code, 'repair_attempts': None}
    return {'bookings': bookings, 'review_reasons': reasons, 'ai': False, 'error': None, 'repair_attempts': None}


def replay_policy_config(model=EXTRACTION_MODEL, *, limit_micros=1_000_000):
    """Test prices (fake/*) plus a zero-rate `replay/<model>` SKU."""
    from src.reliability.budget import BudgetPolicy
    config = copy.deepcopy(BudgetPolicy.for_tests(rate='0', limit=limit_micros).config)
    config['version'] = 'synthetic-eval-replay-v1'
    config['prices'][f'replay/{model}'] = {
        'currency': 'USD', 'rates_per_million': {'input_tokens': '0', 'output_tokens': '0'},
        'max_units': {'input_tokens': 200000, 'output_tokens': 4096}}
    return config


@contextmanager
def eval_environment(workdir, policy_config):
    """Temporary SQLite with one synthetic user and trip, a Budget and a gateway."""
    from src.foundation.db import Database
    from src.foundation.repository import Repository, utcnow
    from src.reliability.budget import Budget, BudgetPolicy, CallContext
    from src.reliability.providers import ProviderGateway
    workdir = Path(workdir)
    db = Database(workdir / 'eval.sqlite3')
    now = utcnow()
    with db.connect() as con:
        con.execute('INSERT INTO users(id,email,auth_provider,auth_subject,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                    (EVAL_OWNER, 'eval@example.invalid', 'fake', EVAL_OWNER, now, now))
    trip = Repository(db).create_trip(EVAL_OWNER, {'title': 'Synthetic eval trip', 'start_date': '2026-10-01',
                                                   'end_date': '2026-12-31'})
    policy = BudgetPolicy(policy_config)
    if not policy.valid:
        raise ValueError('평가용 가격 정책이 올바르지 않습니다.')
    budget = Budget(db, policy)
    gateway = ProviderGateway(budget, workdir / 'receipts')
    context = CallContext(owner_id=EVAL_OWNER, scope_id=trip['id'], trip_id=trip['id'])
    yield SimpleNamespace(db=db, gateway=gateway, budget=budget, context=context)


def case_prefix(case_id):
    return f'eval:{case_id}'


def _telemetry(env, prefix):
    """Cost and latency of one case's calls from llm_calls (None when absent)."""
    reserved = 'request:' + prefix + ':'
    with env.db.connect() as con:
        rows = con.execute('SELECT c.latency_ms, c.cost_micros, r.call_key FROM llm_calls c '
                           'JOIN usage_reservations r ON r.call_id=c.call_id').fetchall()
    rows = [row for row in rows if str(row['call_key']).startswith(reserved)]
    if not rows:
        return None, None, 0
    latencies = [row['latency_ms'] for row in rows if row['latency_ms'] is not None]
    costs = [row['cost_micros'] for row in rows if row['cost_micros'] is not None]
    return (round(sum(costs) / 1_000_000, 6) if costs else None,
            sum(latencies) if latencies else None, len(rows))


def _last_call_row(env):
    with env.db.connect() as con:
        return con.execute('SELECT COALESCE(MAX(rowid),0) AS n FROM llm_calls').fetchone()['n']


def _repair_calls_since(env, after_rowid):
    """Repair attempts since `after_rowid`, including ones refused before the call.

    Two telemetry signals: a recorded `extract_repair` row (the gateway writes
    one even for a budget refusal), and a first `extract` call annotated
    `schema_invalid` — under metering the parser always follows that with one
    repair, even when the repair is refused before telemetry (e.g. REPLAY_MISS
    raised in the adapter preflight).
    """
    with env.db.connect() as con:
        row = con.execute("SELECT SUM(operation='extract_repair') AS repairs, "
                          "SUM(operation='extract' AND outcome='schema_invalid') AS invalid "
                          'FROM llm_calls WHERE rowid>?', (after_rowid,)).fetchone()
    return max(row['repairs'] or 0, row['invalid'] or 0)


OPEN_STATES = ('reserved', 'sent', 'unknown', 'pending_reconciliation')


def ledger_summary(env):
    """Reservation states and money from the eval's own ledger.

    `settled_cost_usd` is what the provider reported. Reservations left open
    (timeouts, unknown outcomes) may still have been charged, so their
    estimated cost is reported separately as an upper bound, never dropped.
    """
    with env.db.connect() as con:
        rows = con.execute('SELECT state, COUNT(*) AS n, COALESCE(SUM(estimated_cost_micros),0) AS est, '
                           'COALESCE(SUM(actual_cost_micros),0) AS act FROM usage_reservations GROUP BY state').fetchall()
    by_state = {row['state']: row['n'] for row in rows}
    settled = sum(row['act'] for row in rows if row['state'] == 'settled')
    open_estimate = sum(row['est'] for row in rows if row['state'] in OPEN_STATES)
    return {'reservations_by_state': dict(sorted(by_state.items())),
            'settled_cost_usd': round(settled / 1_000_000, 6),
            'open_reservations': sum(by_state.get(state, 0) for state in OPEN_STATES),
            'open_estimated_cost_usd_upper_bound': round(open_estimate / 1_000_000, 6)}


def extract_metered(env, case_id, raw, adapter, model=EXTRACTION_MODEL):
    """The production extraction path (with repair) inside a metered scope."""
    from src import parser
    from src.foundation.repository import DomainError
    from src.llm import adapter_scope
    from src.reliability.providers import metered_context
    prefix = case_prefix(case_id)
    result = {'bookings': [], 'review_reasons': [], 'ai': True, 'error': None, 'repair_attempts': 0}
    before = _last_call_row(env)
    try:
        with metered_context(env.gateway, env.context, prefix), adapter_scope(adapter):
            out = parser.extract_document_reservations(raw, model)
        result.update(bookings=out['bookings'], repair_attempts=out['repair_attempts'])
        sources = [call.get('replay_source') for call in out.get('calls') or [] if call.get('replay_source')]
        result['replay_sources'] = sources or None
    except parser.ExtractionFailed as exc:
        result.update(error='EXTRACTION_INCOMPLETE' if exc.kind == 'incomplete' else 'EXTRACTION_INVALID',
                      repair_attempts=exc.repair_attempts)
    except DomainError as exc:
        result['error'] = exc.code
    # A repair call that itself failed with a DomainError (budget, replay miss)
    # never reaches ExtractionFailed, so count attempts from telemetry as well.
    result['repair_attempts'] = max(result['repair_attempts'] or 0, _repair_calls_since(env, before))
    cost, latency, calls = _telemetry(env, prefix)
    result.update(cost_usd=cost, latency_ms=latency, llm_calls=calls)
    return result


# --------------------------------------------------------------------------- #
# Live cost bound
# --------------------------------------------------------------------------- #
PREVIOUS_OUTPUT_MAX_BYTES = 16000 * 4  # parser keeps <=16000 chars; UTF-8 <=4 bytes each.
REPAIR_ERRORS_MAX_BYTES = 20 * 400     # <=20 validator lines.


LIMIT_KEYS = ('user_daily', 'user_monthly', 'global_daily', 'global_monthly')


def live_policy_config(pricing: dict, max_cost_usd: float) -> dict:
    """The pricing file with every spending cap lowered to the requested maximum.

    The file's own limits are never raised (each limit = min(cap, file limit))
    and an operator's emergency stop (`halted: true`) is honoured: refused.
    """
    if pricing.get('halted'):
        raise ValueError('가격 파일이 halted: true (비상 정지) 상태라 live 평가를 실행하지 않습니다.')
    config = copy.deepcopy(pricing)
    cap = int(Decimal(str(max_cost_usd)) * 1_000_000)
    file_limits = pricing.get('limits') or {}
    config['limits'] = {}
    for currency in {price['currency'] for price in config['prices'].values()}:
        own = file_limits.get(currency) or {}
        config['limits'][currency] = {key: min(cap, int(own[key])) if own.get(key) is not None else cap
                                      for key in LIMIT_KEYS}
    return config


def effective_cap_usd(config: dict) -> float:
    """The smallest spending limit the live run will actually be held to."""
    values = [value for limits in (config.get('limits') or {}).values() for value in limits.values()]
    return float(Decimal(min(values)) / 1_000_000) if values else 0.0


def live_upper_bound(cases, raws, pricing: dict, *, provider='openai', model=EXTRACTION_MODEL) -> dict:
    """Worst case per case: first call + one repair, each at its output cap.

    Input is the gateway's own estimate (UTF-8 bytes + 256 per call, schema
    included); the repair adds the previous output and validator errors.
    """
    from src import parser
    from src.llm.openai_adapter import OpenAIAdapter
    from src.prompts import load_prompt
    price = pricing['prices'].get(f'{provider}/{model}')
    if price is None:
        raise ValueError(f'가격 파일에 {provider}/{model} 이 없습니다.')
    rates = {key: Decimal(str(value)) for key, value in price['rates_per_million'].items()}
    out_cap = int(price['max_units']['output_tokens'])
    repair_cap = min(parser.REPAIR_MAX_COMPLETION_TOKENS, out_cap)
    adapter = OpenAIAdapter()
    schema = ('document_reservations', parser.DOCUMENT_RESERVATIONS_SCHEMA)
    repair_text = load_prompt('mail_extraction_repair').render(ERRORS='')
    per_case, total = [], Decimal(0)
    for case, raw in zip(cases, raws):
        messages = [{'role': 'system', 'content': parser.DOCUMENT_SYSTEM_PROMPT}, {'role': 'user', 'content': raw}]
        kwargs = adapter.chat_request(model=model, messages=messages, schema=schema)
        first_in = adapter.estimate_chat(kwargs, out_cap)['input_tokens']
        repair_in = first_in + PREVIOUS_OUTPUT_MAX_BYTES + len(repair_text.encode('utf-8')) + REPAIR_ERRORS_MAX_BYTES + 64
        micros = rates['input_tokens'] * (first_in + repair_in) + rates['output_tokens'] * (out_cap + repair_cap)
        total += micros
        per_case.append({'case_id': case['case_id'], 'input_tokens_bound': first_in + repair_in,
                         'output_tokens_bound': out_cap + repair_cap, 'usd_bound': float(round(micros / 1_000_000, 6))})
    return {'provider': provider, 'model': model, 'cases': len(per_case),
            'usd_bound': float(round(total / 1_000_000, 6)), 'per_case': per_case,
            'assumption': 'worst case: first call + one repair at output caps; input = UTF-8 bytes + framing'}


# --------------------------------------------------------------------------- #
# Run and report
# --------------------------------------------------------------------------- #
def _relative(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return path.name


def _ai_measurable_rules(case):
    return {rule for rule in case['forbidden'] if case['decoys'][rule]['field'] not in AI_ABSENT_FIELDS}


def stratified_case_ids(cases, count):
    """A deterministic, spread-out subset: greedily take the case with an unseen
    language first, then the most not-yet-covered forbidden rules that AI output
    can break (injection, change, payment/deadline dates...), then the most unseen tags;
    ties → smallest case_id.

    Used for small live runs so the first paid numbers are not just the first
    N cases in sorted order (all mtime-*/mtp-*, mostly English).
    """
    remaining = sorted(cases, key=lambda case: case['case_id'])
    languages, rules, tags, chosen = set(), set(), set(), []
    while remaining and len(chosen) < count:
        def gain(case):
            return (case['language'] not in languages, len(_ai_measurable_rules(case) - rules),
                    len(set(case['tags']) - tags))
        best = max(remaining, key=gain)  # max() keeps the first (smallest case_id) on ties
        chosen.append(best['case_id'])
        languages.add(best['language'])
        rules |= _ai_measurable_rules(best)
        tags |= set(best['tags'])
        remaining.remove(best)
    return chosen


def select_cases(cases, *, max_cases=None, case_ids=None):
    """`case_ids` (exact list, unknown id → error) wins over `max_cases` (first N)."""
    if case_ids is not None:
        by_id = {case['case_id']: case for case in cases}
        unknown = [case_id for case_id in case_ids if case_id not in by_id]
        if unknown:
            raise ValueError(f'정답셋에 없는 사례: {unknown}')
        return [by_id[case_id] for case_id in case_ids]
    return cases[:max_cases] if max_cases is not None else cases


def live_workdir(out) -> Path:
    """Where a live run keeps its ledger and receipts: next to --out (under data/)."""
    out = Path(out)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return out.parent / f'{out.stem}-work-{stamp}'


def run(mode, *, gold_dir=GOLD_DIR, replay_dir=DEFAULT_REPLAY_DIR, max_cases=None, case_ids=None,
        model=EXTRACTION_MODEL, live_adapter=None, live_policy=None, workdir=None):
    all_cases = load_gold(gold_dir)
    cases = select_cases(all_cases, max_cases=max_cases, case_ids=case_ids)
    subset = len(cases) != len(all_cases)
    if mode == 'live' and workdir is None:
        raise ValueError('live 평가는 원장·영수증을 남길 workdir 가 필요합니다 (임시 디렉터리 금지).')
    rows, sources, ledger = [], {}, None
    if mode == 'local':
        for case in cases:
            raw = read_case_text(case)
            rows.append(score_case(case, extract_local(raw), raw))
    elif mode in ('replay', 'live'):
        if mode == 'replay':
            from src.llm.replay import ReplayAdapter, ReplayStore
            store = ReplayStore(replay_dir)
            for record in store.records():
                sources[record['source']] = sources.get(record['source'], 0) + 1
            adapter = ReplayAdapter(store)
            policy = replay_policy_config(model)
        else:
            adapter, policy = live_adapter, live_policy
        if workdir is not None:
            Path(workdir).mkdir(parents=True, exist_ok=True)
        holder = tempfile.TemporaryDirectory(prefix='mail-eval-') if workdir is None else nullcontext(workdir)
        with holder as directory, eval_environment(directory, policy) as env:
            for case in cases:
                raw = read_case_text(case)
                rows.append(score_case(case, extract_metered(env, case['case_id'], raw, adapter, model), raw))
            ledger = ledger_summary(env)
    else:
        raise ValueError(f'unknown mode: {mode}')
    result = {
        'suite': SUITE, 'mode': mode, 'synthetic': True, 'quality_claim': QUALITY_CLAIM[mode],
        'generated_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'gold_dir': _relative(gold_dir), 'model': model if mode != 'local' else None,
        'extractor': 'src.foundation.local_mail.parse_local_document' if mode == 'local'
                     else 'src.parser.extract_document_reservations',
        'replay': {'dir': _relative(replay_dir), 'records_by_source': dict(sorted(sources.items()))} if mode == 'replay' else None,
        'subset': subset, 'selected_case_ids': [case['case_id'] for case in cases] if subset else None,
        'unmeasurable_fields': list(AI_ABSENT_FIELDS) if mode != 'local' else list(LOCAL_ABSENT_FIELDS),
        'gold_sha256': gold_content_sha256(gold_dir),
        'ledger': ledger, 'workdir': _relative(workdir) if mode == 'live' else None,
        'metrics': aggregate(rows), 'cases': rows,
    }
    return result


def _fmt(value):
    return '—' if value is None else (f'{value:.4f}' if isinstance(value, float) else str(value))


def render_markdown(result) -> str:
    m = result['metrics']
    claim = {'replay': '**파이프라인 시험, 모델 품질 아님** — 손으로 만든 replay 기록(정답에서 만든 oracle + 고의 결함)으로 낸 숫자다.',
             'local': '규칙 파서(외부 호출 0)의 기준선이다.',
             'live': '실제 모델 호출 결과다. 입력 메일은 모두 합성이다.'}[result['mode']]
    lines = [f"# mail eval — {result['suite']} / {result['mode']} (synthetic)", '',
             f"- synthetic: **true** (모든 사례가 합성 메일)", f"- {claim}",
             f"- 생성: {result['generated_at']} · 정답셋: `{result['gold_dir']}` · 추출기: `{result['extractor']}`"]
    if result.get('replay'):
        lines.append(f"- replay 기록: `{result['replay']['dir']}` {result['replay']['records_by_source']}")
        lines.append('- 비용은 replay 단가 0원, 지연은 로컬 replay 처리 시간이다(모델 지연 아님).')
    if result.get('subset'):
        lines.append(f"- **부분 실행**: {len(result['selected_case_ids'])}건만 평가 — {', '.join(result['selected_case_ids'])}")
    if result.get('unmeasurable_fields'):
        source = 'AI 추출 스키마' if result['mode'] != 'local' else '규칙 파서 출력'
        lines.append(f"- {source}에 없는 필드 `{', '.join(result['unmeasurable_fields'])}` 는 측정 불가다. "
                     f"이 필드에 걸린 forbidden 규칙 {m.get('forbidden_unmeasurable', 0)}건 {m.get('forbidden_unmeasurable_by_rule') or ''}"
                     f"·must_review {m['must_review'].get('unmeasurable', 0)}건은 위반·충족 어느 쪽으로도 세지 않았다 "
                     '(forbidden 0 이 이 규칙들을 보장하지 않음).')
    if result.get('ledger'):
        ledger = result['ledger']
        lines.append(f"- 원장: 예약 상태 {ledger['reservations_by_state']} · 정산 비용 {ledger['settled_cost_usd']} USD · "
                     f"미정산 {ledger['open_reservations']}건 추정 상한 {ledger['open_estimated_cost_usd_upper_bound']} USD"
                     + (f" · 보관 `{result['workdir']}`" if result.get('workdir') else ''))
    lines += ['', '## 전체 (synthetic)', '', '| 지표 | 값 |', '| --- | --- |']
    for label, value in [('사례 수', m['cases']), ('정답 예약 / 예측 예약 / 매칭', f"{m['expected_bookings']} / {m['predicted_bookings']} / {m['matched_bookings']}"),
                         ('micro P / R / F1', f"{_fmt(m['micro']['precision'])} / {_fmt(m['micro']['recall'])} / {_fmt(m['micro']['f1'])}"),
                         ('예약 수 정확도', _fmt(m['booking_count_accuracy'])),
                         ('must_review 재현율', f"{_fmt(m['must_review']['recall'])} ({m['must_review']['satisfied']}/{m['must_review']['total']}, "
                                                f"측정 불가 {m['must_review'].get('unmeasurable', 0)})"),
                         ('forbidden 위반', f"{m['forbidden_violations']} {m['forbidden_by_rule'] or ''}"),
                         ('forbidden 측정 불가', f"{m.get('forbidden_unmeasurable', 0)} {m.get('forbidden_unmeasurable_by_rule') or ''}"),
                         ('근거 없는 예약번호', m['ungrounded_atoms']), ('추출기 오류 사례', m['extractor_errors']),
                         ('수리 호출', m['repair_attempts']), ('정산 비용 합계 USD (미정산 제외)', _fmt(m['cost_usd_total'])),
                         ('지연 p50 / p95 ms', f"{_fmt(m['latency_ms_p50'])} / {_fmt(m['latency_ms_p95'])}")]:
        lines.append(f'| {label} | {value} |')
    lines += ['', '## 필드별 (synthetic)', '', '| 필드 | TP | FP | FN | P | R | F1 |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for field, s in m['fields'].items():
        lines.append(f"| {field} | {s['tp']} | {s['fp']} | {s['fn']} | {_fmt(s['precision'])} | {_fmt(s['recall'])} | {_fmt(s['f1'])} |")
    lines += ['', '## 사례별 (synthetic)', '', '| 사례 | 정답/예측 | forbidden | must_review | 비용 USD | 지연 ms | 실패 유형 |',
              '| --- | --- | --- | --- | ---: | ---: | --- |']
    for row in result['cases']:
        lines.append(f"| {row['case_id']} | {row['expected_bookings']}/{row['predicted_bookings']} | "
                     f"{', '.join(row['forbidden_violations']) or '—'} | {row['must_review']['satisfied']}/{row['must_review']['total']} | "
                     f"{_fmt(row['cost_usd'])} | {_fmt(row['latency_ms'])} | {', '.join(row['failures']) or '—'} |")
    lines.append('')
    return '\n'.join(lines)


def write_outputs(result, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    out.with_suffix('.md').write_text(render_markdown(result), encoding='utf-8')
    return out, out.with_suffix('.md')


def _live_setup(args, cases):
    """All checks that must pass before any client exists. Returns (adapter, policy, bound)."""
    if args.max_cost_usd is None or args.max_cases is None or not args.pricing:
        raise SystemExit('live: --max-cost-usd, --max-cases, --pricing 는 필수입니다.')
    if args.max_cost_usd <= 0 or args.max_cases <= 0:
        raise SystemExit('live: --max-cost-usd 와 --max-cases 는 0보다 커야 합니다.')
    pricing = json.loads(Path(args.pricing).read_text(encoding='utf-8'))
    try:
        policy = live_policy_config(pricing, args.max_cost_usd)
    except ValueError as exc:
        raise SystemExit(f'live: {exc}') from None
    cap = effective_cap_usd(policy)
    raws = [read_case_text(case) for case in cases]
    bound = live_upper_bound(cases, raws, pricing, model=args.model)
    print(json.dumps({'live_upper_bound_usd': bound['usd_bound'], 'cases': bound['cases'], 'model': bound['model'],
                      'case_ids': [case['case_id'] for case in cases],
                      'max_cost_usd': args.max_cost_usd, 'effective_cap_usd': cap,
                      'assumption': bound['assumption']}, ensure_ascii=False))
    if bound['usd_bound'] > cap:
        raise SystemExit(f"live: 예상 상한 {bound['usd_bound']} USD 가 실제 상한 {cap} USD "
                         f"(--max-cost-usd {args.max_cost_usd} 와 가격 파일 한도 중 작은 값)를 넘어 실행하지 않습니다. "
                         '--max-cases 를 줄이거나 상한을 올리세요.')
    if args.dry_run:
        return None, None, bound
    if os.getenv('PYTHON_DOTENV_DISABLED', '').lower() not in ('1', 'true', 'yes'):
        raise SystemExit('live: 키는 환경변수로만 받습니다. PYTHON_DOTENV_DISABLED=1 을 붙여 실행하세요.')
    if not os.environ.get('OPENAI_API_KEY'):
        raise SystemExit('live: OPENAI_API_KEY 환경변수가 없습니다.')
    from src.llm.openai_adapter import OpenAIAdapter
    adapter = OpenAIAdapter()
    if args.record_replay:
        from src.llm.replay import ReplayStore
        adapter = recording_adapter(adapter, ReplayStore(args.record_replay))
    return adapter, policy, bound


def recording_adapter(inner, store):
    """A RecordingAdapter that tags each record with the prompt of *that* call.

    The parser's repair call re-sends the first two messages and appends the
    previous answer and the repair prompt, so more than two messages means repair.
    """
    from src.llm.replay import RecordingAdapter
    from src.prompts import load_prompt
    first, repair = load_prompt('mail_document_extraction').meta(), load_prompt('mail_extraction_repair').meta()

    class _PerCallPrompt(RecordingAdapter):
        def send(self, client, kind, kwargs):
            self.prompt = repair if len(kwargs.get('messages') or ()) > 2 else first
            return super().send(client, kind, kwargs)

    return _PerCallPrompt(inner, store, prompt=first)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m src.evals.mail', description='mail_gold_v1 평가 (synthetic)')
    parser.add_argument('--mode', choices=('local', 'replay', 'live'), required=True)
    parser.add_argument('--out', required=True, help='결과 JSON 경로 (같은 이름 .md 도 쓴다). data/ 아래 권장')
    parser.add_argument('--gold-dir', default=str(GOLD_DIR))
    parser.add_argument('--replay-dir', default=str(DEFAULT_REPLAY_DIR), help='replay 모드 기록 경로')
    parser.add_argument('--model', default=EXTRACTION_MODEL)
    parser.add_argument('--max-cases', type=int, help='live 필수: 평가할 사례 수 상한 (live 는 언어·태그 분산 선택, '
                                                       'local/replay 는 앞에서 N건)')
    parser.add_argument('--cases', help='쉼표로 구분한 case_id 목록 (지정하면 이 사례만, --max-cases 보다 우선)')
    parser.add_argument('--max-cost-usd', type=float, help='live 필수: 비용 상한 (Budget 상한으로도 쓴다)')
    parser.add_argument('--pricing', help='live 필수: pricing-*.json 경로')
    parser.add_argument('--record-replay', help='live 선택: 응답을 replay 기록으로 남길 디렉터리')
    parser.add_argument('--dry-run', action='store_true', help='live: 예상 상한만 출력하고 호출하지 않는다')
    args = parser.parse_args(argv)
    case_ids = [item.strip() for item in args.cases.split(',') if item.strip()] if args.cases else None
    if args.mode == 'live':
        all_cases = load_gold(args.gold_dir)
        if case_ids is None and args.max_cases:
            case_ids = stratified_case_ids(all_cases, args.max_cases)
        if case_ids is not None and args.max_cases and len(case_ids) > args.max_cases:
            raise SystemExit(f'live: --cases {len(case_ids)}건이 --max-cases {args.max_cases} 를 넘습니다.')
        try:
            cases = select_cases(all_cases, case_ids=case_ids) if case_ids is not None else []
        except ValueError as exc:
            raise SystemExit(f'live: {exc}') from None
        adapter, policy, _bound = _live_setup(args, cases)
        if args.dry_run:
            return 0
        result = run('live', gold_dir=args.gold_dir, case_ids=case_ids, model=args.model,
                     live_adapter=adapter, live_policy=policy, workdir=live_workdir(args.out))
    else:
        result = run(args.mode, gold_dir=args.gold_dir, replay_dir=args.replay_dir, max_cases=args.max_cases,
                     case_ids=case_ids, model=args.model)
    json_path, md_path = write_outputs(result, args.out)
    m = result['metrics']
    print(json.dumps({'suite': result['suite'], 'mode': result['mode'], 'synthetic': True,
                      'quality_claim': result['quality_claim'], 'micro_f1': m['micro']['f1'],
                      'booking_count_accuracy': m['booking_count_accuracy'],
                      'must_review_recall': m['must_review']['recall'],
                      'forbidden_violations': m['forbidden_violations'],
                      'forbidden_unmeasurable': m['forbidden_unmeasurable'], 'extractor_errors': m['extractor_errors'],
                      'ledger': result['ledger'], 'workdir': result['workdir'],
                      'out': _relative(json_path), 'md': _relative(md_path)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
