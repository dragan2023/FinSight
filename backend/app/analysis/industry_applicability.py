"""Industry applicability gates for company-research metrics.

This is deliberately a small, user-confirmable classifier: it only decides
whether a metric is applicable, never assigns a financial-company score.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class IndustryKind(StrEnum):
    INDUSTRIAL = "industrial"
    FINANCIAL = "financial"
    UNKNOWN = "unknown"


class MetricApplicability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    applicable: bool
    reason: str | None = None


_FINANCIAL_KEYWORDS = ("银行", "保险", "证券", "券商", "信托", "金融")
_FINANCIAL_EXCLUSIONS = frozenset({
    "gross_margin", "sales_expense_ratio", "management_expense_ratio",
    "rd_expense_ratio", "cash_minus_total_debt", "accounts_receivable_yoy",
})


def classify_industry(industry: str | None) -> IndustryKind:
    if not industry or not industry.strip():
        return IndustryKind.UNKNOWN
    return (IndustryKind.FINANCIAL if any(word in industry for word in _FINANCIAL_KEYWORDS)
            else IndustryKind.INDUSTRIAL)


def metric_applicability(metric: str, industry: str | None) -> MetricApplicability:
    kind = classify_industry(industry)
    if kind is IndustryKind.FINANCIAL and metric in _FINANCIAL_EXCLUSIONS:
        return MetricApplicability(metric=metric, applicable=False,
                                   reason="该指标不适用于银行、保险或证券类企业，已隐藏而非按缺失处理。")
    if kind is IndustryKind.UNKNOWN:
        return MetricApplicability(metric=metric, applicable=True,
                                   reason="行业尚未确认；请由用户标注后再判断指标适用性。")
    return MetricApplicability(metric=metric, applicable=True)
