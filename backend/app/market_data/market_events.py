"""Canonical keys for supplier market-event material before local review."""
from __future__ import annotations


def dragon_tiger_key(row: dict) -> tuple[str, str, str, str]:
    return (
        str(row.get("thscode") or ""),
        str(row.get("trade_date") or ""),
        str(row.get("window") or ""),
        str(row.get("reason") or ""),
    )


def event_date_supported(*, requested_date: str, returned_date: str | None) -> bool:
    """An explanation is usable only for its returned calendar date."""
    return bool(returned_date and requested_date == returned_date)
