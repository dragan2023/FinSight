"""教育性的波动率目标与 half-Kelly 计算。"""
from __future__ import annotations

from math import sqrt
from statistics import stdev
from typing import Iterable


PREFIX = "教育性计算，非仓位建议："


def calculate_volatility_target(closes: Iterable[float], *, target_annual_volatility: float = 0.15) -> dict:
    prices = []
    for close in closes:
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices.append(value)
    if len(prices) < 21:
        return {"weight": None, "annualized_volatility": None,
                "note": PREFIX + "日 K 不足 21 个，无法计算 20 日收益波动率。"}
    returns = [prices[index] / prices[index - 1] - 1 for index in range(len(prices) - 20, len(prices))]
    volatility = stdev(returns) * sqrt(250)
    weight = target_annual_volatility / volatility if volatility > 0 else None
    return {"weight": None if weight is None else round(weight, 4),
            "annualized_volatility": round(volatility, 4),
            "note": PREFIX + "按 20 日收益标准差年化并以 15% 波动率目标估算，仅作风险预算参考。"}


def calculate_half_kelly(*, probability_source: str, sample_count: int,
                         win_rate: float | None, average_win_pct: float | None,
                         average_loss_pct: float | None) -> dict:
    """仅在达标基率下给出 half-Kelly 的保守区间，绝不输出单点仓位。"""
    note = PREFIX
    if probability_source != "base_rate" or sample_count < 100:
        return {"range": None, "note": note + "仅当历史基率可用且样本不少于 100 时才展示 half-Kelly 区间。"}
    if win_rate is None or average_win_pct is None or average_loss_pct is None or average_loss_pct >= 0:
        return {"range": None, "note": note + "胜率或盈亏幅度不足，无法计算 half-Kelly 区间。"}
    payoff = average_win_pct / abs(average_loss_pct)
    if payoff <= 0:
        return {"range": None, "note": note + "盈亏比无效，无法计算 half-Kelly 区间。"}
    raw = (win_rate - (1 - win_rate) / payoff) / 2
    center = max(0.0, raw)
    return {"range": [round(center * 0.75, 4), round(center * 1.25, 4)],
            "note": note + "基于历史样本的胜率与盈亏比给出保守区间，对参数估计非常敏感。"}
