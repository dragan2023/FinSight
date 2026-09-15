"""决策节奏 + 时点股票池的守卫测试（`27` 号文档）。

这两层的作用都是**让前面那些数字变得可信、可执行**，所以它们的错误同样是"不报错、
只是让数字变形"的类型：

1. **决策日算错** → 换手降不下来（打得过算我输），或者干脆不交易。
   守卫：`test_monthly_decision_days_are_the_last_trading_day_of_each_month`。
2. **非决策日偷偷换股** → 换手没降、还破坏了"只减不增"的语义。
   守卫：`test_between_decisions_only_removals_are_allowed`。
3. **时点池子前视** → 用后面的排名回头改前面的池子，正是要修的那个偏差本身。
   守卫：`test_universe_uses_only_information_up_to_the_decision_day`。
4. **池子铺错**（每天重排） → 引入与策略无关的换手。
   守卫：`test_eligibility_holds_the_pool_until_the_next_decision`。
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.app.portfolio.backtest import PortfolioTarget, run_portfolio_backtest
from backend.app.portfolio.schedule import (
    day_to_ms,
    every_nth_decision_days,
    monthly_decision_days,
)
from backend.app.portfolio.universe import (
    describe,
    eligibility_series,
    liquidity_universe,
    union_symbols,
)
from backend.app.strategy.models import PriceBar

START = date(2020, 1, 1)


def _trading_days(count: int) -> list[str]:
    """连续自然日充当交易日（简化；本模块只看日期字符串的顺序与月份）。"""
    return [(START + timedelta(days=index)).isoformat() for index in range(count)]


def _bars(closes: list[float], *, start: date = START) -> list[PriceBar]:
    return [PriceBar(date=(start + timedelta(days=index)).isoformat(), open=value, close=value,
                     low=value, high=value, turnover=5.0e8)
            for index, value in enumerate(closes)]


class _FakeRepository:
    """最小可用的假仓库：按日期返回不同的横截面排名，用来验证"没有前视"。"""

    def __init__(self, rankings: dict[int, list[str]]) -> None:
        self.rankings = rankings
        self.calls: list[int] = []

    def cross_section(self, *, date_ms: int, history_days: int = 400) -> list[dict]:
        self.calls.append(date_ms)
        codes = self.rankings.get(date_ms, [])
        # turnover_20 递减 → 排名就是传入的顺序
        return [{"thscode": code, "turnover_20": float(len(codes) - index)}
                for index, code in enumerate(codes)]


# --- 1. 决策日 ---------------------------------------------------------------

def test_monthly_decision_days_are_the_last_trading_day_of_each_month():
    days = ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-28", "2020-03-02", "2020-03-31"]
    decisions = monthly_decision_days(days)
    assert decisions == {"2020-01-31", "2020-02-28", "2020-03-31"}, decisions
    assert monthly_decision_days([]) == set()
    # 每个月只有一个决策日，且一定落在交易日集合里
    assert decisions <= set(days)
    assert len(every_nth_decision_days(days, every=2)) == 3
    assert every_nth_decision_days(days, every=1) == set(days)


def test_decision_day_ms_is_a_pure_date_conversion():
    """`day_to_ms` 只做日期换算；**它不是 as-of 时点**（后者见下一条测试）。"""
    start = day_to_ms("2020-01-31")
    assert day_to_ms("2020-02-01") - start == 86_400_000


# --- 2. 非决策日只减不增 -----------------------------------------------------

def _uptrend(count: int, *, step: float = 0.004) -> list[float]:
    return [10.0 * (1 + step) ** index for index in range(count)]


def test_between_decisions_only_removals_are_allowed():
    """非决策日**不得**买入新标的，也不得换股；只能把信号消失的卖掉。"""
    targets = [PortfolioTarget(thscode=f"60000{index}.SH", name=f"标的{index}",
                               industry="未分类", bars=_bars(_uptrend(400)))
               for index in range(4)]
    common = {"strategy_ids": ["trend_20_60_v1"], "one_way_cost_bps": 10, "max_positions": 4,
              "max_single_weight": 0.22, "max_industry_weight": 0.30, "max_total_exposure": 0.88}
    daily = run_portfolio_backtest(targets, **common)

    trading = [point["period"] for point in daily["portfolio"]["curve"]]
    decisions = monthly_decision_days(trading)
    scheduled = run_portfolio_backtest(targets, decision_days=decisions, **common)

    stats = scheduled["risk_stats"]
    assert stats["schedule_decision_days"] == len(decisions)
    assert stats["schedule_hold_days"] == len(trading) - len(decisions)
    # 非决策日里"有持仓可沿用"的天数应当占多数（预热期与第一个决策日之前没有持仓，
    # 那几天 carry 为 0 是正常的——策略还没信号，也就没有"沿用"可言）
    assert 0 < stats["schedule_carry_days"] <= stats["schedule_hold_days"]
    assert stats["schedule_carry_days"] > stats["schedule_hold_days"] * 0.5

    # **买入只能发生在决策日**——这是"只减不增"的直接检验，与行情形状无关
    buy_days = {trade["date"] for trade in scheduled["portfolio"]["trades"]
                if trade["target_weight"] > trade["before_weight"] + 1e-9}
    assert buy_days, "整个回测一次都没买过，测试本身失效"
    assert buy_days <= decisions, f"非决策日发生了买入：{sorted(buy_days - decisions)[:5]}"

    # 换手不得变差（真正的幅度对比在真实数据上看，见 27 号文档）
    daily_turnover = daily["portfolio"]["metrics"]["annualized_turnover_pct"]
    scheduled_turnover = scheduled["portfolio"]["metrics"]["annualized_turnover_pct"]
    assert scheduled_turnover <= daily_turnover + 1e-9

    # 非决策日不得出现"买入新标的"：持仓集合只会缩小或不变
    per_day = {}
    for trade in scheduled["portfolio"]["trades"]:
        per_day.setdefault(trade["date"], []).append(trade)
    for day, items in per_day.items():
        if day in decisions:
            continue
        for trade in items:
            assert trade["target_weight"] <= trade["before_weight"] + 1e-9, \
                f"非决策日 {day} 加仓了 {trade['thscode']}"


def test_monthly_cadence_reduces_turnover_when_signals_flicker():
    """**这一层真正解决的问题**：信号反复开关时，日度决策会来回买卖，月度决策不会。

    用一条振荡行情（价格反复穿过均线）制造"信号闪烁"。这时：
    - 日度决策：信号一亮就买、一灭就卖，一个月来回好几次；
    - 月度决策：只有月末能买，灭掉之后必须等到下个月末才能重新进。
    """
    import math

    count = 900
    closes = [10.0 * (1 + 0.18 * math.sin(2 * math.pi * index / 45)) for index in range(count)]
    targets = [PortfolioTarget(thscode=f"6000{index:02d}.SH", name=f"标的{index}",
                               industry="未分类", bars=_bars(closes))
               for index in range(4)]
    common = {"strategy_ids": ["trend_20_60_v1"], "one_way_cost_bps": 10, "max_positions": 4,
              "max_single_weight": 0.22, "max_industry_weight": 0.30, "max_total_exposure": 0.88}
    daily = run_portfolio_backtest(targets, **common)
    trading = [point["period"] for point in daily["portfolio"]["curve"]]
    decisions = monthly_decision_days(trading)
    scheduled = run_portfolio_backtest(targets, decision_days=decisions, **common)

    daily_trades = len(daily["portfolio"]["trades"])
    scheduled_trades = len(scheduled["portfolio"]["trades"])
    assert daily_trades > 50, f"振荡行情没有制造出足够多的交易，测试本身失效：{daily_trades}"
    assert scheduled_trades < daily_trades * 0.75, \
        f"月度决策没有显著减少交易笔数：{daily_trades} → {scheduled_trades}"


def test_daily_behaviour_is_unchanged_when_no_schedule_is_given():
    """不传 `decision_days` 时必须与历史**逐位相同**（新旧对比只能有一个变量）。"""
    targets = [PortfolioTarget(thscode="600000.SH", name="唯一", industry="未分类",
                               bars=_bars(_uptrend(400)))]
    common = {"strategy_ids": ["trend_20_60_v1"], "one_way_cost_bps": 10, "max_positions": 1,
              "max_single_weight": 0.90, "max_industry_weight": 0.30, "max_total_exposure": 0.88}
    plain = run_portfolio_backtest(targets, **common)
    explicit_none = run_portfolio_backtest(targets, decision_days=None, **common)
    assert plain["portfolio"]["curve"] == explicit_none["portfolio"]["curve"]
    assert all(point["decision_day"] for point in plain["portfolio"]["curve"])
    assert plain["risk_stats"]["schedule_hold_days"] == 0


# --- 3. 时点股票池：不得前视 -------------------------------------------------

def test_universe_uses_only_information_up_to_the_decision_day():
    """池子必须只用**截至决策日**的信息；改动之后的数据不得影响之前的池子。

    同时钉住一个**静默失败**的口径：as-of 时点必须用该交易日的**原始** `date_ms`。
    `cross_section` 要求"T 当天有成交"（`last_ms == T`），自己造一个"当日 23:59:59"
    会让接口返回空、股票池静默变成 0 只（2026-09-15 实测踩到）。
    """
    days = ["2020-01-31", "2020-02-28"]
    stamps = {day: day_to_ms(day) for day in days}       # 用真实交易日的原始时间戳
    repo = _FakeRepository({stamps["2020-01-31"]: ["600000.SH", "600001.SH"],
                            stamps["2020-02-28"]: ["600002.SH", "600003.SH"]})
    universe = liquidity_universe(repo, decision_days=stamps, top=2)
    assert universe["2020-01-31"] == ["600000.SH", "600001.SH"]
    assert universe["2020-02-28"] == ["600002.SH", "600003.SH"]
    assert repo.calls == [stamps["2020-01-31"], stamps["2020-02-28"]], \
        "每个决策日必须各查一次，且时点必须是该交易日的原始 date_ms"

    # 反向验证：把 2 月的排名改掉，1 月的池子必须一字不变
    repo2 = _FakeRepository({stamps["2020-01-31"]: ["600000.SH", "600001.SH"],
                             stamps["2020-02-28"]: ["600004.SH"]})
    assert liquidity_universe(repo2, decision_days=stamps, top=2)["2020-01-31"] == \
        universe["2020-01-31"]
    assert union_symbols(universe) == ["600000.SH", "600001.SH", "600002.SH", "600003.SH"]


def test_fabricated_as_of_timestamp_silently_empties_the_universe():
    """**回归守卫**：把 as-of 时点造在交易日中间（当日 00:00 或 23:59:59）会拿到空池子。

    这条不是在测"代码应该报错"，而是在记录**接口的真实行为**：它不报错，只返回空。
    所以调用方绝对不能自己换算 as-of 时点。
    """
    day = "2020-01-31"
    real_stamp = day_to_ms(day)
    repo = _FakeRepository({real_stamp: ["600000.SH"]})       # 只有真实时间戳有数据
    assert liquidity_universe(repo, decision_days={day: real_stamp}, top=1)[day] == ["600000.SH"]
    # 当日 00:00 与 23:59:59 都不是任何一个 date_ms → 静默空
    assert liquidity_universe(repo, decision_days={day: real_stamp - 1}, top=1)[day] == []
    assert liquidity_universe(repo, decision_days={day: real_stamp + 1}, top=1)[day] == []


def test_eligibility_holds_the_pool_until_the_next_decision():
    """池子沿用到下一个决策日之前——**不许每天重排**（那会引入与策略无关的换手）。"""
    universe = {"2020-01-31": ["A", "B"], "2020-02-28": ["B", "C"]}
    days = ["2020-01-30", "2020-01-31", "2020-02-03", "2020-02-27", "2020-02-28", "2020-03-02"]
    series = eligibility_series(universe, days)
    assert "2020-01-30" not in series, "第一个决策日之前没有池子可用"
    assert series["2020-01-31"] == {"A", "B"}
    assert series["2020-02-03"] == {"A", "B"}
    assert series["2020-02-27"] == {"A", "B"}
    assert series["2020-02-28"] == {"B", "C"}
    assert series["2020-03-02"] == {"B", "C"}


def test_eligibility_blocks_names_outside_the_point_in_time_pool():
    """不在当日池子里的标的不得持有（相当于指数调出成分股）。"""
    targets = [PortfolioTarget(thscode=f"60000{index}.SH", name=f"标的{index}",
                               industry="未分类", bars=_bars(_uptrend(400)))
               for index in range(3)]
    common = {"strategy_ids": ["trend_20_60_v1"], "one_way_cost_bps": 10, "max_positions": 3,
              "max_single_weight": 0.30, "max_industry_weight": 0.30, "max_total_exposure": 0.88}
    plain = run_portfolio_backtest(targets, **common)
    trading = [point["period"] for point in plain["portfolio"]["curve"]]
    # 只允许第一只标的
    eligible = {day: {"600000.SH"} for day in trading}
    limited = run_portfolio_backtest(targets, eligible=eligible, **common)
    held = {trade["thscode"] for trade in limited["portfolio"]["trades"]}
    assert held == {"600000.SH"}, f"池子外的标的被持有了：{held}"
    assert limited["risk_stats"]["eligible_blocked_days"] > 0
    # 不传 eligible 时行为不受影响
    assert run_portfolio_backtest(targets, eligible=None, **common)["portfolio"]["curve"] == \
        plain["portfolio"]["curve"]


def test_buy_hold_strategy_is_dispatched_and_actually_holds():
    """`buy_hold_v1` 必须走**持有不动**分支，不能掉进别的策略的 else 里。

    **这条守卫来自一次真实踩坑**：给 `run_backtest` 加 `buy_hold_v1` 分支时，
    编辑落到了同名的 `current_signal_snapshot` 上（缩进不同、内容一样），
    于是 `run_backtest` 里它仍然走 `else`（反转逻辑：持有 5 天就走），
    单标的覆盖率只有 **5.4%**。它不报错，只是让"不做择时的对照组"悄悄变成了
    "一个烂策略"，把结论引向完全错误的方向。守卫就是覆盖率。
    """
    from backend.app.strategy.backtest import run_backtest as single_backtest

    bars = _bars(_uptrend(400))
    held = single_backtest("buy_hold_v1", bars, 10)
    covered = sum(1 for point in held["curve"] if point.get("position"))
    assert covered >= len(bars) - 2, \
        f"buy_hold_v1 只覆盖 {covered}/{len(bars)} 天 —— 掉进了别的策略分支"
    # 其余两条策略的行为不得被这次改动影响
    for strategy in ("trend_20_60_v1", "reversal_5d_v1"):
        outcome = single_backtest(strategy, bars, 10)
        assert len(outcome["curve"]) == len(bars)


def test_universe_description_reports_stability():
    """池子的稳定性必须可复现地报出来（重合率低说明排名噪声大、换手会被池子推高）。"""
    universe = {"2020-01-31": ["A", "B", "C"], "2020-02-28": ["A", "B", "D"],
                "2020-03-31": ["A", "B", "D"]}
    info = describe(universe)
    assert info["decision_days"] == 3
    assert info["union_size"] == 4
    assert info["per_day_size"]["median"] == 3
    assert info["adjacent_overlap_median"] == pytest.approx((2 / 3 + 1.0) / 2, abs=1e-9)
    assert info["adjacent_overlap_min"] == pytest.approx(2 / 3, abs=1e-9)
