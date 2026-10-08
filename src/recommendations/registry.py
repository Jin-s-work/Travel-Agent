"""Versioned, untrained ordering policies. A section chooses once per run."""
from copy import deepcopy

VERSION = 'hybrid_v4'
MODELS = {
    'local_observed_general_v3': {'section': 'local_discovery', 'ordering_kind': 'rule',
        'order': ['local_lower:desc', 'korean_upper:asc', 'language_unknown:asc', 'canonical_place_id:asc'],
        'required_features': ['local_lower', 'korean_upper', 'language_unknown'], 'personalized': False},
    'iconic_general_v3': {'section': 'landmark', 'ordering_kind': 'evidence_groups',
        'order': ['evidence_group', 'platform_cohort', 'rating_count:desc', 'rating:desc', 'canonical_place_id:asc'],
        'required_features': ['iconic_evidence'], 'personalized': False},
    'reference_general_v3': {'section': 'reference', 'ordering_kind': 'rule',
        'order': ['nearby_profile_only:straight_distance:asc', 'canonical_place_id:asc'],
        'required_features': ['permitted_place_identity'], 'personalized': False},
}
FEATURES = {
    'local_lower': 'L/T; observed original-language window, not residency',
    'korean_upper': '(K+U)/T; unknown-language bounds, not a confidence interval',
    'language_unknown': 'U/T; extraction-unknown records are separate',
    'rating': 'permitted platform rating within the same scale',
    'rating_count': 'platform total ratings, never a language denominator',
    'iconic_evidence': 'official_landmark/platform_popular/editorial_recognition',
    'straight_distance': 'Haversine distance from explicit origin; not walking duration',
    'preference_match': 'explicit requested tags only; absent input is unspecified',
}
MODELS.update({name.replace('_general_v3', '_hybrid_v4'): {
    **deepcopy(value), 'ordering_kind': 'content_context_hybrid',
    'order': ['evidence_group', 'comparable_platform_cohort', 'explicit_nearby_if_requested',
              'utility_lower:desc', 'evidence_coverage:desc', 'canonical_place_id:asc'],
    'trained': False,
} for name, value in list(MODELS.items())})


def section_models(snapshot):
    # Explicit preference matching is not learned behavioral personalization.
    suffix = '_hybrid_v4' if snapshot.get('recommendation_model_version') == 'hybrid_v4' else '_general_v3'
    return {value['section']: name for name, value in MODELS.items() if name.endswith(suffix)}


def model_snapshot(snapshot, version=VERSION):
    selected = {**snapshot, 'recommendation_model_version': version}
    models = section_models(selected)
    result = {'recommendation_model_version': version, 'section_models': models,
            'model_registry': {k: deepcopy(MODELS[k]) for k in models.values()}, 'feature_dictionary_version': 'evidence_features_v3',
            'learning_status': 'insufficient_evidence', 'personalization_status': 'general_model',
            'model_selection_reason': 'UNSUPPORTED_PERSONALIZATION_PROFILE' if (snapshot.get('conditions',{}).get('preferred') or {}).get('tags') else 'OPTIONAL_INPUTS_UNSPECIFIED'}
    if version == 'hybrid_v4':
        from .hybrid import spec
        result.update(ranking_spec=spec(snapshot), personalization_status='explicit_context_model',
                      model_selection_reason='EXPLICIT_REQUEST_CONTEXT', feature_dictionary_version='content_context_v1')
    return result
