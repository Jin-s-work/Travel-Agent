"""v6 stage 2: prompt registry identity and pipeline-signature compatibility.

The three sha256 values are the prompts as they were embedded in src/parser.py
and src/rag.py before the registry (2026-10-21). A request hash covers these
bytes, so any drift would make in-flight jobs conflict with their receipts.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from uuid import uuid4

import pytest

from src.config import EMBEDDING_MODEL, EXTRACTION_MODEL, NO_INFO_MESSAGE
from src.foundation.repository import DomainError
from src.loader import read_email_bytes
import src.prompts as prompts
from src.prompts import load_prompt, registry
from src.reliability import handlers
from src.reliability.handlers import document_signatures, stable_hash
from tests.test_foundation_api import service, _job  # noqa: F401 - fixture
from tests.test_v6_extraction_fallback import DINNER_BYTES, DINNER_GOOD, ai_replay, ledger, upload
from tests.test_v6_llm_gateway import booking, document, extraction_kwargs, policy_with, put_record, replay_runtime_prices

SINGLE_SHA256 = 'b43b3cc6cee5c7dd0487b1696b4cd70183c3debf4bbb92320275591b407b0784'    # parser.SYSTEM_PROMPT
DOCUMENT_SHA256 = 'c3cb4036dc53f745197244634ff070a9230634c4b7245eded91ba4f2cea0adba'  # parser.DOCUMENT_SYSTEM_PROMPT
ANSWER_SHA256 = '72a2a27491ae3323472f80873ee2b22629d93a4a35e7023a1109c98479da8a8a'    # rag.SYSTEM_PROMPT (rendered)
# stable_hash({'parser':'local-mail-v1','chunker':'v1','extraction_model':None,'embedding_model':None})
LOCAL_V1_SIGNATURE = '3ed6dde1ae10e353a9e62c86082ae62fb3c0ca987bc7ada4ded523a3adcf552b'
REGISTERED = {'mail_document_extraction', 'mail_extraction_repair', 'mail_single_extraction', 'trip_answer'}


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def ai_v1_signature():
    return stable_hash({'parser': 'foundation-v2', 'chunker': 'v1', 'extraction_model': EXTRACTION_MODEL,
                        'embedding_model': EMBEDDING_MODEL})


# ------------------------------------------------------------ 1. registry --
def test_registry_bodies_are_the_original_strings():
    import src.parser as parser
    import src.rag as rag
    assert load_prompt('mail_single_extraction').sha256 == sha(load_prompt('mail_single_extraction').body) == SINGLE_SHA256
    assert load_prompt('mail_document_extraction').sha256 == sha(load_prompt('mail_document_extraction').body) == DOCUMENT_SHA256
    assert sha(parser.SYSTEM_PROMPT) == SINGLE_SHA256 and parser.SYSTEM_PROMPT == load_prompt('mail_single_extraction').body
    assert sha(parser.DOCUMENT_SYSTEM_PROMPT) == DOCUMENT_SHA256
    assert parser.DOCUMENT_SYSTEM_PROMPT.endswith('\n')  # the original triple-quoted string ended with one
    assert not parser.SYSTEM_PROMPT.endswith('\n')  # the original had no trailing newline
    rendered = load_prompt('trip_answer').render(NO_INFO_MESSAGE=NO_INFO_MESSAGE)
    assert sha(rendered) == sha(rag.SYSTEM_PROMPT) == ANSWER_SHA256
    assert '{provider}' in rag.SYSTEM_PROMPT and '{{' not in rag.SYSTEM_PROMPT  # literal braces untouched
    assert handlers.LEGACY_EXTRACTION_PROMPT_SHA256 == DOCUMENT_SHA256


def test_registry_lists_every_prompt_with_metadata_only():
    listed = registry()
    assert REGISTERED <= set(listed)  # other stage-2 tracks may register more prompts
    for prompt_id, meta in listed.items():
        prompt = load_prompt(prompt_id)
        assert meta == {'id': prompt_id, 'version': prompt.version, 'hash': prompt.sha256,
                        'purpose': prompt.purpose, 'output_schema': prompt.output_schema}
        assert prompt.meta() == {'id': prompt_id, 'version': prompt.version, 'hash': prompt.sha256}
        assert prompt.version >= 1
    assert load_prompt('mail_extraction_repair').variables == ('ERRORS',)
    assert load_prompt('mail_extraction_repair').output_schema == 'document_reservations'
    assert load_prompt('trip_answer').variables == ('NO_INFO_MESSAGE',)
    assert load_prompt('mail_document_extraction').variables == ()


def test_render_requires_exactly_the_declared_variables():
    repair = load_prompt('mail_extraction_repair')
    text = repair.render(ERRORS='- reservations[0].time: HH:MM 형식이 필요합니다.')
    assert '- reservations[0].time' in text and '{{ERRORS}}' not in text
    for values in ({}, {'ERRORS': 'x', 'EXTRA': 'y'}, {'errors': 'x'}):
        with pytest.raises(ValueError):
            repair.render(**values)
    with pytest.raises(ValueError):
        load_prompt('trip_answer').render()


FRONT = '---\nid: sample_prompt\nversion: 1\npurpose: test\noutput_schema: text\nvariables: NAME\n---\nHello {{NAME}}\n'


def test_front_matter_is_validated():
    parsed = prompts._parse(FRONT, 'sample_prompt')
    assert parsed.body == 'Hello {{NAME}}\n' and parsed.variables == ('NAME',) and parsed.sha256 == sha('Hello {{NAME}}\n')
    broken = {
        'id mismatch': FRONT.replace('id: sample_prompt', 'id: other_prompt'),
        'declared but unused': FRONT.replace('variables: NAME', 'variables: NAME, OTHER'),
        'used but undeclared': FRONT.replace('variables: NAME', 'variables: '),
        'missing version': FRONT.replace('version: 1\n', ''),
        'no front matter': 'Hello {{NAME}}\n',
        'unclosed': FRONT.replace('\n---\nHello', '\nHello'),
        'unreadable line': FRONT.replace('purpose: test', 'purpose test'),
    }
    for label, text in broken.items():
        with pytest.raises(ValueError):
            prompts._parse(text, 'sample_prompt')
    with pytest.raises(ValueError):
        load_prompt('../etc')
    with pytest.raises(OSError):
        load_prompt('not_registered_prompt')


# ------------------------------------------------- 2. pipeline signature --
def test_local_signature_is_the_pre_registry_value():
    assert stable_hash({'parser': 'local-mail-v1', 'chunker': 'v1', 'extraction_model': None, 'embedding_model': None}) == LOCAL_V1_SIGNATURE
    assert document_signatures('local') == (LOCAL_V1_SIGNATURE, {LOCAL_V1_SIGNATURE})


def test_ai_signature_v2_accepts_v1_while_prompt_bytes_are_unchanged(monkeypatch):
    monkeypatch.delenv('MAIL_EXTRACTION_CHAIN', raising=False)
    monkeypatch.delenv('LLM_PAID_FALLBACK', raising=False)
    current, compatible = document_signatures('ai')
    base = {'parser': 'foundation-v2', 'chunker': 'v1', 'extraction_model': EXTRACTION_MODEL, 'embedding_model': EMBEDDING_MODEL}
    assert current == stable_hash({**base, 'signature_version': 2, 'extraction_chain': ['openai'],
                                   'prompts': [load_prompt('mail_document_extraction').meta(), load_prompt('mail_extraction_repair').meta()]})
    assert current != ai_v1_signature() and compatible == {current, ai_v1_signature()}
    assert document_signatures('ai') == (current, compatible)  # deterministic
    # v1 jobs always ran the single OpenAI extractor: any other chain is a new pipeline.
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'replay')
    replay_current, replay_compatible = document_signatures('ai')
    assert replay_current != current and replay_compatible == {replay_current}
    monkeypatch.setenv('MAIL_EXTRACTION_CHAIN', 'openai,anthropic')
    monkeypatch.setenv('LLM_PAID_FALLBACK', '1')
    paid_current, paid_compatible = document_signatures('ai')
    assert paid_current not in (current, replay_current) and paid_compatible == {paid_current}
    monkeypatch.delenv('LLM_PAID_FALLBACK')
    assert document_signatures('ai') == (current, compatible)  # tail dropped -> same chain as default


def test_edited_extraction_prompt_makes_v1_incompatible(monkeypatch):
    original = prompts.load_prompt

    def edited(prompt_id):
        prompt = original(prompt_id)
        return replace(prompt, sha256='0' * 64) if prompt_id == 'mail_document_extraction' else prompt
    monkeypatch.setattr(prompts, 'load_prompt', edited)
    current, compatible = document_signatures('ai')
    assert compatible == {current} and ai_v1_signature() not in compatible


def no_network_client():
    raise AssertionError('synthetic test: the OpenAI client must never be built')


def runtime(tmp_path, monkeypatch):
    """jobs.claim -> JobContext -> Operations with the real parser.

    Configuration is the default OpenAI chain (what every pre-registry v1 job
    ran), but run_v6() binds a replay adapter in the worker thread, so nothing
    leaves the process; building the OpenAI client fails the test.
    """
    from src.llm.replay import ReplayAdapter, ReplayStore
    from tests.test_reliability_runtime import construct, seed
    from tests.test_v6_llm_gateway import token_price
    replay_dir = tmp_path / 'replay-records'
    for variable in ('EMBEDDING_PROVIDER', 'ANSWER_PROVIDER', 'LLM_PAID_FALLBACK', 'MAIL_EXTRACTION_CHAIN'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr('src.config.OPENAI_API_KEY', 'synthetic-never-sent')
    monkeypatch.setattr('src.parser._client', no_network_client)
    env = construct(tmp_path, policy=policy_with({**replay_runtime_prices(), f'openai/{EXTRACTION_MODEL}': token_price()}))
    env.documents.parser = None
    env.documents.settings.mail_analysis_mode = 'auto'
    env.replay = ReplayAdapter(ReplayStore(replay_dir))
    raw = read_email_bytes(b'synthetic museum booking', 'synthetic.txt')
    put_record(replay_dir, extraction_kwargs(raw), document(booking('synthetic museum booking', provider='Synthetic Museum',
                                                                    confirmation_number='FAKE', date='2026-11-01', time='09:00')),
               source='handmade')
    trip, job_id, did = seed(env)
    return env, trip, job_id, did


def run_v6(env):
    from src.llm import adapter_scope
    from tests.test_reliability_runtime import run_one
    with adapter_scope(env.replay):
        return run_one(env)


def count_extractions(monkeypatch):
    """Calls of the real extractor (a receipt replay would hide a re-extraction)."""
    import src.parser as parser
    calls = []
    original = parser.extract_document_reservations

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(parser, 'extract_document_reservations', counted)
    return calls


def as_pre_registry(cp):
    """What the pre-registry code wrote: no prompt identity, requested_mode or extraction meta."""
    cp = json.loads(json.dumps(cp))
    cp['pipeline_signature'] = ai_v1_signature()
    cp.pop('requested_mode', None)
    for unit in cp.get('documents', {}).values():
        unit.pop('extraction', None)
    return cp


def checkpoint(db, job_id):
    with db.connect() as con:
        return json.loads(con.execute('SELECT checkpoint_json FROM jobs WHERE id=?', (job_id,)).fetchone()[0])


def replay_extractions(db):
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM usage_reservations WHERE provider='replay'").fetchone()[0]


@pytest.mark.parametrize('stored', ['v1', 'foreign'])
def test_in_flight_job_with_v1_checkpoint_resumes_without_reextraction(tmp_path, monkeypatch, stored):
    from tests.test_reliability_runtime import expire_leases
    env, trip, job_id, did = runtime(tmp_path, monkeypatch)
    extractions = count_extractions(monkeypatch)
    crashed = []

    def lose_lease(point, job):
        if point == 'after_extraction' and not crashed:
            crashed.append(point)
            raise DomainError('LEASE_LOST', 'synthetic lease loss', 409)
    env.operations.fault_hook = lose_lease
    with pytest.raises(DomainError):
        run_v6(env)
    cp = checkpoint(env.db, job_id)
    assert cp['pipeline_signature'] == document_signatures('ai')[0] and cp['requested_mode'] == 'auto'
    assert cp['documents'][did]['extracted_ref'] and replay_extractions(env.db) == 1 and extractions == [1]
    extracted_ref = cp['documents'][did]['extracted_ref']
    # Pretend this checkpoint was written by the pre-registry code (signature v1).
    cp = as_pre_registry(cp)
    if stored == 'foreign':
        cp['pipeline_signature'] = 'f' * 64
    with env.db.connect() as con:
        con.execute('UPDATE jobs SET checkpoint_json=? WHERE id=?', (json.dumps(cp), job_id))
    expire_leases(env)
    if stored == 'foreign':
        with pytest.raises(DomainError) as raised:
            run_v6(env)
        assert raised.value.code == 'CHECKPOINT_INCOMPATIBLE'
        return
    result = run_v6(env)
    assert result['state'] == 'succeeded', result
    assert extractions == [1]  # no second extraction after resume
    resumed = checkpoint(env.db, job_id)
    assert resumed['documents'][did]['extracted_ref'] == extracted_ref  # the same artifact was reused
    assert resumed['pipeline_signature'] == ai_v1_signature()
    # A pre-registry job has no requested_mode: it is treated (and stored) as 'ai'.
    assert resumed['requested_mode'] == 'ai'
    assert [b['provider'] for b in env.repo.list_bookings('A', trip) if b['document_id'] == did] == ['Synthetic Museum']


def test_pre_registry_job_without_requested_mode_never_falls_back_to_local(tmp_path, monkeypatch):
    """No requested_mode in a v1 checkpoint means 'ai': an unusable answer is
    EXTRACTION_INVALID, the basic parser is not run (conservative for billing)."""
    from src.foundation import local_mail
    from tests.test_v6_llm_gateway import repair_kwargs
    env, trip, job_id, did = runtime(tmp_path, monkeypatch)
    raw = read_email_bytes(b'synthetic museum booking', 'synthetic.txt')
    invalid = document(booking('A sentence the mail never contained.', provider='Synthetic Museum'))
    replay_dir = tmp_path / 'replay-records'
    put_record(replay_dir, extraction_kwargs(raw), invalid)
    put_record(replay_dir, repair_kwargs(raw, invalid), invalid)
    local_calls = []
    monkeypatch.setattr(local_mail, 'parse_local_document', lambda raw: local_calls.append(1))
    with env.db.connect() as con:
        con.execute('UPDATE jobs SET checkpoint_json=? WHERE id=?',
                    (json.dumps({'pipeline_signature': ai_v1_signature(), 'analysis_mode': 'ai'}), job_id))
    result = run_v6(env)
    item = result['result']['files'][0]
    assert result['state'] == 'failed' and item['error_code'] == 'EXTRACTION_INVALID', result
    assert item['extraction']['fallback_path'] == ['ai', 'repair'] and item['extraction']['repair_attempts'] == 1
    assert local_calls == []
    stored = checkpoint(env.db, job_id)
    assert stored['requested_mode'] == 'ai' and stored['pipeline_signature'] == ai_v1_signature()
    assert [b['provider'] for b in env.repo.list_bookings('A', trip)] == ['Manual dinner']


@pytest.mark.parametrize('stored,extractions', [('v1', 1), ('foreign', 2)])
def test_retry_resume_from_reuses_extraction_of_a_v1_job(service, monkeypatch, tmp_path, stored, extractions):
    env = ai_replay(service, monkeypatch, tmp_path, name='resume-' + stored)
    raw = read_email_bytes(DINNER_BYTES, 'dinner.txt')
    put_record(env.replay_dir, extraction_kwargs(raw), DINNER_GOOD, source='handmade')

    def interrupt(point, job):
        if point == 'after_ready':
            raise RuntimeError('synthetic activation interruption')
    service.app.state.operations.fault_hook = interrupt
    failed = upload(env, 'dinner.txt', DINNER_BYTES, 'resume-' + stored)
    assert failed['state'] == 'failed', failed
    job_id = failed['id'] if 'id' in failed else failed['job_id']
    cp = checkpoint(service.app.state.db, job_id)
    assert cp['pipeline_signature'] == document_signatures('ai')[0]
    cp = as_pre_registry(cp)
    if stored == 'foreign':
        cp['pipeline_signature'] = 'f' * 64
    with service.app.state.db.connect() as con:
        con.execute('UPDATE jobs SET checkpoint_json=? WHERE id=?', (json.dumps(cp), job_id))
    service.app.state.operations.fault_hook = None
    if stored == 'v1':
        # A v1 job ran the default OpenAI chain; resume it under that chain. The
        # extraction is reused, so the (guarded) OpenAI client is never built.
        from tests.test_v6_llm_gateway import token_price
        monkeypatch.delenv('MAIL_EXTRACTION_CHAIN')
        monkeypatch.setattr('src.parser._client', no_network_client)
        service.app.state.budget.policy = policy_with({**replay_runtime_prices(), f'openai/{EXTRACTION_MODEL}': token_price()})
    extraction_calls = count_extractions(monkeypatch)
    embeds_before = [call[0] for call in service.calls].count('embed')
    retry = env.client.post('/api/v2/jobs/' + job_id + '/retry', headers={'Idempotency-Key': uuid4().hex})
    assert retry.status_code == 202, retry.text
    resumed = _job(env.client, retry.json())
    assert resumed['state'] == 'succeeded', resumed
    assert len([row for row in ledger(service) if row['provider'] == 'replay']) == extractions
    assert len(extraction_calls) == extractions - 1  # calls made by the retry job itself
    if stored == 'v1':
        assert [call[0] for call in service.calls].count('embed') == embeds_before  # vectors_ref reused too
    rows = env.client.get(env.base + '/bookings').json()['items']
    assert [r['confirmation_number'] for r in rows] == ['SYN-DIN-7']
