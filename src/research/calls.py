"""Common budget ledger + external slot, with SQL receipts of safe projections only."""
from __future__ import annotations
import json
from decimal import Decimal
from src.foundation.repository import DomainError
from .service import encoded

class ResearchCalls:
    def __init__(self, service, run, context, guard):
        self.service,self.run,self.context,self.guard=service,run,context,guard
        self.budget=service.gateway.budget

    def actor_charge_ceiling(self):
        request=json.loads(self.run['request_json']); fees=0
        for op,count in [('review_poll',min(150,request['max_elapsed_seconds'])+3),('review_page',request['max_pages']*request['max_attempts_per_page']),('review_delete_dataset',1),('review_delete_run',1),('review_abort',1)]:
            price=self.budget.policy.price('apify',op)
            if price['currency']!='USD': raise DomainError('COST_BOUND_REQUIRED','리뷰 예산 단위는 USD입니다.',503)
            fees+=self.budget.policy.calculate(price['rates_per_million'],price['max_units'])*count
        cap=Decimal(request['max_total_charge_usd'])-Decimal(fees)/1000000
        if cap<=0: raise DomainError('BUDGET_EXHAUSTED','조회·삭제 비용을 포함할 실행 예산이 부족합니다.',429)
        return str(cap)

    def reserve_cleanup(self):
        # Reserve both deletion requests before provider-side raw storage exists.
        for operation in ('review_delete_dataset','review_delete_run','review_abort'):
            self._reserve(operation,operation,self.run['input_hash']+':'+operation,1)
        for n in range(1,4):
            key='cleanup_poll:'+str(n)
            self._reserve('review_poll',key,self.run['input_hash']+':'+key,1)

    def _reserve(self,operation,key,request_hash,attempt):
        provider=self.service.provider.name
        price=self.budget.policy.price(provider,operation)
        bounds=dict(price['max_units'])
        request=json.loads(self.run['request_json'])
        if provider=='apify' and operation=='review_start':
            if set(bounds)!={'usd_micros'} or Decimal(str(price['rates_per_million']['usd_micros']))!=1:
                raise DomainError('COST_BOUND_REQUIRED','실행 비용은 USD micro 단위와 검토한 상한이 필요합니다.',503)
            amount=int(Decimal(self.actor_charge_ceiling())*1000000)
            if not 0<amount<=bounds['usd_micros']:
                raise DomainError('COST_BOUND_EXCEEDED','실행 비용 상한과 설정을 확인해 주세요.',422)
            bounds={'usd_micros':amount}
        if provider=='apify' and operation!='review_start' and set(bounds)!={'requests'}:
            raise DomainError('COST_BOUND_REQUIRED','조회·삭제 요청별 비용 상한이 필요합니다.',503)
        # The globally atomic ledger remains authoritative. Every run uses a
        # single dispatcher, and a fence before each call excludes old workers.
        self.guard()
        record=self.budget.reserve(self.context,provider=provider,sku=operation,operation=operation,
            call_key=key,estimated_units=bounds,request_hash=request_hash,attempt=attempt)
        if request and not operation.startswith('review_delete_') and operation!='review_abort':
            ceiling=int(Decimal(request['max_total_charge_usd'])*1000000)
            with self.service.db.connect() as con:
                totals=con.execute("SELECT currency,SUM(CASE WHEN state='released' THEN 0 WHEN state='settled' THEN actual_cost_micros ELSE estimated_cost_micros END) spent FROM usage_reservations WHERE job_id=? GROUP BY currency",(self.run['job_id'],)).fetchall()
            if any(r['currency']!='USD' or r['spent']>ceiling for r in totals):
                if record['state']=='reserved': self.budget.release_unsent(record['call_id'],reason_code='RUN_BUDGET_EXHAUSTED')
                raise DomainError('BUDGET_EXHAUSTED','이번 수집의 비용 상한에 도달했습니다.',429)
        return record,price

    def run_call(self, operation, key, request_hash, invoke, *, project=lambda x:x, attempt=1):
        provider=self.service.provider.name
        record,price=self._reserve(operation,key,request_hash,attempt)
        try: self.guard()
        except BaseException:
            if record['state']=='reserved': self.budget.release_unsent(record['call_id'],reason_code='GUARD_REJECTED')
            raise
        with self.service.db.connect() as con:
            receipt=con.execute('SELECT * FROM review_call_receipts WHERE call_id=?',(record['call_id'],)).fetchone()
        if receipt:
            units=json.loads(receipt['units_json']) if receipt['units_json'] else None
            if units is not None: self.budget.settle(record['call_id'],units,result_ref='review:'+self.run['id'])
            else: self.budget.uncertain(record['call_id'],pending=True,result_ref='review:'+self.run['id'],error_code='USAGE_PENDING')
            return json.loads(receipt['safe_value_json'])
        if record['state'] in {'sent','unknown','pending_reconciliation','settled'}:
            if record['state']=='sent': self.budget.uncertain(record['call_id'])
            raise DomainError('CALL_OUTCOME_UNKNOWN','이전 호출 결과·과금을 확인해야 합니다. 자동 재호출을 중단했습니다.',409)
        if record['state']=='released': raise DomainError('CALL_NOT_SENT','전송되지 않은 호출은 새 시도가 필요합니다.',409)
        sent=False; units=None
        try:
            with self.service.gateway._external_slot:
                self.guard()
                self.budget.mark_sent(record['call_id']); sent=True
                response=invoke()
            # Each projection is allowlisted at the caller, before any durable write.
            if provider=='fake': units={unit:(1 if unit=='calls' else 0) for unit in price['max_units']}
            # Apify request counts do not confirm final tier/storage billing.
            # Keep real reservations pending until an audited final invoice.
            safe=project(response)
            if self.service.fault_hook: self.service.fault_hook('review_response_received',self.run['id'])
            with self.service.db.connect() as con:
                con.execute('BEGIN IMMEDIATE'); self.guard(con=con)
                con.execute('INSERT OR IGNORE INTO review_call_receipts VALUES(?,?,?,?,?)',
                    (record['call_id'],self.run['id'],encoded(safe),encoded(units) if units is not None else None,self.service.now().isoformat()))
            if units is not None: self.budget.settle(record['call_id'],units,result_ref='review:'+self.run['id'])
            else: self.budget.uncertain(record['call_id'],pending=True,result_ref='review:'+self.run['id'],error_code='USAGE_PENDING')
            self.guard()
            return safe
        except BaseException:
            if not sent: self.budget.release_unsent(record['call_id'],reason_code='GUARD_REJECTED')
            elif units is not None: self.budget.settle(record['call_id'],units)
            else: self.budget.uncertain(record['call_id'])
            raise
