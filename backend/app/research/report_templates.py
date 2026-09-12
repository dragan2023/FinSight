"""V2-05 报告模板登记表：只定义布局与依赖，不触发取数或解释数据。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportTemplate:
    id: str
    name: str
    required_endpoints: tuple[str, ...]
    chart_types: tuple[str, ...]
    layout: str
    enabled: bool


def _template(identifier: str, name: str, endpoints: tuple[str, ...], charts: tuple[str, ...],
              layout: str, *, enabled: bool) -> ReportTemplate:
    return ReportTemplate(identifier, name, endpoints, charts, layout, enabled)


REPORT_TEMPLATES: dict[str, ReportTemplate] = {
    "stock_snapshot": _template("stock_snapshot", "单股行情速览", ("prices.history", "prices.snapshot"), ("candlestick", "line", "panel_grid"), "双栏行情看板", enabled=True),
    "financial_health": _template("financial_health", "单股财务体检", ("financials.income", "financials.balance", "financials.cashflow", "financials.indicators"), ("bar", "panel_grid"), "财务三栏看板", enabled=True),
    "concept_linkage": _template("concept_linkage", "概念板块联动", ("index.context", "prices.history"), ("relationship", "relation_heat"), "关联双图", enabled=True),
    "limit_ladder": _template("limit_ladder", "涨停池与天梯", ("market.limit_up", "market.ladder"), ("bar", "heatmap", "panel_grid"), "梯队看板", enabled=True),
    "watchlist_anomaly": _template("watchlist_anomaly", "自选股异动监控", ("market.anomaly", "prices.history"), ("line", "scatter", "panel_grid"), "监控看板", enabled=True),
    "relative_strength": _template("relative_strength", "相对强弱与排名", ("index.history", "index.constituents"), ("line", "bar"), "对比双图", enabled=False),
    "valuation_percentile": _template("valuation_percentile", "估值历史分位", ("valuations.history",), ("line", "heatmap"), "估值看板", enabled=False),
    "fund_overlap": _template("fund_overlap", "基金重仓重叠", ("fund.portfolio",), ("heatmap", "relationship"), "持仓关系图", enabled=False),
    "industry_strength": _template("industry_strength", "行业强度矩阵", ("index.history", "market.breadth"), ("heatmap", "panel_grid"), "矩阵看板", enabled=True),
    "cashflow_audit": _template("cashflow_audit", "现金流稽核", ("financials.income", "financials.cashflow", "financials.balance"), ("bar", "panel_grid"), "稽核看板", enabled=True),
    "earnings_quality": _template("earnings_quality", "盈利质量拆解", ("financials.income", "financials.indicators"), ("bar", "line"), "质量看板", enabled=False),
    "market_review": _template("market_review", "市场复盘", ("market.breadth", "market.ladder", "market.rank"), ("heatmap", "bar", "panel_grid"), "复盘看板", enabled=False),
    "factor_backtest": _template("factor_backtest", "因子回测净值", ("market.warehouse",), ("backtest_curve",), "净值与回撤双轴", enabled=False),
    "short_reversal": _template("short_reversal", "短期反转实验室", ("market.warehouse",), ("backtest_curve", "scatter"), "回测看板", enabled=False),
    "factor_ic": _template("factor_ic", "因子信息系数", ("market.warehouse",), ("backtest_curve",), "信息系数看板", enabled=False),
    "dragon_tiger_topology": _template("dragon_tiger_topology", "龙虎榜资金拓扑", ("market.dragon_tiger",), ("topology", "panel_grid"), "资金关系图", enabled=True),
}


def get_template(template_id: str) -> ReportTemplate | None:
    return REPORT_TEMPLATES.get(template_id)


def enabled_templates() -> tuple[ReportTemplate, ...]:
    return tuple(template for template in REPORT_TEMPLATES.values() if template.enabled)
