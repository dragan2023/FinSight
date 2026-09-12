"""Scope-to-tool allowlists; schemas stay owned by the compatibility facade."""
from ..tools import TOOL_SCHEMAS

_ALLOW = {
    "company_overview": {"fetch_financials", "fetch_prices", "fetch_valuation", "read_evidence", "calculate_metrics"},
    "watchlist_review": {"fetch_prices", "fetch_valuation", "read_evidence", "calculate_metrics"},
    "index_strength": {"fetch_index_context", "read_evidence", "calculate_metrics"},
    "sector_breadth": {"fetch_index_context", "read_evidence", "calculate_metrics"},
    "market_pulse": {"fetch_market_pulse", "fetch_news", "search_knowledge", "read_evidence", "calculate_metrics"},
    "fund_overview": {"fetch_fund_profile", "fetch_fund_portfolio", "fetch_fund_performance", "read_evidence", "calculate_metrics"},
    "fund_compare": {"fetch_fund_profile", "fetch_fund_performance", "read_evidence", "calculate_metrics"},
    "market_breadth": {"query_market_warehouse", "read_evidence", "calculate_metrics"},
    # Historical/general tasks retain the compatibility tool surface.
    "general": {"fetch_financials", "fetch_prices", "fetch_valuation", "fetch_news", "search_knowledge", "read_evidence", "calculate_metrics"},
    # V2-04 advisor: full read-only surface + advice ledger tools. Write
    # tools are deliberately absent; the ledger is the only recorded write.
    "advisor": {"fetch_financials", "fetch_prices", "fetch_valuation", "fetch_index_context",
                "fetch_market_pulse", "query_market_warehouse", "fetch_news", "search_knowledge",
                "search_web", "record_advice", "query_advice_ledger", "calculate_metrics"},
}
_LEGACY = {"company_quality": "company_overview", "company_comparison": "company_overview", "thesis_review": "company_overview", "sector_relative": "sector_breadth", "fund_overlap": "fund_compare", "fund_quality": "fund_overview", "market_review": "market_pulse"}
def names_for_scope(scope: dict) -> set[str]: return _ALLOW.get(_LEGACY.get(scope.get("type"), scope.get("type", "general")), _ALLOW["general"])
def schemas_for_scope(scope: dict) -> list[dict]: return [item for item in TOOL_SCHEMAS if item["function"]["name"] in names_for_scope(scope)]
