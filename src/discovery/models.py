from __future__ import annotations
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal
import json
from typing import Any,Literal
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator
from src.destinations import CITIES,CityId,Currency,IanaTimezone


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Child(Input):
    age:int|None=Field(default=None,ge=0,le=17)
class Party(Input):
    adults:int=Field(ge=1,le=50)
    children:list[Child]=Field(default_factory=list,max_length=30)
    children_status: Literal['unknown','none','present'] = 'unknown'

    @model_validator(mode='after')
    def child_status(self):
        if self.children:
            if self.children_status == 'none': raise ValueError('아동 없음과 아동 인원을 함께 지정할 수 없습니다.')
            self.children_status = 'present'
        elif self.children_status == 'present': raise ValueError('아동 인원과 나이를 입력해 주세요. 나이는 미확인으로 둘 수 있습니다.')
        return self

class Visit(Input):
    date:date
    local_time:time|None=None
    timezone:IanaTimezone
    @field_validator('local_time')
    @classmethod
    def local_only(cls,value):
        if value is not None and value.tzinfo is not None:raise ValueError('Local time and timezone must be separate')
        return value
class Budget(Input):
    currency:Currency
    amount_min:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    amount_max:Decimal|None=Field(default=None,ge=0,max_digits=12,decimal_places=2)
    basis:Literal['per_person','group']='per_person'
    period:Literal['meal','day','visit']='meal'
    @model_validator(mode='after')
    def bounds(self):
        if self.amount_min is not None and self.amount_max is not None and self.amount_min>self.amount_max:raise ValueError('amount_max must be at least amount_min')
        if self.currency=='JPY' and any(v is not None and v!=v.to_integral_value() for v in (self.amount_min,self.amount_max)):raise ValueError('JPY amounts use whole yen')
        return self
class Origin(Input):
    label:str=Field(default='',max_length=300)
    latitude:float|None=Field(default=None,ge=-90,le=90)
    longitude:float|None=Field(default=None,ge=-180,le=180)
    place_id:str|None=Field(default=None,max_length=100)
    @model_validator(mode='after')
    def paired(self):
        if (self.latitude is None)!=(self.longitude is None):raise ValueError('Provide both location coordinates')
        return self
class Required(Input):
    dietary:list[str]=Field(default_factory=list,max_length=20)
    accessibility:list[str]=Field(default_factory=list,max_length=20)
class Preferred(Input):
    tags:list[str]=Field(default_factory=list,max_length=30)
    dietary:list[str]=Field(default_factory=list,max_length=20)
class Conditions(Input):
    city:CityId
    visit:Visit
    party:Party
    categories:list[Literal['restaurant','cafe','attraction']]=Field(default=['restaurant'],min_length=1,max_length=3)
    recommendation_types:list[Literal['local_discovery','landmark']]=Field(default=['local_discovery','landmark'],min_length=1,max_length=2)
    budget:Budget|None=None
    origin:Origin|None=None
    density:Literal['relaxed','balanced','packed']='balanced'
    transport:Literal['walking','transit','car']='walking'
    meal_time:Literal['breakfast','lunch','dinner']|None=None
    radius_m:int|None=Field(default=None,ge=100,le=50000)
    required:Required=Field(default_factory=Required)
    preferred:Preferred=Field(default_factory=Preferred)
    @model_validator(mode='after')
    def city_rules(self):
        zone=CITIES[self.city]['timezone']
        if self.visit.timezone!=zone:raise ValueError('Timezone must match city')
        if len(set(self.categories))!=len(self.categories) or len(set(self.recommendation_types))!=len(self.recommendation_types):raise ValueError('Duplicate selections')
        for values in (self.required.dietary,self.required.accessibility,self.preferred.tags,self.preferred.dietary):
            if any(not value.strip() or len(value)>100 for value in values):raise ValueError('Preference values must be 1 to 100 characters')
        return self
class ConditionsPatch(Input):
    expected_version:int=Field(ge=0)
    conditions:Conditions
class BookmarkInput(Input):
    run_id:str|None=Field(default=None,max_length=100)
    input_kind:Literal['url','name','place']
    input_value:str=Field(min_length=1,max_length=2000)
    note:str=Field(default='',max_length=3000)
    @field_validator('input_value')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('A place name or URL is required')
        return value.strip()
class BookmarkPatch(Input):
    expected_version:int=Field(ge=1)
    note:str=Field(max_length=3000)
class VersionInput(Input):
    expected_version:int=Field(ge=1)
class Selection(VersionInput):
    place_id:str=Field(max_length=100)
class SourceInput(Input):
    key:str=Field(min_length=1,max_length=100)
    url:str=Field(min_length=8,max_length=2000)
    source_type:Literal['official','tourism','editorial','provider','synthetic','user']
    source_group:str=Field(min_length=1,max_length=150)
    checked_at:datetime
    published_at:datetime|None=None
    read_confirmed:bool=False
    display_permitted:bool=False
    evidence_note:str=Field(default='',max_length=1000)
    @field_validator('checked_at','published_at')
    @classmethod
    def aware(cls,value):
        if value is not None and value.tzinfo is None:raise ValueError('Timezone offset required')
        if value is not None and value>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('Read and publication times cannot be in the future')
        return value
FACT_FIELDS={'opening_hours','reservation_required','reservation_url','reservation_methods','booking_open_rule',
    'min_party','max_party','children_rule','facility_capacity','cancellation_policy','deposit','live_availability',
    'price','closed','category','tags','local_evidence','iconic_evidence','location','dietary','accessibility','rating',
    'exceptional_closures','break_times','last_order','last_entry','weekly_intervals'}
class FactInput(Input):
    field:str
    value:Any=None
    status:Literal['verified','provisional','unknown','conflict']='unknown'
    source_key:str|None=None
    checked_at:datetime
    valid_for_date:date|None=None
    valid_from:date|None=None
    valid_until:date|None=None
    expires_at:datetime
    @model_validator(mode='after')
    def validate_fact(self):
        if self.field not in FACT_FIELDS:raise ValueError('Unsupported fact field')
        if self.checked_at.tzinfo is None or self.expires_at.tzinfo is None:raise ValueError('Timezone offset required')
        if self.checked_at>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('Fact verification cannot be in the future')
        if self.expires_at<=self.checked_at:raise ValueError('Expiry must follow verification')
        if self.valid_from and self.valid_until and self.valid_from>self.valid_until:raise ValueError('Invalid validity interval')
        if len(json.dumps(self.value,ensure_ascii=False,allow_nan=False))>12000:raise ValueError('Fact is too large')
        if self.status=='verified' and not self.source_key:raise ValueError('Verified fact needs a read source')
        if self.status=='verified' and self.value is None:raise ValueError('Verified value cannot be unknown')
        if self.value is not None and self.field in {'min_party','max_party','facility_capacity'} and (type(self.value) is not int or not 0<=self.value<=100000):raise ValueError('Capacity requires an integer')
        if self.value is not None and self.field=='closed' and type(self.value) is not bool:raise ValueError('Closed requires boolean')
        if self.field=='reservation_required' and self.value is not None:
            if type(self.value) is not bool and self.value not in ('required','recommended','optional','unknown'):raise ValueError('Reservation requirement needs a known enum or boolean')
        if self.field=='price' and self.value is not None:
            if not isinstance(self.value,dict):raise ValueError('Price requires structured amount and basis')
            if not {'currency','basis','period'}<=self.value.keys():raise ValueError('Price requires explicit currency, party basis and period')
            if self.status=='verified' and all(self.value.get(k) is None for k in ('amount_min','amount_max')):raise ValueError('Verified price requires a known amount')
            Budget.model_validate({k:v for k,v in self.value.items() if k in Budget.model_fields})
            for key in ('amount_min','amount_max'):
                value=self.value.get(key)
                if value is not None:
                    if not isinstance(value,str):raise ValueError('Money amounts must be decimal strings')
                    amount=Decimal(value)
                    if self.value['currency']=='JPY' and amount!=amount.to_integral_value():raise ValueError('JPY amounts use whole yen')
            if self.value.get('tax_status',self.value.get('tax','unknown')) not in ('included','excluded','unknown'):raise ValueError('Invalid tax status')
        if self.field in {'opening_hours','weekly_intervals'} and self.value is not None:
            if not isinstance(self.value,dict):raise ValueError('Opening hours require structured intervals')
            if self.field=='opening_hours':
                try:ZoneInfo(self.value.get('timezone',''))
                except (ZoneInfoNotFoundError,ValueError,TypeError):raise ValueError('Opening hours require a valid IANA timezone') from None
                weekly=self.value.get('weekly',{})
            else:weekly=self.value
            if not isinstance(weekly,dict):raise ValueError('Weekly intervals require weekday keys')
            days={str(i) for i in range(7)}|{'monday','tuesday','wednesday','thursday','friday','saturday','sunday'}
            for day,intervals in weekly.items():
                if day not in days or not isinstance(intervals,list) or len(intervals)>8:raise ValueError('Invalid weekday intervals')
                for interval in intervals:
                    if isinstance(interval,list) and len(interval)==2:start,end=interval;offset=0
                    elif isinstance(interval,dict):start,end,offset=interval.get('start'),interval.get('end'),interval.get('end_day_offset',0)
                    else:raise ValueError('Invalid opening interval')
                    if not isinstance(start,str) or not isinstance(end,str):raise ValueError('Opening intervals require start and end clock times')
                    a,b=time.fromisoformat(start),time.fromisoformat(end)
                    if a.tzinfo or b.tzinfo or offset not in (0,1) or (offset==0 and b<=a):raise ValueError('Overnight intervals need explicit next-day offset')
        if self.field=='reservation_url' and self.value is not None:
            from .safe_fetch import validate_public_url
            validate_public_url(self.value)
        if self.field in {'local_evidence','iconic_evidence','location','dietary','accessibility','rating'} and self.value is not None and not isinstance(self.value,dict):raise ValueError('Fact requires structured value')
        if self.field in {'local_evidence','iconic_evidence'} and self.value is not None:
            groups=self.value.get('source_groups',[])
            if not isinstance(groups,list) or len(groups)>30 or any(not isinstance(group,str) or not group.strip() or len(group)>150 for group in groups):raise ValueError('Evidence source groups require bounded publisher identities')
            if len(set(groups))!=len(groups):raise ValueError('Evidence source groups must be distinct')
            if 'direct_confirmation' in self.value and type(self.value['direct_confirmation']) is not bool:raise ValueError('Direct confirmation requires boolean')
            if 'level' in self.value and self.value['level'] not in ('city','national','world'):raise ValueError('Invalid landmark significance')
            if 'summary' in self.value and (not isinstance(self.value['summary'],str) or len(self.value['summary'])>2000):raise ValueError('Evidence summary must be bounded text')
        if self.field=='rating' and self.value is not None:
            if not {'platform','scale','rating','total_rating_count','usage_permitted'}<=self.value.keys():raise ValueError('Rating requires its platform, scale, count and use permission')
            if not isinstance(self.value['platform'],str) or not self.value['platform'].strip() or len(self.value['platform'])>100:raise ValueError('Rating requires a platform identity')
            scale,rating,count=self.value['scale'],self.value['rating'],self.value['total_rating_count']
            if type(scale) not in (int,float) or not 0<scale<=100 or type(rating) not in (int,float) or not 0<=rating<=scale:raise ValueError('Invalid rating scale or amount')
            if type(count) is not int or not 0<=count<=1000000000 or type(self.value['usage_permitted']) is not bool:raise ValueError('Invalid rating count or use permission')
        if self.field in {'dietary','accessibility'} and self.value is not None:
            if len(self.value)>30 or any(not isinstance(key,str) or not key.strip() or len(key)>100 or value is not None and type(value) is not bool for key,value in self.value.items()):raise ValueError('Requirement facts use boolean or unknown values')
        if self.field=='live_availability' and self.status=='verified':
            if not isinstance(self.value,dict) or not all(k in self.value for k in ('visit','party','checked_at','provider','slots')):raise ValueError('Availability requires queried visit, party, checked time and slots')
            visit=Visit.model_validate(self.value['visit']);Party.model_validate(self.value['party'])
            if visit.local_time is None:raise ValueError('Live availability requires a requested local clock time')
            if not isinstance(self.value['provider'],str) or not self.value['provider'].strip() or len(self.value['provider'])>100:raise ValueError('Live availability requires provider identity')
            if not isinstance(self.value['checked_at'],str):raise ValueError('Slot checked_at requires ISO timestamp')
            checked=datetime.fromisoformat(self.value['checked_at'].replace('Z','+00:00'))
            if checked.tzinfo is None or checked>self.checked_at or checked>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('Invalid slot verification time')
            slots=self.value['slots']
            if not isinstance(slots,list) or len(slots)>100:raise ValueError('Slot response requires bounded structured slots')
            for slot in slots:
                if not isinstance(slot,dict) or set(slot)-{'local_time','available','remaining','reservation_url'} or 'local_time' not in slot or type(slot.get('available')) is not bool:raise ValueError('Invalid slot structure')
                if not isinstance(slot['local_time'],str) or time.fromisoformat(slot['local_time']).tzinfo is not None:raise ValueError('Invalid slot local time')
                if slot.get('remaining') is not None and (type(slot['remaining']) is not int or not 0<=slot['remaining']<=100000):raise ValueError('Invalid slot remaining count')
                if slot.get('reservation_url') is not None:
                    from .safe_fetch import validate_public_url
                    validate_public_url(slot['reservation_url'])
        return self
class CandidateInput(Input):
    external_id:str=Field(min_length=1,max_length=200)
    name:str=Field(min_length=1,max_length=200)
    native_name:str|None=Field(default=None,max_length=200)
    address:str=Field(min_length=4,max_length=400)
    category:Literal['restaurant','cafe','attraction']
    canonical_url:str=Field(max_length=2000)
    chain_id:str|None=Field(default=None,max_length=100)
    neighborhood:str|None=Field(default=None,max_length=150)
    tags:list[str]=Field(default_factory=list,max_length=30)
    recommendation_types:list[Literal['local_discovery','landmark']]=Field(min_length=1,max_length=2)
    latitude:float|None=Field(default=None,ge=-90,le=90)
    longitude:float|None=Field(default=None,ge=-180,le=180)
    sources:list[SourceInput]=Field(default_factory=list,max_length=30)
    facts:list[FactInput]=Field(default_factory=list,max_length=60)
class PackInput(Input):
    version:str=Field(min_length=1,max_length=100)
    city:CityId
    synthetic:bool=False
    places:list[CandidateInput]=Field(min_length=1,max_length=50)
class Approval(Input):
    status:Literal['approved','needs_review','disabled']
    evidence:str=Field(min_length=10,max_length=1000)
class SourceReview(Input):
    expected_version:int=Field(ge=1)
    status:Literal['active','pending','revoked']
    read_confirmed:bool
    display_permitted:bool
    policy_version:str=Field(min_length=1,max_length=100)
    evidence:str=Field(min_length=10,max_length=1000)
    checked_at:datetime|None=None
    @field_validator('checked_at')
    @classmethod
    def checked_time(cls,value):
        if value is not None and value.tzinfo is None:raise ValueError('Timezone offset required')
        if value is not None and value>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('Source verification cannot be in the future')
        return value
