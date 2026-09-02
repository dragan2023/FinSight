"""Offline contract tests for web search adapters (plan 04 §2.2 / §4)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.app.api_config.service import ApiConfigService
from backend.app.errors import AppError
from backend.app.web_research.bocha import BochaSearchClient
from backend.app.web_research.models import (
    MAX_SEARCH_LIMIT,
    SearchSourceNotConfigured,
    SearchUnauthenticated,
    SearchUpstreamRejected,
    WebSearchRequest,
)
from backend.app.web_research.service import WebResearchService
from backend.app.web_research.tavily import TavilySearchClient

BOCHA_KEY = "bocha-test-key-not-real"
TAVILY_KEY = "tavily-test-key-not-real"

BOCHA_OK = {
    "code": 200,
    "data": {"webPages": {"value": [
        {"name": "巨潮公告 标题A", "url": "https://www.cninfo.com.cn/new/a",
         "snippet": "公告摘要", "summary": "页面摘要", "siteName": "巨潮资讯网",
         "dateLastCrawled": "2026-09-01T00:00:00Z"},
        {"name": "媒体 标题B", "url": "https://finance.example.com/b",
         "siteName": "某媒体", "dateLastCrawled": "2026-09-01T01:00:00Z"},
    ]}},
}

TAVILY_OK = {
    "results": [
        {"title": "上交所公告 T1", "url": "https://www.sse.com.cn/disclosure/a",
         "content": "公告正文片段", "published_date": "2026-08-30"},
        {"title": "深交所页面 T2", "url": "https://www.szse.cn/b", "content": "片段2"},
    ],
}


class StubEncryption:
    def key_status(self, ciphertext):
        return "configured" if ciphertext else "empty"

    def decrypt(self, ciphertext):
        return SecretStr({"cipher-bocha": BOCHA_KEY, "cipher-tavily": TAVILY_KEY}[ciphertext])


class StubCredentials:
    """Duck-typed stand-in for ApiConfigService (repository + root only)."""

    def __init__(self, rows: list[dict]) -> None:
        self.repository = type("Repo", (), {"list": staticmethod(lambda: list(rows))})()
        self.root = type("Root", (), {"encryption": StubEncryption()})()


_UNSET = object()


class Harness:
    """Builds clients/service around a MockTransport and records every request.

    By default a configured bocha row is present so client-level tests have a
    credential; pass bocha_row=None (or a dict) to override.
    """

    def __init__(self, handler, *, bocha_row=_UNSET, tavily_row: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []

        def track(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request, len(self.requests))

        async def fake_sleep(seconds: float) -> None:
            self.slept.append(seconds)

        transport = httpx.MockTransport(track)
        self.http = httpx.AsyncClient(transport=transport, follow_redirects=False,
                                      trust_env=False)
        rows: list[dict] = []
        if bocha_row is _UNSET:
            rows.append(_ROW_FACTORIES["bocha"]())
        elif bocha_row is not None:
            rows.append(bocha_row)
        if tavily_row is not None:
            rows.append(tavily_row)
        self.credentials = StubCredentials(rows)
        self.service = WebResearchService(self.credentials, self.http)
        self.bocha = BochaSearchClient(
            self.http, credential_source=self.service.credential_source("bocha"),
            sleep=fake_sleep)
        self.tavily = TavilySearchClient(
            self.http, credential_source=self.service.credential_source("tavily"),
            sleep=fake_sleep)

    async def aclose(self) -> None:
        await self.http.aclose()

    @staticmethod
    def sent_body(request: httpx.Request) -> dict:
        return json.loads(request.content.decode())


def bocha_row(enabled: bool = True) -> dict:
    return {"provider": "bocha", "enabled": enabled, "encrypted_api_key": "cipher-bocha",
            "credential_version": 1}


def tavily_row(enabled: bool = True) -> dict:
    return {"provider": "tavily", "enabled": enabled, "encrypted_api_key": "cipher-tavily",
            "credential_version": 1}


# Harness 默认行工厂：__init__ 的参数名会遮蔽同名工厂函数，这里取别名。
_ROW_FACTORIES = {"bocha": bocha_row, "tavily": tavily_row}


def run(coro):
    return asyncio.run(coro)


def test_bocha_request_contract_and_normalization():
    harness = Harness(lambda request, attempt: httpx.Response(200, json=BOCHA_OK))
    try:
        result = run(harness.bocha.search(WebSearchRequest(
            query="茅台 公告", count=5, freshness="oneMonth")))
        assert len(harness.requests) == 1
        request = harness.requests[0]
        assert str(request.url) == "https://api.bocha.cn/v1/web-search"
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer " + BOCHA_KEY
        body = Harness.sent_body(request)
        assert body == {"query": "茅台 公告", "freshness": "oneMonth",
                        "summary": True, "count": 5}
        assert len(result[0]) == 2
        first = result[0][0]
        assert first.title == "巨潮公告 标题A"
        assert first.publisher == "巨潮资讯网"
        assert first.domain == "www.cninfo.com.cn"
        assert first.source == "bocha"
        assert first.storage_policy == "reference_only"
        assert first.published_at is None  # dateLastCrawled 是抓取时间，不当发布时间
        assert first.retrieved_at
        second = result[0][1]
        assert second.snippet is None
        assert second.publisher == "某媒体"
    finally:
        run(harness.aclose())


def test_bocha_envelope_rejection_retries_then_rejects():
    harness = Harness(lambda request, attempt: httpx.Response(200, json={"code": 500}))
    try:
        with pytest.raises(SearchUpstreamRejected):
            run(harness.bocha.search(WebSearchRequest(query="x")))
        assert len(harness.requests) == 3
    finally:
        run(harness.aclose())


def test_bocha_401_never_retries():
    harness = Harness(lambda request, attempt: httpx.Response(401, json={}))
    try:
        with pytest.raises(SearchUnauthenticated):
            run(harness.bocha.search(WebSearchRequest(query="x")))
        assert len(harness.requests) == 1
    finally:
        run(harness.aclose())


def test_bocha_429_retries_with_retry_after():
    def handler(request, attempt):
        if attempt == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "3"})
        return httpx.Response(200, json=BOCHA_OK)

    harness = Harness(handler)
    try:
        items, _ = run(harness.bocha.search(WebSearchRequest(query="x")))
        assert len(harness.requests) == 2
        assert harness.slept == [3.0]
        assert len(items) == 2
    finally:
        run(harness.aclose())


def test_bocha_persistent_5xx_raises_invalid_response():
    harness = Harness(lambda request, attempt: httpx.Response(503, json={}))
    try:
        with pytest.raises(AppError) as excinfo:
            run(harness.bocha.search(WebSearchRequest(query="x")))
        assert excinfo.value.category == "invalid_response"
        assert len(harness.requests) == 3
    finally:
        run(harness.aclose())


def test_bocha_invalid_items_dropped_with_warning():
    payload = {"code": 200, "data": {"webPages": {"value": [
        {"url": "https://www.cninfo.com.cn/x"},  # 缺标题
        {"name": "坏链接", "url": "not-a-url"},    # 域名无法安全解析
        {"name": "好的", "url": "https://www.cninfo.com.cn/ok", "siteName": "巨潮资讯网"},
    ]}}}
    harness = Harness(lambda request, attempt: httpx.Response(200, json=payload))
    try:
        items, warnings = run(harness.bocha.search(WebSearchRequest(query="x")))
        assert len(items) == 1
        assert items[0].url.endswith("/ok")
        assert any("丢弃" in w for w in warnings)
    finally:
        run(harness.aclose())


def test_service_local_domain_filter_and_dedupe():
    payload = {"code": 200, "data": {"webPages": {"value": [
        {"name": "A", "url": "https://www.cninfo.com.cn/a", "siteName": "巨潮"},
        {"name": "B", "url": "https://media.example.com/b", "siteName": "媒体"},
        {"name": "A 重复", "url": "https://www.cninfo.com.cn/a", "siteName": "转载"},
    ]}}}
    harness = Harness(lambda request, attempt: httpx.Response(200, json=payload),
                      bocha_row=bocha_row())
    try:
        result = run(harness.service.search("bocha", WebSearchRequest(
            query="x", domains=["cninfo.com.cn"])))
        assert [i.url for i in result.items] == ["https://www.cninfo.com.cn/a"]
        assert result.filtered_by_domain == 1
        assert result.duplicates_removed == 1
    finally:
        run(harness.aclose())


def test_count_cap_lives_in_the_contract():
    """The visible product cap is the request contract; larger asks are 422."""
    with pytest.raises(ValidationError):
        WebSearchRequest(query="x", count=50)

    seen: dict = {}

    def handler(request, attempt):
        seen["body"] = Harness.sent_body(request)
        return httpx.Response(200, json={"code": 200, "data": {"webPages": {"value": []}}})

    harness = Harness(handler, bocha_row=bocha_row())
    try:
        result = run(harness.service.search("bocha", WebSearchRequest(query="x", count=MAX_SEARCH_LIMIT)))
        assert seen["body"]["count"] == MAX_SEARCH_LIMIT
        assert result.count_requested == MAX_SEARCH_LIMIT
    finally:
        run(harness.aclose())


def test_tavily_request_contract_pins_cost_behaviour():
    harness = Harness(lambda request, attempt: httpx.Response(200, json=TAVILY_OK),
                      tavily_row=tavily_row())
    try:
        items, _ = run(harness.tavily.search(WebSearchRequest(query="茅台 年报", count=4)))
        request = harness.requests[0]
        assert str(request.url) == "https://api.tavily.com/search"
        assert request.headers["Authorization"] == "Bearer " + TAVILY_KEY
        body = Harness.sent_body(request)
        assert body["include_answer"] is False
        assert body["auto_parameters"] is False
        assert body["search_depth"] == "basic"
        assert body["max_results"] == 4
        assert "time_range" not in body
        assert "include_domains" not in body
        assert items[0].published_at == "2026-08-30"
        assert items[1].published_at is None
        assert items[0].publisher is None
        assert items[0].source == "tavily"
    finally:
        run(harness.aclose())


def test_tavily_time_range_and_domains_official_params():
    seen: dict = {}

    def handler(request, attempt):
        seen["body"] = Harness.sent_body(request)
        return httpx.Response(200, json={"results": []})

    harness = Harness(handler, tavily_row=tavily_row())
    try:
        run(harness.tavily.search(WebSearchRequest(
            query="x", freshness="oneWeek", domains=["www.cninfo.com.cn"])))
        assert seen["body"]["time_range"] == "week"
        assert seen["body"]["include_domains"] == ["www.cninfo.com.cn"]
    finally:
        run(harness.aclose())


def test_tavily_401_maps_to_unauthenticated():
    harness = Harness(lambda request, attempt: httpx.Response(401, json={}),
                      tavily_row=tavily_row())
    try:
        with pytest.raises(SearchUnauthenticated):
            run(harness.tavily.search(WebSearchRequest(query="x")))
        assert len(harness.requests) == 1
    finally:
        run(harness.aclose())


def test_service_unconfigured_provider_makes_no_request():
    harness = Harness(lambda request, attempt: httpx.Response(200, json=BOCHA_OK),
                      bocha_row=None)
    try:
        with pytest.raises(SearchSourceNotConfigured):
            run(harness.service.search("bocha", WebSearchRequest(query="x")))
        assert harness.requests == []
    finally:
        run(harness.aclose())


def test_tavily_extract_capability_probe_routes_to_extract_endpoint():
    # 批次2 起已实现 Extract；设置页能力测试应访问 /extract 而非搜索端点。
    seen = {}

    def handler(request, attempt):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"results": [], "failed_results": []})

    harness = Harness(handler, tavily_row=tavily_row())
    try:
        result = run(harness.service.test_capability("tavily", "extract", 1))
        assert result["capability"] == "extract"
        assert seen["url"] == "https://api.tavily.com/extract"
    finally:
        run(harness.aclose())


def test_bocha_capability_test_returns_recordable_result():
    harness = Harness(lambda request, attempt: httpx.Response(200, json=BOCHA_OK),
                      bocha_row=bocha_row())
    try:
        result = run(harness.service.test_capability("bocha", "web_search", 3))
        assert result["capability"] == "web_search"
        assert result["status"] == "ok"
        assert result["credential_version"] == 3
        assert result["tested_at"]
        assert len(harness.requests) == 1
    finally:
        run(harness.aclose())


def test_api_config_catalog_routing_for_web_search():
    """Both search and extract are available after batch 2."""
    spec = None
    from backend.app.api_config.catalog import INTEGRATIONS
    for entry in INTEGRATIONS:
        if entry["id"] == "tavily":
            spec = entry
    assert spec["implementation"] == "available"
    extract = next(c for c in spec["capabilities"] if c["id"] == "extract")
    assert extract["implementation"] == "available"
    search = next(c for c in spec["capabilities"] if c["id"] == "search")
    assert search["implementation"] == "available"
    bocha = next(e for e in INTEGRATIONS if e["id"] == "bocha")
    assert bocha["implementation"] == "available"
