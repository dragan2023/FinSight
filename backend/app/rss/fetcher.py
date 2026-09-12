"""RSS/Atom 抓取与规范化（V2-02）。

- 只保留标题、摘要（去 HTML、限长）、链接与发布时间，版权安全。
- 去重键：GUID 优先，否则规范化链接；同一订阅源内稳定。
- 解析为纯函数，网络请求与本模块分离，便于离线夹具测试。
"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import dataclass

import feedparser

SUMMARY_MAX_CHARS = 500
TITLE_MAX_CHARS = 300
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class RawEntry:
    guid: str
    title: str
    summary: str
    link: str
    published_ms: int | None


def strip_html(text: str, *, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """去除标记与多余空白；不补造内容，超长截断。"""
    cleaned = html.unescape(_TAG_RE.sub(" ", text or ""))
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    return cleaned[:max_chars]


def normalize_link(url: str) -> str:
    """规范化链接：去空白与锚点；查询参数原样保留。"""
    base = (url or "").strip().split("#", 1)[0]
    return base


def dedup_key(guid: str, link: str) -> str:
    """GUID 优先；无 GUID 用规范化链接。同一来源内键稳定可去重。"""
    if guid:
        material = "guid:" + guid.strip()
    else:
        material = "link:" + normalize_link(link)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _entry_ms(entry: dict) -> int | None:
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return calendar.timegm(parsed) * 1000
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def parse_feed(content: bytes) -> tuple[str, list[RawEntry]]:
    """解析 RSS/Atom 字节流，返回（订阅源标题, 规范化条目）。

    feedparser 负责 XML 解析与实体清洗；bozo（格式瑕疵）时只要有条目
    仍然接受，没有条目才视为失败。
    """
    parsed = feedparser.parse(content)
    entries = []
    for entry in parsed.entries or []:
        title = strip_html(entry.get("title") or "", max_chars=TITLE_MAX_CHARS)
        link = (entry.get("link") or "").strip()
        if not title and not link:
            continue
        guid = (entry.get("id") or entry.get("guid") or "").strip()
        summary = strip_html(entry.get("summary") or entry.get("description") or "")
        entries.append(RawEntry(guid=guid, title=title, summary=summary,
                                link=link, published_ms=_entry_ms(entry)))
    feed_title = strip_html((parsed.feed or {}).get("title") or "", max_chars=120)
    return feed_title, entries


async def fetch_feed(http, url: str) -> tuple[str, list[RawEntry]]:
    """抓取订阅源。只接受 http(s)；遵循重定向；非 2xx 视为失败。"""
    if not (url or "").strip().lower().startswith(("http://", "https://")):
        raise ValueError("订阅地址必须是 http(s) 链接。")
    response = await http.get(url, follow_redirects=True)
    response.raise_for_status()
    return parse_feed(response.content)