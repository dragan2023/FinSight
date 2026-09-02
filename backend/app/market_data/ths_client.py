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
import json
from typing import Callable

import httpx

from ..errors import AppError
from .models import (
    ADJUSTMENTS,
    ASSET_TYPES,
    CODE_ASSET_TYPE_UNSUPPORTED,
    CODE_DATA_NOT_READY,
    CODE_OK,
    CODE_PARAM_CONFLICT,
    CODE_PARAM_OUT_OF_RANGE,
    CODE_PERMISSION_DENIED,
    CODE_RATE_LIMITED,
    CODE_SERVICE_ERROR,
    CODE_SOURCE_UNAVAILABLE,
    CODE_TICKER_NOT_FOUND,
    CODE_UNAUTHENTICATED,
    CODE_UPSTREAM_TIMEOUT,
    DEFAULT_HISTORY_ADJUST,
    ENDPOINTS,
    EXCHANGES,
    Capability,
    ConnectionFailed,
    InvalidUpstreamResponse,
    MAX_BATCH_THSCODES,
    MAX_SEARCH_LIMIT,
    MAX_STATEMENT_LIMIT,
    HISTORY_WINDOW_CEILING_MS,
    INDICATOR_REPORT_PATTERN,
    PERIODS,
    PermissionDenied,
    RateLimited,
    SourceDisabled,
    SourceNotConfigured,
    STATEMENTS,
    THS_ORIGIN,
    THSCODE_PATTERN,
    UpstreamRejected,
    UpstreamTimeout,
)

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
RETRY_CEILING_SECONDS = 10.0
RETRY_BACKOFF_SECONDS = 0.5


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
        self._http = http
        self._credential_source = credential_source
        self._sleep = sleep
        self._max_retries = max_retries

    def _credential(self) -> str:
        api_key, enabled = self._credential_source()
        if not api_key:
            raise SourceNotConfigured()
        if not enabled:
            raise SourceDisabled()
        return api_key

    async def _request(self, capability: Capability, path: str, params: dict) -> dict:
        api_key = self._credential()
        url = THS_ORIGIN + path
        headers = {"Accept": "application/json", "X-api-key": api_key}
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await self._http.get(url, params=params, headers=headers)
            except httpx.TimeoutException:
                if attempts >= self._max_retries + 1:
                    raise UpstreamTimeout() from None
                await self._sleep(RETRY_BACKOFF_SECONDS * attempts)
                continue
            except httpx.TransportError:
                if attempts >= self._max_retries + 1:
                    raise ConnectionFailed() from None
                await self._sleep(RETRY_BACKOFF_SECONDS * attempts)
                continue
            except asyncio.CancelledError:
                raise
            if response.status_code in (401, 403):
                raise PermissionDenied()
            if response.status_code in RETRYABLE_HTTP_STATUS or response.status_code >= 500:
                if attempts >= self._max_retries + 1:
                    if response.status_code == 429:
                        raise RateLimited()
                    raise InvalidUpstreamResponse("数据源持续返回不可用状态。")
                await self._sleep(self._retry_delay(response))
                continue
            if response.status_code != 200:
                raise InvalidUpstreamResponse()
            try:
                envelope = response.json()
            except (json.JSONDecodeError, ValueError):
                raise InvalidUpstreamResponse() from None
            if not isinstance(envelope, dict) or not isinstance(envelope.get("code"), int):
                raise InvalidUpstreamResponse()
            code = envelope["code"]
            if code == CODE_OK:
                data = envelope.get("data")
                if not isinstance(data, dict):
                    raise InvalidUpstreamResponse()
                return data
            if code in (2001, CODE_PERMISSION_DENIED) or code in (401, 403):
                raise PermissionDenied()
            if code == CODE_RATE_LIMITED:
                if attempts >= self._max_retries + 1:
                    raise RateLimited()
                await self._sleep(self._retry_delay(response))
                continue
            if code in (CODE_UPSTREAM_TIMEOUT, CODE_SOURCE_UNAVAILABLE, CODE_SERVICE_ERROR):
                if attempts >= self._max_retries + 1:
                    raise UpstreamTimeout() if code == CODE_UPSTREAM_TIMEOUT else InvalidUpstreamResponse("数据源暂时不可用。")
                await self._sleep(self._retry_delay(response))
                continue
            raise UpstreamRejected(code)

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), RETRY_CEILING_SECONDS)
            except ValueError:
                pass
        return RETRY_BACKOFF_SECONDS

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
        has_window = start_ms is not None or end_ms is not None
        has_limit = limit is not None
        if has_window != (start_ms is not None and end_ms is not None):
            raise _validation("时间区间模式必须同时提供开始与结束时间。")
        if has_window and has_limit:
            raise _validation("最近 N 期与时间区间两种取数模式互斥。")
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
