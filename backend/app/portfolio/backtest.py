"""组合级、无前视的策略回放引擎。

信号来自既有 ``run_backtest`` 的日末仓位：该仓位已按“前一已完成 K 线决策、
本交易日开盘执行”计算。组合在同一开盘时点按信号再平衡，分别计入买卖换手成本。
它是研究工具，不把实验策略自动变成生产买入建议。
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass

from ..strategy.backtest import run_backtest
from ..strategy.models import PriceBar
from .construction import (
    ConstructionPolicy,
    build_return_series,
    industry_key as _industry_key,
    risk_contributions,
    target_weights as build_target_weights,
)


@dataclass(frozen=True)
class PortfolioTarget:
    thscode: str
    name: str
    industry: str
    bars: list[PriceBar]


#: 缺失/占位行业标签的处理已移到 `construction.industry_key`（避免两个模块互相 import）。
#: 规则：**未知行业不参与行业约束**，否则"行业上限"会退化成"总仓位上限"。
def allocate_candidates(
    candidates: list[dict],
    *,
    max_positions: int,
    max_single_weight: float,
    max_industry_weight: float,
    max_total_exposure: float = 1.0,
) -> dict[str, float]:
    """按分数、代码稳定排序，先应用个股与行业上限，再保留现金。

    这里刻意不做均值方差优化：当前没有已验证预期收益/协方差输入，伪精确优化
    反而会掩盖证据不足。每个入选标的最多占 ``max_single_weight``，未用资金留在现金。

    ``max_total_exposure`` 是**显式的总仓位上限**（默认 1.0，组合回放层会传 0.95 留出成本）。
    在它有名字之前，"投多少"是被行业上限**隐式**决定的——那种隐式约束正是上面那个 bug。
    """
    if max_positions < 1 or not 0 < max_single_weight <= 1 or not 0 < max_industry_weight <= 1:
        raise ValueError("组合上限参数无效。")
    if not 0 < max_total_exposure <= 1:
        raise ValueError("总仓位上限必须落在 (0, 1]。")
    weights: dict[str, float] = {}
    industry_weights: dict[str, float] = {}
    ranked = sorted(candidates, key=lambda item: (-float(item["score"]), str(item["thscode"])))
    for item in ranked:
        if len(weights) >= max_positions:
            break
        industry = _industry_key(item.get("industry"))
        remaining = max_total_exposure - sum(weights.values())
        if industry is not None:
            remaining = min(remaining, max_industry_weight - industry_weights.get(industry, 0.0))
        weight = min(max_single_weight, remaining)
        if weight <= 1e-12:
            continue
        code = str(item["thscode"])
        weights[code] = round(weight, 10)
        if industry is not None:
            industry_weights[industry] = industry_weights.get(industry, 0.0) + weight
    return weights


def _trading_calendar(targets: list[PortfolioTarget]) -> list[str]:
    """全样本交易日**并集**（不是交集）。

    ⚠️ 这是 2026-09-15 实测发现的第二个结构性缺陷。原先用**交集**，后果是
    **股票池越大、可用历史越短**：50 只标的里只要有少数几只 2023 年才上市（或中途长停牌），
    整段回测就被压到 2023 年以后——实测 top-50 只剩 **591 个交易日**，
    于是"年化"被算成 75%、"单年最差"变成 +29.7%（全为正），而对照的等权买入持有是
    **年化 113%**。那不是分散，那是换了个样本。

    并集 + 停牌处理（`_price_maps` / 循环里的 `bar is None` 分支）才是对的：
    某只标的当天没有 K 线 → 视同停牌，价格不变、当天不可交易，其余标的照常。
    """
    dates: set[str] = set()
    for target in targets:
        dates.update(bar.date for bar in target.bars if bar.open is not None and bar.close > 0)
    return sorted(dates)


def _bars_by_date(target: PortfolioTarget) -> dict[str, PriceBar]:
    return {bar.date: bar for bar in target.bars if bar.open is not None and bar.close > 0}


def _previous_index(ordered: list[str], day: str) -> int:
    """`ordered` 中**严格早于** `day` 的最后一根的下标；没有则 -1。"""
    return bisect_left(ordered, day) - 1


def _active_strategy_map(
    targets: list[PortfolioTarget], strategy_ids: list[str], *, one_way_cost_bps: int,
    gate: dict | None, risk=None, regime: dict[str, bool] | None = None,
) -> tuple[dict[str, dict[str, set[str]]], dict]:
    """返回 ``(交易日 -> 标的 -> 当前已执行策略, 风控统计合计)``，不读取未来数据。"""
    daily: dict[str, dict[str, set[str]]] = {}
    totals: dict[str, int] = {}
    for target in targets:
        for strategy_id in strategy_ids:
            result = run_backtest(strategy_id, target.bars, one_way_cost_bps, gate=gate,
                                  risk=risk, regime=regime)
            for key, value in (result.get("risk_stats") or {}).items():
                totals[key] = totals.get(key, 0) + int(value)
            for point in result["curve"]:
                if point.get("position"):
                    daily.setdefault(str(point["period"]), {}).setdefault(target.thscode, set()).add(strategy_id)
    return daily, totals


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
    invested = [float(item["invested_weight"]) for item in curve if "invested_weight" in item]
    payload = {
        "cumulative_return_pct": round((values[-1] - 1) * 100, 4),
        "annualized_return_pct": round(annualized * 100, 4),
        "annualized_volatility_pct": round(volatility * 100, 4),
        "max_drawdown_pct": round(min(drawdowns) * 100, 4),
        "trade_count": trade_count,
        "annualized_turnover_pct": round(total_turnover / max(1, len(curve)) * 252 * 100, 4),
        "total_transaction_cost_pct": round(total_cost * 100, 4),
        "final_net_value": round(values[-1], 8),
    }
    # **仓位必须显式报出来**：不报的话，"收益为什么这么低"会被误判成信号不行，
    # 而真实原因往往是钱根本没投出去（2026-09-15 的隐式总仓位上限就是这么漏掉的）。
    if invested:
        payload.update({
            "avg_invested_weight_pct": round(sum(invested) / len(invested) * 100, 4),
            "min_invested_weight_pct": round(min(invested) * 100, 4),
            "max_invested_weight_pct": round(max(invested) * 100, 4),
        })
    return payload


def _equal_weight_buy_hold(targets: list[PortfolioTarget], dates: list[str], *,
                           one_way_cost_bps: int) -> list[dict]:
    """同一股票池的等权买入持有基线：**每只标的自己上市（或首个有 K 线）那天开盘买入**。

    支持参差不齐的上市时间：晚上市的标的在它开始交易那天才投入它的那一份资金，
    之前那份是现金（净值为 1 的部分）。用"全体共同起点"会强制把样本压到最晚上市那天，
    那是把"股票池大小"和"样本期间"绑在一起（见 `_trading_calendar` 的说明）。
    """
    if not dates:
        return []
    price_maps = {target.thscode: _bars_by_date(target) for target in targets}
    ordered = {code: sorted(mapping) for code, mapping in price_maps.items()}
    cost = one_way_cost_bps / 10_000
    weight = 1.0 / len(targets)
    holdings: dict[str, float] = {target.thscode: 0.0 for target in targets}
    entered: dict[str, bool] = {target.thscode: False for target in targets}
    curve: list[dict] = []
    for day in dates:
        equity = 0.0
        for code, mapping in price_maps.items():
            bar = mapping.get(day)
            if bar is None:
                equity += holdings[code]                       # 停牌：价格不变
                continue
            if not entered[code]:
                holdings[code] = weight * (1 - cost)
                entered[code] = True
            else:
                index = _previous_index(ordered[code], day)
                if index >= 0:
                    prior = mapping[ordered[code][index]]
                    holdings[code] *= float(bar.close) / float(prior.close)
                else:
                    holdings[code] *= float(bar.close) / float(bar.open)
            equity += holdings[code]
        curve.append({"period": day, "net_value": round(equity, 10)})
    return curve


def run_portfolio_backtest(
    targets: list[PortfolioTarget],
    *,
    strategy_ids: list[str],
    one_way_cost_bps: int,
    max_positions: int,
    max_single_weight: float,
    max_industry_weight: float,
    max_total_exposure: float = 0.95,
    analysis_start_date: str | None = None,
    gate: dict | None = None,
    risk=None,
    regime: dict[str, bool] | None = None,
    vol_exposure: dict[str, float] | None = None,
    construction: ConstructionPolicy | None = None,
) -> dict:
    """按日滚动配置研究组合；无信号或触及上限后不强行买入。

    `risk` 是 `strategy.models.RiskProfile`：逐标的回放时就地生效（止损/熔断/流动性/涨跌停
    不可成交），因此组合层拿到的仓位已经是**风控后**的仓位。返回的 ``daily_candidates``
    是审计资料：可复核为什么某天买入、为什么留现金。

    `vol_exposure` 是 ``{交易日: 总仓位上限}``（来自 `strategy.regime.vol_target_exposure`）：
    把当日所有目标权重**整体乘**这个系数，其余留在现金。它只压**深度**、不改选股，
    因此不会把"选股能力"和"仓位调节"两件事混在一起——两者必须能分开评价。

    `construction` 是 `portfolio.construction.ConstructionPolicy`：决定"每只买多少钱"。
    **传 `None` 时行为与历史完全一致**（按分数排序 + 单票/行业上限），
    这样任何新旧对比都只有一个变量在动。

    `max_total_exposure` 是**显式的总仓位上限**，默认 0.95——留 5% 现金覆盖换手成本，
    这样"满仓"不会因为付不起手续费而变成借款（引擎会直接报错，而不是悄悄少买）。
    在此之前，"投多少"是被**行业上限隐式**决定的：行业标签缺失时，"行业上限 30%"
    会退化成"总仓位 30%"，而且报告里看不到。这个参数就是那个 bug 的修复。
    """
    if not targets:
        raise ValueError("组合至少需要一个标的。")
    if not strategy_ids:
        raise ValueError("组合至少需要一个策略。")
    dates = _trading_calendar(targets)
    if analysis_start_date is not None:
        # 预热 K 线只供策略计算，不计入组合表现，避免把策略 warmup 期错当实验期。
        dates = [item for item in dates if item >= analysis_start_date]
    if len(dates) < 2:
        raise ValueError("标的之间缺少足够的可交易日。")
    target_by_code = {item.thscode: item for item in targets}
    price_maps = {item.thscode: _bars_by_date(item) for item in targets}
    ordered_dates = {code: sorted(mapping) for code, mapping in price_maps.items()}
    # 组合构建需要各标的**截至决策日**的日收益（无前视）。预计算一次、按窗口切片。
    return_series = ({item.thscode: build_return_series(item.bars) for item in targets}
                     if construction is not None else {})
    industries = {item.thscode: item.industry for item in targets}
    active, risk_totals = _active_strategy_map(targets, strategy_ids,
                                               one_way_cost_bps=one_way_cost_bps,
                                               gate=gate, risk=risk, regime=regime)
    cost_rate = one_way_cost_bps / 10_000
    cash = 1.0
    holdings: dict[str, float] = {}
    curve: list[dict] = []
    trades: list[dict] = []
    daily_candidates: list[dict] = []
    # 组合级回撤熔断：**只有这里才有组合净值**，所以它必须在这一层实现。
    # 恢复用**时间界**（暂停 N 个交易日后自动恢复），不用"回撤收敛到一半"——
    # 后者会死锁：暂停期间空仓、净值不动、回撤永远不收敛（单标的层实测踩到过）。
    breaker = getattr(risk, "drawdown_breaker_pct", None) if risk is not None else None
    breaker_cooldown = int(getattr(risk, "breaker_cooldown_days", 20)) if risk is not None else 0
    peak = 1.0
    pause_left = 0
    risk_stats = dict(risk_totals)
    risk_stats.update({"breaker_triggers": 0, "breaker_pause_days": 0,
                       "vol_target_scaled_days": 0, "vol_target_avg_exposure_pct": 0.0,
                       "vol_target_min_exposure_pct": 100.0})
    exposure_sum = 0.0
    exposure_days = 0
    exposure_min = 1.0

    for index, day in enumerate(dates):
        if index:
            # 先把昨收持仓过夜标记到今开，再按昨收已知的信号在今开调仓。
            # **停牌处理**：当天没有 K 线 → 价格不变（沿用上次收盘的市值），也不可交易。
            for code, value in list(holdings.items()):
                mapping = price_maps[code]
                bar = mapping.get(day)
                if bar is None:
                    continue
                position = _previous_index(ordered_dates[code], day)
                if position >= 0:
                    previous_close = float(mapping[ordered_dates[code][position]].close)
                    holdings[code] = value * float(bar.open) / previous_close

        pre_trade_equity = cash + sum(holdings.values())
        peak = max(peak, pre_trade_equity)
        # 熔断判断：用**今开的组合净值**与历史峰值比（不含当天之后的信息）
        if breaker is not None:
            if pause_left > 0:
                pause_left -= 1
                risk_stats["breaker_pause_days"] += 1
            elif peak and pre_trade_equity / peak - 1 <= -breaker:
                pause_left = max(0, breaker_cooldown - 1)
                risk_stats["breaker_triggers"] += 1
                risk_stats["breaker_pause_days"] += 1
        paused = pause_left > 0
        candidates = []
        for code, active_ids in active.get(day, {}).items():
            target = target_by_code[code]
            candidates.append({"thscode": code, "name": target.name, "industry": target.industry,
                               "strategy_ids": sorted(active_ids), "score": len(active_ids)})
        if paused:
            # 暂停期语义：**不开新仓、已持仓的仍在信号内的按原值留着**（不调仓、不加仓）；
            # 信号已消失的照常退出（那是止损/止盈的正常路径，不该被熔断冻住）。
            kept = {code: value for code, value in holdings.items() if code in active.get(day, {})}
            target_weights = ({code: value / pre_trade_equity for code, value in kept.items()}
                              if pre_trade_equity else {})
            exposure = 1.0
        else:
            target_weights = allocate_candidates(
                candidates, max_positions=max_positions, max_single_weight=max_single_weight,
                max_industry_weight=max_industry_weight, max_total_exposure=max_total_exposure)
            if construction is not None:
                # 组合构建层：用**风险**而不是等权分钱。约束（上限/换手带）全在它内部施加。
                current = ({code: value / pre_trade_equity for code, value in holdings.items()}
                           if pre_trade_equity else {})
                target_weights = build_target_weights(
                    candidates, policy=construction,
                    returns_by_code={code: series.window(day, construction.lookback_days)
                                     for code, series in return_series.items()
                                     if code in {str(item["thscode"]) for item in candidates}},
                    industries=industries, current_weights=current)
            # 波动率目标：整体缩放总仓位（封顶 1.0，不加杠杆）。熔断暂停期间不缩放——
            # 那时已经在降风险，再叠一层会让两套规则的归因纠缠不清。
            exposure = 1.0 if vol_exposure is None else float(vol_exposure.get(day, 1.0))
            exposure = min(1.0, max(0.0, exposure))
            if target_weights and exposure < 1.0:
                target_weights = {code: weight * exposure for code, weight in target_weights.items()}
            if vol_exposure is not None:
                exposure_days += 1
                exposure_sum += exposure
                exposure_min = min(exposure_min, exposure)
                if exposure < 0.999999:
                    risk_stats["vol_target_scaled_days"] += 1
        # **停牌不可交易**：当天没有 K 线的标的只能维持原仓位（卖不掉、也买不进）。
        # 两件事必须同时做：①它的目标值钉在当前值上；②它占掉的那部分额度要从**其余标的**
        # 的预算里扣掉——否则"钉住的旧仓位 + 新建仓位"会超过总仓位上限，直接触发借款报错。
        # 不这么做，停牌标的还会被"按不存在的价格"平仓，净值凭空跳变。
        frozen = ({code: value / pre_trade_equity for code, value in holdings.items()
                   if day not in price_maps[code]} if pre_trade_equity else {})
        if frozen:
            rest = {code: weight for code, weight in target_weights.items()
                    if code not in frozen}
            budget = max(0.0, sum(target_weights.values()) - sum(frozen.values()))
            rest_total = sum(rest.values())
            scale = budget / rest_total if rest_total > 0 else 0.0
            target_weights = {**{code: weight * scale for code, weight in rest.items()},
                              **frozen}
        target_values = {code: pre_trade_equity * weight for code, weight in target_weights.items()}
        for code in set(target_values) | set(holdings):
            if day not in price_maps[code]:
                target_values[code] = holdings.get(code, 0.0)
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
            bar = price_maps[code].get(day)
            if bar is None:
                continue                                   # 停牌：市值不变
            holdings[code] = value * float(bar.close) / float(bar.open)
        equity = cash + sum(holdings.values())
        invested_weight = sum(holdings.values()) / equity if equity else 0.0
        daily_candidates.append({"date": day, "candidates": candidates, "selected_weights": target_weights,
                                 "exposure": round(exposure, 8),
                                 "cash_weight_at_open": round(cash / (pre_trade_equity - transaction_cost), 8)
                                 if pre_trade_equity > transaction_cost else 0.0})
        curve.append({"period": day, "net_value": round(equity, 10),
                      "invested_weight": round(invested_weight, 8), "candidate_count": len(candidates),
                      "selected_count": len(target_weights), "turnover_value": round(turnover, 10),
                      "transaction_cost": round(transaction_cost, 10),
                      "exposure": round(exposure, 8)})

    if exposure_days:
        risk_stats["vol_target_avg_exposure_pct"] = round(exposure_sum / exposure_days * 100, 4)
        risk_stats["vol_target_min_exposure_pct"] = round(exposure_min * 100, 4)
    else:
        risk_stats["vol_target_min_exposure_pct"] = 100.0

    equal_curve = _equal_weight_buy_hold(targets, dates, one_way_cost_bps=one_way_cost_bps)
    portfolio_metrics = _metrics(curve, trade_count=len(trades))
    equal_metrics = _metrics(equal_curve, trade_count=len(targets))

    # 组合构建审计：**实际风险贡献**（权重的最终结果，不是权重本身）。
    # ERC 声称"每只风险贡献相同"，那就必须拿这个数字来验，而不是拿权重看着差不多就算。
    construction_audit = None
    if construction is not None:
        final_day = dates[-1]
        final_weights = ({code: value / (cash + sum(holdings.values()))
                          for code, value in holdings.items()}
                         if (cash + sum(holdings.values())) > 0 else {})
        contributions = risk_contributions(
            final_weights,
            {code: series.window(final_day, construction.lookback_days)
             for code, series in return_series.items()},
            shrinkage=construction.shrinkage)
        construction_audit = {
            "scheme": construction.scheme,
            "lookback_days": construction.lookback_days,
            "shrinkage": construction.shrinkage,
            "turnover_band": construction.turnover_band,
            "final_weights": {code: round(value, 8) for code, value in sorted(final_weights.items())},
            "final_risk_contributions": {code: round(value, 8)
                                         for code, value in sorted(contributions.items())},
        }
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
        "risk_profile": None if risk is None else getattr(risk, "id", None),
        "construction": construction_audit,
        "risk_stats": risk_stats,
        "production_eligibility": "none：本回放不改变策略候选门禁或顾问生产建议。",
    }
