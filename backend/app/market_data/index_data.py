"""Index-data contracts shared by explicit sector research workflows.

This module intentionally contains no credentials or network side effects.  It
guards the supplier-specific request shapes before a caller elects to refresh
data, and normalises only the minimal fields used by the local comparison.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable


def index_history_params(thscode: str, *, start_ms: int, end_ms: int) -> dict[str, object]:
    """Build an index-price request without an unsupported equity ``adjust`` flag."""
    if not thscode.strip():
        raise ValueError("指数代码不能为空。")
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or start_ms <= 0 or end_ms < start_ms:
        raise ValueError("指数历史区间必须是有效的毫秒时间戳。")
    return {"thscode": thscode.strip(), "interval": "1d", "start": start_ms, "end": end_ms}


def index_prices(rows: Iterable[dict], *, date_key: str = "date", price_key: str = "close") -> dict[str, str | None]:
    """Return an explicit-date price series without filling absent trade days."""
    result: dict[str, str | None] = {}
    for row in rows:
        date = str(row.get(date_key) or "").strip()
        if not date:
            continue
        raw = row.get(price_key)
        if raw is None or raw == "":
            result[date] = None
            continue
        try:
            value = Decimal(str(raw))
        except InvalidOperation as error:
            raise ValueError(f"指数 {date} 的价格无法解释。") from error
        if value <= 0:
            raise ValueError(f"指数 {date} 的价格必须为正数。")
        result[date] = format(value, "f")
    return result
