"""Local market-review list; no background market polling."""
from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Depends
from ..dependencies import research_repository
from ..research.market_review import local_market_reviews

router = APIRouter(prefix="/api/market-reviews", tags=["market-review"])
Repo = Annotated[object, Depends(research_repository)]

@router.get("")
def market_reviews(repo: Repo):
    return local_market_reviews(repo)
