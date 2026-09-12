"""Confirmed security identity for data fetching (plan 02 §2.2)."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .instrument_types import validate_instrument_thscode


class TickerCandidate(BaseModel):
    """One row from /api/meta/tickers/search; kept verbatim, no guessing."""

    model_config = ConfigDict(extra="ignore")

    thscode: str
    ticker: str
    name: str
    exchange: str | None = None
    asset_type: str
    currency: str


class SecurityIdentity(BaseModel):
    """The single security a user confirmed before any data is fetched."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "hexin"
    thscode: str
    ticker: str
    name: str
    exchange: str | None = None
    asset_type: str
    currency: str
    confirmed_source: str
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def validate_confirmed_instrument(self) -> "SecurityIdentity":
        self.thscode = validate_instrument_thscode(self.asset_type, self.thscode)
        return self


def candidate_from_row(row: dict) -> TickerCandidate:
    """Validate one search row; unknown shapes raise instead of being coerced."""
    return TickerCandidate.model_validate(row)


def identity_from_candidate(candidate: TickerCandidate, confirmed_source: str) -> SecurityIdentity:
    """Build the stored identity after the user picked (or a unique match)."""
    return SecurityIdentity(
        thscode=candidate.thscode,
        ticker=candidate.ticker,
        name=candidate.name,
        exchange=candidate.exchange,
        asset_type=candidate.asset_type,
        currency=candidate.currency,
        confirmed_source=confirmed_source,
    )
