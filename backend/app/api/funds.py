"""Local fund report reads.  User actions create any refresh task separately."""
from __future__ import annotations
import asyncio
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from ..dependencies import market_service, research_repository
from ..errors import AppError
from ..research.fund_research import local_fund_research
from ..research.fund_service import FundResearchService

router = APIRouter(prefix="/api/funds", tags=["fund-research"])
Repo = Annotated[object, Depends(research_repository)]
Market = Annotated[object, Depends(market_service)]

class FundRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_fund_type: Literal["otc", "exchange", "reits"]
    modules: list[Literal["profile", "portfolio", "performance", "people", "financials", "diagnostics", "news", "market"]] = Field(default_factory=lambda: ["profile", "performance"])
    range_name: str = "year"
    report_type: str | None = Field(default=None, min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    report_end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

class FundCompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fund_ids: list[str] = Field(min_length=2, max_length=10)

@router.get("/search")
async def search_funds(market: Market, q: str = Query(min_length=1, max_length=100)):
    return {"items": [item.model_dump(mode="json") for item in await market.resolve_candidates(q, asset_type="fund-otc,fund-etf,fund-lof", limit=20)]}

@router.get("/{fund_id}/research")
def fund_research(fund_id: str, repo: Repo):
    return local_fund_research(repo, fund_id)

@router.post("/{thscode}/refresh")
async def refresh_fund(thscode: str, data: FundRefreshInput, market: Market):
    modules = set(data.modules)
    if not modules:
        raise AppError("validation", "至少选择一个基金数据模块。", 422)
    try:
        return await FundResearchService(market).refresh(
            thscode, fund_type=data.provider_fund_type, modules=modules, range_name=data.range_name,
            report_type=data.report_type, report_end=data.report_end)
    except ValueError as error:
        raise AppError("validation", str(error), 422) from error

@router.post("/compare")
def compare_funds(data: FundCompareInput, repo: Repo):
    """Comparison is intentionally based only on saved research artifacts."""
    return {
        "fund_ids": data.fund_ids,
        "items": [local_fund_research(repo, fund_id) for fund_id in data.fund_ids],
        "notice": "比较仅使用本地已保存且共同期间可比的研究报告；缺少共同窗口时不生成比较指标。",
    }

@router.get("/{thscode}/news")
async def fund_news(thscode: str, market: Market, fund_type: Literal["otc", "exchange", "reits"],
                    limit: int = Query(default=20, ge=1, le=100), offset: str | None = None):
    result = await market.fetch_fund_news(thscode, fund_type=fund_type, limit=limit, offset=offset)
    return {"result": result.model_dump(mode="json"), "notice": "仅保存标题、时间、来源和 URL；不会提取资讯正文。"}

@router.get("/{thscode}/report-dates")
async def fund_report_dates(thscode: str, market: Market, fund_type: Literal["otc", "exchange", "reits"]):
    """Explicitly discover selectable disclosure periods; no holdings are fetched here."""
    stock, bond = await asyncio.gather(
        market.fetch_fund_report_dates(thscode, fund_type=fund_type, holding="stock", refresh=True),
        market.fetch_fund_report_dates(thscode, fund_type=fund_type, holding="bond", refresh=True),
    )
    items = []
    for holding, result in (("stock", stock), ("bond", bond)):
        for record in result.records:
            fields = record.get("fields") or {}
            report_type = (fields.get("report_type") or {}).get("value")
            end_date = (fields.get("end_date") or {}).get("value")
            if report_type and end_date:
                items.append({"holding": holding, "report_type": report_type, "end_date": end_date})
    return {"items": items, "results": {"stock": stock.model_dump(mode="json"), "bond": bond.model_dump(mode="json")},
            "notice": "仅发现已披露报告期；选择后才会在下一次刷新中请求对应历史持仓。"}

@router.get("/offerings/list")
async def fund_offerings(market: Market, subscribe: Literal["active", "upcoming"]):
    result = await market.fetch_fund_offerings(subscribe=subscribe)
    return {"result": result.model_dump(mode="json"), "notice": "募集列表仅用于信息浏览，不构成投资建议。"}
