"""Lossless, fund-specific normalization for disclosed and market data."""

from __future__ import annotations

import json
from decimal import Decimal

from .normalization import (
    BASIS_UNKNOWN, GapCandidate, NormalizationResult, NormalizedField,
    NormalizedRecord, date_from_ms, iso_from_ms, to_decimal,
)

PERCENT_FIELDS = {
    "hold_ratio", "security_market_value_rate_pct", "period_increase_rate_pct",
    "total_stock_ratio_pct", "total_bond_ratio_pct", "total_fund_ratio_pct",
    "turnover_rate_pct", "stock_ratio_pct", "ins_position", "psnl_rate",
    "mgmt_staff_hold_rate", "nav_rate", "period_increase_pct", "stock_ratio_pct",
    "bond_ratio_pct", "deposit_ratio_pct", "other_ratio_pct", "ratio_pct",
}
AMOUNT_FIELDS = {
    "position_capital", "fund_scale", "distribution_profit", "current_profit",
    "share_nav", "total_fund_scale", "market_value",
}
DATE_FIELDS = {"nav_date", "date_ms", "end_date_ms", "start_date_ms", "report_date_ms"}
DISCLOSURE_FIELDS = {"publish_date_ms", "modify_time_ms"}
NEWS_METADATA_FIELDS = {
    "id", "news_id", "title", "summary", "source", "author", "content_type",
    "url", "publish_time_ms", "publish_time", "datetime", "time",
}


def _value(raw: object, name: str) -> NormalizedField:
    if raw is None:
        return NormalizedField(source_field=name, label=name, kind="unknown", unit="", raw_value=None, value=None)
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return NormalizedField(source_field=name, label=name, kind="json", unit="", raw_value=text, value=text)
    number = to_decimal(raw)
    if number is None:
        return NormalizedField(source_field=name, label=name, kind="text", unit="", raw_value=str(raw), value=str(raw))
    if name in PERCENT_FIELDS:
        number = number / Decimal(100)
        return NormalizedField(source_field=name, label=name, kind="percent", unit="ratio", raw_value=str(raw), value=format(number, "f"))
    if name in AMOUNT_FIELDS:
        return NormalizedField(source_field=name, label=name, kind="amount", unit="CNY", raw_value=str(raw), value=format(number, "f"), currency="CNY")
    if name in DATE_FIELDS | DISCLOSURE_FIELDS:
        value = iso_from_ms(raw) if isinstance(raw, int) else None
        return NormalizedField(source_field=name, label=name, kind="date", unit="", raw_value=str(raw), value=value,
                               gap_reason=None if value else "invalid_response")
    return NormalizedField(source_field=name, label=name, kind="number", unit="", raw_value=str(raw), value=format(number, "f"))


def normalize_fund_rows(payload: dict, thscode: str, basis: str) -> NormalizationResult:
    """Preserve disclosure and vendor structures without treating missing data as zero."""
    rows = payload.get("item") or []
    records: list[NormalizedRecord] = []
    gaps: list[GapCandidate] = []
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        period_end = date_from_ms(row.get("end_date_ms") or row.get("report_date_ms") or row.get("nav_date"))
        period_key = period_end or str(row.get("end_date") or row.get("report_type") or position)
        disclosed_at = iso_from_ms(row.get("publish_date_ms") or row.get("modify_time_ms"))
        fields = {name: _value(raw, name) for name, raw in row.items() if name != "thscode"}
        records.append(NormalizedRecord(
            thscode=str(row.get("thscode") or thscode), period_key=period_key,
            period_end=period_end, disclosed_at=disclosed_at, basis=basis,
            currency="CNY", fields=fields))
    # Holdings summaries are first-class evidence rather than hidden metadata.
    summaries = {name: value for name, value in payload.items() if name not in {"item", "timestamp"}}
    if summaries:
        records.append(NormalizedRecord(
            thscode=thscode, period_key="summary", period_end=None, disclosed_at=None,
            basis=basis, currency="CNY", fields={name: _value(value, name) for name, value in summaries.items()}))
    records.sort(key=lambda record: record.period_key)
    return NormalizationResult(domain=basis, basis=basis or BASIS_UNKNOWN, records=records, gaps=gaps, duplicate_versions=0)


def normalize_fund_news(payload: dict, thscode: str):
    """Persist list metadata only; article bodies require a separately authorized reader."""
    items = [
        {name: value for name, value in row.items() if name in NEWS_METADATA_FIELDS}
        for row in payload.get("item") or []
        if isinstance(row, dict)
    ]
    result = normalize_fund_rows({"item": items}, thscode, "fund_news")
    raw_has_more = payload.get("has_more")
    has_more = raw_has_more is True or str(raw_has_more).lower() in {"1", "true", "yes"}
    next_offset = payload.get("next_offset", payload.get("offset"))
    scope = {"has_more": has_more}
    if next_offset not in (None, ""):
        scope["offset"] = str(next_offset)
    return result.records, result.gaps, result.duplicate_versions, scope
