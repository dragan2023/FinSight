"""Price metrics on normalized daily bars: range change and max drawdown.

Inputs are normalized records (dicts) from a price-history DataResult.
Close prices are Decimal strings; rows without a usable close are skipped
(their gaps already exist in the evidence). No interpolation, no filling.
"""

from __future__ import annotations

from decimal import Decimal

from .models import MetricValue

ZERO = Decimal(0)


def close_series(records: list[dict]) -> list[tuple[str, Decimal]]:
    """(date, close) pairs in ascending date order; unusable closes skipped."""
    points: list[tuple[str, Decimal]] = []
    for record in records or []:
        close = (record.get("fields") or {}).get("close_price")
        if not close or close.get("value") in (None, ""):
            continue
        try:
            value = Decimal(close["value"])
        except Exception:
            continue
        points.append((record.get("period_key", ""), value))
    points.sort(key=lambda item: item[0])
    return points


def range_change(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    """(last_close - first_close) / first_close over the requested window."""
    series = close_series(records)
    if len(series) < 2:
        return MetricValue(
            metric="range_change", value=None, unit="ratio", basis="daily_bar",
            formula="(end_close - start_close) / start_close",
            evidence_ids=evidence_ids or [],
            note="区间内可用收盘价不足两个，无法计算区间涨跌。")
    (start_date, start), (end_date, end) = series[0], series[-1]
    if start == ZERO:
        return MetricValue(
            metric="range_change", value=None, unit="ratio", basis="daily_bar",
            formula="(end_close - start_close) / start_close",
            evidence_ids=evidence_ids or [],
            note="起点收盘价为 0，百分比无意义，拒绝计算。")
    change = (end - start) / start
    return MetricValue(
        metric="range_change", value=format(change, "f"), unit="ratio", basis="daily_bar",
        formula=f"({end} - {start}) / {start}  # {start_date} -> {end_date}",
        evidence_ids=evidence_ids or [])


def max_drawdown(records: list[dict], *, evidence_ids: list[str] | None = None) -> MetricValue:
    """Largest peak-to-trough decline of close prices within the window."""
    series = close_series(records)
    peak: Decimal | None = None
    worst = ZERO
    for _, value in series:
        if peak is None or value > peak:
            peak = value
        if peak > ZERO:
            drawdown = (value - peak) / peak
            if drawdown < worst:
                worst = drawdown
    if peak is None or len(series) < 2:
        return MetricValue(
            metric="max_drawdown", value=None, unit="ratio", basis="daily_bar",
            formula="min((close_t - running_peak) / running_peak)",
            evidence_ids=evidence_ids or [],
            note="区间内可用收盘价不足两个，无法计算最大回撤。")
    return MetricValue(
        metric="max_drawdown", value=format(worst, "f"), unit="ratio", basis="daily_bar",
        formula="min((close_t - running_peak) / running_peak)",
        evidence_ids=evidence_ids or [])
