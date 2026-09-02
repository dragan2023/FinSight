"""Deterministic metric outputs (plan 02 section 3.5).

Every metric result carries the formula, its inputs' evidence ids, the basis
it used and a note when the base made a percentage meaningless. All numbers
are Decimal strings in JSON; null is absence, never zero.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MetricValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    value: str | None
    unit: str
    basis: str
    formula: str
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class MetricSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_id: str | None = None
    thscode: str | None = None
    metrics: list[MetricValue] = Field(default_factory=list)
