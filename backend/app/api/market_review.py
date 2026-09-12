"""Local market-review list; no background market polling."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from ..dependencies import market_service, research_repository
from ..research.market_review import local_market_reviews
from ..research.market_observation import MarketObservationService

router = APIRouter(prefix="/api/market-reviews", tags=["market-review"])
Repo = Annotated[object, Depends(research_repository)]
Market = Annotated[object, Depends(market_service)]

class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_ms: int = Field(gt=0)
    modules: list[str] = Field(default_factory=list, max_length=10)
    max_calls: int = Field(default=6, ge=1, le=10)

class AuctionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thscode: str
    stage: str = "final"

class HotTrendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thscode: str
    start_ms: int = Field(gt=0)
    end_ms: int = Field(gt=0)

@router.get("")
def market_reviews(repo: Repo):
    return local_market_reviews(repo)

@router.get("/latest")
def latest_market_review(repo: Repo):
    rows = repo.list_market_observations(limit=1)
    return {"observation": rows[0] if rows else None, "local_only": True}

@router.get("/{date}")
def market_review_by_date(date: str, repo: Repo):
    return {"date": date, "observations": repo.list_market_observations(date), "local_only": True}

@router.post("/refresh")
async def refresh_market_review(data: RefreshInput, repo: Repo, market: Market):
    return await MarketObservationService(repo, market).refresh(date_ms=data.date_ms, modules=data.modules, max_calls=data.max_calls)

@router.post("/auction")
async def auction(data: AuctionInput, market: Market):
    return (await market.fetch_auction_snapshot(data.thscode, stage=data.stage, refresh=True)).model_dump(mode="json")

@router.post("/hot-rank-trend")
async def hot_rank_trend(data: HotTrendInput, market: Market):
    return (await market.fetch_hot_rank_trend(data.thscode, start_ms=data.start_ms, end_ms=data.end_ms, refresh=True)).model_dump(mode="json")
