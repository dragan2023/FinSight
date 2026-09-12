"""Explicit refresh and local reads for index and sector research."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ..dependencies import market_service, research_repository
from ..errors import AppError
from ..market_data.ths.index import INDEX_TAGS
from ..market_data.identity import SecurityIdentity
from ..research.index_research import IndexResearchService, _records, local_catalog, local_compare, local_research

router = APIRouter(prefix="/api/indices", tags=["index-research"])
Market = Annotated[object, Depends(market_service)]
Repo = Annotated[object, Depends(research_repository)]


class IndexRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history: bool = True
    constituents: bool = True
    member_snapshots: bool = False
    start_ms: int | None = Field(default=None, gt=0)
    end_ms: int | None = Field(default=None, gt=0)


class IndexCompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thscodes: list[str] = Field(min_length=2, max_length=20)
    reference_id: str


@router.get("/catalog")
def catalog(repo: Repo, tag: str = Query(default="industry")):
    if tag not in INDEX_TAGS:
        raise AppError("validation", "指数类型仅支持 cn_concept / region / tszs / industry。", 422)
    return local_catalog(repo, tag)


@router.post("/catalog/refresh")
async def refresh_catalog(tag: str, market: Market):
    if tag not in INDEX_TAGS:
        raise AppError("validation", "指数类型仅支持 cn_concept / region / tszs / industry。", 422)
    return (await market.fetch_index_catalog(tag, refresh=True)).model_dump(mode="json")


@router.post("/{thscode}/select")
def select_index(thscode: str, repo: Repo):
    """Confirm only an index already present in local catalogue evidence."""
    records, _ids = _records(repo, capability="index_catalog")
    match = next((row for row in records if row.get("thscode") == thscode), None)
    if match is None:
        raise AppError("not_found", "请先刷新目录并从目录中选择指数。", 404)
    fields = match.get("fields") or {}
    name = (fields.get("name") or {}).get("value") or thscode
    identity = SecurityIdentity(thscode=thscode, ticker=thscode.split(".")[0], name=name,
                                exchange=thscode.split(".")[-1] if thscode.endswith((".SH", ".SZ")) else None,
                                asset_type="a-share-index", currency="CNY", confirmed_source="user_selected_catalog")
    repo.upsert_identity(identity)
    return identity.model_dump(mode="json")


@router.get("/{thscode}/research")
def research(thscode: str, repo: Repo):
    return local_research(repo, thscode)


@router.post("/{thscode}/refresh")
async def refresh(thscode: str, data: IndexRefreshInput, market: Market, repo: Repo):
    return await IndexResearchService(market=market, repository=repo).refresh(
        thscode, history=data.history, constituents=data.constituents,
        member_snapshots=data.member_snapshots, start_ms=data.start_ms, end_ms=data.end_ms)


@router.post("/compare")
def compare(data: IndexCompareInput, repo: Repo):
    if data.reference_id not in data.thscodes:
        raise AppError("validation", "参考指数必须在所选指数中。", 422)
    return local_compare(repo, data.thscodes, data.reference_id)
