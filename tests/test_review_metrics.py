"""Synthetic arithmetic contracts, not measured detector accuracy."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.research.config import CITY_LOCAL_LANGUAGES, ReviewThresholds, base_language
from src.research.metrics import aggregate_observations, evaluate_review_signal

FIXTURE = json.loads((Path(__file__).parents[1] / "docs/service-v2/examples/review-language-fixtures.json").read_text())


def merged(base, override):
    output = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = merged(output[key], value)
        else:
            output[key] = deepcopy(value)
    return output


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_all_24_review_contract_cases(case):
    payload = merged(FIXTURE["defaults"], case["input_overrides"])
    result = evaluate_review_signal(payload)
    expected = case["expected"]
    for field in ("decision", "strict_pass", "display_mode", "population_inference_allowed", "residency_inference_allowed"):
        assert result[field] == expected[field]
    assert set(expected["reason_codes_include"]) <= set(result["reason_codes"])
    if expected["metrics"] is None:
        assert result["metrics"] is None
    else:
        assert result["metrics"].keys() == expected["metrics"].keys()
        for name, value in expected["metrics"].items():
            if value is None:
                assert result["metrics"][name] is None
            else:
                assert result["metrics"][name] == pytest.approx(value, abs=FIXTURE["numeric_tolerance"])


@pytest.mark.parametrize("field,value", [("text_count", True), ("text_count", 200.0), ("text_count", "200"), ("observed_records", 199)])
def test_counts_are_not_coerced_or_repaired(field, value):
    payload = merged(FIXTURE["defaults"], {"counts": {field: value}})
    assert evaluate_review_signal(payload)["decision"] == "invalid_input"


@pytest.mark.parametrize("field,value", [("max_unknown_share", float("nan")), ("local_share_min", float("inf")), ("korean_share_max", -1), ("min_classified_texts", 0), ("min_classified_texts", True)])
def test_thresholds_are_validated(field, value):
    with pytest.raises(ValueError):
        ReviewThresholds(**{field: value})
    result = evaluate_review_signal(merged(FIXTURE["defaults"], {"thresholds": {field: value}}))
    assert result["decision"] == "invalid_input"


def test_total_rating_count_never_enters_language_denominator():
    first = evaluate_review_signal(FIXTURE["defaults"])
    second = evaluate_review_signal(merged(FIXTURE["defaults"], {"total_rating_count": 900_000}))
    assert first == second


@pytest.mark.parametrize("override,reason", [
    ({"place_identity_confirmed": False}, "PLACE_IDENTITY_UNVERIFIED"),
    ({"window": {"locale_filter": "ja"}}, "INELIGIBLE_SELECTION"),
    ({"window": {"keyword_filter": "locals"}}, "INELIGIBLE_SELECTION"),
    ({"window": {"rating_filter": [5]}}, "INELIGIBLE_SELECTION"),
    ({"window": {"ordering_semantics": "unknown"}}, "ORDERING_SEMANTICS_UNVERIFIED"),
    ({"window": {"date_precision": "relative"}}, "DATE_PRECISION_UNVERIFIED"),
    ({"quality": {"korean_recall_validation": "no_samples"}}, "CLASSIFICATION_QUALITY_UNVERIFIED"),
    ({"window": {"stop_reason": "provider_blocked"}}, "PARTIAL_COLLECTION"),
])
def test_quality_and_selection_gates_are_independent(override, reason):
    result = evaluate_review_signal(merged(FIXTURE["defaults"], override))
    assert result["decision"] == "unsupported"
    assert reason in result["reason_codes"]
    assert result["strict_pass"] is False


def test_missing_gate_information_fails_closed():
    data = deepcopy(FIXTURE["defaults"])
    del data["quality"]
    assert evaluate_review_signal(data)["decision"] == "unsupported"
    del data["rights"]
    result = evaluate_review_signal(data)
    assert result["metrics"] is None
    assert result["display_mode"] == "none"


def test_bcp47_and_city_languages_preserve_semantics():
    assert CITY_LOCAL_LANGUAGES["tokyo"] == ("ja",)
    assert CITY_LOCAL_LANGUAGES["barcelona"] == ("es", "ca")
    assert base_language("es-419") == "es"
    assert base_language("ca-ES") == "ca"
    assert base_language("ko-KR") == "ko"
    for invalid in ("unknown", "und", "mul", "zxx", None, "ja<script>"):
        assert base_language(invalid) is None
    payload = merged(FIXTURE["defaults"], {"local_languages": ["ja-JP", "ko-KR"]})
    assert "OVERLAPPING_LANGUAGE_SETS" in evaluate_review_signal(payload)["reason_codes"]


def test_normalized_records_count_presence_and_source_language_separately():
    observations = [
        {"text_presence": "present", "text_status": "original", "language": "es-419"},
        {"text_presence": "present", "text_status": "original", "language": "ca-ES"},
        {"text_presence": "present", "text_status": "original", "language": "ko-KR"},
        {"text_presence": "present", "text_status": "translated_only", "language": None},
        {"text_presence": "present", "text_status": "mixed_unseparated", "language": None},
        {"text_presence": "rating_only", "language": None},
        {"text_presence": "unextractable", "language": None},
    ]
    result = aggregate_observations(observations, ("es", "ca"))
    assert result["counts"] == {"observed_records": 7, "rating_only_count": 1,
        "text_count": 5, "classified_count": 3, "unknown_count": 2,
        "extraction_unknown_count": 1, "local_count": 2, "korean_count": 1}
    assert result["counts_by_language"] == {"ca": 1, "es": 1, "ko": 1}


@pytest.mark.parametrize("record,reason", [
    ({"text_presence": "rating_only", "language": "ja"}, "RATING_ONLY_HAS_LANGUAGE"),
    ({"text_presence": "unextractable", "language": "ja"}, "UNEXTRACTABLE_HAS_LANGUAGE"),
    ({"text_presence": "present", "text_status": "translated_only", "language": "ja"}, "UNVERIFIED_ORIGINAL_HAS_LANGUAGE"),
    ({"text_presence": None}, "INVALID_TEXT_PRESENCE"),
])
def test_bad_normalized_input_not_counted(record, reason):
    with pytest.raises(ValueError, match=reason):
        aggregate_observations([record], ("ja",))


def test_same_review_id_must_be_deduplicated_before_counting():
    observation = {"provider_review_id": "r1", "place_id": "p1", "provider": "fake", "text_presence": "rating_only"}
    with pytest.raises(ValueError, match="DUPLICATE_REVIEW_ID"):
        aggregate_observations([observation, observation], ("ja",))


def test_counts_by_language_must_match_classified_and_local_counts():
    valid = merged(FIXTURE["defaults"], {"counts_by_language": {"ja": 150, "ko-KR": 4, "en": 36}})
    assert evaluate_review_signal(valid)["decision"] == "pass"
    valid["counts_by_language"]["en"] = 35
    assert evaluate_review_signal(valid)["decision"] == "invalid_input"


def test_pure_function_does_not_mutate_fixture_input():
    before = deepcopy(FIXTURE["defaults"])
    evaluate_review_signal(FIXTURE["defaults"])
    assert FIXTURE["defaults"] == before
