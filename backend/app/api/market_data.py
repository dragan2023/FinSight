"""Stage 02 data endpoints: resolve, fetch, evidence read.

Fetch requires a confirmed identity (saved via resolve) except for the
trading calendar. Responses return DataResult lists so multi-statement
fetches and single fetches share one shape; data absence is a gap inside a
200 response, never a 500.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import market_service
from ..errors import AppError
from ..market_data.identity import SecurityIdentity, TickerCandidate
from ..market_data.service import MarketDataService

router = APIRouter(prefix="/api/data", tags=["market-data"])
EvidenceRouter = APIRouter(prefix="/api/evidence", tags=["market-data"])
Service = Annotated[MarketDataService, Depends(market_service)]

EVIDENCE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    confirm_thscode: str | None = None
    exchange: Literal["SH", "SZ", "BJ"] | None = None
    asset_type: Literal["a-share", "a-share-index", "fund-otc", "fund-etf", "fund-lof"] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class FetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: Literal[
        "price_snapshot", "price_history", "valuations",
        "financial_statements", "financial_indicators", "trading_days",
    ]
    thscode: str | None = None
    start: int | None = Field(default=None, gt=0)
    end: int | None = Field(default=None, gt=0)
    adjust: Literal["none", "forward", "backward"] | None = None
    period: Literal["annual", "quarterly"] | None = None
    limit: int | None = Field(default=None, ge=1, le=20)
    report: str | None = None
    statement: Literal["income", "balance", "cashflow", "all"] | None = None
    refresh: bool = False


def _require_thscode(data: FetchInput) -> str:
    if not data.thscode:
        raise AppError("validation", "该能力需要先确认标的。", 422)
    return data.thscode


@router.post("/resolve")
async def resolve(data: ResolveInput, service: Service):
    if data.query and data.confirm_thscode:
        raise AppError("validation", "检索与确认只能二选一。", 422)
    if data.query:
        candidates = await service.resolve_candidates(
            data.query, exchange=data.exchange, asset_type=data.asset_type, limit=data.limit)
        return {"candidates": [candidate.model_dump() for candidate in candidates]}
    if data.confirm_thscode:
        # Server-side confirmation: the exact code must come back from search.
        candidates = await service.resolve_candidates(data.confirm_thscode, limit=50)
        exact = [item for item in candidates if item.thscode == data.confirm_thscode]
        if len(exact) != 1:
            raise AppError("not_found", "未能唯一确认该标的，请改用检索后从列表中选择。", 404)
        identity = service.confirm_identity(exact[0], "user_selected")
        return {"identity": identity.model_dump()}
    raise AppError("validation", "请提供检索关键词或待确认的标的代码。", 422)


@router.get("/identities")
def identities(service: Service):
    return {"identities": [identity.model_dump() for identity in service.identities()]}


@router.post("/fetch")
async def fetch(data: FetchInput, service: Service):
    if data.capability == "trading_days":
        if not data.start or not data.end:
            raise AppError("validation", "交易日历需要开始与结束时间。", 422)
        result = await service.fetch_trading_days(start_ms=data.start, end_ms=data.end,
                                                  refresh=data.refresh)
        return {"results": [result.model_dump()]}

    thscode = _require_thscode(data)
    service.require_stock_identity(thscode)
    security_id = thscode
    if data.capability == "price_snapshot":
        result = await service.fetch_price_snapshot([thscode], security_id=security_id,
                                                    refresh=data.refresh)
        return {"results": [result.model_dump()]}
    if data.capability == "valuations":
        result = await service.fetch_valuations([thscode], security_id=security_id,
                                                refresh=data.refresh)
        return {"results": [result.model_dump()]}
    if data.capability == "price_history":
        if not data.start or not data.end:
            raise AppError("validation", "历史价格需要开始与结束时间（毫秒时间戳）。", 422)
        result = await service.fetch_price_history(
            thscode, start_ms=data.start, end_ms=data.end,
            adjust=data.adjust or "forward", security_id=security_id, refresh=data.refresh)
        return {"results": [result.model_dump()]}
    if data.capability == "financial_statements":
        statement = data.statement or "all"
        period = data.period or "annual"
        if statement == "all":
            results = await service.fetch_all_statements(
                thscode, period=period, limit=data.limit, security_id=security_id,
                refresh=data.refresh)
        else:
            results = [await service.fetch_statement(
                statement, thscode, period=period, limit=data.limit,
                security_id=security_id, refresh=data.refresh)]
        return {"results": [result.model_dump() for result in results]}
    # financial_indicators
    if not data.report:
        raise AppError("validation", "财务指标需要报告期，例如 2024-4。", 422)
    result = await service.fetch_indicators(thscode, data.report, security_id=security_id,
                                            refresh=data.refresh)
    return {"results": [result.model_dump()]}


@EvidenceRouter.get("/{evidence_id}")
def evidence(evidence_id: str, service: Service):
    # Evidence ids are 32-hex chars; anything else (including traversal shapes)
    # is rejected before any lookup. Lookups hit a primary key, never a path.
    if not EVIDENCE_ID_PATTERN.match(evidence_id):
        raise AppError("not_found", "证据不存在或已被清理。", 404)
    found = service.evidence(evidence_id)
    if found is None:
        raise AppError("not_found", "证据不存在或已被清理。", 404)
    meta, records, gaps = found
    return {"evidence": {**meta.model_dump(mode="json"), "records": records, "gaps": gaps}}
