"""HTTP client for the hexin Financial-API with layered response checks.

Layered validation: HTTP status, business code envelope, response schema.
HTTP 200 never implies business success. Only idempotent fetches retry on
transient network errors, HTTP 429 and retryable 5xx/business codes — at most
two retries, honouring Retry-After within a small ceiling. Authentication and
parameter rejections never retry. Header values, raw exceptions and submitted
credentials are never logged or included in public messages.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx

from ..errors import AppError
from .ths.contracts import contract_for_request, require_exclusive_window
from .ths.transport import ThsTransport
from .ths.index import IndexClient
from .ths.auction import AuctionClient
from .ths.special import SpecialDataClient
from .ths.fund import FundClient
from .ths.dumps import DumpClient
from .models import (
    ADJUSTMENTS,
    ASSET_TYPES,
    DEFAULT_HISTORY_ADJUST,
    ENDPOINTS,
    EXCHANGES,
    Capability,
    MAX_BATCH_THSCODES,
    MAX_SEARCH_LIMIT,
    MAX_STATEMENT_LIMIT,
    HISTORY_WINDOW_CEILING_MS,
    INDICATOR_REPORT_PATTERN,
    PERIODS,
    STATEMENTS,
    THSCODE_PATTERN,
)

def _validation(message: str) -> AppError:
    return AppError("validation", message, 422)


def validate_thscode(thscode: str) -> str:
    code = thscode.strip()
    if not THSCODE_PATTERN.match(code):
        raise _validation("标的需要形如 600519.SH 的完整同花顺代码。")
    return code


def validate_thscodes(thscodes: list[str]) -> list[str]:
    if not thscodes:
        raise _validation("请至少提供一个标的代码。")
    if len(thscodes) > MAX_BATCH_THSCODES:
        raise _validation(f"单次最多批量查询 {MAX_BATCH_THSCODES} 个标的。")
    return [validate_thscode(code) for code in thscodes]


def validate_ms_range(start_ms: int, end_ms: int, *, ceiling_ms: int | None = None) -> tuple[int, int]:
    for value in (start_ms, end_ms):
        if not isinstance(value, int) or value <= 0:
            raise _validation("时间范围必须是毫秒 Unix 时间戳。")
    if end_ms < start_ms:
        raise _validation("时间范围结束不能早于开始。")
    if ceiling_ms is not None and end_ms - start_ms > ceiling_ms:
        raise _validation("时间范围跨度超过数据源允许的上限。")
    return start_ms, end_ms


class ThsClient:
    """Capability-oriented client; one shared httpx pool, no per-call pools."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        credential_source: Callable[[], tuple[str | None, bool]],
        sleep: Callable[[float], object] = asyncio.sleep,
        max_retries: int = 2,
    ) -> None:
        self._transport = ThsTransport(
            http, credential_source=credential_source, sleep=sleep, max_retries=max_retries)
        self.indices = IndexClient(self._transport)
        self.auction = AuctionClient(self._transport)
        self.special = SpecialDataClient(self._transport)
        self.funds = FundClient(self._transport)
        self.dumps = DumpClient(self._transport)

    async def _request(self, capability: Capability | str, path: str, params: dict) -> dict:
        return await self._transport.request(contract_for_request(capability, path), params=params)

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        return ThsTransport.retry_delay(response)

    async def search(self, q: str, *, exchange: str | None = None,
                     asset_type: str | None = None, limit: int = 10) -> dict:
        query = (q or "").strip()
        if not query:
            raise _validation("请输入检索关键词。")
        params: dict = {"q": query}
        if exchange is not None:
            if exchange not in EXCHANGES:
                raise _validation("交易所仅支持 SH / SZ / BJ。")
            params["exchange"] = exchange
        if asset_type is not None:
            values = [item.strip() for item in asset_type.split(",") if item.strip()]
            if not values or any(item not in ASSET_TYPES for item in values):
                raise _validation("资产类型取值不受支持。")
            params["asset_type"] = ",".join(values)
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise _validation(f"检索数量范围为 1-{MAX_SEARCH_LIMIT}。")
        params["limit"] = limit
        return await self._request(Capability.TICKER_SEARCH, ENDPOINTS[Capability.TICKER_SEARCH], params)

    async def price_snapshot(self, thscodes: list[str]) -> dict:
        codes = validate_thscodes(thscodes)
        params = {"thscodes": ",".join(codes)}
        return await self._request(Capability.PRICE_SNAPSHOT, ENDPOINTS[Capability.PRICE_SNAPSHOT], params)

    async def price_history(self, thscode: str, *, start_ms: int, end_ms: int,
                            adjust: str = DEFAULT_HISTORY_ADJUST, offset: int = 0) -> dict:
        code_value = validate_thscode(thscode)
        if "," in thscode:
            raise _validation("历史 K 线每次只接受一个标的。")
        start, end = validate_ms_range(start_ms, end_ms, ceiling_ms=HISTORY_WINDOW_CEILING_MS)
        if adjust not in ADJUSTMENTS:
            raise _validation("复权方式仅支持 none / forward / backward。")
        params = {"thscode": code_value, "interval": "1d", "start": start, "end": end, "adjust": adjust}
        if offset:
            params["offset"] = offset
        return await self._request(Capability.PRICE_HISTORY, ENDPOINTS[Capability.PRICE_HISTORY], params)

    async def valuation_snapshot(self, thscodes: list[str]) -> dict:
        codes = validate_thscodes(thscodes)
        params = {"thscodes": ",".join(codes)}
        return await self._request(Capability.VALUATION_SNAPSHOT, ENDPOINTS[Capability.VALUATION_SNAPSHOT], params)

    async def financial_statement(self, statement: str, thscode: str, *,
                                  period: str = "annual", limit: int | None = None,
                                  start_ms: int | None = None, end_ms: int | None = None) -> dict:
        if statement not in STATEMENTS:
            raise _validation("报表类型仅支持 income / balance / cashflow。")
        code_value = validate_thscode(thscode)
        if period not in PERIODS:
            raise _validation("报告期类型仅支持 annual / quarterly。")
        require_exclusive_window(limit=limit, start_ms=start_ms, end_ms=end_ms)
        has_window = start_ms is not None
        params: dict = {"thscode": code_value, "period": period}
        if has_window:
            start, end = validate_ms_range(start_ms, end_ms, ceiling_ms=HISTORY_WINDOW_CEILING_MS)
            params["start"], params["end"] = start, end
        else:
            count = 4 if limit is None else limit
            if not 1 <= count <= MAX_STATEMENT_LIMIT:
                raise _validation(f"报表期数范围为 1-{MAX_STATEMENT_LIMIT}。")
            params["limit"] = count
        path = ENDPOINTS[Capability.FINANCIAL_STATEMENTS].format(statement=STATEMENTS[statement])
        return await self._request(Capability.FINANCIAL_STATEMENTS, path, params)

    async def financial_indicators(self, thscode: str, report: str) -> dict:
        code_value = validate_thscode(thscode)
        if not INDICATOR_REPORT_PATTERN.match((report or "").strip()):
            raise _validation("报告期格式必须形如 2024-4。")
        params = {"thscode": code_value, "report": report.strip()}
        return await self._request(Capability.FINANCIAL_INDICATORS, ENDPOINTS[Capability.FINANCIAL_INDICATORS], params)

    async def trading_days(self, *, start_ms: int, end_ms: int) -> dict:
        start, end = validate_ms_range(start_ms, end_ms)
        params = {"start": start, "end": end}
        return await self._request(Capability.TRADING_DAYS, ENDPOINTS[Capability.TRADING_DAYS], params)

    async def ticker_list(self, *, asset_type: str | None = None, limit: int = 1000,
                          offset: int = 0) -> dict:
        if not 1 <= limit <= 10_000 or offset < 0:
            raise _validation("标的列表分页参数不合法。")
        params: dict = {"limit": limit, "offset": offset}
        if asset_type:
            params["asset_type"] = asset_type
        return await self._request(Capability.TICKER_LIST, ENDPOINTS[Capability.TICKER_LIST], params)

    async def adjustment_factors(self, thscode: str, *, start_ms: int | None = None,
                                 end_ms: int | None = None) -> dict:
        params: dict = {"thscode": validate_thscode(thscode)}
        if start_ms is not None or end_ms is not None:
            start, end = validate_ms_range(start_ms, end_ms)
            params.update({"start": start, "end": end})
        return await self._request(Capability.CORPORATE_ACTIONS, ENDPOINTS[Capability.CORPORATE_ACTIONS], params)

    async def anomaly_analysis_stock(self, thscode: str, *, date_ms: int | None = None) -> dict:
        code = validate_thscode(thscode)
        # 同花顺当前定义为“当日个股异动原因”，参数为 thscodes；观察日仍由
        # MarketDataService 的缓存键保存，但不属于上游请求参数。
        return await self.special.stock_anomaly(code)
