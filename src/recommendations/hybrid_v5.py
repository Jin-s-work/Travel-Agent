"""Content/context ranker v5. Local, deterministic, untrained and explainable.

Eligibility is owned by general.py. Unknown components stay null; their weights
are not redistributed. Lower/upper scores are evidence bounds, not probabilities.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from math import log, sqrt
import unicodedata
import re

VERSION = 'hybrid_v5'
PARAMETERS = {'rating_prior_count': 200, 'distance_scale_m': 2000,
              'feature_version': 'content_context_v2', 'tag_normalization': 'nfkc_alias_v2'}
PROFILES = {
    'evidence': {'core': 1.0},
    'taste': {'core': .55, 'preference': .45},
    'origin': {'core': .65, 'proximity': .35},
    'taste_origin': {'core': .40, 'preference': .40, 'proximity': .20},
}
# Explicit taxonomy only: no machine translation or guessed dietary suitability.
ALIASES = {'라멘': 'ramen', 'ラーメン': 'ramen', '라면': 'ramen',
           '스시': 'sushi', '초밥': 'sushi', '寿司': 'sushi',
           '커피': 'coffee', 'コーヒー': 'coffee', 'café': 'coffee',
           '미술': 'art', '예술': 'art', '박물관': 'museum', '문화': 'culture',
           '국수': 'noodles', '타파스': 'tapas', '휴식': 'relax'}

ALIASES.update({'해산물':'seafood','marisco':'seafood','mariscos':'seafood','생선':'fish',
    '피자':'pizza','파스타':'pasta','이탈리아':'italian','이탈리안':'italian',
    '프랑스':'french','프렌치':'french','일식':'japanese','일본식':'japanese',
    '스페인':'spanish','카탈루냐':'catalan','크레페':'crepe','crêpe':'crepe',
    'crepes':'crepe','crêpes':'crepe','스테이크':'steak','steak_house':'steak',
    '빵':'bakery','베이커리':'bakery','디저트':'dessert','아이스크림':'ice_cream',
    '카페':'coffee','cafe':'coffee','샌드위치':'sandwich','버거':'burger'})

def terms(values):
    normalized = {unicodedata.normalize('NFKC', x).strip().casefold()
                  for x in values if isinstance(x, str) and x.strip()}
    return {ALIASES.get(part, part) for x in normalized for raw in re.split(r'[,;/、]+', x) if (part := raw.strip())}


def profile(snapshot):
    conditions = snapshot['conditions']
    taste = bool(terms((conditions.get('preferred') or {}).get('tags') or []))
    origin = bool(conditions.get('origin') or snapshot.get('ranking_reference_origin'))
    return 'taste_origin' if taste and origin else 'taste' if taste else 'origin' if origin else 'evidence'


def spec(snapshot):
    key = profile(snapshot)
    return {'version': VERSION, 'profile': key, 'weights': deepcopy(PROFILES[key]),
            'parameters': deepcopy(PARAMETERS), 'trained': False,
            'selection': 'explicit_request_context', 'missing': 'null_no_redistribution'}


def cosine(query, document, corpus):
    """Binary TF, smoothed IDF log((1+n)/(1+df))+1, L2 cosine."""
    if not query or not document:
        return None
    frequency = Counter(token for row in corpus for token in row)
    idf = {token: log((1 + len(corpus)) / (1 + frequency[token])) + 1
           for token in query | document}
    numerator = sum(idf[t] ** 2 for t in sorted(query & document))
    denominator = sqrt(sum(idf[t] ** 2 for t in sorted(query)) * sum(idf[t] ** 2 for t in sorted(document)))
    return min(1.0, numerator / denominator)


def prepare(items, sources, snapshot, current):
    """Fit transient corpus statistics only on qualified candidates before top-k.

    No feedback training, no provider calls, no PII persisted in model diagnostics.
    """
    from .engine import Facts, _rating
    query = terms((snapshot['conditions'].get('preferred') or {}).get('tags') or [])
    by_id = {s['place_id']: s for s in sources}
    documents, ratings, cohorts, tag_evidence = {}, {}, defaultdict(list), {}
    for item in items:
        ident = item['place_id']; source = by_id[ident]
        facts = Facts(source, snapshot['conditions']['visit'], current)
        tags, tag_rows, _ = facts.get('tags')
        if not isinstance(tags, list):
            has_tag_fact = any(row.get('field') == 'tags' for row in source.get('facts', []))
            tags = source.get('tags') if not has_tag_fact and source.get('pack_status') == 'approved' and facts.sources else []
        tag_evidence[ident] = {'kind':'verified_tags', 'source_ids': facts.refs(tag_rows)}
        if not tags and source.get('pack_status') == 'public_data' and not any(r.get('field') == 'tags' for r in source.get('facts', [])):
            # Public cuisine is a soft preference hint, never a dietary/visit fact.
            rows = [r for r in source.get('facts', []) if r.get('field') == 'public_map_tags' and facts.permitted(r)]
            if rows and all(r.get('status') == 'provisional' for r in rows):
                cuisines = [(r.get('value') or {}).get('tags', {}).get('cuisine') for r in rows]
                if all(isinstance(c, str) for c in cuisines) and len(set(cuisines)) == 1:
                    tags = cuisines[0].split(';')
                    tag_evidence[ident] = {'kind':'public_cuisine_provisional', 'source_ids': facts.refs(rows)}
        documents[ident] = terms(tags or [])
        value, state, _, refs = _rating(facts, {'min_rating': 0, 'min_count': 1})
        if state == 'confirmed':
            cohort = (value['platform'], item['city'], item['category'], value['scale'])
            ratings[ident] = (value, refs, cohort)
            cohorts[cohort].append(value['rating'])
    corpus = list(documents.values())
    configuration = spec(snapshot)
    corpus_hash = sha256(json.dumps({k: sorted(v) for k, v in sorted(documents.items())},
                                    sort_keys=True).encode()).hexdigest()
    for item in items:
        ident = item['place_id']; features = item['features']; kind = item['recommendation_type']
        quality = None; rating_meta = None
        if ident in ratings:
            rating, refs, cohort = ratings[ident]
            prior = sum(cohorts[cohort]) / len(cohorts[cohort])
            count = rating['total_rating_count']; strength = PARAMETERS['rating_prior_count']
            adjusted = (count * rating['rating'] + strength * prior) / (count + strength)
            quality = adjusted / rating['scale']
            rating_meta = {'adjusted_rating': adjusted, 'raw_rating': rating['rating'],
                           'count': count, 'prior_mean': prior, 'prior_count': strength,
                           'cohort': list(cohort), 'cohort_places': len(cohorts[cohort]), 'source_ids': refs}
        # The evidence types are separate groups, never a synthetic universal popularity score.
        if kind == 'local_discovery':
            language = features.get('local_lower', {}).get('value')
            core = .7 * language + .3 * quality if language is not None and quality is not None else None
        elif kind == 'landmark' and item['iconic_kind'] == 'platform_popular':
            core = quality
        else:
            core = 1.0  # identity/editorial qualification indicator, not venue quality
        distance = features['straight_distance']['value']
        distance_basis = 'explicit_origin'
        anchor = snapshot.get('ranking_reference_origin')
        if not snapshot['conditions'].get('origin') and anchor:
            from .engine import straight_line_distance
            source = by_id[ident]
            distance = straight_line_distance(anchor, source)
            distance_basis = 'city_center'
        if distance is None: distance_basis = 'unknown'
        values = {'core': core, 'preference': cosine(query, documents[ident], corpus),
                  'proximity': 1 / (1 + distance / PARAMETERS['distance_scale_m']) if distance is not None else None}
        components = {key: {'value': values[key], 'weight': weight,
                            'contribution': values[key] * weight if values[key] is not None else None}
                      for key, weight in configuration['weights'].items()}
        missing = [key for key, value in components.items() if value['value'] is None]
        lower = sum(v['contribution'] for v in components.values() if v['contribution'] is not None)
        unknown_weight = sum(components[key]['weight'] for key in missing)
        soft_avoid = ident in snapshot.get('soft_avoid_place_ids', [])
        multiplier = .5 if soft_avoid else 1.0
        lower *= multiplier; upper = (lower + unknown_weight * multiplier)
        # Keep the legacy venue-score contract null. This contextual utility is
        # not a complete venue-quality score, particularly for public references
        # and facts-only official places. Expose it in named diagnostics instead.
        item.update(score=None, score_complete=False)
        item['ranking_diagnostics'] = {**deepcopy(configuration), 'components': components,
            'utility': round(lower, 6) if not missing else None, 'complete': not missing,
            'core_kind': 'language_quality' if kind == 'local_discovery' else 'platform_quality'
                if kind == 'landmark' and item['iconic_kind'] == 'platform_popular' else 'evidence_gate_only',
            'score_lower': round(lower, 6), 'score_upper': round(upper, 6),
            'evidence_coverage': round(1 - unknown_weight, 6), 'missing_components': missing,
            'rating': rating_meta, 'content_corpus_hash': corpus_hash, 'corpus_size': len(corpus),
            'matched_tags': sorted(query & documents[ident]), 'tag_evidence': tag_evidence[ident],
            'proximity_reference': distance_basis, 'proximity_distance_m': distance, 'soft_avoid_multiplier': multiplier,
            'distance_basis': 'straight_line_not_walking_time', 'score_meaning': 'ranking_utility_not_probability'}
        from .explanations import render
        item['supported_reasons'] = render(item)
        item['reason_sentences'] = [{k: v for k, v in r.items() if k != 'code'} for r in item['supported_reasons']]


def order_key(item):
    diagnostics = item['ranking_diagnostics']
    prefix = ()
    if item['recommendation_type'] == 'landmark':
        kind = item['iconic_kind']
        cohort = (diagnostics.get('rating') or {}).get('cohort', ['', '', '', '']) if kind == 'platform_popular' else ['', '', '', '']
        prefix = ({'official_landmark': 0, 'platform_popular': 1, 'editorial_recognition': 2}[kind], *map(str, cohort))
    if item['ordering_profile'] == 'nearby':
        distance = item['features']['straight_distance']['value']
        prefix += (distance is None, distance if distance is not None else 0)
    # Unknown evidence never gets a neutral/positive imputed contribution.
    return (*prefix, -diagnostics['score_lower'], -diagnostics['evidence_coverage'], item['place_id'])
