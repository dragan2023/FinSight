"""Stage 05 batch 1: deterministic company-quality observations."""

from decimal import Decimal

from backend.app.analysis.company_quality import QualityStatus, assess_company_quality
from backend.app.analysis.financial_metrics import (cash_minus_total_debt, expense_to_revenue,
                                                    gross_margin, ocf_vs_parent_profit)


def annual(period: str, **fields) -> dict:
    return {"period_key": period, "basis": "annual_report",
            "fields": {name: {"value": value, "unit": "CNY"} for name, value in fields.items()}}


def quarterly(period: str, **fields) -> dict:
    return {"period_key": period, "basis": "quarterly_cumulative_unknown",
            "fields": {name: {"value": value, "unit": "CNY"} for name, value in fields.items()}}


def dimension(result, identifier: str):
    return next(item for item in result.dimensions if item.id == identifier)


def test_margin_and_expense_metrics_use_disclosed_same_period_fields_only():
    records = [annual("2024-FY", operating_income="100", operating_costs="40", sales_fee="5")]
    assert Decimal(gross_margin(records).value) == Decimal("0.6")
    assert Decimal(expense_to_revenue(records, "sales_fee", "sales_expense_ratio").value) == Decimal("0.05")
    missing = expense_to_revenue(records, "research_and_development_expenses", "rd_expense_ratio")
    assert missing.value is None and "不从其他费用倒推" in missing.note


def test_parent_profit_cash_ratio_requires_matched_annual_period_and_nonzero_base():
    income = [annual("2024-FY", parent_holder_net_profit="20")]
    cashflow = [annual("2024-FY", act_cash_flow_net="30")]
    assert Decimal(ocf_vs_parent_profit(income, cashflow).value) == Decimal("1.5")
    assert ocf_vs_parent_profit(income, [quarterly("2024-Q4", act_cash_flow_net="30")]).value is None
    assert ocf_vs_parent_profit([annual("2024-FY", parent_holder_net_profit="0")], cashflow).value is None


def test_cash_position_is_explicitly_a_simplified_observation():
    metric = cash_minus_total_debt([annual("2024-FY", cash="80", total_debt="100")])
    assert Decimal(metric.value) == Decimal("-20")
    assert "简化现金位置观察" in metric.formula
    assert "不等同于有息负债" in metric.note


def test_financial_industry_marks_inapplicable_separately_from_no_data():
    result = assess_company_quality(income_records=[], balance_records=[], cashflow_records=[], industry="商业银行")
    investment = dimension(result, "operating_investment")
    assert investment.status is QualityStatus.NOT_APPLICABLE
    assert all(not item.applicable for item in investment.applicability)
    growth = dimension(result, "growth_profitability")
    assert growth.status is QualityStatus.NO_DATA  # 收入/利润同比仍允许观察，但当前没有数值
    assert all(item.metric not in {"gross_margin"} or not item.applicable for item in growth.applicability)


def test_no_data_and_not_applicable_are_not_conflated_for_industrial_company():
    result = assess_company_quality(income_records=[], balance_records=[], cashflow_records=[], industry="白酒")
    assert dimension(result, "growth_profitability").status is QualityStatus.NO_DATA
    assert dimension(result, "asset_quality").status is QualityStatus.NO_DATA
    assert "不推导周转天数" in dimension(result, "asset_quality").notes[0]
