"""Confirmed security identity for data fetching (plan 02 §2.2)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError
from .models import THSCODE_PATTERN

# Tushare A 股 ts_code 后缀域（SH/SZ/BJ 与同花顺后缀一致，但映射必须显式校验）。
TUSHARE_TS_CODE_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


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
    thscode: str = Field(pattern=THSCODE_PATTERN.pattern)
    ticker: str
    name: str
    exchange: str | None = None
    asset_type: str
    currency: str
    confirmed_source: str
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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



def ts_code_from_thscode(thscode: str) -> str:
    """显式的同花顺代码 -> Tushare ts_code 映射点；不做盲拼接。

    只接受后缀在 Tushare A 股后缀域（SH/SZ/BJ）内的完整代码；其他后缀
    属于映射未知，必须走人工核对，而不是猜一个 ts_code 出来。
    """
    code = (thscode or "").strip()
    if not TUSHARE_TS_CODE_PATTERN.match(code):
        raise AppError("validation", "该代码不在 Tushare A 股代码映射范围内，请先核对代码映射。", 422)
    return code


def verify_stock_basic_mapping(row: dict, ts_code: str) -> bool:
    """官方 stock_basic 行自身携带相同 ts_code 且有名称时，映射才算核验通过。"""
    if not isinstance(row, dict):
        return False
    return row.get("ts_code") == ts_code and bool(str(row.get("name") or "").strip())
