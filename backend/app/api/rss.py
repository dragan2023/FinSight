"""RSS 订阅只读与受控写端点（V2-02）。

- 增删改查与显式刷新都是用户主动操作；删除级联清掉条目。
- 轮询开关持久化在 rss_settings，默认关闭；开启后由后台任务运行。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from ..rss.models import FeedInput, FeedUpdateInput, RssSettingsInput

router = APIRouter(tags=["news"])


def _service(request: Request):
    service = getattr(request.app.state, "rss", None)
    if service is None:
        raise AppError("not_configured", "新闻订阅服务不可用。", 409)
    return service


@router.get("/api/rss/feeds")
def list_feeds(request: Request):
    return {"feeds": [feed.model_dump(mode="json") for feed in _service(request).list_feeds()]}


@router.post("/api/rss/feeds", status_code=201)
async def add_feed(data: FeedInput, request: Request):
    feed = _service(request).add_feed(data.name, data.url)
    return {"feed": feed.model_dump(mode="json")}


@router.post("/api/rss/feeds/recommended")
async def add_recommended_feeds(request: Request):
    feeds = _service(request).add_recommended_feeds()
    return {"feeds": [feed.model_dump(mode="json") for feed in feeds]}


@router.put("/api/rss/feeds/{feed_id}")
def update_feed(feed_id: str, data: FeedUpdateInput, request: Request):
    feed = _service(request).update_feed(feed_id, name=data.name, enabled=data.enabled)
    return {"feed": feed.model_dump(mode="json")}


@router.delete("/api/rss/feeds/{feed_id}")
def delete_feed(feed_id: str, request: Request):
    return {"deleted": _service(request).delete_feed(feed_id)}


@router.post("/api/rss/feeds/{feed_id}/refresh")
async def refresh_feed(feed_id: str, request: Request):
    summary = await _service(request).refresh(feed_id=feed_id, allow_network=True)
    if not summary.outcomes:
        raise AppError("not_found", "订阅源不存在。", 404)
    return {"summary": summary.model_dump(mode="json")}


@router.post("/api/rss/refresh")
async def refresh_all(request: Request):
    summary = await _service(request).refresh(feed_id=None, allow_network=True)
    return {"summary": summary.model_dump(mode="json")}


@router.get("/api/rss/items")
def list_items(request: Request, thscode: str | None = None, feed_id: str | None = None,
               since_ms: int | None = None, limit: int = 50, offset: int = 0):
    if thscode is not None:
        thscode = thscode.strip().upper() or None
    items, total = _service(request).list_items(
        thscode=thscode, feed_id=feed_id, since_ms=since_ms, limit=limit, offset=offset)
    return {"items": [item.model_dump(mode="json") for item in items], "total": total}



class OfficialNewsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_limit: int = Field(default=6, ge=0, le=20)
    stock_limit: int = Field(default=6, ge=0, le=20)
    articles_per_fund: int = Field(default=5, ge=1, le=10)


@router.post("/api/rss/official-news")
async def official_news(data: OfficialNewsInput, request: Request):
    """用户点击触发的官方资讯拉取（同花顺官方免费开放；缓存期内重复点击不重复请求）。"""
    result = await _service(request).official_news(
        fund_limit=data.fund_limit, stock_limit=data.stock_limit,
        articles_per_fund=data.articles_per_fund)
    return result

@router.get("/api/rss/settings")
def get_settings(request: Request):
    return {"settings": _service(request).get_settings()}


@router.put("/api/rss/settings")
async def update_settings(data: RssSettingsInput, request: Request):
    service = _service(request)
    settings = service.update_settings(poll_enabled=data.poll_enabled,
                                       interval_minutes=data.interval_minutes)
    if data.poll_enabled:
        await service.start_polling_if_enabled()
    else:
        await service.shutdown()
    return {"settings": settings}
