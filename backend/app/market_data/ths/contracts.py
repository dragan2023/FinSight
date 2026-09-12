"""Static, reviewable contracts for supported Financial-API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ...errors import AppError
from ..models import Capability, HISTORY_WINDOW_CEILING_MS, MAX_BATCH_THSCODES

RequestMode = Literal["search", "snapshot", "history", "statement", "calendar", "detail", "download"]


@dataclass(frozen=True)
class EndpointContract:
    id: str
    capability: str
    path: str
    request_mode: RequestMode
    asset_types: tuple[str, ...] = ()
    max_batch: int | None = None
    window_ceiling_ms: int | None = None
    cache_class: Literal["reference", "history", "event", "live", "financial", "download"] = "history"
    allow_persistence: bool = True
    allows_empty_items: bool = True


def _contract(
    id: str, capability: str, path: str, request_mode: RequestMode,
    asset_types: tuple[str, ...] = (), **kwargs,
) -> EndpointContract:
    return EndpointContract(
        id=id, capability=capability, path=path, request_mode=request_mode,
        asset_types=asset_types, **kwargs)


_CONTRACT_LIST = (
    _contract("ticker_search", Capability.TICKER_SEARCH, "/api/meta/tickers/search", "search", cache_class="reference"),
    _contract("ticker_list", "ticker_list", "/api/meta/tickers/list", "search", cache_class="reference"),
    _contract("price_snapshot", Capability.PRICE_SNAPSHOT, "/api/a-share/prices/snapshot", "snapshot", ("a-share",), max_batch=MAX_BATCH_THSCODES, cache_class="live"),
    _contract("price_history", Capability.PRICE_HISTORY, "/api/a-share/prices/historical", "history", ("a-share",), window_ceiling_ms=HISTORY_WINDOW_CEILING_MS),
    _contract("valuation_snapshot", Capability.VALUATION_SNAPSHOT, "/api/a-share/valuations/snapshot", "snapshot", ("a-share",), max_batch=MAX_BATCH_THSCODES, cache_class="live"),
    _contract("income_statements", Capability.FINANCIAL_STATEMENTS, "/api/a-share/financials/income-statements", "statement", ("a-share",), cache_class="financial"),
    _contract("balance_sheets", Capability.FINANCIAL_STATEMENTS, "/api/a-share/financials/balance-sheets", "statement", ("a-share",), cache_class="financial"),
    _contract("cash_flow_statements", Capability.FINANCIAL_STATEMENTS, "/api/a-share/financials/cash-flow-statements", "statement", ("a-share",), cache_class="financial"),
    _contract("financial_indicators", Capability.FINANCIAL_INDICATORS, "/api/a-share/financials/indicators", "detail", ("a-share",), cache_class="financial"),
    _contract("trading_days", Capability.TRADING_DAYS, "/api/a-share/calendar/trading-days", "calendar", cache_class="reference"),
    _contract("adjustment_factors", "corporate_actions", "/api/a-share/corporate-actions/adjustment-factors", "history", ("a-share",), cache_class="financial"),
    _contract("index_catalog", "indices", "/api/a-share-index/catalog/ths-index-list", "search", ("a-share-index",), cache_class="reference"),
    _contract("index_constituents", "indices", "/api/a-share-index/constituents/ths-stock-list", "detail", ("a-share-index",), cache_class="reference"),
    _contract("index_snapshot", "indices", "/api/a-share-index/prices/snapshot", "snapshot", ("a-share-index",), max_batch=MAX_BATCH_THSCODES, cache_class="live"),
    _contract("index_history", "indices", "/api/a-share-index/prices/historical", "history", ("a-share-index",), window_ceiling_ms=HISTORY_WINDOW_CEILING_MS),
    _contract("auction_snapshot", "auction", "/api/a-share/auction/snapshot", "snapshot", ("a-share",), cache_class="live", allow_persistence=False),
    _contract("auction_benchmark", "auction", "/api/a-share/auction/short-term-benchmark", "detail", ("a-share",), cache_class="event"),
    _contract("limit_up_pool", "special_data", "/api/a-share/special-data/limit-up-pool", "detail", cache_class="event"),
    _contract("limit_down_pool", "special_data", "/api/a-share/special-data/limit-down-pool", "detail", cache_class="event"),
    _contract("limit_break_pool", "special_data", "/api/a-share/special-data/limit-break-pool", "detail", cache_class="event"),
    _contract("limit_up_ladder", "special_data", "/api/a-share/special-data/limit-up-ladder", "detail", cache_class="event"),
    _contract("skyrocket_list", "special_data", "/api/a-share/special-data/skyrocket-list", "detail", cache_class="event"),
    _contract("hot_stock_list", "special_data", "/api/a-share/special-data/hot-stock-list", "detail", cache_class="event"),
    _contract("hot_stock_list_history", "special_data", "/api/a-share/special-data/hot-stock-list-history", "history", cache_class="event"),
    _contract("hot_stock_rank_trend", "special_data", "/api/a-share/special-data/hot-stock-rank-trend", "history", ("a-share",), cache_class="event"),
    _contract("anomaly_analysis_list", "special_data", "/api/a-share/special-data/anomaly-analysis-list", "detail", cache_class="event"),
    _contract("anomaly_analysis_stock", "special_data", "/api/a-share/special-data/anomaly-analysis-stock", "detail", ("a-share",), cache_class="event"),
    _contract("dragon_tiger_list", "special_data", "/api/a-share/special-data/dragon-tiger-list", "detail", cache_class="event"),
    _contract("fund_profile", "fund_profile", "/api/fund/profile/detail", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="reference"),
    _contract("fund_holdings", "fund_portfolio", "/api/fund/portfolio/holdings", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_stock_history", "fund_portfolio", "/api/fund/portfolio/stock-history", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_bond_history", "fund_portfolio", "/api/fund/portfolio/bond-history", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_stock_report_dates", "fund_portfolio", "/api/fund/portfolio/stock-report-dates", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="reference"),
    _contract("fund_bond_report_dates", "fund_portfolio", "/api/fund/portfolio/bond-report-dates", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="reference"),
    _contract("fund_asset_allocation", "fund_portfolio", "/api/fund/portfolio/asset-allocation", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_industry_allocation", "fund_portfolio", "/api/fund/portfolio/industry-allocation", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_nav", "fund_performance", "/api/fund/performance/nav", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="history"),
    _contract("fund_returns", "fund_performance", "/api/fund/performance/returns", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="history"),
    _contract("fund_indicators_historical", "fund_performance", "/api/fund/performance/indicators-historical", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="history"),
    _contract("fund_drawdowns", "fund_performance", "/api/fund/performance/drawdowns", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="history"),
    _contract("fund_holders_detail", "fund_extended", "/api/fund/holders/detail", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_holders_top", "fund_extended", "/api/fund/holders/top", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_dividends", "fund_extended", "/api/fund/corporate-actions/dividends", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_manager_style", "fund_extended", "/api/fund/managers/investment-style", "detail", cache_class="reference"),
    _contract("fund_manager_performance", "fund_extended", "/api/fund/managers/performance", "detail", cache_class="reference"),
    _contract("fund_manager_experience", "fund_extended", "/api/fund/managers/experience", "detail", cache_class="reference"),
    _contract("fund_manager_detail", "fund_extended", "/api/fund/managers/detail", "detail", cache_class="reference"),
    _contract("fund_company_detail", "fund_extended", "/api/fund/companies/detail", "detail", cache_class="reference"),
    _contract("fund_diagnostics", "fund_extended", "/api/fund/diagnostics/detail", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="history"),
    _contract("fund_offerings", "fund_extended", "/api/fund/offerings/list", "search", cache_class="live"),
    _contract("fund_news", "fund_extended", "/api/fund/news/article-list", "search", ("fund-otc", "fund-etf", "fund-lof"), cache_class="live"),
    _contract("fund_financial_indicators", "fund_extended", "/api/fund/financials/indicators", "detail", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_income_statements", "fund_extended", "/api/fund/financials/income-statements", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_balance_sheets", "fund_extended", "/api/fund/financials/balance-sheets", "history", ("fund-otc", "fund-etf", "fund-lof"), cache_class="financial"),
    _contract("fund_market_snapshot", "fund_market", "/api/fund/market/snapshot", "snapshot", ("fund-etf",), cache_class="live"),
    _contract("fund_market_history", "fund_market", "/api/fund/market/historical", "history", ("fund-etf",), cache_class="history"),
    _contract("daily_k_download", "market_dumps", "/api/dump/market-dumps/daily-k/download-url", "download", cache_class="download", allow_persistence=False),
    _contract("daily_k_10d_download", "market_dumps", "/api/dump/market-dumps/daily-k-10d/download-url", "download", cache_class="download", allow_persistence=False),
    _contract("adjustment_factors_download", "market_dumps", "/api/dump/market-dumps/adjustment-factors/download-url", "download", cache_class="download", allow_persistence=False),
)

CONTRACTS = {item.id: item for item in _CONTRACT_LIST}


def contract_for(contract_id: str) -> EndpointContract:
    try:
        return CONTRACTS[contract_id]
    except KeyError as error:
        raise AppError("validation", "请求的同花顺端点未登记。", 422) from error


def contract_for_request(capability: str | Capability, path: str) -> EndpointContract:
    found = next((item for item in _CONTRACT_LIST if item.capability == str(capability) and item.path == path), None)
    if found is None:
        raise AppError("validation", "请求的同花顺端点未登记。", 422)
    return found


def require_batch(contract: EndpointContract, values: list[str]) -> None:
    if contract.max_batch is not None and len(values) > contract.max_batch:
        raise AppError("validation", f"单次最多查询 {contract.max_batch} 个标的。", 422)


def require_exclusive_window(*, limit: int | None, start_ms: int | None, end_ms: int | None) -> None:
    has_window = start_ms is not None or end_ms is not None
    if has_window != (start_ms is not None and end_ms is not None):
        raise AppError("validation", "时间区间模式必须同时提供开始与结束时间。", 422)
    if has_window and limit is not None:
        raise AppError("validation", "最近 N 期与时间区间两种取数模式互斥。", 422)
