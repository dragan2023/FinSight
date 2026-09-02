"""Contracts for the hexin Financial-API client layer.

Envelope, business error codes and capability endpoints follow the official
contract reviewed 2026-08-31 (fuyao.aicubes.cn/llms-full.txt). Client errors
carry a DataGap reason so the service layer can build gaps without re-mapping.
"""

from __future__ import annotations

import re
from enum import StrEnum

from ..errors import AppError

THS_ORIGIN = "https://fuyao.aicubes.cn"

THSCODE_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
INDICATOR_REPORT_PATTERN = re.compile(r"^[0-9]{4}-[1-4]$")

MAX_SEARCH_LIMIT = 50
MAX_BATCH_THSCODES = 50
MAX_STATEMENT_LIMIT = 20
HISTORY_WINDOW_CEILING_MS = 10 * 365 * 24 * 60 * 60 * 1000
DEFAULT_HISTORY_ADJUST = "forward"

# Business codes documented in the unified contract.
CODE_OK = 0
CODE_MISSING_PARAM = 1001
CODE_BAD_PARAM_FORMAT = 1002
CODE_PARAM_OUT_OF_RANGE = 1003
CODE_PARAM_CONFLICT = 1004
CODE_UNAUTHENTICATED = 2001
CODE_PERMISSION_DENIED = 2003
CODE_TICKER_NOT_FOUND = 3001
CODE_DATA_NOT_READY = 3002
CODE_ASSET_TYPE_UNSUPPORTED = 3004
CODE_RATE_LIMITED = 4001
CODE_SERVICE_ERROR = 5001
CODE_UPSTREAM_TIMEOUT = 5002
CODE_SOURCE_UNAVAILABLE = 5003


class Capability(StrEnum):
    TICKER_SEARCH = "ticker_search"
    PRICE_SNAPSHOT = "price_snapshot"
    PRICE_HISTORY = "price_history"
    VALUATION_SNAPSHOT = "valuation_snapshot"
    FINANCIAL_STATEMENTS = "financial_statements"
    FINANCIAL_INDICATORS = "financial_indicators"
    TRADING_DAYS = "trading_days"


ENDPOINTS: dict[Capability, str] = {
    Capability.TICKER_SEARCH: "/api/meta/tickers/search",
    Capability.PRICE_SNAPSHOT: "/api/a-share/prices/snapshot",
    Capability.PRICE_HISTORY: "/api/a-share/prices/historical",
    Capability.VALUATION_SNAPSHOT: "/api/a-share/valuations/snapshot",
    Capability.FINANCIAL_STATEMENTS: "/api/a-share/financials/{statement}",
    Capability.FINANCIAL_INDICATORS: "/api/a-share/financials/indicators",
    Capability.TRADING_DAYS: "/api/a-share/calendar/trading-days",
}

STATEMENTS = {"income": "income-statements", "balance": "balance-sheets", "cashflow": "cash-flow-statements"}
PERIODS = {"annual", "quarterly"}
ADJUSTMENTS = {"none", "forward", "backward"}
EXCHANGES = {"SH", "SZ", "BJ"}
ASSET_TYPES = {"a-share", "a-share-index", "fund-otc", "fund-etf", "fund-lof"}


class SourceNotConfigured(AppError):
    def __init__(self) -> None:
        super().__init__("not_configured", "金融数据服务尚未配置凭据，请先在设置中保存。", 409)


class SourceDisabled(AppError):
    def __init__(self) -> None:
        super().__init__("unsupported", "该数据服务已被停用，请先在设置中启用。", 409)


class MarketDataError(AppError):
    """Upstream-facing failure; gap_reason feeds DataGap without re-mapping."""

    gap_reason: str = "invalid_response"

    def __init__(self, message: str, status_code: int = 502, retryable: bool = False) -> None:
        super().__init__(type(self).gap_reason, message, status_code, retryable)


class PermissionDenied(MarketDataError):
    gap_reason = "permission_denied"

    def __init__(self) -> None:
        super().__init__("当前凭据对该数据能力没有权限，请检查同花顺账号授权。", 403)


class RateLimited(MarketDataError):
    gap_reason = "rate_limited"

    def __init__(self) -> None:
        super().__init__("已达到数据服务的频率或额度限制，请稍后重试。", 429, True)


class UpstreamTimeout(MarketDataError):
    gap_reason = "timeout"

    def __init__(self) -> None:
        super().__init__("数据源响应超时，未取得可用结果。", 504, True)


class ConnectionFailed(MarketDataError):
    gap_reason = "invalid_response"

    def __init__(self) -> None:
        super().__init__("无法连接数据源，请检查本机网络。", 502, True)


class InvalidUpstreamResponse(MarketDataError):
    gap_reason = "invalid_response"

    def __init__(self, message: str = "数据源返回了无法解释的响应格式。") -> None:
        super().__init__(message, 502)


class UpstreamRejected(MarketDataError):
    """Business rejection documented by the contract; never retried."""

    def __init__(self, code: int) -> None:
        self.code = code
        if code == CODE_DATA_NOT_READY:
            reason, message = "not_published", "该报告期数据尚未在上游就绪。"
        elif code in (CODE_TICKER_NOT_FOUND, CODE_ASSET_TYPE_UNSUPPORTED):
            reason, message = "unsupported", "该标的或资产类型不支持所请求的数据能力。"
        elif code in (CODE_MISSING_PARAM, CODE_BAD_PARAM_FORMAT, CODE_PARAM_OUT_OF_RANGE, CODE_PARAM_CONFLICT):
            reason, message = "invalid_response", "请求参数未通过数据源校验，可能是契约变化。"
        else:
            reason, message = "invalid_response", "数据源暂时无法返回该数据。"
        AppError.__init__(self, reason, message, 502)
        self.gap_reason = reason
