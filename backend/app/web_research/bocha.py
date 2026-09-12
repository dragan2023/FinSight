"""Bocha Web Search thin adapter (plan 04 §2.2).

Only the officially verified request parameters are sent: query, freshness,
summary, count. Domain restriction is applied locally after the response, and
no unverified server-side filter is guessed. Layered response checks mirror the
stage-02 client: HTTP status, business envelope, response schema. HTTP 200
never implies business success. Transient failures retry at most twice
honouring Retry-After within a small ceiling; authentication and parameter
rejections never retry. Keys, raw exceptions and submitted credentials are
never logged or included in public messages.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx

from .models import (
    SearchConnectionFailed,
    SearchInvalidResponse,
    SearchPermissionDenied,
    SearchRateLimited,
    SearchSourceDisabled,
    SearchSourceNotConfigured,
    SearchUnauthenticated,
    SearchUpstreamRejected,
    SearchUpstreamTimeout,
    SearchItem,
    WebSearchRequest,
    extract_domain,
    utc_now_iso,
)

# 博查开放平台的 API 域名与官网不同；错误地使用 bocha.cn 会让有效的
# Bearer Key 被非开放接口拒绝。
BOCHA_ORIGIN = "https://api.bochaai.com"
BOCHA_SEARCH_PATH = "/v1/web-search"
RETRY_BACKOFF_SECONDS = 1.0
RETRY_CEILING_SECONDS = 8.0
RETRYABLE_HTTP_STATUS = {429}


class BochaSearchClient:
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
            raise SearchSourceNotConfigured("bocha")
        if not enabled:
            raise SearchSourceDisabled()
        # 官方示例把 "Bearer " 写在 HTTP 头中。配置页允许用户直接粘贴
        # 示例中的整段值，因此在客户端统一去掉一次前缀，避免发出
        # "Authorization: Bearer Bearer ..." 而误判为权限不足。
        return api_key.removeprefix("Bearer ").removeprefix("bearer ").strip()

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), RETRY_CEILING_SECONDS)
            except ValueError:
                pass
        return RETRY_BACKOFF_SECONDS

    async def _request(self, body: dict) -> dict:
        api_key = self._credential()
        url = BOCHA_ORIGIN + BOCHA_SEARCH_PATH
        headers = {"Accept": "application/json", "Authorization": "Bearer " + api_key}
        attempts = 0
        while True:
            attempts += 1
            try:
                response = await self._http.post(url, json=body, headers=headers)
            except httpx.TimeoutException:
                if attempts >= self._max_retries + 1:
                    raise SearchUpstreamTimeout() from None
                await self._sleep(RETRY_BACKOFF_SECONDS * attempts)
                continue
            except httpx.TransportError:
                if attempts >= self._max_retries + 1:
                    raise SearchConnectionFailed() from None
                await self._sleep(RETRY_BACKOFF_SECONDS * attempts)
                continue
            except asyncio.CancelledError:
                raise
            if response.status_code == 401:
                raise SearchUnauthenticated()
            if response.status_code in (402, 403):
                raise SearchPermissionDenied()
            if response.status_code in RETRYABLE_HTTP_STATUS or response.status_code >= 500:
                if attempts >= self._max_retries + 1:
                    if response.status_code == 429:
                        raise SearchRateLimited()
                    raise SearchInvalidResponse("搜索服务持续返回不可用状态。")
                await self._sleep(self._retry_delay(response))
                continue
            if response.status_code != 200:
                raise SearchInvalidResponse()
            try:
                envelope = response.json()
            except (ValueError, TypeError):
                raise SearchInvalidResponse() from None
            if not isinstance(envelope, dict) or envelope.get("code") != 200:
                # Bocha wraps failures in a business code; the HTTP status alone
                # is not authoritative, so a non-200 envelope is a rejection.
                if attempts >= self._max_retries + 1:
                    raise SearchUpstreamRejected(int(envelope.get("code") or 0))
                await self._sleep(self._retry_delay(response))
                continue
            data = envelope.get("data")
            if not isinstance(data, dict):
                raise SearchInvalidResponse()
            return data

    async def search(self, request: WebSearchRequest) -> tuple[list[SearchItem], list[str]]:
        """Returns (items, warnings); policy filtering lives in the service."""
        body: dict = {
            "query": request.query,
            "freshness": request.freshness,
            "summary": request.include_summary,
            "count": request.count,
        }
        data = await self._request(body)
        pages = data.get("webPages")
        values = pages.get("value") if isinstance(pages, dict) else None
        if not isinstance(values, list):
            raise SearchInvalidResponse()
        retrieved_at = utc_now_iso()
        items: list[SearchItem] = []
        warnings: list[str] = []
        dropped = 0
        for value in values:
            if not isinstance(value, dict):
                dropped += 1
                continue
            title = value.get("name")
            url = value.get("url")
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
                dropped += 1
                continue
            domain = extract_domain(url.strip())
            if domain is None:
                dropped += 1
                warnings.append("已丢弃一条无法解析安全域名的结果。")
                continue
            snippet = value.get("snippet") or value.get("summary")
            # dateLastCrawled is a crawl timestamp, never a publish time, so it
            # is deliberately not mapped to published_at.
            items.append(SearchItem(
                title=title.strip(),
                url=url.strip(),
                domain=domain,
                publisher=(value.get("siteName") or None) if isinstance(value.get("siteName"), str) else None,
                published_at=None,
                retrieved_at=retrieved_at,
                snippet=snippet if isinstance(snippet, str) and snippet.strip() else None,
                source="bocha",
            ))
        if dropped:
            warnings.append(f"已丢弃 {dropped} 条缺少标题或链接的结果。")
        return items, warnings

    async def balance(self) -> None:
        """Verify the documented balance endpoint without returning account data."""
        headers = {"Accept": "application/json", "Authorization": "Bearer " + self._credential()}
        try:
            response = await self._http.get(BOCHA_ORIGIN + "/v1/fund/remaining", headers=headers)
        except httpx.TimeoutException:
            raise SearchUpstreamTimeout() from None
        except httpx.TransportError:
            raise SearchConnectionFailed() from None
        if response.status_code == 401:
            raise SearchUnauthenticated()
        if response.status_code in (402, 403):
            raise SearchPermissionDenied()
        if response.status_code != 200:
            raise SearchInvalidResponse()
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise SearchInvalidResponse() from None
        if not isinstance(payload, dict) or payload.get("success") is not True or str(payload.get("code")) != "200":
            raise SearchInvalidResponse("余额接口返回了无法解释的响应。")
