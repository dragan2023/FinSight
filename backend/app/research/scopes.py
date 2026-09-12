"""Closed, typed research scopes.  They bound assets before any tool is exposed."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

class _Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thscodes: list[str] = Field(default_factory=list, max_length=10)
    start_ms: int | None = Field(default=None, gt=0)
    end_ms: int | None = Field(default=None, gt=0)
    modules: list[str] = Field(default_factory=list, max_length=8)
class CompanyOverview(_Scope): type: Literal["company_overview"]; thscodes: list[str] = Field(min_length=1, max_length=1)
class WatchlistReview(_Scope): type: Literal["watchlist_review"]; thscodes: list[str] = Field(default_factory=list, max_length=20)
class IndexStrength(_Scope): type: Literal["index_strength"]; thscodes: list[str] = Field(min_length=1, max_length=20)
class SectorBreadth(_Scope): type: Literal["sector_breadth"]; thscodes: list[str] = Field(min_length=1, max_length=20)
class MarketPulse(_Scope): type: Literal["market_pulse"]
class FundOverview(_Scope): type: Literal["fund_overview"]; thscodes: list[str] = Field(min_length=1, max_length=1)
class FundCompare(_Scope): type: Literal["fund_compare"]; thscodes: list[str] = Field(min_length=2, max_length=10)
class MarketBreadth(_Scope): type: Literal["market_breadth"]
class General(_Scope): type: Literal["general"]
ResearchScope = Annotated[CompanyOverview | WatchlistReview | IndexStrength | SectorBreadth | MarketPulse | FundOverview | FundCompare | MarketBreadth | General, Field(discriminator="type")]
_ADAPTER = TypeAdapter(ResearchScope)

def validate_scope(value: dict) -> dict:
    scope = _ADAPTER.validate_python(value)
    dumped = scope.model_dump(exclude_none=True)
    if dumped.get("start_ms") and dumped.get("end_ms") and dumped["end_ms"] < dumped["start_ms"]:
        raise ValueError("研究范围结束日期不能早于开始日期。")
    return dumped

OLD_TYPE_TO_SCOPE = {
    "company_quality": "company_overview", "company_comparison": "company_overview", "thesis_review": "company_overview",
    "sector_relative": "sector_breadth", "fund_overlap": "fund_compare", "fund_quality": "fund_overview",
    "market_review": "market_pulse", "general": "general",
}
