"""Pure, conservative statistics for an observed review window.

No inference about the platform population, nationality or residence is made.
The bounds account for unclassified text only, not detector errors or sampling.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from fractions import Fraction

from .config import NORMAL_COMPLETION_REASONS, REVIEW_CONFIG_VERSION, ReviewThresholds, base_language

COUNT_FIELDS = (
    "observed_records", "rating_only_count", "text_count", "classified_count",
    "unknown_count", "extraction_unknown_count", "local_count", "korean_count",
)


def _result(decision: str, reasons: list[str], display_mode: str, metrics: dict | None,
            config_version: str = REVIEW_CONFIG_VERSION) -> dict:
    return {
        "decision": decision,
        "strict_pass": decision == "pass",
        "reason_codes": list(dict.fromkeys(reasons)),
        "display_mode": display_mode,
        "metrics": metrics,
        "config_version": config_version,
        "population_inference_allowed": False,
        "residency_inference_allowed": False,
        "scope_mode": "observed_window",
        "inference_method": "descriptive_unknown_bounds",
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return float(Fraction(numerator, denominator)) if denominator else None


def _language_sets(local_languages: object, korean_language: object) -> tuple[set[str], str]:
    if not isinstance(local_languages, (tuple, list, set, frozenset)) or not local_languages:
        raise ValueError("INVALID_LANGUAGE_CONFIGURATION")
    languages = [base_language(language) for language in local_languages]
    korean = base_language(korean_language)
    if any(language is None for language in languages) or korean != "ko":
        raise ValueError("INVALID_LANGUAGE_CONFIGURATION")
    if korean in languages:
        raise ValueError("OVERLAPPING_LANGUAGE_SETS")
    return set(languages), korean


def validate_counts(counts: Mapping, *, local_languages: object = ("ja",),
                    korean_language: object = "ko", counts_by_language: Mapping | None = None) -> list[str]:
    """Return machine-readable errors without coercing or repairing input."""
    reasons = []
    try:
        local, korean = _language_sets(local_languages, korean_language)
    except ValueError as exc:
        return [str(exc)]
    if not isinstance(counts, Mapping) or any(name not in counts for name in COUNT_FIELDS):
        return ["INVALID_COUNT"]
    values = [counts[name] for name in COUNT_FIELDS]
    if any(type(value) is not int for value in values):
        return ["INVALID_COUNT"]
    if any(value < 0 for value in values):
        return ["NEGATIVE_COUNT"]
    r, b, t, c, u, e, l, k = values
    if r != t + b + e or t != c + u or l + k > c:
        reasons.append("COUNT_INVARIANT_VIOLATION")
    if counts_by_language is not None:
        if not isinstance(counts_by_language, Mapping):
            return reasons + ["INVALID_LANGUAGE_COUNTS"]
        by_language: Counter = Counter()
        for tag, value in counts_by_language.items():
            language = base_language(tag)
            if language is None or type(value) is not int or value < 0:
                return reasons + ["INVALID_LANGUAGE_COUNTS"]
            by_language[language] += value
        if sum(by_language.values()) != c or sum(by_language[lang] for lang in local) != l or by_language[korean] != k:
            reasons.append("COUNT_INVARIANT_VIOLATION")
    return list(dict.fromkeys(reasons))


def aggregate_observations(observations: Iterable[Mapping], local_languages: object,
                           korean_language: str = "ko") -> dict:
    """Count already-deduplicated observations. Invalid classifications are rejected.

    Raw provider records must be normalized first. Text with unavailable original
    language contributes to T and U; extraction uncertainty contributes to E.
    """
    local, korean = _language_sets(local_languages, korean_language)
    counts = dict.fromkeys(COUNT_FIELDS, 0)
    by_language: Counter = Counter()
    identities = set()
    for observation in observations:
        if not isinstance(observation, Mapping):
            raise ValueError("INVALID_OBSERVATION")
        stable = observation.get("provider_review_id")
        if stable:
            identity = (observation.get("provider"), observation.get("place_id"), stable)
            if identity in identities:
                raise ValueError("DUPLICATE_REVIEW_ID")
            identities.add(identity)
        counts["observed_records"] += 1
        presence = observation.get("text_presence")
        language = base_language(observation.get("language"))
        if presence == "rating_only":
            if language:
                raise ValueError("RATING_ONLY_HAS_LANGUAGE")
            counts["rating_only_count"] += 1
        elif presence == "unextractable":
            if language:
                raise ValueError("UNEXTRACTABLE_HAS_LANGUAGE")
            counts["extraction_unknown_count"] += 1
        elif presence == "present":
            counts["text_count"] += 1
            if language:
                if observation.get("text_status") in {"translated_only", "mixed_unseparated", "truncated"}:
                    raise ValueError("UNVERIFIED_ORIGINAL_HAS_LANGUAGE")
                counts["classified_count"] += 1
                by_language[language] += 1
                counts["local_count"] += int(language in local)
                counts["korean_count"] += int(language == korean)
            else:
                counts["unknown_count"] += 1
        else:
            raise ValueError("INVALID_TEXT_PRESENCE")
    return {"counts": counts, "counts_by_language": dict(sorted(by_language.items()))}


def evaluate_review_signal(payload: Mapping) -> dict:
    """Apply policy/quality gates before strict observed-language thresholds.

    The input shape intentionally matches the versioned review-language fixture.
    Missing authorization, freshness or evaluation facts fail closed.
    """
    if not isinstance(payload, Mapping):
        return _result("invalid_input", ["INVALID_INPUT"], "none", None)
    try:
        thresholds = ReviewThresholds.from_mapping(payload.get("thresholds"))
    except (ValueError, TypeError):
        return _result("invalid_input", ["INVALID_THRESHOLD_CONFIGURATION"], "none", None)
    counts = payload.get("counts")
    errors = validate_counts(counts, local_languages=payload.get("local_languages"),
                             korean_language=payload.get("korean_language", "ko"),
                             counts_by_language=payload.get("counts_by_language"))
    if errors:
        return _result("invalid_input", errors, "none", None, thresholds.version)
    rights = payload.get("rights") or {}
    freshness = payload.get("freshness") or {}
    hidden_reasons = []
    if not isinstance(rights, Mapping) or any(rights.get(key) is not True for key in ("access_confirmed", "compute_confirmed", "display_confirmed")):
        hidden_reasons.append("RIGHTS_UNVERIFIED")
    if not isinstance(freshness, Mapping) or freshness.get("state") != "valid":
        hidden_reasons.append("OBSERVATION_EXPIRED" if isinstance(freshness, Mapping) and freshness.get("state") in {"expired", "stale"} else "FRESHNESS_UNVERIFIED")
    if hidden_reasons:
        return _result("unsupported", hidden_reasons, "none", None, thresholds.version)
    t, c, u, l, k = (counts[key] for key in ("text_count", "classified_count", "unknown_count", "local_count", "korean_count"))
    metrics = {
        "classified_local_share": _ratio(l, c),
        "classified_korean_share": _ratio(k, c),
        "unknown_share": _ratio(u, t),
        "local_share_lower_bound": _ratio(l, t),
        "local_share_upper_bound": _ratio(l + u, t),
        "korean_share_lower_bound": _ratio(k, t),
        "korean_share_upper_bound": _ratio(k + u, t),
    }
    reasons = []
    if payload.get("collection_mode") != "observed_window":
        reasons.append("POPULATION_DESIGN_UNSUPPORTED")
    if payload.get("place_identity_confirmed") is not True:
        reasons.append("PLACE_IDENTITY_UNVERIFIED")
    window = payload.get("window") or {}
    if not isinstance(window, Mapping):
        window = {}
    if window.get("sort") != "newest" or any(window.get(key) is not None for key in ("locale_filter", "keyword_filter", "rating_filter")):
        reasons.append("INELIGIBLE_SELECTION")
    if window.get("stop_reason") not in NORMAL_COMPLETION_REASONS or window.get("continuity_status") not in {"verified_supplier_window", "selected_sample"}:
        reasons.append("PARTIAL_COLLECTION")
    if window.get("continuity_status") == "selected_sample" and "INELIGIBLE_SELECTION" not in reasons:
        reasons.append("INELIGIBLE_SELECTION")
    ordering = window.get("ordering_semantics")
    if ordering not in {"published_at", "edited_at"}:
        reasons.append("ORDERING_SEMANTICS_UNVERIFIED")
    elif window.get("boundary_time_field") != ordering:
        reasons.append("TIME_BASIS_MISMATCH")
    if window.get("date_precision") in {"unknown", "relative", "month", "year"}:
        reasons.append("DATE_PRECISION_UNVERIFIED")
    if counts["extraction_unknown_count"]:
        reasons.append("UNEXTRACTABLE_RECORDS")
    quality = payload.get("quality") or {}
    if not isinstance(quality, Mapping) or any(quality.get(key) != "passed" for key in ("language_evaluation", "korean_recall_validation", "local_precision_validation")):
        reasons.append("CLASSIFICATION_QUALITY_UNVERIFIED")
    if not t:
        reasons.append("NO_TEXT_REVIEWS")
    elif not c:
        reasons.append("NO_CLASSIFIED_TEXTS")
    if c < thresholds.min_classified_texts:
        reasons.append("INSUFFICIENT_CLASSIFIED_TEXTS")
    if t and Fraction(u, t) > Fraction(str(thresholds.max_unknown_share)):
        reasons.append("TOO_MANY_UNKNOWN")
    if reasons:
        # Tiny samples only show counts; selected samples never gain a badge.
        mode = "counts_only" if c < 10 or not t else "reference_only"
        return _result("unsupported", reasons, mode, metrics, thresholds.version)
    failures = []
    if Fraction(l, t) < Fraction(str(thresholds.local_share_min)):
        failures.append("LOCAL_SHARE_BELOW_MIN")
    if Fraction(k + u, t) > Fraction(str(thresholds.korean_share_max)):
        failures.append("KOREAN_SHARE_ABOVE_MAX")
    return _result("fail" if failures else "pass", failures, "observed_statistics", metrics, thresholds.version)
