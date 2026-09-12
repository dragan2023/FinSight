"""Company research reads local evidence; updates remain explicit research tasks."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from ..dependencies import market_service, research_repository
from ..errors import AppError
from ..research.company import local_company_comparison, local_company_research
router=APIRouter(prefix="/api/companies",tags=["company-research"])
Repo=Annotated[object,Depends(research_repository)]
Market=Annotated[object,Depends(market_service)]
@router.get("/{thscode}/research")
def company_research(thscode:str,repo:Repo,industry:str|None=Query(default=None,max_length=100)):
    identity=repo.get_identity(thscode)
    if not identity: raise AppError("not_found","请先在数据预览中确认标的。",404)
    return {"identity":identity.model_dump(mode="json"),**local_company_research(repo,thscode,industry)}

class CompanyRefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int | None = Field(default=None, gt=0)
    end: int | None = Field(default=None, gt=0)

@router.post("/{thscode}/refresh-market")
async def refresh_company_market(thscode: str, data: CompanyRefreshInput, repo: Repo, market: Market):
    identity = repo.get_identity(thscode)
    if identity is None or identity.asset_type != "a-share":
        raise AppError("not_found", "请先确认股票身份后再更新行情。", 404)
    end = data.end or int(datetime.now(timezone.utc).timestamp() * 1000)
    start = data.start or int((datetime.now(timezone.utc) - timedelta(days=200)).timestamp() * 1000)
    history = await market.fetch_price_history(thscode, start_ms=start, end_ms=end,
                                               security_id=thscode, refresh=True)
    snapshot = await market.fetch_price_snapshot([thscode], security_id=thscode, refresh=True)
    return {"results": [history.model_dump(mode="json"), snapshot.model_dump(mode="json")]}

class ComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thscodes: list[str] = Field(min_length=2, max_length=10)
    industries: dict[str, str] = Field(default_factory=dict)

@router.post("/compare")
def compare_companies(data: ComparisonInput, repo: Repo):
    try:
        return {"comparison": local_company_comparison(repo, data.thscodes, data.industries)}
    except KeyError as error:
        raise AppError("not_found", f"标的 {error.args[0]} 尚未确认，无法纳入比较。", 404)
    except ValueError as error:
        raise AppError("validation", str(error), 422)
