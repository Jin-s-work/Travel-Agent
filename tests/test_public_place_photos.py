"""Free photo metadata: exact feature identity, rights, fencing and quotas."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from types import SimpleNamespace

import pytest

from src.discovery import photo_provider as provider, photos
from src.discovery.safe_fetch import Page, FetchRejected
from src.foundation.repository import DomainError
from tests.test_foundation_api import service, _job
from tests.test_public_discovery import public, prepare, run

NOW=datetime.now(timezone.utc)
TITLE='File:Fixture.jpg'

def commons():
    return {'query':{'pages':{'1':{'title':TITLE,'imageinfo':[{'url':'https://upload.wikimedia.org/wikipedia/commons/a/ab/Fixture.jpg','thumburl':'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Fixture.jpg/960px-Fixture.jpg','descriptionurl':'https://commons.wikimedia.org/wiki/File:Fixture.jpg','width':1200,'height':800,'thumbwidth':960,'thumbheight':640,'extmetadata':{'LicenseShortName':{'value':'CC BY-SA 4.0'},'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/4.0/'},'Artist':{'value':'<a href="https://example.com">Fixture Author</a>'}}}]}}}}

def setup(public,fetcher=None):
    trip,base=prepare(public);out=run(public,trip,base)
    calls=[]
    def fake(url,**limits):
        calls.append(url)
        assert limits=={'max_bytes':provider.MAX_BYTES,'timeout_seconds':4,'max_redirects':0}
        if fetcher:return fetcher(url)
        if 'api.openstreetmap.org' in url:
            ident=int(url.split('/')[-1].split('.')[0])
            data={'elements':[{'type':'node','id':ident,'tags':{'amenity':'restaurant','wikimedia_commons':TITLE}}]}
        else:data=commons()
        return Page(url,'application/json',json.dumps(data).encode(),0)
    public.app.state.place_photos.enabled=True
    public.app.state.place_photos.fetcher=fake
    return trip,base,out,calls

def submit(public,base,ids=None,key=None):
    return public.client.post(base+'/place-photos',json={'place_ids':ids or ['osm_node_700000000']},headers={'Idempotency-Key':key} if key else {})

def test_background_photo_cache_is_read_without_network_and_revocation_wins(public):
    trip,base,out,calls=setup(public)
    receipt=submit(public,base);assert receipt.status_code==202,receipt.text
    assert _job(public.client,receipt.json())['state']=='succeeded'
    assert len(calls)==2
    with public.app.state.db.connect() as con:
        photo=photos.for_place(con,'osm_node_700000000')['photos'][0]
        assert photo['author']=='Fixture Author' and photo['kind']=='other' and photo['taken_at'] is None
        assert len(con.execute("SELECT * FROM usage_reservations WHERE provider=?",(provider.PROVIDER,)).fetchall())==2
        assert con.execute("SELECT SUM(actual_cost_micros) FROM usage_reservations WHERE provider=?",(provider.PROVIDER,)).fetchone()[0]==0
    again=submit(public,base);assert again.json()=={'state':'succeeded','cached':True}
    response=public.client.get(base+'/recommendations/'+out['run_id']).json()
    entries=[item for section in response['result']['sections'].values() for group in section.values() for item in group]
    assert next(p for p in entries if p['place_id']=='osm_node_700000000')['photos']
    assert len(calls)==2
    with public.app.state.db.connect() as con:
        con.execute("UPDATE evidence_sources SET display_permitted=0 WHERE place_id='osm_node_700000000'")
        assert photos.for_place(con,'osm_node_700000000')['photos']==[]

def test_explicit_key_idempotency_and_cross_user_scope(public):
    trip,base,_,calls=setup(public)
    first=submit(public,base,key='same-photo-request');assert first.status_code==202
    _job(public.client,first.json())
    assert submit(public,base,key='same-photo-request').json()['job_id']==first.json()['job_id']
    assert submit(public,base,['osm_node_700000001'],key='same-photo-request').status_code==409
    other=public.login('other-photos')
    assert other.client.post(base+'/place-photos',json={'place_ids':['osm_node_700000000']}).status_code==404
    public.app.state.place_photos.enabled=False
    assert other.client.post(base+'/place-photos',json={'place_ids':['osm_node_700000000']}).status_code==404
    assert len(calls)==2

@pytest.mark.parametrize('failure',['timeout','too_big','wrong_mime','api_error','429'])
def test_provider_failure_is_bounded_and_does_not_erase_recommendations(public,failure):
    def fail(url):
        if failure=='timeout':raise FetchRejected('FETCH_TIMEOUT')
        if failure=='429':raise FetchRejected('HTTP_ERROR',http_status=429)
        return Page(url,'text/html' if failure=='wrong_mime' else 'application/json',b'x'*(provider.MAX_BYTES+1) if failure=='too_big' else b'{"error":{"code":"bad"}}',0)
    trip,base,out,calls=setup(public,fail)
    receipt=submit(public,base);job=_job(public.client,receipt.json());assert job['state']=='partial'
    assert job['result']['places_unresolved']==1 and job['result']['places_with_photos']==0
    assert len(calls)==1
    assert submit(public,base).json()['cached'] is True
    fetched=public.client.get(base+'/recommendations/'+out['run_id']);assert fetched.status_code==200 and fetched.json()['result']
    with public.app.state.db.connect() as con:
        assert photos.for_place(con,'osm_node_700000000')['photos']==[]
        row=con.execute('SELECT * FROM place_photo_cache').fetchone()
        assert datetime.fromisoformat(row['expires_at'])-datetime.fromisoformat(row['checked_at'])==timedelta(minutes=15)

def test_quota_and_emergency_pause_block_before_call(public,monkeypatch):
    trip,base,_,calls=setup(public)
    monkeypatch.setattr(provider,'GLOBAL_DAILY',1)
    first=submit(public,base);_job(public.client,first.json())
    assert len(calls)==1 # OSM used last free quota, Commons not called.
    with public.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM place_photo_cache').fetchone()[0]==1
    from src.operations.controls import update
    update(public.app.state.db,external_enabled=False)
    second=submit(public,base,['osm_node_700000001']);_job(public.client,second.json())
    assert len(calls)==1

def test_deleted_trip_cannot_activate_a_late_photo(public):
    trip,base,_,calls=setup(public)
    state=public.app.state
    with state.db.connect() as con:
        user=con.execute('SELECT * FROM users WHERE id=?',(public.user['id'],)).fetchone()
        session=con.execute('SELECT id FROM sessions WHERE user_id=?',(public.user['id'],)).fetchone()
    actor=SimpleNamespace(id=user['id'],session_id=session['id'])
    job={'actor_id':actor.id,'session_id':actor.session_id,'trip_id':trip['id'],'payload':{'place_ids':['osm_node_700000000']}}
    class Context:
        def guard(self,**kw):pass
        def progress(self,*args,**kwargs):pass
        def checkpoint(self,*args,**kwargs):pass
    def read(url,*args):
        if 'openstreetmap' in url:return {'elements':[{'type':'node','id':700000000,'tags':{'amenity':'restaurant','image':TITLE}}]}
        with state.db.connect() as con:con.execute('UPDATE trips SET deleted_at=? WHERE id=?',(NOW.isoformat(),trip['id']))
        return commons()
    from unittest.mock import patch
    with patch.object(state.place_photos,'_read',read),pytest.raises(DomainError):state.place_photos.execute(job,Context())
    with state.db.connect() as con:assert con.execute('SELECT COUNT(*) FROM place_photo_cache').fetchone()[0]==0

@pytest.mark.parametrize('field,value',[('LicenseShortName','All rights reserved'),('LicenseUrl','https://example.com/license')])
def test_photo_requires_explicit_license_metadata(field,value):
    data=commons();data['query']['pages']['1']['imageinfo'][0]['extmetadata'][field]['value']=value
    assert provider.metadata(data,TITLE,'Fixture',NOW) is None

def test_feature_identity_brand_media_and_unknown_url_are_not_photos():
    p={'external_place_id':'node/1','latitude':48.85,'longitude':2.35}
    calls=[]
    def read(url):
        calls.append(url)
        return {'elements':[{'type':'node','id':1,'tags':{'amenity':'restaurant','wikidata':'Q1','brand:wikidata':'Q1','image':'http://127.0.0.1/private','wikimedia_commons':'Category:Paris'}}]}
    assert provider.linked_titles(p,read)==[] and len(calls)==1
    assert provider.linked_titles({**p,'external_place_id':'node/2'},read)==[]

def test_wikidata_photo_requires_nearby_branch_coordinates():
    p={'external_place_id':'node/1','latitude':48.85,'longitude':2.35}
    def read(url):
        if 'openstreetmap' in url:return {'elements':[{'type':'node','id':1,'tags':{'amenity':'cafe','wikidata':'Q1'}}]}
        return {'entities':{'Q1':{'claims':{'P18':[{'mainsnak':{'datavalue':{'value':'Fixture.jpg'}}}], 'P625':[{'mainsnak':{'datavalue':{'value':{'latitude':48.85,'longitude':2.35,'globe':'http://www.wikidata.org/entity/Q2'}}}}]}}}}
    assert provider.linked_titles(p,read)==[TITLE]
    assert provider.linked_titles({**p,'latitude':0},read)==[]

def test_current_commons_tracking_urls_normalize_without_accepting_signed_or_proxy_urls():
    data=commons();info=data['query']['pages']['1']['imageinfo'][0]
    info['thumburl']+='?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=thumbnail'
    photo=provider.metadata(data,TITLE,'Fixture',NOW)
    assert photo and '?' not in photo['url']
    info['thumburl']+='&token=unsafe'
    assert provider.metadata(data,TITLE,'Fixture',NOW) is None

def test_schema14_photo_upgrade_preserves_identity_and_is_repeatable(tmp_path):
    from src.foundation.db import Database
    path=tmp_path/'schema14.sqlite3';db=Database(path)
    with db.connect() as con:
        con.execute('DROP TABLE place_photo_cache')
        con.execute('UPDATE schema_version SET version=14' if db.backend=='postgres' else 'PRAGMA user_version=14')
        before=[dict(r) for r in con.execute('SELECT * FROM place_identities')]
    db.close();db=Database(path)
    with db.connect() as con:
        assert [dict(r) for r in con.execute('SELECT * FROM place_identities')]==before
        assert con.execute('SELECT count(*) FROM place_photo_cache').fetchone()[0]==0
    assert db.schema_version()==15;db.close();db=Database(path);assert db.schema_version()==15;db.close()

def test_legacy_saved_run_can_resolve_photos_without_post_schema13_trip_links(public):
    trip,base,out,calls=setup(public)
    with public.app.state.db.connect() as con:con.execute('DELETE FROM trip_places WHERE trip_id=?',(trip['id'],))
    response=submit(public,base);assert response.status_code==202,response.text
    assert _job(public.client,response.json())['state']=='succeeded'
    assert len(calls)==2
    other_trip,other_base=prepare(public)
    assert submit(public,other_base).status_code==404
