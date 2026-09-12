"""RSS 聚合的数据模型（V2-02）。字段面向用户界面的部分一律由程序转中文。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FeedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60, description="用户可见的订阅名称")
    url: str = Field(min_length=1, max_length=2000, description="RSS/Atom 订阅地址")

class FeedUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=60)
    enabled: bool | None = None

class RssSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poll_enabled: bool
    interval_minutes: int = Field(default=30, ge=5, le=720)

class FeedInfo(BaseModel):
    id: str
    name: str
    url: str
    enabled: bool
    last_fetch_at: str | None
    last_success_at: str | None
    last_error: str
    consecutive_failures: int
    item_count: int
    created_at: str

class TickerLink(BaseModel):
    thscode: str
    name: str
    confidence: str  # high / medium；低置信度不落库

class NewsItem(BaseModel):
    id: str
    feed_id: str
    feed_name: str
    title: str
    summary: str
    link: str
    published_ms: int | None
    fetched_at: str
    tickers: list[TickerLink]

class FeedRefreshOutcome(BaseModel):
    feed_id: str
    name: str
    status: str  # ok / failed
    added: int = 0
    message: str = ""

class RefreshSummary(BaseModel):
    outcomes: list[FeedRefreshOutcome]
    added: int
