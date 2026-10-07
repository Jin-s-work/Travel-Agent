"""Versioned review hypotheses; these are not measured quality guarantees."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Mapping

REVIEW_CONFIG_VERSION = "observed-window-v1"
CITY_LOCAL_LANGUAGES = MappingProxyType({"tokyo": ("ja",), "barcelona": ("es", "ca")})
NORMAL_COMPLETION_REASONS = frozenset({"record_cap", "date_boundary", "exhausted"})


def base_language(tag: object) -> str | None:
    """Keep the BCP-47 tag separately; return its primary language for counting."""
    if not isinstance(tag, str):
        return None
    value = tag.strip().replace("_", "-").lower()
    primary, *subtags = value.split("-")
    if not primary.isascii() or not primary.isalpha() or not 2 <= len(primary) <= 3:
        return None
    if primary in {"und", "mul", "zxx"}:
        return None
    if any(not part or not part.isascii() or not part.isalnum() or len(part) > 8 for part in subtags):
        return None
    return primary


@dataclass(frozen=True)
class ReviewThresholds:
    version: str = REVIEW_CONFIG_VERSION
    min_classified_texts: int = 100
    max_unknown_share: float = 0.10
    local_share_min: float = 0.60
    korean_share_max: float = 0.10

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("A configuration version is required")
        if type(self.min_classified_texts) is not int or self.min_classified_texts < 1:
            raise ValueError("min_classified_texts must be a positive integer")
        for name in ("max_unknown_share", "local_share_min", "korean_share_max"):
            value = getattr(self, name)
            if isinstance(value, bool):
                raise ValueError("Thresholds must be finite proportions")
            try:
                ratio = Fraction(str(value))
            except (ValueError, ZeroDivisionError, TypeError) as exc:
                raise ValueError("Thresholds must be finite proportions") from exc
            if not 0 <= ratio <= 1:
                raise ValueError("Thresholds must be between zero and one")

    @classmethod
    def from_mapping(cls, data: Mapping | None) -> "ReviewThresholds":
        if data is None:
            return cls()
        if not isinstance(data, Mapping):
            raise ValueError("Threshold configuration must be an object")
        known = {"version", "min_classified_texts", "max_unknown_share", "local_share_min", "korean_share_max"}
        if set(data) - known:
            raise ValueError("Unknown threshold configuration")
        return cls(**data)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewCollectionLimits:
    """Server defaults, independent of the separate approved experiment budget."""
    lookback_days: int = 180
    max_review_records: int = 200
    max_pages: int = 20
    max_attempts_per_page: int = 2
    timeout_seconds: int = 900
    max_response_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_THRESHOLDS = ReviewThresholds()
DEFAULT_COLLECTION_LIMITS = ReviewCollectionLimits()
