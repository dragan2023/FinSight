"""Public reader flow tests + Tavily extract contract (plan 04 §2.2/§2.3)."""

from __future__ import annotations

import asyncio
import gzip
import json

import httpx
import pytest
from pydantic import SecretStr

from backend.app.api_config.catalog import INTEGRATIONS
from backend.app.errors import AppError
from backend.app.web_research.models import SearchValidationError
from backend.app.web_research.public_reader import PublicPageReader
from backend.app.web_research.service import WebResearchService
from backend.app.web_research.url_safety import UrlRejected


run = asyncio.run
OFFICIAL = ("www.cninfo.com.cn", "static.cninfo.com.cn")
BOCHA_KEY = "bocha-test-key"
TAVILY_KEY = "tavily-test-key"


HTML_PAGE = """<html><head><title>巨潮资讯</title>
<script>alert('x')</script><style>body{}</style></head>
<body><h1>公告标题</h1><p>公告正文第一段。</p><img src="/logo.png"><iframe src="//evil.example"></iframe>
</body></html>"""


def _creds():
    class Encryption:
        def key_status(self, ciphertext):
            return "configured" if ciphertext else "empty"

        def decrypt(self, ciphertext):
            return SecretStr({"cipher-bocha": BOCHA_KEY, "cipher-tavily": TAVILY_KEY}[ciphertext])

    class Repository:
        def list(self):
            return [
                {"provider": "bocha", "enabled": True, "encrypted_api_key": "cipher-bocha",
                 "credential_version": 1},
                {"provider": "tavily", "enabled": True, "encrypted_api_key": "cipher-tavily",
                 "credential_version": 1},
            ]

    class Credentials:
        repository = Repository()
        root = type("R", (), {"encryption": Encryption()})()

    return Credentials()


class Harness:
    """MockTransport client + WebResearchService with stub credentials."""

    def __init__(self, handler):
        self.requests: list[httpx.Request] = []

        def tracking(request):
            self.requests.append(request)
            return handler(request)

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(tracking))
        self.service = WebResearchService(_creds(), self.http)

    async def aclose(self):
        await self.http.aclose()


def reader_handler(pages):
    def handler(request):
        page = pages[str(request.url)]
        status, ctype, body = page[0], page[1], page[2]
        headers = dict(page[3]) if len(page) > 3 else {"content-type": ctype}
        return httpx.Response(status, headers=headers, content=body)

    return handler


def make_reader(**kw):
    defaults = dict(download_limit=1_000_000, decompress_limit=1_000_000,
                    time_budget=10.0, clock=lambda: 0.0)
    defaults.update(kw)
    return PublicPageReader(**defaults)


def read(pages, urls, reader=None):
    r = reader or make_reader()
    harness = Harness(reader_handler(pages))
    try:
        return run(r.read_many(urls, allowed_domains=OFFICIAL,
                               client_factory=lambda pins, resolver: harness.http))
    finally:
        run(harness.aclose())


HTML_BYTES = HTML_PAGE.encode("utf-8")


class TestReaderFlows:
    def test_html_text_extraction(self):
        pages = {"https://www.cninfo.com.cn/a": (200, "text/html", HTML_BYTES)}
        [result] = read(pages, ["https://www.cninfo.com.cn/a"])
        assert result.outcome == "ok"
        assert result.title == "巨潮资讯"
        assert "公告正文第一段。" in result.body
        assert "alert" not in result.body and "body{}" not in result.body

    def test_subresources_never_fetched(self):
        pages = {"https://www.cninfo.com.cn/a": (200, "text/html", HTML_BYTES)}
        harness = Harness(reader_handler(pages))
        try:
            [result] = run(make_reader().read_many(
                ["https://www.cninfo.com.cn/a"], allowed_domains=OFFICIAL,
                client_factory=lambda pins, resolver: harness.http))
            assert len(harness.requests) == 1  # 仅一次 GET，无图片/iframe 请求
        finally:
            run(harness.aclose())

    def test_plain_text_and_pdf_and_bad_magic(self):
        pages = {
            "https://www.cninfo.com.cn/t.txt": (200, "text/plain", "纯文本内容".encode()),
            "https://www.cninfo.com.cn/p.pdf": (200, "application/pdf", b"%PDF-1.4 rest"),
            "https://www.cninfo.com.cn/fake.pdf": (200, "application/pdf", b"not a pdf"),
        }
        results = read(pages, list(pages))
        assert results[0].body == "纯文本内容"
        assert results[1].outcome == "ok" and results[1].pdf_size == len(b"%PDF-1.4 rest")
        assert results[2].outcome == "failed" and "魔数" in results[2].detail

    def test_unsupported_content_type_skipped_before_body(self):
        pages = {"https://www.cninfo.com.cn/x.js": (200, "application/javascript", b"let x=1;")}
        [result] = read(pages, ["https://www.cninfo.com.cn/x.js"])
        assert result.outcome == "skipped_content_type"

    def test_redirect_reported_not_followed(self):
        pages = {"https://www.cninfo.com.cn/r": (
            301, "text/html", b"",
            {"content-type": "text/html", "location": "https://static.cninfo.com.cn/next"})}
        harness = Harness(reader_handler(pages))
        try:
            [result] = run(make_reader().read_many(
                ["https://www.cninfo.com.cn/r"], allowed_domains=OFFICIAL,
                client_factory=lambda pins, resolver: harness.http))
            assert result.outcome == "skipped_redirect"
            assert result.redirect_target == "https://static.cninfo.com.cn/next"
            assert len(harness.requests) == 1  # 新目标不会自动访问
            sent = harness.requests[0]
            assert "cookie" not in sent.headers and "authorization" not in sent.headers
        finally:
            run(harness.aclose())

    def test_decompress_bomb_capped(self):
        bomb = gzip.compress(b"A" * 5000)
        pages = {"https://www.cninfo.com.cn/bomb": (
            200, "text/plain", bomb, {"content-type": "text/plain", "content-encoding": "gzip"})}
        [result] = read(pages, ["https://www.cninfo.com.cn/bomb"],
                        reader=make_reader(decompress_limit=1000))
        assert result.outcome == "failed" and "50 MiB" in result.detail

    def test_download_cap(self):
        pages = {"https://www.cninfo.com.cn/big": (200, "text/plain", b"B" * 5000)}
        [result] = read(pages, ["https://www.cninfo.com.cn/big"],
                        reader=make_reader(download_limit=1000))
        assert result.outcome == "failed" and "25 MiB" in result.detail

    def test_time_budget_enforced(self):
        ticks = iter([0.0, 100.0, 200.0, 300.0])
        reader = make_reader(time_budget=1.0, clock=lambda: next(ticks))
        pages = {"https://www.cninfo.com.cn/slow": (200, "text/plain", b"C" * 3000)}
        [result] = read(pages, ["https://www.cninfo.com.cn/slow"], reader=reader)
        assert result.outcome == "failed" and "60 秒" in result.detail

    def test_unapproved_domain_is_per_url_failure(self):
        pages = {"https://evil.com/a": (200, "text/plain", b"x")}
        [result] = read(pages, ["https://evil.com/a"])
        assert result.outcome == "failed" and "允许清单" in result.detail

    def test_connection_failure_isolated(self):
        def handler(request):
            if request.url.host == "www.cninfo.com.cn":
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

        harness = Harness(handler)
        try:
            results = run(make_reader().read_many(
                ["https://www.cninfo.com.cn/down", "https://static.cninfo.com.cn/up"],
                allowed_domains=OFFICIAL,
                client_factory=lambda pins, resolver: harness.http))
            assert [r.outcome for r in results] == ["failed", "ok"]
        finally:
            run(harness.aclose())


class TestTavilyExtract:
    def _extract(self, payload, urls=None):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.read())
            return httpx.Response(200, json=payload)

        harness = Harness(handler)
        try:
            if urls is None:
                urls = ["https://www.sse.com.cn/a", "https://www.szse.com.cn/b"]
            result = run(harness.service.extract_pages(urls))
            return result, seen
        finally:
            run(harness.aclose())

    def test_contract_and_normalization(self):
        payload = {
            "results": [{"url": "https://www.sse.com.cn/a", "raw_content": "公告全文"}],
            "failed_results": [
                {"url": "https://www.szse.com.cn/b", "error": "timeout"},
                "https://www.cninfo.com.cn/c",
            ],
        }
        (succeeded, failed), seen = self._extract(payload)
        assert seen["url"] == "https://api.tavily.com/extract"
        assert seen["auth"] == "Bearer " + TAVILY_KEY
        assert seen["body"] == {"urls": ["https://www.sse.com.cn/a", "https://www.szse.com.cn/b"],
                                "extract_depth": "basic"}
        assert succeeded == {"https://www.sse.com.cn/a": "公告全文"}
        assert failed == [("https://www.szse.com.cn/b", "timeout"),
                          ("https://www.cninfo.com.cn/c", "提取失败")]

    def test_missing_raw_content_not_invented(self):
        payload = {"results": [{"url": "https://www.sse.com.cn/a", "raw_content": None}],
                   "failed_results": []}
        (succeeded, failed), _ = self._extract(payload)
        assert succeeded == {} and failed == []

    def test_caps(self):
        with pytest.raises(SearchValidationError) as excinfo:
            self._extract({"results": [], "failed_results": []},
                          urls=["https://www.sse.com.cn/" + str(i) for i in range(6)])
        assert excinfo.value.status_code == 422
        with pytest.raises(SearchValidationError):
            self._extract({"results": [], "failed_results": []}, urls=[])

    @pytest.mark.parametrize("url", [
        "http://www.sse.com.cn/a",
        "https://user@www.sse.com.cn/a",
        "https://192.168.1.1/a",
        "https://127.0.0.1/a",
        "https://169.254.169.254/latest",
    ])
    def test_extract_target_precheck(self, url):
        harness = Harness(lambda request: httpx.Response(200, json={"results": [], "failed_results": []}))
        try:
            with pytest.raises(UrlRejected):
                run(harness.service.extract_pages([url]))
            assert len(harness.requests) == 0  # 校验拒绝，未发起付费调用
        finally:
            run(harness.aclose())


class TestWiring:
    def test_catalog_extract_available(self):
        tavily = next(i for i in INTEGRATIONS if i["id"] == "tavily")
        extract = next(c for c in tavily["capabilities"] if c["id"] == "extract")
        assert extract["implementation"] == "available"

    def test_capability_test_tavily_extract(self):
        payload = {"results": [{"url": "https://www.sse.com.cn/", "raw_content": "首页"}],
                   "failed_results": []}

        def handler(request):
            assert str(request.url) == "https://api.tavily.com/extract"
            return httpx.Response(200, json=payload)

        harness = Harness(handler)
        try:
            view = run(harness.service.test_capability("tavily", "extract", 1))
            assert view["status"] == "ok" and view["capability"] == "extract"
            assert view["credential_version"] == 1
        finally:
            run(harness.aclose())

    def test_unknown_capability_still_refused(self):
        harness = Harness(lambda request: httpx.Response(200, json={}))
        try:
            with pytest.raises(AppError) as excinfo:
                run(harness.service.test_capability("tavily", "nonexistent", 1))
            assert excinfo.value.status_code == 422
        finally:
            run(harness.aclose())
