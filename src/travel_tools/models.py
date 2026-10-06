from datetime import date, time, datetime
from typing import Literal
from pydantic import Field, field_validator
from src.discovery.models import Input, Party, Origin

class TaskCreate(Input):
    title: str = Field(min_length=1,max_length=200)
    place_id: str | None = Field(default=None,max_length=100)
    item_id: str | None = Field(default=None,max_length=100)
    itinerary_id: str | None = Field(default=None,max_length=100)
    booking_id: str | None = Field(default=None,max_length=100)
    task_kind: Literal['open_check','reserve','party_inquiry','cancel_check'] = 'reserve'
    visit_date: date
    requested_time: time | None = None
    timezone: Literal['Asia/Tokyo','Europe/Madrid']
    party: Party | None = None
    rule_fact_id: str | None = Field(default=None,max_length=100)
    request_keys: list[Literal['vegetarian','vegan','nut_allergy','gluten_free','step_free']] = Field(default_factory=list,max_length=5)
    requests: str = Field(default='',max_length=1000)
    @field_validator('requested_time')
    @classmethod
    def local_time(cls,v):
        if v and v.tzinfo: raise ValueError('Use a local time and a separate timezone')
        return v

class TaskCommand(Input):
    expected_version: int = Field(ge=1)
    action: Literal['start','report_complete','verify_evidence','cancel','reopen','revalidate','update']
    booking_id: str | None = Field(default=None,max_length=100)
    changes: TaskCreate | None = None

class Inquiry(Input):
    expected_version: int = Field(ge=1)
    language: Literal['ja','es','ca']

class Alternative(Input):
    expected_version: int = Field(ge=1)
    item_id: str = Field(min_length=1,max_length=100)
    reason: Literal['rain','closed','long_queue','fatigue','reservation_failed']
    basis: Literal['user_report','provider_verified','assumption'] = 'user_report'
    evidence_fact_id: str | None = Field(default=None,max_length=100)
    origin: Origin | None = None
    # Omission reuses the saved starting point; new limits can only narrow it.
    radius_m: int | None = Field(default=None,ge=100,le=50000)
    duration_minutes: int | None = Field(default=None,ge=5,le=720)

class OfflineRequest(Input):
    include_notes: bool = False
    private_device_confirmed: Literal[True]

class OfflinePermission(Input):
    source_id: str = Field(min_length=1,max_length=100)
    source_version: int = Field(ge=1)
    fields: list[Literal['name','native_name','address','map_url']] = Field(max_length=4)
    expires_at: datetime
    policy_version: str = Field(min_length=1,max_length=100)
    @field_validator('expires_at')
    @classmethod
    def aware(cls,v):
        if not v.tzinfo: raise ValueError('Use an aware timestamp')
        return v

class OfflineCheck(Input):
    manifest: str = Field(pattern=r'^[a-f0-9]{64}$')
    include_notes: bool = False
