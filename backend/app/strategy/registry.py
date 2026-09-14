from .models import RiskProfile, StrategySpec

_SPECS = {
 "buy_hold_v1": StrategySpec(id="buy_hold_v1", version="v1", name="买入持有基准", family="benchmark", asset_types=["a-share"], horizon="长期", required_fields=["open_price","close_price"], warmup_bars=1, parameters={}, signal_rule="首个可执行开盘后持续持有", exit_rule="实验结束", risk_rule="无", cost_basis="单边成本假设", benchmark_id="buy_hold_v1", applicability="仅作比较", contraindications="不得作为推荐策略", research_sources=[], lifecycle="experimental"),
 "trend_20_60_v1": StrategySpec(id="trend_20_60_v1", version="v2", name="20/60 日趋势", family="trend", asset_types=["a-share"], horizon="中期", required_fields=["open_price","close_price"], warmup_bars=252, parameters={"fast":20,"slow":60}, signal_rule="收盘 close > MA20 > MA60，下一日开盘持有", exit_rule="条件失效，下一日开盘退出", risk_rule="volatility_gate_v1：20 日年化波动高于仅由此前 252 日计算的 80% 分位时不新开仓", cost_basis="单边成本假设", benchmark_id="buy_hold_v1", applicability="趋势连续行情", contraindications="高波动或震荡行情", research_sources=[]),
 "reversal_5d_v1": StrategySpec(id="reversal_5d_v1", version="v2", name="5 日短期反转", family="reversal", asset_types=["a-share"], horizon="5 交易日", required_fields=["open_price","close_price"], warmup_bars=252, parameters={"return_window":5,"quantile_window":120,"quantile":0.1,"ma":60,"hold_days":5}, signal_rule="5 日收益低于此前 120 日第 10 分位且 close > MA60，下一日开盘进入", exit_rule="持有 5 个交易日后下一日开盘退出", risk_rule="volatility_gate_v1：20 日年化波动高于仅由此前 252 日计算的 80% 分位时不新开仓", cost_basis="单边成本假设", benchmark_id="buy_hold_v1", applicability="均值回归明显且中期趋势向上", contraindications="下跌趋势或高波动行情", research_sources=[]),
 "volatility_gate_v1": StrategySpec(id="volatility_gate_v1", version="v1", name="20 日波动率闸门", family="risk_overlay", asset_types=["a-share"], horizon="不适用", required_fields=["close_price"], warmup_bars=252, parameters={"vol_window":20,"vol_history":252,"vol_quantile":0.8}, signal_rule="不产生交易信号；作为 trend/reversal 策略的共同风险覆盖层", exit_rule="不适用", risk_rule="20 日年化波动高于仅由此前 252 日计算的 80% 分位时不新开仓", cost_basis="单边成本假设", benchmark_id="buy_hold_v1", applicability="为趋势与反转策略提供统一的高波动拦截", contraindications="不得单独作为交易策略运行实验", research_sources=[]),
}
def list_specs(): return list(_SPECS.values())
def get_spec(strategy_id: str) -> StrategySpec: return _SPECS[strategy_id]


#: **风控档位**（用户 2026-09-15 定下的验收偏好：最大回撤 20% / 单年最差 −10% /
#: 回撤最长持续 12 个月 / 单笔最大风险 1%）。档位是**声明式**的：跑任何策略都可以
#: 挂不同档位做对比，风控与信号因此解耦。
_RISK_PROFILES = {
    # 基准档：什么都不拦。用于回答"风控到底起了多大作用"（对照组必须有）。
    "risk_none_v1": RiskProfile(id="risk_none_v1", name="无风控（对照基准）"),
    # 保守档：按用户给的容忍度反推——止损 8%，单票最多 25%，回撤超 20% 停止开新仓，
    # 成交额低于 1 亿不碰，涨停开盘不买、跌停不卖，并叠加既有波动率闸门。
    "risk_conservative_v1": RiskProfile(
        id="risk_conservative_v1", name="保守档（止损 8% + 回撤熔断 20% + 流动性 1 亿）",
        stop_loss_pct=0.08, drawdown_breaker_pct=0.20, min_turnover=1.0e8,
        limit_move_pct=0.095, volatility_gate=True),
    # 宽止损档：只拦最坏情况（止损放到 15%、熔断 30%），用来判断"紧止损是不是在砍掉收益"。
    "risk_loose_v1": RiskProfile(
        id="risk_loose_v1", name="宽档（止损 15% + 回撤熔断 30%）",
        stop_loss_pct=0.15, drawdown_breaker_pct=0.30, min_turnover=1.0e8,
        limit_move_pct=0.095, volatility_gate=True),
}


def list_risk_profiles() -> list[RiskProfile]:
    return list(_RISK_PROFILES.values())


def get_risk_profile(profile_id: str) -> RiskProfile:
    return _RISK_PROFILES[profile_id]


def risk_gate(profile: RiskProfile | None) -> dict:
    """把风控档位里**与特征计算有关**的部分转成 `feature_rows` 的参数。

    只有波动率闸门需要在特征层生效（它决定 `vol_gate` 是否存在）；其余规则都在
    回测主循环里逐 bar 判断，因为它们依赖**当时**的净值与成交价。
    """
    if profile is None or not profile.volatility_gate:
        return {}
    spec = _SPECS["volatility_gate_v1"].parameters
    return {"vol_window": spec["vol_window"], "vol_history": spec["vol_history"],
            "vol_quantile": spec["vol_quantile"]}
