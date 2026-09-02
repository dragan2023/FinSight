"""Web research service: provider dispatch plus shared local policy.

Policy that must not depend on provider behaviour lives here: the local domain
filter, deduplication and the visible count cap. Reprint detection has no
reliable signal, so only identical entries are deduplicated and the original
publisher field is preserved untouched; near-duplicates across domains are
kept and left for human/model judgement. Nothing in batch 01 is persisted:
results are reference-only leads.
"""

from __future__ import annotations

import httpx

import socket

from ..api_config.service import ApiConfigService
from ..errors import AppError
from .bocha import BochaSearchClient
from .models import (
    PROVIDERS,
    SearchItem,
    WebSearchError,
    WebSearchRequest,
    WebSearchResult,
    host_matches,
    utc_now_iso,
)
from .public_reader import PublicPageReader
from .url_safety import (
    DEFAULT_OFFICIAL_DOMAINS,
    validate_extract_target,
)
from .tavily import TavilySearchClient


class WebResearchService:
    def __init__(self, credentials: ApiConfigService, http: httpx.AsyncClient) -> None:
        self._credentials = credentials
        self._clients = {
            "bocha": BochaSearchClient(http, credential_source=self.credential_source("bocha")),
            "tavily": TavilySearchClient(http, credential_source=self.credential_source("tavily")),
        }

    # -- credentials ---------------------------------------------------------
    def credential_source(self, provider: str):
        def resolve() -> tuple[str | None, bool]:
            return self.api_key(provider)
        return resolve

    def profile(self, provider: str) -> dict | None:
        for row in self._credentials.repository.list():
            if row.get("provider") == provider:
                return row
        return None

    def api_key(self, provider: str) -> tuple[str | None, bool]:
        row = self.profile(provider)
        if row is None or not row.get("enabled"):
            return None, False
        key_status = self._credentials.root.encryption.key_status(row.get("encrypted_api_key"))
        if key_status != "configured":
            return None, bool(row.get("enabled"))
        secret = self._credentials.root.encryption.decrypt(row["encrypted_api_key"])
        return secret.get_secret_value(), True

    def source_status(self) -> dict:
        return {
            provider: {
                "configured": (self.profile(provider) is not None
                               and self._credentials.root.encryption.key_status(
                                   (self.profile(provider) or {}).get("encrypted_api_key")) == "configured"),
                "enabled": bool((self.profile(provider) or {}).get("enabled")),
                "profile_id": (self.profile(provider) or {}).get("id"),
            }
            for provider in PROVIDERS
        }

    # -- search --------------------------------------------------------------
    async def search(self, provider: str, request: WebSearchRequest) -> WebSearchResult:
        if provider not in PROVIDERS:
            raise AppError("unknown_provider", "未知的搜索服务。", 422)
        client = self._clients[provider]
        items, warnings = await client.search(request)
        filtered = 0
        if request.domains:
            kept = []
            for item in items:
                if any(host_matches(item.domain, allowed) for allowed in request.domains):
                    kept.append(item)
                else:
                    filtered += 1
            items = kept
        seen_urls: set[str] = set()
        seen_pairs: set[tuple[str, str]] = set()
        duplicates = 0
        unique: list[SearchItem] = []
        for item in items:
            if item.url in seen_urls:
                duplicates += 1
                continue
            pair = (item.domain, item.title.strip().lower())
            if pair in seen_pairs:
                duplicates += 1
                continue
            seen_urls.add(item.url)
            seen_pairs.add(pair)
            unique.append(item)
        return WebSearchResult(
            provider=provider,
            query=request.query,
            count_requested=request.count,
            freshness=request.freshness,
            items=unique,
            filtered_by_domain=filtered,
            duplicates_removed=duplicates,
            warnings=warnings,
        )

    # -- public page reads + extraction (batch 02) ----------------------------
    async def extract_pages(self, urls: list[str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
        """Tavily Extract; billed per successful page, failures stay per-URL."""
        for url in urls:
            validate_extract_target(url)
        return await self._clients["tavily"].extract(list(urls))

    async def read_public_pages(self, urls: list[str], *, extra_domains=None,
                                resolver=socket.getaddrinfo):
        """Allowlisted official-domain reads with pinned DNS and hard caps."""
        allowed = DEFAULT_OFFICIAL_DOMAINS
        if extra_domains:
            from .url_safety import normalize_allowlist
            allowed = normalize_allowlist(list(DEFAULT_OFFICIAL_DOMAINS) + list(extra_domains))
        reader = PublicPageReader()
        return await reader.read_many(urls, allowed_domains=allowed, resolver=resolver)

    # -- capability tests (settings page, confirm-gated) ----------------------
    async def test_capability(self, provider: str, capability: str,
                              credential_version: int) -> dict:
        """Minimal real probe for one catalog capability; never fakes results."""
        if provider not in PROVIDERS:
            raise AppError("not_implemented", "此服务的适配器尚未实现，暂无可执行的能力测试；本次未发起外部请求。", 501)
        expected = {"bocha": "web_search", "tavily": ("search" if capability == "search" else "extract")}
        if capability != expected.get(provider):
            raise AppError("unknown_capability", "此服务没有所请求的能力项。", 422)
        status, message = "ok", ""
        try:
            if capability == "extract":
                succeeded, _failed = await self._clients[provider].extract(
                    ["https://www.sse.com.cn/"])
                message = "提取连通性正常。" if succeeded else "连通正常，但本次没有取得正文。"
            else:
                request = WebSearchRequest(query="FinSight 连通性测试", count=1, include_summary=False)
                items, _warnings = await self._clients[provider].search(request)
                message = "搜索连通性正常，返回 1 条结果。" if items else "连通正常，但本次搜索没有返回结果。"
        except WebSearchError as error:
            if error.category == "permission_denied":
                status, message = "permission_denied", "凭据对该能力没有权限，请检查 Key 或套餐。"
            elif error.category == "quota_limited":
                status, message = "quota_limited", "已触发频率或额度限制。"
            else:
                status, message = "failed", "探针未取得可用结果。"
        return {"capability": capability, "status": status, "message": message,
                "tested_at": utc_now_iso(), "credential_version": credential_version}
