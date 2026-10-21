"""Stage 4 bundle D: explicit feedback scope, opt-in saved taste, nearest saved order.

Synthetic catalog and synthetic rubric grades only. No network, no provider call.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import pytest

from tests.test_foundation_api import service, _trip, _job
from tests.test_discovery_foundation import discovery, import_pack
from tests.test_recommendation_persistence import setup
from tests.test_product_feedback import event
from tests.discovery_synthetic import conditions
from tests.test_recommendation_engine import NOW, catalog, snapshot as engine_snapshot
from src.recommendations import saved_taste as T
from src.recommendations.feedback_loop import with_feedback_exclusions


def run(discovery, path, trip, key, **extra):
    body = {'trip_version': trip['version'], 'conditions_version': 1, 'rating_filter': {'enabled': False}, **extra}
    response = discovery.client.post(path + '/recommendations', json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    assert _job(discovery.client, response.json())['state'] == 'succeeded'
    return discovery.client.get(path + '/recommendations/' + response.json()['run_id']).json()


def placements(result, pid):
    return [(kind, group, item['reason_codes']) for kind, groups in result['result']['sections'].items()
            for group, items in groups.items() if isinstance(items, list) for item in items if item['place_id'] == pid]


def feedback(discovery, path, place, key, **body):
    payload = {'feedback_kind': 'preference', 'reason_codes': ['price'], **body}
    return discovery.client.post(path + '/places/' + place + '/feedback', json=payload, headers={'Idempotency-Key': key})


def new_trip(discovery, title='Tokyo 2'):
    trip = _trip(discovery.client, title)
    path = f"/api/v2/trips/{trip['id']}"
    assert discovery.client.patch(path + '/discovery-conditions', json={'expected_version': 0, 'conditions': conditions()}).status_code == 200
    return trip, path


# ------------------------------------------------------------------ D1 less / exclude / scope

def test_click_alone_cannot_exclude_or_reach_future_trips(discovery):
    pack, trip, path, _ = setup(discovery)
    place = pack['places'][0]['place_id']
    for extra in ({'preference_action': 'exclude'}, {'preference_scope': 'future'},
                  {'preference_action': 'exclude', 'preference_scope': 'future'}):
        assert feedback(discovery, path, place, 'bad-' + json.dumps(extra), **extra).status_code == 422
    visit = {'feedback_kind': 'visit', 'visit_status': 'not_visited', 'reflect_preference': False, 'preference_scope': 'future'}
    assert discovery.client.post(path + '/places/' + place + '/feedback', json=visit, headers={'Idempotency-Key': 'v'}).status_code == 422
    # A plain "관심 없음" record is stored but changes nothing.
    assert feedback(discovery, path, place, 'plain').status_code == 201
    latest = run(discovery, path, trip, 'plain-run')
    assert latest['snapshot']['soft_avoid_place_ids'] == [] and latest['snapshot']['feedback_excluded_place_ids'] == []
    assert latest['snapshot']['feedback_inputs']['learned'] is False
    assert latest['snapshot']['saved_taste'] == T.disabled()


def test_legacy_retry_keeps_idempotency_hash(discovery):
    pack, trip, path, _ = setup(discovery)
    place = pack['places'][0]['place_id']
    first = feedback(discovery, path, place, 'same', reflect_preference=True)
    assert first.status_code == 201, first.text
    again = feedback(discovery, path, place, 'same', reflect_preference=True, preference_action='less', preference_scope='this_trip')
    assert again.status_code == 201 and again.json()['id'] == first.json()['id']
    assert first.json()['preference_action'] == 'less' and first.json()['preference_scope'] == 'this_trip'
    assert feedback(discovery, path, place, 'same', reflect_preference=True, preference_action='exclude').status_code == 409


def test_less_and_exclude_this_trip_then_withdraw(discovery):
    pack, trip, path, _ = setup(discovery)
    less, out = pack['places'][0]['place_id'], pack['places'][1]['place_id']
    a = feedback(discovery, path, less, 'less', reflect_preference=True)
    b = feedback(discovery, path, out, 'out', reflect_preference=True, preference_action='exclude')
    assert a.status_code == b.status_code == 201
    latest = run(discovery, path, trip, 'after-choices')
    snap = latest['snapshot']
    assert snap['soft_avoid_place_ids'] == [less] and snap['feedback_excluded_place_ids'] == [out]
    assert snap['feedback_policy_version'] == 'soft_avoid_half_v1'
    assert snap['feedback_inputs']['this_trip_less'] == 1 and snap['feedback_inputs']['this_trip_exclude'] == 1
    where = placements(latest, out)
    assert where and all(group == 'excluded' and 'USER_EXCLUDED' in codes for _, group, codes in where)
    # Captured candidates are not rewritten, so data guards keep the run current.
    assert latest['data_status'] == 'current'
    with discovery.app.state.db.connect() as con:
        captured = json.loads(con.execute('SELECT candidates_json FROM recommendation_runs WHERE id=?', (latest['run_id'],)).fetchone()[0])
    assert next(c for c in captured if c['place_id'] == out)['excluded'] is False
    for item in (a.json(), b.json()):
        assert discovery.client.request('DELETE', path + '/feedback/' + item['id'], json={'expected_version': item['version']}).status_code == 200
    after = run(discovery, path, trip, 'after-withdraw')
    assert after['snapshot']['soft_avoid_place_ids'] == [] and after['snapshot']['feedback_excluded_place_ids'] == []
    assert not any(group == 'excluded' and 'USER_EXCLUDED' in codes for _, group, codes in placements(after, out))


def test_future_scope_applies_to_other_trips_until_withdrawn_or_trip_deleted(discovery):
    pack, trip, path, _ = setup(discovery)
    first, second = pack['places'][0]['place_id'], pack['places'][1]['place_id']
    only_here = feedback(discovery, path, first, 'here', reflect_preference=True, preference_action='exclude')
    future = feedback(discovery, path, second, 'future', reflect_preference=True, preference_action='exclude', preference_scope='future')
    assert only_here.status_code == future.status_code == 201
    trip2, path2 = new_trip(discovery)
    latest = run(discovery, path2, trip2, 'other-trip')
    assert latest['snapshot']['feedback_excluded_place_ids'] == [second]
    assert latest['snapshot']['feedback_inputs']['carried_from_other_trips'] == 1
    listed = discovery.client.get(path2 + '/feedback').json()
    assert listed['items'] == [] and [c['id'] for c in listed['carried_from_other_trips']] == [future.json()['id']]
    assert 'private_note' not in listed['carried_from_other_trips'][0]
    # Another user never sees or inherits this owner's choices.
    other = discovery.login('feedback-other')
    trip3 = _trip(other.client)
    assert other.client.get(path + '/feedback').status_code == 404
    with discovery.app.state.db.connect() as con:
        from src.recommendations.feedback_loop import feedback_inputs
        assert feedback_inputs(con, other.user['id'], trip3['id'])['feedback_excluded_place_ids'] == []
    # Withdrawal (on the trip that holds it) stops every later run.
    item = future.json()
    assert discovery.client.request('DELETE', path + '/feedback/' + item['id'], json={'expected_version': item['version']}).status_code == 200
    assert run(discovery, path2, trip2, 'other-trip-after')['snapshot']['feedback_excluded_place_ids'] == []
    # Deleting the trip that holds a future choice removes it as well.
    again = feedback(discovery, path, second, 'future-2', reflect_preference=True, preference_scope='future')
    assert again.status_code == 201
    assert run(discovery, path2, trip2, 'other-trip-3')['snapshot']['soft_avoid_place_ids'] == [second]
    assert discovery.client.delete(path).status_code == 202
    assert run(discovery, path2, trip2, 'other-trip-4')['snapshot']['soft_avoid_place_ids'] == []


def test_exclusion_helper_copies_and_ignores_empty():
    rows = catalog()
    assert with_feedback_exclusions({}, rows) is rows
    marked = with_feedback_exclusions({'feedback_excluded_place_ids': [rows[0]['place_id']]}, rows)
    assert marked[0]['excluded'] is True and 'excluded' not in rows[0] and marked[1] is rows[1]


# ------------------------------------------------------------------ D2 saved taste

def save(discovery, path, place):
    response = discovery.client.post(path + '/bookmarks', json={'input_kind': 'place', 'input_value': place, 'note': ''})
    assert response.status_code == 201, response.text
    assert response.json()['resolve_state'] == 'resolved'
    return response.json()


def test_saved_taste_is_off_by_default_and_explicit_opt_in_extends_input_only(discovery):
    pack, trip, path, _ = setup(discovery)
    save(discovery, path, pack['places'][0]['place_id'])  # approved pack tags: culture, noodles
    off = run(discovery, path, trip, 'taste-off')
    assert off['snapshot']['conditions']['preferred']['tags'] == ['noodles']
    assert off['snapshot']['saved_taste'] == T.disabled() and 'saved_taste' not in off['request']
    on = run(discovery, path, trip, 'taste-on', saved_taste=True)
    taste = on['snapshot']['saved_taste']
    assert taste['applied'] is True and taste['added_tags'] == ['culture'] and taste['typed_tags'] == ['noodles']
    assert on['snapshot']['conditions']['preferred']['tags'] == ['noodles', 'culture']
    assert taste['preference_weight_cap'] == .40 and taste['profile_after'] == 'taste_origin'
    assert on['snapshot']['ranking_spec']['profile'] == 'taste_origin'
    assert on['snapshot']['ranking_spec']['weights'] == off['snapshot']['ranking_spec']['weights']  # formula unchanged
    assert taste['seed_evidence'][0]['evidence'] == 'approved_pack_tags'
    # The opt-in is part of the request identity, so a reused key cannot silently switch arms.
    body = {'trip_version': trip['version'], 'conditions_version': 1, 'rating_filter': {'enabled': False}}
    assert discovery.client.post(path + '/recommendations', json=body, headers={'Idempotency-Key': 'taste-on'}).status_code == 409


def test_saved_taste_skips_soft_avoided_seed_and_intent_path_uses_same_builder(discovery):
    from tests.test_discovery_intents import trip_for, body as intent_body
    pack = import_pack(discovery)
    trip = trip_for(discovery.client)
    path = f"/api/v2/trips/{trip['id']}"
    place = pack['places'][4]['place_id']  # cafe, tags: art
    save(discovery, path, place)
    payload = intent_body(trip)
    payload['filters']['saved_taste'] = True
    response = discovery.client.post(path + '/discovery-intents', json=payload, headers={'Idempotency-Key': 'intent-taste-1'})
    assert response.status_code == 202, response.text
    _job(discovery.client, response.json())
    snap = discovery.client.get(path + '/recommendations/' + response.json()['run_id']).json()['snapshot']
    assert snap['saved_taste']['applied'] is True and snap['saved_taste']['added_tags'] == ['art']
    assert snap['conditions']['preferred']['tags'][-1] == 'art' and snap['feedback_scope_policy_version'] == 'trip_feedback_scope_v1'
    assert feedback(discovery, path, place, 'avoid-seed', reflect_preference=True).status_code == 201
    payload = intent_body(trip, cv=1)
    payload['filters']['saved_taste'] = True
    response = discovery.client.post(path + '/discovery-intents', json=payload, headers={'Idempotency-Key': 'intent-taste-2'})
    assert response.status_code == 202, response.text
    _job(discovery.client, response.json())
    snap = discovery.client.get(path + '/recommendations/' + response.json()['run_id']).json()['snapshot']
    assert snap['soft_avoid_place_ids'] == [place]
    assert snap['saved_taste']['applied'] is False and snap['saved_taste']['reason'] == 'NO_SAVED_PLACES'
    # Default intent: no opt-in.
    response = discovery.client.post(path + '/discovery-intents', json=intent_body(trip, cv=2), headers={'Idempotency-Key': 'intent-taste-3'})
    assert response.status_code == 202, response.text
    _job(discovery.client, response.json())
    assert discovery.client.get(path + '/recommendations/' + response.json()['run_id']).json()['snapshot']['saved_taste'] == T.disabled()


def test_saved_taste_caps_terms_and_records_profile_change():
    rows = catalog()
    for i, row in enumerate(rows):
        row['tags'] = [['ramen', 'sushi'], ['ramen', 'coffee'], ['ramen', 'tapas'], ['sushi', 'seafood'], ['pizza'], ['art']][i]
    base = engine_snapshot(evaluation_at=NOW.isoformat())
    base['conditions']['preferred']['tags'] = []
    base['conditions'].pop('origin')
    value, diag = T.apply(base, rows, NOW, seed_basis='test')
    # support: ramen 3, sushi 2, then ties by term; capped at three.
    assert diag['added_tags'] == ['ramen', 'sushi', 'art'] and len(diag['candidates_not_added']) == 4
    assert value['conditions']['preferred']['tags'] == ['ramen', 'sushi', 'art'] and base['conditions']['preferred']['tags'] == []
    assert (diag['profile_before'], diag['profile_after'], diag['preference_weight_cap']) == ('evidence', 'taste', .45)
    typed = deepcopy(base); typed['conditions']['preferred']['tags'] = ['라멘']  # alias of ramen
    _, diag = T.apply(typed, rows, NOW, seed_basis='test')
    assert 'ramen' not in diag['added_tags'] and diag['added_tags'][0] == 'sushi'
    _, empty = T.apply(base, [], NOW, seed_basis='test')
    assert empty['applied'] is False and empty['reason'] == 'NO_SAVED_PLACES'


def test_seed_tags_follow_v5_document_rule():
    rows = catalog()
    terms, kind, _ = T.seed_tags(rows[0], conditions()['visit'], NOW)
    assert kind == 'approved_pack_tags' and terms == ['culture', 'noodles']
    verified = deepcopy(rows[0])
    verified['facts'].append({**verified['facts'][0], 'id': 'tag_fact', 'field': 'tags', 'value': ['Sushi']})
    assert T.seed_tags(verified, conditions()['visit'], NOW)[:2] == (['sushi'], 'verified_tags')
    public = deepcopy(rows[0])
    public.update(pack_status='public_data', tags=['ignored'])
    public['facts'] = [{**rows[0]['facts'][0], 'id': 'map', 'field': 'public_map_tags', 'status': 'provisional',
                        'value': {'tags': {'cuisine': 'ramen;gyoza'}}}]
    assert T.seed_tags(public, conditions()['visit'], NOW)[:2] == (['gyoza', 'ramen'], 'public_cuisine_provisional')
    public['facts'][0]['usable'] = False  # unusable facts never seed a term
    assert T.seed_tags(public, conditions()['visit'], NOW)[0] == []


# ------------------------------------------------------------------ D3 nearest saved order

class Tripwire:
    """Stands in for a provider; any use fails the test."""
    def __init__(self, name):
        object.__setattr__(self, 'name', name)
    def __bool__(self):
        return True
    def __getattr__(self, attr):
        raise AssertionError(f'{self.name}.{attr} used by nearest saved order')


def no_providers(discovery, monkeypatch):
    import socket
    def blocked(*args, **kwargs):
        raise AssertionError('network access attempted')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(discovery.app.state.discovery, 'public_provider', Tripwire('public_provider'))
    monkeypatch.setattr(discovery.app.state.accommodations, 'geocoder', Tripwire('geocoder'))


def test_nearest_saved_order_is_straight_line_without_provider_calls(discovery, monkeypatch):
    pack, trip, path, _ = setup(discovery)
    saved = [save(discovery, path, pack['places'][i]['place_id']) for i in (5, 0, 3)]
    no_providers(discovery, monkeypatch)  # public map and geocoder fail on any use from here on
    recent = discovery.client.get(path + '/bookmarks').json()
    assert set(recent) == {'items'} and 'straight_line_m' not in recent['items'][0]
    anchored = discovery.client.get(path + '/bookmarks', params={'order': 'nearest', 'anchor': saved[0]['id']}).json()
    ids = [i['matched_place_id'] for i in anchored['items']]
    assert ids == [pack['places'][i]['place_id'] for i in (5, 3, 0)]
    distances = [i['straight_line_m'] for i in anchored['items']]
    assert distances[0] == 0 and distances == sorted(distances)
    assert anchored['order']['distance_basis'] == 'straight_line_not_walking_time' and anchored['order']['provider_calls'] == 0
    assert all(i['distance_basis'] == 'straight_line_not_walking_time' for i in anchored['items'])
    origin = discovery.client.get(path + '/bookmarks', params={'order': 'nearest'}).json()
    assert origin['order']['anchor_kind'] == 'trip_origin'
    assert [i['matched_place_id'] for i in origin['items']] == [pack['places'][i]['place_id'] for i in (0, 3, 5)]
    assert 'latitude' not in json.dumps(origin['order'])
    assert discovery.client.get(path + '/bookmarks', params={'order': 'nearest', 'anchor': 'missing'}).status_code == 404
    other = discovery.login('nearest-other')
    assert other.client.get(path + '/bookmarks', params={'order': 'nearest'}).status_code == 404


def test_nearest_without_anchor_keeps_saved_order_and_null_distance(discovery):
    pack = import_pack(discovery)
    trip = _trip(discovery.client)
    path = f"/api/v2/trips/{trip['id']}"
    for i in (0, 1):
        save(discovery, path, pack['places'][i]['place_id'])
    data = discovery.client.get(path + '/bookmarks', params={'order': 'nearest'}).json()
    assert data['order']['reason'] == 'ANCHOR_UNAVAILABLE' and data['order']['order'] == 'recent'
    assert [i['straight_line_m'] for i in data['items']] == [None, None]


def test_nearest_uses_resolved_origin_and_stops_after_stay_deleted(discovery, monkeypatch):
    from tests.test_discovery_intents import trip_for, body as intent_body
    from tests.test_accommodations_api import fake, create, resolved, base
    pack = import_pack(discovery)
    fake(discovery)
    trip = trip_for(discovery.client)
    stay = create(discovery.client, trip)
    found, _ = resolved(discovery, trip, stay)
    stay_path = base(trip) + '/' + stay['id']
    chosen = discovery.client.post(stay_path + '/select', json={'expected_version': found['version'], 'candidate_id': found['candidates'][0]['candidate_id']})
    assert chosen.status_code == 200, chosen.text
    chosen = chosen.json()
    path = f"/api/v2/trips/{trip['id']}"
    overrides = {'origin_selection': {'kind': 'accommodation', 'accommodation_id': stay['id'], 'expected_version': chosen['version']}}
    response = discovery.client.post(path + '/discovery-intents', json=intent_body(trip, overrides=overrides), headers={'Idempotency-Key': 'nearest-stay-1'})
    assert response.status_code == 202, response.text
    _job(discovery.client, response.json())
    for i in (0, 1):
        save(discovery, path, pack['places'][i]['place_id'])
    no_providers(discovery, monkeypatch)
    assert discovery.client.get(path + '/discovery-conditions').json()['origin_context']['status'] == 'ready'
    before = discovery.client.get(path + '/bookmarks', params={'order': 'nearest'}).json()
    assert before['order']['anchor_kind'] == 'trip_origin' and before['order']['with_distance'] == 2
    assert 'latitude' not in json.dumps(before['order'])
    deleted = discovery.client.delete(stay_path + '?expected_version=' + str(chosen['version']))
    assert deleted.status_code == 200, deleted.text
    context = discovery.client.get(path + '/discovery-conditions').json()
    assert context['origin_context']['status'] != 'ready'
    after = discovery.client.get(path + '/bookmarks', params={'order': 'nearest'}).json()
    # The deleted stay's coordinates are no longer used: same rule as recommendations.
    assert after['order']['anchor_kind'] == 'none' and after['order']['reason'] == 'ANCHOR_UNAVAILABLE'
    assert [i['straight_line_m'] for i in after['items']] == [None, None]


def test_resolved_origin_requires_ready_context():
    from src.discovery.saved_order import resolved_origin
    point = {'label': 'x', 'latitude': 1.0, 'longitude': 2.0, 'place_id': None}
    ready = {'validation': None, 'origin_context': {'status': 'ready'}, 'conditions': {'origin': point}}
    assert resolved_origin(ready) == point
    for status in ('unknown', 'unresolved', 'ambiguous'):
        assert resolved_origin({**ready, 'origin_context': {'status': status}}) is None
    assert resolved_origin({**ready, 'origin_context': None}) is None  # outdated context: enrich_origin not run
    assert resolved_origin({**ready, 'validation': {'code': 'TRIP_CHANGED'}}) is None
    assert resolved_origin({**ready, 'conditions': {'origin': {**point, 'latitude': None}}}) is None


# ------------------------------------------------------------------ D1 list = applied set, matched counts

def test_carried_list_equals_applied_set_despite_many_visit_records(discovery):
    from src.recommendations.feedback_loop import feedback_inputs
    pack, trip, path, _ = setup(discovery)
    future = feedback(discovery, path, pack['places'][1]['place_id'], 'future-keep', reflect_preference=True,
                      preference_action='exclude', preference_scope='future')
    assert future.status_code == 201, future.text
    with discovery.app.state.db.connect() as con:
        row = dict(con.execute('SELECT * FROM visit_feedback WHERE id=?', (future.json()['id'],)).fetchone())
        con.execute('BEGIN IMMEDIATE')
        for n in range(205):  # newer, non-applied visit records on the same trip
            con.execute('INSERT INTO visit_feedback(' + ','.join(row) + ') VALUES(' + ','.join('?' * len(row)) + ')',
                        tuple({**row, 'id': f'vf_bulk_{n:03d}', 'idempotency_key': f'bulk-{n}', 'context_key': f'bulk-{n}',
                               'payload_json': json.dumps({'feedback_kind': 'visit', 'visit_status': 'not_visited', 'reflect_preference': False}),
                               'created_at': '2099-01-01T00:00:00+00:00', 'updated_at': f'2099-01-01T00:{n // 60:02d}:{n % 60:02d}+00:00'}.values()))
        con.commit()
    trip2, path2 = new_trip(discovery)
    listed = discovery.client.get(path2 + '/feedback').json()['carried_from_other_trips']
    with discovery.app.state.db.connect() as con:
        owner = con.execute('SELECT owner_id FROM trips WHERE id=?', (trip2['id'],)).fetchone()[0]
        applied = feedback_inputs(con, owner, trip2['id'])
    assert [c['id'] for c in listed] == [future.json()['id']]
    assert sorted(c['place_id'] for c in listed) == applied['feedback_excluded_place_ids'] + applied['soft_avoid_place_ids']


def test_feedback_match_counts_only_this_runs_candidates():
    from src.recommendations.feedback_loop import feedback_match
    from src.product.events import _feedback_applied
    rows = catalog()
    ids = [r['place_id'] for r in rows]
    snap = {'soft_avoid_place_ids': [ids[0], 'tokyo-only'], 'feedback_excluded_place_ids': ['tokyo-other'],
            'saved_taste': {'applied': True, 'seed_evidence': [{'place_id': ids[1]}, {'place_id': 'saved-elsewhere'}]}}
    match = feedback_match(snap, rows)
    assert match == {'soft_avoid_in_candidates': 1, 'excluded_in_candidates': 0, 'soft_avoid_total': 2, 'excluded_total': 1,
                     'saved_seed_places_in_candidates': 1, 'saved_seed_rule': 'saved_places_kept_as_candidates'}
    far = {'soft_avoid_place_ids': ['tokyo-only'], 'feedback_excluded_place_ids': ['tokyo-other']}
    assert _feedback_applied(far, {'feedback_match': feedback_match(far, rows)}) is False
    assert _feedback_applied(snap, {'feedback_match': match}) is True
    assert _feedback_applied(far, {}) is True  # runs stored before feedback_match keep the old flag


def test_run_result_records_feedback_match_and_saved_seed_rule(discovery):
    pack, trip, path, _ = setup(discovery)
    save(discovery, path, pack['places'][0]['place_id'])
    on = run(discovery, path, trip, 'match-on', saved_taste=True)
    match = on['result']['feedback_match']
    assert match['saved_seed_rule'] == 'saved_places_kept_as_candidates'
    with discovery.app.state.db.connect() as con:
        captured = {c['place_id'] for c in json.loads(con.execute('SELECT candidates_json FROM recommendation_runs WHERE id=?', (on['run_id'],)).fetchone()[0])}
    assert match['saved_seed_places_in_candidates'] == int(pack['places'][0]['place_id'] in captured)


# ------------------------------------------------------------------ D4 measurement

def test_offline_comparison_uses_development_seeds_only():
    from tests.test_v6_labeling import queries_doc, catalog_doc
    from scripts.prepare_v6_label_queries import PLAN
    from src.recommendations import labeling as L
    pool = L.build_pool(queries_doc(), catalog_doc(), plan=json.loads(PLAN.read_text()), created_at=NOW.isoformat())
    labels = L.new_labels(pool, 'R1')
    for query in pool['queries']:
        row = L.start_query(labels, query)
        for n, pid in enumerate(query['blind_order']):
            L.record(row, pid, n % 4)  # synthetic rubric by blind position, not a model feature
        L.finish_query(row, query)
    seeds = T.development_seeds(pool, labels)
    assert seeds and all(isinstance(v, list) for v in seeds.values())
    changed = deepcopy(labels)
    for qid, row in changed['queries'].items():
        if row['split'] == 'holdout':
            row['judgments'] = {pid: 3 for pid in row['judgments']}
    assert T.development_seeds(pool, changed) == seeds  # holdout grades never pick seeds
    report = T.offline_comparison(pool, labels)
    assert report['label_source'] == 'synthetic_rubric' and report['model'] == 'hybrid_v5'
    assert report['comparable_queries'] + len(report['skipped']) == report['holdout_queries']
    assert report['comparable_queries'] > 0
    assert report['estimate_scope'] == 'non_saved_places_only'
    assert report['top5_changed_paired'] + report['top5_changed_coverage_insufficient'] <= report['top5_changed_queries']
    for row in report['queries']:
        seeds_city = set(seeds[row['city']])
        assert not seeds_city & set(row['on']['top_ids']) and not seeds_city & set(row['off']['top_ids'])
        for arm in ('on', 'off'):
            metrics = row[arm]['metrics']
            assert metrics['ndcg'] is None or not metrics['coverage_insufficient']
    # OFF-only pool: changed queries drop out, so the paired subset cannot be read as an effect.
    assert report['top5_changed_queries'] > 0 and report['top5_changed_unjudged_share'] > T.MAX_CHANGED_UNJUDGED_SHARE
    assert report['decision'] == 'UNMEASURED_COVERAGE' and report['ndcg_delta_on_minus_off'] is None
    assert report['diagnostic_only']['ndcg_delta_paired_subset']['paired_queries'] == report['paired_queries']
    assert all(v['ndcg_delta_on_minus_off'] is None for v in report['by_profile_change'].values())
    assert sum(v['paired_queries'] for v in report['by_profile_change'].values()) == report['paired_queries']
    small = T.offline_comparison(pool, labels, max_changed_unjudged_share=1.0)
    assert small['decision'] == ('UNMEASURED_SAMPLE' if 0 < small['paired_queries'] < T.MIN_PAIRED_QUERIES else small['decision'])
    loose = T.offline_comparison(pool, labels, max_changed_unjudged_share=1.0, min_paired=1)
    assert loose['decision'] == 'MEASURED_NOT_A_PROMOTION_RULE' and loose['diagnostic_only'] is None
    assert loose['ndcg_delta_on_minus_off']['paired_queries'] == loose['paired_queries']
    assert loose['ndcg_delta_on_minus_off']['iterations'] == 2000 and loose['ndcg_delta_on_minus_off']['seed'] == 20261008
    # The precondition list names ON top-10 places nobody judged; nothing is labelled by code.
    gaps = T.on_pool_gaps(pool, labels)
    assert gaps['queries_with_gaps'] > 0 and gaps['depth'] == T.ON_POOL_DEPTH
    for row in gaps['queries']:
        judged = set(labels['queries'][row['id']]['judgments'])
        assert row['unjudged_on_top_ids'] and not judged & set(row['unjudged_on_top_ids'])


def test_online_effect_is_consented_only_and_small_samples_are_unmeasured(discovery):
    from src.product.feedback_effect import online_effect
    from src.product.events import capture_run
    assert discovery.client.patch('/api/v2/product-preferences', json={'expected_version': 0, 'analytics_enabled': True}).status_code == 200
    pack, trip, path, _ = setup(discovery)
    save(discovery, path, pack['places'][0]['place_id'])
    on = run(discovery, path, trip, 'effect-on', saved_taste=True)
    off = run(discovery, path, trip, 'effect-off')
    with discovery.app.state.db.connect() as con:
        summaries = {r['run_id']: json.loads(r['summary_json']) for r in con.execute('SELECT * FROM product_run_metrics')}
    assert summaries[on['run_id']]['saved_taste_applied'] is True and summaries[off['run_id']]['saved_taste_applied'] is False
    with discovery.app.state.db.connect() as con:
        shared = set.intersection(*[{c['place_id'] for c in json.loads(con.execute('SELECT candidates_json FROM recommendation_runs WHERE id=?', (r['run_id'],)).fetchone()[0])} for r in (on, off)])
    place = sorted(shared)[0]
    for current in (on, off):
        assert discovery.client.post(path + '/events', json=event(current['run_id'], place)).status_code == 200
    start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    small = online_effect(discovery.app.state.db, start=start, end=end, synthetic=True)
    for arm in small['arms']['saved_taste'].values():
        assert arm['exposure_to_save']['display'] == '미측정' and arm['exposure_to_save']['ratio'] is None
    measured = online_effect(discovery.app.state.db, start=start, end=end, synthetic=True, min_exposures=1, min_runs=1)
    assert measured['arms']['saved_taste']['on']['exposures'] == 1 and measured['arms']['saved_taste']['on']['exposure_to_save']['ratio'] == 0
    real = online_effect(discovery.app.state.db, start=start, end=end, synthetic=False, min_exposures=1, min_runs=1)
    assert real['arms']['saved_taste']['on']['completed_runs'] == 0  # synthetic runs never enter the real cohort
    assert discovery.client.patch('/api/v2/product-preferences', json={'expected_version': 1, 'analytics_enabled': False}).status_code == 200
    revoked = online_effect(discovery.app.state.db, start=start, end=end, synthetic=True, min_exposures=1, min_runs=1)
    assert all(a['completed_runs'] == 0 for arms in revoked['arms'].values() for a in arms.values())
    report = discovery.client.get('/api/v2/admin/product-report?synthetic=true').json()
    assert report['feedback_loop_effect']['cohort'] == 'analytics_consented_only'
