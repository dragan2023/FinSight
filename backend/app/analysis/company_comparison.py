"""Small, deterministic comparison-table contract for user-selected peers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field


class ComparisonCompany(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_id: str
    thscode: str
    name: str
    industry: str | None = None
    period: str | None = None
    currency: str | None = None
    unit: str | None = None
    consolidation: str | None = None
    period_basis: str | None = None
    disclosed_at: str | None = None
    values: dict[str, str | None] = Field(default_factory=dict)


class ComparisonCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None
    status: str
    note: str | None = None


class ComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: ComparisonCompany
    cells: dict[str, ComparisonCell]


class ComparisonTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_count: int
    comparable_count: int
    common_period: str | None
    rows: list[ComparisonRow]
    gaps: list[str] = Field(default_factory=list)
    rankings: dict[str, list[str]] = Field(default_factory=dict)


def validate_group(companies: list[ComparisonCompany]) -> None:
    if not 2 <= len(companies) <= 10:
        raise ValueError("比较组必须由用户选择 2 至 10 家公司。")
    ids = [company.security_id for company in companies]
    if len(set(ids)) != len(ids):
        raise ValueError("比较组不能包含重复公司。")


def _cell(metric: str, value: str | None, values: dict[str, str | None]) -> ComparisonCell:
    if metric == "pe_ttm" and value is not None:
        profit = values.get("parent_holder_net_profit") or values.get("net_profit")
        if _non_positive(profit):
            return ComparisonCell(value=None, status="not_applicable", note="亏损或零利润时 PE 不适用。")
    if metric == "pb_mrq" and value is not None and _non_positive(values.get("holder_equity_total")):
        return ComparisonCell(value=None, status="not_applicable", note="负或零净资产时 PB 不作普通比较。")
    if value is None:
        return ComparisonCell(value=None, status="no_data", note="该公司未取得此项数据。")
    return ComparisonCell(value=value, status="available")


def _non_positive(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return Decimal(value) <= 0
    except InvalidOperation:
        return False


def build_comparison(companies: list[ComparisonCompany], metrics: list[str]) -> ComparisonTable:
    """Return a table; inconsistent accounting keys stay visible as gaps, not merged."""
    validate_group(companies)
    keys = {(item.period, item.currency, item.unit, item.consolidation, item.period_basis) for item in companies}
    compatible = len(keys) == 1
    common_period = companies[0].period if compatible else None
    rows = [ComparisonRow(company=company,
                          cells={metric: _cell(metric, company.values.get(metric), company.values)
                                 for metric in metrics}) for company in companies]
    gaps = [] if compatible else ["报告期、币种、单位、合并口径或期间口径不一致，已并列展示且未强行比较。"]
    rankings: dict[str, list[str]] = {}
    if compatible:
        for metric in metrics:
            available: list[tuple[Decimal, str]] = []
            for row in rows:
                cell = row.cells[metric]
                if cell.status != "available" or cell.value is None:
                    continue
                try:
                    available.append((Decimal(cell.value), row.company.security_id))
                except InvalidOperation:
                    continue
            if available:
                rankings[metric] = [security_id for _, security_id in sorted(available, reverse=True)]
    return ComparisonTable(selected_count=len(companies), comparable_count=len(companies) if compatible else 0,
                           common_period=common_period, rows=rows, gaps=gaps, rankings=rankings)
