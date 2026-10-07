from copy import deepcopy

import pytest

from src.research.metrics import aggregate_observations
from src.research.normalization import deduplicate_observations, normalize_review

FETCHED = "2026-10-01T10:00:00+00:00"
TEXT = "料理がとても美味しくてまた食べたいと思いました。"


def detector(text):
    return {"language": "ja", "detector_version": "fake-diagnostic-v1", "model_confidence": 0.97, "disagreement_reason": None}


def normalize(record, model=detector):
    return normalize_review(record, run_id="run-1", place_id="place-1", provider="fake", fetched_at=FETCHED, detector=model, policy_version="synthetic-v1")


def test_author_profile_and_website_locale_never_enter_normalized_contract():
    record = {"original_text": TEXT, "language": "ko", "hl": "ko", "website_locale": "ko",
              "author_name": "SENSITIVE NAME", "profile_url": "https://example.test/profile",
              "author_photo": "portrait", "reviews": [{"text": "elsewhere"}],
              "visits": ["place2"], "raw_response": "unbounded"}
    result = normalize(record)
    assert result["language"] == "ja"
    assert result["original_language"] is None
    for key in ("language", "hl", "website_locale", "author_name", "profile_url", "author_photo", "reviews", "visits", "raw_response"):
        if key != "language":
            assert key not in result
    assert "SENSITIVE NAME" not in str(result)


def test_translation_only_has_text_but_no_original_language():
    result = normalize({"translated_text": "이것은 원문이 아닌 번역된 한국어입니다.", "original_language": "ja", "original_language_verified": True})
    assert result["text_presence"] == "present"
    assert result["text_status"] == "translated_only"
    assert result["language"] is None
    counts = aggregate_observations([result], ("ja",))["counts"]
    assert counts["text_count"] == counts["unknown_count"] == 1
    assert counts["korean_count"] == 0


def test_combined_original_and_translation_must_have_verified_structure():
    record = {"original_text": TEXT, "translated_text": "번역된 한국어", "original_language": "ja"}
    blocked = normalize(record)
    assert blocked["text_status"] == "mixed_unseparated"
    assert blocked["language"] is None
    record["original_separation_verified"] = True
    assert normalize(record)["language"] == "ja"


def test_original_translation_mixed_flag_never_classifies_as_original():
    result = normalize({"original_text": TEXT + "맛있어요", "original_translation_mixed": True, "original_separation_verified": True})
    assert result["language"] is None
    assert result["text_status"] == "mixed_unseparated"


@pytest.mark.parametrize("record,presence", [
    ({"original_text": "", "rating": 5}, "unextractable"),
    ({"rating": 4, "text_presence": "rating_only"}, "rating_only"),
    ({"rating": 4, "text_presence": "unextractable"}, "unextractable"),
    ({"text_presence": "present"}, "present"),
])
def test_actual_rating_only_differs_from_failed_extraction(record, presence):
    result = normalize(record)
    assert result["text_presence"] == presence
    assert result["language"] is None


@pytest.mark.parametrize("text", ["🍣😋", "Sushi", "ラーメン", "12345", "!!!"])
def test_short_menu_emoji_numbers_are_unknown_without_model_call(text):
    def must_not_call(_):
        raise AssertionError("should not call")
    result = normalize({"original_text": text}, must_not_call)
    assert result["text_presence"] == "present"
    assert result["language"] is None
    assert result["disagreement_reason"] == "INSUFFICIENT_LANGUAGE_EVIDENCE"


def test_verified_provider_and_detector_disagreement_is_unknown():
    result = normalize({"original_text": TEXT, "original_language": "ko-KR", "original_language_verified": True})
    assert result["language"] is None
    assert result["original_language"] == "ko-KR"
    assert result["original_language_base"] == "ko"
    assert result["disagreement_reason"] == "PROVIDER_DETECTOR_DISAGREEMENT"


def test_unverified_provider_language_does_not_override_detector():
    assert normalize({"original_text": TEXT, "original_language": "ko"})["language"] == "ja"


def test_unavailable_and_failed_detector_leave_unknown():
    assert normalize({"original_text": TEXT}, None)["disagreement_reason"] == "DETECTOR_UNAVAILABLE"
    def broken(_):
        raise RuntimeError("SECRET provider token")
    result = normalize({"original_text": TEXT}, broken)
    assert result["language"] is None
    assert result["disagreement_reason"] == "DETECTOR_FAILED"
    assert "SECRET" not in str(result)


def test_truncated_original_does_not_gain_language_count():
    result = normalize({"original_text": TEXT, "truncated": True})
    assert result["text_status"] == "truncated"
    assert result["language"] is None


def test_relative_dates_and_naive_timestamps_not_fabricated():
    result = normalize({"original_text": TEXT, "published_at": "3 months ago", "edited_at": "2026-09-20T10:00:00"})
    assert result["published_at"] is None
    assert result["edited_at"] is None
    assert result["date_precision"] == "unknown"
    result = normalize({"published_at": "2026-09-20", "text_presence": "rating_only"})
    assert result["published_at"] == "2026-09-20"
    assert result["date_precision"] == "day"
    assert "T00:00" not in str(result["published_at"])


def test_same_stable_id_original_translation_and_edit_count_once():
    old = normalize({"provider_review_id": "a", "translated_text": "A translated body", "edited_at": "2026-09-01T10:00:00Z"})
    latest = normalize({"provider_review_id": "a", "original_text": TEXT, "edited_at": "2026-09-03T10:00:00Z"})
    result = deduplicate_observations([old, latest])
    assert result["fetched_count"] == 2
    assert result["unique_count"] == 1
    assert result["duplicate_count"] == 1
    assert result["observations"][0]["original_text"] == TEXT


def test_different_stable_ids_with_identical_short_reviews_stay_distinct():
    records = [normalize({"provider_review_id": str(i), "original_text": "Sushi"}) for i in range(2)]
    assert deduplicate_observations(records)["unique_count"] == 2


def test_missing_id_fingerprint_collision_keeps_records_and_marks_uncertainty():
    record = normalize({"original_text": "Sushi", "rating": 5})
    result = deduplicate_observations([record, deepcopy(record)], allow_fingerprint=True)
    assert result["unique_count"] == 2
    assert result["uncertain_duplicate_count"] == 1
    assert "DEDUPE_COLLISION" in result["reason_codes"]
    assert result["observations"][0]["dedupe_key"] != result["observations"][1]["dedupe_key"]


def test_unapproved_hash_not_generated():
    record = normalize({"original_text": TEXT})
    result = deduplicate_observations([record], allow_fingerprint=False)
    assert result["observations"][0]["dedupe_key"] is None
    assert "MISSING_REVIEW_ID" in result["reason_codes"]


@pytest.mark.parametrize("rating", [True, float("nan"), float("inf"), -1, 6, "5"])
def test_invalid_rating_rejected(rating):
    with pytest.raises(ValueError, match="INVALID_REVIEW_RATING"):
        normalize({"rating": rating})
