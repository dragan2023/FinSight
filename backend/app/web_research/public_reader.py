"""Public page reader restricted by the url_safety policy (plan 04 §2.3).

Per-read limits are visible product caps: 25 MiB downloaded, 50 MiB after
decompression, 60 seconds total. Only HTML, plain text and PDF content types
are accepted; everything else is skipped before the body is read. Scripts,
images, frames and sub-resources are never fetched or executed — the reader
performs exactly one GET per URL and extracts text with the stdlib HTML
parser. Redirects are not followed: the target is reported so the user can
choose it, and the choice passes the full rules again. No credentials,
cookies or session state are ever attached.

A single failed URL never fails the batch: outcomes are returned per URL,
and extracted text is the page body (never a search-style snippet).
"""

from __future__ import annotations

import socket
import time
from html.parser import HTMLParser
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from .models import utc_now_iso
from .url_safety import (
    DEFAULT_OFFICIAL_DOMAINS,
    ParsedTarget,
    build_public_reader_client,
    resolve_and_pin,
    validate_url,
)

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024
TOTAL_TIME_BUDGET_SECONDS = 60.0
ACCEPTED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/pdf")
# PDF 魔数校验是公开读取与本地附件解析（批次4）共享的第一道内容闸门；
# 只认文件头字节，扩展名与 Content-Type 永远不当成内容证明。
PDF_MAGIC = b"%PDF"


def has_pdf_magic(data: bytes) -> bool:
    return data.startswith(PDF_MAGIC)
ReadOutcome = Literal["ok", "failed", "skipped_redirect", "skipped_content_type"]


class PublicPageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    outcome: ReadOutcome
    status_code: int | None = None
    content_type: str | None = None
    title: str | None = None
    # 全文（页面正文提取结果）；摘要永远不进入该字段。
    body: str | None = None
    pdf_size: int | None = None
    redirect_target: str | None = None
    detail: str | None = None
    retrieved_at: str


class PayloadLimitExceeded(Exception):
    """Internal signal converted into a per-URL failed outcome."""


class _TextExtractor(HTMLParser):
    """Stdlib text extraction; scripts/styles are skipped, nothing executes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skipping = 0
        self._in_title = False
        self.parts: list[str] = []
        self.title: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skipping += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skipping:
            self._skipping -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = data.strip() or None
        elif not self._skipping and data.strip():
            self.parts.append(data.strip())


def extract_html_text(html: str) -> tuple[str | None, str | None]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = " ".join(parser.parts).strip()
    return (parser.title, text or None)


class PublicPageReader:
    def __init__(
        self,
        *,
        download_limit: int = MAX_DOWNLOAD_BYTES,
        decompress_limit: int = MAX_DECOMPRESSED_BYTES,
        time_budget: float = TOTAL_TIME_BUDGET_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._download_limit = download_limit
        self._decompress_limit = decompress_limit
        self._time_budget = time_budget
        self._clock = clock

    async def read_many(self, urls, *, allowed_domains=DEFAULT_OFFICIAL_DOMAINS,
                        resolver=socket.getaddrinfo,
                        client_factory=build_public_reader_client) -> list[PublicPageResult]:
        """client_factory(pins, resolver) builds the HTTP client; tests inject
        a MockTransport-backed factory while the production default builds the
        pinned-DNS safe client."""
        deadline = self._clock() + self._time_budget
        pins: dict[str, tuple[str, ...]] = {}
        targets: list[tuple[str, ParsedTarget | None]] = []
        # One validated resolution per host per batch; the connection binds to it.
        for url in urls:
            try:
                target = validate_url(url, allowed_domains=allowed_domains)
            except Exception as error:  # noqa: BLE001 - per-URL outcome
                targets.append((url, error))  # type: ignore[list-item]
                continue
            if target.host not in pins:
                try:
                    pins[target.host] = resolve_and_pin(target.host, resolver=resolver)
                except Exception as error:  # noqa: BLE001 - per-URL outcome
                    targets.append((url, error))  # type: ignore[list-item]
                    continue
            targets.append((url, target))
        results: list[PublicPageResult] = []
        async with client_factory(pins, resolver=resolver) as client:
            for url, target in targets:
                if isinstance(target, Exception):
                    results.append(PublicPageResult(
                        url=url, outcome="failed", detail=str(target),
                        retrieved_at=utc_now_iso()))
                    continue
                results.append(await self._read_one(client, target, deadline))
        return results

    async def _read_one(self, client: httpx.AsyncClient, target: ParsedTarget,
                        deadline: float) -> PublicPageResult:
        try:
            response = await client.get(target.url)
        except Exception as error:  # noqa: BLE001 - 逐 URL 隔离；只报类型不透传上游内容
            return PublicPageResult(
                url=target.url, outcome="failed",
                detail="读取失败：" + type(error).__name__, retrieved_at=utc_now_iso())
        if 300 <= response.status_code < 400:
            # Never followed; the new target must pass the full rules if chosen.
            return PublicPageResult(
                url=target.url, outcome="skipped_redirect",
                status_code=response.status_code,
                redirect_target=response.headers.get("location"),
                detail="重定向未自动跟随；如需读取新目标，将重新执行全部安全规则。",
                retrieved_at=utc_now_iso())
        content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if content_type not in ACCEPTED_CONTENT_TYPES:
            return PublicPageResult(
                url=target.url, outcome="skipped_content_type",
                status_code=response.status_code, content_type=content_type,
                detail="仅接受 HTML/纯文本/PDF，已跳过该内容类型。", retrieved_at=utc_now_iso())
        if response.status_code != 200:
            return PublicPageResult(
                url=target.url, outcome="failed", status_code=response.status_code,
                content_type=content_type, detail="非 200 响应，未读取正文。",
                retrieved_at=utc_now_iso())
        try:
            data = await self._stream(response, deadline)
        except PayloadLimitExceeded as error:
            return PublicPageResult(
                url=target.url, outcome="failed", status_code=response.status_code,
                content_type=content_type, detail=str(error), retrieved_at=utc_now_iso())
        except Exception as error:  # noqa: BLE001 - 逐 URL 隔离；只报类型不透传上游内容
            return PublicPageResult(
                url=target.url, outcome="failed", status_code=response.status_code,
                content_type=content_type,
                detail="读取失败：" + type(error).__name__, retrieved_at=utc_now_iso())
        if self._clock() > deadline:
            return PublicPageResult(
                url=target.url, outcome="failed", status_code=response.status_code,
                content_type=content_type, detail="总时间超过 60 秒预算，读取中止。",
                retrieved_at=utc_now_iso())
        if content_type == "application/pdf":
            if not has_pdf_magic(data):
                return PublicPageResult(
                    url=target.url, outcome="failed", status_code=response.status_code,
                    content_type=content_type, detail="PDF 魔数缺失，拒绝该文件。",
                    retrieved_at=utc_now_iso())
            return PublicPageResult(
                url=target.url, outcome="ok", status_code=response.status_code,
                content_type=content_type, pdf_size=len(data),
                detail="PDF 已按大小/类型约束接收；文本解析由附件解析批次提供。",
                retrieved_at=utc_now_iso())
        encoding = response.encoding or "utf-8"
        text = data.decode(encoding, errors="replace")
        if content_type in ("text/html", "application/xhtml+xml"):
            title, body = extract_html_text(text)
        else:
            title, body = None, (text or None)
        return PublicPageResult(
            url=target.url, outcome="ok", status_code=response.status_code,
            content_type=content_type, title=title, body=body,
            retrieved_at=utc_now_iso())

    async def _stream(self, response: httpx.Response, deadline: float) -> bytes:
        chunks: list[bytes] = []
        decompressed = 0
        base = response.num_bytes_downloaded
        encoded = (response.headers.get("content-encoding") or "identity").lower()
        async for chunk in response.aiter_bytes():
            decompressed += len(chunk)
            if decompressed > self._decompress_limit:
                await response.aclose()
                raise PayloadLimitExceeded("解压后超过 50 MiB 上限，读取中止。")
            # identity 时分块累计即原始字节；压缩流退回网络读数增量
            # （预缓冲响应的读数增量恒为 0，此时由解压上限兜底）。
            if encoded == "identity":
                downloaded = decompressed
            else:
                downloaded = response.num_bytes_downloaded - base
            if downloaded > self._download_limit:
                await response.aclose()
                raise PayloadLimitExceeded("下载量超过 25 MiB 上限，读取中止。")
            if self._clock() > deadline:
                await response.aclose()
                raise PayloadLimitExceeded("总时间超过 60 秒预算，读取中止。")
            chunks.append(chunk)
        return b"".join(chunks)



