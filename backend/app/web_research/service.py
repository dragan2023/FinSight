"""Web research service: single compliant provider plus shared local policy.

博查是唯一搜索提供方（付费官方 API，用户自带 Key，仅用户主动触发）。
Policy that must not depend on provider behaviour lives here: the local domain
filter, deduplication and the visible count cap. Reprint detection has no
reliable signal, so only identical entries are deduplicated and the original
publisher field is preserved untouched; near-duplicates across domains are
kept and left for human/model judgement. Nothing is persisted:
results are reference-only leads.

合规红线（docs/API与数据使用边界.md §4）：本服务只调用博查搜索 API 并使用
其返回的标题/摘要/链接；不抓取、不下载、不解析任何网页正文，产品内不存在
自建网页抓取技术。
"""

from __future__ import annotations

import httpx

from ..api_config.service import ApiConfigService
from ..errors import AppError
from .bocha import BochaSearchClient
from .models import (
    PROVIDERS,
    SearchPermissionDenied,
    SearchUnauthenticated,
    SearchItem,
    WebSearchError,
    WebSearchRequest,
    WebSearchResult,
    host_matches,
    utc_now_iso,
)


class WebResearchService:
    def __init__(self, credentials: ApiConfigService, http: httpx.AsyncClient) -> None:
        self._credentials = credentials
        self._clients = {
            "bocha": BochaSearchClient(http, credential_source=self.credential_source("bocha")),
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

    # -- capability tests (settings page, confirm-gated) ----------------------
    async def test_capability(self, provider: str, capability: str,
                              credential_version: int) -> dict:
        """Minimal real probe for one catalog capability; never fakes results."""
        if provider not in PROVIDERS:
            raise AppError("not_implemented", "此服务的适配器尚未实现，暂无可执行的能力测试；本次未发起外部请求。", 501)
        if capability != "web_search":
            raise AppError("unknown_capability", "此服务没有所请求的能力项。", 422)
        status, message = "ok", ""
        try:
            # 先走官方余额接口，避免把 API Key 未认证、账号权限和
            # Web Search 请求本身的问题混为"权限不足"。余额不会返回给前端。
            await self._clients["bocha"].balance()
            request = WebSearchRequest(query="FinSight 连通性测试", count=1, include_summary=False)
            items, _warnings = await self._clients["bocha"].search(request)
            message = "账户认证与搜索连通性正常，返回 1 条结果。" if items else "账户认证正常，但本次搜索没有返回结果。"
        except WebSearchError as error:
            if isinstance(error, SearchUnauthenticated):
                status, message = "permission_denied", "博查未接受该 API Key。请在开放平台核对 Key；输入框只需粘贴 Key 本体，不要包含 Bearer 前缀。"
            elif isinstance(error, SearchPermissionDenied):
                status, message = "permission_denied", "博查账号暂未获得网页搜索权限，或余额不足。请在开放平台核对服务权限与余额。"
            elif error.category == "quota_limited":
                status, message = "quota_limited", "已触发频率或额度限制。"
            else:
                status, message = "failed", "探针未取得可用结果。"
        return {"capability": capability, "status": status, "message": message,
                "tested_at": utc_now_iso(), "credential_version": credential_version}