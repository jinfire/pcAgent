from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


SourceStatus = Literal[
    "ready",
    "configuration_required",
    "temporarily_unavailable",
    "disabled",
    "error",
]
ReliabilityLevel = Literal["A", "B", "C", "D", "E"]
ClaimType = Literal["fact", "inference", "opinion", "scenario"]
Confidence = Literal["low", "medium", "high"]

ALL_ANALYSIS_MODES = (
    "market_trend",
    "apartment_comparison",
    "buy_or_sell_scenario",
    "supply_analysis",
    "policy_analysis",
    "redevelopment_due_diligence",
    "tax_and_financing",
    "expert_claim_check",
)
MVP_ANALYSIS_MODES = (
    "market_trend",
    "apartment_comparison",
    "buy_or_sell_scenario",
    "policy_analysis",
)
POLICY_STATUSES = (
    "statement",
    "pledge",
    "under_review",
    "official_announcement",
    "bill_proposed",
    "bill_passed",
    "promulgated",
    "effective",
    "repealed",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def stable_key(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return content_hash(payload)


@dataclass(frozen=True)
class FetchRequest:
    period_start: str
    period_end: str
    region: str
    district_code: str | None = None
    source_url: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedSourceItem:
    source_id: str
    source_name: str
    source_type: str
    source_url: str
    publisher: str
    published_at: str | None
    retrieved_at: str
    event_date: str | None
    period: str | None
    geographic_scope: str | None
    property_type: str | None
    reliability_level: ReliabilityLevel
    raw_reference: str
    structured_values: dict[str, Any]
    caveats: list[str]
    content_hash: str
    raw_payload: str

    @property
    def idempotency_key(self) -> str:
        return stable_key(self.source_id, self.content_hash, self.period, self.geographic_scope)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FetchResult:
    items: list[NormalizedSourceItem]
    transactions: list[dict[str, Any]] = field(default_factory=list)
    indicators: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Evidence:
    id: str
    statement: str
    source_id: str
    source_name: str
    source_url: str
    reliability_level: ReliabilityLevel
    as_of_date: str
    period: str | None = None
    geographic_scope: str | None = None
    unit: str | None = None
    sample_count: int | None = None
    caveats: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Claim:
    statement: str
    claim_type: ClaimType
    evidence_ids: list[str]
    counter_evidence_ids: list[str]
    confidence: Confidence
    caveats: list[str]
    conditions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceAdapterError(RuntimeError):
    def __init__(self, message: str, *, category: str, retryable: bool) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
