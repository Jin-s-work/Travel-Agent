from __future__ import annotations
from datetime import date,datetime,time
from typing import Annotated,Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class SelectedPlace(Input):
    place_id:str=Field(min_length=1,max_length=100)
    duration_minutes:int=Field(default=60,ge=5,le=720)
    duration_origin:Literal['default','user']='default'
    priority:int=Field(default=0,ge=0,le=100)


class Buffers(Input):
    general_minutes:int=Field(default=10,ge=0,le=180)
    booking_before_minutes:int=Field(default=0,ge=0,le=240)
    booking_after_minutes:int=Field(default=0,ge=0,le=240)
    airport_before_minutes:int|None=Field(default=None,ge=0,le=480)
    airport_after_minutes:int|None=Field(default=None,ge=0,le=480)
    unknown_travel_allowance_minutes:int=Field(default=30,ge=5,le=240)


class RestPreferences(Input):
    minutes:int=Field(default=20,ge=0,le=180)
    after_visits:int=Field(default=2,ge=1,le=10)
    required:bool=False


class MealWindow(Input):
    meal:Literal['breakfast','lunch','dinner']
    start:time
    end:time
    @model_validator(mode='after')
    def clocks(self):
        if self.start.tzinfo or self.end.tzinfo or self.start>=self.end:raise ValueError('Meal windows use ordered local clock times')
        return self


class Generation(Input):
    trip_version:int=Field(ge=1)
    conditions_version:int=Field(ge=1)
    itinerary_request_version:Literal[1]=1
    recommendation_run_id:str|None=Field(default=None,max_length=100)
    start_date:date
    end_date:date
    selected:list[SelectedPlace]=Field(default_factory=list,max_length=30)
    activity_start:time=time(9)
    activity_end:time=time(21)
    allow_provisional:bool=False
    buffers:Buffers=Field(default_factory=Buffers)
    rest_preferences:RestPreferences=Field(default_factory=RestPreferences)
    meal_windows:list[MealWindow]=Field(default_factory=list,max_length=3)
    @model_validator(mode='after')
    def valid(self):
        if self.end_date<self.start_date or (self.end_date-self.start_date).days>=14:raise ValueError('Plan one to fourteen days at a time')
        if self.activity_start.tzinfo or self.activity_end.tzinfo or self.activity_end<=self.activity_start:raise ValueError('Activity times must be ordered local clock times')
        if len({p.place_id for p in self.selected})!=len(self.selected):raise ValueError('Choose each place once')
        return self


class ItemRef(Input):
    item_id:str=Field(min_length=1,max_length=150)


class AtTime(Input):
    local_start:str=Field(min_length=16,max_length=26)
    fold:Literal[0,1]|None=None
    @field_validator('local_start')
    @classmethod
    def local(cls,value):
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo or 'T' not in value:raise ValueError('Use a local date and clock time without a timezone offset')
        return parsed.isoformat(timespec='seconds')


class Add(AtTime):
    op:Literal['add']
    place_id:str=Field(min_length=1,max_length=100)
    duration_minutes:int=Field(ge=5,le=720)
    duration_origin:Literal['default','user']='user'


class Move(ItemRef,AtTime):
    op:Literal['move']
    duration_minutes:int|None=Field(default=None,ge=5,le=720)


class Remove(ItemRef):op:Literal['remove']
class Lock(ItemRef):op:Literal['lock']
class Unlock(ItemRef):op:Literal['unlock']
Command=Annotated[Add|Move|Remove|Lock|Unlock,Field(discriminator='op')]


class Preview(Input):
    expected_version:int=Field(ge=1)
    commands:list[Command]=Field(min_length=1,max_length=20)


class Apply(Input):
    expected_version:int=Field(ge=1)
    preview_id:str=Field(min_length=1,max_length=100)


class Undo(Input):
    expected_version:int=Field(ge=1)
    steps:int=Field(default=1,ge=1,le=10)


class Intent(Input):
    expected_version:int=Field(ge=1)
    text:str=Field(min_length=1,max_length=1000)
    item_id:str|None=Field(default=None,min_length=1,max_length=150)
