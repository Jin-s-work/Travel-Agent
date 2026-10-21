"""Stage 4 D2/D4: trip-scoped taste from saved places (default OFF) and its offline check.

The v5 formula is not touched. The only change is the *input*: permitted tags of
places the person saved for this trip are appended to
``conditions.preferred.tags`` when, and only when, the request opts in.

Tag rule = the hybrid_v5 document rule (``hybrid_v5.prepare``), copied rather
than changed: verified ``tags`` fact -> approved pack tags (when no tags fact
exists and a permitted source exists) -> single provisional public-map cuisine.
Nothing is inferred from names, reviews or behaviour.

Caps (recorded in the snapshot):
  * at most ``MAX_ADDED_TAGS`` new terms, ranked by how many seed places carry
    them (ties by term), never more than the ``Preferred.tags`` limit;
  * at most ``MAX_SEEDS`` most recent saved places of this trip and city;
  * the preference component weight stays the v5 profile weight
    (``hybrid_v5.PROFILES``: 0.45 taste / 0.40 taste_origin). Saved terms share
    that one component with typed terms, so their influence cannot exceed it.
    Recorded as ``preference_weight_cap``. When the person typed no taste, the
    opt-in switches the v5 profile from evidence/origin to taste/taste_origin;
    ``profile_before``/``profile_after`` record that.
"""
from collections import Counter
from copy import deepcopy

POLICY = 'saved_taste_v1'
MAX_ADDED_TAGS = 3
MAX_SEEDS = 20
PREFERRED_TAG_LIMIT = 30  # src.discovery.models.Preferred.tags max_length


def disabled():
    return {'policy': POLICY, 'requested': False, 'applied': False, 'reason': 'NOT_REQUESTED', 'added_tags': []}


def seed_tags(source, visit, current):
    """(terms, evidence kind, source ids) for one saved place, hybrid_v5 rule."""
    from .engine import Facts
    from .hybrid_v5 import terms
    facts = Facts(source, visit, current)
    tags, rows, _ = facts.get('tags')
    kind = 'verified_tags'
    refs = facts.refs(rows)
    if not isinstance(tags, list):
        has_tag_fact = any(row.get('field') == 'tags' for row in source.get('facts', []))
        tags = source.get('tags') if not has_tag_fact and source.get('pack_status') == 'approved' and facts.sources else []
        kind = 'approved_pack_tags' if tags else 'none'
        refs = sorted(facts.sources) if tags else []
    if not tags and source.get('pack_status') == 'public_data' and not any(r.get('field') == 'tags' for r in source.get('facts', [])):
        rows = [r for r in source.get('facts', []) if r.get('field') == 'public_map_tags' and facts.permitted(r)]
        if rows and all(r.get('status') == 'provisional' for r in rows):
            cuisines = [(r.get('value') or {}).get('tags', {}).get('cuisine') for r in rows]
            if all(isinstance(c, str) for c in cuisines) and len(set(cuisines)) == 1:
                tags, kind, refs = cuisines[0].split(';'), 'public_cuisine_provisional', facts.refs(rows)
    return sorted(terms(tags or [])), kind, refs


def apply(snapshot, seeds, current, *, seed_basis):
    """Return (new snapshot, diagnostics). ``snapshot`` itself is not modified."""
    from .hybrid_v5 import PROFILES, profile, terms
    value = deepcopy(snapshot)
    conditions = value['conditions']
    preferred = conditions.setdefault('preferred', {})
    typed = list(preferred.get('tags') or [])
    known = terms(typed)
    support, per_seed = Counter(), []
    for source in sorted(seeds, key=lambda row: row['place_id'])[:MAX_SEEDS]:
        found, kind, refs = seed_tags(source, conditions['visit'], current)
        per_seed.append({'place_id': source['place_id'], 'evidence': kind, 'terms': found, 'source_ids': refs})
        support.update(set(found))
    room = max(0, PREFERRED_TAG_LIMIT - len(typed))
    ranked = sorted((t for t in support if t not in known), key=lambda t: (-support[t], t))
    added = ranked[:min(MAX_ADDED_TAGS, room)]
    before = profile(snapshot)
    diagnostics = {'policy': POLICY, 'requested': True, 'seed_basis': seed_basis,
                   'seed_places': len(per_seed), 'seeds_with_terms': sum(bool(s['terms']) for s in per_seed),
                   'seed_evidence': per_seed, 'typed_tags': typed, 'added_tags': added,
                   'added_support': {t: support[t] for t in added},
                   'candidates_not_added': [t for t in ranked if t not in added],
                   'max_added_tags': MAX_ADDED_TAGS, 'max_seeds': MAX_SEEDS,
                   'profile_before': before, 'formula': 'hybrid_v5_unchanged_input_only'}
    if not added:
        diagnostics.update(applied=False, reason='NO_SAVED_PLACES' if not per_seed else 'NO_PERMITTED_SEED_TAGS',
                           profile_after=before, preference_weight_cap=PROFILES[before].get('preference'))
        return value, diagnostics
    preferred['tags'] = typed + added
    after = profile(value)
    diagnostics.update(applied=True, reason=None, profile_after=after,
                       preference_weight_cap=PROFILES[after].get('preference'))
    return value, diagnostics


# ---------------------------------------------------------------- offline ON/OFF (D4)

# Offline decision thresholds (descriptive gates, not promotion rules).
MIN_PAIRED_QUERIES = 20          # half of the 40 holdout queries in the label plan
MAX_CHANGED_UNJUDGED_SHARE = 0.2  # of queries whose top-5 the opt-in changed
ON_POOL_DEPTH = 10                # ON top-k that must be judged before measuring


def development_seeds(pool, labels, *, min_grade=2):
    """Per city: places a human graded >= min_grade in *development* queries only.

    Holdout judgments are never read here, so a holdout query cannot be seeded
    with its own relevant answers.
    """
    seeds = {}
    for query in pool['queries']:
        if query['split'] != 'development':
            continue
        row = labels['queries'].get(query['id'])
        if not row:
            continue
        for place, grade in row['judgments'].items():
            if grade >= min_grade:
                seeds.setdefault(query['city'], set()).add(place)
    return {city: sorted(ids) for city, ids in sorted(seeds.items())}


def _holdout_arms(pool, labels, *, view, version, min_grade):
    """Yield (query, row-or-skip) with OFF/ON full rankings, seeds removed."""
    from datetime import datetime
    from .labeling import RANKING_VIEWS, check_labels, model_result, section_ranking
    if view not in RANKING_VIEWS:
        raise ValueError(f'ranking view must be one of {sorted(RANKING_VIEWS)}')
    check_labels(pool, labels)
    seeds = development_seeds(pool, labels, min_grade=min_grade)
    for query in pool['queries']:
        if query['split'] != 'holdout':
            continue
        row = labels['queries'].get(query['id'])
        if not row:
            yield query, {'skip': 'UNLABELED'}; continue
        city_seeds = [i for i in seeds.get(query['city'], []) if i in pool['catalog']]
        if not city_seeds:
            yield query, {'skip': 'NO_DEVELOPMENT_SEEDS'}; continue
        snapshot = query['snapshot']
        current = datetime.fromisoformat(snapshot['evaluation_at'])
        on_snapshot, diagnostics = apply(snapshot, [pool['catalog'][i] for i in city_seeds], current,
                                         seed_basis='development_labels_offline')
        if not diagnostics['applied']:
            yield query, {'skip': diagnostics['reason']}; continue
        drop = set(city_seeds)
        candidates = [pool['catalog'][i] for i in query['candidate_ids']]
        rankings = {name: [i for i in section_ranking(model_result(value, candidates, version), query['section'], view) if i not in drop]
                    for name, value in (('off', snapshot), ('on', on_snapshot))}
        yield query, {'row': row, 'drop': drop, 'diagnostics': diagnostics, 'rankings': rankings, 'seed_count': len(city_seeds)}


def on_pool_gaps(pool, labels, *, view='displayed', version='hybrid_v5', min_grade=2, depth=ON_POOL_DEPTH):
    """Holdout places in the ON top-``depth`` that no human has judged yet.

    The label pool was frozen from OFF rankings only. Measuring the opt-in
    requires these places to be judged first (development labels -> seeds ->
    ON rankings -> extra holdout judgments). Nothing is labelled here.
    """
    gaps, total = [], 0
    for query, value in _holdout_arms(pool, labels, view=view, version=version, min_grade=min_grade):
        if 'skip' in value:
            continue
        judged = set(value['row']['judgments'])
        missing = [i for i in value['rankings']['on'][:depth] if i not in judged]
        total += len(missing)
        if missing:
            gaps.append({'id': query['id'], 'city': query['city'], 'section': query['section'], 'unjudged_on_top_ids': missing})
    return {'kind': 'saved_taste_on_pool_gaps', 'policy': POLICY, 'model': version, 'ranking_view': view, 'depth': depth,
            'queries_with_gaps': len(gaps), 'unjudged_places': total, 'queries': gaps,
            'note': 'Precondition for offline_comparison. AI does not label these places.'}


def _effect(rows):
    from .evaluation_v5 import bootstrap95
    return bootstrap95([r['on']['metrics']['ndcg'] - r['off']['metrics']['ndcg'] for r in rows])


def offline_comparison(pool, labels, *, view='displayed', version='hybrid_v5', min_grade=2,
                       min_paired=MIN_PAIRED_QUERIES, max_changed_unjudged_share=MAX_CHANGED_UNJUDGED_SHARE):
    """Holdout ON/OFF comparison of the saved-taste input on frozen human labels.

    * Seeds: development-only places (``development_seeds``), treated as the
      person's saved places for every holdout query of that city.
    * Seed places are removed from both rankings and from the judged pool. This
      is required (a development-relevant place could also be judged in a
      holdout query), so the offline estimate covers *non-saved* places only.
      Real runs keep saved places as candidates; see
      ``feedback_loop.feedback_match`` (``saved_seed_places_in_candidates``).
    * A query is comparable only when the opt-in adds at least one term.
    * Pooled metrics (k=5): unjudged candidates are never 0; an unjudged top-5
      marks the query coverage_insufficient and leaves it out of the paired test.
    * Paired bootstrap: ``evaluation_v5.bootstrap95`` (2,000 / seed 20261008).

    Because the pool was frozen from OFF rankings, a query whose top-5 the
    opt-in changed tends to be unjudged and drops out, leaving only unchanged
    queries (delta 0). Therefore the effect is reported only when
    * at most ``max_changed_unjudged_share`` of the changed queries are
      coverage-insufficient (else ``UNMEASURED_COVERAGE``), and
    * at least ``min_paired`` queries are paired (else ``UNMEASURED_SAMPLE``).
    Otherwise ``ndcg_delta_on_minus_off`` is null; the raw number stays in
    ``diagnostic_only`` and must not be read as "no effect".
    """
    from .benchmark import pooled_ranking_metrics
    from .labeling import K
    rows, skipped = [], []
    for query, value in _holdout_arms(pool, labels, view=view, version=version, min_grade=min_grade):
        if 'skip' in value:
            skipped.append({'id': query['id'], 'reason': value['skip']}); continue
        drop, diagnostics = value['drop'], value['diagnostics']
        pooled = [i for i in query['pooled_place_ids'] if i not in drop]
        judgments = {i: g for i, g in value['row']['judgments'].items() if i not in drop}
        arms = {name: {'top_ids': ranking[:K], 'metrics': pooled_ranking_metrics(ranking, judgments, pooled, k=K)}
                for name, ranking in value['rankings'].items()}
        rows.append({'id': query['id'], 'city': query['city'], 'section': query['section'],
                     'added_tags': diagnostics['added_tags'], 'profile_before': diagnostics['profile_before'],
                     'profile_after': diagnostics['profile_after'],
                     'profile_switched': diagnostics['profile_before'] != diagnostics['profile_after'],
                     'top5_changed': arms['on']['top_ids'] != arms['off']['top_ids'],
                     'seed_count': value['seed_count'], **arms})
    insufficient = lambda r: bool(r['on']['metrics']['coverage_insufficient'] or r['off']['metrics']['coverage_insufficient'])
    paired = [r for r in rows if r['on']['metrics']['ndcg'] is not None and r['off']['metrics']['ndcg'] is not None]
    changed = [r for r in rows if r['top5_changed']]
    changed_insufficient = sum(insufficient(r) for r in changed)
    changed_share = changed_insufficient / len(changed) if changed else None
    if not paired:
        decision, reason = 'UNMEASURED', 'NO_PAIRED_QUERIES'
    elif changed_share is not None and changed_share > max_changed_unjudged_share:
        decision, reason = 'UNMEASURED_COVERAGE', 'CHANGED_TOP5_UNJUDGED'
    elif len(paired) < min_paired:
        decision, reason = 'UNMEASURED_SAMPLE', 'PAIRED_BELOW_MINIMUM'
    else:
        decision, reason = 'MEASURED_NOT_A_PROMOTION_RULE', None
    measured = decision == 'MEASURED_NOT_A_PROMOTION_RULE'
    effect = _effect(paired)
    split = {name: [r for r in paired if r['profile_switched'] is flag] for name, flag in (('same_profile', False), ('profile_switched', True))}
    by_profile = {name: {'paired_queries': len(part), 'ndcg_delta_on_minus_off': _effect(part) if measured else None}
                  for name, part in split.items()}
    return {'kind': 'saved_taste_offline_comparison', 'policy': POLICY, 'model': version, 'ranking_view': view,
            'label_source': labels['label_source'], 'seed_rule': f'development grade >= {min_grade}, same city',
            'estimate_scope': 'non_saved_places_only',
            'holdout_queries': sum(q['split'] == 'holdout' for q in pool['queries']),
            'comparable_queries': len(rows), 'paired_queries': len(paired),
            'coverage_insufficient': sum(insufficient(r) for r in rows),
            'top5_changed_queries': len(changed), 'top5_changed_paired': sum(r['id'] in {p['id'] for p in paired} for r in changed),
            'top5_changed_coverage_insufficient': changed_insufficient,
            'top5_changed_unjudged_share': changed_share,
            'thresholds': {'min_paired_queries': min_paired, 'max_changed_unjudged_share': max_changed_unjudged_share},
            'ndcg_delta_on_minus_off': effect if measured else None,
            'by_profile_change': by_profile,
            'diagnostic_only': None if measured else {'ndcg_delta_paired_subset': effect,
                                                      'warning': 'Not an effect estimate: changed queries are missing or the sample is too small.'},
            'skipped': skipped, 'queries': rows, 'decision': decision, 'decision_reason': reason,
            'precondition': f'Judge ON top-{ON_POOL_DEPTH} holdout places first (on_pool_gaps).',
            'note': 'Default stays OFF. This comparison never changes the default model or promotion rules.'}
