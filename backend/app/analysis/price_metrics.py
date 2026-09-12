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


def _window_series(records: list[dict], window: int) -> list[tuple[str, Decimal]]:
    if window < 2:
        raise ValueError("价格指标窗口至少为两个交易日。")
    series = close_series(records)
    return series[-window:] if len(series) >= window else []


def moving_average(records: list[dict], window: int, *, evidence_ids: list[str] | None = None) -> MetricValue:
    series = _window_series(records, window)
    if not series:
        return MetricValue(metric=f"ma_{window}", value=None, unit="CNY", basis="daily_bar",
                           formula=f"mean(close over last {window} available trading days)",
                           evidence_ids=evidence_ids or [],
                           note=f"可用收盘价不足 {window} 个交易日，未插值计算均线。")
    value = sum((item[1] for item in series), ZERO) / Decimal(window)
    return MetricValue(metric=f"ma_{window}", value=format(value, "f"), unit="CNY", basis="daily_bar",
                       formula=f"mean(close, {series[0][0]} -> {series[-1][0]}, {window} points)",
                       evidence_ids=evidence_ids or [])


def average_turnover(records: list[dict], window: int = 20, *, evidence_ids: list[str] | None = None) -> MetricValue:
    points: list[tuple[str, Decimal]] = []
    for record in records or []:
        field = (record.get("fields") or {}).get("turnover") or {}
        try:
            value = Decimal(field.get("value"))
        except Exception:
            continue
        points.append((record.get("period_key", ""), value))
    points.sort(key=lambda item: item[0])
    if len(points) < window:
        return MetricValue(metric=f"avg_turnover_{window}", value=None, unit="CNY", basis="daily_bar",
                           formula=f"mean(turnover over last {window} available trading days)",
                           evidence_ids=evidence_ids or [],
                           note=f"可用成交额不足 {window} 个交易日，未插值计算平均成交额。")
    selected = points[-window:]
    value = sum((item[1] for item in selected), ZERO) / Decimal(window)
    return MetricValue(metric=f"avg_turnover_{window}", value=format(value, "f"), unit="CNY", basis="daily_bar",
                       formula=f"mean(turnover, {selected[0][0]} -> {selected[-1][0]}, {window} points)",
                       evidence_ids=evidence_ids or [])


def window_change(records: list[dict], window: int, *, evidence_ids: list[str] | None = None) -> MetricValue:
    series = _window_series(records, window)
    if not series:
        return MetricValue(metric=f"change_{window}", value=None, unit="ratio", basis="daily_bar",
                           formula=f"(last_close - first_close) / first_close over {window} points",
                           evidence_ids=evidence_ids or [],
                           note=f"可用收盘价不足 {window} 个交易日，无法计算区间涨跌。")
    start, end = series[0], series[-1]
    if start[1] == ZERO:
        return MetricValue(metric=f"change_{window}", value=None, unit="ratio", basis="daily_bar",
                           formula="(last_close - first_close) / first_close", evidence_ids=evidence_ids or [],
                           note="区间起点收盘价为 0，比率无意义，拒绝计算。")
    return MetricValue(metric=f"change_{window}", value=format((end[1] - start[1]) / start[1], "f"),
                       unit="ratio", basis="daily_bar",
                       formula=f"({end[1]} - {start[1]}) / {start[1]}  # {start[0]} -> {end[0]}",
                       evidence_ids=evidence_ids or [])


def window_max_drawdown(records: list[dict], window: int = 60, *, evidence_ids: list[str] | None = None) -> MetricValue:
    series = _window_series(records, window)
    if not series:
        return MetricValue(metric=f"max_drawdown_{window}", value=None, unit="ratio", basis="daily_bar",
                           formula="min((close_t - running_peak) / running_peak)", evidence_ids=evidence_ids or [],
                           note=f"可用收盘价不足 {window} 个交易日，无法计算窗口最大回撤。")
    peak, worst = series[0][1], ZERO
    for _, value in series:
        peak = max(peak, value)
        if peak > ZERO:
            worst = min(worst, (value - peak) / peak)
    return MetricValue(metric=f"max_drawdown_{window}", value=format(worst, "f"), unit="ratio", basis="daily_bar",
                       formula=f"min((close_t - running_peak) / running_peak), last {window} points",
                       evidence_ids=evidence_ids or [])


def distance_from_high(records: list[dict], window: int = 120, *, evidence_ids: list[str] | None = None) -> MetricValue:
    series = _window_series(records, window)
    if not series:
        return MetricValue(metric=f"distance_from_high_{window}", value=None, unit="ratio", basis="daily_bar",
                           formula="(last_close - max(close)) / max(close)", evidence_ids=evidence_ids or [],
                           note=f"可用收盘价不足 {window} 个交易日，无法计算距区间高点。")
    high = max(value for _, value in series)
    return MetricValue(metric=f"distance_from_high_{window}", value=format((series[-1][1] - high) / high, "f"),
                       unit="ratio", basis="daily_bar",
                       formula=f"({series[-1][1]} - {high}) / {high}  # {series[0][0]} -> {series[-1][0]}",
                       evidence_ids=evidence_ids or [])
