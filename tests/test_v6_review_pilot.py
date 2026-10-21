"""Bundle E: review pilot plan, blind language labels, Wilson intervals, consumer reviews OFF.

All rows are synthetic sentences written for these tests. No network, no provider run.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts.label_review_language import ScriptedKeys, label
from scripts.plan_v5_review_pilot import plan
from src.research import language_labels as L
from src.research.language import wilson95

ROOT = Path(__file__).resolve().parents[1]

def review_text(lang, tag):
    """Two unique sentences per review plus one repeated sentence (dropped as a duplicate)."""
    return {
        'ja': f'駅前の小さな店{tag}で昼にラーメンを食べました。スープ{tag}がとても濃くて満足です！また来ます。',
        'ko': f'점심에 라멘{tag}을 먹었는데 국물이 진했어요. 직원분들{tag}이 친절했습니다! 또 올게요.',
        'en': f'We had lunch at shop {tag} and the broth was rich. Service at {tag} was quick and kind. We will come back.',
    }[lang]


def source(places=12, per_place=2):
    """Synthetic export rows with the extra fields a real export may carry."""
    rows = []
    for p in range(places):
        for r in range(per_place):
            lang = ('ja', 'ko', 'en')[(p + r) % 3]
            rows.append({'placeId': f'synthetic-place-{p}', 'text': review_text(lang, f'{p}-{r}'), 'stars': 5,
                         'name': 'PRIVATE AUTHOR', 'reviewerUrl': 'https://example.invalid/u/1',
                         'originalLanguage': 'ja', 'textTranslated': 'translated body', 'reviewId': f'rid-{p}-{r}'})
    rows.append({'placeId': 'synthetic-place-0', 'stars': 4})  # rating only
    rows.append({'text': 'no place here at all, four words'})  # no place
    return rows


def frozen(**options):
    return L.build_set(source(), 'tokyo', synthetic=True, created_at='2026-10-21T00:00:00+00:00', **options)


class Exploding:
    version = 'never'

    def detect(self, text, provider_language=None):  # pragma: no cover - must not run while labeling
        raise AssertionError('detector must not run during labeling')


class FakeDetector:
    """Deterministic stand-in: answers by script, abstains on Latin text."""
    version = 'fake-detector-v1'

    def detect(self, text, provider_language=None):
        if any('가' <= ch <= '힣' for ch in text):
            return {'language': 'ko'}
        if any('぀' <= ch <= 'ヿ' or '一' <= ch <= '鿿' for ch in text):
            return {'language': 'ja'}
        return {'language': None}


def truth(text):
    """The test author's own answer key for the synthetic sentences (not a model output)."""
    if any('가' <= ch <= '힣' for ch in text):
        return 'k'
    if any('぀' <= ch <= 'ヿ' or '一' <= ch <= '鿿' for ch in text):
        return 'j'
    return 'e'


def keys_for(doc, splits=('heldout',), translation='1'):
    keys = []
    for review in sorted((r for r in doc['reviews'] if r['split'] in splits), key=lambda r: r['id']):
        keys.append(translation)
        keys += [truth(doc['rows'][sid]['text']) for sid in review['row_ids']]
    return keys


# ---------------------------------------------------------------- frozen set

def test_set_keeps_only_text_and_pseudonymous_place():
    doc = frozen()
    dumped = json.dumps(doc, ensure_ascii=False)
    for secret in ('PRIVATE AUTHOR', 'example.invalid', 'synthetic-place-', 'rid-', 'translated body', 'originalLanguage', 'stars'):
        assert secret not in dumped
    assert doc['counts']['no_text'] == 1 and doc['counts']['no_place'] == 1
    for review in doc['reviews']:
        assert set(review) == {'id', 'place_key', 'split', 'row_ids'}
    for row in doc['rows'].values():
        assert set(row) == {'review_id', 'text'}


def test_sentence_split_and_duplicate_rows_dropped():
    assert L.split_sentences('一つ目。二つ目！三つ目？') == ['一つ目。', '二つ目！', '三つ目？']
    assert L.split_sentences('Muy bueno. ¡Volveremos!\nGràcies') == ['Muy bueno.', '¡Volveremos!', 'Gràcies']
    doc = frozen()
    texts = [' '.join(r['text'].split()).casefold() for r in doc['rows'].values()]
    assert len(texts) == len(set(texts))
    assert doc['counts']['duplicate_rows_dropped'] > 0  # repeated sentences across synthetic reviews
    review_unit = frozen(unit='review')
    assert all(len(r['row_ids']) == 1 for r in review_unit['reviews'])


def test_splits_are_place_disjoint_seeded_and_capped():
    a, b = frozen(), frozen()
    assert a['set_hash'] == b['set_hash'] and a['reviews'] == b['reviews']
    dev = {r['place_key'] for r in a['reviews'] if r['split'] == 'development'}
    held = {r['place_key'] for r in a['reviews'] if r['split'] == 'heldout'}
    assert dev and held and not dev & held
    capped = frozen(max_rows=5)
    assert capped['counts']['rows'] == 5 and capped['counts']['rows_over_cap'] > 0
    with pytest.raises(ValueError):
        L.build_set(source(), 'seoul')


def test_edited_set_is_refused():
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    sid = next(iter(doc['rows']))
    doc['rows'][sid]['text'] = 'changed after freezing'
    with pytest.raises(ValueError, match='set_hash'):
        L.check_labels(doc, labels)
    moved = frozen()
    dev = next(r for r in moved['reviews'] if r['split'] == 'development')
    held = next(r for r in moved['reviews'] if r['split'] == 'heldout')
    dev['place_key'] = held['place_key']
    moved['set_hash'] = L.set_hash(moved)
    with pytest.raises(ValueError, match='both development and heldout'):
        L.verify_set(moved)


def test_raw_text_files_only_under_data_or_outside_repo(tmp_path):
    doc = frozen()
    for bad in (ROOT / 'evals' / 'review_pilot' / 'set.json', ROOT / 'docs' / 'set.json', ROOT / 'set.json'):
        with pytest.raises(ValueError, match='data/'):
            L.write_private(bad, doc)
        assert not bad.exists()
    L.ensure_private_path(ROOT / 'data' / 'review-language-labels' / 'set.json')
    out = tmp_path / 'set.json'
    L.write_private(out, doc)
    assert oct(out.stat().st_mode & 0o777) == '0o600'


# ---------------------------------------------------------------- blind screen and keys

def test_blind_screen_shows_text_only():
    doc = frozen()
    review = doc['reviews'][0]
    row = L.blind_row(doc, review, 0)
    assert set(row) == {'row_id', 'text', 'position', 'of'}
    assert not set(row) & L.HIDDEN_KEYS
    screen = L.render_review(doc, review, 1, 1) + L.render_row(row)
    for leak in ('ja', 'detector', 'confidence', 'p0', 'tokyo', '★'):
        assert leak not in screen.replace(doc['rows'][review['row_ids'][0]]['text'], '')


def test_label_loop_output_is_blind(tmp_path):
    """Every line the real loop prints, minus the review text itself, carries no hidden field."""
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    out = []
    label(doc, labels, tmp_path / 'l.json', ScriptedKeys(keys_for(doc)), out.append, splits=('heldout',))
    texts = sorted((row['text'] for row in doc['rows'].values()), key=len, reverse=True)
    assert len(out) > 10
    forbidden = ('ja', 'ko', 'detector', 'confidence', 'predicted', 'place', 'p0', 'tokyo', '도쿄', '★', 'stars',
                 'synthetic-place', 'PRIVATE AUTHOR', 'originalLanguage')
    for line in out:
        for text in texts:
            line = line.replace(text, '')
        for word in forbidden:
            assert word not in line, (word, line)


def test_labels_come_only_from_keys_and_save_every_answer(tmp_path):
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    assert labels['rows'] == {} and labels['reviews'] == {}  # nothing prefilled
    path = tmp_path / 'labels.json'
    first = sorted((r for r in doc['reviews'] if r['split'] == 'heldout'), key=lambda r: r['id'])[0]
    keys = ['2', 'k', 'q']
    out = []
    assert label(doc, labels, path, ScriptedKeys(keys), out.append, splits=('heldout',)) == 'quit'
    saved = json.loads(path.read_text())
    assert saved['reviews'][first['id']]['translation'] == 'translated'
    assert saved['rows'][first['row_ids'][0]]['expected'] == 'ko'
    assert all('text' not in row for row in saved['rows'].values())
    assert 'text' not in json.dumps(saved['reviews'])
    assert any('남은 예상' in line for line in out)


def test_other_code_skip_back_and_resume(tmp_path):
    doc = frozen()
    path = tmp_path / 'labels.json'
    labels = L.new_labels(doc, 'R1')
    first = sorted((r for r in doc['reviews'] if r['split'] == 'heldout'), key=lambda r: r['id'])[0]
    ids = first['row_ids']
    assert len(ids) >= 2
    keys = ['1', 'o', 'FRENCH!', 'o', 'fr', 'b', 'j'] + ['-'] * (len(ids) - 1) + ['q']
    label(doc, labels, path, ScriptedKeys(keys), lambda _: None, splits=('heldout',))
    assert labels['rows'][ids[0]]['expected'] == 'ja'  # 'b' went back and 'j' replaced 'fr'
    assert labels['rows'][ids[1]]['skipped'] is True and L.review_status(labels, first) == 'partial'
    label(doc, labels, path, ScriptedKeys(['k', 'q']), lambda _: None, splits=('heldout',))
    assert labels['rows'][ids[1]]['expected'] == 'ko'  # skipped sentence shown again on resume


def test_ctrl_c_saves(tmp_path):
    doc = frozen()
    path = tmp_path / 'labels.json'
    labels = L.new_labels(doc, 'R1')

    class Interrupt(ScriptedKeys):
        def key(self):
            if not self.keys:
                raise KeyboardInterrupt
            return super().key()

    assert label(doc, labels, path, Interrupt(['1', 'j']), lambda _: None, splits=('heldout',)) == 'quit'
    assert len(json.loads(path.read_text())['rows']) == 1


def test_labeling_never_runs_the_detector_and_reviewer_is_pseudonymous(tmp_path, monkeypatch):
    import src.research.evaluation as evaluation
    import src.research.language as language
    calls = []

    class Counting(Exploding):
        def __init__(self, *args, **kwargs):
            calls.append('constructed')

        def detect(self, text, provider_language=None):
            calls.append('detect')
            raise AssertionError('detector must not run during labeling')

    def engine():
        calls.append('engine')
        raise AssertionError('lingua engine must not load during labeling')

    # evaluation.py binds its own name via from-import, so patch every handle.
    monkeypatch.setattr(language, 'LocalLanguageDetector', Counting)
    monkeypatch.setattr(evaluation, 'LocalLanguageDetector', Counting)
    monkeypatch.setattr(language, '_engine', engine)
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    keys = keys_for(doc)
    assert label(doc, labels, tmp_path / 'l.json', ScriptedKeys(keys), lambda _: None, splits=('heldout',)) == 'done'
    assert L.progress(doc, labels, ('heldout',))['rows_remaining'] == 0
    assert calls == []
    with pytest.raises(ValueError):
        L.new_labels(doc, 'person@example.com')


# ---------------------------------------------------------------- evaluation and Wilson intervals

def test_wilson_reference_values():
    low, high = wilson95(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4) and high == pytest.approx(0.9433, abs=1e-4)
    assert wilson95(0, 0) is None


def test_evaluation_uses_typed_answers_and_reports_intervals(tmp_path):
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    label(doc, labels, tmp_path / 'l.json', ScriptedKeys(keys_for(doc)), lambda _: None, splits=('heldout',))
    payload, counts = L.evaluation_payload(doc, labels)
    assert payload['development'] == [] and counts['heldout_labeled_rows'] == len(payload['heldout'])
    heldout_reviews = [r for r in doc['reviews'] if r['split'] == 'heldout']
    # one translation check per review, not per sentence
    assert sum(row['original_checked'] for row in payload['heldout']) == len(heldout_reviews)
    assert not any(row['original_mismatch'] for row in payload['heldout'])
    report = L.evaluate(doc, labels, detector=FakeDetector())
    held = payload['heldout']
    ja_truth = sum(r['expected'] == 'ja' for r in held)
    ko_truth = sum(r['expected'] == 'ko' for r in held)
    # Fake detector is right on ja/ko and abstains on English: local precision ja/ja, Korean recall ko/ko.
    assert report['intervals']['local_precision_interval95'] == wilson95(ja_truth, ja_truth)
    assert report['intervals']['korean_recall_interval95'] == wilson95(ko_truth, ko_truth)
    assert report['intervals']['korean_support'] == ko_truth
    assert report['support_sufficient'] is False  # far below 100 / 50 / 50 / 50
    assert report['production_strict_gate_supported'] is False and report['synthetic'] is True
    assert report['original_checks'] == len(heldout_reviews)
    assert report['labeling_gate_blockers'] == [L.SENTENCE_UNIT_BLOCK, L.NON_INTERACTIVE_BLOCK]
    assert report['labeling']['label_source'] == 'synthetic_fixture' and report['labeling']['reviewer_count'] == 1
    assert report['runs_started'] == 0 and report['product_controls_changed'] is False
    dumped = json.dumps(report, ensure_ascii=False)
    assert not any(row['text'] in dumped for row in held)


def test_korean_recall_null_without_korean_truth_and_translation_flags(tmp_path):
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    keys = [k if k in ('1', '2', '0') else 'j' for k in keys_for(doc, translation='2')]
    label(doc, labels, tmp_path / 'l.json', ScriptedKeys(keys), lambda _: None, splits=('heldout',))
    payload, _ = L.evaluation_payload(doc, labels)
    heldout_reviews = [r for r in doc['reviews'] if r['split'] == 'heldout']
    assert sum(row['original_mismatch'] for row in payload['heldout']) == len(heldout_reviews)
    report = L.evaluate(doc, labels, detector=FakeDetector())
    assert report['korean_recall'] is None and report['intervals']['korean_recall_interval95'] is None
    assert report['original_translation_errors'] == len(heldout_reviews)


def test_unlabeled_rows_are_not_filled():
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    with pytest.raises(ValueError, match='No labeled heldout rows'):
        L.evaluation_payload(doc, labels)
    review = next(r for r in doc['reviews'] if r['split'] == 'heldout')
    L.record_translation(labels, review['id'], 'unchecked')
    L.record_language(labels, review['row_ids'][0], 'ja')
    payload, counts = L.evaluation_payload(doc, labels)
    assert len(payload['heldout']) == 1 and counts['unlabeled_rows'] == len(doc['rows']) - 1
    assert payload['heldout'][0]['original_checked'] is False
    with pytest.raises(ValueError):
        L.record_language(labels, review['row_ids'][0], 'Japanese')


def test_progress_and_estimate():
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    state = L.progress(doc, labels, ('heldout',))
    rows = doc['counts']['heldout_rows']
    assert state['rows_total'] == rows and state['rows_done'] == 0
    assert state['remaining_seconds'][0] == rows * 4 + state['translation_checks_remaining'] * 5
    assert '[추정]' in L.estimate(doc)['text']


# ---------------------------------------------------------------- pilot plan and consumer reviews OFF

def account(**changes):
    return {'plan': 'FREE', 'monthly_credits_usd': 5, 'monthly_limit_usd': 5, 'used_credits_usd': 4.7,
            'checked_at': datetime.now(timezone.utc).isoformat(), 'account_active_runs': 0, 'shared_account_check': True,
            'event_prices_usd': {'review-scraped': .0006}, **changes}


def test_pilot_plan_adds_language_section_and_never_starts_runs():
    result = plan(account(), {}, '0', '0')
    assert result['status'] == 'blocked' and 'NO_EXISTING_APPROVED_CONFIGURED_BUDGET' in result['reason_codes']
    assert 'DATA_RIGHTS_POLICY_MISSING' in result['reason_codes']
    assert result['runs_started'] == 0 and result['production_enabled'] is False
    section = result['language_labeling']
    assert section['labels_started'] == 0 and section['policy_minimums']['min_labels'] == 100
    stages = section['stages']
    assert [s['max_review_records_total'] for s in stages] == [40, 600, 2000]
    # one city, heldout places only: 1 -> 1, 3 -> round(2.1)=2, (10 - 1 other-city place) -> round(6.3)=6
    assert [s['max_heldout_places_one_city'] for s in stages] == [1, 2, 6]
    assert [s['max_heldout_review_records_one_city'] for s in stages] == [40, 400, 1200]
    assert [s['could_reach_min_labels_review_unit_upper_bound'] for s in stages] == [False, True, True]
    assert all(s['max_heldout_rows_sentence_unit'] is None for s in stages)  # unknown, not a bound
    assert all(s['expected_korean_truth'] is None for s in stages)
    assert section['seconds_per_row_assumed'] == list(L.SECONDS_PER_ROW)  # kept in sync without an import
    assert section['heldout_share_assumed'] == L.DEFAULT_HELDOUT_SHARE
    assert 'separate from PRD decision 5' in section['labeling_time_budget']


def test_plan_cli_runs_offline(tmp_path):
    (tmp_path / 'pre.json').write_text(json.dumps(account()))
    (tmp_path / 'cov.json').write_text('{}')
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1'}
    done = subprocess.run([sys.executable, 'scripts/plan_v5_review_pilot.py', '--preflight', str(tmp_path / 'pre.json'),
                           '--coverage', str(tmp_path / 'cov.json'), '--output', str(tmp_path / 'plan.json')],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)['runs_started'] == 0
    assert json.loads((tmp_path / 'plan.json').read_text())['language_labeling']['labels_started'] == 0


def test_consumer_reviews_stay_off_by_default(monkeypatch):
    from src.research.schema import SCHEMA
    con = sqlite3.connect(':memory:')
    con.executescript(SCHEMA)
    assert tuple(con.execute('SELECT research_enabled, production_enabled FROM review_controls')) == ((0, 0),)
    monkeypatch.delenv('BOUNDED_REVIEW_ENABLED', raising=False)
    from src.foundation.settings import Settings
    assert Settings().bounded_review_enabled is False
    for path in ('src/research/language_labels.py', 'scripts/label_review_language.py', 'scripts/plan_v5_review_pilot.py'):
        text = (ROOT / path).read_text()
        assert 'review_controls' not in text and 'set_controls' not in text
        assert 'httpx' not in text and 'apify.com' not in text


def test_cli_end_to_end_with_piped_keys(tmp_path):
    src = tmp_path / 'export.jsonl'
    src.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in source()))
    env = {**os.environ, 'PYTHON_DOTENV_DISABLED': '1'}
    run = lambda *args, stdin='': subprocess.run([sys.executable, 'scripts/label_review_language.py', *args], cwd=ROOT,
                                                 env=env, input=stdin, capture_output=True, text=True, timeout=120)
    prepared = run('prepare', '--source', str(src), '--city', 'tokyo', '--output', str(tmp_path / 'set.json'), '--synthetic')
    assert prepared.returncode == 0, prepared.stderr
    doc = json.loads((tmp_path / 'set.json').read_text())
    labelled = run('label', '--set', str(tmp_path / 'set.json'), '--labels', str(tmp_path / 'labels.json'), '--reviewer-id', 'R1',
                   stdin='\n'.join(keys_for(doc)) + '\n')
    assert labelled.returncode == 0, labelled.stderr
    progress = run('progress', '--set', str(tmp_path / 'set.json'), '--labels', str(tmp_path / 'labels.json'), '--reviewer-id', 'R1')
    assert json.loads(progress.stdout)['rows_remaining'] == 0
    refused = run('prepare', '--source', str(src), '--city', 'tokyo', '--output', str(ROOT / 'evals' / 'x.json'))
    assert refused.returncode != 0 and not (ROOT / 'evals' / 'x.json').exists()


# ---------------------------------------------------------------- fixes after bundle E review

class WrongSometimes:
    """Deliberately imperfect stand-in so the interval denominators differ from the truth counts."""
    version = 'fake-wrong-v1'

    def detect(self, text, provider_language=None):
        if text.startswith('Service at'):
            return {'language': 'ja'}  # English truth predicted local -> local false positive
        if text.startswith('직원분들'):
            return {'language': None}  # Korean truth abstained -> Korean false negative
        return FakeDetector().detect(text)


def test_intervals_use_fp_and_fn_denominators(tmp_path):
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    label(doc, labels, tmp_path / 'l.json', ScriptedKeys(keys_for(doc)), lambda _: None, splits=('heldout',))
    payload, _ = L.evaluation_payload(doc, labels)
    held = payload['heldout']
    ja = sum(r['expected'] == 'ja' for r in held)
    ko = sum(r['expected'] == 'ko' for r in held)
    en_fp = sum(r['expected'] == 'en' and r['text'].startswith('Service at') for r in held)
    ko_fn = sum(r['expected'] == 'ko' and r['text'].startswith('직원분들') for r in held)
    assert en_fp and ko_fn  # the fixture really contains both kinds of error
    report = L.evaluate(doc, labels, detector=WrongSometimes())
    assert report['local_fp'] == en_fp and report['local_prediction_support'] == ja + en_fp
    assert report['intervals']['local_precision_interval95'] == wilson95(ja, ja + en_fp)
    assert report['intervals']['korean_recall_interval95'] == wilson95(ko - ko_fn, ko)
    assert report['korean_fn'] == ko_fn


def long_review_source(sentences=25):
    body = ' '.join(f'Sentence number {i} about the noodle broth here.' for i in range(sentences))
    return [{'placeId': 'one', 'text': body}]


def test_one_translation_key_counts_as_one_original_check(tmp_path):
    doc = L.build_set(long_review_source(), 'tokyo', synthetic=True)
    (review,) = doc['reviews']
    assert len(review['row_ids']) == 25
    labels = L.new_labels(doc, 'R1')
    label(doc, labels, tmp_path / 'l.json', ScriptedKeys(['1'] + ['e'] * 25), lambda _: None, splits=('heldout',))
    report = L.evaluate(doc, labels, detector=FakeDetector())
    assert report['original_checks'] == 1  # was 25 before the fix
    assert report['labeling']['original_checked_reviews_heldout'] == 1
    assert report['labeling']['original_checks_unit'] == 'review'


def test_sentence_unit_and_non_terminal_input_block_the_gate(tmp_path):
    review_doc = L.build_set(source(), 'tokyo', unit='review', synthetic=True)
    labels = L.new_labels(review_doc, 'R1')
    for review in review_doc['reviews']:
        if review['split'] != 'heldout':
            continue
        L.record_translation(labels, review['id'], 'original', input_mode='terminal')
        for sid in review['row_ids']:
            L.record_language(labels, sid, {'j': 'ja', 'k': 'ko', 'e': 'en'}[truth(review_doc['rows'][sid]['text'])],
                              input_mode='terminal')
    report = L.evaluate(review_doc, labels, detector=FakeDetector())
    assert report['labeling_gate_blockers'] == []  # review unit, terminal input
    assert report['production_strict_gate_supported'] is False  # still synthetic and far below the minimums
    sentence_doc = frozen()
    sentence_labels = L.new_labels(sentence_doc, 'R1')
    label(sentence_doc, sentence_labels, tmp_path / 's.json', ScriptedKeys(keys_for(sentence_doc)), lambda _: None,
          splits=('heldout',), input_mode='terminal')
    sentence_report = L.evaluate(sentence_doc, sentence_labels, detector=FakeDetector())
    assert sentence_report['labeling_gate_blockers'] == [L.SENTENCE_UNIT_BLOCK]
    piped = L.new_labels(sentence_doc, 'R1')
    label(sentence_doc, piped, tmp_path / 'p.json', ScriptedKeys(keys_for(sentence_doc)), lambda _: None,
          splits=('heldout',), input_mode='piped')
    assert all(row['input'] == 'piped' for row in piped['rows'].values())
    assert L.NON_INTERACTIVE_BLOCK in L.evaluate(sentence_doc, piped, detector=FakeDetector())['labeling_gate_blockers']


def test_other_codes_are_normalized_or_refused(tmp_path):
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    sid = next(iter(doc['rows']))
    for typed, stored in (('kor', 'ko'), ('jpn', 'ja'), ('spa', 'es'), ('cat', 'ca'), ('eng', 'en'), ('fr', 'fr'),
                          ('und', 'und'), ('mul', 'mul'), ('zxx', 'zxx')):
        assert L.record_language(labels, sid, typed) == stored
        assert labels['rows'][sid]['expected'] == stored
    for bad in ('fra', 'zho', 'deu', 'k', 'Japanese', 'ko-KR'):
        with pytest.raises(ValueError):
            L.record_language(labels, sid, bad)
    labels['rows'][sid]['expected'] = 'kor'  # a hand-edited file is refused too
    with pytest.raises(ValueError, match='ISO 639-1'):
        L.check_labels(doc, labels)
    # through the 'o' key: refused code asks again, alias is stored normalized
    fresh = L.new_labels(doc, 'R1')
    first = sorted((r for r in doc['reviews'] if r['split'] == 'heldout'), key=lambda r: r['id'])[0]
    out = []
    label(doc, fresh, tmp_path / 'l.json', ScriptedKeys(['1', 'o', 'fra', 'o', 'kor', 'q']), out.append, splits=('heldout',))
    assert fresh['rows'][first['row_ids'][0]]['expected'] == 'ko'
    assert any('kor → ko' in line for line in out)


def test_und_mul_zxx_stay_out_of_metric_denominators(tmp_path):
    doc = frozen()
    labels = L.new_labels(doc, 'R1')
    heldout = sorted((r for r in doc['reviews'] if r['split'] == 'heldout'), key=lambda r: r['id'])
    special = {}
    for review, code in zip(heldout, ('und', 'mul', 'zxx')):
        L.record_translation(labels, review['id'], 'original')
        for sid in review['row_ids']:
            L.record_language(labels, sid, code)
            special[sid] = code
    rest = heldout[3]
    L.record_translation(labels, rest['id'], 'original')
    for sid in rest['row_ids']:
        L.record_language(labels, sid, {'j': 'ja', 'k': 'ko', 'e': 'en'}[truth(doc['rows'][sid]['text'])])

    class AlwaysJa:
        version = 'always-ja'

        def detect(self, text, provider_language=None):
            return {'language': 'ja'}

    report = L.evaluate(doc, labels, detector=AlwaysJa())
    assert report['count'] == len(rest['row_ids'])  # only the real-language rows are metric rows
    excluded = report['labeling']['excluded_from_metrics']
    assert excluded == {code: sum(c == code for c in special.values()) for code in ('und', 'mul', 'zxx')}
    mul = report['labeling']['mul_diagnostic']
    assert mul['rows'] == excluded['mul'] and mul['predicted_local'] == excluded['mul']
    assert report['local_fp'] == sum(truth(doc['rows'][sid]['text']) != 'j' for sid in rest['row_ids'])
    # translation checks of reviews with only excluded rows are reported but not counted as metric checks
    assert report['labeling']['original_checked_reviews_heldout'] == 4 and report['original_checks'] == 1


def test_resume_keeps_skipped_flag_when_quitting_immediately(tmp_path):
    doc = frozen()
    path = tmp_path / 'labels.json'
    labels = L.new_labels(doc, 'R1')
    first = sorted((r for r in doc['reviews'] if r['split'] == 'heldout'), key=lambda r: r['id'])[0]
    ids = first['row_ids']
    label(doc, labels, path, ScriptedKeys(['1', 'j', '-', 'q']), lambda _: None, splits=('heldout',))
    assert labels['rows'][ids[1]]['skipped'] is True
    labels = json.loads(path.read_text())
    out = []
    label(doc, labels, path, ScriptedKeys(['q']), out.append, splits=('heldout',))
    saved = json.loads(path.read_text())
    assert saved['rows'][ids[1]]['skipped'] is True
    assert any(doc['rows'][ids[1]]['text'] in line for line in out)  # the skipped sentence was asked again
    _, counts = L.evaluation_payload(doc, saved)
    assert counts['skipped_rows'] == 1


def test_unsupported_source_shapes_and_empty_sets_are_refused():
    with pytest.raises(ValueError, match='UNSUPPORTED_SOURCE_SHAPE'):
        L.load_source(json.dumps({'data': [{'text': 'x', 'placeId': 'p'}]}))
    assert L.load_source(json.dumps({'items': [{'a': 1}]})) == [{'a': 1}]
    with pytest.raises(ValueError, match='NO_LABELABLE_ROWS'):
        L.build_set([{'body_text': 'wrong field name for text', 'placeId': 'p'}], 'tokyo')
    with pytest.raises(ValueError, match='NO_LABELABLE_ROWS'):
        L.build_set([], 'tokyo')
