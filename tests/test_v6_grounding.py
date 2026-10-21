"""Bundle F: generated /ask answers are shown only when every atom is grounded.

All data is synthetic. Generators here are hand-written fakes, so nothing in
this file measures model quality; it fixes the verifier's contract only.
"""
from __future__ import annotations

import json

import pytest

from src.foundation.grounding import check, extract_atoms, strip_source_lines
from tests.test_foundation_api import _fact, _trip, _upload, service  # noqa: F401  (fixture)
from tests.test_local_mail import local_mode


def _atoms(text):
    return [(atom.kind, sorted(atom.keys)) for atom in extract_atoms(text)]


def _hit(**facts):
    return {'metadata': dict(facts), 'document': json.dumps(facts, ensure_ascii=False), 'similarity': 1.0}


# --- atom extraction (synthetic strings) -----------------------------------

@pytest.mark.parametrize('text, expected', [
    ('2026-11-07', [('date', ['2026-11-07'])]),
    ('2026년 11월 7일', [('date', ['2026-11-07'])]),
    ('11월 7일', [('date', ['11-07'])]),
    ('11/7', [('date', ['11-07'])]),
    ('Nov 7', [('date', ['11-07'])]),
    ('November 7th, 2026', [('date', ['2026-11-07'])]),
    ('7 Nov 2026', [('date', ['2026-11-07'])]),
    ('you may 2 go', []),
])
def test_dates_in_korean_english_and_numeric_forms(text, expected):
    assert _atoms(text) == expected


@pytest.mark.parametrize('text, expected', [
    ('14:00', ['14:00']),
    ('9:05', ['09:05']),
    ('오후 2시 30분', ['14:30']),
    ('오전 9시', ['09:00']),
    ('오전 12시', ['00:00']),
    ('9:30 PM', ['21:30']),
    ('9 am', ['09:00']),
    ('3시 반', ['03:30', '15:30']),
])
def test_times_are_zero_padded_24h(text, expected):
    assert _atoms(text) == [('time', expected)]


def test_duration_hours_are_not_clock_times():
    assert _atoms('1시간 걸립니다') == []


@pytest.mark.parametrize('text, key', [
    ('JPY 3000', 'JPY 3000'), ('¥3,000', 'JPY 3000'), ('3,000엔', 'JPY 3000'), ('3000 JPY', 'JPY 3000'),
    ('EUR 30', 'EUR 30'), ('€30', 'EUR 30'), ('30유로', 'EUR 30'), ('€30.00', 'EUR 30'),
    ('$214.50', 'USD 214.5'), ('USD 214.50', 'USD 214.5'), ('3만 원', 'KRW 30000'),
])
def test_money_normalises_separators_and_currency_symbols(text, key):
    assert _atoms(text) == [('money', [key])]


def test_reservation_numbers_and_party_counts():
    assert _atoms('예약번호 ABC-1234') == [('reservation', ['abc-1234'])]
    assert _atoms('성인 2명') == [('party', ['2'])]
    assert _atoms('2 adults') == [('party', ['2'])]
    # Upper case without a digit, lower-case ids and dates are not reservations.
    assert _atoms('REFUND POLICY bkg_0123abcd') == []
    assert _atoms('2026-11-07T14:00:00') == [('date', ['2026-11-07']), ('time', ['14:00'])]


def test_model_written_source_lines_are_removed():
    text = '체크인은 15:00입니다. (출처: Hotel)\n출처: Hotel (예약번호 FAKE-999)\n**Sources:** x'
    assert strip_source_lines(text) == '체크인은 15:00입니다.'


def test_unbracketed_mid_line_source_word_is_kept_and_checked():
    assert strip_source_lines('Open source: 09:00') == 'Open source: 09:00'
    assert not check('Open source: 09:00', [_hit(time='14:00')]).verified


@pytest.mark.parametrize('text, expected', [
    ('예약번호 abc12345', [('reservation', ['abc12345'])]),
    ('confirmation no. xy-2026a', [('reservation', ['xy-2026a'])]),
    ('Ab12345', [('reservation', ['ab12345'])]),
    ('수수료 50%', [('percent', ['50'])]),
    ('3일 전까지', [('before', ['d3'])]),
    ('72시간 전', [('before', ['h72'])]),
    ('3 days before', [('before', ['d3'])]),
    ('요금은 15,000입니다', [('number', ['15000'])]),
    ('1만 5천원', [('money', ['KRW 15000'])]),
    ('밤 12시', [('time', ['00:00'])]),
    ('낮 12시', [('time', ['12:00'])]),
    ('2 시티투어', []),
    ('2026-11-07T06:00:00+00:00', [('instant', ['2026-11-07T06:00:00Z'])]),
])
def test_wider_atom_kinds_and_normalisation_fixes(text, expected):
    assert _atoms(text) == expected


def test_wider_atoms_are_grounded_only_by_matching_facts():
    hits = [_hit(confirmation_number='abc12345', refund_policy='Free until 3 days before; fee 50% (JPY 15,000)')]
    assert check('예약번호 abc12345, 3일 전까지 무료, 이후 수수료 50%·15,000', hits).verified
    for wrong in ('예약번호 abc99999', '5일 전까지', '수수료 30%', '요금은 12,000입니다', 'Ab12345'):
        assert not check(wrong, hits).verified, wrong


# --- production-shaped facts (search.facts with events and UTC instants) ----

def _production_hit(start_local, zone, **booking):
    from src.foundation.repository import _event_facts
    from src.foundation.search import facts
    event = _event_facts({'event_type': 'checkin', 'start_local': start_local, 'end_local': None,
                          'start_timezone': zone, 'end_timezone': None, 'location': None}, 'evt_synthetic')
    row = {'id': 'bkg_synthetic', 'kind': '숙소', 'provider': 'Synthetic Inn', 'confirmation_number': 'SYN-1',
           'date': start_local[:10], 'time': start_local[11:], 'events': [event],
           'source_file': 'synthetic_0600.eml', **booking}
    # Same construction as search._answer: metadata + serialised facts document.
    return {'metadata': {**facts(row), 'type': row['kind'], 'source_file': row['source_file']},
            'document': json.dumps(facts(row), ensure_ascii=False), 'similarity': 1.0}


def test_utc_instant_hour_and_offset_do_not_ground_bare_times():
    hit = _production_hit('2026-11-07T15:00', 'Asia/Tokyo')  # instant 06:00 UTC
    assert hit['metadata']['events'][0]['start_instant'].startswith('2026-11-07T06:00')
    assert check('체크인은 15:00입니다.', [hit]).verified
    assert not check('체크인은 06:00입니다.', [hit]).verified
    assert not check('체크인은 00:00입니다.', [hit]).verified
    # The whole instant echoed back is still the same fact.
    assert check('2026-11-07T06:00:00+00:00', [hit]).verified


def test_utc_date_rollover_does_not_ground_the_next_day():
    hit = _production_hit('2026-11-07T20:00', 'America/New_York')  # 2026-11-08 01:00 UTC
    assert check('11월 7일 20:00', [hit]).verified
    assert not check('11월 8일 체크인', [hit]).verified
    assert not check('01:00 체크인', [hit]).verified


def test_tool_output_documents_skip_noise_keys_and_count_party():
    # Trip agent hits carry JSON tool output in ``document`` and no metadata.
    from src.foundation.repository import _event_facts
    event = _event_facts({'event_type': 'checkin', 'start_local': '2026-11-07T15:00', 'end_local': None,
                          'start_timezone': 'Asia/Tokyo', 'end_timezone': None, 'location': None}, 'evt_synthetic')
    tool = {'bookings': [{'id': 'bkg_synthetic', 'time': '15:00', 'events': [event],
                          'party': {'adults': 2, 'children': 1}}], '_sources': [{'source_file': 'x_0600.eml'}]}
    hits = [{'metadata': {}, 'document': json.dumps(tool)}]
    assert check('15:00, 성인 2명·아동 1명, 총 3명', hits).verified
    assert not check('06:00', hits).verified
    assert check('plain text 09:00', [{'metadata': {}, 'document': 'opens at 09:00'}]).verified


# --- verdicts against facts --------------------------------------------------

def test_all_atoms_in_facts_is_verified_with_deterministic_normalisation():
    hits = [_hit(provider='Synthetic Hotel', confirmation_number='SYN-77', date='2026-11-07', time='9:00',
                 refund_policy='Cancellation fee JPY 3,000 per 2 adults')]
    verdict = check('11월 7일 오전 9시, 수수료 3000엔(성인 2명). 예약번호 SYN-77.\n출처: Synthetic Hotel (예약번호 OTHER-1)', hits)
    assert verdict.verified and len(verdict.atoms) == 5
    assert '출처' not in verdict.text


def test_any_ungrounded_atom_fails_and_yearless_date_must_match_month_day():
    hits = [_hit(date='2026-11-07', time='14:00')]
    assert check('11/7 14:00', hits).verified
    assert not check('11/8 14:00', hits).verified
    assert len(check('2026-11-07 14:00, EUR 30', hits).ungrounded) == 1


def test_superseded_original_value_is_ungrounded_because_facts_are_effective_only():
    effective = [_hit(time='14:00', date='2026-11-07')]  # extracted 09:00, user corrected to 14:00
    assert check('집합 시각은 14:00입니다.', effective).verified
    assert not check('집합 시각은 09:00입니다.', effective).verified


def test_other_trip_reservation_number_is_ungrounded():
    mine = [_hit(provider='Alpha tour', confirmation_number='ALPHA-101')]
    assert check('ALPHA-101', mine).verified
    assert not check('ALPHA-101 / BETA-202', mine).verified
    assert not check('ALPHA-1010', mine).verified


# --- HTTP branches (fake generator, synthetic facts) --------------------------

def _ask(actor, trip, question, **body):
    response = actor.client.post(f"/api/v2/trips/{trip['id']}/ask", json={'question': question, **body})
    assert response.status_code == 200, response.text
    return response.json()


def _generated(calls, before):
    return [call[0] for call in calls[before:] if call[0] == 'generate']


def test_echoing_generator_is_verified_once_with_prompt_meta(service):
    actor = service.login('A'); trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    before = len(service.calls)
    result = _ask(actor, trip, 'Synthetic Tour 0 환불 규정')
    assert result['grounding'] == 'verified' and result['answer_type'] == 'generated'
    assert result['model_ungrounded_atoms'] == 0 and result['tools_used'] == ['search_bookings']
    assert result['prompt']['id'] == 'trip_answer' and set(result['prompt']) == {'id', 'version', 'hash'}
    assert 'Synthetic refundable policy' in result['answer'] and 'answer_mode' not in result
    assert _generated(service.calls, before) == ['generate']


def test_corrected_value_wins_and_original_value_answer_falls_back(service):
    actor = service.login('A'); trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    base = f"/api/v2/trips/{trip['id']}"
    booking = actor.client.get(base + '/bookings').json()['items'][0]
    assert actor.client.patch(base + '/bookings/' + booking['id'], json={
        'expected_version': booking['version'], 'changes': [{'field_path': 'time', 'value': '14:00'}]}).status_code == 200
    calls = []
    service.app.state.answer_generator = lambda question, hits: calls.append(hits) or 'Synthetic Tour 0 집합은 09:00입니다.'
    result = _ask(actor, trip, 'Synthetic Tour 0 몇 시야?')
    assert len(calls) == 1 and all('09:00' not in hit['document'] for hit in calls[0])
    assert result['grounding'] == 'fallback' and result['answer_type'] == 'fact'
    assert result['model_ungrounded_atoms'] == 1 and result['answer_mode'] == 'structured'
    assert '14:00' in result['answer'] and '09:00' not in result['answer']
    assert [s['booking_id'] for s in result['sources']] == [booking['id']]


def test_model_source_line_is_not_shown_or_checked(service):
    actor = service.login('A'); trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    service.app.state.answer_generator = lambda question, hits: '환불 가능합니다.\n출처: Invented (예약번호 FAKE-999)'
    result = _ask(actor, trip, 'Synthetic Tour 0 환불 규정')
    assert result['grounding'] == 'verified' and result['answer'] == '환불 가능합니다.'
    assert result['sources'][0]['provider'] == 'Synthetic Tour 0'


def test_other_trip_facts_are_not_passed_and_their_values_fall_back(service):
    a, b = service.login('A'), service.login('B')
    at, bt = _trip(a.client), _trip(b.client)
    service.state.parsed = [_fact(provider='Alpha tour', confirmation_number='ALPHA-101')]
    _upload(a.client, at['id'])
    service.state.parsed = [_fact(provider='Beta tour', confirmation_number='BETA-202', time='16:00')]
    _upload(b.client, bt['id'])
    seen = []

    def leaky(question, hits):
        seen.append(json.dumps(hits, ensure_ascii=False))
        return ' | '.join(hit['document'] for hit in hits) + ' 그리고 BETA-202 16:00'

    service.app.state.answer_generator = leaky
    result = _ask(a, at, 'Alpha tour 환불 규정')
    assert 'BETA-202' not in seen[0] and 'Beta' not in seen[0]
    assert result['grounding'] == 'fallback' and result['model_ungrounded_atoms'] == 2
    assert 'BETA-202' not in result['answer'] and 'Beta' not in result['answer']


def test_weak_semantic_hits_refuse_without_generating(service):
    actor = service.login('A'); trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    # Index vectors are [0.1]*8; an orthogonal query has cosine similarity 0.
    service.app.state.documents.embedder = lambda texts: [[0.1, -0.1] * 4 for _ in texts]
    service.app.state.answer_generator = lambda *_: pytest.fail('generator must not run below the floor')
    result = _ask(actor, trip, '취소 규정 알려줘')
    assert result['grounding'] == 'refused' and result['answer_type'] == 'refusal'
    assert '메일에서 확인하지 못했어요' in result['answer'] and result['sources'] == []
    assert [item['type'] for item in result['next_actions']] == ['upload_mail', 'ask_by_date']


def test_floor_drops_only_the_weak_document_when_hits_are_mixed(service):
    actor = service.login('A'); trip = _trip(actor.client)
    service.state.parsed = [_fact(0, provider='Alpha tour', confirmation_number='ALPHA-1')]
    _upload(actor.client, trip['id'], name='alpha.txt', content=b'Synthetic alpha', key='alpha')
    # Beta is indexed (and the query embedded) with a vector orthogonal to Alpha's:
    # the query hits Beta with similarity 1 and Alpha with similarity 0.
    service.app.state.documents.embedder = lambda texts: [[0.1, -0.1] * 4 for _ in texts]
    service.state.parsed = [_fact(1, provider='Beta tour', confirmation_number='BETA-2')]
    _upload(actor.client, trip['id'], name='beta.txt', content=b'Synthetic beta', key='beta')
    seen = []
    service.app.state.answer_generator = lambda question, hits: seen.append(hits) or '확인했습니다.'
    result = _ask(actor, trip, '취소 규정 알려줘')
    assert [s['provider'] for s in result['sources']] == ['Beta tour']
    assert len(seen) == 1 and [hit['metadata']['provider'] for hit in seen[0]] == ['Beta tour']
    assert result['grounding'] == 'verified'


def test_empty_answer_after_source_lines_falls_back(service):
    actor = service.login('A'); trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    service.app.state.answer_generator = lambda *_: '출처: Synthetic Tour 0 (예약번호 SYN-0)'
    result = _ask(actor, trip, 'Synthetic Tour 0 환불 규정')
    assert result['grounding'] == 'fallback' and result['answer_mode'] == 'structured'
    assert result['model_ungrounded_atoms'] == 0 and result['answer'].strip()


def test_party_question_is_answered_from_sql_without_generation(service):
    actor = service.login('A'); trip = _trip(actor.client)
    service.state.parsed = [_fact(0, party={'adults': 2, 'children': [], 'children_status': 'none'})]
    _upload(actor.client, trip['id'])
    service.app.state.answer_generator = lambda *_: pytest.fail('party questions must not call the generator')
    result = _ask(actor, trip, 'Synthetic Tour 0 인원 몇 명이야?')
    assert result['grounding'] == 'structured' and result['answer_type'] == 'fact'
    assert result['tools_used'] == ['structured_bookings'] and '성인 2명' in result['answer']


def test_floor_is_a_setting_and_manual_rows_stay_eligible(service, monkeypatch):
    actor = service.login('A'); trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    monkeypatch.setattr('src.config.ANSWER_MIN_SIMILARITY', 1.5)
    assert _ask(actor, trip, '취소 규정 알려줘')['grounding'] == 'refused'
    manual = actor.client.post(f"/api/v2/trips/{trip['id']}/bookings", json={
        'kind': '투어', 'provider': 'Manual stop', 'date': '2026-11-07', 'time': '16:00'})
    assert manual.status_code == 201
    result = _ask(actor, trip, '취소 규정 알려줘')
    assert result['grounding'] == 'verified'
    assert [s['provider'] for s in result['sources']] == ['Manual stop']


def test_nothing_found_listing_clarify_and_model_refusal_branches(service):
    actor = service.login('A'); empty = _trip(actor.client)
    nothing = _ask(actor, empty, '환불 규정 알려줘')
    assert nothing['grounding'] == 'refused' and nothing['answer_type'] == 'refusal'
    trip = _trip(actor.client)
    _upload(actor.client, trip['id'])
    before = len(service.calls)
    listing = _ask(actor, trip, '2026-11-07 일정 알려줘')
    assert listing['grounding'] == 'structured' and listing['answer_type'] == 'listing'
    assert listing['tools_used'] == ['bookings_on_date'] and len(service.calls) == before
    clarify = _ask(actor, trip, '그거 환불돼?')
    assert clarify['grounding'] == 'clarify' and clarify['answer_type'] == 'clarification'
    bad_day = _ask(actor, trip, '9일차 일정')
    assert bad_day['grounding'] == 'clarify'
    from src.config import NO_INFO_MESSAGE
    service.app.state.answer_generator = lambda *_: NO_INFO_MESSAGE
    refusal = _ask(actor, trip, 'Synthetic Tour 0 환불 규정')
    assert refusal['grounding'] == 'verified' and refusal['answer_type'] == 'refusal'


def test_basic_mode_structured_and_unsupported_branches(service, monkeypatch):
    user = local_mode(service, monkeypatch); trip = _trip(user.client)
    base = f"/api/v2/trips/{trip['id']}"
    assert user.client.post(base + '/bookings', json={'kind': 'hotel', 'provider': 'Hotel Fixture',
        'date': '2026-11-06', 'time': '17:00'}).status_code == 201
    detail = _ask(user, trip, '호텔 체크인 몇 시야?')
    assert detail['grounding'] == 'structured' and detail['answer_type'] == 'fact' and '17:00' in detail['answer']
    unknown = _ask(user, trip, '지금 날씨랑 여권 번호를 알려줘')
    assert unknown['grounding'] == 'refused' and unknown['answer_mode'] == 'structured'
