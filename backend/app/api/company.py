"""Company research reads local evidence; updates remain explicit research tasks."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from ..dependencies import research_repository
from ..errors import AppError
from ..research.company import local_company_comparison, local_company_research
router=APIRouter(prefix="/api/companies",tags=["company-research"])
Repo=Annotated[object,Depends(research_repository)]
@router.get("/{thscode}/research")
def company_research(thscode:str,repo:Repo,industry:str|None=Query(default=None,max_length=100)):
    identity=repo.get_identity(thscode)
    if not identity: raise AppError("not_found","请先在数据预览中确认标的。",404)
    return {"identity":identity.model_dump(mode="json"),**local_company_research(repo,thscode,industry)}

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
