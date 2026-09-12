"""One authenticated, cancellation-safe transport for registered contracts."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx

from ..models import (
    CODE_OK, CODE_PERMISSION_DENIED, CODE_RATE_LIMITED, CODE_SERVICE_ERROR,
    CODE_SOURCE_UNAVAILABLE, CODE_UPSTREAM_TIMEOUT, ConnectionFailed,
    InvalidUpstreamResponse, PermissionDenied, RateLimited, SourceDisabled,
    SourceNotConfigured, THS_ORIGIN, UpstreamRejected, UpstreamTimeout,
)
from .contracts import EndpointContract

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
RETRY_CEILING_SECONDS = 10.0
RETRY_BACKOFF_SECONDS = 0.5


class ThsTransport:
    def __init__(self, http: httpx.AsyncClient, *, credential_source: Callable[[], tuple[str | None, bool]],
                 sleep: Callable[[float], object] = asyncio.sleep, max_retries: int = 2) -> None:
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

    async def request(self, contract: EndpointContract, *, params: dict[str, Any]) -> dict:
        api_key = self._credential()
        headers = {"Accept": "application/json", "X-api-key": api_key}
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await self._http.get(THS_ORIGIN + contract.path, params=params, headers=headers)
            except asyncio.CancelledError:
                raise
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
            if response.status_code in (401, 403):
                raise PermissionDenied()
            if response.status_code in RETRYABLE_HTTP_STATUS or response.status_code >= 500:
                if attempts >= self._max_retries + 1:
                    if response.status_code == 429:
                        raise RateLimited()
                    raise InvalidUpstreamResponse("数据源持续返回不可用状态。")
                await self._sleep(self.retry_delay(response))
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
            if code in (2001, CODE_PERMISSION_DENIED, 401, 403):
                raise PermissionDenied()
            if code == CODE_RATE_LIMITED:
                if attempts >= self._max_retries + 1:
                    raise RateLimited()
                await self._sleep(self.retry_delay(response))
                continue
            if code in (CODE_UPSTREAM_TIMEOUT, CODE_SOURCE_UNAVAILABLE, CODE_SERVICE_ERROR):
                if attempts >= self._max_retries + 1:
                    raise UpstreamTimeout() if code == CODE_UPSTREAM_TIMEOUT else InvalidUpstreamResponse("数据源暂时不可用。")
                await self._sleep(self.retry_delay(response))
                continue
            raise UpstreamRejected(code)

    @staticmethod
    def retry_delay(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), RETRY_CEILING_SECONDS)
            except ValueError:
                pass
        return RETRY_BACKOFF_SECONDS
