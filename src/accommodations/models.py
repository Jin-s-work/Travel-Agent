"""Private write contracts; identity coordinates may only come from server candidates."""
from datetime import time
from typing import Literal
from pydantic import Field, field_validator, model_validator
from src.foundation.models import StrictModel, valid_date, valid_zone, local_to_instant

class AccommodationInput(StrictModel):
    stop_id: str = Field(min_length=1,max_length=100)
    display_name: str | None = Field(default=None,max_length=300)
    input_kind: Literal['name','map_url','booking'] = 'name'
    input_value: str = Field(default='',max_length=2000)
    private_note: str = Field(default='',max_length=2000)
    booking_id: str | None = Field(default=None,max_length=100)
    checkin_date: str | None = None
    checkout_date: str | None = None
    checkin_time: str | None = None
    checkout_time: str | None = None
    facility_timezone: str | None = None
    dates_confirmed: bool = False
    _dates=field_validator('checkin_date','checkout_date')(valid_date)
    _zone=field_validator('facility_timezone')(valid_zone)
    @field_validator('checkin_time','checkout_time')
    @classmethod
    def precise_time(cls,value):
        if value is not None and (len(value)!=5 or time.fromisoformat(value).isoformat(timespec='minutes')!=value):
            raise ValueError('시각은 HH:MM 형식으로 입력해 주세요.')
        return value
    @model_validator(mode='after')
    def valid_range(self):
        if self.checkin_date and self.checkout_date and self.checkout_date<=self.checkin_date:
            raise ValueError('체크아웃은 체크인 다음 날짜부터 지정해 주세요.')
        for boundary in ('checkin','checkout'):
            day,clock=getattr(self,boundary+'_date'),getattr(self,boundary+'_time')
            if clock and not day: raise ValueError('시각을 입력하려면 날짜가 필요합니다.')
            if day and clock and self.facility_timezone: local_to_instant(day+'T'+clock,self.facility_timezone)
        if self.input_kind=='booking' and not self.booking_id: raise ValueError('연결할 숙박 예약을 선택해 주세요.')
        if self.input_kind!='booking' and not self.input_value.strip(): raise ValueError('숙소 이름 또는 지도 링크를 입력해 주세요.')
        return self

class AccommodationPatch(AccommodationInput):
    expected_version: int = Field(ge=1)

class VersionInput(StrictModel):
    expected_version: int = Field(ge=1)

class ResolutionInput(VersionInput):
    max_candidates: int = Field(default=5,ge=1,le=5)
    consent_to_provider: bool = False

class CandidateSelection(VersionInput):
    candidate_id: str = Field(min_length=1,max_length=200)
