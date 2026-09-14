"""风控层与验收判据的守卫测试（`25` 号文档 / D70–D73）。

这个文件里最重要的一条是 **`test_breaker_pause_is_time_bounded_and_does_not_deadlock`**：
第一版回撤熔断写成「回撤收敛到一半以内才恢复」，实测**死锁**——熔断后空仓、净值不动、
回撤永不收敛，该标的从此再不交易（症状：逐年收益连续几年都是 0.00%）。
**这类 bug 不报错、只是悄悄不交易**，所以必须有守卫。

其余三条规则（止损、涨停不可买、流动性门槛）同样都属于"错了也不报错、只是数字变形"
的类型，逐条钉住。
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.app.portfolio.backtest import PortfolioTarget, run_portfolio_backtest
from backend.app.strategy.acceptance import AcceptanceThresholds, evaluate_acceptance
from backend.app.strategy.backtest import _gapped, run_backtest
from backend.app.strategy.models import PriceBar, RiskProfile
from backend.app.strategy.registry import get_risk_profile, list_risk_profiles
from backend.app.strategy.regime import (  # noqa: E402
    realized_vol_series,
    regime_gate,
    vol_target_exposure,
)

START = date(2020, 1, 1)


def bars_from(closes: list[float], *, opens: list[float] | None = None,
              lows: list[float] | None = None, turnover: float | None = 5.0e8) -> list[PriceBar]:
    out: list[PriceBar] = []
    for index, close in enumerate(closes):
        opening = opens[index] if opens else close
        low = lows[index] if lows else min(opening, close)
        out.append(PriceBar(date=(START + timedelta(days=index)).isoformat(),
                            open=opening, close=close, low=low,
                            high=max(opening, close), turnover=turnover))
    return out


def _trending_up(count: int = 160, step: float = 0.004) -> list[float]:
    return [10.0 * (1 + step) ** index for index in range(count)]


# --- 0. 不挂风控时必须与历史行为一致 -----------------------------------------

def test_risk_none_profile_matches_no_risk_at_all():
    """`risk=risk_none_v1` 与不传风控必须给出**同一条净值曲线**（否则对照就是假的）。"""
    bars = bars_from(_trending_up())
    plain = run_backtest("trend_20_60_v1", bars, 10)
    none_profile = run_backtest("trend_20_60_v1", bars, 10, risk=get_risk_profile("risk_none_v1"))
    assert plain["curve"] == none_profile["curve"]
    assert plain["trades"] == none_profile["trades"]


# --- 1. 回撤熔断：必须是时间界，不得死锁 -------------------------------------

def test_breaker_must_be_time_bounded():
    """**死锁的结构守卫**：熔断必须带一个 ≥1 个交易日的暂停期。

    第一版用「回撤收敛到一半以内才恢复」→ 空仓时净值不动、回撤永不收敛 → 死锁，
    症状是逐年收益连续几年 0.00%（实测踩到）。现在恢复机制只允许**时间界**，
    因此 `breaker_cooldown_days` 被模型约束为 ≥1：想配置一个"永不恢复"的熔断会被直接拒绝。

    （端到端的熔断行为在真实数据上由 `scripts/run_strategy_acceptance.py` 的
    `risk_stats.breaker_triggers / breaker_pause_days` 报告，不靠脆弱的合成行情。）
    """
    import pytest

    with pytest.raises(Exception):
        RiskProfile(id="risk_bad_v1", name="坏档", drawdown_breaker_pct=0.2,
                    breaker_cooldown_days=0)
    ok = RiskProfile(id="risk_ok_v1", name="好档", drawdown_breaker_pct=0.2,
                     breaker_cooldown_days=1)
    assert ok.breaker_cooldown_days == 1
    # 默认值必须是"能自己恢复"的
    assert RiskProfile(id="risk_default_v1", name="默认").breaker_cooldown_days >= 1


def test_risk_stats_are_reported_so_the_layer_is_auditable():
    """统计必须报出来：否则无法回答「风控到底拦了什么」（等于不可审计）。"""
    bars = bars_from(_trending_up())
    result = run_backtest("trend_20_60_v1", bars, 10,
                          risk=get_risk_profile("risk_conservative_v1"))
    for key in ("entries", "stop_exits", "blocked_entries_limit_up",
                "blocked_exits_limit_down", "skipped_entries_liquidity",
                "skipped_entries_missing_turnover", "skipped_entries_cooldown"):
        assert key in result["risk_stats"], f"缺少风控统计 {key}"
    assert result["risk_profile"] == "risk_conservative_v1"


# --- 2. 止损：要真的截断亏损 -------------------------------------------------

def test_stop_loss_exits_with_that_reason_and_caps_the_loss():
    """入场后跌破止损线 → 以 `stop` 为原因离场，亏损被截断在止损幅度附近。"""
    up = _trending_up(80, 0.008)
    entry_day = len(up)
    opens = up + [up[-1] * 1.02, up[-1] * 0.80]       # 次日高开、再过一天崩到 −20%
    closes = up + [up[-1] * 1.01, up[-1] * 0.80]
    lows = up + [up[-1] * 1.00, up[-1] * 0.78]
    bars = bars_from(closes, opens=opens, lows=lows)
    profile = RiskProfile(id="risk_test_stop_v1", name="测试用止损档",
                          stop_loss_pct=0.08, volatility_gate=False)
    result = run_backtest("trend_20_60_v1", bars, 10, risk=profile)

    stops = [trade for trade in result["trades"] if trade.get("exit_reason") == "stop"]
    assert stops, "跌破 8% 止损线却没有任何一笔以 stop 离场"
    assert result["risk_stats"]["stop_exits"] == len(stops)
    worst = min(trade["return_pct"] for trade in stops)
    assert worst > -25, f"止损后单笔亏损 {worst}% 明显超出止损幅度（说明成交价算错了）"


def test_stop_loss_reduces_the_loss_when_the_decline_continues():
    """止损的价值**取决于之后是否继续下跌**——这里给一个"继续深跌"的场景。

    诚实说明：这不是"止损永远更好"。跌破止损线后如果马上反弹，止损反而更差
    （把浮亏变实亏、还错过反弹）——本项目的 8/20 只标的实测都出现过类似现象。
    所以这条只钉住**方向明确的那一半**：持续下跌时，止损必须减少亏损。
    两边的特征门禁保持一致（都关掉波动率闸门），否则差异分不清是谁造成的。
    """
    up = _trending_up(80, 0.008)
    opens = up + [up[-1] * 0.95] * 12
    closes = up + [up[-1] * 0.95 * (1 - 0.03) ** k for k in range(1, 13)]
    lows = [min(o, c) for o, c in zip(opens, closes)]
    bars = bars_from(closes, opens=opens, lows=lows)
    stopped = run_backtest("trend_20_60_v1", bars, 10,
                           risk=RiskProfile(id="risk_test_a_v1", name="止损",
                                            stop_loss_pct=0.08, volatility_gate=False))
    plain = run_backtest("trend_20_60_v1", bars, 10)
    assert stopped["risk_stats"]["stop_exits"] + stopped["risk_stats"]["entries"] > 0, \
        "这段行情里两边都没有交易，测试本身失效"
    assert min(p["net_value"] for p in stopped["curve"]) >= min(
        p["net_value"] for p in plain["curve"]) - 1e-9


# --- 3. 涨停不可买 / 流动性门槛 ----------------------------------------------

def test_limit_up_blocking_rule_itself():
    """涨停不可成交的判据：开盘相对前收跳空 ≥ 阈值就算买不进/卖不出。

    这里直接测判据函数（构造"入场当天恰好跳空涨停"的合成序列很脆——趋势策略在那天
    往往早已持仓，于是根本没有"入场尝试"可拦，测出来会误报通过）。
    """
    assert _gapped(11.0, 10.0, 0.095, down=False) is True      # +10% 涨停 → 买不进
    assert _gapped(10.9, 10.0, 0.095, down=False) is False     # +9% 未涨停 → 能买
    assert _gapped(9.0, 10.0, 0.095, down=True) is True        # −10% 跌停 → 卖不出
    assert _gapped(None, 10.0, 0.095, down=False) is False     # 缺开盘价 → 不臆断
    assert _gapped(11.0, None, 0.095, down=False) is False     # 缺前收 → 不臆断


def test_liquidity_floor_and_missing_turnover_both_block_entries():
    """成交额不足、或成交额缺失 → 都不开仓（缺失时**不假装它达标**）。"""
    up = _trending_up(80, 0.008)
    thin = RiskProfile(id="risk_test_liq_v1", name="测试用流动性档",
                       min_turnover=1.0e9, volatility_gate=False)
    thin_result = run_backtest("trend_20_60_v1", bars_from(up, turnover=1.0e7), 10, risk=thin)
    assert thin_result["risk_stats"]["skipped_entries_liquidity"] >= 1
    assert thin_result["risk_stats"]["entries"] == 0

    missing_result = run_backtest("trend_20_60_v1", bars_from(up, turnover=None), 10, risk=thin)
    assert missing_result["risk_stats"]["skipped_entries_missing_turnover"] >= 1
    assert missing_result["risk_stats"]["entries"] == 0


def test_all_declared_risk_profiles_are_wellformed():
    profiles = list_risk_profiles()
    assert {profile.id for profile in profiles} >= {"risk_none_v1", "risk_conservative_v1"}
    for profile in profiles:
        assert profile.name
        if profile.stop_loss_pct is not None:
            assert 0 < profile.stop_loss_pct <= 0.5


# --- 4. 验收判据本身 ---------------------------------------------------------

def _curve(values: list[float], start_year: int = 2018) -> list[dict]:
    day = date(start_year, 1, 1)
    out = []
    for index, value in enumerate(values):
        out.append({"period": (day + timedelta(days=index)).isoformat(), "net_value": value})
    return out


def test_acceptance_passes_a_smooth_uptrend():
    values = [1.0 * (1.0006) ** index for index in range(1200)]
    verdict = evaluate_acceptance(_curve(values), label="测试")
    assert verdict["verdict"] == "通过", verdict["summary"]
    assert verdict["metrics"]["max_drawdown_pct"] > -1


def test_acceptance_fails_a_deep_drawdown_and_names_the_check():
    values = [1.0 * (1.0008) ** index for index in range(400)]
    values += [values[-1] * (1 - 0.003) ** k for k in range(1, 200)]      # 深跌 ~45%
    values += [values[-1] * (1.0005) ** k for k in range(1, 600)]
    verdict = evaluate_acceptance(_curve(values), label="测试")
    assert verdict["verdict"] == "不通过"
    assert "max_drawdown" in verdict["failed_checks"]
    check = next(item for item in verdict["checks"] if item["key"] == "max_drawdown")
    assert check["measured"] < -20 and "没有剔除" in check["why"]


def test_acceptance_refuses_to_judge_tiny_samples():
    verdict = evaluate_acceptance(_curve([1.0, 1.01, 1.02]), label="测试")
    assert verdict["verdict"] == "数据不足"
    assert verdict["checks"] == []


def test_threshold_defaults_are_the_ones_the_user_confirmed():
    """验收阈值是用户确认的，不能悄悄改。

    回撤持续这一条：2026-09-15 首轮跑完（实测 586–792 天，阈值 365 天不过）之后，
    用户**主动把它收紧到 183 天（半年）**——收紧方向合规；若是看到不过就放宽，
    那才是"调参以通过"。
    """
    thresholds = AcceptanceThresholds()
    assert thresholds.max_drawdown_pct == 20.0
    assert thresholds.max_worst_year_loss_pct == 10.0
    assert thresholds.max_drawdown_duration_days == 183
    assert thresholds.require_positive_expectancy is True


def test_regime_gate_is_not_look_ahead_and_blocks_before_warmup():
    """市场状态序列：均线成形前一律不可开仓；`risk_on` 只看当日及以前的指数。

    构造一个"先跌后涨"的人工指数序列：在均线还没成形时必须为 False；
    指数上穿均线之后才允许开仓。
    """
    series = {}
    index = 1.0
    for day in range(1, 41):
        index *= 1.0 - 0.01 if day <= 20 else 1.03      # 先跌 20 天，再涨 20 天
        # `ma` 刻意等于 `index` → `risk_on` 必须是 False（判据是**严格大于**）
        series[f"2020-01-{day:02d}"] = {"index": index, "ma": index, "risk_on": False,
                                        "samples": day}
    gate = regime_gate(series, warmup_days=10)
    assert gate["2020-01-05"] is False, "均线样本不足时必须拒绝开仓（宁可晚入场）"
    assert gate["2020-01-15"] is False, "index 不大于 ma 时必须拒绝"

    series["2020-02-01"] = {"index": 1.2, "ma": 1.0, "risk_on": True, "samples": 60}
    assert regime_gate(series, warmup_days=10)["2020-02-01"] is True, "上穿均线后应允许开仓"
    # 样本不足时即使 risk_on 为真也必须拒绝（预热期不得假装"大盘没问题"）
    series["2020-02-02"] = {"index": 1.3, "ma": 1.0, "risk_on": True, "samples": 3}
    assert regime_gate(series, warmup_days=10)["2020-02-02"] is False


def test_regime_filter_blocks_new_entries(repository=None):
    """风控档位里 `regime_filter=True` 时，状态为"关"的日子不得开新仓。"""
    bars = bars_from(_trending_up())
    profile = RiskProfile(id="risk_test_regime_v1", name="测试用状态档",
                          regime_filter=True, volatility_gate=False)
    off = {bar.date: False for bar in bars}
    result = run_backtest("trend_20_60_v1", bars, 10, risk=profile, regime=off)
    assert result["risk_stats"]["entries"] == 0
    assert result["risk_stats"]["skipped_entries_regime"] > 0

    on = {bar.date: True for bar in bars}
    allowed = run_backtest("trend_20_60_v1", bars, 10, risk=profile, regime=on)
    assert allowed["risk_stats"]["entries"] > 0
    assert allowed["risk_stats"]["skipped_entries_regime"] == 0


# --- 5. 波动率目标：只压深度、不加杠杆、不降到 0 -----------------------------

def test_vol_target_exposure_never_leverages_and_respects_the_floor():
    """机构式仓位规则的三条硬约束：**不加杠杆、有下限、波动越大仓位越低**。"""
    vols = {"2020-01-01": 0.30, "2020-01-02": 0.15, "2020-01-03": 0.05, "2020-01-04": 5.0}
    exposure = vol_target_exposure(vols, target_vol=0.15, min_exposure=0.10)
    assert abs(exposure["2020-01-01"] - 0.5) < 1e-12, "目标 15% / 实现 30% → 应恰好半仓"
    assert abs(exposure["2020-01-02"] - 1.0) < 1e-12, "实现波动等于目标 → 满仓"
    assert abs(exposure["2020-01-03"] - 1.0) < 1e-12, \
        "市场波动低于目标时也只能满仓——这是长多研究工具，**不许加杠杆**"
    assert abs(exposure["2020-01-04"] - 0.10) < 1e-12, "极端波动时降到下限"
    assert all(0.0 < value <= 1.0 for value in exposure.values()), "仓位必须落在 (0, 1]"


def test_vol_target_exposure_rejects_a_zero_floor():
    """下限必须 > 0：**"降到空仓"是被实测否决过的做法**（全进全出会拉长回撤期）。"""
    import pytest

    with pytest.raises(ValueError):
        vol_target_exposure({"2020-01-01": 0.3}, target_vol=0.15, min_exposure=0.0)


def test_realized_vol_has_no_look_ahead_and_skips_short_history():
    """第 d 天的波动率只能用到第 d 天为止的收益。

    做法：先在完整序列上算一遍，再在**截断到第 80 天**的序列上算一遍，
    前 80 天的值必须逐日相同——任何前视都会让两者不一致。
    另外，样本不足 `lookback_days` 的日子**不出现在结果里**（不补 0、不猜）。
    """
    returns = {f"2020-{1 + index // 28:02d}-{1 + index % 28:02d}":
               0.01 if index % 3 else -0.008 for index in range(200)}
    full = realized_vol_series(returns, lookback_days=60)
    days = sorted(returns)
    assert days[0] not in full, "首个交易日没有 60 天历史，不该给出波动率"
    truncated = realized_vol_series({day: returns[day] for day in days[:80]}, lookback_days=60)
    assert set(truncated) == set(days[59:80])
    for day in truncated:
        assert truncated[day] == full[day], f"{day} 的波动率随未来数据变化 → 存在前视"


def test_portfolio_vol_target_scales_total_exposure_and_keeps_cash():
    """波动率目标在**组合层**整体缩放总仓位：仓位减半、其余进现金，选股不变。"""
    targets = [PortfolioTarget(thscode=f"60000{index}.SH", name=f"标的{index}",
                               industry="未分类", bars=bars_from(_trending_up()))
               for index in range(6)]
    common = {"strategy_ids": ["trend_20_60_v1"], "one_way_cost_bps": 10, "max_positions": 6,
              "max_single_weight": 0.15, "max_industry_weight": 1.0}
    plain = run_portfolio_backtest(targets, **common)
    scaled_days = {point["period"]: 0.5 for point in plain["portfolio"]["curve"]}
    scaled = run_portfolio_backtest(targets, vol_exposure=scaled_days, **common)

    assert all(point["exposure"] == 1.0 for point in plain["portfolio"]["curve"]), \
        "不传 vol_exposure 时不得改变任何行为（否则对照失效）"
    traded = [point for point in scaled["portfolio"]["curve"] if point["exposure"] == 0.5]
    assert traded, "缩放的交易日一个都没有，测试本身失效"
    for point in traded:
        assert point["invested_weight"] <= 0.5 + 1e-9, \
            f"{point['period']} 仓位 {point['invested_weight']} 超过了 0.5 的缩放上限"
    # 净值必须更低（仓位减半 + 成本），否则说明缩放根本没生效
    assert scaled["portfolio"]["metrics"]["final_net_value"] < \
        plain["portfolio"]["metrics"]["final_net_value"]
    stats = scaled["risk_stats"]
    assert stats["vol_target_scaled_days"] > 0
    assert 0 < stats["vol_target_min_exposure_pct"] <= stats["vol_target_avg_exposure_pct"] <= 100


def test_constant_exposure_cannot_change_drawdown_duration_but_time_varying_can():
    """**上一轮那个错误结论的结构守卫**（见 25 号文档 §8）。

    我先前量到"仓位 1.0/0.7/0.5/0.3/0.1 的回撤持续都是 1876 天"，据此断言
    "降仓位只能改深度、改不了时长"。真相是**常数暴露下这是恒等式**：

        净值(e) = e × 满仓净值 + (1 − e)

    正仿射变换不移动峰值位置，也不移动"重新超过前高"的日期——所以时长必然逐档相同，
    那个探针**结构上就不可能测出差异**。而**时变**暴露不受此限（波动率目标就是时变的），
    实测确实把最长回撤期从 730 天压到 417 天。两条都要钉住，否则以后还会再犯同一个错。
    """
    values = [1.0 * (1.004 ** index) for index in range(160)]
    values += [values[-1] * (0.995 ** k) for k in range(1, 140)]
    bottom = values[-1]
    values += [bottom * (1.002 ** k) for k in range(1, 240)]
    curve = _curve(values)

    def duration(items: list[dict]) -> int:
        return evaluate_acceptance(items, label="探针")["metrics"]["max_drawdown_duration_days"]

    baseline = duration(curve)
    for exposure in (0.7, 0.5, 0.3, 0.1):
        scaled = _curve([exposure * value + (1 - exposure) for value in values])
        assert duration(scaled) == baseline, \
            f"常数暴露 {exposure} 改变了回撤持续天数——仿射恒等式被打破了"
        assert min(item["net_value"] for item in scaled) > min(
            item["net_value"] for item in curve), "常数降仓必须让**深度**变浅"

    # 时变暴露：只在深跌那一段（第 160–300 天）减半，其余满仓。
    # 深度被削掉的是**下跌段**，要爬回来的距离因此变短 → 回撤期应当更短。
    equity = 1.0
    varying: list[float] = []
    for index, value in enumerate(values):
        gross = value / values[index - 1] - 1 if index else 0.0
        exposure = 0.5 if 160 <= index < 300 else 1.0
        equity *= 1 + exposure * gross
        varying.append(equity)
    assert duration(_curve(varying)) < baseline, \
        "时变暴露（只在下跌段降仓）没能缩短回撤期——与 25 号文档 §8 的实测结论矛盾"


def test_vol_target_profiles_are_declared_and_coherent():
    """两档波动目标必须同时存在：只看一档无法判断是规则生效还是系数碰巧。

    单调性由 `目标越低 → 仓位越低` 直接锁死，避免事后挑一个好看的系数。
    """
    from backend.app.strategy.registry import get_risk_profile as get_profile

    low = get_profile("risk_voltarget_low_v1")
    high = get_profile("risk_voltarget_v1")
    assert low.target_vol_pct < high.target_vol_pct
    assert low.vol_lookback_days == high.vol_lookback_days, \
        "两档若连回看窗口都不同，比出来的差异就分不清是目标值还是窗口造成的"
    assert low.min_exposure >= high.min_exposure, \
        "目标更低的一档必须要求**更高的仓位下限**：两者连起来才是'更保守'（仓位更低但不清仓）"
    assert 0 < high.min_exposure <= 1.0
    for profile in list_risk_profiles():
        assert 0 < profile.min_exposure <= 1.0
