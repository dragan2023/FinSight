"""组合级、无前视的策略回放引擎。

信号来自既有 ``run_backtest`` 的日末仓位：该仓位已按“前一已完成 K 线决策、
本交易日开盘执行”计算。组合在同一开盘时点按信号再平衡，分别计入买卖换手成本。
它是研究工具，不把实验策略自动变成生产买入建议。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..strategy.backtest import run_backtest
from ..strategy.models import PriceBar


@dataclass(frozen=True)
class PortfolioTarget:
    thscode: str
    name: str
    industry: str
    bars: list[PriceBar]


def allocate_candidates(
    candidates: list[dict],
    *,
    max_positions: int,
    max_single_weight: float,
    max_industry_weight: float,
) -> dict[str, float]:
    """按分数、代码稳定排序，先应用个股与行业上限，再保留现金。

    这里刻意不做均值方差优化：当前没有已验证预期收益/协方差输入，伪精确优化
    反而会掩盖证据不足。每个入选标的最多占 ``max_single_weight``，未用资金留在现金。
    """
    if max_positions < 1 or not 0 < max_single_weight <= 1 or not 0 < max_industry_weight <= 1:
        raise ValueError("组合上限参数无效。")
    weights: dict[str, float] = {}
    industry_weights: dict[str, float] = {}
    ranked = sorted(candidates, key=lambda item: (-float(item["score"]), str(item["thscode"])))
    for item in ranked:
        if len(weights) >= max_positions:
            break
        industry = str(item.get("industry") or "未分类")
        remaining = max_industry_weight - industry_weights.get(industry, 0.0)
        weight = min(max_single_weight, remaining)
        if weight <= 1e-12:
            continue
        code = str(item["thscode"])
        weights[code] = round(weight, 10)
        industry_weights[industry] = industry_weights.get(industry, 0.0) + weight
    return weights


def _common_dates(targets: list[PortfolioTarget]) -> list[str]:
    if not targets:
        return []
    dates = [{bar.date for bar in target.bars if bar.open is not None and bar.close > 0}
             for target in targets]
    return sorted(set.intersection(*dates)) if dates else []


def _bars_by_date(target: PortfolioTarget) -> dict[str, PriceBar]:
    return {bar.date: bar for bar in target.bars if bar.open is not None and bar.close > 0}


def _active_strategy_map(
    targets: list[PortfolioTarget], strategy_ids: list[str], *, one_way_cost_bps: int,
    gate: dict | None,
) -> dict[str, dict[str, set[str]]]:
    """返回 ``交易日 -> 标的 -> 当前已执行策略``，不读取未来数据。"""
    daily: dict[str, dict[str, set[str]]] = {}
    for target in targets:
        for strategy_id in strategy_ids:
            curve = run_backtest(strategy_id, target.bars, one_way_cost_bps, gate=gate)["curve"]
            for point in curve:
                if point.get("position"):
                    daily.setdefault(str(point["period"]), {}).setdefault(target.thscode, set()).add(strategy_id)
    return daily


def _metrics(curve: list[dict], *, trade_count: int) -> dict:
    if not curve:
        return {"cumulative_return_pct": None, "annualized_return_pct": None,
                "max_drawdown_pct": None, "trade_count": trade_count}
    values = [float(item["net_value"]) for item in curve]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values))]
    peak = 1.0
    drawdowns: list[float] = []
    for value in values:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1)
    annualized = (values[-1] ** (252 / max(1, len(returns))) - 1) if values[-1] > 0 else -1.0
    volatility = (math.sqrt(sum((item - (sum(returns) / len(returns))) ** 2 for item in returns) / len(returns))
                  * math.sqrt(252)) if returns else 0.0
    total_turnover = sum(float(item.get("turnover_value") or 0.0) for item in curve)
    total_cost = sum(float(item.get("transaction_cost") or 0.0) for item in curve)
    return {
        "cumulative_return_pct": round((values[-1] - 1) * 100, 4),
        "annualized_return_pct": round(annualized * 100, 4),
        "annualized_volatility_pct": round(volatility * 100, 4),
        "max_drawdown_pct": round(min(drawdowns) * 100, 4),
        "trade_count": trade_count,
        "annualized_turnover_pct": round(total_turnover / max(1, len(curve)) * 252 * 100, 4),
        "total_transaction_cost_pct": round(total_cost * 100, 4),
        "final_net_value": round(values[-1], 8),
    }


def _equal_weight_buy_hold(targets: list[PortfolioTarget], dates: list[str], *, one_way_cost_bps: int) -> list[dict]:
    """同一股票池的全仓等权买入持有基线，首日开盘一次性成交并扣成本。"""
    if not dates:
        return []
    price_maps = {target.thscode: _bars_by_date(target) for target in targets}
    cost = one_way_cost_bps / 10_000
    holdings = {target.thscode: (1 - cost) / len(targets) for target in targets}
    curve: list[dict] = []
    for index, day in enumerate(dates):
        if index == 0:
            for target in targets:
                bar = price_maps[target.thscode][day]
                holdings[target.thscode] *= float(bar.close) / float(bar.open)
        else:
            previous = dates[index - 1]
            for target in targets:
                bar = price_maps[target.thscode][day]
                prior = price_maps[target.thscode][previous]
                holdings[target.thscode] *= float(bar.close) / float(prior.close)
        curve.append({"period": day, "net_value": round(sum(holdings.values()), 10)})
    return curve


def run_portfolio_backtest(
    targets: list[PortfolioTarget],
    *,
    strategy_ids: list[str],
    one_way_cost_bps: int,
    max_positions: int,
    max_single_weight: float,
    max_industry_weight: float,
    analysis_start_date: str | None = None,
    gate: dict | None = None,
) -> dict:
    """按日滚动配置研究组合；无信号或触及上限后不强行买入。

    返回的 ``daily_candidates`` 是审计资料：可复核为什么某天买入、为什么留现金。
    """
    if not targets:
        raise ValueError("组合至少需要一个标的。")
    if not strategy_ids:
        raise ValueError("组合至少需要一个策略。")
    dates = _common_dates(targets)
    if analysis_start_date is not None:
        # 预热 K 线只供策略计算，不计入组合表现，避免把策略 warmup 期错当实验期。
        dates = [item for item in dates if item >= analysis_start_date]
    if len(dates) < 2:
        raise ValueError("标的之间缺少足够的共同可交易日。")
    target_by_code = {item.thscode: item for item in targets}
    price_maps = {item.thscode: _bars_by_date(item) for item in targets}
    active = _active_strategy_map(targets, strategy_ids, one_way_cost_bps=one_way_cost_bps, gate=gate)
    cost_rate = one_way_cost_bps / 10_000
    cash = 1.0
    holdings: dict[str, float] = {}
    curve: list[dict] = []
    trades: list[dict] = []
    daily_candidates: list[dict] = []

    for index, day in enumerate(dates):
        if index:
            previous_day = dates[index - 1]
            # 先把昨收持仓过夜标记到今开，再按昨收已知的信号在今开调仓。
            for code, value in list(holdings.items()):
                opening = float(price_maps[code][day].open)
                previous_close = float(price_maps[code][previous_day].close)
                holdings[code] = value * opening / previous_close

        pre_trade_equity = cash + sum(holdings.values())
        candidates = []
        for code, active_ids in active.get(day, {}).items():
            target = target_by_code[code]
            candidates.append({"thscode": code, "name": target.name, "industry": target.industry,
                               "strategy_ids": sorted(active_ids), "score": len(active_ids)})
        target_weights = allocate_candidates(
            candidates, max_positions=max_positions, max_single_weight=max_single_weight,
            max_industry_weight=max_industry_weight)
        target_values = {code: pre_trade_equity * weight for code, weight in target_weights.items()}
        turnover = sum(abs(target_values.get(code, 0.0) - holdings.get(code, 0.0))
                       for code in set(target_values) | set(holdings))
        transaction_cost = turnover * cost_rate
        # 组合配置上限严格小于 100%，因此成本不会通过借款被掩盖。
        cash = pre_trade_equity - sum(target_values.values()) - transaction_cost
        if cash < -1e-9:
            raise RuntimeError("组合配置超出可用资金；请降低仓位上限。")
        for code in sorted(set(target_values) | set(holdings)):
            before = holdings.get(code, 0.0)
            after = target_values.get(code, 0.0)
            if abs(after - before) > 1e-12:
                trades.append({"date": day, "thscode": code, "before_weight": round(before / pre_trade_equity, 8)
                               if pre_trade_equity else 0.0, "target_weight": round(after / pre_trade_equity, 8)
                               if pre_trade_equity else 0.0, "turnover_value": round(abs(after - before), 10),
                               "transaction_cost": round(abs(after - before) * cost_rate, 10)})
        holdings = target_values
        for code, value in list(holdings.items()):
            bar = price_maps[code][day]
            holdings[code] = value * float(bar.close) / float(bar.open)
        equity = cash + sum(holdings.values())
        invested_weight = sum(holdings.values()) / equity if equity else 0.0
        daily_candidates.append({"date": day, "candidates": candidates, "selected_weights": target_weights,
                                 "cash_weight_at_open": round(cash / (pre_trade_equity - transaction_cost), 8)
                                 if pre_trade_equity > transaction_cost else 0.0})
        curve.append({"period": day, "net_value": round(equity, 10),
                      "invested_weight": round(invested_weight, 8), "candidate_count": len(candidates),
                      "selected_count": len(target_weights), "turnover_value": round(turnover, 10),
                      "transaction_cost": round(transaction_cost, 10)})

    equal_curve = _equal_weight_buy_hold(targets, dates, one_way_cost_bps=one_way_cost_bps)
    portfolio_metrics = _metrics(curve, trade_count=len(trades))
    equal_metrics = _metrics(equal_curve, trade_count=len(targets))
    return {
        "date_range": {"start": dates[0], "end": dates[-1], "common_trading_days": len(dates)},
        "portfolio": {"curve": curve, "metrics": portfolio_metrics, "trades": trades,
                      "daily_candidates": daily_candidates},
        "baselines": {"cash": {"cumulative_return_pct": 0.0, "max_drawdown_pct": 0.0},
                      "equal_weight_buy_hold": {"curve": equal_curve, "metrics": equal_metrics}},
        "comparison": {"excess_vs_equal_weight_pct": round(
            portfolio_metrics["cumulative_return_pct"] - equal_metrics["cumulative_return_pct"], 4),
                       "excess_vs_cash_pct": portfolio_metrics["cumulative_return_pct"]},
        "research_only": True,
        "production_eligibility": "none：本回放不改变策略候选门禁或顾问生产建议。",
    }
