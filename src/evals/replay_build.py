"""Build the hand-made replay records for `python -m src.evals.mail --mode replay`.

    python -m src.evals.replay_build            # (re)write evals/replay/mail/
    python -m src.evals.replay_build --check    # exit 1 if the stored records differ

Every record is made by hand, never by a model:

* `gold_oracle`    — the gold answer re-expressed in DOCUMENT_RESERVATIONS_SCHEMA.
* `handmade_fault` — deliberate defects on a few cases (see FAULTS).

The request each record answers is captured by running the real
`parser.extract_document_reservations` path (metered, repair included) with a
scripted adapter, so request keys always match what replay mode will send.
Records hold hashes of the request, never the mail text. Numbers produced from
these records test the pipeline and the scorer; they are not model quality.
After any prompt or schema change, rerun this script.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

from src.config import EXTRACTION_MODEL, SNIPPET_MAX_CHARS
from src.evals.gold import GOLD_DIR, load_gold
from src.evals.mail import DEFAULT_REPLAY_DIR, eval_environment, extract_metered, read_case_text, replay_policy_config
from src.foundation.repository import DomainError
from src.llm.replay import ReplayAdapter, ReplayStore, make_record
from src.prompts import load_prompt
from src.reliability.providers import ProviderResult

KIND = {'flight': '항공', 'stay': '숙소', 'car_rental': '렌터카', 'tour': '투어'}  # restaurant → null
EVENT = {'flight': 'flight', 'stay': 'stay', 'car_rental': 'pickup', 'tour': 'activity', 'restaurant': 'activity'}
FAULTS = {
    # first answer breaks the schema (date format), the single repair returns the oracle
    'mtp-02-tokyo-hotel': 'schema_violation_repaired',
    # first answer is not JSON, the repair cites a snippet absent from the mail → extraction fails
    'mtp-06-date-only-unknown-party': 'repair_failed',
    # the payment date written as the visit date
    'syn-01-ja-ryokan-payment-date': 'payment_date_as_visit',
    # the requested (unconfirmed) new time written as the confirmed time
    'syn-13-es-change-request-pending': 'change_as_new_confirmed',
}


def _snippet(raw, code):
    if not code:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if code in line and len(line) <= SNIPPET_MAX_CHARS:
            return line
    return None


def _event(booking):
    return {'event_type': EVENT.get(booking['type'], 'activity'), 'start_local': booking['start_local'],
            'end_local': booking['end_local'], 'start_timezone': booking['start_tz'],
            'end_timezone': booking['end_tz'], 'location': None}


def _reservation(bookings, raw):
    first, last = bookings[0], bookings[-1]
    start, end = first['start_local'], last['end_local']
    date = start[:10] if start else None
    date_end = end[:10] if end else None
    if date and date_end and date_end < date:
        date_end = None
    return {'kind': KIND.get(first['type']), 'provider': first['provider_name'],
            'confirmation_number': first['confirmation_code'],
            'date': date, 'date_end': date_end,
            'time': start[11:16] if start and len(start) > 10 else None,
            'time_end': end[11:16] if end and len(end) > 10 else None,
            'location': None, 'refund_policy': None,
            'raw_snippet': _snippet(raw, first['confirmation_code']),
            'events': [_event(booking) for booking in bookings]}


def oracle_payload(case, raw) -> dict:
    """Gold → DOCUMENT_RESERVATIONS_SCHEMA. Consecutive legs of one flight booking share a reservation."""
    groups = []
    for booking in case['expected_bookings']:
        previous = groups[-1][-1] if groups else None
        if (previous and booking['type'] == 'flight' and previous['type'] == 'flight'
                and booking['confirmation_code'] and booking['confirmation_code'] == previous['confirmation_code']
                and booking['provider_name'] == previous['provider_name']):
            groups[-1].append(booking)
        else:
            groups.append([booking])
    return {'reservations': [_reservation(group, raw) for group in groups]}


def _with_start(payload, value):
    """Write `value` as the first reservation's start (a forbidden decoy)."""
    item = payload['reservations'][0]
    item['date'] = value[:10]
    item['time'] = value[11:16] if len(value) > 10 else None
    if item['date_end'] and item['date_end'] < item['date']:
        item['date_end'] = None
    item['events'][0]['start_local'] = value
    return payload


def script_for(case, raw):
    """[(response text, finish)] for each call, plus (source, label prefix)."""
    oracle = oracle_payload(case, raw)
    fault = FAULTS.get(case['case_id'])
    text = json.dumps(oracle, ensure_ascii=False)
    if fault is None:
        return [(text, 'stop')], 'gold_oracle', f"{case['case_id']}:oracle"
    label = f"{case['case_id']}:fault:{fault}"
    if fault == 'schema_violation_repaired':
        broken = json.loads(text)
        broken['reservations'][0]['date'] = broken['reservations'][0]['date'].replace('-', '/')
        return [(json.dumps(broken, ensure_ascii=False), 'stop'), (text, 'stop')], 'handmade_fault', label
    if fault == 'repair_failed':
        bad = json.loads(text)
        bad['reservations'][0]['raw_snippet'] = 'summary: synthetic tour booking (not in the mail)'
        return [(text[:40], 'stop'), (json.dumps(bad, ensure_ascii=False), 'stop')], 'handmade_fault', label
    if fault in ('payment_date_as_visit', 'change_as_new_confirmed'):
        decoy = case['decoys'][fault]['values'][0]
        start = case['expected_bookings'][0]['start_local'] or ''
        value = decoy if len(decoy) > 10 or len(start) <= 10 else f'{decoy}T{start[11:16]}'
        return [(json.dumps(_with_start(json.loads(text), value), ensure_ascii=False), 'stop')], 'handmade_fault', label
    raise ValueError(f'unknown fault {fault}')


class ScriptedAdapter(ReplayAdapter):
    """Answers calls from a script and keeps each request's kwargs for the record."""

    def __init__(self, script):
        super().__init__(ReplayStore(tempfile.gettempdir()))
        self.script = list(script)
        self.captured = []

    def preflight(self, kind, kwargs):
        if kind != 'chat':
            raise DomainError('PROVIDER_CAPABILITY_MISSING', 'replay 는 임베딩을 지원하지 않습니다.', 503)
        if not self.script:
            raise DomainError('REPLAY_MISS', '스크립트에 남은 응답이 없습니다.', 503)

    def send(self, client, kind, kwargs):
        text, finish = self.script.pop(0)
        self.captured.append((dict(kwargs), text, finish))
        response = {'text': text, 'finish': finish, 'refusal': None, 'tool_calls': [],
                    'usage': {'input_tokens': 0, 'output_tokens': 0}}
        return ProviderResult({'replay': True, 'source': 'scripted', 'label': None, 'response': response},
                              {'input_tokens': 0, 'output_tokens': 0})


def build_records(gold_dir=GOLD_DIR, model=EXTRACTION_MODEL) -> list[dict]:
    prompts = [load_prompt('mail_document_extraction').meta(), load_prompt('mail_extraction_repair').meta()]
    records = []
    with tempfile.TemporaryDirectory(prefix='replay-build-') as directory, \
            eval_environment(directory, replay_policy_config(model)) as env:
        for case in load_gold(gold_dir):
            raw = read_case_text(case)
            script, source, label = script_for(case, raw)
            adapter = ScriptedAdapter(script)
            extract_metered(env, case['case_id'], raw, adapter, model)
            if adapter.script:
                raise RuntimeError(f"{case['case_id']}: 스크립트 응답이 남았습니다 (경로가 예상과 다름).")
            for index, (kwargs, text, finish) in enumerate(adapter.captured):
                step = 'first' if index == 0 else 'repair'
                records.append(make_record(kwargs, text=text, finish=finish, source=source,
                                           label=f'{label}:{step}', prompt=prompts[min(index, 1)]))
    keys = [record['request_key'] for record in records]
    if len(keys) != len(set(keys)):
        raise RuntimeError('서로 다른 사례가 같은 replay 키를 냈습니다.')
    return records


def _serialise(record):
    return json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m src.evals.replay_build',
                                     description='손으로 만든 mail replay 기록 생성 (synthetic)')
    parser.add_argument('--out', default=str(DEFAULT_REPLAY_DIR))
    parser.add_argument('--gold-dir', default=str(GOLD_DIR))
    parser.add_argument('--model', default=EXTRACTION_MODEL)
    parser.add_argument('--check', action='store_true', help='저장된 기록이 재생성 결과와 같은지만 확인')
    parser.add_argument('--prune', action='store_true', help='재생성 대상이 아닌 손 기록(gold_oracle/handmade_fault)을 지운다')
    args = parser.parse_args(argv)
    out = Path(args.out)
    records = build_records(args.gold_dir, args.model)
    wanted = {f"{record['request_key']}.json": _serialise(record) for record in records}
    existing = {path.name: path for path in out.glob('*.json')} if out.is_dir() else {}
    stale = []
    for name, path in existing.items():
        if name in wanted:
            continue
        try:
            source = json.loads(path.read_text(encoding='utf-8')).get('source')
        except ValueError:
            source = None
        if source in ('gold_oracle', 'handmade_fault'):
            stale.append(path)
    if args.check:
        changed = [name for name, body in wanted.items()
                   if name not in existing or existing[name].read_text(encoding='utf-8') != body]
        print(json.dumps({'records': len(wanted), 'changed_or_missing': len(changed), 'stale': len(stale)}))
        return 1 if changed or stale else 0
    store = ReplayStore(out)
    for record in records:
        store.put(record)
    if args.prune:
        for path in stale:
            path.unlink()
    by_source = {}
    for record in records:
        by_source[record['source']] = by_source.get(record['source'], 0) + 1
    print(json.dumps({'records': len(records), 'by_source': by_source, 'faults': FAULTS,
                      'stale_handmade': len(stale), 'pruned': bool(args.prune)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
