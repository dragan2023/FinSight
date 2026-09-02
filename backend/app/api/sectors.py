"""Local sector report reads.  Calculations stay in market_research routes."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from ..dependencies import research_repository
from ..research.sectors import local_sector_research

router = APIRouter(prefix="/api/sectors", tags=["sector-research"])
Repo = Annotated[object, Depends(research_repository)]

@router.get("/{sector_id}/research")
def sector_research(sector_id: str, repo: Repo):
    return local_sector_research(repo, sector_id)
