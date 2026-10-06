from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class LanguageFilter(Input):
    required: bool = False
    apply_only_if_qualified: Literal[True] = True
    apply_to: list[Literal['local_discovery','landmark']] = Field(default=['local_discovery'], min_length=1,max_length=2)
    mode: Literal['observed_window'] = 'observed_window'


class RatingFilter(Input):
    enabled: bool = True
    min_rating: float = Field(default=4.2,ge=0,le=5)
    min_count: int = Field(default=200,ge=0,le=100000000)
    apply_to: list[Literal['local_discovery','landmark']] = Field(default=['local_discovery'],min_length=1,max_length=2)


class RecommendationInput(Input):
    trip_version: int = Field(ge=1)
    conditions_version: int = Field(ge=0)
    review_language_filter: LanguageFilter = Field(default_factory=LanguageFilter)
    rating_filter: RatingFilter = Field(default_factory=RatingFilter)
    limit: int = Field(default=6,ge=1,le=12)


class ComparisonInput(Input):
    run_id: str = Field(min_length=1,max_length=100)
    place_ids: list[str] = Field(min_length=2,max_length=3)
    @model_validator(mode='after')
    def unique(self):
        if len(set(self.place_ids)) != len(self.place_ids):
            raise ValueError('서로 다른 장소 2~3개를 선택해 주세요.')
        return self


class EventInput(Input):
    event: Literal['recommendation_view','place_save','place_exclude','source_open']
    place_id: str | None = Field(default=None,max_length=100)
    run_id: str | None = Field(default=None,max_length=100)
