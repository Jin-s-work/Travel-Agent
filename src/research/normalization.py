"""Whitelist-only normalization for permitted review processing.

This module never fetches a URL or executes markup. Text fields in its result
are transient processing inputs: callers must separately authorize any storage.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import unicodedata

from .config import base_language

MAX_TEXT_CHARS = 30_000


def _string(value: object, *, limit: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > limit:
        raise ValueError("REVIEW_FIELD_TOO_LONG")
    return value


def _timestamp(value: object) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value.strip():
        return None, "unknown"
    value = value.strip()
    if len(value) == 10:
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return value, "day"
        except ValueError:
            return None, "unknown"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None, "unknown"
        return timestamp.astimezone(timezone.utc).isoformat(), "exact"
    except ValueError:
        return None, "unknown"


def _call_detector(detector: object, text: str) -> dict:
    try:
        result = detector.detect(text) if hasattr(detector, "detect") else detector(text)
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        elif hasattr(result, "__dict__") and not isinstance(result, Mapping):
            result = vars(result)
        if not isinstance(result, Mapping):
            raise ValueError("Invalid detector result")
        return dict(result)
    except Exception:
        # Do not expose provider/model exceptions or raw text in error messages.
        return {"language": None, "disagreement_reason": "DETECTOR_FAILED"}


def normalize_review(record: Mapping, *, run_id: str, place_id: str, provider: str,
                     fetched_at: str, detector: object = None,
                     policy_version: str | None = None) -> dict:
    """Normalize an adapter's canonical record, dropping every unlisted field.

    `language`, `hl` and website locale are deliberately ignored. The adapter
    must explicitly assert `text_presence='rating_only'` to establish no body.
    A provider's language alone does not substitute for a validated detector.
    """
    if hasattr(record, "model_dump"):
        record = record.model_dump()
    if not isinstance(record, Mapping):
        raise ValueError("INVALID_REVIEW_RECORD")
    original = _string(record.get("original_text"), limit=MAX_TEXT_CHARS)
    translated = _string(record.get("translated_text"), limit=MAX_TEXT_CHARS)
    translated_present = bool(translated) or record.get("translated_text_present") is True
    combined = record.get("original_translation_mixed") is True or record.get("text_status") == "mixed_unseparated"
    separation_verified = record.get("original_separation_verified") is True
    original_tag = _string(record.get("original_language"), limit=80)
    declared_presence = record.get("text_presence")
    reason = None
    if original or translated_present or declared_presence == "present":
        presence = "present"
        if record.get("truncated") is True or record.get("text_status") == "truncated":
            status, reason = "truncated", "ORIGINAL_TRUNCATED"
        elif combined or (original and translated_present and not separation_verified):
            status, reason = "mixed_unseparated", "ORIGINAL_TRANSLATION_UNSEPARATED"
        elif original:
            status = "original"
        elif translated_present:
            status, reason = "translated_only", "ORIGINAL_UNAVAILABLE"
        else:
            status, reason = "original_unavailable", "ORIGINAL_UNAVAILABLE"
    elif declared_presence == "rating_only":
        presence, status = "rating_only", "rating_only"
    else:
        presence, status, reason = "unextractable", "unextractable", "TEXT_PRESENCE_UNVERIFIED"
    language, version, confidence = None, None, None
    if status == "original":
        letters = [char for char in original if char.isalpha()]
        if len(letters) < 8:
            reason = "INSUFFICIENT_LANGUAGE_EVIDENCE"
        elif detector is None:
            reason = "DETECTOR_UNAVAILABLE"
        else:
            detection = _call_detector(detector, original)
            version = _string(detection.get("detector_version", detection.get("model_version")), limit=160)
            language = base_language(detection.get("language"))
            score = detection.get("model_confidence", detection.get("confidence"))
            if isinstance(score, (float, int)) and not isinstance(score, bool) and math.isfinite(score) and 0 <= score <= 1:
                confidence = float(score)
            reason = _string(detection.get("disagreement_reason", detection.get("reason")), limit=160)
            if detection.get("is_mixed") is True:
                language, reason = None, "MIXED_LANGUAGE"
            elif not language:
                reason = reason or "LANGUAGE_UNCLASSIFIED"
            elif record.get("original_language_verified") is True and base_language(original_tag) and language != base_language(original_tag):
                language, reason = None, "PROVIDER_DETECTOR_DISAGREEMENT"
    published_at, published_precision = _timestamp(record.get("published_at"))
    edited_at, edited_precision = _timestamp(record.get("edited_at"))
    fetched, fetched_precision = _timestamp(fetched_at)
    if not fetched or fetched_precision != "exact":
        raise ValueError("INVALID_FETCHED_AT")
    supplied_precision = record.get("date_precision")
    if supplied_precision in {"approximate", "unknown", "relative", "month", "year"}:
        # Exact-looking adapter values do not erase source ambiguity.
        precision = supplied_precision
    else:
        precision = published_precision
    rating = record.get("rating")
    rating_scale = record.get("rating_scale", 5)
    if type(rating_scale) not in {int, float} or not math.isfinite(rating_scale) or rating_scale <= 0:
        raise ValueError("INVALID_RATING_SCALE")
    if rating is not None and (type(rating) not in {int, float} or not math.isfinite(rating) or not 0 <= rating <= rating_scale):
        raise ValueError("INVALID_REVIEW_RATING")
    return {
        "run_id": run_id, "place_id": place_id, "provider": provider,
        "provider_review_id": _string(record.get("provider_review_id"), limit=512),
        "dedupe_key": _string(record.get("dedupe_key"), limit=512),
        "original_text": original, "translated_text": translated,
        "original_text_ref": _string(record.get("original_text_ref"), limit=512),
        "original_language": original_tag,
        "original_language_base": base_language(original_tag),
        "original_language_verified": record.get("original_language_verified") is True,
        "translated_text_present": translated_present,
        "original_separation_verified": separation_verified,
        "text_presence": presence, "text_status": status,
        "language": language, "detector_version": version,
        "model_confidence": confidence, "disagreement_reason": reason,
        "classification_checked_at": fetched,
        "published_at": published_at, "edited_at": edited_at,
        "date_precision": precision, "edited_date_precision": edited_precision,
        "fetched_at": fetched, "rating": rating, "rating_scale": rating_scale,
        "policy_version": policy_version,
    }


def _fingerprint(record: Mapping) -> str:
    text = record.get("original_text") or record.get("translated_text") or ""
    text = " ".join(unicodedata.normalize("NFKC", text).split())
    allowed = [record.get("provider"), record.get("place_id"), text,
               record.get("rating"), record.get("published_at")]
    return hashlib.sha256(json.dumps(allowed, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _preference(record: Mapping) -> tuple:
    # An edited copy remains one review. Prefer the latest precise edit, then
    # original availability. No profile or author identity enters this choice.
    edited, precision = _timestamp(record.get("edited_at"))
    return (edited if precision == "exact" else "", bool(record.get("original_text")), bool(record.get("language")))


def deduplicate_observations(observations: Sequence[Mapping], *, allow_fingerprint: bool = False) -> dict:
    """Stable IDs deduplicate. A text fingerprint only detects uncertain matches.

    Distinct stable IDs, including identical short reviews, are never merged.
    Hash collisions retain both records and lower collection quality.
    """
    output: list[dict] = []
    stable_positions: dict[tuple, int] = {}
    fingerprints: set[str] = set()
    duplicates = 0
    uncertain = 0
    reasons = []
    for raw in observations:
        record = dict(raw)
        review_id = record.get("provider_review_id")
        if review_id:
            key = (record.get("provider"), record.get("place_id"), review_id)
            if key in stable_positions:
                duplicates += 1
                position = stable_positions[key]
                if _preference(record) > _preference(output[position]):
                    output[position] = record
                continue
            stable_positions[key] = len(output)
        else:
            reasons.append("MISSING_REVIEW_ID")
            if allow_fingerprint:
                fingerprint = _fingerprint(record)
                if fingerprint in fingerprints:
                    uncertain += 1
                    reasons.append("DEDUPE_COLLISION")
                fingerprints.add(fingerprint)
                record["dedupe_key"] = f"candidate:{fingerprint}:{len(output)}"
                record["dedupe_status"] = "unverified"
        output.append(record)
    return {
        "observations": output, "fetched_count": len(observations),
        "unique_count": len(output), "duplicate_count": duplicates,
        "uncertain_duplicate_count": uncertain,
        "reason_codes": list(dict.fromkeys(reasons)),
    }
