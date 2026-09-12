"""Fund-data guards for local research; no code-suffix inference or network I/O."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .instrument_types import validate_provider_fund_type

NAV_RANGES = {"week", "month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"}
FUND_TYPES = {"otc", "etf", "lof", "reit", "bond", "money", "other"}


def require_fund_type(metadata: dict) -> str:
    """Require supplier/user metadata instead of guessing from a fund code."""
    fund_type = str(metadata.get("fund_type") or "").strip().lower()
    if fund_type not in FUND_TYPES:
        raise ValueError("基金类型必须由已确认的元数据提供，不能根据代码后缀推断。")
    return fund_type


def nav_request_params(thscode: str, *, range_name: str | None = None) -> dict[str, str]:
    if not thscode.strip():
        raise ValueError("基金标识不能为空。")
    if range_name is not None and range_name not in NAV_RANGES:
        raise ValueError("净值范围仅支持供应商定义的 week/month/tmonth/hyear/year/twoyear/tyear/fyear。")
    result = {"thscode": thscode.strip()}
    if range_name:
        result["range"] = range_name
    return result


def provider_fund_type(metadata: dict) -> str:
    """Validate the upstream enum separately from FinSight's research class."""
    return validate_provider_fund_type(str(metadata.get("provider_fund_type") or ""))


def discover_report_dates(rows: Iterable[dict]) -> list[dict[str, str]]:
    """Keep returned report type/end date together; never hard-code their meaning."""
    discovered: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        report_type = str(row.get("report_type") or "").strip()
        end_date = str(row.get("end_date") or row.get("report_end") or "").strip()
        if not report_type or not end_date:
            continue
        try:
            date.fromisoformat(end_date[:10])
        except ValueError:
            continue
        discovered[(report_type, end_date)] = {"report_type": report_type, "end_date": end_date}
    return sorted(discovered.values(), key=lambda item: item["end_date"], reverse=True)


def disclosed_weight_total(rows: Iterable[dict]) -> str | None:
    """Sum only confirmed numeric row weights; absent rows remain unknown, not zero."""
    total = Decimal(0)
    found = False
    for row in rows:
        value = row.get("weight")
        if value is None or value == "":
            continue
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError("披露持仓权重无法解释。") from error
        if parsed < 0:
            raise ValueError("披露持仓权重不能为负。")
        total += parsed
        found = True
    return format(total, "f") if found else None
