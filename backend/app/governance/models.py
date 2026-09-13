"""治理层 API 的输入契约（`extra="forbid"`，拒绝未声明字段）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HypothesisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["strategy", "factor", "rule", "scan"]
    name: str = Field(min_length=1, max_length=120)
    reference: str = Field(min_length=1, max_length=120,
                           pattern=r"^[a-z0-9_]+@[A-Za-z0-9._-]+$")
    definition: dict = Field(default_factory=dict)
    direction: str = Field(default="", max_length=120)
    params: dict = Field(default_factory=dict)
    exploratory: bool = False
    trial_count: int = Field(default=1, ge=1)
    notes: str = Field(default="", max_length=2000)


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    plan: dict = Field(default_factory=dict)
    stop_criteria: dict | None = None
    exploratory: bool = False


class BatchVerdictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: dict | None = None


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str = Field(min_length=1, max_length=64)
    artifact_kind: Literal["advice", "prediction", "experiment", "scan", "hypothesis"]
    artifact_id: str = Field(min_length=1, max_length=64)


class ChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    old_hash: str = Field(default="", max_length=64)
    new_hash: str = Field(default="", max_length=64)
    hypothesis_id: str | None = Field(default=None, max_length=64)
    replacement_id: str | None = Field(default=None, max_length=64)
    new_batch_name: str | None = Field(default=None, max_length=120)
    plan: dict | None = None
    stop_criteria: dict | None = None


class CombinedStatisticsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_ids: list[str] = Field(min_length=2, max_length=50)
    acknowledge_cross_batch: bool = False
