from datetime import date,datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field,model_validator,field_validator
from src.discovery.models import Input,Party,FactInput

Reason=Literal['distance','price','category','already_visited','schedule','closed','reservation_failed','long_queue','other']
class Feedback(Input):
    feedback_kind:Literal['preference','visit']
    visit_status:Literal['unknown','visited','not_visited','not_yet']='unknown'
    visit_date:date|None=None
    reason_codes:list[Reason]=Field(default_factory=list,max_length=8)
    satisfaction:Literal['good','different','neutral']|None=None
    price_feeling:Literal['cheap','expected','expensive']|None=None
    wait_feeling:Literal['short','expected','long']|None=None
    revisit:bool|None=None
    private_note:str=Field(default='',max_length=1000)
    reflect_preference:bool=False
    run_id:str|None=Field(default=None,max_length=100)
    itinerary_id:str|None=Field(default=None,max_length=100)
    item_id:str|None=Field(default=None,max_length=100)
    @model_validator(mode='after')
    def experiences(self):
        if self.feedback_kind=='preference' and self.visit_status!='unknown':raise ValueError('추천 선호와 방문 경험은 따로 입력해 주세요.')
        if self.visit_status!='visited' and any(v is not None for v in (self.satisfaction,self.price_feeling,self.wait_feeling,self.revisit)):raise ValueError('방문한 경우에만 현장 경험을 평가할 수 있습니다.')
        if bool(self.itinerary_id)!=bool(self.item_id):raise ValueError('일정과 항목을 함께 선택해 주세요.')
        if self.reflect_preference and self.feedback_kind!='preference':raise ValueError('다음 추천 반영은 별도 관심 없음 입력에서 선택해 주세요.')
        return self
class FeedbackPatch(Input):
    expected_version:int=Field(ge=1)
    feedback:Feedback
class Version(Input):
    expected_version:int=Field(ge=1)
class Consent(Input):
    expected_version:int=Field(ge=0)
    analytics_enabled:bool
class ClientEvent(Input):
    event_id:UUID
    event_name:Literal['recommendation_view','source_open']
    schema_version:Literal[1]=1
    run_id:str|None=Field(default=None,max_length=100)
    place_id:str=Field(min_length=1,max_length=100)
    source_id:str|None=Field(default=None,max_length=100)
    client_at:datetime
    @field_validator('client_at')
    @classmethod
    def aware(cls,v):
        if not v.tzinfo:raise ValueError('Timezone required')
        return v
    @model_validator(mode='after')
    def required_refs(self):
        if self.event_name=='recommendation_view' and not self.run_id:raise ValueError('run required')
        if self.event_name=='source_open' and not self.source_id:raise ValueError('source required')
        return self
class FactReport(Input):
    fact_id:str=Field(min_length=1,max_length=100)
    category:Literal['branch','hours','price','reservation','party','other']
    observed_date:date|None=None
    description:str=Field(default='',max_length=600)
class ReviewReport(Input):
    expected_version:int=Field(ge=1)
    status:Literal['triaged','needs_evidence','resolved','dismissed']
    reason_code:Literal['checking','official_update','insufficient_evidence','no_change','duplicate']
    correction:FactInput|None=None
    official_source_read:bool=False
    @model_validator(mode='after')
    def resolution(self):
        if self.status=='resolved' and (not self.correction or not self.official_source_read or self.reason_code!='official_update'):raise ValueError('공식 근거 확인과 수정 사실이 있어야 해결할 수 있습니다.')
        if self.status!='resolved' and self.correction:raise ValueError('수정 사실은 해결 단계에서만 적용합니다.')
        return self
class ChildRate(Input):
    min_age:int=Field(ge=0,le=17)
    max_age:int=Field(ge=0,le=17)
    amount_min:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    amount_max:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
class Deposit(Input):
    kind:Literal['part_payment','additional_fee','refundable']
    amount:Decimal=Field(ge=0,max_digits=12,decimal_places=2)
class Price(Input):
    currency:Literal['JPY','EUR']
    basis:Literal['per_person','group','per_booking']
    amount_min:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    amount_max:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    period:Literal['meal','visit','day']='visit'
    tax:Literal['included','excluded','unknown']='unknown'
    tax_rate:Decimal|None=Field(default=None,ge=0,le=1,decimal_places=4)
    children_same_price:bool|None=None
    child_rates:list[ChildRate]=Field(default_factory=list,max_length=18)
    fee_min:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    fee_max:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    fees_known:bool=False
    deposit:Deposit|None=None
    prepaid:Decimal=Field(default=Decimal('0'),ge=0,max_digits=12,decimal_places=2)
    cancellation_fee:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    min_party:int|None=Field(default=None,ge=1,le=80)
    max_party:int|None=Field(default=None,ge=1,le=80)
    @model_validator(mode='after')
    def bounds(self):
        ranges=[(self.amount_min,self.amount_max),(self.fee_min,self.fee_max)]+[(r.amount_min,r.amount_max) for r in self.child_rates]
        if any(a is not None and b is not None and a>b for a,b in ranges):raise ValueError('가격 하한이 상한보다 큽니다.')
        if self.currency=='JPY':
            values=[v for pair in ranges for v in pair]+[self.prepaid,self.cancellation_fee,self.deposit.amount if self.deposit else None]
            if any(v is not None and v!=v.to_integral_value() for v in values):raise ValueError('JPY는 정수 엔 단위입니다.')
        seen=set()
        for r in self.child_rates:
            ages=set(range(r.min_age,r.max_age+1))
            if not ages or seen&ages:raise ValueError('아동 요금 연령 구간이 겹치거나 잘못되었습니다.')
            seen|=ages
        if self.min_party and self.max_party and self.min_party>self.max_party:raise ValueError('인원 범위 오류')
        return self
class Expense(Input):
    days:int|None=Field(default=None,ge=1,le=365)
    expected_version:int=Field(ge=0)
    itinerary_version:int=Field(ge=1)
    itinerary_id:str=Field(max_length=100)
    item_key:str=Field(max_length=250)
    price:Price|None=None
    party:Party|None=None
    included_by:str|None=Field(default=None,max_length=250)
    @model_validator(mode='after')
    def exclusive(self):
        if self.included_by==self.item_key:raise ValueError('자기 자신에 포함될 수 없습니다.')
        if self.included_by and self.price is not None:raise ValueError('다른 항목에 포함된 비용에 별도 가격을 입력하지 마세요.')
        return self
class Evaluation(Input):
    candidate_version:Literal['diversity-v2','distance-v2','movement-v2']='diversity-v2'
