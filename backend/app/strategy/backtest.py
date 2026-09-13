from __future__ import annotations
import math
from .features import feature_rows
from .models import PriceBar

def current_signal_snapshot(strategy_id: str, bars: list[PriceBar], *, gate: dict | None = None) -> dict:
    """用最新已完成 K 线生成下一交易日的确定性策略信号，不调用模型。"""
    if not bars:
        return {}
    features = feature_rows(bars, **(gate or {}))
    position = 0
    held = 0
    for i in range(1, len(bars)):
        previous = features[i - 1]
        gate_ok = (previous.get("vol") is None or previous.get("vol_gate") is None
                   or previous["vol"] <= previous["vol_gate"])
        if strategy_id == "trend_20_60_v1":
            desired = int(bool(previous.get("ma20") and previous.get("ma60")
                               and bars[i - 1].close > previous["ma20"] > previous["ma60"]))
        else:
            desired = (int(held < 5) if position else int(bool(
                previous.get("ret5_q10") is not None and previous.get("ret5") is not None
                and previous["ret5"] <= previous["ret5_q10"]
                and previous.get("ma60") and bars[i - 1].close > previous["ma60"])))
        if not gate_ok and not position:
            desired = 0
        if bars[i].open is not None and desired != position:
            position = desired
            held = 0
        if position:
            held += 1

    latest = features[-1]
    gate_ok = (latest.get("vol") is None or latest.get("vol_gate") is None
               or latest["vol"] <= latest["vol_gate"])
    if strategy_id == "trend_20_60_v1":
        desired_next = int(bool(latest.get("ma20") and latest.get("ma60")
                                and bars[-1].close > latest["ma20"] > latest["ma60"]))
    else:
        desired_next = (int(held < 5) if position else int(bool(
            latest.get("ret5_q10") is not None and latest.get("ret5") is not None
            and latest["ret5"] <= latest["ret5_q10"]
            and latest.get("ma60") and bars[-1].close > latest["ma60"])))
    if not gate_ok and not position:
        desired_next = 0
    action = ("enter" if not position and desired_next else
              "hold" if position and desired_next else
              "exit" if position and not desired_next else "flat")
    return {"as_of": bars[-1].date, "current_position": position,
            "next_position": desired_next, "action": action,
            "volatility_gate_passed": bool(gate_ok),
            "basis": "latest_completed_forward_adjusted_daily_bar"}

def run_backtest(strategy_id: str, bars: list[PriceBar], cost_bps: int, *, gate: dict | None = None) -> dict:
    features=feature_rows(bars, **(gate or {})); position=0; entry=None; held=0; cash=1.0; benchmark=1.0; peak=1.0; trades=[]; curve=[]; costs=cost_bps/10000
    for i, bar in enumerate(bars):
        # 信号在 i-1 收盘后形成，最早只能在 i 的开盘成交——净值曲线必须与这个执行
        # 模型**一致**：换手当日按成交价（开盘）盯市，而不是按「前收→收盘」的整段涨跌。
        # 旧实现在换手当日沿用 close_i/close_{i-1}，等于按前收成交却只按开盘扣成本，
        # 有跳空时开仓偏乐观、平仓漏掉隔夜变动，而且与 trades[].return_pct（open→open）
        # 互相矛盾。**无跳空时两种算法恰好相同**，所以这个缺陷长期没被发现。
        execute = i>0 and bars[i].open is not None
        previous=features[i-1] if i else {}
        desired=0
        if i:
            gate_ok = previous.get("vol") is None or previous.get("vol_gate") is None or previous["vol"] <= previous["vol_gate"]
            if strategy_id=="trend_20_60_v1": desired=int(bool(previous.get("ma20") and previous.get("ma60") and bars[i-1].close>previous["ma20"]>previous["ma60"]))
            else:
                desired = int(held < 5) if position else int(bool(previous.get("ret5_q10") is not None and previous.get("ret5") is not None and previous["ret5"]<=previous["ret5_q10"] and previous.get("ma60") and bars[i-1].close>previous["ma60"]))
            if not gate_ok and not position: desired=0
        # `desired != position` 时恰好只有一支分支会执行（旧代码同样如此）。
        if execute and desired != position and position:
            # 先按开盘价把已有仓位盯市到成交时点，再扣平仓成本。
            cash *= bar.open/bars[i-1].close
            cash *= (1-costs)
            trades[-1]["exit_date"] = bar.date
            trades[-1]["exit_price"] = bar.open
            trades[-1]["return_pct"] = round(((bar.open / entry) * (1-costs) ** 2 - 1) * 100, 4)
            position=0; held=0
        elif execute and desired != position and desired:
            cash *= (1-costs)                 # 开仓成本按开盘成交价计
            position=1; entry=bar.open; held=0
            trades.append({"entry_date":bar.date,"entry_price":bar.open})
            cash *= bar.close/bar.open        # 从成交价盯市到当日收盘
        elif i and position:
            cash *= bar.close/bars[i-1].close
        if i and position: held += 1
        if i: benchmark *= bar.close/bars[i-1].close
        peak=max(peak,cash); curve.append({"period":bar.date,"net_value":round(cash,6),"benchmark_net_value":round(benchmark,6),"drawdown_pct":round((cash/peak-1)*100,4),"position":position})
    if position and trades:
        # 回测末日的持仓仅按收盘盯市，不假定为了展示指标而额外平仓；因此只
        # 计入已经发生的开仓成本，避免把并未执行的未来卖出成本混进历史结果。
        trades[-1]["exit_date"] = bars[-1].date
        trades[-1]["exit_price"] = bars[-1].close
        trades[-1]["return_pct"] = round(((bars[-1].close / entry) * (1-costs) - 1) * 100, 4)
    return {"curve":curve,"trades":trades}

def metrics(curve: list[dict], trades: list[dict]) -> dict:
    if len(curve)<2: return {}
    values=[p["net_value"] for p in curve]; bench=[p["benchmark_net_value"] for p in curve]; daily=[values[i]/values[i-1]-1 for i in range(1,len(values))]
    annual=(values[-1]**(252/max(1,len(daily)))-1)*100; vol=(math.sqrt(sum((x-sum(daily)/len(daily))**2 for x in daily)/len(daily))*math.sqrt(252)*100) if daily else 0
    max_dd=min(p["drawdown_pct"] for p in curve); returns=[t.get("return_pct",0) for t in trades]; wins=[x for x in returns if x>0]; losses=[x for x in returns if x<0]
    benchmark_peak=1.0; benchmark_dd=[]
    for value in bench:
        benchmark_peak=max(benchmark_peak,value); benchmark_dd.append((value/benchmark_peak-1)*100)
    longest_dd=current_dd=0
    for point in curve:
        if point["drawdown_pct"] < 0:
            current_dd += 1; longest_dd=max(longest_dd,current_dd)
        else: current_dd=0
    turnover=(len(trades)*2/max(1,len(daily)))*252
    return {"cumulative_return_pct":round((values[-1]-1)*100,4),"annualized_return_pct":round(annual,4),"annualized_volatility_pct":round(vol,4),"sharpe_zero_rf":round((sum(daily)/len(daily))/(vol/100/math.sqrt(252)) if vol else 0,4),"benchmark_return_pct":round((bench[-1]-1)*100,4),"excess_return_pct":round((values[-1]-bench[-1])*100,4),"max_drawdown_pct":round(max_dd,4),"benchmark_max_drawdown_pct":round(min(benchmark_dd),4),"max_drawdown_duration_days":longest_dd,"annualized_turnover":round(turnover,4),"trade_count":len(trades),"hit_rate":round(len(wins)/len(returns),4) if returns else None,"average_win_pct":round(sum(wins)/len(wins),4) if wins else None,"average_loss_pct":round(sum(losses)/len(losses),4) if losses else None,"profit_loss_ratio":round((sum(wins)/len(wins))/abs(sum(losses)/len(losses)),4) if wins and losses else None,"coverage_pct":round(sum(1 for point in curve if point.get("position"))/len(curve)*100,2)}

def segments(strategy_id: str, bars: list[PriceBar], cost_bps: int, *, gate: dict | None = None) -> list[dict]:
    """按时间切分样本外表现，但保留切分点之前的完整可见历史。

    验证/测试段不能从零开始重算均线和波动率：那会把策略所需的预热期错误地
    算进样本外段，尤其会让 20% 测试段在 252 根预热要求下永远没有信号。
    先对全序列进行一次无前视回放，再在分界点对净值基准化；每个切片仍只使用
    当时及以前的 K 线和已发生交易成本。
    """
    n = len(bars)
    full = run_backtest(strategy_id, bars, cost_bps, gate=gate)
    out = []
    for label, a, b in (("development", 0, int(n * .6)),
                        ("validation", int(n * .6), int(n * .8)),
                        ("test", int(n * .8), n)):
        curve = _rebased_segment_curve(full["curve"], a, b)
        trades = _segment_entry_trades(full["trades"], bars, a, b, cost_bps)
        out.append({"segment": label, "signal_history_basis": "full_history_before_segment_end",
                    **metrics(curve, trades)})
    return out


def _rebased_segment_curve(curve: list[dict], start: int, end: int) -> list[dict]:
    """把全周期净值切片转换为该分段起点=1 的独立评价曲线。"""
    points = curve[start:end]
    if not points:
        return []
    prior = curve[start - 1] if start > 0 else None
    net_base = float(prior["net_value"]) if prior else 1.0
    benchmark_base = float(prior["benchmark_net_value"]) if prior else 1.0
    peak = 1.0
    rebased = []
    for point in points:
        net_value = float(point["net_value"]) / net_base if net_base else float(point["net_value"])
        benchmark_value = (float(point["benchmark_net_value"]) / benchmark_base
                           if benchmark_base else float(point["benchmark_net_value"]))
        peak = max(peak, net_value)
        rebased.append({"period": point["period"], "net_value": net_value,
                        "benchmark_net_value": benchmark_value,
                        "drawdown_pct": round((net_value / peak - 1) * 100, 4) if peak else 0.0,
                        "position": point.get("position", 0)})
    return rebased


def _segment_entry_trades(trades: list[dict], bars: list[PriceBar], start: int, end: int,
                          cost_bps: int) -> list[dict]:
    """仅统计分段内新开仓；跨段持仓不会借用段末之后的成交结果。"""
    if start >= end:
        return []
    dates = {bar.date for bar in bars[start:end]}
    end_bar = bars[end - 1]
    costs = cost_bps / 10_000
    result = []
    for trade in trades:
        if trade.get("entry_date") not in dates:
            continue
        item = dict(trade)
        if item.get("exit_date") not in dates:
            # 该笔交易在分段结束时仍未退出：只盯市至段末，并只扣已发生的开仓成本。
            entry_price = float(item["entry_price"])
            item["exit_date"] = end_bar.date
            item["exit_price"] = end_bar.close
            item["return_pct"] = round(((end_bar.close / entry_price) * (1-costs) - 1) * 100, 4)
            item["marked_to_segment_end"] = True
        result.append(item)
    return result

def year_segments(curve: list[dict], trades: list[dict]) -> list[dict]:
    """按自然年切分全周期净值曲线，输出年度内分段指标（描述性，不重跑回测）。

    年度收益以上一自然年年末净值为基，避免把跨年涨幅记入下一年；
    年度内回撤按该年内运行峰值重算，与全周期回撤口径分开。
    """
    groups: dict[str, list[dict]] = {}
    for point in curve:
        groups.setdefault(str(point["period"])[:4], []).append(point)
    out = []
    base = bench_base = 1.0
    for year in sorted(groups):
        points = groups[year]
        peak = base
        scaled = []
        for point in points:
            peak = max(peak, point["net_value"])
            scaled.append({"period": point["period"],
                           "net_value": point["net_value"] / base if base else point["net_value"],
                           "benchmark_net_value": point["benchmark_net_value"] / bench_base if bench_base else point["benchmark_net_value"],
                           "drawdown_pct": round((point["net_value"] / peak - 1) * 100, 4) if peak else 0.0,
                           "position": point.get("position", 0)})
        yearly_trades = [t for t in trades if str(t.get("entry_date", ""))[:4] == year]
        out.append({"year": year, **metrics(scaled, yearly_trades)})
        base = points[-1]["net_value"]
        bench_base = points[-1]["benchmark_net_value"]
    return out
