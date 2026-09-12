"""Evidence-linked company quality observations, without a composite score."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .financial_metrics import (accrual_ratio, cash_conversion, cash_minus_total_debt, debt_ratio,
                                expense_to_revenue, gross_margin, net_cash_position_ratio,
                                net_margin, net_profit_yoy, ocf_vs_net_profit,
                                ocf_vs_parent_profit, operating_cash_surplus_margin,
                                receivable_pressure, revenue_yoy)
from .industry_applicability import IndustryKind, MetricApplicability, classify_industry, metric_applicability
from .models import MetricValue


class QualityStatus(StrEnum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    NOT_APPLICABLE = "not_applicable"


class QualityDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    status: QualityStatus
    metrics: list[MetricValue] = Field(default_factory=list)
    applicability: list[MetricApplicability] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CompanyQualityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry: str | None = None
    industry_kind: IndustryKind
    dimensions: list[QualityDimension]


def _dimension(identifier: str, label: str, metrics: list[MetricValue], industry: str | None,
               notes: list[str] | None = None) -> QualityDimension:
    applicability = [metric_applicability(metric.metric, industry) for metric in metrics]
    applicable_metrics = [metric for metric, gate in zip(metrics, applicability) if gate.applicable]
    if not metrics:
        status = QualityStatus.NO_DATA
    elif not applicable_metrics:
        status = QualityStatus.NOT_APPLICABLE
    elif any(metric.value is not None for metric in applicable_metrics):
        status = QualityStatus.AVAILABLE
    else:
        status = QualityStatus.NO_DATA
    return QualityDimension(id=identifier, label=label, status=status, metrics=metrics,
                            applicability=applicability, notes=notes or [])


def assess_company_quality(*, income_records: list[dict], balance_records: list[dict],
                           cashflow_records: list[dict], industry: str | None = None,
                           evidence_ids: list[str] | None = None) -> CompanyQualityResult:
    """Build the six planned dimensions with explicit no-data/not-applicable states."""
    evidence_ids = evidence_ids or []
    growth = _dimension("growth_profitability", "增长与盈利", [
        revenue_yoy(income_records, evidence_ids=evidence_ids),
        net_profit_yoy(income_records, evidence_ids=evidence_ids),
        gross_margin(income_records, evidence_ids=evidence_ids),
        net_margin(income_records, evidence_ids=evidence_ids),
    ], industry, ["扣非净利润字段当前未接入；不能据此区分非经常损益影响。"])
    cash = _dimension("profit_cash", "利润与现金", [
        ocf_vs_net_profit(income_records, cashflow_records, evidence_ids=evidence_ids),
        ocf_vs_parent_profit(income_records, cashflow_records, evidence_ids=evidence_ids),
        cash_conversion(income_records, cashflow_records, evidence_ids=evidence_ids),
        operating_cash_surplus_margin(income_records, cashflow_records, evidence_ids=evidence_ids),
        accrual_ratio(income_records, balance_records, cashflow_records, evidence_ids=evidence_ids),
    ], industry, ["经营现金净余量率不等同于完整 FCFF/FCFE；非正利润应单列金额与趋势，不按异常比率排序。"])
    assets = _dimension("asset_quality", "资产质量线索", [], industry,
                        ["当前字段没有存货，且缺少期初余额；不推导周转天数。"])
    liabilities = _dimension("liabilities", "负债与偿付", [
        debt_ratio(balance_records, evidence_ids=evidence_ids),
        cash_minus_total_debt(balance_records, evidence_ids=evidence_ids),
        net_cash_position_ratio(balance_records, evidence_ids=evidence_ids),
    ], industry, ["缺少有息负债与到期结构，不能推断再融资压力大小。"])
    assets = _dimension("asset_quality", "资产质量线索", [
        receivable_pressure(income_records, evidence_ids=evidence_ids),
    ], industry, ["当前字段没有存货，且缺少期初余额；不推导周转天数。"])
    investment = _dimension("operating_investment", "经营投入", [
        expense_to_revenue(income_records, "sales_fee", "sales_expense_ratio", evidence_ids=evidence_ids),
        expense_to_revenue(income_records, "manage_fee", "management_expense_ratio", evidence_ids=evidence_ids),
        expense_to_revenue(income_records, "research_and_development_expenses", "rd_expense_ratio", evidence_ids=evidence_ids),
    ], industry)
    disclosure = _dimension("audit_disclosure", "审计与披露", [], industry,
                            ["审计意见、实际披露日期需由同花顺已披露字段或用户提供原文后核验。"])
    return CompanyQualityResult(industry=industry, industry_kind=classify_industry(industry),
                                dimensions=[growth, cash, assets, liabilities, investment, disclosure])
