#!/usr/bin/env python3
"""Blind review-language labeling CLI (operator only, local files, no network).

Subcommands:
  prepare   operator-provided pilot export -> frozen label set under data/ (raw text, gitignored)
  label     type answers with single keys; saved after every keystroke
  progress  print progress and the remaining-time estimate
  evaluate  run the existing heldout evaluator; Wilson 95% intervals; no raw text in the output

Per review: 1 원문으로 보임 · 2 번역문으로 보임 · 0 확인 안 함
Per sentence: j 일본어 · k 한국어 · e 영어 · s 스페인어 · c 카탈루냐어 · h 중국어 ·
              u 판단 불가 · m 여러 언어 섞임 · x 언어 내용 없음 · o 기타(코드 입력) ·
              - 건너뛰기 · b 이전 · q 저장 후 종료

Never starts a provider run, never shows detector output, never writes product controls.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.label_recommendations import TerminalKeys, LineKeys, ScriptedKeys  # noqa: E402,F401  (same key readers as bundle B)


def load_labels(path, doc, reviewer_id):
    from src.research import language_labels as L
    if Path(path).exists():
        labels = json.loads(Path(path).read_text())
        L.check_labels(doc, labels)
        if labels['reviewer_id'] != reviewer_id:
            raise SystemExit('This labels file belongs to another reviewer id; use a separate file per reviewer.')
        return labels
    return L.new_labels(doc, reviewer_id)


def label(doc, labels, path, keys, out=print, *, splits=('heldout', 'development'), clock=time.monotonic,
          input_mode='scripted'):
    """Labeling loop. Returns 'quit' or 'done'. Every answer comes from `keys`; nothing is prefilled.

    input_mode is stored on every answer: 'terminal' only for single keys on a TTY (cmd_label),
    'piped' for stdin lines, 'scripted' for injected keys (tests). Evaluation treats any
    non-terminal heldout answer as a gate blocker.
    """
    from src.research import language_labels as L
    try:
        return _label(doc, labels, path, keys, out, splits, clock, input_mode)
    except KeyboardInterrupt:
        L.write_private(path, labels)
        out('')
        out('중단(Ctrl-C): 저장했습니다. 다시 실행하면 이어서 합니다.')
        out(L.progress_line(L.progress(doc, labels, tuple(splits))))
        return 'quit'


def _label(doc, labels, path, keys, out, splits, clock, input_mode):
    from src.research import language_labels as L
    order = {split: i for i, split in enumerate(splits)}
    reviews = sorted((r for r in doc['reviews'] if r['split'] in order), key=lambda r: (order[r['split']], r['id']))
    out(L.progress_line(L.progress(doc, labels, tuple(splits))))
    for number, review in enumerate(reviews, 1):
        status = L.review_status(labels, review)
        if status == 'complete':
            continue
        out('')
        out(L.render_review(doc, review, number, len(reviews)))
        ids = review['row_ids']
        # index -1 = translation judgment for the whole review. A partial review resumes at its
        # first unanswered sentence, previously skipped ones included; the stored skipped flag
        # is left alone and is cleared only when a new answer is recorded (record_language).
        index = -1 if review['id'] not in labels['reviews'] else next(
            (i for i, sid in enumerate(ids) if not labels['rows'].get(sid, {}).get('expected')), len(ids))
        while index < len(ids):
            if index == -1:
                current = labels['reviews'].get(review['id'], {}).get('translation')
                out(L.translation_help() + (f' (현재: {L.TRANSLATION_NAMES[current]})' if current else ''))
                key = keys.key()
                if key in L.TRANSLATION_KEYS:
                    L.record_translation(labels, review['id'], L.TRANSLATION_KEYS[key], input_mode=input_mode)
                    L.write_private(path, labels); index = 0
                elif key == 'q':
                    L.write_private(path, labels)
                    out(L.progress_line(L.progress(doc, labels, tuple(splits))))
                    return 'quit'
                continue
            sid = ids[index]
            out(L.render_row(L.blind_row(doc, review, index)))
            current = labels['rows'].get(sid, {}).get('expected')
            if current:
                out(f'  현재 답: {current}')
            started = clock()
            while True:
                key = keys.key()
                if key in L.LANGUAGE_KEYS:
                    L.record_language(labels, sid, L.LANGUAGE_KEYS[key], clock() - started, input_mode=input_mode)
                    L.write_private(path, labels); index += 1; break
                if key == L.OTHER_KEY:
                    code = keys.line('  언어 코드(ISO 639-1 두 글자, 예: fr, de, it): ').strip().lower()
                    stored = L.normalize_code(code)
                    if stored:
                        L.record_language(labels, sid, stored, clock() - started, input_mode=input_mode)
                        if stored != code:
                            out(f'  {code} → {stored} 로 저장했습니다.')
                        L.write_private(path, labels); index += 1; break
                    out('  ISO 639-1 두 글자 코드(예: fr)로 입력하세요. kor·jpn·spa·cat·eng 외의 세 글자 코드는 받지 않습니다.')
                    continue
                if key == '-':
                    L.skip_row(labels, sid); L.write_private(path, labels); index += 1; break
                if key == 'b':
                    index -= 1; break
                if key == 'q':
                    L.write_private(path, labels)
                    out(L.progress_line(L.progress(doc, labels, tuple(splits))))
                    return 'quit'
                if key == '?':
                    out(L.language_help())
        out(f'리뷰 {review["id"]} {"완료" if L.review_status(labels, review) == "complete" else "부분 저장(건너뛴 문장 있음)"}')
        out(L.progress_line(L.progress(doc, labels, tuple(splits))))
    return 'done'


def cmd_prepare(args):
    from src.research import language_labels as L
    raw = args.source.read_bytes()
    doc = L.build_set(L.load_source(raw), args.city, unit=args.unit, heldout_share=args.heldout_share,
                      max_rows=args.max_rows, text_field=args.text_field, place_field=args.place_field,
                      synthetic=args.synthetic, source_sha256=hashlib.sha256(raw).hexdigest())
    L.write_private(args.output, doc)
    print(json.dumps({'output': str(args.output), 'set_hash': doc['set_hash'], 'counts': doc['counts'],
                      'estimate': L.estimate(doc)['text']}, ensure_ascii=False))
    return 0


def cmd_label(args):
    from src.research import language_labels as L
    doc = json.loads(args.set.read_text())
    L.verify_set(doc)
    L.ensure_private_path(args.labels)
    labels = load_labels(args.labels, doc, args.reviewer_id)
    splits = ('heldout', 'development') if args.split == 'all' else (args.split,)
    interactive = sys.stdin.isatty()
    keys = TerminalKeys() if interactive else LineKeys()
    result = label(doc, labels, args.labels, keys, splits=splits, input_mode='terminal' if interactive else 'piped')
    print('라벨 저장: ' + str(args.labels) + (' · 모든 대상 리뷰 완료' if result == 'done' else ' · 중간 종료(다시 실행하면 이어서)'))
    return 0


def cmd_progress(args):
    from src.research import language_labels as L
    doc = json.loads(args.set.read_text())
    labels = load_labels(args.labels, doc, args.reviewer_id)
    splits = ('heldout', 'development') if args.split == 'all' else (args.split,)
    print(json.dumps(L.progress(doc, labels, splits), ensure_ascii=False))
    return 0


def cmd_evaluate(args):
    from src.research import language_labels as L
    doc = json.loads(args.set.read_text())
    labels = json.loads(args.labels.read_text())
    report = L.evaluate(doc, labels)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text)
    args.output.chmod(0o600)
    summary = report['intervals']
    print(json.dumps({'output': str(args.output), 'heldout_rows': report['count'],
                      'local_precision_interval95': summary['local_precision_interval95'],
                      'korean_recall_interval95': summary['korean_recall_interval95'],
                      'support_sufficient': summary['support_sufficient'],
                      'production_strict_gate_supported': report['production_strict_gate_supported'],
                      'labeling_gate_blockers': report['labeling_gate_blockers'],
                      'original_checks_reviews': report['original_checks']}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='freeze a label set from an operator-provided export')
    p.add_argument('--source', type=Path, required=True, help='JSON array / {"items": [...]} / JSON Lines')
    p.add_argument('--city', choices=('tokyo', 'barcelona'), required=True)
    p.add_argument('--output', type=Path, required=True, help='under data/ (gitignored) or outside the repository')
    p.add_argument('--unit', choices=('sentence', 'review'), default='sentence')
    p.add_argument('--heldout-share', type=float, default=0.7)
    p.add_argument('--max-rows', type=int, default=2000)
    p.add_argument('--text-field', help='source field holding the original text (default: text/original_text/body)')
    p.add_argument('--place-field', help='source field holding the place id (default: place_id/placeId/canonical_place_id)')
    p.add_argument('--synthetic', action='store_true', help='mark fixture data; can never pass the quality gate')
    p.set_defaults(func=cmd_prepare)
    for name, func, help_text in (('label', cmd_label, 'type answers'), ('progress', cmd_progress, 'print progress')):
        q = sub.add_parser(name, help=help_text)
        q.add_argument('--set', type=Path, required=True)
        q.add_argument('--labels', type=Path, required=True)
        q.add_argument('--reviewer-id', required=True, help='pseudonym, e.g. R1 (not an email or name)')
        q.add_argument('--split', choices=('heldout', 'development', 'all'), default='heldout')
        q.set_defaults(func=func)
    e = sub.add_parser('evaluate', help='heldout evaluation with Wilson 95% intervals')
    e.add_argument('--set', type=Path, required=True)
    e.add_argument('--labels', type=Path, required=True)
    e.add_argument('--output', type=Path, required=True)
    e.set_defaults(func=cmd_evaluate)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
