"""Atomic, currency-separated cost reservations and an append-only audit ledger.

Money is an integer number of millionths of the configured currency, never a
float. A reservation survives timeouts and process crashes until actual usage or
explicit, audited reconciliation is known. Network calls never hold a DB lock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
from pathlib import Path
import re
from uuid import uuid4

from src.foundation.repository import DomainError

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_reservations (
 call_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id),
 trip_id TEXT REFERENCES trips(id), job_id TEXT, scope_kind TEXT NOT NULL,
 scope_id TEXT NOT NULL CHECK(length(scope_id)>0), provider TEXT NOT NULL,
 sku TEXT NOT NULL, operation TEXT NOT NULL, attempt INTEGER NOT NULL,
 call_key TEXT NOT NULL, request_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('reserved','sent','settled','unknown','pending_reconciliation','released')),
 currency TEXT NOT NULL, estimated_units_json TEXT NOT NULL,
 estimated_cost_micros INTEGER NOT NULL CHECK(estimated_cost_micros>=0),
 actual_units_json TEXT, actual_cost_micros INTEGER,
 price_version TEXT NOT NULL, price_confirmed_at TEXT NOT NULL,
 price_rates_json TEXT NOT NULL, period_day TEXT NOT NULL, period_month TEXT NOT NULL,
 result_ref TEXT, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK((scope_kind='personal_trip' AND trip_id IS NOT NULL AND scope_id=trip_id)
    OR (scope_kind='admin_research' AND trip_id IS NULL)),
 UNIQUE(owner_id,scope_kind,scope_id,operation,call_key,attempt)
);
CREATE INDEX IF NOT EXISTS usage_period ON usage_reservations(currency,period_day,period_month,owner_id);
CREATE TABLE IF NOT EXISTS usage_ledger (
 id TEXT PRIMARY KEY, call_id TEXT NOT NULL REFERENCES usage_reservations(call_id),
 entry_kind TEXT NOT NULL, amount_micros INTEGER NOT NULL, units_json TEXT,
 actor_id TEXT, reason_code TEXT, created_at TEXT NOT NULL,
 UNIQUE(call_id,entry_kind)
);
CREATE TABLE IF NOT EXISTS cost_controls (
 currency TEXT PRIMARY KEY, halted INTEGER NOT NULL DEFAULT 0,
 reason_code TEXT, updated_at TEXT NOT NULL
);
"""


def _json(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True, ensure_ascii=False)


def _error(code, message, status=429):
    return DomainError(code, message, status)


@dataclass(frozen=True)
class CallContext:
    owner_id: str
    scope_id: str
    trip_id: str | None = None
    scope_kind: str = 'personal_trip'
    job_id: str | None = None
    actor_id: str | None = None


class BudgetPolicy:
    """Prices are explicit configuration, not historical source-code constants.

    rates_per_million is a currency amount per million named units (e.g. tokens).
    This has the same numeric value as microcurrency per single unit. Every SKU
    requires hard unit bounds. Every currency requires all four spending caps.
    """
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.valid = False
        try:
            if self.config.get('timezone') != 'UTC':
                return
            if not isinstance(self.config['version'], str) or not self.config['version'].strip():
                return
            datetime.fromisoformat(self.config['confirmed_at'].replace('Z', '+00:00'))
            prices, limits = self.config['prices'], self.config['limits']
            if not prices or not limits:
                return
            for sku, price in prices.items():
                if not isinstance(sku, str) or '/' not in sku:
                    return
                currency = price['currency']
                if not re.fullmatch('[A-Z]{3}', currency):
                    return
                bounds, rates = price['max_units'], price['rates_per_million']
                if not bounds or set(bounds) != set(rates):
                    return
                for key, rate in rates.items():
                    rate = Decimal(str(rate))
                    if not rate.is_finite() or rate < 0 or not isinstance(bounds[key], int) or isinstance(bounds[key], bool) or bounds[key] <= 0:
                        return
                cap = limits[currency]
                for key in ('user_daily', 'user_monthly', 'global_daily', 'global_monthly'):
                    if not isinstance(cap[key], int) or isinstance(cap[key], bool) or cap[key] < 0:
                        return
            self.valid = True
        except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
            pass

    @classmethod
    def from_file(cls, path):
        if not path:
            return cls()
        try:
            return cls(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            return cls()

    @classmethod
    def for_tests(cls, *, rate='0', limit=1000000):
        """Explicit injection only; production environment never enables this."""
        return cls({'version':'synthetic-tests-v1','confirmed_at':'2026-01-01T00:00:00+00:00',
            'timezone':'UTC','prices':{f'fake/{op}':{'currency':'USD',
                'rates_per_million':{'calls':rate},'max_units':{'calls':100}}
                for op in ('extract','embed','query_embed','generate')},
            'limits':{'USD':{key:limit for key in ('user_daily','user_monthly','global_daily','global_monthly')}}})

    def price(self, provider, sku):
        if not self.valid:
            raise _error('BUDGET_NOT_CONFIGURED', '외부 호출 단가와 예산 상한을 먼저 설정해 주세요.', 503)
        price = self.config['prices'].get(f'{provider}/{sku}')
        if price is None:
            raise _error('BUDGET_NOT_CONFIGURED', '이 공급자 작업의 단가와 상한이 설정되지 않았습니다.', 503)
        return price

    @staticmethod
    def calculate(rates, units):
        if set(units) - set(rates):
            raise _error('USAGE_INVALID', '정산 단위를 확인할 수 없습니다.', 503)
        total = Decimal(0)
        for key, value in units.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise _error('USAGE_INVALID', '사용량은 음수가 아닌 정수여야 합니다.', 503)
            total += Decimal(str(rates[key])) * value
        return int(total.to_integral_value(rounding=ROUND_CEILING))


class Budget:
    def __init__(self, db, policy: BudgetPolicy | None = None, clock=None):
        self.db = db
        self.policy = policy or BudgetPolicy()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        return self.clock().astimezone(timezone.utc)

    @staticmethod
    def _scope(con, context):
        actor_id = context.actor_id or context.owner_id
        actor = con.execute("SELECT status,role FROM users WHERE id=?", (actor_id,)).fetchone()
        if not actor or actor['status'] != 'active':
            raise _error('ACCESS_REVOKED', '외부 호출 권한이 회수되었습니다.', 403)
        if context.scope_kind == 'personal_trip':
            if actor_id != context.owner_id or not context.trip_id or context.scope_id != context.trip_id:
                raise _error('NOT_FOUND', '여행을 찾을 수 없습니다.', 404)
            trip = con.execute('SELECT id FROM trips WHERE id=? AND owner_id=? AND deleted_at IS NULL', (context.trip_id, context.owner_id)).fetchone()
            if not trip:
                raise _error('NOT_FOUND', '여행을 찾을 수 없습니다.', 404)
        elif context.scope_kind == 'admin_research':
            if actor['role'] != 'admin' or context.trip_id is not None or not context.scope_id or actor_id != context.owner_id:
                raise _error('NOT_FOUND', '조사 범위를 찾을 수 없습니다.', 404)
        else:
            raise _error('NOT_FOUND', '호출 범위를 찾을 수 없습니다.', 404)

    def reserve(self, context, *, provider, sku, operation, call_key, estimated_units, request_hash, attempt=1):
        if not call_key or not request_hash or not isinstance(attempt, int) or attempt < 1:
            raise _error('CALL_INVALID', '호출 식별자가 올바르지 않습니다.', 422)
        # A recovered stage reuses its job's receipt; a user's explicit new job
        # must be a new paid attempt even when document ID and content match.
        call_key = ('job:' + context.job_id + ':' if context.job_id else 'request:') + call_key
        now = self._now()
        stamp, day, month = now.isoformat(), now.date().isoformat(), now.strftime('%Y-%m')
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            self._scope(con, context)
            existing = con.execute('SELECT * FROM usage_reservations WHERE owner_id=? AND scope_kind=? AND scope_id=? AND operation=? AND call_key=? AND attempt=?',
                (context.owner_id, context.scope_kind, context.scope_id, operation, call_key, attempt)).fetchone()
            if existing:
                if existing['request_hash'] != request_hash or existing['provider'] != provider or existing['sku'] != sku:
                    raise _error('IDEMPOTENCY_CONFLICT', '같은 호출 식별자에 다른 입력을 사용할 수 없습니다.', 409)
                return dict(existing)
            price = self.policy.price(provider, sku)
            if set(estimated_units) != set(price['max_units']):
                raise _error('COST_BOUND_REQUIRED', '모든 과금 단위의 예상 최대치를 지정해 주세요.', 503)
            cost = self.policy.calculate(price['rates_per_million'], estimated_units)
            if any(value > price['max_units'][key] for key, value in estimated_units.items()):
                raise _error('COST_BOUND_EXCEEDED', '호출 크기가 설정된 최대치를 넘었습니다.', 422)
            currency, caps = price['currency'], self.policy.config['limits'][price['currency']]
            control = con.execute('SELECT * FROM cost_controls WHERE currency=?', (currency,)).fetchone()
            if self.policy.config.get('halted') or (control and control['halted']):
                raise _error('GLOBAL_BUDGET_STOPPED', '외부 호출이 운영 예산 검토를 위해 중지되었습니다.', 503)
            for limit, period_column, period, personal in (
                ('user_daily','period_day',day,True), ('user_monthly','period_month',month,True),
                ('global_daily','period_day',day,False), ('global_monthly','period_month',month,False)):
                query = f"SELECT COALESCE(SUM(CASE WHEN state='released' THEN 0 WHEN state='settled' THEN actual_cost_micros ELSE estimated_cost_micros END),0) FROM usage_reservations WHERE currency=? AND {period_column}=?"
                args = [currency, period]
                if personal:
                    query += ' AND owner_id=?'
                    args.append(context.owner_id)
                spent = con.execute(query, args).fetchone()[0]
                if spent + cost > caps[limit]:
                    code = 'BUDGET_EXHAUSTED' if personal else 'GLOBAL_BUDGET_STOPPED'
                    raise _error(code, '설정된 외부 호출 예산이 소진되었습니다.', 429 if personal else 503)
            call_id = 'call_' + uuid4().hex
            values = (call_id,context.owner_id,context.trip_id,context.job_id,context.scope_kind,context.scope_id,provider,sku,operation,attempt,call_key,request_hash,
                      'reserved',currency,_json(estimated_units),cost,self.policy.config['version'],self.policy.config['confirmed_at'],_json(price['rates_per_million']),day,month,stamp,stamp)
            con.execute('INSERT INTO usage_reservations (call_id,owner_id,trip_id,job_id,scope_kind,scope_id,provider,sku,operation,attempt,call_key,request_hash,state,currency,estimated_units_json,estimated_cost_micros,price_version,price_confirmed_at,price_rates_json,period_day,period_month,created_at,updated_at) VALUES (' + ','.join('?' for _ in values) + ')', values)
            self._ledger(con,call_id,'reserved',cost,estimated_units,stamp)
            return dict(con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone())

    @staticmethod
    def _ledger(con, call_id, kind, amount, units, stamp, actor=None, reason=None):
        con.execute('INSERT INTO usage_ledger VALUES(?,?,?,?,?,?,?,?)', ('audit_'+uuid4().hex,call_id,kind,amount,_json(units) if units is not None else None,actor,reason,stamp))

    def get(self, call_id):
        with self.db.connect() as con:
            row = con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone()
            if not row:
                raise _error('CALL_NOT_FOUND', '호출 기록을 찾을 수 없습니다.', 404)
            return dict(row)

    def mark_sent(self, call_id):
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            changed = con.execute("UPDATE usage_reservations SET state='sent',updated_at=? WHERE call_id=? AND state='reserved'", (self._now().isoformat(),call_id)).rowcount
            if changed != 1:
                raise _error('CALL_OUTCOME_UNKNOWN', '이미 전송한 호출입니다. 과금과 응답 확인이 필요합니다.', 409)

    def uncertain(self, call_id, *, pending=False, error_code='PROVIDER_OUTCOME_UNKNOWN', result_ref=None):
        stamp = self._now().isoformat()
        state = 'pending_reconciliation' if pending else 'unknown'
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone()
            if not row or row['state'] in ('settled','released'):
                return
            con.execute('UPDATE usage_reservations SET state=?,error_code=?,result_ref=COALESCE(?,result_ref),updated_at=? WHERE call_id=?', (state,error_code,result_ref,stamp,call_id))
            if not con.execute('SELECT 1 FROM usage_ledger WHERE call_id=? AND entry_kind=?',(call_id,state)).fetchone():
                self._ledger(con,call_id,state,0,None,stamp,reason=error_code)

    def settle(self, call_id, actual_units, *, result_ref=None, actor_id=None, reason_code=None):
        stamp = self._now().isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone()
            if not row:
                raise _error('CALL_NOT_FOUND', '호출 기록을 찾을 수 없습니다.', 404)
            actual = self.policy.calculate(json.loads(row['price_rates_json']), actual_units)
            if row['state'] == 'settled':
                if actual != row['actual_cost_micros'] or _json(actual_units) != row['actual_units_json']:
                    raise _error('SETTLEMENT_CONFLICT', '이미 정산된 호출과 다른 사용량입니다.', 409)
                if result_ref:
                    con.execute('UPDATE usage_reservations SET result_ref=?,updated_at=? WHERE call_id=?',(result_ref,stamp,call_id))
                return dict(con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone())
            if row['state'] not in ('sent','unknown','pending_reconciliation'):
                raise _error('SETTLEMENT_INVALID', '전송되지 않은 호출은 정산할 수 없습니다.', 409)
            if set(actual_units) != set(json.loads(row['price_rates_json'])):
                raise _error('USAGE_INVALID', '일부 사용량이 누락되어 정산할 수 없습니다.', 503)
            con.execute("UPDATE usage_reservations SET state='settled',actual_units_json=?,actual_cost_micros=?,result_ref=COALESCE(?,result_ref),error_code=NULL,updated_at=? WHERE call_id=?", (_json(actual_units),actual,result_ref,stamp,call_id))
            self._ledger(con,call_id,'settled',actual-row['estimated_cost_micros'],actual_units,stamp,actor_id,reason_code)
            if actual > row['estimated_cost_micros']:
                con.execute('INSERT INTO cost_controls VALUES(?,1,?,?) ON CONFLICT(currency) DO UPDATE SET halted=1,reason_code=excluded.reason_code,updated_at=excluded.updated_at', (row['currency'],'ESTIMATE_EXCEEDED',stamp))
                self._ledger(con,call_id,'estimate_exceeded',actual-row['estimated_cost_micros'],None,stamp)
            return dict(con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone())

    def release_unsent(self, call_id, *, reason_code='NOT_SENT', explicitly_unsent=False, guard=None):
        """A generic error is never evidence of no charge. Explicit transport proof only."""
        stamp = self._now().isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            if guard: guard(con=con)
            row = con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone()
            if not row or row['state'] == 'released':
                return
            if row['state'] != 'reserved' and not (row['state']=='sent' and explicitly_unsent):
                raise _error('RECONCILIATION_REQUIRED', '과금 여부 확인 전에는 예약 비용을 해제할 수 없습니다.', 409)
            con.execute("UPDATE usage_reservations SET state='released',error_code=?,updated_at=? WHERE call_id=?", (reason_code,stamp,call_id))
            self._ledger(con,call_id,'released',-row['estimated_cost_micros'],None,stamp,reason=reason_code)

    def reconcile(self, call_id, actual_units, *, actor_id, reason_code):
        """Operator supplies provider-observed usage; original reservation remains auditable."""
        if not reason_code or not re.fullmatch('[A-Z0-9_]{3,80}', reason_code):
            raise _error('RECONCILIATION_REASON_REQUIRED','정산 근거 코드를 지정해 주세요.',422)
        with self.db.connect() as con:
            actor = con.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND status='active'",(actor_id,)).fetchone()
            if not actor:
                raise _error('NOT_FOUND','정산 기록을 찾을 수 없습니다.',404)
        return self.settle(call_id,actual_units,actor_id=actor_id,reason_code=reason_code)

    def summary(self, owner_id=None):
        """No raw provider response or call payload is returned to usage UI."""
        with self.db.connect() as con:
            rows = con.execute('SELECT currency,state,SUM(estimated_cost_micros) estimated_micros,SUM(actual_cost_micros) actual_micros,COUNT(*) calls FROM usage_reservations' + (' WHERE owner_id=?' if owner_id else '') + ' GROUP BY currency,state', (owner_id,) if owner_id else ()).fetchall()
            controls = con.execute('SELECT currency,halted,reason_code FROM cost_controls').fetchall()
        return {'timezone':'UTC','currencies':[dict(row) for row in rows], 'controls':[dict(row) for row in controls],
                'outside_app_usage_observed':False,'provider_billing_may_be_delayed':True}

    def resume_after_review(self, call_id, *, actor_id, reason_code):
        """Explicit admin acknowledgement, without clearing spent/unknown costs."""
        if not reason_code or not re.fullmatch('[A-Z0-9_]{3,80}',reason_code):
            raise _error('RECONCILIATION_REASON_REQUIRED','검토 근거 코드를 지정해 주세요.',422)
        stamp=self._now().isoformat()
        with self.db.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            actor=con.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND status='active'",(actor_id,)).fetchone()
            row=con.execute('SELECT * FROM usage_reservations WHERE call_id=?',(call_id,)).fetchone()
            if not actor or not row:
                raise _error('NOT_FOUND','정산 기록을 찾을 수 없습니다.',404)
            if row['state']!='settled' or row['actual_cost_micros']<=row['estimated_cost_micros']:
                raise _error('RECONCILIATION_REQUIRED','초과 사용량 정산 근거를 먼저 확인해 주세요.',409)
            con.execute('UPDATE cost_controls SET halted=0,reason_code=?,updated_at=? WHERE currency=?',(reason_code,stamp,row['currency']))
            self._ledger(con,call_id,'control_resumed:'+uuid4().hex,0,None,stamp,actor_id,reason_code)
