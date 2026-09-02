"""Normalized web-search contracts (plan 04 §2.2).

Missing publish time stays unknown (published_at=None); a crawl timestamp is
never presented as a publish time. Batch 01 ships retrieval only: results are
reference-only leads and nothing is persisted as evidence yet.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import AppError

PROVIDERS = ("bocha", "tavily")
FRESHNESS_LEVELS = ("noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear")
# Visible product cap for one search call; the caller budget may be smaller.
MAX_SEARCH_LIMIT = 10
# Visible product cap for one Tavily extract call (billed per successful page).
MAX_EXTRACT_URLS = 5
# Storage policy for batch 01: search hits are leads; nothing is stored yet.
SEARCH_STORAGE_POLICY = Literal["reference_only"]


class SearchValidationError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("validation_failed", message, 422)


class WebSearchError(AppError):
    """Base for upstream search failures; messages never carry upstream text."""


class SearchSourceNotConfigured(WebSearchError):
    def __init__(self, provider: str) -> None:
        super().__init__("not_configured", "搜索服务尚未配置凭据，请先在设置中保存。", 409)


class SearchSourceDisabled(WebSearchError):
    def __init__(self) -> None:
        super().__init__("unsupported", "该搜索服务已被停用，请先在设置中启用。", 409)


class SearchUnauthenticated(WebSearchError):
    def __init__(self) -> None:
        super().__init__("permission_denied", "搜索服务拒绝了凭据，请检查已保存的 Key。", 402)


class SearchPermissionDenied(WebSearchError):
    def __init__(self) -> None:
        super().__init__("permission_denied", "搜索服务拒绝了本次请求（账号或套餐权限）。", 402)


class SearchRateLimited(WebSearchError):
    def __init__(self) -> None:
        super().__init__("quota_limited", "搜索服务触发频率/额度限制，请稍后重试。", 429, retryable=True)


class SearchUpstreamTimeout(WebSearchError):
    def __init__(self) -> None:
        super().__init__("timeout", "搜索服务响应超时。", 504, retryable=True)


class SearchConnectionFailed(WebSearchError):
    def __init__(self) -> None:
        super().__init__("invalid_response", "无法连接搜索服务。", 502, retryable=True)


class SearchInvalidResponse(WebSearchError):
    def __init__(self, message: str = "搜索服务返回了无法解释的响应格式。") -> None:
        super().__init__("invalid_response", message, 502)


class SearchUpstreamRejected(WebSearchError):
    """Business rejection documented by the provider contract; never retried."""

    def __init__(self, code: int) -> None:
        super().__init__("upstream_rejected", "搜索服务拒绝了本次请求。", 502)


class UrlRejected(WebSearchError):
    """URL refused by the public-reader safety policy (plan 04 §2.3)."""

    def __init__(self, message: str) -> None:
        super().__init__("unsafe_url", message, 422)


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    count: int = Field(default=5, ge=1, le=MAX_SEARCH_LIMIT)
    freshness: Literal["noLimit", "oneDay", "oneWeek", "oneMonth", "oneYear"] = "noLimit"
    # Local post-filter (verified domains only); never sent as an unverified
    # upstream parameter for bocha.
    domains: list[str] = Field(default_factory=list, max_length=8)
    include_summary: bool = True

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise SearchValidationError("请输入搜索关键词。")
        return query

    @field_validator("domains")
    @classmethod
    def _clean_domains(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            domain = item.strip().lower().lstrip(".")
            if not domain or "/" in domain or "@" in domain or ":" in domain:
                raise SearchValidationError("域名过滤仅支持主机名，例如 www.cninfo.com.cn。")
            if domain not in cleaned:
                cleaned.append(domain)
        return cleaned


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    domain: str
    publisher: str | None = None
    published_at: str | None = None
    retrieved_at: str
    snippet: str | None = None
    source: str
    storage_policy: SEARCH_STORAGE_POLICY = "reference_only"
    evidence_id: str | None = None


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    query: str
    count_requested: int
    freshness: str
    items: list[SearchItem]
    filtered_by_domain: int = 0
    duplicates_removed: int = 0
    dropped_invalid: int = 0
    warnings: list[str] = Field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_domain(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return None
    host = (parts.hostname or "").lower()
    return host or None


def host_matches(host: str, allowed: str) -> bool:
    return host == allowed or host.endswith("." + allowed)
