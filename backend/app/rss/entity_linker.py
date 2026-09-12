"""新闻条目的标的实体关联（V2-02）。

- 候选：标题/摘要中的 6 位代码（含可选 .SH/.SZ/.BJ 后缀）与代码表中的
  公司简称（长度至少 3，避免大量误命中）。
- 解析优先走本地缓存的代码表（data_cache，provider=meta）；显式刷新
  （用户主动操作）允许经同花顺 meta 接口拉取/补查，结果一律缓存。
- 置信度：完整代码且在代码表中命中=high；其余可解析命中=medium；
  无法解析的一律不关联（置信度低的不强关联，计划 V2-02 批次 3）。
- 自动轮询不产生任何付费调用：无缓存代码表时直接跳过关联。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from ..errors import AppError
from .models import TickerLink

_CODE_RE = re.compile(r"(?<![0-9A-Za-z])(\d{6})(?:\.(SH|SZ|BJ))?(?![0-9])", re.IGNORECASE)
_CACHE_KEY = "rss:ticker_table:a-share"
_CACHE_TTL = timedelta(hours=24)
_MIN_NAME_LEN = 3
_MAX_LINKS_PER_ITEM = 8
_CONFIDENCE_RANK = {"high": 2, "medium": 1}


class EntityLinker:
    def __init__(self, storage, market, *, now: datetime | None = None) -> None:
        self._storage = storage
        self._market = market
        self._now = now or datetime.now(timezone.utc)
        self._search_cache: dict[str, list[dict]] = {}

    # -- 代码表缓存 ------------------------------------------------------------
    def _cached_table(self) -> list[dict] | None:
        with self._storage.connect() as db:
            row = db.execute(
                "SELECT payload_json, expires_at FROM data_cache WHERE cache_key=?",
                (_CACHE_KEY,)).fetchone()
        if row is None:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) < self._now:
                return None
        except ValueError:
            return None
        rows = json.loads(row["payload_json"])
        return rows if isinstance(rows, list) else None

    def _store_table(self, rows: list[dict]) -> None:
        payload = json.dumps(rows, ensure_ascii=False)
        expires = (self._now + _CACHE_TTL).isoformat()
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO data_cache(cache_key, provider, profile_id, credential_version, "
                "endpoint_path, schema_version, payload_json, created_at, expires_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json, "
                "created_at=excluded.created_at, expires_at=excluded.expires_at",
                (_CACHE_KEY, "meta", None, 1, "/api/meta/tickers/list", "v1", payload,
                 self._now.isoformat(), expires))

    async def _table(self, *, allow_network: bool) -> list[dict]:
        rows = self._cached_table()
        if rows is not None:
            return rows
        if not allow_network or self._market is None:
            return []
        data = await self._market.ticker_table()
        rows = [dict(item) for item in (data.get("item") or [])]
        self._store_table(rows)
        return rows

    # -- 候选解析 ---------------------------------------------------------------
    def _resolve_from_table(self, code: str, suffix: str | None,
                            by_thscode: dict[str, dict], by_ticker: dict[str, dict]):
        if suffix:
            thscode = code + "." + suffix.upper()
            hit = by_thscode.get(thscode)
            if hit is not None:
                return thscode, hit.get("name") or "", "high"
            return None  # 表中未验证的后缀码交给检索补查，补查不到则按低置信处理
        hit = by_ticker.get(code)
        if hit is not None:
            return hit["thscode"], hit.get("name") or "", "medium"
        return None

    async def _search_fallback(self, code: str, suffix: str):
        """后缀代码不在缓存表中：经 meta 检索补查（仅显式刷新路径，结果缓存）。"""
        if self._market is None:
            return None
        cached = self._search_cache.get(code)
        if cached is None:
            try:
                candidates = await self._market.resolve_candidates(code, limit=5)
            except AppError:
                return None
            cached = [candidate.model_dump() for candidate in candidates]
            self._search_cache[code] = cached
        wanted = (code + "." + suffix.upper()).upper()
        for item in cached:
            if str(item.get("thscode", "")).upper() == wanted:
                return item["thscode"], item.get("name") or "", "high"
        return None

    async def link(self, texts: list[str], *, allow_network: bool) -> list[list[TickerLink]]:
        """对每段文本给出关联标的；无缓存且不允许联网时返回空关联。"""
        table_rows = await self._table(allow_network=allow_network)
        by_thscode = {row["thscode"]: row for row in table_rows if row.get("thscode")}
        by_ticker = {row.get("ticker"): row for row in table_rows if row.get("ticker")}
        names: dict[str, dict] = {}
        for row in table_rows:
            name = (row.get("name") or "").strip()
            if len(name) >= _MIN_NAME_LEN:
                names.setdefault(name, row)
        results: list[list[TickerLink]] = []
        for text in texts:
            found: dict[str, TickerLink] = {}
            for match in _CODE_RE.finditer(text or ""):
                code, suffix = match.group(1), match.group(2)
                resolved = self._resolve_from_table(code, suffix, by_thscode, by_ticker)
                if resolved is None and suffix:
                    if allow_network:
                        resolved = await self._search_fallback(code, suffix)
                    if resolved is None:
                        # 结构合法但未能验证：保留中等置信，不丢弃
                        resolved = (code + "." + suffix.upper(), "", "medium")
                if resolved is not None:
                    thscode, name, confidence = resolved
                    found[thscode] = TickerLink(thscode=thscode, name=name, confidence=confidence)
            for name, row in names.items():
                if name in (text or "") and row.get("thscode"):
                    thscode = row["thscode"]
                    candidate = TickerLink(thscode=thscode, name=name, confidence="medium")
                    existing = found.get(thscode)
                    if existing is None or _CONFIDENCE_RANK[candidate.confidence] > _CONFIDENCE_RANK[existing.confidence]:
                        found[thscode] = candidate
            links = sorted(found.values(),
                           key=lambda item: -_CONFIDENCE_RANK[item.confidence])[:_MAX_LINKS_PER_ITEM]
            results.append(links)
        return results