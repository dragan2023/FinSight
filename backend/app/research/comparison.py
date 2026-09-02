"""Collection boundary for a user-selected company comparison group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..analysis.company_comparison import ComparisonCompany, validate_group
from ..market_data.service import MarketDataService


class PeerCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: ComparisonCompany
    statements: dict[str, dict] = Field(default_factory=dict)
    valuation: dict | None = None
    gaps: list[dict] = Field(default_factory=list)


async def collect_selected_peers(service: MarketDataService, companies: list[ComparisonCompany], *,
                                 period: str = "annual", limit: int = 4) -> list[PeerCollection]:
    """Fetch only the explicit group; a failed peer never suppresses other peers."""
    validate_group(companies)
    collected: list[PeerCollection] = []
    for company in companies:
        service.require_identity(company.thscode)
        statements = await service.fetch_all_statements(company.thscode, period=period, limit=limit,
                                                        security_id=company.security_id)
        valuation = await service.fetch_valuations([company.thscode], security_id=company.security_id)
        gaps = [gap.model_dump() for result in [*statements, valuation] for gap in result.gaps]
        collected.append(PeerCollection(
            company=company,
            statements={name: result.model_dump() for name, result in zip(("income", "balance", "cashflow"), statements)},
            valuation=valuation.model_dump(), gaps=gaps))
    return collected
