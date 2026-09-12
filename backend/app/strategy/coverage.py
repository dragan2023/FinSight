"""逐交易日的策略入场覆盖度评估。

它回答的是“规则策略抓住了多少事后有效的买入窗口”，不是价格方向预测。
所有入场信号均复用 :func:`run_backtest` 的前一根已完成 K 线决策、下一根开盘执行
口径；未来数据只用于给事后窗口贴标签，绝不参与产生信号。
"""
from __future__ import annotations

from statistics import median

from .backtest import run_backtest
from .models import PriceBar


def net_return_pct(entry_price: float, exit_price: float, one_way_cost_bps: int) -> float:
    """按进出各一次成本计算持有期净收益。"""
    cost = one_way_cost_bps / 10_000
    return round((((exit_price / entry_price) * (1 - cost) ** 2) - 1) * 100, 4)


def evaluate_entry_coverage(
    strategy_id: str,
    bars: list[PriceBar],
    *,
    horizons: list[int],
    one_way_cost_bps: int,
    minimum_warmup_bars: int,
    minimum_net_return_pct: dict[int, float],
    maximum_adverse_excursion_pct: float,
    gate: dict | None = None,
) -> list[dict]:
    """返回每个“决策日 × 持有期”的可审计评分行。

    ``signal_entered`` 为真表示策略在该决策日的下一交易日开盘新开仓；如果
    策略仍在既有仓位中，则不将其重复计作一次新的买入信号。
    """
    if not bars:
        return []
    trades = run_backtest(strategy_id, bars, one_way_cost_bps, gate=gate).get("trades", [])
    entries = {str(item.get("entry_date") or "") for item in trades}
    rows: list[dict] = []
    for decision_index in range(max(0, minimum_warmup_bars - 1), len(bars)):
        entry_index = decision_index + 1
        if entry_index >= len(bars) or bars[entry_index].open is None:
            continue
        entry_price = float(bars[entry_index].open)
        for horizon in horizons:
            exit_index = decision_index + int(horizon)
            if exit_index >= len(bars):
                continue
            path = bars[entry_index:exit_index + 1]
            adverse = min((bar.close / entry_price - 1) * 100 for bar in path)
            net_return = net_return_pct(entry_price, bars[exit_index].close, one_way_cost_bps)
            threshold = float(minimum_net_return_pct[int(horizon)])
            effective = net_return >= threshold and adverse >= maximum_adverse_excursion_pct
            entry_date = bars[entry_index].date
            rows.append({
                "strategy_id": strategy_id,
                "decision_date": bars[decision_index].date,
                "entry_date": entry_date,
                "exit_date": bars[exit_index].date,
                "horizon_days": int(horizon),
                "entry_price": entry_price,
                "exit_close": bars[exit_index].close,
                "net_return_pct": net_return,
                "max_adverse_excursion_pct": round(adverse, 4),
                "minimum_net_return_pct": threshold,
                "maximum_adverse_excursion_pct": maximum_adverse_excursion_pct,
                "signal_entered": entry_date in entries,
                "effective_opportunity": effective,
            })
    return rows


def coverage_summary(rows: list[dict]) -> dict:
    """以买入信号为预测正类、事后有效窗口为真实正类汇总。"""
    total = len(rows)
    signals = [row for row in rows if row["signal_entered"]]
    opportunities = [row for row in rows if row["effective_opportunity"]]
    true_positive = [row for row in signals if row["effective_opportunity"]]
    false_positive = [row for row in signals if not row["effective_opportunity"]]
    false_negative = [row for row in rows if not row["signal_entered"] and row["effective_opportunity"]]
    returns = [float(row["net_return_pct"]) for row in signals]
    adverse = [float(row["max_adverse_excursion_pct"]) for row in signals]
    return {
        "eligible_decision_days": total,
        "entry_signals": len(signals),
        "effective_opportunities": len(opportunities),
        "true_positive": len(true_positive),
        "false_positive": len(false_positive),
        "false_negative": len(false_negative),
        "signal_coverage": round(len(signals) / total, 6) if total else None,
        "opportunity_rate": round(len(opportunities) / total, 6) if total else None,
        "precision": round(len(true_positive) / len(signals), 6) if signals else None,
        "recall": round(len(true_positive) / len(opportunities), 6) if opportunities else None,
        "average_signal_net_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
        "median_signal_net_return_pct": round(float(median(returns)), 4) if returns else None,
        "worst_signal_adverse_excursion_pct": round(min(adverse), 4) if adverse else None,
    }
