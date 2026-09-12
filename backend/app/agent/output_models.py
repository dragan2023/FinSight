"""Report contract for validated research output (plan 03 section 2.3).

Numbers are computed by deterministic code and rendered from metric blocks;
the model references them with metric_refs instead of retyping the table.
Claims carry their kind so facts, derivations, inferences and user
assumptions stay separable. Raw model text never enters these objects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..analysis.models import MetricValue
from ..research.evidence_models import DataGap

REPORT_SCHEMA_VERSION = "finsight.report.v1"

MAX_CLAIMS_PER_SECTION = 24
MAX_CLAIM_TEXT = 1200
MAX_SECTIONS = 8
MAX_SUMMARY = 2000


class ClaimKind(StrEnum):
    FACT = "fact"
    DERIVED = "derived"
    INFERENCE = "inference"
    USER_ASSUMPTION = "user_assumption"


class SectionStatus(StrEnum):
    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    UNAVAILABLE = "unavailable"


class Coverage(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    NO_BUSINESS_EVIDENCE = "no_business_evidence"


class OutputIntegrity(StrEnum):
    VALIDATED = "validated"
    PARTIAL = "partial"
    UNSTRUCTURED = "unstructured"
    TRUNCATED = "truncated"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ClaimKind
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)
    metric_refs: list[str] = Field(default_factory=list, max_length=8)
    gap_refs: list[str] = Field(default_factory=list, max_length=8)


class CounterEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=48)
    title: str = Field(min_length=1, max_length=120)
    status: SectionStatus
    summary: str = Field(default="", max_length=MAX_SUMMARY)
    claims: list[Claim] = Field(default_factory=list, max_length=MAX_CLAIMS_PER_SECTION)
    counter_evidence: list[CounterEvidence] = Field(default_factory=list, max_length=12)
    follow_up_questions: list[str] = Field(default_factory=list, max_length=8)


class ChartSpec(BaseModel):
    """Program-owned display contract; intentionally not an ECharts option."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    chart_type: Literal["line", "bar", "candlestick", "scatter", "heatmap", "relationship",
                        "topology", "panel_grid", "backtest_curve", "relation_heat"]
    title: str = Field(min_length=1, max_length=120)
    metric_refs: list[str] = Field(default_factory=list, max_length=16)
    topology_nodes: list["TopologyNode"] = Field(default_factory=list, max_length=100)
    topology_edges: list["TopologyEdge"] = Field(default_factory=list, max_length=240)
    panel_grid: "PanelGridSpec | None" = None
    backtest_points: list["BacktestPoint"] = Field(default_factory=list, max_length=500)
    relation_heat: "RelationHeatSpec | None" = None


class TopologyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=80)
    category: str = Field(default="其他", max_length=40)
    value: float | None = None


class TopologyEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    target: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(default="关联", max_length=80)
    value: float | None = None


class PanelCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0, le=11)
    column: int = Field(ge=0, le=11)
    row_span: int = Field(default=1, ge=1, le=12)
    column_span: int = Field(default=1, ge=1, le=12)
    component: Literal["chart", "metrics", "gaps"]
    reference: str = Field(default="", max_length=48)
    title: str = Field(min_length=1, max_length=80)


class PanelGridSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: int = Field(default=12, ge=1, le=12)
    cells: list[PanelCell] = Field(min_length=1, max_length=16)


class BacktestPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: str = Field(min_length=1, max_length=32)
    net_value: float | None = None
    drawdown_pct: float | None = None
    information_coefficient: float | None = None


class RelationHeatSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_labels: list[str] = Field(min_length=1, max_length=48)
    y_labels: list[str] = Field(min_length=1, max_length=48)
    values: list[list[float | None]] = Field(min_length=1, max_length=48)



class ModelSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    output_label: str
    mode: str  # "tools" | "template" | "none" (none: no usable model)
    json_mode: bool = False


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = REPORT_SCHEMA_VERSION
    id: str
    version: int = Field(ge=1)
    parent_report_id: str | None = None
    task_id: str
    question: str = Field(min_length=1, max_length=2000)
    scope: dict = Field(default_factory=dict)
    model_snapshot: ModelSnapshot | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    evidence_ids: list[str] = Field(default_factory=list, max_length=256)
    metrics: list[MetricValue] = Field(default_factory=list, max_length=64)
    chart_specs: list[ChartSpec] = Field(default_factory=list, max_length=8)
    sections: list[ReportSection] = Field(default_factory=list, max_length=MAX_SECTIONS)
    data_gaps: list[DataGap] = Field(default_factory=list, max_length=128)
    coverage: Coverage
    output_integrity: OutputIntegrity
    limitations: list[str] = Field(default_factory=list, max_length=16)


# Suggested section skeleton; the graph fills only what the material supports.
SECTION_TEMPLATES = [
    {"id": "facts", "title": "事实概览"},
    {"id": "financials", "title": "财务质量"},
    {"id": "price_valuation", "title": "价格与估值"},
    {"id": "risk", "title": "风险与反证"},
    {"id": "followups", "title": "待验证事项"},
]
