"""Versioned, untrained ordering policies. A section chooses once per run."""
from copy import deepcopy

VERSION = 'general_v3'
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

def section_models(snapshot):
    # v3 only supports explicit general models. Having preferences is not enough
    # evidence to claim that an untrained personalized model has been evaluated.
    return {value['section']: name for name, value in MODELS.items()}


def model_snapshot(snapshot):
    return {'recommendation_model_version': VERSION, 'section_models': section_models(snapshot),
            'model_registry': deepcopy(MODELS), 'feature_dictionary_version': 'evidence_features_v3',
            'learning_status': 'insufficient_evidence', 'personalization_status': 'general_model',
            'model_selection_reason': 'UNSUPPORTED_PERSONALIZATION_PROFILE' if (snapshot.get('conditions',{}).get('preferred') or {}).get('tags') else 'OPTIONAL_INPUTS_UNSPECIFIED'}
