"""World-city metadata is not evidence of live restaurant coverage."""
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from src.destinations import CATALOG,CITIES,city_key,normalized
from src.discovery.models import Conditions,PackInput
from tests.test_foundation_api import service


def test_registry_has_100_unambiguous_identities_and_iana_timezones():
    assert len(CATALOG['cities'])==len(CITIES)==100
    aliases={}
    for city in CITIES.values():
        assert ZoneInfo(city['timezone'])
        for name in [city['id'],city['name_ko'],city['name_en'],*city['aliases']]:
            key=normalized(name)
            assert key not in aliases or aliases[key]==city['id']
            aliases[key]=city['id']
            assert city_key(name)==city['id']
    assert city_key(' Madrid ')=='madrid'
    assert city_key('서울')=='seoul'
    assert city_key('Unregistered City') is None


def test_all_cities_accept_own_timezone_and_currency_without_converting_money():
    for city in CITIES.values():
        data={'city':city['id'],'visit':{'date':'2026-11-06','timezone':city['timezone']},'party':{'adults':2},
              'budget':{'currency':city['currency'],'amount_min':'0','amount_max':'100'}}
        parsed=Conditions.model_validate(data)
        assert parsed.city==city['id'] and parsed.budget.currency==city['currency']
        data['visit']['timezone']='Asia/Tokyo' if city['timezone']!='Asia/Tokyo' else 'Europe/Madrid'
        with pytest.raises(ValidationError):Conditions.model_validate(data)


def test_every_city_roundtrips_through_owned_trip_and_conditions(service):
    client=service.login('world-city-user').client
    catalog=client.get('/api/v2/cities')
    assert catalog.status_code==200 and len(catalog.json()['cities'])==100
    for city in CITIES.values():
        res=client.post('/api/v2/trips',json={'title':'Synthetic '+city['name_en'],'start_date':'2026-11-06','end_date':'2026-11-08',
            'stops':[{'city':city['name_en'],'sequence':1,'start_date':'2026-11-06','end_date':'2026-11-08','timezone':city['timezone']}]})
        assert res.status_code==201,res.text
        base='/api/v2/trips/'+res.json()['id']+'/discovery-conditions'
        envelope=client.get(base).json()
        assert envelope['context_state']=='ready' and envelope['conditions']['city']==city['id']
        assert envelope['city_metadata']['currency']==city['currency']
        assert envelope['catalog_availability']['real_reviewed_candidates']==0
        saved=client.patch(base,json={'expected_version':0,'conditions':envelope['conditions']})
        assert saved.status_code==200,saved.text


def test_mail_examples_are_valid_synthetic_messages_and_zip_matches():
    import zipfile
    from src.loader import read_email_bytes
    root=Path(__file__).resolve().parents[1]
    base=root/'examples/mail-test-pack'
    cases=json.loads((base/'expected-results.json').read_text())['cases']
    assert len(cases)==12
    with zipfile.ZipFile(root/'web/examples/mail-test-pack.zip') as archive:
        for case in cases:
            raw=(base/case['file']).read_bytes()
            assert archive.read('mail-test-pack/'+case['file'])==raw
            text=read_email_bytes(raw,case['file'])
            assert 'SYNTHETIC TEST DATA' in text and 'TEST ONLY' in text
            assert 'example.invalid' in text
    # Body remains text, not executable HTML.
    html=read_email_bytes((base/'09-html-only.eml').read_bytes(),'09-html-only.eml')
    assert '<html>' not in html and '2026-11-08' in html
