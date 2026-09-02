"""Financial metrics on normalized annual statements.

Rules fixed by the plan: YoY with a base <= 0 falls back to the absolute
change (a percentage would be meaningless); net margin uses consolidated
net_profit; debt ratio uses total_debt = 负债合计 over assets_total;
OCF-vs-net-profit runs only on annual (basis-known) rows and matches the
consolidated net profit basis. Quarterly rows are never aggregated or
differenced because their cumulative-vs-single-quarter basis is unknown.
"""

from __future__ import annotations

from decimal import Decimal

from .models import MetricValue

ZERO = Decimal(0)


def _annual(records: list[dict], fields: list[str]) -> list[dict]:
    """Annual records that contain every requested numeric field, newest first."""
    rows = []
    for record in records or []:
        if record.get("basis") != "annual_report":
            continue
        values = {}
        for name in fields:
            field = (record.get("fields") or {}).get(name)
            if not field or field.get("value") in (None, ""):
                values = {}
                break
            values[name] = Decimal(field["value"])
        if values:
            rows.append({"period": record.get("period_key"), "values": values})
    rows.sort(key=lambda row: row["period"] or "", reverse=True)
    return rows


def _yoy(records: list[dict], metric: str, field: str, label: str,
         evidence_ids: list[str] | None = None) -> MetricValue:
    rows = _annual(records, [field])
    if len(rows) < 2:
        return MetricValue(
            metric=metric, value=None, unit="ratio", basis="annual_report",
            formula=f"({field}_t - {field}_t-1) / |{field}_t-1|",
            evidence_ids=evidence_ids or [],
            note="可用年报不足两期，无法计算同比。")
    current, base = rows[0], rows[1]
    base_value = base["values"][field]
    delta = current["values"][field] - base_value
    if base_value <= ZERO:
        return MetricValue(
            metric=metric, value=None, unit="CNY", basis="annual_report",
            formula=f"{field}_t - {field}_t-1",
            evidence_ids=evidence_ids or [],
            note=f"基期（{base['period']}）数值 {base_value} ≤ 0，同比百分比无意义；仅列绝对变化 {delta}。")
    ratio = delta / abs(base_value)
    return MetricValue(
        metric=metric, value=format(ratio, "f"), unit="ratio", basis="annual_report",
        formula=f"({current['values'][field]} - {base_value}) / |{base_value}|  # {base['period']} -> {current['period']}",
        evidence_ids=evidence_ids or [])


def revenue_yoy(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    return _yoy(records, "revenue_yoy", "operating_income", "营业收入同比", evidence_ids)


def net_profit_yoy(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    return _yoy(records, "net_profit_yoy", "parent_holder_net_profit", "归母净利润同比", evidence_ids)


def net_margin(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    """Consolidated net_profit / operating_income for the latest annual row."""
    rows = _annual(records, ["net_profit", "operating_income"])
    if not rows:
        return MetricValue(
            metric="net_margin", value=None, unit="ratio", basis="annual_report",
            formula="net_profit / operating_income", evidence_ids=evidence_ids or [],
            note="缺少可用年报数据。")
    row = rows[0]
    income = row["values"]["operating_income"]
    if income == ZERO:
        return MetricValue(
            metric="net_margin", value=None, unit="ratio", basis="annual_report",
            formula="net_profit / operating_income", evidence_ids=evidence_ids or [],
            note="营业收入为 0，比率无意义，拒绝计算。")
    margin = row["values"]["net_profit"] / income
    return MetricValue(
        metric="net_margin", value=format(margin, "f"), unit="ratio", basis="annual_report",
        formula=f"{row['values']['net_profit']} / {income}  # {row['period']} 合并净利润口径",
        evidence_ids=evidence_ids or [])


def debt_ratio(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    """total_debt (= 负债合计, not interest-bearing debt) / assets_total."""
    rows = _annual(records, ["total_debt", "assets_total"])
    if not rows:
        return MetricValue(
            metric="debt_ratio", value=None, unit="ratio", basis="annual_report",
            formula="total_debt / assets_total", evidence_ids=evidence_ids or [],
            note="缺少可用年报数据。total_debt 为负债合计口径，非有息负债。")
    row = rows[0]
    assets = row["values"]["assets_total"]
    if assets == ZERO:
        return MetricValue(
            metric="debt_ratio", value=None, unit="ratio", basis="annual_report",
            formula="total_debt / assets_total", evidence_ids=evidence_ids or [],
            note="资产总计为 0，比率无意义，拒绝计算。")
    ratio = row["values"]["total_debt"] / assets
    return MetricValue(
        metric="debt_ratio", value=format(ratio, "f"), unit="ratio", basis="annual_report",
        formula=f"{row['values']['total_debt']} / {assets}  # {row['period']} 负债合计口径",
        evidence_ids=evidence_ids or [])


def ocf_vs_net_profit(income_records: list[dict], cashflow_records: list[dict],
                      *, evidence_ids: list[str] | None = None) -> MetricValue:
    """OCF / consolidated net_profit on matched annual periods only."""
    income_rows = _annual(income_records, ["net_profit"])
    cash_rows = _annual(cashflow_records, ["act_cash_flow_net"])
    by_period = {row["period"]: row["values"]["act_cash_flow_net"] for row in cash_rows}
    for row in income_rows:
        if row["period"] in by_period:
            profit = row["values"]["net_profit"]
            ocf = by_period[row["period"]]
            if profit == ZERO:
                return MetricValue(
                    metric="ocf_vs_net_profit", value=None, unit="ratio", basis="annual_report",
                    formula="act_cash_flow_net / net_profit", evidence_ids=evidence_ids or [],
                    note="当期净利润为 0，比率无意义，拒绝计算。")
            ratio = ocf / profit
            return MetricValue(
                metric="ocf_vs_net_profit", value=format(ratio, "f"), unit="ratio",
                basis="annual_report",
                formula=f"{ocf} / {profit}  # {row['period']} 合并净利润口径",
                evidence_ids=evidence_ids or [])
    return MetricValue(
        metric="ocf_vs_net_profit", value=None, unit="ratio", basis="annual_report",
        formula="act_cash_flow_net / net_profit", evidence_ids=evidence_ids or [],
        note="缺少可匹配的年报期；季度口径（累计/单季）未确认，不做推导。")


def gross_margin(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    """(营业收入 - 营业成本) / 营业收入，限于同一期年报。"""
    rows = _annual(records, ["operating_income", "operating_costs"])
    if not rows:
        return MetricValue(metric="gross_margin", value=None, unit="ratio", basis="annual_report",
                           formula="(operating_income - operating_costs) / operating_income",
                           evidence_ids=evidence_ids or [], note="缺少同一期营业收入和营业成本年报数据。")
    row = rows[0]
    income = row["values"]["operating_income"]
    if income == ZERO:
        return MetricValue(metric="gross_margin", value=None, unit="ratio", basis="annual_report",
                           formula="(operating_income - operating_costs) / operating_income",
                           evidence_ids=evidence_ids or [], note="营业收入为 0，比率无意义，拒绝计算。")
    value = (income - row["values"]["operating_costs"]) / income
    return MetricValue(metric="gross_margin", value=format(value, "f"), unit="ratio", basis="annual_report",
                       formula=f"({income} - {row['values']['operating_costs']}) / {income}  # {row['period']}",
                       evidence_ids=evidence_ids or [])


def expense_to_revenue(records: list[dict], field: str, metric: str, *,
                       evidence_ids: list[str] | None = None) -> MetricValue:
    """某个已披露费用 / 营业收入；不以营业总成本倒挤缺失费用。"""
    rows = _annual(records, [field, "operating_income"])
    if not rows:
        return MetricValue(metric=metric, value=None, unit="ratio", basis="annual_report",
                           formula=f"{field} / operating_income", evidence_ids=evidence_ids or [],
                           note=f"缺少已披露的 {field} 或同一期营业收入；不从其他费用倒推。")
    row = rows[0]
    income = row["values"]["operating_income"]
    if income == ZERO:
        return MetricValue(metric=metric, value=None, unit="ratio", basis="annual_report",
                           formula=f"{field} / operating_income", evidence_ids=evidence_ids or [],
                           note="营业收入为 0，比率无意义，拒绝计算。")
    value = row["values"][field] / income
    return MetricValue(metric=metric, value=format(value, "f"), unit="ratio", basis="annual_report",
                       formula=f"{row['values'][field]} / {income}  # {row['period']}",
                       evidence_ids=evidence_ids or [])


def ocf_vs_parent_profit(income_records: list[dict], cashflow_records: list[dict], *,
                         evidence_ids: list[str] | None = None) -> MetricValue:
    """经营现金流 / 归母净利润；仅匹配同一已知年报期。"""
    income_rows = _annual(income_records, ["parent_holder_net_profit"])
    cash_rows = _annual(cashflow_records, ["act_cash_flow_net"])
    cash_by_period = {row["period"]: row["values"]["act_cash_flow_net"] for row in cash_rows}
    for row in income_rows:
        cash = cash_by_period.get(row["period"])
        if cash is None:
            continue
        profit = row["values"]["parent_holder_net_profit"]
        if profit == ZERO:
            return MetricValue(metric="ocf_vs_parent_profit", value=None, unit="ratio", basis="annual_report",
                               formula="act_cash_flow_net / parent_holder_net_profit",
                               evidence_ids=evidence_ids or [], note="当期归母净利润为 0，比率无意义，拒绝计算。")
        return MetricValue(metric="ocf_vs_parent_profit", value=format(cash / profit, "f"), unit="ratio",
                           basis="annual_report",
                           formula=f"{cash} / {profit}  # {row['period']} 归母净利润口径",
                           evidence_ids=evidence_ids or [])
    return MetricValue(metric="ocf_vs_parent_profit", value=None, unit="ratio", basis="annual_report",
                       formula="act_cash_flow_net / parent_holder_net_profit", evidence_ids=evidence_ids or [],
                       note="缺少可匹配的归母净利润与经营现金流年报期；季度口径不做推导。")


def cash_minus_total_debt(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    """货币资金 - 负债合计，只作为现金位置观察，非偿债能力结论。"""
    rows = _annual(records, ["cash", "total_debt"])
    if not rows:
        return MetricValue(metric="cash_minus_total_debt", value=None, unit="CNY", basis="annual_report",
                           formula="cash - total_debt", evidence_ids=evidence_ids or [],
                           note="缺少同一期货币资金或负债合计；无法观察现金位置。")
    row = rows[0]
    value = row["values"]["cash"] - row["values"]["total_debt"]
    return MetricValue(metric="cash_minus_total_debt", value=format(value, "f"), unit="CNY",
                       basis="annual_report",
                       formula=f"{row['values']['cash']} - {row['values']['total_debt']}  # {row['period']}；仅作简化现金位置观察",
                       evidence_ids=evidence_ids or [],
                       note="负债合计不等同于有息负债，不能据此推断到期压力。")
