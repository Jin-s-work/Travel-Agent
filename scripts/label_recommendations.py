#!/usr/bin/env python3
"""Blind recommendation labeling CLI (operator only, local files, no network).

Shows each frozen query's conditions and its pooled candidates in a seeded random
order with public facts only: no model score, rank or model name. Single keys:

  0 부적합 · 1 약간 관련 · 2 적합 · 3 매우 적합   (기록 후 다음 후보)
  r 짧은 이유 입력 · b 이전 후보 · s 건너뛰기(미판정 유지) · q 저장 후 종료 · ? 도움말

Every keystroke that changes a grade is saved atomically to the labels file.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HELP = '0 부적합 · 1 약간 관련 · 2 적합 · 3 매우 적합 · r 이유 · b 이전 · s 건너뛰기 · q 저장 후 종료'


class TerminalKeys:
    """Single keypress via the standard library (tty/termios); no install needed."""

    def key(self):
        import termios, tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)  # keeps ISIG: Ctrl-C raises KeyboardInterrupt, handled in label()
            char = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if char in ('', '\x04'):  # closed stdin or Ctrl-D: save and quit instead of spinning
            return 'q'
        return char

    def line(self, prompt):
        try:
            return input(prompt)
        except EOFError:
            return ''


class LineKeys:
    """Fallback when stdin is not a terminal: one command per line."""

    def key(self):
        value = sys.stdin.readline()
        return value.strip()[:1] if value else 'q'

    def line(self, prompt):
        return sys.stdin.readline().strip()


class ScriptedKeys:
    """Injected keys for tests. Exhaustion means quit."""

    def __init__(self, keys):
        self.keys = list(keys)

    def key(self):
        return self.keys.pop(0) if self.keys else 'q'

    def line(self, prompt):
        return self.keys.pop(0) if self.keys else ''


def save(path, labels):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(labels, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    os.replace(temp, path)


def load_labels(path, pool, reviewer_id):
    from src.recommendations.labeling import new_labels, check_labels
    if path.exists():
        labels = json.loads(path.read_text())
        check_labels(pool, labels)
        if labels['reviewer_id'] != reviewer_id:
            raise SystemExit('This labels file belongs to another reviewer id; use a separate file per reviewer.')
        return labels
    return new_labels(pool, reviewer_id)


def label(pool, labels, path, keys, out=print, *, splits=('holdout', 'development'), cities=None, clock=time.monotonic):
    """Run the labeling loop. Returns 'quit' or 'done'. Grades come only from keys.

    Ctrl-C saves the current query state (partial) and returns 'quit' instead of a traceback.
    """
    from src.recommendations import labeling as L
    state = {}
    try:
        return _label(pool, labels, path, keys, out, splits, cities, clock, state)
    except KeyboardInterrupt:
        if state.get('row') is not None:
            L.finish_query(state['row'], state['query'])
        save(path, labels)
        out('')
        out('중단(Ctrl-C): 저장했습니다. 다시 실행하면 이어서 합니다.')
        out(L.progress_line(L.progress(pool, labels, tuple(splits))))
        return 'quit'


def _label(pool, labels, path, keys, out, splits, cities, clock, state):
    from src.recommendations import labeling as L
    order = {split: i for i, split in enumerate(splits)}
    queries = sorted((q for q in pool['queries'] if q['split'] in order and (not cities or q['city'] in cities)),
                     key=lambda q: (order[q['split']], q['id']))
    out(L.progress_line(L.progress(pool, labels, tuple(splits))))
    for query in queries:
        row = labels['queries'].get(query['id'])
        if row and row['status'] == 'complete':
            continue
        if row and row['status'] == 'partial':
            row['skipped'] = []  # show previously skipped candidates again
        row = L.start_query(labels, query)
        state.update(row=row, query=query)
        view = L.blind_view(query, pool['catalog'])
        ids = list(query['blind_order'])
        out('')
        out(f'=== 질의 {query["id"]} ({query["split"]}) ===')
        out(L.render_query(view['query']))
        out(HELP)
        index = next((i for i, pid in enumerate(ids) if pid not in row['judgments'] and pid not in row['skipped']), len(ids))
        while index < len(ids):
            pid = ids[index]
            out('')
            out(L.render_card(view['cards'][index], index + 1, len(ids)))
            if pid in row['judgments']:
                out(f'  현재 점수: {row["judgments"][pid]}' + (f' · 이유: {row["reasons"][pid]}' if pid in row['reasons'] else ''))
            started = clock()
            while True:
                key = keys.key()
                if key in ('0', '1', '2', '3'):
                    L.record(row, pid, int(key), clock() - started)
                    save(path, labels); index += 1; break
                if key == 'r':
                    text = ' '.join(keys.line('  이유(짧게, Enter로 끝): ').split())[:200]
                    if text:
                        row['reasons'][pid] = text
                    else:
                        row['reasons'].pop(pid, None)
                    save(path, labels); continue
                if key == 'b':
                    index = max(0, index - 1); break
                if key == 's':
                    if pid not in row['judgments'] and pid not in row['skipped']:
                        row['skipped'].append(pid)
                    save(path, labels); index += 1; break
                if key == 'q':
                    L.finish_query(row, query); save(path, labels)
                    out(L.progress_line(L.progress(pool, labels, tuple(splits))))
                    return 'quit'
                if key == '?':
                    out(HELP)
        L.finish_query(row, query); save(path, labels)
        state.clear()
        out(f'질의 {query["id"]} {"완료" if row["status"] == "complete" else "부분 저장(건너뛴 후보 있음)"}')
        out(L.progress_line(L.progress(pool, labels, tuple(splits))))
    return 'done'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--reviewer-id', required=True, help='pseudonym, e.g. R1 (not an email or name)')
    parser.add_argument('--split', choices=('holdout', 'development', 'all'), default='holdout')
    parser.add_argument('--city', choices=('tokyo', 'barcelona'))
    parser.add_argument('--progress', action='store_true', help='print progress and exit')
    args = parser.parse_args()
    from src.recommendations import labeling as L
    pool = json.loads(args.pool.read_text())
    try:
        L.verify_pool(pool)
    except ValueError as error:
        parser.error(f'Pool file is not a valid frozen label pool: {error}')
    labels = load_labels(args.labels, pool, args.reviewer_id)
    splits = ('holdout', 'development') if args.split == 'all' else (args.split,)
    if args.progress:
        print(json.dumps(L.progress(pool, labels, splits), ensure_ascii=False)); return 0
    keys = TerminalKeys() if sys.stdin.isatty() else LineKeys()
    result = label(pool, labels, args.labels, keys, splits=splits, cities={args.city} if args.city else None)
    print('라벨 저장: ' + str(args.labels) + (' · 모든 대상 질의 완료' if result == 'done' else ' · 중간 종료(다시 실행하면 이어서)'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
