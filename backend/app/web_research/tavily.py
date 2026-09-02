"""Tavily Search thin adapter (plan 04 §2.2).

Search and (batch 02) extract. Both requests pin the provider's cost
behaviour explicitly: include_answer=false,
auto_parameters=false and basic depth, so the server can never silently
upgrade depth or attach a generated answer. Time and domain constraints use
officially documented parameters; unknown features are not guessed. Layered
response checks and the bounded transient retry mirror the bocha adapter.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import httpx

from .models import (
    MAX_EXTRACT_URLS,
    SearchValidationError,
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

TAVILY_ORIGIN = "https://api.tavily.com"
TAVILY_SEARCH_PATH = "/search"
TAVILY_EXTRACT_PATH = "/extract"
# Official freshness mapping for the documented time_range parameter.
FRESHNESS_TO_TIME_RANGE = {
    "oneDay": "day",
    "oneWeek": "week",
    "oneMonth": "month",
    "oneYear": "year",
}
RETRY_BACKOFF_SECONDS = 1.0
RETRY_CEILING_SECONDS = 8.0
RETRYABLE_HTTP_STATUS = {429}


class TavilySearchClient:
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
            raise SearchSourceNotConfigured("tavily")
        if not enabled:
            raise SearchSourceDisabled()
        return api_key

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), RETRY_CEILING_SECONDS)
            except ValueError:
                pass
        return RETRY_BACKOFF_SECONDS

    async def _request(self, body: dict, *, require: str | None = "results",
                       path: str = TAVILY_SEARCH_PATH) -> dict:
        api_key = self._credential()
        url = TAVILY_ORIGIN + path
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
                payload = response.json()
            except (ValueError, TypeError):
                raise SearchInvalidResponse() from None
            if not isinstance(payload, dict):
                raise SearchInvalidResponse()
            if require is not None and not isinstance(payload.get(require), list):
                raise SearchInvalidResponse()
            return payload

    async def extract(self, urls: list[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """Per-URL body extraction on the provider's crawler.

        Billed separately from search, pinned to basic depth (no silent cost
        upgrades). Returns (succeeded {url: raw_content}, failed [(url,
        reason)]); an individual failure never raises. raw_content is the full
        text as returned and is never a snippet; a missing raw_content is
        reported as a failure rather than invented.
        """
        cleaned = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
        if not cleaned:
            raise SearchValidationError("请提供至少一个要提取的 URL。")
        if len(cleaned) > MAX_EXTRACT_URLS:
            raise SearchValidationError("单次正文提取最多 5 个 URL（按成功页面计费）。")
        payload = await self._request(
            {"urls": cleaned, "extract_depth": "basic"}, require="results",
            path=TAVILY_EXTRACT_PATH)
        succeeded: dict[str, str] = {}
        for item in payload.get("results", []):
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            raw = item.get("raw_content")
            if isinstance(url, str) and isinstance(raw, str) and raw.strip():
                succeeded[url] = raw
        failed: list[tuple[str, str]] = []
        for item in payload.get("failed_results", []):
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                reason = item.get("error") if isinstance(item.get("error"), str) else "提取失败"
                failed.append((item["url"], reason))
            elif isinstance(item, str):
                failed.append((item, "提取失败"))
        return succeeded, failed

    async def search(self, request: WebSearchRequest) -> tuple[list[SearchItem], list[str]]:
        """Returns (items, warnings); policy filtering lives in the service."""
        body: dict = {
            "query": request.query,
            "search_depth": "basic",
            "topic": "general",
            "max_results": request.count,
            "include_answer": False,
            "auto_parameters": False,
        }
        time_range = FRESHNESS_TO_TIME_RANGE.get(request.freshness)
        if time_range:
            body["time_range"] = time_range
        if request.domains:
            body["include_domains"] = list(request.domains)
        payload = await self._request(body)
        retrieved_at = utc_now_iso()
        items: list[SearchItem] = []
        warnings: list[str] = []
        dropped = 0
        for value in payload["results"]:
            if not isinstance(value, dict):
                dropped += 1
                continue
            title = value.get("title")
            url = value.get("url")
            if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
                dropped += 1
                continue
            domain = extract_domain(url.strip())
            if domain is None:
                dropped += 1
                warnings.append("已丢弃一条无法解析安全域名的结果。")
                continue
            published = value.get("published_date")
            items.append(SearchItem(
                title=title.strip(),
                url=url.strip(),
                domain=domain,
                # Tavily search exposes no publisher field: stays unknown.
                publisher=None,
                published_at=published if isinstance(published, str) and published.strip() else None,
                retrieved_at=retrieved_at,
                snippet=value.get("content") if isinstance(value.get("content"), str) else None,
                source="tavily",
            ))
        if dropped:
            warnings.append(f"已丢弃 {dropped} 条缺少标题或链接的结果。")
        return items, warnings
