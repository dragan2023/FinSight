"""RSS 订阅服务（V2-02）：增删改查、显式刷新、去重入库、健康与可选轮询。

- 轮询默认关闭；只有用户显式开启后才在后台运行，且只跑免费公开源。
  自动轮询期间绝不产生付费调用：实体关联只使用已缓存的代码表。
- 显式刷新（用户点击）允许经同花顺 meta 接口补查实体，结果缓存。
- 每源最多保留最近 500 条，防止本地库无限增长。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..errors import AppError
from .entity_linker import EntityLinker
from .fetcher import dedup_key, fetch_feed
from .models import FeedInfo, FeedRefreshOutcome, NewsItem, RefreshSummary, TickerLink

_RETENTION_PER_FEED = 500
_DEFAULT_INTERVAL_MINUTES = 30
_MIN_INTERVAL_SECONDS = 300
_REFRESH_CONCURRENCY = 8

# 只收录发布方明确公开的 RSS 地址。新闻仍然只是线索，关键事实必须二次核验。
RECOMMENDED_FEEDS = (
    ("国家统计局·最新发布", "https://www.stats.gov.cn/sj/zxfb/rss.xml"),
    ("国家统计局·数据解读", "https://www.stats.gov.cn/sj/sjjd/rss.xml"),
    ("中国新闻网·财经", "https://www.chinanews.com.cn/rss/finance.xml"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RssService:
    def __init__(self, storage, http, market) -> None:
        self._storage = storage
        self._http = http
        self._market = market
        self._linker = EntityLinker(storage, market)
        self._poll_task: asyncio.Task | None = None
        self._poll_stop: asyncio.Event | None = None


    # -- 同花顺官方资讯（V2-02 附加） -------------------------------------------
    # 官方免费 API（仅限流无计费）：仍由用户点击「拉取官方资讯」触发——取数需
    # 用户主动操作是边界文档 §5 的控制原则，与费用无关；上游 fetch_result 自带
    # 数据缓存，TTL 内重复点击不重复请求。单个标的失败跳过，不阻塞其他标的。
    OFFICIAL_FUND_CAP = 8
    OFFICIAL_STOCK_CAP = 8
    OFFICIAL_ARTICLES_CAP = 10

    async def official_news(self, *, fund_limit: int = 6, stock_limit: int = 6,
                            articles_per_fund: int = 5) -> dict:
        fund_limit = max(0, min(int(fund_limit), self.OFFICIAL_FUND_CAP))
        stock_limit = max(0, min(int(stock_limit), self.OFFICIAL_STOCK_CAP))
        articles_per_fund = max(1, min(int(articles_per_fund), self.OFFICIAL_ARTICLES_CAP))
        identities = list(self._market.identities())
        funds = [i for i in identities if i.asset_type in ("fund-otc", "fund-etf", "fund-lof")]
        stocks = [i for i in identities if i.asset_type == "a-share"]
        items: list[dict] = []
        warnings: list[str] = []
        today_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for identity in funds[:fund_limit]:
            fund_type = "otc" if identity.asset_type == "fund-otc" else "exchange"
            result = await self._market.fetch_fund_news(
                identity.thscode, fund_type=fund_type, limit=articles_per_fund)
            self._collect_official(result, items, warnings, identity,
                                   feed_name="同花顺基金资讯", channel="ths_fund_news")
        for identity in stocks[:stock_limit]:
            result = await self._market.fetch_anomaly_analysis(
                identity.thscode, date_ms=today_ms, security_id=identity.thscode)
            self._collect_official(result, items, warnings, identity,
                                   feed_name="同花顺异动解读", channel="ths_anomaly")
        items.sort(key=lambda item: item["published_ms"] or 0, reverse=True)
        return {"items": items, "funds": len(funds[:fund_limit]), "stocks": len(stocks[:stock_limit]),
                "warnings": warnings, "fetched_at": _now_iso()}

    def _collect_official(self, result, items: list[dict], warnings: list[str],
                          identity, *, feed_name: str, channel: str) -> None:
        from ..research.evidence_models import ResultStatus
        if result.status is not ResultStatus.AVAILABLE:
            reason = result.gaps[0].reason if result.gaps else "unavailable"
            warnings.append(f"{feed_name}「{identity.name}」暂不可用（{reason}）。")
            return
        for position, record in enumerate(result.records):
            item = (self._fund_news_item(record, identity, position) if channel == "ths_fund_news"
                    else self._anomaly_item(record, identity))
            if item is not None:
                items.append(item)

    @staticmethod
    def _field(record, name: str) -> str | None:
        field = record.fields.get(name)
        return field.value if field is not None else None

    def _fund_news_item(self, record, identity, position: int) -> dict | None:
        title = self._field(record, "title")
        if not title:
            return None
        ms_raw = self._field(record, "publish_time_ms")
        published_ms = int(ms_raw) if ms_raw and ms_raw.isdigit() else None
        return {
            "id": f"ths-fund-{identity.thscode}-{record.period_key}-{position}",
            "feed_name": "同花顺基金资讯",
            "title": title,
            "summary": self._field(record, "summary") or "",
            "link": self._field(record, "url") or "",
            "published_ms": published_ms,
            "fetched_at": _now_iso(),
            "channel": "ths_fund_news",
            "tickers": [{"thscode": identity.thscode, "name": identity.name, "confidence": "high"}],
        }

    def _anomaly_item(self, record, identity) -> dict | None:
        reason = self._field(record, "reason")
        if not reason:
            return None
        tag = self._field(record, "tag")
        event_time = self._field(record, "event_time")
        published_ms = int(event_time) if event_time and event_time.isdigit() else None
        return {
            "id": f"ths-anomaly-{identity.thscode}-{record.period_key}",
            "feed_name": "同花顺异动解读",
            "title": f"异动解读·{tag}" if tag else "异动解读",
            "summary": reason,
            "link": "",
            "published_ms": published_ms,
            "fetched_at": _now_iso(),
            "channel": "ths_anomaly",
            "tickers": [{"thscode": identity.thscode, "name": identity.name, "confidence": "high"}],
        }
    # -- 订阅源增删改查 ---------------------------------------------------------
    def add_feed(self, name: str, url: str) -> FeedInfo:
        cleaned_url = (url or "").strip()
        if not cleaned_url.lower().startswith(("http://", "https://")):
            raise AppError("validation", "订阅地址必须是 http(s) 链接。", 422)
        feed_id = uuid.uuid4().hex
        try:
            with self._storage.connect() as db:
                db.execute(
                    "INSERT INTO rss_feeds(id, name, url, enabled, created_at) VALUES(?,?,?,1,?)",
                    (feed_id, (name or "").strip(), cleaned_url, _now_iso()))
        except sqlite3.IntegrityError:
            raise AppError("request_conflict", "该订阅地址已存在。", 409) from None
        return self.get_feed(feed_id)

    def add_recommended_feeds(self) -> list[FeedInfo]:
        """幂等添加公开基础源；只建订阅，不在该操作中发起网络抓取。"""
        with self._storage.connect() as db:
            existing = {row["url"] for row in db.execute("SELECT url FROM rss_feeds").fetchall()}
        for name, url in RECOMMENDED_FEEDS:
            if url not in existing:
                self.add_feed(name, url)
                existing.add(url)
        return self.list_feeds()

    def get_feed(self, feed_id: str) -> FeedInfo:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM rss_feeds WHERE id=?", (feed_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "订阅源不存在。", 404)
        return self._feed_info(row)

    def list_feeds(self) -> list[FeedInfo]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM rss_feeds ORDER BY created_at, id").fetchall()
        return [self._feed_info(row) for row in rows]

    def update_feed(self, feed_id: str, *, name: str | None = None,
                    enabled: bool | None = None) -> FeedInfo:
        with self._storage.connect() as db:
            row = db.execute("SELECT id FROM rss_feeds WHERE id=?", (feed_id,)).fetchone()
            if row is None:
                raise AppError("not_found", "订阅源不存在。", 404)
            if name is not None:
                db.execute("UPDATE rss_feeds SET name=? WHERE id=?", (name.strip(), feed_id))
            if enabled is not None:
                db.execute("UPDATE rss_feeds SET enabled=? WHERE id=?", (1 if enabled else 0, feed_id))
        return self.get_feed(feed_id)

    def delete_feed(self, feed_id: str) -> bool:
        with self._storage.connect() as db:
            cursor = db.execute("DELETE FROM rss_feeds WHERE id=?", (feed_id,))
        if cursor.rowcount == 0:
            raise AppError("not_found", "订阅源不存在。", 404)
        return True

    def _feed_info(self, row: sqlite3.Row) -> FeedInfo:
        with self._storage.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM rss_items WHERE feed_id=?",
                               (row["id"],)).fetchone()[0]
        return FeedInfo(
            id=row["id"], name=row["name"], url=row["url"],
            enabled=bool(row["enabled"]),
            last_fetch_at=row["last_fetch_at"], last_success_at=row["last_success_at"],
            last_error=row["last_error"] or "",
            consecutive_failures=int(row["consecutive_failures"] or 0),
            item_count=count, created_at=row["created_at"])

    # -- 轮询设置 ----------------------------------------------------------------
    def get_settings(self) -> dict:
        with self._storage.connect() as db:
            rows = db.execute("SELECT key, value FROM rss_settings").fetchall()
        values = {row["key"]: row["value"] for row in rows}
        return {"poll_enabled": values.get("poll_enabled") == "1",
                "interval_minutes": int(values.get("interval_minutes") or _DEFAULT_INTERVAL_MINUTES)}

    def update_settings(self, *, poll_enabled: bool, interval_minutes: int) -> dict:
        with self._storage.connect() as db:
            for key, value in (("poll_enabled", "1" if poll_enabled else "0"),
                               ("interval_minutes", str(int(interval_minutes)))):
                db.execute("INSERT INTO rss_settings(key, value) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        return self.get_settings()

    # -- 刷新与入库 ---------------------------------------------------------------
    async def refresh(self, *, feed_id: str | None = None,
                      allow_network: bool = True) -> RefreshSummary:
        """刷新订阅源。allow_network=False 时实体关联只用本地缓存（轮询路径）。"""
        with self._storage.connect() as db:
            if feed_id is not None:
                rows = db.execute("SELECT * FROM rss_feeds WHERE id=?", (feed_id,)).fetchall()
                if not rows:
                    raise AppError("not_found", "订阅源不存在。", 404)
            else:
                rows = db.execute("SELECT * FROM rss_feeds WHERE enabled=1 ORDER BY created_at, id").fetchall()
        # 单源失败由 _refresh_one 吸收为 outcome；有界并发避免 50 源串行等待，
        # 同时不把 RSS 端点压成无上限连接风暴。gather 保持输入顺序，前端展示稳定。
        semaphore = asyncio.Semaphore(_REFRESH_CONCURRENCY)

        async def one(row):
            async with semaphore:
                return await self._refresh_one(row, allow_network=allow_network)

        outcomes = list(await asyncio.gather(*(one(row) for row in rows)))
        return RefreshSummary(outcomes=outcomes, added=sum(o.added for o in outcomes))

    async def _refresh_one(self, row: sqlite3.Row, *, allow_network: bool) -> FeedRefreshOutcome:
        feed_id, feed_name = row["id"], row["name"]
        fetched_at = _now_iso()
        try:
            _title, entries = await fetch_feed(self._http, row["url"])
        except Exception as error:  # 源级健康：单源失败不影响其他源。
            message = str(error) or error.__class__.__name__
            with self._storage.connect() as db:
                db.execute(
                    "UPDATE rss_feeds SET last_fetch_at=?, last_error=?, "
                    "consecutive_failures=consecutive_failures+1 WHERE id=?",
                    (fetched_at, message[:400], feed_id))
            return FeedRefreshOutcome(feed_id=feed_id, name=feed_name, status="failed",
                                      added=0, message=message[:200])
        texts = [entry.title + " " + entry.summary for entry in entries]
        links = await self._linker.link(texts, allow_network=allow_network)
        added = self._store_entries(feed_id, entries, links, fetched_at)
        with self._storage.connect() as db:
            db.execute(
                "UPDATE rss_feeds SET last_fetch_at=?, last_success_at=?, last_error='', "
                "consecutive_failures=0 WHERE id=?", (fetched_at, fetched_at, feed_id))
        return FeedRefreshOutcome(feed_id=feed_id, name=feed_name, status="ok", added=added)

    def _store_entries(self, feed_id: str, entries, links, fetched_at: str) -> int:
        added = 0
        with self._storage.connect() as db:
            for entry, item_links in zip(entries, links):
                key = dedup_key(entry.guid, entry.link)
                cursor = db.execute(
                    "INSERT OR IGNORE INTO rss_items(id, feed_id, guid, dedup_key, title, summary, "
                    "link, published_ms, fetched_at, tickers_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, feed_id, entry.guid, key, entry.title, entry.summary,
                     entry.link, entry.published_ms, fetched_at,
                     json.dumps([link.model_dump() for link in item_links], ensure_ascii=False)))
                added += max(cursor.rowcount, 0)
            db.execute(
                "DELETE FROM rss_items WHERE feed_id=? AND id NOT IN "
                "(SELECT id FROM rss_items WHERE feed_id=? "
                 "ORDER BY COALESCE(published_ms, 0) DESC, fetched_at DESC LIMIT ?)",
                (feed_id, feed_id, _RETENTION_PER_FEED))
        return added

    # -- 时间线查询 ----------------------------------------------------------------
    def list_items(self, *, thscode: str | None = None, feed_id: str | None = None,
                   since_ms: int | None = None, limit: int = 50,
                   offset: int = 0) -> tuple[list[NewsItem], int]:
        clauses, params = [], []
        if thscode:
            clauses.append("EXISTS (SELECT 1 FROM json_each(ri.tickers_json) je "
                           "WHERE json_extract(je.value, '$.thscode') = ?)")
            params.append(thscode)
        if feed_id:
            clauses.append("ri.feed_id=?")
            params.append(feed_id)
        if since_ms is not None:
            clauses.append("COALESCE(ri.published_ms, 0) >= ?")
            params.append(int(since_ms))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._storage.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM rss_items ri" + where, params).fetchone()[0]
            rows = db.execute(
                "SELECT ri.*, rf.name AS feed_name FROM rss_items ri "
                "JOIN rss_feeds rf ON rf.id=ri.feed_id" + where + 
                " ORDER BY COALESCE(ri.published_ms, 0) DESC, ri.fetched_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset]).fetchall()
        items = []
        for row in rows:
            links = json.loads(row["tickers_json"] or "[]")
            items.append(NewsItem(
                id=row["id"], feed_id=row["feed_id"], feed_name=row["feed_name"],
                title=row["title"], summary=row["summary"], link=row["link"],
                published_ms=row["published_ms"], fetched_at=row["fetched_at"],
                tickers=[TickerLink.model_validate(link) for link in links]))
        return items, total

    # -- 可选轮询（默认关闭）------------------------------------------------------
    async def start_polling_if_enabled(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            return
        if not self.get_settings()["poll_enabled"]:
            return
        self._poll_stop = asyncio.Event()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="rss-poll")

    async def _poll_loop(self) -> None:
        stop = self._poll_stop
        while stop is not None and not stop.is_set():
            settings = self.get_settings()
            timeout = max(_MIN_INTERVAL_SECONDS, settings["interval_minutes"] * 60)
            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout)
                break
            except asyncio.TimeoutError:
                pass
            if stop.is_set() or not self.get_settings()["poll_enabled"]:
                break
            try:
                await self.refresh(allow_network=False)  # 轮询路径：无付费调用。
            except Exception:  # 轮询永不因单次异常退出。
                continue

    async def shutdown(self) -> None:
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_stop.set()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

    def runtime_summary(self) -> dict:
        """数据来源注册表端点的启用状态。"""
        feeds = self.list_feeds()
        active = [feed for feed in feeds if feed.enabled]
        if active:
            return {"enabled": True, "detail": f"已有 {len(active)} 个启用中的订阅源，共 {sum(f.item_count for f in feeds)} 条新闻。"}
        if feeds:
            return {"enabled": False, "detail": f"已添加 {len(feeds)} 个订阅源但均未启用。"}
        return {"enabled": False, "detail": "尚未添加订阅源，可在新闻订阅页添加免费公开源。"}
