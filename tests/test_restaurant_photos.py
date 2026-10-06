"""Photo rights are independent from facts; fixtures never authorize real media."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from src.discovery import photos
from tests.test_foundation_api import service, _trip, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.test_catalog_registration import actual_shape
from tests.discovery_synthetic import conditions

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


def picture(number=1):
    return {'id': f'fixture-photo-{number}',
        'url': f'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Fixture{number}.jpg/640px-Fixture{number}.jpg',
        'source_url': f'https://commons.wikimedia.org/wiki/File:Fixture{number}.jpg',
        'author': 'Synthetic attribution fixture', 'license': 'CC BY 3.0',
        'license_url': 'https://creativecommons.org/licenses/by/3.0/',
        'alt': 'Test fixture only; never a production restaurant photo',
        'checked_at': (NOW-timedelta(days=1)).isoformat(),
        'expires_at': (NOW+timedelta(days=365)).isoformat(),
        'identity_evidence': 'Test-only branch identity fixture, no live media verification.',
        'enabled': True, 'taken_at': '2018-06-21', 'width': 640, 'height': 480}


def manifest():
    return {'version': 'test-photos-v1', 'places': [{'city': 'tokyo', 'external_id': 'branch-1',
        'address': 'Exact address 1', 'canonical_url': 'https://example.org/branch-1',
        'photos': [picture(), picture(2)]}]}


def identity():
    return {'city': 'tokyo', 'external_place_id': 'branch-1', 'address': 'Exact address 1',
            'source_url': 'https://example.org/branch-1'}


def select(value=None, **kwargs):
    return photos.select_photos(value or manifest(), identity(), approved=True, clock=NOW, **kwargs)


def test_two_photos_have_plain_explicit_attribution_and_historical_capture_date():
    result=select()
    assert result['photo_status']=={'state':'available','available_count':2,'reason_codes':[]}
    assert result['photos'][0]['taken_at']=='2018-06-21'
    assert result['photos'][0]['checked_at'] != result['photos'][0]['taken_at']
    assert 'identity_evidence' not in result['photos'][0] and 'enabled' not in result['photos'][0]
    assert all(isinstance(p['author'],str) and p['source_url'].startswith('https://commons.wikimedia.org/') for p in result['photos'])


@pytest.mark.parametrize('url',[
    'http://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg',
    'https://upload.wikimedia.org.evil.example/wikipedia/commons/a/ab/X.jpg',
    'https://upload.wikimedia.org@127.0.0.1/wikipedia/commons/a/ab/X.jpg',
    'https://127.0.0.1/wikipedia/commons/a/ab/X.jpg',
    'https://upload.wikimedia.org/wikipedia/commons/a/ab/X.svg',
    'https://upload.wikimedia.org/wikipedia/commons/a/ab/X.html',
    'https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg?redirect=secret',
    'https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg#fragment',
    'https://upload.wikimedia.org/wikipedia/en/a/ab/X.jpg',
    'https://upload.wikimedia.org/wikipedia/commons/a/ab/%0AX.jpg',
])
def test_untrusted_image_urls_never_reach_api(url):
    value=manifest();value['places'][0]['photos']=[{**picture(),'url':url}]
    result=select(value)
    assert result['photos']==[] and result['photo_status']['reason_codes']==['PHOTO_METADATA_INVALID']


@pytest.mark.parametrize('key,value',[
    ('source_url','https://example.org/license-claim'),('author','<a>Author</a>'),
    ('author',''),('identity_evidence',''),('license','All rights reserved'),
    ('license_url','https://example.org/cc-by'),('checked_at','2026-10-07T12:00:00Z'),
    ('checked_at','2026-10-05'),('width',0),('height',False),('taken_at','2020-02-30'),
])
def test_unreviewed_or_invalid_metadata_is_not_displayed(key,value):
    data=manifest();data['places'][0]['photos']=[{**picture(),key:value}]
    assert select(data)['photos']==[]


def test_bounded_photos_exact_branch_and_current_rights():
    data=manifest();data['places'][0]['photos'].append(picture(3))
    assert select(data)['photos']==[]
    for key,value in [('city','barcelona'),('external_id','different'),('address','another branch'),('canonical_url','https://example.org/other')]:
        data=manifest();data['places'][0][key]=value
        assert select(data)['photos']==[]
    data=manifest();data['places'].append(deepcopy(data['places'][0]))
    assert select(data)['photo_status']['reason_codes']==['PHOTO_IDENTITY_CONFLICT']
    data=manifest();data['places'][0]['photos'][0]['expires_at']=NOW.isoformat()
    result=select(data)
    assert len(result['photos'])==1 and result['photo_status']['reason_codes']==['PHOTO_POLICY_EXPIRED']
    data['places'][0]['photos'][1]['enabled']=False
    assert select(data)['photos']==[]
    assert photos.select_photos(manifest(),identity(),approved=False,clock=NOW)['photos']==[]


def test_current_thumb_host_and_public_domain_are_explicitly_supported():
    data=manifest();image=data['places'][0]['photos'][0]
    image['url']=image['url'].replace('upload.wikimedia.org','thumb.wikimedia.org')
    image['license']='CC0';image['license_url']='https://creativecommons.org/publicdomain/zero/1.0/'
    assert select(data)['photos'][0]['license']=='CC0'


def test_manifest_failure_is_local_and_replacement_is_read(monkeypatch,tmp_path):
    target=tmp_path/'photos.json';monkeypatch.setattr(photos,'MANIFEST_PATH',target)
    assert photos.load_manifest() is None
    target.write_text(json.dumps(manifest()))
    assert photos.load_manifest()['version']=='test-photos-v1'
    target.write_text('{broken')
    assert photos.load_manifest() is None
    target.write_text(json.dumps({**manifest(),'version':'replacement-v2'}))
    assert photos.load_manifest()['version']=='replacement-v2'


def prepare(discovery,monkeypatch):
    data=actual_shape();stored=import_pack(discovery,data)
    now=datetime.now(timezone.utc)
    value={'version':'test-read-time-v1','places':[]}
    for item in data['places']:
        image=picture()
        image['checked_at']=(now-timedelta(minutes=1)).isoformat()
        image['expires_at']=(now+timedelta(days=1)).isoformat()
        value['places'].append({'city':data['city'],'external_id':item['external_id'],
            'address':item['address'],'canonical_url':item['canonical_url'],'photos':[image]})
    monkeypatch.setattr(photos,'load_manifest',lambda:deepcopy(value))
    trip=_trip(discovery.client)
    return trip,stored,value


def test_catalog_and_detail_enrich_only_authorized_owned_read(discovery,monkeypatch):
    trip,stored,value=prepare(discovery,monkeypatch)
    base=f"/api/v2/trips/{trip['id']}"
    catalog=discovery.client.get(base+'/discovery-catalog').json()['items']
    assert len(catalog)==2 and all(len(p['photos'])==1 for p in catalog)
    place_id=catalog[0]['place_id']
    detail=discovery.client.get(base+f'/places/{place_id}/detail').json()
    assert detail['place']['photos']==catalog[0]['photos']
    outsider=discovery.login('photo-other-owner')
    assert outsider.client.get(base+'/discovery-catalog').status_code==404
    assert outsider.client.get(base+f'/places/{place_id}/detail').status_code==404
    with discovery.app.state.db.connect() as con:
        con.execute("UPDATE evidence_sources SET status='revoked',display_permitted=0 WHERE place_id=?",(place_id,))
    assert discovery.client.get(base+f'/places/{place_id}/detail').json()['place']['photos']==[]


@pytest.mark.parametrize('change',['disabled','identity','deleted','tombstone'])
def test_current_place_pack_and_tombstone_gates_do_not_revive_photos(discovery,monkeypatch,change):
    trip,stored,value=prepare(discovery,monkeypatch)
    place_id=stored['places'][0]['place_id']
    with discovery.app.state.db.connect() as con:
        assert len(photos.for_place(con,place_id)['photos'])==1
        if change=='disabled':con.execute("UPDATE candidate_packs SET status='disabled' WHERE id=?",(stored['id'],))
        elif change=='identity':con.execute("UPDATE place_identities SET identity_status='needs_confirmation' WHERE id=?",(place_id,))
        elif change=='deleted':con.execute("UPDATE place_identities SET deleted_at=? WHERE id=?",(NOW.isoformat(),place_id))
        else:con.execute('INSERT INTO discovery_tombstones VALUES(?,?,?,?)',('pack',stored['id'],'TEST',NOW.isoformat()))
        assert photos.for_place(con,place_id)['photos']==[]


def test_saved_runs_get_current_photos_without_persisting_media_or_restarting(discovery,monkeypatch):
    trip,stored,value=prepare(discovery,monkeypatch)
    base=f"/api/v2/trips/{trip['id']}"
    assert discovery.client.patch(base+'/discovery-conditions',json={'expected_version':0,'conditions':conditions()}).status_code==200
    receipt=discovery.client.post(base+'/recommendations',json={'trip_version':trip['version'],'conditions_version':1},headers={'Idempotency-Key':'photo-run'})
    assert receipt.status_code==202,receipt.text
    assert _job(discovery.client,receipt.json())['state']=='succeeded'
    path=base+'/recommendations/'+receipt.json()['run_id']
    before=discovery.client.get(path).json()
    items=lambda run:[item for section in run['result']['sections'].values() for group in section.values() for item in group]
    assert items(before) and all(len(item['photos'])==1 for item in items(before))
    with discovery.app.state.db.connect() as con:
        persisted=dict(con.execute('SELECT result_json,candidates_json,manifest_hash FROM recommendation_runs WHERE id=?',(receipt.json()['run_id'],)).fetchone())
        assert '"photos"' not in persisted['result_json'] and '"photos"' not in persisted['candidates_json']
    for entry in value['places']:entry['photos'][0]['enabled']=False
    after=discovery.client.get(path).json()
    assert after['data_status']=='current' and after['state']=='succeeded'
    assert all(item['photos']==[] for item in items(after))
    with discovery.app.state.db.connect() as con:
        assert dict(con.execute('SELECT result_json,candidates_json,manifest_hash FROM recommendation_runs WHERE id=?',(receipt.json()['run_id'],)).fetchone())==persisted
        assert con.execute("SELECT count(*) FROM jobs WHERE operation='recommendations'").fetchone()[0]==1
        assert con.execute('SELECT count(*) FROM usage_ledger').fetchone()[0]==0
    assert discovery.calls==[]


def test_reviewed_manifest_matches_seven_exact_real_branches_and_thirteen_images():
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((root/'src/discovery/restaurant_photos.json').read_text())
    catalog=json.loads((root/'docs/service-v3/data/official-restaurants-2026-10-06.json').read_text())
    checked=max(photos._stamp(image['checked_at']) for entry in manifest['places'] for image in entry['photos'])
    results=[]
    # Evaluate the frozen data at its actual review date, not a fake freshness bump.
    # This is identity/rights-contract validation, not an external image fetch test.
    for pack in catalog['packs']:
        for place in pack['places']:
            record={'city':pack['city'],'external_place_id':place['external_id'],
                    'address':place['address'],'source_url':place['canonical_url']}
            results.append(photos.select_photos(manifest,record,approved=True,clock=checked))
    assert len(results)==9
    assert sum(result['photo_status']['state']=='available' for result in results)==7
    assert sum(len(result['photos']) for result in results)==13
    assert all(len(result['photos'])<=2 for result in results)
    assert all(not result['photo_status']['reason_codes'] for result in results if result['photos'])


def test_expired_hours_do_not_pretend_independent_photo_rights_are_expired(discovery,monkeypatch):
    trip,stored,value=prepare(discovery,monkeypatch)
    place_id=stored['places'][0]['place_id']
    with discovery.app.state.db.connect() as con:
        con.execute("UPDATE place_facts SET expires_at='2020-01-01T00:00:00+00:00' WHERE place_id=?",(place_id,))
        assert len(photos.for_place(con,place_id)['photos'])==1
    detail=discovery.client.get(f"/api/v2/trips/{trip['id']}/places/{place_id}/detail").json()
    assert len(detail['place']['photos'])==1
    assert all(not fact['usable'] and fact['freshness']=='expired' for fact in detail['facts'])
