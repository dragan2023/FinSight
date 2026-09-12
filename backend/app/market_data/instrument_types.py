"""Confirmed instrument type rules shared by identity and domain clients.

Codes are only shape checks.  The provider's search/list response remains the
source of truth for ``asset_type``; a suffix never promotes an unconfirmed
instrument into a different research domain.
"""

from __future__ import annotations

import re

EQUITY_THSCODE_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")
INDEX_THSCODE_PATTERN = re.compile(r"^[0-9]{6}\.(SH|SZ|TI)$")
FUND_THSCODE_PATTERN = re.compile(r"^[0-9]{6}\.(OF|SH|SZ)$")

STOCK_ASSET_TYPE = "a-share"
INDEX_ASSET_TYPE = "a-share-index"
FUND_ASSET_TYPES = frozenset({"fund-otc", "fund-etf", "fund-lof"})
PROVIDER_FUND_TYPES = frozenset({"otc", "exchange", "reits"})


def clean_thscode(value: str) -> str:
    code = (value or "").strip()
    if not code:
        raise ValueError("同花顺代码不能为空。")
    return code


def validate_instrument_thscode(asset_type: str, thscode: str) -> str:
    """Validate a confirmed identity without inferring its asset type."""
    code = clean_thscode(thscode)
    if asset_type == STOCK_ASSET_TYPE:
        pattern, label = EQUITY_THSCODE_PATTERN, "股票"
    elif asset_type == INDEX_ASSET_TYPE:
        pattern, label = INDEX_THSCODE_PATTERN, "指数"
    elif asset_type in FUND_ASSET_TYPES:
        pattern, label = FUND_THSCODE_PATTERN, "基金"
    else:
        # Unknown provider types can still be searched, but cannot become a
        # persisted identity until a dedicated domain has a contract for them.
        raise ValueError("该资产类型尚未支持确认身份。")
    if not pattern.fullmatch(code):
        raise ValueError(f"{label}代码与已确认的资产类型不匹配。")
    return code


def validate_provider_fund_type(value: str) -> str:
    fund_type = (value or "").strip().lower()
    if fund_type not in PROVIDER_FUND_TYPES:
        raise ValueError("供应商基金类型仅支持 otc / exchange / reits。")
    return fund_type
