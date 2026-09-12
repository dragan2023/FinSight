"""同标的历史价格状态基率（v1 降级实现）。"""
from __future__ import annotations

from statistics import median, stdev
from typing import Iterable, Mapping

MIN_SAMPLE = 30


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


def calculate_base_rate(rows: Iterable[Mapping[str, object]]) -> dict:
    """按 20 日动量符号 × 20 日波动档匹配该标的的后续 20/60 日表现。"""
    bars = _clean_bars(rows)
    if len(bars) < 81:
        return _unavailable(0, "历史日 K 不足以同时计算状态与后续 60 日表现。")
    closes = [close for _, close in bars]
    candidates = [(index, _volatility(closes, index)) for index in range(20, len(closes) - 60)]
    candidate_vols = [value for _, value in candidates if value is not None]
    latest_vol = _volatility(closes, len(closes) - 1)
    if not candidate_vols or latest_vol is None:
        return _unavailable(0, "无法计算 20 日波动率。")
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
        "current_momentum_pct": round(latest_momentum * 100, 3),
        "current_volatility_pct": round(latest_vol * 100, 3),
        "forward_20": forward_20, "forward_60": forward_60,
        "note": "本基率基于该标的历史价格状态，跨截面估值×ROE 分层待基本面历史库就绪后升级；数据不含退市股，存在幸存者偏差风险。",
    }


def _unavailable(sample_count: int, reason: str) -> dict:
    return {"available": False, "state": None, "state_label": None,
            "forward_20": _distribution([]), "forward_60": _distribution([]),
            "note": f"{reason} 本基率基于该标的历史价格状态，跨截面估值×ROE 分层待基本面历史库就绪后升级；数据不含退市股，存在幸存者偏差风险。"}


def _state(momentum: float, volatility: float | None, threshold: float) -> str:
    momentum_key = "up" if momentum > 0 else ("down" if momentum < 0 else "flat")
    volatility_key = "low_vol" if volatility is not None and volatility <= threshold else "high_vol"
    return f"{momentum_key}_{volatility_key}"


def _state_label(state: str) -> str:
    labels = {"up": "20 日动量为正", "down": "20 日动量为负", "flat": "20 日动量持平",
              "low_vol": "低波动", "high_vol": "高波动"}
    momentum, volatility = state.split("_", 1)
    return f"{labels[momentum]} × {labels[volatility]}"
