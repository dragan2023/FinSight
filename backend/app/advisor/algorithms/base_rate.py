"""同标的历史价格状态基率（v1 降级实现）。

口径说明（P0-1）：基率必须建立在**含公司行动**的收益序列上。未复权收盘价在
除权除息日会机械性下跌，高送转尤其严重，会把真实正收益记成负收益并污染
half-Kelly 仓位区间。本模块提供 ``total_return_closes`` 把仓库的逐日公司行动
口径折算成总收益指数，并强制 as-of 截断——仓库 ``adjustment_factors`` 里含
**已公告但尚未发生**的未来除权日，任何越过 as-of 的 join 都会泄漏未来信息。
"""
from __future__ import annotations

from statistics import median, stdev
from typing import Iterable, Mapping

MIN_SAMPLE = 30

#: 直接使用未复权收盘价（降级口径，除权日会被计为真实负收益）。
BASIS_UN_ADJUSTED = "unadjusted_daily_k"
#: 使用公司行动总收益指数（本机 DuckDB 仓库首选口径）。
BASIS_CORPORATE_ACTION_TOTAL_RETURN = "corporate_action_total_return_v1"
#: 数据源本身已前复权（同花顺回退路径）。
BASIS_FORWARD_ADJUSTED_SOURCE = "forward_adjusted_source"

_BASIS_NOTES = {
    BASIS_CORPORATE_ACTION_TOTAL_RETURN:
        "收益口径为公司行动总收益指数（已按 as-of 截断复权事件），除权除息日不产生机械性负收益。",
    BASIS_FORWARD_ADJUSTED_SOURCE:
        "收益口径为数据源前复权收盘价。",
    BASIS_UN_ADJUSTED:
        "收益口径为未复权收盘价，除权除息日会被计为真实负收益，仅作降级参考。",
}

_SURVIVOR_NOTE = "本基率基于该标的历史价格状态，跨截面估值×ROE 分层待基本面历史库就绪后升级；数据不含退市股，存在幸存者偏差风险。"


def _clean_bars(rows: Iterable[Mapping[str, object]]) -> list[tuple[int, float]]:
    bars: list[tuple[int, float]] = []
    for row in rows:
        try:
            date_ms, close = int(row["date_ms"]), float(row["close_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if date_ms > 0 and close > 0:
            bars.append((date_ms, close))
    return sorted(dict(bars).items())


def total_return_closes(items: Iterable[Mapping[str, object]], *,
                        as_of_ms: int | None = None) -> dict:
    """把逐日公司行动口径折算成总收益指数序列，并强制 as-of 截断。

    ``items`` 是 :meth:`WarehouseRepository.corporate_action_total_return_history`
    的返回项，每项含 ``date_ms`` / ``close_price`` / ``total_return_factor``
    （后者已含当日现金分红、送股与配股成本）。指数以首根可用收盘价为基准
    逐日累乘该因子，因此除权除息日不再产生机械性负收益。

    ``as_of_ms`` 之后的事件与行情**一律丢弃**：任何越过 as-of 的复权事件都是
    未来信息泄漏。返回 ``raw_closes`` 供价格水平类计算（当前价、情景 EV）使用，
    ``closes`` 供收益类计算（状态基率、波动率、市场状态）使用。

    每项还带 ``open_price``（总收益口径）：开盘价按当日 ``level/close`` 同一比例
    折算，因此除权日的开盘价与收盘价口径一致，可直接用于「次日开盘成交」的回测。
    """
    raw_closes: list[dict] = []
    return_closes: list[dict] = []
    level: float | None = None
    applied_event_count = dropped_after_as_of_count = skipped_bar_count = 0
    for item in sorted(items, key=lambda row: int(row.get("date_ms") or 0)):
        try:
            date_ms = int(item["date_ms"])
            close = float(item["close_price"])
        except (KeyError, TypeError, ValueError):
            skipped_bar_count += 1
            continue
        if as_of_ms is not None and date_ms > as_of_ms:
            dropped_after_as_of_count += 1
            continue
        if date_ms <= 0 or close <= 0:
            skipped_bar_count += 1
            continue
        try:
            opening = float(item["open_price"])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            opening = None
        if opening is not None and opening <= 0:
            opening = None
        if level is None:
            level = close
        else:
            factor = item.get("total_return_factor")
            try:
                factor = float(factor)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                factor = None
            if factor is None or factor <= 0:
                skipped_bar_count += 1
                continue
            level *= factor
            applied_event_count += int(item.get("event_count") or 0)
        scale = level / close
        raw_closes.append({"date_ms": date_ms, "close_price": close, "open_price": opening})
        return_closes.append({"date_ms": date_ms, "close_price": level,
                              "open_price": None if opening is None else opening * scale})
    return {
        "basis": BASIS_CORPORATE_ACTION_TOTAL_RETURN,
        "raw_closes": raw_closes,
        "closes": return_closes,
        "applied_event_count": applied_event_count,
        "dropped_after_as_of_count": dropped_after_as_of_count,
        "skipped_bar_count": skipped_bar_count,
    }


def _volatility(closes: list[float], index: int) -> float | None:
    if index < 20:
        return None
    returns = [closes[pos] / closes[pos - 1] - 1 for pos in range(index - 19, index + 1)]
    return stdev(returns) if len(returns) > 1 else None


def _distribution(returns: list[float]) -> dict:
    count = len(returns)
    if count < MIN_SAMPLE:
        return {"available": False, "sample_count": count, "median_return_pct": None,
                "win_rate": None, "average_win_pct": None, "average_loss_pct": None}
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    return {
        "available": True, "sample_count": count,
        "median_return_pct": round(median(returns) * 100, 3),
        "win_rate": round(len(wins) / count, 4),
        "average_win_pct": round(median(wins) * 100, 3) if wins else None,
        "average_loss_pct": round(median(losses) * 100, 3) if losses else None,
    }


def calculate_base_rate(rows: Iterable[Mapping[str, object]], *,
                        return_rows: Iterable[Mapping[str, object]] | None = None,
                        price_basis: str = BASIS_UN_ADJUSTED,
                        as_of_ms: int | None = None) -> dict:
    """按 20 日动量符号 × 20 日波动档匹配该标的的后续 20/60 日表现。

    ``rows`` 为价格水平序列（未复权或数据源前复权收盘价），``return_rows``
    为对应的公司行动总收益指数序列。状态判定与后续收益一律使用后者；两者
    之间误传不会发生——调用方必须显式给出 ``price_basis``，它会写入返回值
    与说明文字，便于事后判断该基率是否受除权污染。
    """
    bars = _clean_bars(rows)
    series = _clean_bars(return_rows) if return_rows is not None else []
    basis = price_basis
    if as_of_ms is not None:
        bars = [(date_ms, close) for date_ms, close in bars if date_ms <= as_of_ms]
        series = [(date_ms, close) for date_ms, close in series if date_ms <= as_of_ms]
    if len(series) < 81:
        # 总收益序列长度不足时回落到价格水平序列，并如实改标口径，
        # 绝不假装仍在用复权口径。
        series = bars
        if return_rows is not None and price_basis == BASIS_CORPORATE_ACTION_TOTAL_RETURN:
            basis = BASIS_UN_ADJUSTED
    if len(series) < 81:
        return _unavailable(0, "历史日 K 不足以同时计算状态与后续 60 日表现。", basis)
    closes = [close for _, close in series]
    candidates = [(index, _volatility(closes, index)) for index in range(20, len(closes) - 60)]
    candidate_vols = [value for _, value in candidates if value is not None]
    latest_vol = _volatility(closes, len(closes) - 1)
    if not candidate_vols or latest_vol is None:
        return _unavailable(0, "无法计算 20 日波动率。", basis)
    threshold = median(candidate_vols)
    latest_momentum = closes[-1] / closes[-21] - 1
    latest_state = _state(latest_momentum, latest_vol, threshold)
    returns_20: list[float] = []
    returns_60: list[float] = []
    for index, volatility in candidates:
        momentum = closes[index] / closes[index - 20] - 1
        if _state(momentum, volatility, threshold) != latest_state:
            continue
        returns_20.append(closes[index + 20] / closes[index] - 1)
        returns_60.append(closes[index + 60] / closes[index] - 1)
    forward_20 = _distribution(returns_20)
    forward_60 = _distribution(returns_60)
    return {
        "available": forward_20["available"], "state": latest_state,
        "state_label": _state_label(latest_state),
        "price_basis": basis,
        "current_momentum_pct": round(latest_momentum * 100, 3),
        "current_volatility_pct": round(latest_vol * 100, 3),
        "forward_20": forward_20, "forward_60": forward_60,
        "note": _note(basis),
    }


def _note(basis: str) -> str:
    return f"{_BASIS_NOTES.get(basis, _BASIS_NOTES[BASIS_UN_ADJUSTED])}{_SURVIVOR_NOTE}"


def _unavailable(sample_count: int, reason: str, basis: str = BASIS_UN_ADJUSTED) -> dict:
    return {"available": False, "state": None, "state_label": None, "price_basis": basis,
            "forward_20": _distribution([]), "forward_60": _distribution([]),
            "note": f"{reason} {_note(basis)}"}


def _state(momentum: float, volatility: float | None, threshold: float) -> str:
    momentum_key = "up" if momentum > 0 else ("down" if momentum < 0 else "flat")
    volatility_key = "low_vol" if volatility is not None and volatility <= threshold else "high_vol"
    return f"{momentum_key}_{volatility_key}"


def _state_label(state: str) -> str:
    labels = {"up": "20 日动量为正", "down": "20 日动量为负", "flat": "20 日动量持平",
              "low_vol": "低波动", "high_vol": "高波动"}
    momentum, volatility = state.split("_", 1)
    return f"{labels[momentum]} × {labels[volatility]}"
