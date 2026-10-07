"""Local-to-AI upgrade must index all active documents without re-extraction."""
from uuid import uuid4

from src.foundation.local_mail import parse_local_document
from tests.test_foundation_api import service, _trip, _upload, _job, _reprocess
from tests.test_local_mail import PACK


def prepare(service):
    user = service.login('upgrade')
    trip = _trip(user.client)
    base = f"/api/v2/trips/{trip['id']}"
    service.settings.mail_analysis_mode = 'local'
    receipts = [_upload(user.client,trip['id'],name=name,content=(PACK/name).read_bytes(),key=name)
                for name in ['02-tokyo-hotel.eml','03-tokyo-dinner.eml','07-eight-bookings-one-day.eml']]
    rows = user.client.get(base+'/bookings').json()['items']
    corrected = next(row for row in rows if row['kind'] == '숙소')
    assert user.client.patch(base+'/bookings/'+corrected['id'],json={'expected_version':corrected['version'],
        'changes':[{'field_path':'time','value':'16:00'}]}).status_code == 200
    assert user.client.post(base+'/bookings',json={'provider':'Manual stop','date':'2026-11-07','time':'18:00'}).status_code == 201
    before = user.client.get(base+'/bookings').json()['items']
    active = {d['id']:d['active_generation_id'] for d in user.client.get(base+'/documents').json()['items']}
    assert user.client.get(base).json()['active_index_id'] is None
    assert not service.calls
    counts = {'parse':0}
    def parse(raw):
        counts['parse'] += 1
        return parse_local_document(raw)[0]
    service.app.state.documents.parser = parse
    service.settings.mail_analysis_mode = 'ai'
    return user, trip, base, receipts[-1]['accepted'][0]['document_id'], before, active, counts


def assert_complete(service,user,trip,base,did,before,active):
    after = user.client.get(base+'/bookings').json()['items']
    assert len(after) == len(before) == 11
    assert {r['id'] for r in after} == {r['id'] for r in before}
    assert {r['id']:r for r in after if r['document_id'] != did} == {r['id']:r for r in before if r['document_id'] != did}
    documents = user.client.get(base+'/documents').json()['items']
    assert all(d['active_generation_id'] == active[d['id']] for d in documents if d['id'] != did)
    with service.app.state.generations.reader(user.user['id'],trip['id']) as reader:
        assert {d['document_id'] for d in reader.generation['manifest']['documents']} == set(active)
    response = user.client.post(base+'/ask',json={'question':'2026년 11월 7일 전체 예약 알려줘'})
    assert response.status_code == 200
    assert 'Manual stop' in response.json()['answer'] and '총 11개' in response.json()['answer']
    assert len(response.json()['sources']) == 11


def test_upgrade_repairs_all_active_documents_and_reuses_healthy_vectors(service):
    user,trip,base,did,before,active,counts = prepare(service)
    result = _job(user.client,_reprocess(user.client,base+f'/documents/{did}/reprocess').json())
    assert result['state'] in ('succeeded','partial'), result
    assert_complete(service,user,trip,base,did,before,active)
    assert counts['parse'] == 1
    assert [call[0] for call in service.calls].count('embed') == 3
    again = _job(user.client,_reprocess(user.client,base+f'/documents/{did}/reprocess').json())
    assert again['state'] in ('succeeded','partial')
    assert [call[0] for call in service.calls].count('embed') == 4


def test_failed_upgrade_retry_reuses_completed_extraction_and_repair_vectors(service):
    user,trip,base,did,before,active,counts = prepare(service)
    def fail_ready(point,job):
        if point == 'after_ready':
            raise RuntimeError('synthetic activation interruption')
    service.app.state.operations.fault_hook = fail_ready
    receipt = _reprocess(user.client,base+f'/documents/{did}/reprocess').json()
    assert _job(user.client,receipt)['state'] == 'failed'
    assert user.client.get(base+'/bookings').json()['items'] == before
    assert user.client.get(base).json()['active_index_id'] is None
    calls_before = len(service.calls)
    service.app.state.operations.fault_hook = None
    retry = user.client.post('/api/v2/jobs/'+receipt['job_id']+'/retry',headers={'Idempotency-Key':uuid4().hex})
    assert retry.status_code == 202, retry.text
    assert _job(user.client,retry.json())['state'] in ('succeeded','partial')
    assert counts['parse'] == 1 and len(service.calls) == calls_before
    assert_complete(service,user,trip,base,did,before,active)


def test_repair_embedding_failure_preserves_old_facts_and_active_generations(service):
    user,trip,base,did,before,active,counts = prepare(service)
    embed = service.app.state.documents.embedder
    attempts = 0
    def fail_repair(texts):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError('synthetic repair failure')
        return embed(texts)
    service.app.state.documents.embedder = fail_repair
    result = _job(user.client,_reprocess(user.client,base+f'/documents/{did}/reprocess').json())
    assert result['state'] == 'failed'
    assert user.client.get(base+'/bookings').json()['items'] == before
    assert {d['id']:d['active_generation_id'] for d in user.client.get(base+'/documents').json()['items']} == active
    assert user.client.get(base).json()['active_index_id'] is None
