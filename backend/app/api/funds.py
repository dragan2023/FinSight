"""Local fund report reads.  User actions create any refresh task separately."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from ..dependencies import research_repository
from ..research.fund_research import local_fund_research

router = APIRouter(prefix="/api/funds", tags=["fund-research"])
Repo = Annotated[object, Depends(research_repository)]

@router.get("/{fund_id}/research")
def fund_research(fund_id: str, repo: Repo):
    return local_fund_research(repo, fund_id)
