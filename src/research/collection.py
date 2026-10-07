"""Bounded observation-window collection, independent from database and HTTP.

Durability is injected: checkpoint receives transformed observations, never raw
provider responses unless a caller explicitly chooses an unsafe transform. The
service supplies its policy-approved normalization and persistence transaction.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Callable

from src.providers.reviews import CollectionRequest, ProviderPage, ReviewProviderError, parse_timestamp


@dataclass
class CollectionResult:
    records: list[dict]
    coverage: dict
    reason_codes: list[str]
    state: str
    checkpoint: dict

    def to_dict(self):
        return {'data': {'records':self.records}, 'coverage':self.coverage,
                'reason_codes':self.reason_codes,'state':self.state,'checkpoint':self.checkpoint}


NORMAL_STOPS = {'record_cap','date_boundary','exhausted'}


def _initial_state():
    return {'records':[], 'seen_ids':[], 'seen_cursors':[], 'page_fingerprints':[],
            'cursor':None,'received_count':0,'duplicate_count':0,'overshoot_count':0,
            'out_of_window_count':0,'pages':0,'attempts':0,'sort_basis':None,
            'continuity_verified':True,'last_timestamp':None,'observed_start':None,
            'observed_end':None,'reason_codes':[],'elapsed_seconds':0,'stop_reason':None}


def collect_reviews(request: CollectionRequest, fetch_page: Callable, *, transform: Callable,
                    checkpoint: Callable | None = None, initial: dict | None = None,
                    guard: Callable | None = None, clock=time.monotonic, sleeper=time.sleep) -> CollectionResult:
    """Collect via fetch_page(cursor, attempt), with attempts numbered from one.

    The callback MUST budget every network attempt and preserve unknown charges.
    Checkpoints may be resumed without re-reading already committed pages. A
    transform is required so callers consciously choose their storage policy.
    """
    state = _initial_state()
    if initial:
        state.update(deepcopy(initial))
    if len(state['records']) > request.max_review_records or state['pages'] > request.max_pages:
        raise ValueError('Checkpoint exceeds current request bounds')
    began = clock()
    prior_elapsed = state['elapsed_seconds']
    start, end = parse_timestamp(request.requested_start),parse_timestamp(request.requested_end)

    def reason(code):
        if code not in state['reason_codes']:
            state['reason_codes'].append(code)

    def save():
        state['elapsed_seconds'] = prior_elapsed + max(0,clock()-began)
        if guard:
            guard()
        if checkpoint:
            checkpoint(deepcopy(state))

    def finish(stop):
        state['stop_reason'] = stop
        if stop not in NORMAL_STOPS:
            reason('PARTIAL_COLLECTION')
        save()
        coverage = {k:state[k] for k in ('received_count','duplicate_count','overshoot_count',
                    'out_of_window_count','pages','attempts','sort_basis','continuity_verified',
                    'observed_start','observed_end','stop_reason')}
        coverage.update(unique_count=len(state['records']), requested_count=request.max_review_records,
                        requested_start=request.requested_start,requested_end=request.requested_end,
                        partial=stop not in NORMAL_STOPS, collection_complete=stop in NORMAL_STOPS,
                        platform_census=False, page_kind='provider_page',
                        exhaustion_scope='provider_returned_window' if stop=='exhausted' else None)
        return CollectionResult(deepcopy(state['records']),coverage,list(state['reason_codes']),
                                'succeeded' if stop in NORMAL_STOPS else 'partial',deepcopy(state))

    if state['stop_reason']:
        return finish(state['stop_reason'])
    while True:
        if guard:
            guard()
        if prior_elapsed + clock()-began >= request.max_elapsed_seconds:
            return finish('time_cap')
        if len(state['records']) >= request.max_review_records:
            return finish('record_cap')
        if state['pages'] >= request.max_pages:
            return finish('page_cap')
        cursor = state['cursor']
        cursor_key = cursor if cursor is not None else '__first__'
        if cursor_key in state['seen_cursors']:
            reason('REPEATED_CURSOR')
            state['continuity_verified'] = False
            return finish('cursor_expired')
        page = None
        for attempt in range(1,request.max_attempts_per_page+1):
            if guard:
                guard()
            state['attempts'] += 1
            try:
                page = fetch_page(cursor,attempt)
                if not isinstance(page,ProviderPage):
                    raise ReviewProviderError('parse_error')
                break
            except ReviewProviderError as exc:
                reason(exc.code.upper())
                remaining = request.max_elapsed_seconds-(prior_elapsed+clock()-began)
                if exc.retryable and attempt < request.max_attempts_per_page and remaining > exc.retry_after:
                    # No SQLITE locks held here. Guard runs again before the retry.
                    if exc.retry_after:
                        sleeper(exc.retry_after)
                    continue
                state['continuity_verified'] = False
                stop = exc.code if exc.code in {'budget_cap','time_cap','provider_blocked','cursor_expired','parse_error'} else 'provider_error'
                return finish(stop)
        if page is None:
            return finish('provider_error')
        state['pages'] += 1
        state['seen_cursors'].append(cursor_key)
        state['received_count'] += len(page.records)
        if page.sort_basis not in ('published_at','edited_at'):
            reason('SORT_BASIS_UNKNOWN')
        if state['sort_basis'] is not None and state['sort_basis'] != page.sort_basis:
            reason('SORT_BASIS_CHANGED')
            state['continuity_verified'] = False
            return finish('parse_error')
        state['sort_basis'] = page.sort_basis
        state['continuity_verified'] = state['continuity_verified'] and page.continuity_verified
        if not page.continuity_verified:
            reason('CONTINUITY_UNVERIFIED')
        for limitation in page.limitations:
            reason(limitation)
        if any(not isinstance(record,dict) for record in page.records):
            reason('MALFORMED_RECORD')
            state['continuity_verified'] = False
            return finish('parse_error')
        ids = [str(record.get('provider_review_id') or '') for record in page.records]
        # Opaque ID-only fingerprint; never retains a digest of review bodies.
        fingerprint = hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest()
        if page.records and all(ids) and fingerprint in state['page_fingerprints']:
            reason('REPEATED_PAGE')
            state['continuity_verified'] = False
            return finish('cursor_expired')
        state['page_fingerprints'].append(fingerprint)
        boundary = False
        for index, record in enumerate(page.records):
            if not isinstance(record,dict):
                reason('MALFORMED_RECORD')
                return finish('parse_error')
            review_id = record.get('provider_review_id')
            if review_id and review_id in state['seen_ids']:
                state['duplicate_count'] += 1
                # Keep the first copy in the provider's observed ordering. A
                # later translation/edit is not independent evidence and is
                # not silently promoted over the frozen observation.
                reason('DUPLICATE_COPY_SKIPPED')
                continue
            if review_id:
                state['seen_ids'].append(review_id)
            else:
                # Different missing-ID records are never silently merged by body.
                reason('MISSING_STABLE_ID')
                reason('DEDUPE_UNVERIFIED')
                state['continuity_verified'] = False
            if len(state['records']) >= request.max_review_records:
                state['overshoot_count'] += len(page.records)-index
                break
            sort_basis = state['sort_basis']
            timestamp = parse_timestamp(record.get(sort_basis)) if sort_basis in ('published_at','edited_at') else None
            precision = record.get(sort_basis+'_precision',record.get('edited_date_precision' if sort_basis=='edited_at' else 'date_precision',record.get('date_precision','unknown')))
            precise = timestamp is not None and precision == 'exact'
            if sort_basis in ('published_at','edited_at') and not precise:
                reason('AMBIGUOUS_DATE')
                state['continuity_verified'] = False
            previous = parse_timestamp(state['last_timestamp'])
            if precise and previous and timestamp > previous:
                reason('SORT_INVERSION')
                state['continuity_verified'] = False
                return finish('parse_error')
            if precise:
                state['last_timestamp'] = timestamp.isoformat()
                if timestamp > end:
                    # Snapshot time is frozen; a later publication cannot enter it.
                    state['out_of_window_count'] += 1
                    continue
                if timestamp < start:
                    if state['continuity_verified']:
                        boundary = True
                        state['out_of_window_count'] += len(page.records)-index
                        break
                    reason('DATE_BOUNDARY_UNVERIFIED')
                    state['out_of_window_count'] += 1
                    continue
                stamp = timestamp.isoformat()
                if state['observed_start'] is None or stamp < state['observed_start']:
                    state['observed_start'] = stamp
                if state['observed_end'] is None or stamp > state['observed_end']:
                    state['observed_end'] = stamp
            state['records'].append(transform(record))
        state['cursor'] = page.next_cursor
        if boundary:
            return finish('date_boundary')
        if len(state['records']) >= request.max_review_records:
            return finish('record_cap')
        if page.exhausted and page.next_cursor is None:
            return finish('exhausted')
        if page.next_cursor is None or not page.records:
            reason('PAGE_CONTINUITY_GAP')
            state['continuity_verified'] = False
            return finish('cursor_expired')
        save()
