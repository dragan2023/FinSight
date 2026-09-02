"""Explicit-input market research calculations; all endpoints are local only."""
from __future__ import annotations

from decimal import InvalidOperation

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..analysis.fund_overlap import FundDisclosure, visible_overlap
from ..analysis.fund_quality import nav_quality
from ..analysis.market_context import dedupe_dragon_tiger, historical_anomaly_context, limit_ladder
from ..analysis.relative_performance import RelativeSeries, breadth, relative_performance
from ..errors import AppError

router = APIRouter(prefix="/api", tags=["market-research"])


class RelativeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series: list[RelativeSeries] = Field(min_length=2, max_length=4)
    reference_id: str


class BreadthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[dict] = Field(max_length=500)
    current_constituents_only: bool = True


class FundOverlapInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: FundDisclosure
    right: FundDisclosure


class NavInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[tuple[str, str | None]] = Field(max_length=3000)
    adjusted: bool


class MarketReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dragon_tiger: list[dict] = Field(default_factory=list, max_length=500)
    limit_pool: list[dict] = Field(default_factory=list, max_length=500)
    requested_date: str
    anomaly_supported_date: str | None = None
    anomaly_explanation: str | None = Field(default=None, max_length=2000)


@router.post("/sectors/relative-performance")
def sector_relative(data: RelativeInput):
    try:
        return relative_performance(data.series, data.reference_id).model_dump(mode="json")
    except ValueError as error:
        raise AppError("validation", str(error), 422) from None


@router.post("/sectors/breadth")
def sector_breadth(data: BreadthInput):
    return breadth(data.rows, current_constituents_only=data.current_constituents_only)


@router.post("/funds/visible-overlap")
def fund_overlap(data: FundOverlapInput):
    try:
        return visible_overlap(data.left, data.right)
    except (ValueError, InvalidOperation):
        raise AppError("validation", "披露持仓权重无法解释，未计算重叠。", 422) from None


@router.post("/funds/nav-quality")
def fund_nav_quality(data: NavInput):
    try:
        return nav_quality(data.points, adjusted=data.adjusted)
    except Exception:
        raise AppError("validation", "净值序列包含无法计算的值。", 422) from None


@router.post("/market-reviews/context")
def market_review(data: MarketReviewInput):
    return {
        "dragon_tiger": dedupe_dragon_tiger(data.dragon_tiger),
        "limit_ladder": limit_ladder(data.limit_pool),
        "anomaly": historical_anomaly_context(requested_date=data.requested_date,
                                                supported_date=data.anomaly_supported_date,
                                                explanation=data.anomaly_explanation),
        "notice": "结果只基于用户明确提供或已授权取得的本地资料；不构成交易信号。",
    }
