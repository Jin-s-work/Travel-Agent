"""Offline parsing and the real authenticated durable upload path; no live mail/API."""
from copy import deepcopy
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import pytest

from src.foundation.local_mail import parse_local_document
from src.foundation.repository import DomainError
from src.loader import read_email_bytes, read_email_file
from src.reliability.budget import BudgetPolicy
from tests.test_foundation_api import service, _trip, _upload, _job, _reprocess

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'examples/mail-test-pack'


@pytest.mark.parametrize('name,count', [
    ('01-tokyo-roundtrip.eml', 1), ('02-tokyo-hotel.eml', 1),
    ('03-tokyo-dinner.eml', 1), ('04-madrid-hotel.eml', 1),
    ('05-madrid-multiple.eml', 2), ('06-date-only-unknown-party.eml', 1),
    ('07-eight-bookings-one-day.eml', 8), ('08-madrid-time-change.eml', 1),
    ('09-html-only.eml', 1), ('10-dst-ambiguous-madrid.eml', 1),
    ('same-name-a/reservation.eml', 1), ('same-name-b/reservation.eml', 1),
])
def test_download_pack_is_parsed_without_model(name, count):
    raw = read_email_file(PACK / name)
    rows, reasons = parse_local_document(raw)
    assert len(rows) == count
    assert len({row['stable_item_key'] for row in rows}) == count
    assert 'BASIC_EXTRACTION_REVIEW' in reasons
    assert all(row['status'] == 'needs_review' and row['raw_snippet'] in raw for row in rows)


def test_roundtrip_preserves_both_local_dates_and_zones():
    row = parse_local_document(read_email_file(PACK / '01-tokyo-roundtrip.eml'))[0][0]
    assert row['confirmation_number'] == 'TEST-AIR-001'
    assert row['provider'] == 'Demo Air'
    assert [(e['event_type'], e['start_local'], e['end_local'], e['start_timezone'], e['end_timezone']) for e in row['events']] == [
        ('outbound', '2026-11-06T09:00:00', '2026-11-06T11:30:00', 'Asia/Seoul', 'Asia/Tokyo'),
        ('return', '2026-11-09T20:00:00', '2026-11-09T22:40:00', 'Asia/Tokyo', 'Asia/Seoul')]


def test_unknown_and_ambiguous_times_are_not_midnight_or_confirmed_changes():
    date_only = parse_local_document(read_email_file(PACK / '06-date-only-unknown-party.eml'))[0][0]
    assert date_only['date'] == '2026-11-15' and date_only['time'] is None and date_only['party'] is None
    assert date_only['events'][0]['start_local'] == '2026-11-15'
    rows, reasons = parse_local_document(read_email_file(PACK / '10-dst-ambiguous-madrid.eml'))
    assert rows[0]['time'] is None and rows[0]['events'][0]['start_local'] == '2026-10-25'
    assert 'AMBIGUOUS_LOCAL_TIME' in reasons
    rows, reasons = parse_local_document(read_email_file(PACK / '08-madrid-time-change.eml'))
    assert rows[0]['time'] is None and 'REQUEST_NOT_CONFIRMATION' in reasons


@pytest.mark.parametrize('path,kind,provider,start,clock,end,end_clock', [
    ('sample_emails/01_korean_air_outbound.txt', '항공', '대한항공', '2026-10-12', '09:20', '2026-10-12', '11:45'),
    ('sample_emails/02_ana_return.eml', '항공', 'ANA', '2026-10-17', '18:55', '2026-10-17', '21:35'),
    ('sample_emails/03_shinjuku_hotel.txt', '숙소', 'Hotel Gracery Shinjuku', '2026-10-12', '15:00', '2026-10-15', '11:00'),
    ('sample_emails/04_hakone_ryokan.txt', '숙소', 'Yumotoso Hakone (箱根 湯本荘)', '2026-10-15', '15:00', '2026-10-17', '10:00'),
    ('sample_emails/05_fuji_day_tour.txt', '투어', 'Japan Highlight Travel Co., Ltd', '2026-10-14', '07:40', '2026-10-14', '19:30'),
    ('demo_emails/06_hakone_rentcar.eml', '렌터카', 'Times CAR RENTAL', '2026-10-15', '13:00', '2026-10-17', '09:30'),
    ('demo_emails/07_asakusa_tour.eml', '투어', 'Tokyo Local Walks Inc', '2026-10-13', '16:30', '2026-10-13', '20:00'),
])
def test_original_demo_mail_regression(path, kind, provider, start, clock, end, end_clock):
    rows, _ = parse_local_document(read_email_file(ROOT / 'tests' / path))
    assert len(rows) == 1
    row = rows[0]
    assert (row['kind'], row['provider'], row['date'], row['time'], row['date_end'], row['time_end']) == (kind, provider, start, clock, end, end_clock)
    assert row['refund_policy']
    assert 'https://' not in row['provider']


def test_unknown_email_dates_and_policy_do_not_become_visit_dates():
    with pytest.raises(DomainError) as error:
        parse_local_document('From: a@example.invalid\nSubject: Hotel booking\nDate: 2026-11-06\n\nThanks for writing. No confirmed dates supplied.')
    assert error.value.code == 'LOCAL_EXTRACTION_UNSUPPORTED'
    with pytest.raises(DomainError) as policy_error:
        parse_local_document('Hotel Demo\nReference CONF-001\nIssued: 2026-11-01\nFree cancellation until 2026-11-05')
    assert policy_error.value.code == 'LOCAL_EXTRACTION_UNSUPPORTED'


def test_original_html_and_mime_are_inert_and_empty_plain_uses_html():
    mail = EmailMessage()
    mail['From'] = 'fixture@example.invalid'; mail['Subject'] = 'Hotel booking'
    mail.set_content(' ')
    mail.add_alternative('<p>Hotel Demo</p><p>Reference CONF-321</p><p>Check-in: 2026-11-06 15:00 Asia/Tokyo</p><script>Hotel ATTACK 2026-11-10</script><p>Check-out: 2026-11-08 11:00 Asia/Tokyo</p>', subtype='html')
    raw = read_email_bytes(mail.as_bytes(), 'mail.eml')
    assert 'ATTACK' not in raw
    row = parse_local_document(raw)[0][0]
    assert row['date'] == '2026-11-06' and row['date_end'] == '2026-11-08'


def local_mode(service, monkeypatch):
    service.app.state.documents.parser = None
    service.app.state.documents.embedder = None
    service.app.state.answer_generator = None
    service.app.state.operations.answer_generator = None
    service.app.state.budget.policy = BudgetPolicy()
    service.settings.mail_analysis_mode = 'auto'
    monkeypatch.setattr('src.config.OPENAI_API_KEY', None)
    def never(*args, **kwargs):
        pytest.fail('Local mail flow attempted a provider call')
    monkeypatch.setattr(service.app.state.gateway, 'run', never)
    monkeypatch.setattr(service.app.state.generations, 'build', never)
    return service.login('basic')


def test_authenticated_free_upload_eight_records_manual_question_and_no_billing(service, monkeypatch):
    user = local_mode(service, monkeypatch)
    trip = _trip(user.client)
    base = f"/api/v2/trips/{trip['id']}"
    capabilities = user.client.get('/api/v2/mail-capabilities').json()
    assert capabilities['analysis_mode'] == 'local' and not capabilities['external_calls']
    assert capabilities['reason_code'] == 'AI_KEY_NOT_CONFIGURED'
    first = _upload(user.client, trip['id'], name='eight.eml', content=(PACK / '07-eight-bookings-one-day.eml').read_bytes())
    result = _job(user.client, first)
    assert result['state'] == 'succeeded' and result['review_required']
    assert result['files'][0]['analysis_mode'] == 'local' and result['files'][0]['bookings_count'] == 8
    assert result['files'][0]['review_reasons'] == ['BASIC_EXTRACTION_REVIEW']
    rows = user.client.get(base + '/bookings').json()['items']
    assert len(rows) == 8 and all(row['status'] == 'needs_review' for row in rows)
    manual = user.client.post(base + '/bookings', json={'kind':'투어','provider':'Manual stop', 'date':'2026-11-07','time':'16:00'})
    assert manual.status_code == 201
    answer = user.client.post(base + '/ask', json={'question':'둘째 날 전체 예약 알려줘'}).json()
    assert '총 9개' in answer['answer'] and len(answer['sources']) == 9
    detail = user.client.post(base + '/ask', json={'question':'Fictional Tokyo Demo Stop 1 예약번호 알려줘'}).json()
    assert detail['answer_mode'] == 'structured' and 'TEST-EIGHT-01' in detail['answer']
    assert len(detail['sources']) == 1
    documents = user.client.get(base + '/documents').json()['items']
    assert documents[0]['latest_generation']['parse_version'] == 'local-mail-v1'
    assert user.client.get(base).json()['active_index_id'] is None
    with service.app.state.db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM usage_reservations').fetchone()[0] == 0
        assert con.execute('SELECT COUNT(*) FROM trip_index_generations').fetchone()[0] == 0
    other = service.login('other')
    assert other.client.get(base + '/documents').status_code == 404
    assert other.client.get('/api/v2/jobs/' + first['job_id']).status_code == 404


def test_free_reextraction_preserves_correction_and_failure_preserves_active_generation(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    base = f"/api/v2/trips/{trip['id']}"
    receipt = _upload(user.client, trip['id'], name='hotel.eml', content=(PACK / '02-tokyo-hotel.eml').read_bytes())
    document_id = receipt['accepted'][0]['document_id']
    row = user.client.get(base + '/bookings').json()['items'][0]
    corrected = user.client.patch(base + '/bookings/' + row['id'], json={'expected_version':row['version'], 'changes':[{'field_path':'time','value':'16:00'}]})
    assert corrected.status_code == 200
    rerun = _reprocess(user.client, base + f'/documents/{document_id}/reprocess')
    assert _job(user.client, rerun.json())['files'][0]['state'] == 'needs_review'
    after = user.client.get(base + '/bookings').json()['items'][0]
    assert after['id'] == row['id'] and after['time'] == '16:00'
    active = user.client.get(base + '/documents').json()['items'][0]['active_generation_id']
    monkeypatch.setattr(service.app.state.documents, 'raw_text', lambda document:'unstructured unsupported text')
    failed = _job(user.client, _reprocess(user.client, base + f'/documents/{document_id}/reprocess').json())
    assert failed['files'][0]['error_code'] == 'LOCAL_EXTRACTION_UNSUPPORTED'
    assert user.client.get(base + '/documents').json()['items'][0]['active_generation_id'] == active
    assert user.client.get(base + '/bookings').json()['items'][0]['time'] == '16:00'
    download = user.client.get(base + f'/documents/{document_id}/content')
    assert download.status_code == 200 and download.content == (PACK / '02-tokyo-hotel.eml').read_bytes()


def test_unsupported_file_is_preserved_and_can_be_reprocessed_after_repair(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    receipt = _upload(user.client, trip['id'], content=b'Unknown file layout')
    failed = _job(user.client, receipt)
    assert failed['state'] == 'failed' and failed['files'][0]['error_code'] == 'LOCAL_EXTRACTION_UNSUPPORTED'
    monkeypatch.setattr(service.app.state.documents, 'raw_text', lambda document:read_email_file(PACK / '03-tokyo-dinner.eml'))
    response = user.client.post('/api/v2/jobs/' + receipt['job_id'] + '/retry', headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code == 202
    assert _job(user.client, response.json())['files'][0]['bookings_count'] == 1


def test_local_delete_job_does_not_reembed_or_resurrect_bookings(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    receipt = _upload(user.client, trip['id'], name='hotel.eml', content=(PACK / '02-tokyo-hotel.eml').read_bytes())
    base = f"/api/v2/trips/{trip['id']}"
    document_id = receipt['accepted'][0]['document_id']
    result = user.client.delete(base + '/documents/' + document_id)
    assert result.status_code == 202
    assert _job(user.client, result.json())['state'] == 'succeeded'
    assert user.client.get(base + '/bookings').json()['items'] == []
    assert user.client.get(base + '/documents').json()['items'] == []


def test_sql_question_uses_corrected_facts_and_unknown_questions_never_invent(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    base = f"/api/v2/trips/{trip['id']}"
    manual = user.client.post(base + '/bookings', json={'kind':'hotel','provider':'Hotel Fixture','date':'2026-11-06','time':'17:00','refund_policy':'전날까지 취소 가능'})
    assert manual.status_code == 201
    answer = user.client.post(base + '/ask', json={'question':'호텔 체크인 몇 시야?'}).json()
    assert '17:00' in answer['answer'] and answer['answer_mode'] == 'structured'
    follow = user.client.post(base + '/ask', json={'question':'그 숙소 취소 규정?', 'history':[{'role':'assistant','content':answer['answer']}]}).json()
    assert '전날까지 취소 가능' in follow['answer']
    unknown = user.client.post(base + '/ask', json={'question':'지금 날씨랑 여권 번호를 알려줘'}).json()
    assert unknown['sources'] == [] and '기본 분석에서는' in unknown['answer']


def test_same_filename_is_not_an_id_and_duplicate_only_within_trip(service, monkeypatch):
    user = local_mode(service, monkeypatch); first_trip = _trip(user.client); other_trip = _trip(user.client)
    receipts = []
    for folder in ('same-name-a','same-name-b'):
        receipts.append(_upload(user.client, first_trip['id'], name='reservation.eml', content=(PACK / folder / 'reservation.eml').read_bytes(), key=folder))
    assert len({r['accepted'][0]['document_id'] for r in receipts}) == 2
    again = _upload(user.client, first_trip['id'], name='reservation.eml', content=(PACK / 'same-name-a/reservation.eml').read_bytes(), key='again')
    assert again['duplicates'][0]['document_id'] == receipts[0]['accepted'][0]['document_id']
    other = _upload(user.client, other_trip['id'], name='reservation.eml', content=(PACK / 'same-name-a/reservation.eml').read_bytes(), key='other')
    assert other['accepted'][0]['document_id'] != receipts[0]['accepted'][0]['document_id']


def test_deleted_document_during_local_processing_cannot_activate(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    operations = service.app.state.operations
    def fault(point, job):
        if point == 'after_extraction':
            did = job['payload']['accepted'][0]['document_id']
            service.app.state.repo.delete_document(user.user['id'], trip['id'], did)
    operations.fault_hook = fault
    receipt = _upload(user.client, trip['id'], name='hotel.eml', content=(PACK / '02-tokyo-hotel.eml').read_bytes())
    result = _job(user.client, receipt)
    assert result['state'] == 'cancelled' and result['files'][0]['error_code'] == 'DOCUMENT_DELETED'
    assert user.client.get(f"/api/v2/trips/{trip['id']}/bookings").json()['items'] == []


def test_switch_to_local_retires_old_semantic_index_without_losing_facts(service, monkeypatch):
    user = service.login('original'); trip = _trip(user.client)
    receipt = _upload(user.client, trip['id'], name='hotel.eml', content=(PACK / '02-tokyo-hotel.eml').read_bytes())
    old_index = user.client.get(f"/api/v2/trips/{trip['id']}").json()['active_index_id']
    assert old_index
    local_mode(service, monkeypatch)
    path = f"/api/v2/trips/{trip['id']}/documents/{receipt['accepted'][0]['document_id']}/reprocess"
    result = _job(user.client, _reprocess(user.client,path).json())
    assert result['files'][0]['state'] == 'needs_review'
    assert user.client.get(f"/api/v2/trips/{trip['id']}").json()['active_index_id'] is None
    # Unmatched older facts are flagged for review, never deleted by partial parsing.
    rows = user.client.get(f"/api/v2/trips/{trip['id']}/bookings").json()['items']
    assert len(rows) == 2 and all(b['status'] == 'needs_review' for b in rows)


def test_capability_fails_closed_for_bad_mode_and_halted_paid_budget(service, monkeypatch):
    local_mode(service, monkeypatch)
    service.settings.mail_analysis_mode = 'unexpected'
    assert service.app.state.operations.mail_capabilities()['analysis_mode'] == 'local'
    from src.config import EXTRACTION_MODEL, EMBEDDING_MODEL, ANSWER_MODEL
    config = deepcopy(BudgetPolicy.for_tests().config)
    template = next(iter(config['prices'].values()))
    config['prices'] = {'openai/' + model:template for model in {EXTRACTION_MODEL,EMBEDDING_MODEL,ANSWER_MODEL}}
    config['halted'] = True
    service.app.state.budget.policy = BudgetPolicy(config)
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    service.settings.mail_analysis_mode = 'auto'
    result = service.app.state.operations.mail_capabilities()
    assert result['analysis_mode'] == 'local' and result['reason_code'] == 'AI_BUDGET_PAUSED'
    config['halted'] = False
    service.app.state.budget.policy = BudgetPolicy(config)
    assert service.app.state.operations.mail_capabilities()['analysis_mode'] == 'ai'
    service.settings.mail_analysis_mode = 'local'
    result = service.app.state.operations.mail_capabilities()
    assert result['analysis_mode'] == 'local' and result['ai_available']


def test_crlf_and_utf8_bom_text_dont_use_sent_date():
    raw = '\ufeffFrom: fixture@example.invalid\r\nSubject: Hotel booking\r\nDate: 2026-10-01\r\n\r\nHotel Offline\r\nReference ABCDEF\r\nCheck-in: 2026-11-06 15:00\r\nCheck-out: 2026-11-08 11:00'
    text = read_email_bytes(raw.encode(), 'booking.txt')
    row = parse_local_document(text)[0][0]
    assert row['date'] == '2026-11-06' and row['raw_snippet'] in text
    assert row['confirmation_number'] == 'ABCDEF'


def test_unsupported_independent_record_is_not_silently_omitted():
    with pytest.raises(DomainError) as error:
        parse_local_document('1. Hotel One; 2026-11-06; Reference ONE123.\n2. Hotel Two; 2026-11-07; Reference TWO456.\n3. Unrecognised confirmation, date not provided.')
    assert error.value.code == 'LOCAL_EXTRACTION_UNSUPPORTED'


def test_checkpoint_retry_reuses_extraction_with_review_reasons(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    import src.foundation.local_mail as parser
    original = parser.parse_local_document
    calls = []
    def tracked(raw):
        calls.append(1)
        return original(raw)
    monkeypatch.setattr(parser, 'parse_local_document', tracked)
    def fail(point, job):
        if point == 'after_extraction': raise RuntimeError('synthetic checkpoint interruption')
    service.app.state.operations.fault_hook = fail
    first = _upload(user.client, trip['id'], name='hotel.eml', content=(PACK / '02-tokyo-hotel.eml').read_bytes())
    assert _job(user.client, first)['state'] == 'failed' and len(calls) == 1
    service.app.state.operations.fault_hook = None
    response = user.client.post('/api/v2/jobs/' + first['job_id'] + '/retry', headers={'Idempotency-Key':uuid4().hex})
    result = _job(user.client, response.json())
    assert result['state'] == 'succeeded' and len(calls) == 1
    assert result['files'][0]['review_reasons'] == ['BASIC_EXTRACTION_REVIEW']


@pytest.mark.parametrize('suffix', [
    '\nCancellation deadline 2026-11-06 12:00 Asia/Tokyo\nReservation time not yet known.',
    '. Free cancellation until 12:00.',
    '\nPayment due: 2026-11-06 12:00 Asia/Tokyo',
])
def test_deadline_clock_is_never_attached_to_date_only_visit(suffix):
    row = parse_local_document('Restaurant Example\nReference: ABC123\nVisit date: 2026-11-06' + suffix)[0][0]
    assert row['date'] == '2026-11-06' and row['time'] is None
    assert row['events'][0]['start_local'] == '2026-11-06'


@pytest.mark.parametrize('label', ['Payment due:', 'Issued on:', 'Cancellation deadline:', '취소 마감:', '결제 기한:'])
def test_a_single_non_visit_date_cannot_become_a_booking_date(label):
    with pytest.raises(DomainError) as error:
        parse_local_document('Hotel Example\nReference: ABC123\n' + label + ' 2026-11-01 23:59 Asia/Tokyo\nYour check-in date will be confirmed later.')
    assert error.value.code == 'LOCAL_EXTRACTION_UNSUPPORTED'


@pytest.mark.parametrize('notice,reason', [
    ('Change request pending. This is not a confirmed reservation.', 'REQUEST_NOT_CONFIRMATION'),
    ('Your reservation has been cancelled.', 'RESERVATION_CANCELLED_REVIEW'),
])
def test_pending_and_cancelled_stays_do_not_gain_exact_visit_times(notice, reason):
    rows, reasons = parse_local_document('Hotel Example\nReference: ABC123\nCheck-in: 2026-11-08 15:00 Asia/Tokyo\nCheck-out: 2026-11-09 11:00 Asia/Tokyo\n' + notice)
    assert reason in reasons and rows[0]['time'] is None and rows[0]['time_end'] is None
    assert rows[0]['status'] == 'needs_review'
    assert rows[0]['events'][0]['start_local'] == '2026-11-08'


def test_airport_pickup_sentence_does_not_reclassify_a_stay():
    row = parse_local_document('Hotel Example\nReference: ABC123\nCheck-in: 2026-11-06 15:00 Asia/Tokyo\nCheck-out: 2026-11-08 11:00 Asia/Tokyo\nPlease tell us your flight number for pickup.')[0][0]
    assert row['kind'] == '숙소' and row['date'] == '2026-11-06' and row['time'] == '15:00'


def test_header_only_email_never_uses_sent_date_for_a_reservation():
    mail = b'From: fixture@example.invalid\r\nSubject: Hotel Example reservation ABC123\r\nDate: Tue, 6 Oct 2026 09:00:00 +0900\r\n\r\n'
    with pytest.raises(DomainError) as error:
        parse_local_document(read_email_bytes(mail, 'empty.eml'))
    assert error.value.code == 'MAIL_BODY_EMPTY'


def test_oversized_date_list_has_a_bounded_failure():
    with pytest.raises(DomainError) as error:
        parse_local_document('Hotel Example\nReference: ABC123\n' + '2026-11-06 ' * 513)
    assert error.value.code == 'LOCAL_EXTRACTION_UNSUPPORTED'


@pytest.mark.parametrize('line', ['住所と緯度経度は未提供。施設名だけで座標を確定しないでください。','住所: 未提供','Address: Not provided','주소: 미확인'])
def test_unavailable_address_never_becomes_a_location(line):
    row = parse_local_document('Hotel Example\nReference ABC123\nCheck-in: 2026-11-06 15:00\nCheck-out: 2026-11-08 11:00\n' + line)[0][0]
    assert row['location'] is None


def test_explicit_japanese_address_is_preserved():
    row = parse_local_document('Hotel Example\nReference ABC123\nCheck-in: 2026-11-06 15:00\nCheck-out: 2026-11-08 11:00\n住所: 東京都新宿区歌舞伎町1-1-1')[0][0]
    assert row['location'] == '東京都新宿区歌舞伎町1-1-1'


@pytest.mark.parametrize('case',__import__('json').loads((ROOT/'examples/mail-time-pack/expected-results.json').read_text()))
def test_time_practice_pack_preserves_both_ends(case):
    row=parse_local_document(read_email_file(ROOT/'examples/mail-time-pack'/case['file']))[0][0]
    assert {key:row[key] for key in ('date','time','date_end','time_end')}=={key:case[key] for key in ('date','time','date_end','time_end')}
