"""Deterministic calculation tests (plan 02 section 3.5)."""

from __future__ import annotations

from decimal import Decimal

from backend.app.analysis.financial_metrics import (
    debt_ratio,
    net_margin,
    net_profit_yoy,
    ocf_vs_net_profit,
    revenue_yoy,
)
from backend.app.analysis.price_metrics import max_drawdown, range_change


def bar(date: str, close: str) -> dict:
    return {"period_key": date, "basis": "daily_bar",
            "fields": {"close_price": {"value": close, "unit": "CNY"}}}


def annual(period: str, **fields) -> dict:
    return {"period_key": period, "basis": "annual_report",
            "fields": {name: {"value": value, "unit": "CNY"} for name, value in fields.items()}}


# --- price metrics -------------------------------------------------------------
def test_range_change_uses_first_and_last_close():
    records = [bar("2025-09-01", "100"), bar("2025-09-02", "110"),
               bar("2025-09-03", "99")]
    metric = range_change(records)
    assert Decimal(metric.value) == Decimal("-0.01")
    assert metric.basis == "daily_bar"
    assert "2025-09-01" in metric.formula and "2025-09-03" in metric.formula


def test_range_change_skips_null_closes_without_filling():
    records = [bar("2025-09-01", "100"),
               {"period_key": "2025-09-02", "basis": "daily_bar",
                "fields": {"close_price": {"value": None, "unit": "CNY"}}},
               bar("2025-09-03", "120")]
    metric = range_change(records)
    assert Decimal(metric.value) == Decimal("0.2")


def test_range_change_refuses_when_zero_base():
    records = [bar("2025-09-01", "0"), bar("2025-09-02", "10")]
    metric = range_change(records)
    assert metric.value is None
    assert "无意义" in metric.note


def test_range_change_needs_two_points():
    assert range_change([bar("2025-09-01", "10")]).value is None
    assert range_change([]).value is None


def test_max_drawdown_tracks_running_peak():
    records = [bar("2025-09-01", "100"), bar("2025-09-02", "120"),
               bar("2025-09-03", "90"), bar("2025-09-04", "110")]
    metric = max_drawdown(records)
    assert Decimal(metric.value) == Decimal("-0.25")


def test_max_drawdown_no_decline_is_zero_not_null():
    records = [bar("2025-09-01", "100"), bar("2025-09-02", "120")]
    assert Decimal(max_drawdown(records).value) == Decimal("0")


# --- financial metrics ---------------------------------------------------------
def test_revenue_yoy_positive_base():
    records = [annual("2023-FY", operating_income="100"),
               annual("2024-FY", operating_income="130")]
    metric = revenue_yoy(records)
    assert Decimal(metric.value) == Decimal("0.3")
    assert metric.basis == "annual_report"


def test_yoy_negative_base_falls_back_to_absolute_change():
    records = [annual("2023-FY", operating_income="-50"),
               annual("2024-FY", operating_income="-30")]
    metric = revenue_yoy(records)
    assert metric.value is None
    assert metric.unit == "CNY"
    assert "绝对变化 20" in metric.note


def test_yoy_needs_two_annual_periods():
    metric = revenue_yoy([annual("2024-FY", operating_income="100")])
    assert metric.value is None
    assert "不足两期" in metric.note


def test_quarterly_rows_are_never_used_for_yoy():
    quarterly = {"period_key": "2024-Q3", "basis": "quarterly_cumulative_unknown",
                 "fields": {"operating_income": {"value": "999", "unit": "CNY"}}}
    records = [quarterly, annual("2023-FY", operating_income="100")]
    assert revenue_yoy(records).value is None


def test_net_profit_yoy_uses_parent_holder_field():
    records = [annual("2023-FY", parent_holder_net_profit="200"),
               annual("2024-FY", parent_holder_net_profit="150")]
    metric = net_profit_yoy(records)
    assert Decimal(metric.value) == Decimal("-0.25")


def test_net_margin_consolidated_basis():
    records = [annual("2024-FY", net_profit="93", operating_income="174.144")]
    metric = net_margin(records)
    assert metric.basis == "annual_report"
    assert "合并净利润口径" in metric.formula
    assert Decimal(metric.value) == Decimal("93") / Decimal("174.144")


def test_debt_ratio_maps_total_debt_to_liabilities_total():
    records = [annual("2024-FY", total_debt="60", assets_total="250")]
    metric = debt_ratio(records)
    assert Decimal(metric.value) == Decimal("0.24")
    assert "负债合计" in metric.formula


def test_ocf_vs_net_profit_matches_annual_periods_only():
    income = [annual("2024-FY", net_profit="92")]
    cashflow = [annual("2024-FY", act_cash_flow_net="115")]
    metric = ocf_vs_net_profit(income, cashflow)
    assert Decimal(metric.value) == Decimal("1.25")


def test_ocf_vs_net_profit_skips_unmatched_quarters():
    income = [annual("2024-FY", net_profit="92")]
    cashflow = [{"period_key": "2024-Q3", "basis": "quarterly_cumulative_unknown",
                 "fields": {"act_cash_flow_net": {"value": "10", "unit": "CNY"}}}]
    metric = ocf_vs_net_profit(income, cashflow)
    assert metric.value is None
    assert "不做推导" in metric.note
