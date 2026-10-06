"""One durable discovery intent: conditions + immutable run + job in one SQL commit."""
from copy import deepcopy
import hashlib
import json
from pydantic import Field, field_validator, model_validator
from src.foundation.repository import DomainError, dump, new_id, utcnow
from src.foundation.models import valid_date
from src.recommendations.models import Input, LanguageFilter, RatingFilter
from src.recommendations.service import VERSIONS, digest
from .context import load, resolve


class Filters(Input):
    review_language_filter: LanguageFilter = Field(default_factory=LanguageFilter)
    rating_filter: RatingFilter = Field(default_factory=RatingFilter)
    limit: int = Field(default=6, ge=1, le=12)


class DiscoveryIntent(Input):
    expected_trip_version: int = Field(ge=1)
    expected_conditions_version: int = Field(ge=0)
    stop_id: str | None = Field(default=None, max_length=100)
    visit_date: str
    overrides: dict = Field(default_factory=dict)
    filters: Filters = Field(default_factory=Filters)

    _date = field_validator('visit_date')(valid_date)

    @model_validator(mode='after')
    def bounded(self):
        from .models import Conditions
        for field in ('visit','party','required','preferred'):
            if field in self.overrides and not isinstance(self.overrides[field],dict): raise ValueError('방문 조건의 구조를 확인해 주세요.')
        if 'city' in self.overrides and not isinstance(self.overrides['city'],str): raise ValueError('도시 ID를 확인해 주세요.')
        if len(dump(self.overrides).encode()) > 16000 or set(self.overrides) - set(Conditions.model_fields):
            raise ValueError('방문 조건의 필드와 크기를 확인해 주세요.')
        return self


def receipt(service, con, actor, trip_id, row):
    service.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
    job=con.execute('SELECT * FROM jobs WHERE id=?',(row['job_id'],)).fetchone()
    return {**service._receipt(row['run_id'],job), 'intent_id':row['id'], 'conditions_version':row['conditions_version'], 'resolved_context':json.loads(row['resolved_json']), 'intent_url':f"/api/v2/trips/{trip_id}/discovery-intents/{row['id']}"}


def get(service, actor, trip_id, ident):
    with service.db.connect() as con:
        service.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
        row=con.execute('SELECT * FROM discovery_intents WHERE id=? AND trip_id=? AND owner_id=?',(ident,trip_id,actor.id)).fetchone()
        if not row: raise DomainError('NOT_FOUND','추천 요청을 찾을 수 없습니다.',404)
        return receipt(service,con,actor,trip_id,row)


def submit(service, actor, trip_id, body, key):
    body=DiscoveryIntent.model_validate(body).model_dump(mode='json')
    if not isinstance(key,str) or not 8 <= len(key) <= 200:
        raise DomainError('IDEMPOTENCY_REQUIRED','8~200자의 Idempotency-Key가 필요합니다.',400)
    key_hash=hashlib.sha256(key.encode()).hexdigest(); fingerprint=digest(body)
    with service.db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        current=service.jobs._scope(con,actor.id,actor.session_id,'personal_trip',trip_id)
        old=con.execute('SELECT * FROM discovery_intents WHERE owner_id=? AND trip_id=? AND key_hash=?',(actor.id,trip_id,key_hash)).fetchone()
        if old:
            if old['request_hash'] != fingerprint: raise DomainError('IDEMPOTENCY_CONFLICT','같은 실행 키에 다른 입력이 사용되었습니다.',409)
            return receipt(service,con,actor,trip_id,old)
        saved=con.execute('SELECT * FROM discovery_conditions WHERE trip_id=?',(trip_id,)).fetchone()
        version=saved['version'] if saved else 0
        if current['version'] != body['expected_trip_version'] or version != body['expected_conditions_version']:
            raise DomainError('VERSION_CONFLICT','여행 또는 방문 조건이 바뀌었습니다. 입력은 유지한 채 최신 내용을 확인해 주세요.',409)
        trip=service.repo._trip_dto(con,current)
        if body['stop_id'] and not any(s['id']==body['stop_id'] for s in trip['stops']):
            raise DomainError('NOT_FOUND','도시 구간을 찾을 수 없습니다.',404)
        previous,previous_stop,basis=load(con,trip_id,saved)
        overrides=deepcopy(body['overrides'])  # complete sparse set; omitted keys return to inheritance, null clears
        if overrides.get('origin') != previous.get('origin'):
            basis={k:v for k,v in basis.items() if k not in ('origin_invalidated','previous_origin')}
        if isinstance(overrides.get('origin'),dict) and isinstance(previous.get('origin'),dict) and overrides['origin'].get('label') != previous['origin'].get('label') and all(overrides['origin'].get(k)==previous['origin'].get(k) for k in ('latitude','longitude')):
            basis={**basis,'origin_invalidated':True}
        resolved=resolve(trip,overrides,body['stop_id'],basis)
        if isinstance(resolved['conditions'].get('visit'),dict) and body['visit_date'] != resolved['conditions']['visit'].get('date'):
            overrides.setdefault('visit',{})['date']=body['visit_date']
            resolved=resolve(trip,overrides,body['stop_id'],basis)
        if resolved['validation']:
            raise DomainError('VALIDATION_FAILED',resolved['validation'][0]['message'],422,resolved['validation'])
        # SQL ownership checks precede all persistence and all work is within this transaction.
        cv=version+1; stamp=utcnow(); ident=new_id('intent'); run=new_id('rec')
        conditions=resolved['conditions']
        con.execute('INSERT INTO discovery_conditions VALUES(?,?,?,?,?,?,?) ON CONFLICT(trip_id) DO UPDATE SET version=excluded.version,trip_version=excluded.trip_version,snapshot_json=excluded.snapshot_json,conditions_json=excluded.conditions_json,updated_at=excluded.updated_at',
            (trip_id,actor.id,cv,trip['version'],dump(trip),dump(conditions),stamp))
        con.execute('INSERT INTO discovery_contexts VALUES(?,?,?,?,?,?) ON CONFLICT(trip_id) DO UPDATE SET stop_id=excluded.stop_id,overrides_json=excluded.overrides_json,basis_json=excluded.basis_json,updated_at=excluded.updated_at',
            (trip_id,actor.id,resolved['trip_context']['stop_id'],dump(overrides),dump(resolved['basis']),stamp))
        from src.product.events import consented
        snapshot={**body['filters'],'trip_version':trip['version'],'conditions_version':cv,'conditions':conditions,
            'trip':{k:trip[k] for k in ('id','version','start_date','end_date','stops')},'resolved_context':resolved,'intent_id':ident,
            'pipeline_version':'discovery_pipeline_v1','ranker_versions':VERSIONS,'explanation_version':'server_templates_v1','evaluation_at':stamp,
            'analytics_opt_in':consented(con,actor.id),'feedback_policy_version':'soft_avoid_half_v1',
            'soft_avoid_place_ids':[r['place_id'] for r in con.execute('SELECT place_id,payload_json FROM visit_feedback WHERE owner_id=? AND trip_id=? AND withdrawn_at IS NULL',(actor.id,trip_id)) if json.loads(r['payload_json']).get('reflect_preference')]}
        job=service.jobs.enqueue(actor.id,actor.session_id,'personal_trip',trip_id,'recommendations',{'run_id':run},trip['version'],'discovery-intent:'+key_hash,request_fingerprint=fingerprint,con=con)
        con.execute('INSERT INTO recommendation_runs(id,trip_id,owner_id,job_id,trip_version,conditions_version,snapshot_json,ranker_versions_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (run,trip_id,actor.id,job['id'],trip['version'],cv,dump(snapshot),dump(VERSIONS),stamp))
        con.execute('INSERT INTO discovery_intents VALUES(?,?,?,?,?,?,?,?,?,?)',(ident,trip_id,actor.id,key_hash,fingerprint,job['id'],run,cv,dump(resolved),stamp))
        return receipt(service,con,actor,trip_id,con.execute('SELECT * FROM discovery_intents WHERE id=?',(ident,)).fetchone())
