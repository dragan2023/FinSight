"""Evidence and data-result contracts shared by service, API and storage.

DataResult.status is available / partial / unavailable. Every gap carries one
of the fixed reasons so the UI can explain absence without inventing data.
Evidence keeps provenance and basis; raw payloads are persisted only when the
storage policy allows it (default reference_only: hash + params, no body).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

GAP_REASONS = (
    "not_configured", "permission_denied", "rate_limited", "timeout",
    "not_published", "unsupported", "invalid_response", "basis_unknown",
    "source_conflict", "stale", "not_applicable",
)


class ResultStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class StoragePolicy(StrEnum):
    FULL = "full"
    EXCERPT = "excerpt"
    REFERENCE_ONLY = "reference_only"
    EPHEMERAL = "ephemeral"


class DataGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    dimension: str
    fields: list[str] = Field(default_factory=list)
    security_id: str | None = None
    period: str | None = None
    reason: str
    impact: str
    blocking_for: list[str] = Field(default_factory=list)
    remedies: list[str] = Field(default_factory=list)


class RequestedScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    security_id: str | None = None
    thscode: str | None = None
    params: dict = Field(default_factory=dict)


class EvidenceMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str
    profile_id: str | None
    capability: str
    endpoint_path: str
    params: dict = Field(default_factory=dict)
    security_id: str | None = None
    thscode: str | None = None
    basis: str
    source_time: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str
    record_count: int
    schema_version: str
    storage_policy: StoragePolicy
    license_note: str = ""


class DataResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResultStatus
    records: list = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    gaps: list[DataGap] = Field(default_factory=list)
    requested_scope: RequestedScope
    returned_scope: dict = Field(default_factory=dict)
    source_profile_id: str | None = None
