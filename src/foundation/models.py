"""Validated write contracts. Local date-only facts never become midnight instants."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def valid_date(value: str | None) -> str | None:
    if value is not None:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
    return value


def valid_zone(value: str | None) -> str | None:
    if value is not None:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('올바른 IANA 시간대를 입력해 주세요.') from exc
    return value


def local_to_instant(local: str | None, zone: str | None) -> str | None:
    if local is None or zone is None or len(local) == 10:
        return None
    parsed = datetime.fromisoformat(local)
    if parsed.tzinfo is not None:
        raise ValueError('현지 시각과 시간대를 별도로 입력해 주세요.')
    tz = ZoneInfo(zone)
    candidates = set()
    for fold in (0, 1):
        instant = parsed.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(tz).replace(tzinfo=None) == parsed:
            candidates.add(instant.isoformat())
    if len(candidates) != 1:
        raise ValueError('서머타임으로 없거나 중복되는 시각입니다. 시각을 확인해 주세요.')
    return candidates.pop()


class Child(StrictModel):
    age: int | None = Field(default=None, ge=0, le=17)


class Party(StrictModel):
    adults: int = Field(default=1, ge=1, le=50)
    children: list[Child] = Field(default_factory=list, max_length=30)


class StopInput(StrictModel):
    city: str = Field(min_length=1, max_length=120)
    sequence: int = Field(ge=1, le=100)
    start_date: str
    end_date: str
    timezone: str
    base_location: str | None = Field(default=None, max_length=500)

    _dates = field_validator('start_date', 'end_date')(valid_date)
    _zone = field_validator('timezone')(valid_zone)

    @model_validator(mode='after')
    def date_order(self):
        if self.end_date < self.start_date:
            raise ValueError('체류 종료일이 시작일보다 빠릅니다.')
        return self


class TripCreate(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    start_date: str
    end_date: str
    party: Party = Field(default_factory=Party)
    stops: list[StopInput] = Field(default_factory=list, max_length=100)

    _dates = field_validator('start_date', 'end_date')(valid_date)

    @model_validator(mode='after')
    def date_order(self):
        if self.end_date < self.start_date:
            raise ValueError('여행 종료일이 시작일보다 빠릅니다.')
        if len({stop.sequence for stop in self.stops}) != len(self.stops):
            raise ValueError('도시 순서는 중복될 수 없습니다.')
        for stop in self.stops:
            if stop.start_date < self.start_date or stop.end_date > self.end_date:
                raise ValueError('도시 체류일이 여행 기간을 벗어났습니다.')
        return self


class TripPatch(StrictModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    start_date: str | None = None
    end_date: str | None = None
    party: Party | None = None
    stops: list[StopInput] | None = Field(default=None, max_length=100)

    _dates = field_validator('start_date', 'end_date')(valid_date)


class EventInput(StrictModel):
    event_type: str = Field(default='visit', min_length=1, max_length=60)
    start_local: str | None = None
    end_local: str | None = None
    start_timezone: str | None = None
    end_timezone: str | None = None
    location: str | None = Field(default=None, max_length=1000)

    _zones = field_validator('start_timezone', 'end_timezone')(valid_zone)

    @field_validator('start_local', 'end_local')
    @classmethod
    def local_time(cls, value):
        if value is not None:
            if len(value) == 10:
                return valid_date(value)
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is not None or 'T' not in value:
                raise ValueError('현지 시각은 offset 없는 ISO 날짜·시각으로 입력해 주세요.')
        return value

    @model_validator(mode='after')
    def event_order(self):
        start = local_to_instant(self.start_local, self.start_timezone)
        end = local_to_instant(self.end_local, self.end_timezone)
        if start and end and datetime.fromisoformat(end) < datetime.fromisoformat(start):
            raise ValueError('종료 시각이 시작 시각보다 빠릅니다.')
        return self


class BookingInput(StrictModel):
    place_id: str | None = Field(default=None, max_length=100)
    party: Party | None = None
    kind: str | None = Field(default=None, max_length=80)
    provider: str | None = Field(default=None, max_length=300)
    confirmation_number: str | None = Field(default=None, max_length=200)
    date: str | None = None
    date_end: str | None = None
    time: str | None = None
    time_end: str | None = None
    location: str | None = Field(default=None, max_length=1000)
    refund_policy: str | None = Field(default=None, max_length=12000)
    raw_snippet: str | None = Field(default=None, max_length=12000)
    status: Literal['needs_review', 'source_verified', 'user_confirmed', 'cancelled'] = 'needs_review'
    events: list[EventInput] = Field(default_factory=list, max_length=32)
    stable_item_key: str | None = Field(default=None, max_length=200)

    _dates = field_validator('date', 'date_end')(valid_date)

    @field_validator('time', 'time_end')
    @classmethod
    def clock_time(cls, value):
        if value is not None:
            parsed = time.fromisoformat(value)
            if parsed.tzinfo is not None or len(value) not in (5, 8):
                raise ValueError('시각은 HH:MM 또는 HH:MM:SS 형식이어야 합니다.')
        return value

    @model_validator(mode='after')
    def date_order(self):
        if self.date and self.date_end and self.date_end < self.date:
            raise ValueError('예약 종료일이 시작일보다 빠릅니다.')
        return self


class BookingCreate(BookingInput):
    """Manual input; status still explicitly distinguishes confirmation evidence."""


class FieldChange(StrictModel):
    field_path: str = Field(min_length=1, max_length=200)
    value: Any
    remove_override: bool = False


class BookingPatch(StrictModel):
    expected_version: int = Field(ge=1)
    changes: list[FieldChange] = Field(min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def unique_fields(self):
        if len({change.field_path for change in self.changes}) != len(self.changes):
            raise ValueError('같은 필드를 한 요청에서 두 번 수정할 수 없습니다.')
        return self
