"""Versioned destination identities, independent of live recommendation coverage."""
import json
from pathlib import Path
import unicodedata
from typing import Annotated
from pydantic import AfterValidator
from zoneinfo import ZoneInfo

CATALOG=json.loads(Path(__file__).with_name('cities.json').read_text())
CITIES={city['id']:city for city in CATALOG['cities']}
CURRENCIES=frozenset(city['currency'] for city in CITIES.values())

def normalized(value):return unicodedata.normalize('NFKC',value).strip().casefold()
ALIASES={normalized(label):city['id'] for city in CITIES.values() for label in [city['id'],city['name_ko'],city['name_en'],*city['aliases']]}
def city_key(value):return ALIASES.get(normalized(value)) if isinstance(value,str) else None
def validate_city(value):
    key=city_key(value)
    if not key:raise ValueError('등록한 도시를 선택해 주세요.')
    return key
CityId=Annotated[str,AfterValidator(validate_city)]
def validate_timezone(value):
    try:ZoneInfo(value)
    except (KeyError,ValueError,TypeError):raise ValueError('유효한 IANA 시간대가 필요합니다.') from None
    return value
IanaTimezone=Annotated[str,AfterValidator(validate_timezone)]
def validate_currency(value):
    if value not in CURRENCIES:raise ValueError('지원하는 통화 코드를 선택해 주세요.')
    return value
Currency=Annotated[str,AfterValidator(validate_currency)]
