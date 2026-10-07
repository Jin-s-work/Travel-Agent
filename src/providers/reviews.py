"""Versioned, privacy-minimized review collection contracts.

Adapters perform one request per method. Their caller MUST reserve/settle each
call using the application's budget gateway; adapters never hide SDK retries.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json


def parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


@dataclass(frozen=True)
class CollectionRequest:
    place_identity_id: str
    external_place_id: str
    provider: str
    adapter_version: str
    requested_start: str
    requested_end: str
    policy_version: str
    actor_id: str
    job_id: str
    idempotency_key: str
    budget_reservation_id: str | None = None
    collection_mode: str = 'observed_window'
    sort: str = 'newest'
    lookback_days: int = 180
    max_review_records: int = 200
    max_pages: int = 20
    max_attempts_per_page: int = 2
    max_elapsed_seconds: int = 300
    max_response_bytes: int = 2_000_000
    max_total_charge_usd: str = '0'
    locale_filter: str | None = None
    keyword_filter: str | None = None
    rating_filter: str | None = None
    website_locale: str = 'en'
    verified_place_url: str | None = None
    source: str = 'google_maps'

    def __post_init__(self):
        for name in ('place_identity_id','external_place_id','provider','adapter_version',
                     'policy_version','actor_id','job_id','idempotency_key'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f'Invalid {name}')
        limits = {'lookback_days':180,'max_review_records':200,'max_pages':20,
                  'max_attempts_per_page':2,'max_elapsed_seconds':900,'max_response_bytes':4_000_000}
        for name, maximum in limits.items():
            value = getattr(self,name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f'{name} exceeds server limit')
        start, end = parse_timestamp(self.requested_start), parse_timestamp(self.requested_end)
        if not start or not end or start >= end or end-start > timedelta(days=self.lookback_days):
            raise ValueError('Invalid frozen observation window')
        if self.collection_mode != 'observed_window' or self.sort != 'newest':
            raise ValueError('Only observed_window/newest collection is supported')
        if any(getattr(self,k) is not None for k in ('locale_filter','keyword_filter','rating_filter')):
            raise ValueError('Selected reviews cannot represent the general language window')
        try:
            amount = Decimal(self.max_total_charge_usd)
        except Exception as exc:
            raise ValueError('Invalid finite cost ceiling') from exc
        if not amount.is_finite() or amount < 0 or amount > 3:
            raise ValueError('Invalid finite cost ceiling')
        if not isinstance(self.website_locale,str) or len(self.website_locale)>20:
            raise ValueError('Invalid website locale')

    def to_dict(self):
        return asdict(self)

    @property
    def request_hash(self):
        return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class ProviderCapabilities:
    original_text: bool = True
    translated_text: bool = True
    original_language: bool = True
    stable_review_ids: bool = True
    published_at: bool = True
    edited_at: bool = False
    sort_basis: str = 'unknown'
    source_pagination_visible: bool = False
    continuity_verified: bool = False
    remote_abort: bool = True

    def to_dict(self):
        return asdict(self)


@dataclass
class ProviderPage:
    records: list[dict]
    next_cursor: str | None
    exhausted: bool
    sort_basis: str = 'unknown'
    continuity_verified: bool = False
    remote_ref: str | None = None
    usage: dict = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    capabilities: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class ReviewProviderError(Exception):
    """Sanitized provider failure. No response text, URL token, or review body."""
    def __init__(self, code: str, *, retryable=False, retry_after=0):
        super().__init__(code)
        self.code = code
        self.retryable = bool(retryable)
        self.retry_after = max(0, min(float(retry_after), 60))


class ReviewCollectionProvider(ABC):
    name: str
    adapter_version: str
    capabilities: ProviderCapabilities

    @abstractmethod
    def start(self, request: CollectionRequest) -> dict:
        """Return privacy-safe run_id/dataset_id/status metadata only."""

    @abstractmethod
    def poll(self, remote: dict, request: CollectionRequest) -> dict:
        """Return safe status/cost metadata; never start another paid run."""

    @abstractmethod
    def fetch_page(self, remote: dict, cursor: str | None, request: CollectionRequest) -> ProviderPage:
        """One bounded dataset/page request, canonical allowlisted records."""

    @abstractmethod
    def abort(self, remote: dict, request: CollectionRequest) -> dict:
        """Request cancellation; does not promise external charge cancellation."""

    def delete_dataset(self, remote: dict, request: CollectionRequest) -> dict:
        raise ReviewProviderError('remote_deletion_unsupported')

    def delete_run(self, remote: dict, request: CollectionRequest) -> dict:
        raise ReviewProviderError('remote_deletion_unsupported')
