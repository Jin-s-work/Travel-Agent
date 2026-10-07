"""Stage 1 discovery contracts. Synthetic temporary SQL; no live provider calls."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from tests.test_foundation_api import service, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.test_public_discovery import public, prepare as public_prepare, run as public_run, payload
from tests.test_recommendation_api import prepare, submit, completed, eligible_ids
from tests.discovery_synthetic import pack, conditions
from src.discovery.public_places import normalize, center, POLICY
from tests.test_review_integration import reviews


def clone_places(fixture, count, *, cafe_last=False):
    data=pack();data['places']=data['places'][:1]
    saved=import_pack(fixture,data)
    seed=saved['places'][0]['place_id']
    with fixture.app.state.db.connect() as con:
        place=dict(con.execute('SELECT * FROM place_identities WHERE id=?',(seed,)).fetchone())
        candidate=dict(con.execute('SELECT * FROM research_candidates WHERE place_id=?',(seed,)).fetchone())
        sources=[dict(r) for r in con.execute('SELECT * FROM evidence_sources WHERE place_id=?',(seed,))]
        facts=[dict(r) for r in con.execute('SELECT * FROM place_facts WHERE place_id=?',(seed,))]
        def insert(table,row):
            con.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
        for n in range(1,count):
            ident='stage1_fixture_'+str(n).zfill(4)
            insert('place_identities',{**place,'id':ident,'external_place_id':'synthetic-stage1-'+str(n),'name':'Synthetic stage1 '+str(n)})
            insert('research_candidates',{**candidate,'id':'stage1_candidate_'+str(n),'place_id':ident,'category':'cafe' if cafe_last and n==count-1 else 'restaurant'})
            mapped={source['id']:'stage1_source_'+str(n)+'_'+str(i) for i,source in enumerate(sources)}
            for source in sources:insert('evidence_sources',{**source,'id':mapped[source['id']],'place_id':ident})
            for i,fact in enumerate(facts):insert('place_facts',{**fact,'id':'stage1_fact_'+str(n)+'_'+str(i),'place_id':ident,'source_id':mapped[fact['source_id']]})
    return saved


def test_thirteenth_public_candidate_is_evaluated_from_requested_origin(public):
    trip,base=public_prepare(public)
    public.app.state.discovery.public_provider.fetcher=lambda city:(normalize(payload(city,13),city),1000)
    c=public.client.get(base+'/discovery-conditions').json()['conditions']
    last=payload()['elements'][0];origin=center('paris')
    c.update(origin={'label':'Synthetic test origin','latitude':origin['latitude']+.024,'longitude':origin['longitude']},radius_m=100)
    assert public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':c}).status_code==200
    result=public_run(public,trip,base,conditions_version=2)['result']
    assert [p['place_id'] for p in result['sections']['reference']['needs_confirmation']]==['osm_node_700000012']
    assert result['candidate_selection']['evaluated']==13
    assert result['external_discovery']['scope']=='city_center'
    assert result['external_discovery']['origin_scope_supported'] is False


def test_cafe_after_one_hundred_restaurants_is_not_capped_away(discovery):
    clone_places(discovery,101,cafe_last=True)
    from tests.test_foundation_api import _trip
    trip=_trip(discovery.client);base=f"/api/v2/trips/{trip['id']}"
    value=conditions();value['categories']=['cafe']
    assert discovery.client.patch(base+'/discovery-conditions',json={'expected_version':0,'conditions':value}).status_code==200
    result=completed(discovery,trip,submit(discovery,trip))
    assert 'stage1_fixture_0100' in eligible_ids(result)
    assert result['result']['candidate_selection']['evaluated']==1


def test_curated_restaurants_do_not_suppress_cafe_search(public):
    with public.app.state.db.connect() as con:con.execute("UPDATE users SET role='admin' WHERE id=?",(public.user['id'],))
    data=pack();data['places']=[p for p in data['places'] if p['category']=='restaurant']
    saved=import_pack(public,data)
    with public.app.state.db.connect() as con:con.execute('UPDATE candidate_packs SET synthetic=0 WHERE id=?',(saved['id'],))
    trip,base=public_prepare(public,'tokyo')
    value=public.client.get(base+'/discovery-conditions').json()['conditions'];value['categories']=['cafe']
    assert public.client.patch(base+'/discovery-conditions',json={'expected_version':1,'conditions':value}).status_code==200
    def cafes(city):
        public.calls_public.append(city);data=payload(city)
        for p in data['elements']:p['tags']['amenity']='cafe'
        return normalize(data,city),1000
    public.app.state.discovery.public_provider.fetcher=cafes
    result=public_run(public,trip,base,conditions_version=2)
    assert public.calls_public==['tokyo']
    assert result['result']['sections']['reference']['needs_confirmation']


def test_unrelated_policy_and_place_do_not_change_saved_dependencies(discovery):
    trip,_=prepare(discovery);run=completed(discovery,trip,submit(discovery,trip))
    with discovery.app.state.db.connect() as con:
        before=con.execute('SELECT result_json,candidates_json FROM recommendation_runs WHERE id=?',(run['run_id'],)).fetchone()
        con.execute('INSERT INTO provider_policies(id,provider,version,status,rights_json,evidence_json,purpose,reviewed_at,expires_at,aggregate_ttl_seconds,id_ttl_seconds,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',('unrelated-policy','fake','unrelated-v1','active','{}','[]','Synthetic unrelated',datetime.now(timezone.utc).isoformat(),(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),60,60,discovery.admin.user['id'],datetime.now(timezone.utc).isoformat()))
        con.execute("UPDATE place_identities SET version=version+1 WHERE city='barcelona'")
    after=discovery.client.get(f"/api/v2/trips/{trip['id']}/recommendations/{run['run_id']}").json()
    assert after['data_status']=='current' and eligible_ids(after)==eligible_ids(run)
    with discovery.app.state.db.connect() as con:
        current=con.execute('SELECT result_json,candidates_json FROM recommendation_runs WHERE id=?',(run['run_id'],)).fetchone()
        assert dict(current)==dict(before)


class Meter:
    """Only aggregate counts and timing are retained; never SQL text or parameters."""
    def __init__(self,db,monkeypatch):
        self.db=db;self.thread=None;self.data={};self.original=db.connect
        monkeypatch.setattr(db,'connect',self.connect)
    @contextmanager
    def connect(self):
        active=self.thread==threading.get_ident();started=time.perf_counter()
        with self.original() as con:
            if active:
                self.data['checkouts']+=1;self.data['checkout_elapsed_ms']+=(time.perf_counter()-started)*1000
                yield Counted(con,self.data)
            else:yield con
    def measure(self,operation):
        self.data={'selects':0,'writes':0,'transactions':0,'execute_calls':0,'checkouts':0,'checkout_elapsed_ms':0.0,'external_provider_calls':0,'pool_wait_ms':None,'queue_wait_ms':None}
        self.thread=threading.get_ident();started=time.perf_counter()
        pool=getattr(self.db,'pool',None);pool_before=pool.get_stats() if pool else {}
        try:result=operation()
        finally:self.thread=None;self.data['elapsed_ms']=round((time.perf_counter()-started)*1000,3)
        self.data['checkout_elapsed_ms']=round(self.data['checkout_elapsed_ms'],3)
        if pool:self.data['pool_wait_ms']=max(0,pool.get_stats().get('requests_wait_ms',0)-pool_before.get('requests_wait_ms',0))
        return result,dict(self.data)


class Counted:
    def __init__(self,con,data):self.con=con;self.data=data
    def __getattr__(self,key):return getattr(self.con,key)
    def count(self,statement):
        verb=statement.lstrip().split(None,1)[0].upper()
        self.data['execute_calls']+=1
        if verb in {'SELECT','WITH'}:self.data['selects']+=1
        elif verb in {'BEGIN','COMMIT','ROLLBACK'}:self.data['transactions']+=1
        else:self.data['writes']+=1
    def execute(self,statement,*args,**kwargs):self.count(statement);return self.con.execute(statement,*args,**kwargs)
    def executemany(self,statement,*args,**kwargs):self.count(statement);return self.con.executemany(statement,*args,**kwargs)


def legacy_services(app):
    def old(path,name):
        namespace={'__name__':'stage1_baseline','__package__':path.replace('/','.')[:-3].rsplit('.',1)[0]}
        exec(compile(subprocess.check_output(['git','show',os.environ.get('GOING_STAGE1_BASELINE_REF','e8ea36faadac4912a523ae7bea5f02368d7f1ba1')+':'+path],text=True),path,'exec'),namespace)
        return namespace[name]
    old_reviews=old('src/research/service.py','ReviewService')(app.state.db,app.state.repo,app.state.jobs,app.state.gateway,provider=app.state.reviews.provider)
    old_discovery=old('src/discovery/service.py','DiscoveryService')(app.state.db,app.state.repo,app.state.jobs,old_reviews,allow_synthetic=True)
    return old('src/recommendations/service.py','Recommendations')(app.state.db,app.state.repo,app.state.jobs,old_discovery,old_reviews,matrix=app.state.location_matrix)


@pytest.mark.parametrize('count',[6,12,50,100])
def test_batched_capture_and_get_have_constant_sql_count(discovery,monkeypatch,count):
    clone_places(discovery,count)
    from tests.test_foundation_api import _trip
    trip=_trip(discovery.client);base=f"/api/v2/trips/{trip['id']}"
    assert discovery.client.patch(base+'/discovery-conditions',json={'expected_version':0,'conditions':conditions()}).status_code==200
    app=discovery.app;new_service=app.state.recommendations
    meter=Meter(app.state.db,monkeypatch);records=[]
    versions=[('after',new_service)]
    if os.environ.get('GOING_STAGE1_METRICS_BASELINE')=='1':versions.insert(0,('before',legacy_services(app)))
    for label,target in versions:
        original=target.execute;execution={}
        def measured(job,ctx):
            queued=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(job['created_at'])).total_seconds()*1000)
            value,stats=meter.measure(lambda:original(job,ctx));execution.update(stats);execution['queue_wait_ms']=round(queued,3)
            return value
        monkeypatch.setattr(target,'execute',measured);app.state.recommendations=target
        receipt=submit(discovery,trip,key='stage1-metrics-'+label)
        assert receipt.status_code==202,receipt.text
        assert _job(discovery.client,receipt.json())['state']=='succeeded'
        with app.state.db.connect() as con:
            job=dict(con.execute('SELECT * FROM jobs WHERE id=?',(receipt.json()['job_id'],)).fetchone())
        actor=SimpleNamespace(id=job['actor_id'],session_id=job['session_id'])
        result,stats=meter.measure(lambda:target.get(actor,trip['id'],receipt.json()['run_id']))
        _,cached=meter.measure(lambda:original({**job,'payload':{'run_id':receipt.json()['run_id']}},SimpleNamespace(guard=lambda:None)))
        records.append({'version':label,'candidate_count':count,'backend':app.state.db.backend,'fresh_execute':dict(execution),'cached_execute':cached,'get':stats})
        if label=='after':
            assert stats['writes']==0 and stats['transactions']==0
            assert stats['selects']<=45 and stats['checkouts']<=8
            assert result['data_status']=='current'
            assert execution['selects']<=180 and execution['checkouts']<=50
    app.state.recommendations=new_service
    directory=os.environ.get('GOING_STAGE1_METRICS_DIR')
    if directory:
        target=Path(directory);target.mkdir(parents=True,exist_ok=True)
        (target/f'discovery-{app.state.db.backend}-{count}.json').write_text(json.dumps({'baseline_ref':os.environ.get('GOING_STAGE1_BASELINE_REF','e8ea36faadac4912a523ae7bea5f02368d7f1ba1'),'scope':'synthetic application SQL calls on measured worker/request thread; no auth/network transport; executemany counts one batch; pool wait is pool-wide delta (null for SQLite); queue wait is job created-to-handler-start','records':records},ensure_ascii=False,indent=2))


@pytest.mark.parametrize('withdrawal',['policy_marker','run_marker','place_marker','run_expiry','aggregate_expiry','identity_version','production_off','display_rights'])
def test_review_batch_stays_read_only_and_rejects_current_withdrawal(reviews,monkeypatch,withdrawal):
    from tests.test_review_integration import completed as collect
    from tests.test_foundation_api import _trip
    receipt,_=collect(reviews)
    trip=_trip(reviews.client);pid=reviews.place['id'];db=reviews.app.state.db
    assert reviews.client.post(f"/api/v2/trips/{trip['id']}/places",json={'place_id':pid}).status_code==201
    with db.connect() as con:
        con.execute('UPDATE review_controls SET production_enabled=1')
        job=con.execute('SELECT actor_id,session_id FROM jobs WHERE id=?',(receipt['job_id'],)).fetchone()
    actor=SimpleNamespace(id=job['actor_id'],session_id=job['session_id']);svc=reviews.app.state.reviews
    monkeypatch.setattr(svc,'purge',lambda **kw:pytest.fail('A read must not invoke global purge'))
    meter=Meter(db,monkeypatch)
    fresh,stats=meter.measure(lambda:svc.evidence(actor,trip['id'],pid))
    assert fresh['counts']['text_count']==200 and stats['writes']==0 and stats['transactions']==0
    with db.connect() as con:
        if withdrawal.endswith('_marker'):
            kind=withdrawal.removesuffix('_marker');target={'policy':reviews.policy['id'],'run':receipt['run_id'],'place':pid}[kind]
            con.execute('INSERT INTO research_tombstones VALUES(?,?,?,?,NULL)',(kind,target,'SYNTHETIC_WITHDRAWAL',datetime.now(timezone.utc).isoformat()))
        elif withdrawal=='run_expiry':con.execute("UPDATE review_collection_runs SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(receipt['run_id'],))
        elif withdrawal=='aggregate_expiry':con.execute("UPDATE review_aggregates SET expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",(receipt['run_id'],))
        elif withdrawal=='identity_version':con.execute('UPDATE place_identities SET version=version+1 WHERE id=?',(pid,))
        elif withdrawal=='production_off':con.execute('UPDATE review_controls SET production_enabled=0')
        else:con.execute("UPDATE provider_policies SET rights_json='{}' WHERE id=?",(reviews.policy['id'],))
    blocked,stats=meter.measure(lambda:svc.evidence(actor,trip['id'],pid))
    assert blocked['counts'] is None and blocked['metrics'] is None
    assert stats['writes']==0 and stats['transactions']==0 and stats['checkouts']==1
    # Withdrawal is enforced from current predicates even before maintenance scrubs SQL.
    with db.connect() as con:
        row=con.execute('SELECT summary_json,deleted_at FROM review_collection_runs WHERE id=?',(receipt['run_id'],)).fetchone()
    assert row['summary_json'] is not None and row['deleted_at'] is None
