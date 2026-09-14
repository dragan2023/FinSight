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


def test_risk_stats_are_reported_so_the_layer_is_auditable():
    """统计必须报出来：否则无法回答"风控到底拦了什么"（等于不可审计）。"""
    bars = bars_from(_trending_up())
    result = run_backtest("trend_20_60_v1", bars, 10,
                          risk=get_risk_profile("risk_conservative_v1"))
    for key in ("entries", "stop_exits", "blocked_entries_limit_up",
                "blocked_exits_limit_down", "skipped_entries_liquidity",
                "skipped_entries_missing_turnover", "skipped_entries_cooldown"):
        assert key in result["risk_stats"], f"缺少风控统计 {key}"
    assert result["risk_profile"] == "risk_conservative_v1"


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
    """验收阈值是用户 2026-09-15 确认的，不能悄悄改。"""
    thresholds = AcceptanceThresholds()
    assert thresholds.max_drawdown_pct == 20.0
    assert thresholds.max_worst_year_loss_pct == 10.0
    assert thresholds.max_drawdown_duration_days == 365
    assert thresholds.require_positive_expectancy is True
