from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrategySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+_v[0-9]+$")
    version: str
    name: str
    family: str
    asset_types: list[Literal["a-share"]]
    horizon: str
    required_fields: list[str]
    warmup_bars: int = Field(ge=1, le=300)
    parameters: dict[str, int | float | str]
    signal_rule: str
    exit_rule: str
    risk_rule: str
    cost_basis: str
    benchmark_id: Literal["buy_hold_v1"]
    applicability: str
    contraindications: str
    research_sources: list[str]
    lifecycle: Literal["experimental", "candidate", "validated", "retired"] = "experimental"

class StrategyExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: Literal["trend_20_60_v1", "reversal_5d_v1"]
    thscode: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    start_ms: int = Field(gt=0)
    end_ms: int = Field(gt=0)
    one_way_cost_bps: int = Field(default=10, ge=0, le=100)
    refresh: bool = False

    @model_validator(mode="after")
    def _ordered_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("结束日期必须晚于开始日期。")
        if self.end_ms - self.start_ms > 15 * 366 * 86_400_000:
            raise ValueError("单次实验日期范围不得超过15年。")
        return self

class PriceBar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    open: float | None = None
    close: float

class StrategyExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_snapshot: dict
    data_snapshot: dict
    metrics: dict
    segment_metrics: list[dict]
    cost_sensitivity: list[dict]
    validation_grade: Literal["rejected", "insufficient", "experimental", "candidate"]
    validation_reasons: list[str]
    curve: list[dict]
    trades: list[dict]
    year_metrics: list[dict] = Field(default_factory=list)
    signal_snapshot: dict = Field(default_factory=dict)


class StrategyReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_version: str = Field(min_length=1, max_length=40)
    horizon_days: int = Field(ge=1, le=365)
    decision: Literal["validated", "keep_candidate", "retire"]
