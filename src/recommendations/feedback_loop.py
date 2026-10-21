"""Stage 4 D1/D2: explicit feedback inputs frozen into a recommendation snapshot.

One function builds the feedback part of a snapshot for both submission paths
(direct recommendation request and discovery intent) so they cannot drift.

* Only feedback the person explicitly chose to reflect (``reflect_preference``)
  is used. A plain "관심 없음" record changes nothing.
* ``preference_action``: ``less`` keeps the existing soft avoid (x0.5 in the
  unchanged v5 formula); ``exclude`` marks the candidate as user-excluded for
  this run (the existing USER_EXCLUDED hard check), applied to a copy at
  scoring time so captured candidates and data guards stay untouched.
* ``preference_scope``: ``this_trip`` (default) or ``future``. Future-scoped
  rows of the owner's other, non-deleted trips also apply. Deleting the trip
  that holds the record deletes the record (existing trip scrub), and
  withdrawing it stops every later run from using it. Neither one rewrites
  runs that already happened: a future-scoped place_id copied into another
  trip's ``recommendation_runs.snapshot_json`` (``soft_avoid_place_ids`` /
  ``feedback_excluded_place_ids``) stays there as that run's input until
  that other trip is deleted.
* Saved-place taste is OFF unless the request sets ``saved_taste: true``.

No provider is called and nothing here learns from behaviour.
"""
from copy import deepcopy
import json

SCOPE_POLICY_VERSION = 'trip_feedback_scope_v1'


def _reflected(payload):
    return bool(payload.get('feedback_kind') == 'preference' and payload.get('reflect_preference'))


def _owner_rows(con, owner_id):
    return con.execute(
        'SELECT f.* FROM visit_feedback f JOIN trips t ON t.id=f.trip_id '
        'WHERE f.owner_id=? AND f.withdrawn_at IS NULL AND t.deleted_at IS NULL AND t.owner_id=? '
        'ORDER BY f.created_at,f.id', (owner_id, owner_id)).fetchall()


def carried_rows(con, owner_id, trip_id):
    """Rows of the owner's other trips that ``feedback_inputs`` applies to ``trip_id``.

    The feedback list shows exactly this set (no separate LIMIT), so every
    applied future-scoped choice can be seen and withdrawn.
    """
    output = []
    for row in _owner_rows(con, owner_id):
        payload = json.loads(row['payload_json'] or '{}')
        if row['trip_id'] != trip_id and _reflected(payload) and payload.get('preference_scope', 'this_trip') == 'future':
            output.append(row)
    return output


def feedback_inputs(con, owner_id, trip_id):
    """Return snapshot keys for the current trip. Counts only; no other trip ids."""
    less, exclude = set(), set()
    counts = {'this_trip_less': 0, 'this_trip_exclude': 0, 'future_less': 0, 'future_exclude': 0}
    carried = 0
    for row in _owner_rows(con, owner_id):
        payload = json.loads(row['payload_json'] or '{}')
        if not _reflected(payload):
            continue
        scope = payload.get('preference_scope', 'this_trip')
        action = payload.get('preference_action', 'less')
        if row['trip_id'] != trip_id:
            if scope != 'future':
                continue
            carried += 1
        (exclude if action == 'exclude' else less).add(row['place_id'])
        counts[('future_' if scope == 'future' else 'this_trip_') + ('exclude' if action == 'exclude' else 'less')] += 1
    less -= exclude  # an exclusion already removes the place; no double effect
    return {
        'soft_avoid_place_ids': sorted(less),
        'feedback_excluded_place_ids': sorted(exclude),
        'feedback_scope_policy_version': SCOPE_POLICY_VERSION,
        'feedback_inputs': {**counts, 'carried_from_other_trips': carried,
                            'soft_avoid_places': len(less), 'excluded_places': len(exclude),
                            'source': 'explicit_user_choice', 'learned': False},
    }


def with_feedback_exclusions(snapshot, candidates):
    """Copies with ``excluded`` set for explicitly excluded places; inputs unchanged."""
    ids = set(snapshot.get('feedback_excluded_place_ids') or [])
    if not ids:
        return candidates
    output = []
    for candidate in candidates:
        if candidate['place_id'] in ids and not candidate.get('excluded'):
            candidate = {**deepcopy(candidate), 'excluded': True}
        output.append(candidate)
    return output


SEED_RULE = 'saved_places_kept_as_candidates'


def feedback_match(snapshot, candidates):
    """Counts of frozen choices that actually meet this run's candidates.

    ``feedback_inputs`` counts every reflected choice (a future-scoped choice
    for a Tokyo place is also counted on a Barcelona trip). The indicator and
    the online arm flag use these matched counts so a choice with no effect on
    this run is not shown as applied.

    Saved-place taste keeps saved places as candidates (``SEED_RULE``): a saved
    place may score higher from its own tags in a real run. The offline
    comparison must drop seeds to avoid label leakage, so it estimates the
    effect on non-saved places only; ``saved_seed_places_in_candidates`` records
    how many real candidates are outside that estimate.
    """
    ids = {c['place_id'] for c in candidates}
    less = set(snapshot.get('soft_avoid_place_ids') or [])
    out = set(snapshot.get('feedback_excluded_place_ids') or [])
    taste = snapshot.get('saved_taste') or {}
    seeds = {s['place_id'] for s in taste.get('seed_evidence') or []} if taste.get('applied') else set()
    return {'soft_avoid_in_candidates': len(less & ids), 'excluded_in_candidates': len(out & ids),
            'soft_avoid_total': len(less), 'excluded_total': len(out),
            'saved_seed_places_in_candidates': len(seeds & ids), 'saved_seed_rule': SEED_RULE}


def saved_seed_sources(con, discovery, owner_id, trip_id, city, skip):
    """Saved, resolved places of this trip in this city as permitted catalog rows."""
    from .saved_taste import MAX_SEEDS
    rows = con.execute(
        "SELECT b.matched_place_id FROM bookmarks b JOIN place_identities p ON p.id=b.matched_place_id "
        "WHERE b.owner_id=? AND b.trip_id=? AND b.deleted_at IS NULL AND b.resolve_state='resolved' "
        "AND p.city=? AND p.deleted_at IS NULL ORDER BY b.created_at DESC,b.id", (owner_id, trip_id, city)).fetchall()
    excluded = {r[0] for r in con.execute('SELECT place_id FROM discovery_exclusions WHERE trip_id=?', (trip_id,))}
    ids = []
    for row in rows:
        ident = row[0]
        if ident in skip or ident in excluded or ident in ids:
            continue
        ids.append(ident)
        if len(ids) >= MAX_SEEDS:
            break
    if not ids:
        return []
    # Same permission gates as the recommendation catalog (available facts only).
    return discovery._catalog_on(con, trip_id, city, include_photos=False, place_ids=ids, available_only=True)


def apply_to_snapshot(con, discovery, owner_id, trip_id, snapshot, *, saved_taste=False):
    """Mutate ``snapshot`` with feedback inputs and (opt-in) saved-place taste."""
    from datetime import datetime
    from .saved_taste import apply, disabled
    snapshot.update(feedback_inputs(con, owner_id, trip_id))
    if not saved_taste:
        snapshot['saved_taste'] = disabled()
        return snapshot
    skip = set(snapshot['soft_avoid_place_ids']) | set(snapshot['feedback_excluded_place_ids'])
    seeds = saved_seed_sources(con, discovery, owner_id, trip_id, snapshot['conditions']['city'], skip)
    updated, diagnostics = apply(snapshot, seeds, datetime.fromisoformat(snapshot['evaluation_at']), seed_basis='trip_saved_places')
    snapshot.clear()
    snapshot.update(updated)
    snapshot['saved_taste'] = diagnostics
    return snapshot
