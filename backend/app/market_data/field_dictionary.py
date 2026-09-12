"""Field dictionary: upstream names to display labels, kinds and units.

Percent fields carry the vendor's percent notation (89.12 means 89.12%);
normalization converts them to ratios. Ratio/multiple/turnover fields are
used as-is with their unit labels. Unknown fields stay passthrough-with-gap.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class FieldKind(StrEnum):
    AMOUNT = "amount"        # currency units (CNY)
    PRICE = "price"          # currency units
    VOLUME = "volume"        # shares
    TURNOVER = "turnover"    # currency units
    PERCENT = "percent"      # vendor percent -> normalized ratio
    RATIO = "ratio"          # already a ratio (x)
    MULTIPLE = "multiple"    # times covered
    TURNS = "turns"          # turnover frequency
    PER_SHARE = "per_share"  # CNY / share
    TEXT = "text"
    TIMESTAMP = "timestamp"
    DATE = "date"


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream: str
    label: str
    kind: FieldKind


def _spec(upstream: str, label: str, kind: FieldKind) -> FieldSpec:
    return FieldSpec(upstream=upstream, label=label, kind=kind)


# --- price history / snapshot -------------------------------------------------
PRICE_FIELDS: dict[str, FieldSpec] = {
    spec.upstream: spec
    for spec in (
        _spec("open_price", "开盘价", FieldKind.PRICE),
        _spec("high_price", "最高价", FieldKind.PRICE),
        _spec("low_price", "最低价", FieldKind.PRICE),
        _spec("close_price", "收盘价", FieldKind.PRICE),
        _spec("last_price", "最新价", FieldKind.PRICE),
        _spec("prev_price", "前收盘价", FieldKind.PRICE),
        _spec("price_change", "涨跌额", FieldKind.PRICE),
        _spec("price_change_ratio_pct", "涨跌幅", FieldKind.PERCENT),
        _spec("volume", "成交量", FieldKind.VOLUME),
        _spec("turnover", "成交额", FieldKind.TURNOVER),
    )
}

# --- valuations ---------------------------------------------------------------
VALUATION_FIELDS: dict[str, FieldSpec] = {
    spec.upstream: spec
    for spec in (
        _spec("pe_ttm", "市盈率 TTM", FieldKind.RATIO),
        _spec("pe_mrq", "市盈率 MRQ", FieldKind.RATIO),
        _spec("pb_mrq", "市净率 MRQ", FieldKind.RATIO),
        _spec("ps_ttm", "市销率 TTM", FieldKind.RATIO),
        _spec("pcf_ttm", "市现率 TTM", FieldKind.RATIO),
    )
}

# --- financial statements -----------------------------------------------------
STATEMENT_FIELDS: dict[str, FieldSpec] = {
    spec.upstream: spec
    for spec in (
        _spec("operating_income", "营业收入", FieldKind.AMOUNT),
        _spec("operating_costs", "营业成本", FieldKind.AMOUNT),
        _spec("operating_expenses", "营业总成本", FieldKind.AMOUNT),
        _spec("sales_fee", "销售费用", FieldKind.AMOUNT),
        _spec("manage_fee", "管理费用", FieldKind.AMOUNT),
        _spec("research_and_development_expenses", "研发费用", FieldKind.AMOUNT),
        _spec("operating_profit", "营业利润", FieldKind.AMOUNT),
        _spec("interest_expenses", "利息费用", FieldKind.AMOUNT),
        _spec("profit_total", "利润总额", FieldKind.AMOUNT),
        _spec("income_tax_expense", "所得税费用", FieldKind.AMOUNT),
        _spec("net_profit", "净利润", FieldKind.AMOUNT),
        _spec("parent_holder_net_profit", "归母净利润", FieldKind.AMOUNT),
        _spec("basic_eps", "基本每股收益", FieldKind.PER_SHARE),
        _spec("assets_total", "资产总计", FieldKind.AMOUNT),
        _spec("total_current_assets", "流动资产合计", FieldKind.AMOUNT),
        _spec("non_current_nets_total", "非流动资产合计", FieldKind.AMOUNT),
        _spec("cash", "货币资金", FieldKind.AMOUNT),
        _spec("accounts_receivable", "应收账款", FieldKind.AMOUNT),
        _spec("total_debt", "负债合计", FieldKind.AMOUNT),
        _spec("holder_equity_total", "所有者权益合计", FieldKind.AMOUNT),
        _spec("act_cash_flow_net", "经营活动现金流净额", FieldKind.AMOUNT),
        _spec("invest_cash_flow_net", "投资活动现金流净额", FieldKind.AMOUNT),
        _spec("financing_cash_flow_net", "筹资活动现金流净额", FieldKind.AMOUNT),
        _spec("pay_fixed_assets_etc_cash", "购建长期资产支付的现金", FieldKind.AMOUNT),
        _spec("pay_dividends_profits_interest_cash", "分配股利利润或偿付利息支付的现金", FieldKind.AMOUNT),
        _spec("cash_equivalents_net_addition", "现金及等价物净增加额", FieldKind.AMOUNT),
    )
}

# --- financial indicators (index_id -> unit kind) ------------------------------
PERCENT_INDICATORS = {
    "total_assets_growth_ratio", "net_profit_yoy_growth_ratio",
    "operating_income_yoy_growth_ratio", "operating_profit_yoy_growth_ratio",
    "sale_gross_margin", "sale_net_interest_ratio", "total_assets_net_ratio",
    "index_deduct_weighted_avg_roe", "index_weighted_avg_roe",
    "assets_debt_ratio", "cash_ratio",
    "operating_cash_flow_net_yoy_growth_ratio",
}
MULTIPLE_INDICATORS = {"earned_interest_multiple"}
TURNS_INDICATORS = {
    "total_assets_turnover_ratio", "inventory_turnover_ratio",
    "current_assets_turnover_ratio", "receive_account_turnover_ratio",
}
RATIO_INDICATORS = {
    "current_ratio", "quick_ratio", "long_term_debt_equity_ratio",
    "cash_operating_index", "net_profit_cash_content", "cash_meet_invest_ratio",
    "operating_cash_flow_net_divide_income",
}

INDICATOR_LABELS: dict[str, str] = {
    "total_assets_growth_ratio": "总资产增长率",
    "net_profit_yoy_growth_ratio": "净利润同比增长率",
    "operating_income_yoy_growth_ratio": "营业收入同比增长率",
    "operating_profit_yoy_growth_ratio": "营业利润同比增长率",
    "sale_gross_margin": "销售毛利率",
    "sale_net_interest_ratio": "销售净利率",
    "total_assets_net_ratio": "总资产收益率",
    "index_deduct_weighted_avg_roe": "扣非加权净资产收益率",
    "index_weighted_avg_roe": "净资产收益率",
    "current_ratio": "流动比率",
    "quick_ratio": "速动比率",
    "assets_debt_ratio": "资产负债率",
    "cash_ratio": "现金比率",
    "earned_interest_multiple": "已获利息倍数",
    "long_term_debt_equity_ratio": "长期负债权益比率",
    "total_assets_turnover_ratio": "总资产周转率",
    "inventory_turnover_ratio": "存货周转率",
    "current_assets_turnover_ratio": "流动资产周转率",
    "receive_account_turnover_ratio": "应收账款周转率",
    "cash_operating_index": "现金营运指数",
    "operating_cash_flow_net_divide_income": "销售现金比率",
    "net_profit_cash_content": "净利润现金含量",
    "operating_cash_flow_net_yoy_growth_ratio": "现金流量净额增长率",
    "cash_meet_invest_ratio": "现金满足投资比率",
}


def indicator_spec(index_id: str) -> FieldSpec | None:
    """Unit kind per documented rule: percent-like by name, turns, multiple, ratio."""
    label = INDICATOR_LABELS.get(index_id)
    if label is None:
        return None
    if index_id in PERCENT_INDICATORS:
        return FieldSpec(upstream=index_id, label=label, kind=FieldKind.PERCENT)
    if index_id in MULTIPLE_INDICATORS:
        return FieldSpec(upstream=index_id, label=label, kind=FieldKind.MULTIPLE)
    if index_id in TURNS_INDICATORS:
        return FieldSpec(upstream=index_id, label=label, kind=FieldKind.TURNS)
    if index_id in RATIO_INDICATORS:
        return FieldSpec(upstream=index_id, label=label, kind=FieldKind.RATIO)
    return FieldSpec(upstream=index_id, label=label, kind=FieldKind.RATIO)

