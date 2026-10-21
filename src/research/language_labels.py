"""Blind sentence-level review-language labeling (operator only, local files, no network).

This module never produces a language answer. A person types every `expected`
language and every translation judgment; the detector is called only when a
finished label file is evaluated (`evaluation_payload` -> `evaluate_dataset`).

Raw review text exists only in the frozen label *set* file, which must live
under the repository's gitignored `data/` directory or outside the repository.
Label files hold row ids and the typed answers only (no text, no per-row text hash; only
the whole-set identifier set_hash, which is computed over the frozen rows).

Flow (same shape as the recommendation labeling tool, bundle B):
  build_set(source rows)  -> frozen set (seeded order, place-disjoint splits)
  new_labels / record_*   -> labels typed by a person, saved per keystroke
  evaluation_payload      -> src.research.evaluation.evaluate_dataset input
  evaluate                -> Wilson 95% intervals for local precision / Korean recall
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import unicodedata

from src.recommendations.labeling import REVIEWER_PATTERN, format_duration
from src.research.language import base_language
from src.research.profiles import QUALITY_POLICY, city_profile

SET_KIND = 'review_language_label_set'
LABEL_KIND = 'review_language_labels'
SCHEMA_VERSION = 1
SEED = 20261021
UNITS = ('sentence', 'review')
SPLITS = ('development', 'heldout')
DEFAULT_HELDOUT_SHARE = 0.7
DEFAULT_MAX_ROWS = 2000
MAX_TEXT = 20000  # evaluate_dataset rejects longer rows
# [추정] seconds per typed answer; replaced by measured time after 10 timed rows.
SECONDS_PER_ROW = (4, 8)
SECONDS_PER_REVIEW_CHECK = (5, 10)

# Single-key language answers. Codes follow ISO 639-1, plus ISO 639-2 special codes:
# und = cannot decide, mul = several languages in one row, zxx = no linguistic content.
LANGUAGE_KEYS = {'j': 'ja', 'k': 'ko', 'e': 'en', 's': 'es', 'c': 'ca', 'h': 'zh',
                 'u': 'und', 'm': 'mul', 'x': 'zxx'}
LANGUAGE_NAMES = {'ja': '일본어', 'ko': '한국어', 'en': '영어', 'es': '스페인어', 'ca': '카탈루냐어', 'zh': '중국어',
                  'und': '판단 불가', 'mul': '여러 언어 섞임', 'zxx': '언어 내용 없음(이모지·숫자만)'}
OTHER_KEY = 'o'  # then type a 2-letter ISO 639-1 code
CODE_PATTERN = re.compile(r'^[a-z]{2,3}$')
# Stored answers: ISO 639-1 (2 letters, what the detector predicts) or one of the special codes.
SPECIAL_CODES = frozenset({'und', 'mul', 'zxx'})
# Rows with these answers are kept in the label file but left out of the metric denominators.
# und/zxx have no language to predict; mul has no single correct answer (policy, LABELING.md).
EXCLUDED_FROM_METRICS = ('und', 'mul', 'zxx')
# Translation judgment per review (the text shown is the provider's "original" field).
TRANSLATION_KEYS = {'1': 'original', '2': 'translated', '0': 'unchecked'}
TRANSLATION_NAMES = {'original': '원문으로 보임', 'translated': '번역문으로 보임', 'unchecked': '확인 안 함'}

SOURCE_TEXT_FIELDS = ('text', 'original_text', 'body')
SOURCE_PLACE_FIELDS = ('place_id', 'placeId', 'canonical_place_id')
# Keys that must never reach the blind screen or the frozen set.
HIDDEN_KEYS = frozenset({'language', 'predicted', 'detector', 'detector_version', 'model_confidence',
    'provider_language', 'originalLanguage', 'translatedLanguage', 'textTranslated', 'stars', 'rating',
    'name', 'author', 'reviewer', 'reviewerId', 'reviewerUrl', 'reviewId', 'review_id', 'url', 'publishedAtDate',
    'place_name', 'title', 'city'})

_SENTENCE_BREAK = re.compile(r'(?<=[.!?…])\s+|(?<=[。！？])|\n+')
REPO_ROOT = Path(__file__).resolve().parents[2]


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def ensure_private_path(path):
    """Raw-text files may live only under <repo>/data/ (gitignored) or outside the repository."""
    resolved = Path(path).resolve()
    data = (REPO_ROOT / 'data').resolve()
    inside_repo = resolved == REPO_ROOT or REPO_ROOT in resolved.parents
    if inside_repo and data not in resolved.parents:
        raise ValueError('Review text files must be under data/ (gitignored) or outside the repository')
    return resolved


def write_private(path, value):
    """Atomic write with 0600 permissions (the set file contains raw review text)."""
    path = ensure_private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    os.replace(temp, path)
    os.chmod(path, 0o600)


# ---------------------------------------------------------------- source -> frozen set

def load_source(raw):
    """JSON array, {"items": [...]}, or JSON Lines. Field names of the export are [미확인]."""
    text = raw.decode() if isinstance(raw, bytes) else raw
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in '[{':
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = None  # maybe JSON Lines
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            if isinstance(value.get('items'), list):
                return value['items']
            # A single JSON object that is not {"items": [...]} is not a row list; guessing
            # which key holds the rows would silently freeze the wrong thing.
            raise ValueError('UNSUPPORTED_SOURCE_SHAPE: JSON object without an "items" list '
                             f'(top-level keys: {sorted(value)[:10]})')
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def _field(row, explicit, defaults):
    for name in ((explicit,) if explicit else defaults):
        value = row.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def split_sentences(text):
    """Simple rule split (., !, ?, … + space; 。！？; newlines). Not a linguistic segmenter."""
    text = unicodedata.normalize('NFC', text)
    return [part.strip() for part in _SENTENCE_BREAK.split(text) if part and part.strip()]


def build_set(items, city, *, unit='sentence', seed=SEED, heldout_share=DEFAULT_HELDOUT_SHARE,
              max_rows=DEFAULT_MAX_ROWS, text_field=None, place_field=None, synthetic=False,
              source_sha256=None, created_at=None):
    """Freeze rows for labeling. Keeps only text + a pseudonymous place key.

    Dropped: author, rating, provider language tags, translated text, review ids and URLs.
    Duplicate rows (same normalized text) keep the first occurrence so that the
    evaluator's duplicate/disjointness check is not tripped by repeated short phrases.
    """
    profile = city_profile(city)
    if profile['status'] != 'available':
        raise ValueError('CITY_LANGUAGE_PROFILE_UNAVAILABLE')
    if unit not in UNITS:
        raise ValueError(f'unit must be one of {UNITS}')
    if not 0 < heldout_share <= 1:
        raise ValueError('heldout_share must be in (0, 1]')
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError('max_rows must be a positive integer')
    counts = Counter()
    by_place = {}
    for item in items:
        counts['source_items'] += 1
        if not isinstance(item, dict):
            counts['invalid_items'] += 1; continue
        text = _field(item, text_field, SOURCE_TEXT_FIELDS)
        place = _field(item, place_field, SOURCE_PLACE_FIELDS)
        if text is None:
            counts['no_text'] += 1; continue  # rating-only or extraction unknown: not a language row
        if place is None:
            counts['no_place'] += 1; continue  # evaluator needs a place for disjoint splits
        by_place.setdefault(place, []).append(text)
    rng = random.Random(f'{seed}:{city}:{unit}')
    places = sorted(by_place)
    rng.shuffle(places)
    heldout_places = max(1, round(len(places) * heldout_share)) if places else 0
    reviews, sentences, seen = [], {}, set()
    for place_index, place in enumerate(places):
        split = 'heldout' if place_index < heldout_places else 'development'
        for text in by_place[place]:
            parts = split_sentences(text) if unit == 'sentence' else [unicodedata.normalize('NFC', text).strip()]
            reviews.append({'place_index': place_index, 'split': split, 'parts': parts})
    rng.shuffle(reviews)
    out_reviews = []
    for review in reviews:
        kept = []
        for part in review['parts']:
            if len(part) > MAX_TEXT:
                counts['too_long_rows'] += 1; continue
            key = ' '.join(part.split()).casefold()
            if key in seen:
                counts['duplicate_rows_dropped'] += 1; continue
            if len(sentences) + len(kept) >= max_rows:
                counts['rows_over_cap'] += 1; continue
            seen.add(key)
            kept.append(part)
        if not kept:
            continue
        rid = f'r{len(out_reviews) + 1:04d}'
        ids = []
        for part in kept:
            sid = f's{len(sentences) + 1:05d}'
            sentences[sid] = {'review_id': rid, 'text': part}
            ids.append(sid)
        out_reviews.append({'id': rid, 'place_key': f'p{review["place_index"] + 1:04d}',
                            'split': review['split'], 'row_ids': ids})
    counts['reviews'] = len(out_reviews)
    counts['rows'] = len(sentences)
    for split in SPLITS:
        counts[f'{split}_rows'] = sum(len(r['row_ids']) for r in out_reviews if r['split'] == split)
        counts[f'{split}_places'] = len({r['place_key'] for r in out_reviews if r['split'] == split})
    if not counts['rows'] or not counts['heldout_rows']:
        # Wrong --text-field/--place-field or an unexpected export shape: refuse instead of
        # freezing an empty set that "succeeds".
        raise ValueError(f'NO_LABELABLE_ROWS counts={json.dumps(dict(sorted(counts.items())))}')
    doc ={'schema_version': SCHEMA_VERSION, 'kind': SET_KIND, 'city': city, 'unit': unit,
           'local_languages': profile['local_languages'], 'synthetic': bool(synthetic), 'seed': seed,
           'heldout_share': heldout_share, 'max_rows': max_rows, 'source_sha256': source_sha256,
           'created_at': created_at or utcnow(), 'counts': dict(sorted(counts.items())),
           'reviews': out_reviews, 'rows': sentences,
           'notice': 'Raw review text. Keep under data/ (gitignored); never commit or paste into reports.'}
    doc['set_hash'] = set_hash(doc)
    return doc


def set_hash(doc):
    return fingerprint({k: doc[k] for k in ('city', 'unit', 'synthetic', 'seed', 'reviews', 'rows', 'local_languages')})


def verify_set(doc):
    if doc.get('kind') != SET_KIND or doc.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Not a frozen review-language label set')
    if doc.get('set_hash') != set_hash(doc):
        raise ValueError('Label set was edited after freezing (set_hash mismatch)')
    listed = [sid for review in doc['reviews'] for sid in review['row_ids']]
    if sorted(listed) != sorted(doc['rows']) or len(listed) != len(set(listed)):
        raise ValueError('Label set rows and reviews do not match')
    places = {split: {r['place_key'] for r in doc['reviews'] if r['split'] == split} for split in SPLITS}
    if places['development'] & places['heldout']:
        raise ValueError('A place appears in both development and heldout')


# ---------------------------------------------------------------- blind view

def blind_row(doc, review, index):
    """Text plus position only: no detector output, provider tag, place, author or rating."""
    sid = review['row_ids'][index]
    return {'row_id': sid, 'text': doc['rows'][sid]['text'], 'position': index + 1, 'of': len(review['row_ids'])}


def render_review(doc, review, number, total):
    lines = [f'=== 리뷰 {number}/{total} · {review["split"]} ===']
    lines += [f'  {i + 1}. {doc["rows"][sid]["text"]}' for i, sid in enumerate(review['row_ids'])]
    return '\n'.join(lines)


def render_row(row):
    return f'[문장 {row["position"]}/{row["of"]}] {row["text"]}'


def language_help():
    keys = ' · '.join(f'{key} {LANGUAGE_NAMES[code]}({code})' for key, code in LANGUAGE_KEYS.items())
    return keys + ' · o 기타(코드 입력) · - 건너뛰기 · b 이전 · q 저장 후 종료 · ? 도움말'


def translation_help():
    return '번역 여부: ' + ' · '.join(f'{k} {TRANSLATION_NAMES[v]}' for k, v in TRANSLATION_KEYS.items()) + ' · q 저장 후 종료'


# ---------------------------------------------------------------- labels

def new_labels(doc, reviewer_id):
    if not REVIEWER_PATTERN.match(reviewer_id or '') or '@' in reviewer_id:
        raise ValueError('Pseudonymous reviewer id required (letters, digits, - or _, max 32)')
    return {'schema_version': SCHEMA_VERSION, 'kind': LABEL_KIND, 'set_hash': doc['set_hash'],
            'label_source': 'synthetic_fixture' if doc['synthetic'] else 'independent_human',
            'reviewer_id': reviewer_id, 'created_at': utcnow(), 'reviews': {}, 'rows': {}}


def check_labels(doc, labels):
    verify_set(doc)
    if labels.get('kind') != LABEL_KIND or labels.get('set_hash') != doc['set_hash']:
        raise ValueError('Labels belong to a different frozen set')
    expected_source = 'synthetic_fixture' if doc['synthetic'] else 'independent_human'
    if labels.get('label_source') != expected_source:
        raise ValueError('Label provenance does not match the set')
    if set(labels['rows']) - set(doc['rows']):
        raise ValueError('Labels reference rows outside the set')
    if set(labels['reviews']) - {r['id'] for r in doc['reviews']}:
        raise ValueError('Labels reference reviews outside the set')
    for row in labels['rows'].values():
        code = row.get('expected')
        if code is not None and normalize_code(code) != code:
            raise ValueError('Language answers must be ISO 639-1 codes or und/mul/zxx')
        if row.get('input') not in (None, *INPUT_MODES):
            raise ValueError('Invalid input mode')
    for review in labels['reviews'].values():
        if review.get('translation') not in (None, *TRANSLATION_NAMES):
            raise ValueError('Invalid translation judgment')
        if review.get('input') not in (None, *INPUT_MODES):
            raise ValueError('Invalid input mode')


# How an answer reached the file. Only 'terminal' (single keys on a TTY) counts as a
# person at the keyboard; this is a marker, not proof of who typed (see LABELING.md).
INPUT_MODES = ('terminal', 'piped', 'scripted')


def normalize_code(code):
    """Return the stored form of a typed language code, or None when it is not acceptable.

    The detector predicts ISO 639-1, so 3-letter aliases are mapped (kor -> ko, jpn -> ja,
    spa -> es, cat -> ca, eng -> en via language.base_language) and other 3-letter codes
    are refused instead of silently never matching a prediction. und/mul/zxx stay as is.
    """
    if not isinstance(code, str) or not CODE_PATTERN.match(code):
        return None
    if code in SPECIAL_CODES:
        return code
    mapped = base_language(code)
    return mapped if mapped and len(mapped) == 2 else None


def record_language(labels, row_id, code, seconds=None, *, input_mode='scripted'):
    stored = normalize_code(code)
    if stored is None:
        raise ValueError('Language answer must be a 2-letter ISO 639-1 code (or und/mul/zxx)')
    if input_mode not in INPUT_MODES:
        raise ValueError('Invalid input mode')
    row = labels['rows'].setdefault(row_id, {})
    row.update(expected=stored, skipped=False, input=input_mode, updated_at=utcnow())
    if seconds is not None:
        row['seconds'] = round(min(max(seconds, 0.0), 120.0), 3)
    return stored


def skip_row(labels, row_id):
    row = labels['rows'].setdefault(row_id, {})
    if row.get('expected') is None:
        row.update(expected=None, skipped=True, updated_at=utcnow())


def record_translation(labels, review_id, judgment, *, input_mode='scripted'):
    if judgment not in TRANSLATION_NAMES:
        raise ValueError('Invalid translation judgment')
    if input_mode not in INPUT_MODES:
        raise ValueError('Invalid input mode')
    labels['reviews'][review_id] = {'translation': judgment, 'input': input_mode, 'updated_at': utcnow()}


def review_status(labels, review):
    rows = [labels['rows'].get(sid, {}) for sid in review['row_ids']]
    if review['id'] not in labels['reviews'] and not any(rows):
        return 'pending'
    decided = review['id'] in labels['reviews']
    return 'complete' if decided and all(r.get('expected') for r in rows) else 'partial'


def progress(doc, labels, splits=SPLITS):
    reviews = [r for r in doc['reviews'] if r['split'] in splits]
    total = sum(len(r['row_ids']) for r in reviews)
    done = sum(bool(labels['rows'].get(sid, {}).get('expected')) for r in reviews for sid in r['row_ids'])
    checks_left = sum(r['id'] not in labels['reviews'] for r in reviews)
    timed = [labels['rows'][sid]['seconds'] for r in reviews for sid in r['row_ids']
             if 'seconds' in labels['rows'].get(sid, {})]
    measured = sum(timed) / len(timed) if len(timed) >= 10 else None
    remaining = total - done
    review_low, review_high = SECONDS_PER_REVIEW_CHECK
    if measured:
        seconds = [round(remaining * measured + checks_left * review_low)] * 2
    else:
        low, high = SECONDS_PER_ROW
        seconds = [remaining * low + checks_left * review_low, remaining * high + checks_left * review_high]
    return {'reviews_total': len(reviews), 'reviews_complete': sum(review_status(labels, r) == 'complete' for r in reviews),
            'rows_total': total, 'rows_done': done, 'rows_remaining': remaining,
            'translation_checks_remaining': checks_left,
            'measured_seconds_per_row': round(measured, 1) if measured else None, 'remaining_seconds': seconds}


def progress_line(state):
    low, high = state['remaining_seconds']
    eta = format_duration(low) if low == high else f'{format_duration(low)}~{format_duration(high)}'
    basis = (f'실측 평균 {state["measured_seconds_per_row"]}초' if state['measured_seconds_per_row']
             else f'문장당 {SECONDS_PER_ROW[0]}~{SECONDS_PER_ROW[1]}초 가정')
    return (f'리뷰 {state["reviews_complete"]}/{state["reviews_total"]} 완료 · 문장 {state["rows_done"]}/{state["rows_total"]}'
            f' · 남은 예상 {eta} ({basis})')


def estimate(doc):
    """Labeling time for the whole set before starting [추정]."""
    rows = doc['counts'].get('rows', 0)
    reviews = doc['counts'].get('reviews', 0)
    low = rows * SECONDS_PER_ROW[0] + reviews * SECONDS_PER_REVIEW_CHECK[0]
    high = rows * SECONDS_PER_ROW[1] + reviews * SECONDS_PER_REVIEW_CHECK[1]
    return {'rows': rows, 'reviews': reviews, 'seconds': [low, high],
            'text': f'{format_duration(low)}~{format_duration(high)} [추정]'}


# ---------------------------------------------------------------- evaluation

# Reason codes that keep production_strict_gate_supported false even if the evaluator passes.
SENTENCE_UNIT_BLOCK = 'SENTENCE_UNIT_NOT_PRODUCTION_UNIT'  # product classifies whole reviews
NON_INTERACTIVE_BLOCK = 'NON_INTERACTIVE_LABEL_INPUT'     # an answer did not come from a TTY session


def evaluation_payload(doc, labels, *, observation_profile='bounded_recent_v1'):
    """evaluate_dataset input built only from typed answers. Unlabeled/skipped rows stay out.

    Translation checks are per review: only the first metric row of a review carries
    original_checked/original_mismatch, so the evaluator's row-level original_checks equals
    the number of checked reviews (not sentences). und/mul/zxx answers are kept out of the
    metric rows and counted separately (EXCLUDED_FROM_METRICS).
    """
    check_labels(doc, labels)
    out = {split: [] for split in SPLITS}
    counts = Counter()
    for review in doc['reviews']:
        decision = labels['reviews'].get(review['id'], {})
        judgment = decision.get('translation')
        checked = judgment in ('original', 'translated')
        if checked:
            counts[f'{review["split"]}_original_checked_reviews'] += 1
        marked = False
        for sid in review['row_ids']:
            answer = labels['rows'].get(sid, {})
            code = answer.get('expected')
            if not code:
                counts['skipped_rows' if answer.get('skipped') else 'unlabeled_rows'] += 1
                continue
            if code in EXCLUDED_FROM_METRICS:
                counts[f'{review["split"]}_excluded_{code}_rows'] += 1
                continue
            mark = checked and not marked
            marked = marked or mark
            out[review['split']].append({'text': doc['rows'][sid]['text'], 'expected': code,
                'place_id': review['place_key'], 'original_checked': mark,
                'original_mismatch': mark and judgment == 'translated'})
            counts[f'{review["split"]}_labeled_rows'] += 1
            if review['split'] == 'heldout':
                counts[f'heldout_input_{answer.get("input") or "unknown"}_rows'] += 1
        if review['split'] == 'heldout' and marked:
            counts['heldout_original_checks_counted'] += 1
            counts[f'heldout_input_{decision.get("input") or "unknown"}_translation_checks'] += 1
    if not out['heldout']:
        raise ValueError('No labeled heldout rows to evaluate')
    payload = {'city': doc['city'], 'domain': 'restaurant_reviews', 'synthetic': bool(doc['synthetic']),
               'observation_profile': observation_profile, 'development': out['development'], 'heldout': out['heldout']}
    return payload, dict(counts)


def interval_summary(result):
    """The two numbers the PRD asks for, with explicit supports. Null when the denominator is 0."""
    return {'local_precision': result['local_precision'], 'local_precision_interval95': result['local_precision_interval95'],
            'local_prediction_support': result['local_prediction_support'],
            'korean_recall': result['korean_recall'], 'korean_recall_interval95': result['korean_recall_interval95'],
            'korean_support': result['korean_support'], 'interval_method': result['interval_method'],
            'support_sufficient': result['support_sufficient'],
            'policy_minimums': {k: QUALITY_POLICY[k] for k in ('min_labels', 'min_korean_truth', 'min_local_truth',
                                                               'min_local_predictions', 'min_original_checks')}}


def _mul_diagnostic(doc, labels, detector, local_languages):
    """How the detector treats heldout rows a person marked 'mul' (no text in the output)."""
    out = Counter()
    for review in doc['reviews']:
        if review['split'] != 'heldout':
            continue
        for sid in review['row_ids']:
            if labels['rows'].get(sid, {}).get('expected') != 'mul':
                continue
            predicted = detector.detect(doc['rows'][sid]['text']).get('language')
            out['predicted_local' if predicted in local_languages else 'predicted_unknown' if not predicted
                else 'predicted_other'] += 1
    return {'rows': sum(out.values()), **{k: out[k] for k in ('predicted_local', 'predicted_other', 'predicted_unknown')},
            'policy': 'excluded from metric denominators; reported here only'}


def evaluate(doc, labels, *, detector=None):
    """Run the existing evaluator; the report carries no raw text and changes no product control."""
    from src.research.evaluation import evaluate_dataset
    from src.research.language import LocalLanguageDetector
    payload, counts = evaluation_payload(doc, labels)
    detector = detector or LocalLanguageDetector()
    result = evaluate_dataset(payload, detector=detector)
    expected = Counter(row['expected'] for row in payload['heldout'])
    blockers = []
    if doc['unit'] == 'sentence':
        blockers.append(SENTENCE_UNIT_BLOCK)
    modes = {key.split('_')[2] for key in counts if key.startswith('heldout_input_')}  # terminal/piped/scripted/unknown
    if modes - {'terminal'}:
        blockers.append(NON_INTERACTIVE_BLOCK)
    report = deepcopy(result)
    report.update(evaluator_production_strict_gate_supported=result['production_strict_gate_supported'],
                  labeling_gate_blockers=blockers)
    if blockers:
        report.update(production_strict_gate_supported=False, classification_quality='CLASSIFICATION_QUALITY_UNVERIFIED')
    report.update(labeling={'kind': LABEL_KIND, 'set_hash': doc['set_hash'], 'unit': doc['unit'],
                            'reviewer_id': labels['reviewer_id'], 'label_source': labels['label_source'],
                            'reviewer_count': 1, 'set_counts': doc['counts'], 'label_counts': counts,
                            'original_checks_unit': 'review',
                            'original_checked_reviews_heldout': counts.get('heldout_original_checked_reviews', 0),
                            'excluded_from_metrics': {code: counts.get(f'heldout_excluded_{code}_rows', 0)
                                                      for code in EXCLUDED_FROM_METRICS},
                            'mul_diagnostic': _mul_diagnostic(doc, labels, detector, set(doc['local_languages'])),
                            'heldout_expected_languages': dict(sorted(expected.items()))},
                  intervals=interval_summary(result), runs_started=0, product_controls_changed=False,
                  production_enabled=False)
    return report
