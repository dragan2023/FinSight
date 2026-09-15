"""组合构建层的守卫测试（`26` 号文档）。

这一层做的事是"把风险预算分好，再让钱跟着风险走"。它有两个**错了也不报错**的风险：

1. **前视**：风险估计如果用了决策日之后的收益，回测会变好看而完全不可复现。
   守卫是 `test_return_window_strictly_excludes_the_decision_day`。
2. **ERC 名不副实**：代码可以收敛出一个"看起来像"的解，但风险贡献根本没摊平。
   守卫是 `test_equal_risk_actually_equalizes_risk_contributions`——它不检查权重，
   只检查**风险贡献**，因为权重相等 ≠ 风险相等（这正是整层存在的理由）。

另外两条钉住约束的施加顺序（单票上限、行业上限、换手带都不能偷偷改变总仓位）。
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from backend.app.portfolio.backtest import PortfolioTarget, run_portfolio_backtest
from backend.app.portfolio.construction import (
    MIN_RETURN_SAMPLES,
    ConstructionPolicy,
    build_return_series,
    equal_risk_weights,
    inverse_vol_weights,
    risk_contributions,
    shrink_covariance,
    target_weights,
    trailing_returns,
)
from backend.app.strategy.models import PriceBar

START = date(2020, 1, 1)


def _bars(closes: list[float]) -> list[PriceBar]:
    return [PriceBar(date=(START + timedelta(days=index)).isoformat(), open=value, close=value,
                     low=value, high=value, turnover=5.0e8)
            for index, value in enumerate(closes)]


def _series(count: int, *, sigma: float, seed: int, drift: float = 0.0) -> list[float]:
    rng = random.Random(seed)
    value = 10.0
    out = [value]
    for _ in range(count - 1):
        value *= 1 + drift + rng.gauss(0, sigma)
        out.append(value)
    return out


# --- 1. 无前视 ---------------------------------------------------------------

def test_return_window_strictly_excludes_the_decision_day():
    """风险估计只能用**严格早于决策日**的收益（与信号同一套"次日开盘执行"口径）。

    口径：评估日 `day` 的意思是"在 `day` 开盘下单"，此时已知的是 `day-1` 收盘及以前。
    所以 `day` **当天**的收益必须被排除——改动 `day` 当天的收盘价不得影响窗口。
    """
    bars = _bars([10.0, 10.1, 10.2, 10.3, 10.4])
    series = build_return_series(bars)
    assert series.dates == [bars[index].date for index in (1, 2, 3, 4)]

    decision_day = bars[4].date
    window = series.window(decision_day, 10)
    # 决策日是第 5 根，可用的只有前 3 根（10.1/10.0、10.2/10.1、10.3/10.2）；10.4/10.3 不算
    assert len(window) == 3, f"窗口里混进了决策日当天的收益：{window}"

    # 改动**决策日当天**的价格 → 窗口必须不变（这是"没有前视"的直接证明）
    future = _bars([10.0, 10.1, 10.2, 10.3, 99.0])
    assert build_return_series(future).window(decision_day, 10) == window
    # 反向对照：改动**决策日前一天**的价格 → 窗口**必须**变（否则说明窗口取错了区间）
    earlier = _bars([10.0, 10.1, 10.2, 88.0, 10.4])
    assert build_return_series(earlier).window(decision_day, 10) != window

    assert trailing_returns(bars, decision_day, lookback_days=2) == window[-2:]


# --- 2. ERC 必须真的摊平风险贡献 ---------------------------------------------

def test_equal_risk_actually_equalizes_risk_contributions():
    """**本层最重要的一条**：ERC 的结果必须让各标的的风险贡献接近相等。

    权重故意不等（三只标的波动率差了 5 倍），因为"权重相等"根本不需要算法。
    检查的是 `risk_contributions` 的输出，不是权重——权重看起来像不代表风险真摊平了。
    """
    returns = {
        "low_vol": _series(300, sigma=0.008, seed=1),
        "mid_vol": _series(300, sigma=0.020, seed=2),
        "high_vol": _series(300, sigma=0.045, seed=3),
    }
    windows = {code: values[-200:] for code, values in returns.items()}
    codes, matrix = shrink_covariance(windows, shrinkage=0.0)   # 不收缩，纯样本协方差
    assert len(codes) == 3

    erc = equal_risk_weights(codes, matrix)
    contributions = risk_contributions(erc, windows, shrinkage=0.0)
    assert len(contributions) == 3
    spread = max(contributions.values()) - min(contributions.values())
    assert spread < 0.02, f"ERC 的风险贡献没有摊平：{contributions}"
    # 权重必须真的不等——否则说明它退化成了等权（那就不是 ERC 了）
    weights = sorted(erc.values())
    assert weights[-1] / weights[0] > 1.5, f"权重不该相等：{erc}"
    assert abs(sum(contributions.values()) - 1.0) < 1e-9

    # 对照：等权方案的贡献**不**相等（否则这条测试没有区分度）
    equal = {code: 1 / 3 for code in codes}
    equal_spread = max(risk_contributions(equal, windows, shrinkage=0.0).values()) - min(
        risk_contributions(equal, windows, shrinkage=0.0).values())
    assert equal_spread > spread * 3, "等权的分散度没有明显更差，测试本身失效"


def test_equal_risk_beats_inverse_vol_only_with_correlation():
    """`inverse_vol` 与 `equal_risk` 是两个不同的方案：前者忽略相关性。

    构造两只**高相关但波动率差很大**的标的：只看各自波动率会把风险集中到相关性里，
    ERC 应当把贡献摊得更平。这条也解释了为什么两个方案都要保留——
    如果两者结果一样，就说明协方差的非对角项没有信息量，不该维护更复杂的代码。
    """
    rng = random.Random(7)
    common = [rng.gauss(0, 0.02) for _ in range(400)]
    a = [0.0]
    b = [0.0]
    for value in common:
        a.append(a[-1] + value + rng.gauss(0, 0.001))          # 与 common 几乎同步
        b.append(b[-1] + value + rng.gauss(0, 0.012))          # 同向但自身噪声大得多
    windows = {"a": a[-250:], "b": b[-250:]}
    codes, matrix = shrink_covariance(windows, shrinkage=0.0)

    erc_spread = max(risk_contributions(
        equal_risk_weights(codes, matrix), windows, shrinkage=0.0).values()) - min(
        risk_contributions(equal_risk_weights(codes, matrix), windows, shrinkage=0.0).values())
    ivol = inverse_vol_weights(codes, matrix)
    ivol_spread = max(risk_contributions(ivol, windows, shrinkage=0.0).values()) - min(
        risk_contributions(ivol, windows, shrinkage=0.0).values())
    assert erc_spread <= ivol_spread + 1e-9, \
        f"ERC（{erc_spread:.4f}）没有比只看波动率（{ivol_spread:.4f}）更平"


# --- 3. 约束：上限与换手带不得偷偷改变总仓位 ---------------------------------

def test_caps_hold_and_do_not_silently_shrink_the_book():
    """**能力上限约束不能被"向上归一化"冲掉**——这是第一版的真实 bug。

    第一版末尾无条件 `_normalize`，于是 6 只票、单票上限 20% 的场景下权重被拉到
    33% × 3 —— 上限形同虚设。正确语义是：**约束不可行时只投得少，不能投得多**。
    """
    candidates = [{"thscode": f"60000{i}.SH", "score": 1, "industry": "A" if i < 3 else "B"}
                  for i in range(6)]
    industries = {item["thscode"]: item["industry"] for item in candidates}
    total_cap = 0.95

    # 可行场景：两个行业各上限 60%，容量 1.2 ≥ 0.95 → 应当投满到总仓位上限
    feasible = ConstructionPolicy(scheme="rank_equal", max_positions=6, max_single_weight=0.20,
                                  max_industry_weight=0.60, max_total_exposure=total_cap)
    weights = target_weights(candidates, policy=feasible, returns_by_code={},
                             industries=industries)
    assert sum(weights.values()) == pytest.approx(total_cap, abs=1e-9), \
        f"可行场景没有投满：{sum(weights.values())}"
    assert max(weights.values()) <= 0.20 + 1e-9, "单票上限被突破"
    for group in ("A", "B"):
        used = sum(value for code, value in weights.items() if industries[code] == group)
        assert used <= 0.60 + 1e-9, f"行业 {group} 上限被突破：{used}"

    # 不可行场景：两个行业各上限 30%，容量只有 0.60 < 0.95
    # → 必须**只投 0.60**，绝不能拉到 0.95
    tight = ConstructionPolicy(scheme="rank_equal", max_positions=6, max_single_weight=0.20,
                               max_industry_weight=0.30, max_total_exposure=total_cap)
    tight_weights = target_weights(candidates, policy=tight, returns_by_code={},
                                   industries=industries)
    assert sum(tight_weights.values()) == pytest.approx(0.60, abs=1e-6), \
        f"不可行约束被硬凑：{sum(tight_weights.values())}"
    for group in ("A", "B"):
        used = sum(value for code, value in tight_weights.items() if industries[code] == group)
        assert used <= 0.30 + 1e-9, f"行业 {group} 上限被突破：{used}"


def test_unknown_industry_must_not_act_as_a_total_position_cap():
    """**2026-09-15 实测踩到的 bug 的结构守卫**。

    验收脚本把 20 只标的的行业全部写成 `未分类`。如果给这个标签套用行业上限，
    `max_industry_weight=0.30` 就会让**总仓位只剩 30%**（3 只 × 10%），
    而对照的"等权买入持有"是满仓——对比从一开始就不公平，且报告里看不出来。
    规则：**未知行业不参与行业约束**（返回 None），而不是被当成一个行业。
    """
    from backend.app.portfolio.backtest import allocate_candidates
    from backend.app.portfolio.construction import industry_key

    assert industry_key("未分类") is None
    assert industry_key("") is None
    assert industry_key(None) is None
    assert industry_key("银行") == "银行"

    names = [{"thscode": f"60000{i}.SH", "score": 1, "industry": "未分类"} for i in range(6)]
    weights = allocate_candidates(names, max_positions=10, max_single_weight=0.10,
                                 max_industry_weight=0.30, max_total_exposure=0.95)
    assert sum(weights.values()) == pytest.approx(0.60, abs=1e-9), \
        f"未知行业仍然把总仓位压住了：{sum(weights.values())}（应当只受单票上限 0.10×6 约束）"

    # 已知行业时，行业上限必须重新生效
    known = [{"thscode": f"60000{i}.SH", "score": 1, "industry": "银行"} for i in range(6)]
    known_weights = allocate_candidates(known, max_positions=10, max_single_weight=0.10,
                                        max_industry_weight=0.30, max_total_exposure=0.95)
    assert sum(known_weights.values()) == pytest.approx(0.30, abs=1e-9)


def test_turnover_band_suppresses_small_changes_without_changing_total():
    """换手带：小幅变化不调仓；但总暴露必须与不带时**完全一致**。"""
    candidates = [{"thscode": f"60000{i}.SH", "score": 1, "industry": "银行"} for i in range(4)]
    industries = {item["thscode"]: "银行" for item in candidates}
    base = ConstructionPolicy(scheme="rank_equal", max_positions=4, max_single_weight=0.25,
                              max_industry_weight=1.0, max_total_exposure=0.95)
    banded = ConstructionPolicy(scheme="rank_equal", max_positions=4, max_single_weight=0.25,
                                max_industry_weight=1.0, max_total_exposure=0.95,
                                turnover_band=0.05)
    plain = target_weights(candidates, policy=base, returns_by_code={}, industries=industries)
    assert sum(plain.values()) == pytest.approx(0.95, abs=1e-9)

    # 当前权重与目标**总额相同**，只在两只之间挪了 4%（小于 5% 带宽）
    codes = sorted(plain)
    current = dict(plain)
    current[codes[0]] += 0.04
    current[codes[1]] -= 0.04
    kept = target_weights(candidates, policy=banded, returns_by_code={}, industries=industries,
                          current_weights=current)
    assert sum(kept.values()) == pytest.approx(sum(plain.values()), abs=1e-9), \
        "换手带偷偷改变了总仓位"
    for code, value in kept.items():
        assert abs(value - current[code]) < 1e-6, f"{code} 的小幅变化没有被带内保留"

    # 对照：带宽收到 1% 之后，同样的 4% 偏差必须被纠正回目标
    tight = ConstructionPolicy(scheme="rank_equal", max_positions=4, max_single_weight=0.25,
                               max_industry_weight=1.0, max_total_exposure=0.95,
                               turnover_band=0.01)
    corrected = target_weights(candidates, policy=tight, returns_by_code={}, industries=industries,
                               current_weights=current)
    assert abs(corrected[codes[0]] - plain[codes[0]]) < 1e-6, "带宽之外的偏差没有被纠正"


def test_exposure_is_reported_because_it_explains_returns():
    """**仓位必须显式报出**：否则"收益低"会被误判成信号不行，而原因往往是钱没投出去。"""
    targets = [PortfolioTarget(thscode=f"60000{index}.SH", name=f"标的{index}", industry="未分类",
                               bars=_bars(_series(400, sigma=0.02, seed=index)))
               for index in range(4)]
    outcome = run_portfolio_backtest(
        targets, strategy_ids=["trend_20_60_v1"], one_way_cost_bps=10, max_positions=4,
        max_single_weight=0.25, max_industry_weight=0.30, max_total_exposure=0.95)
    metrics = outcome["portfolio"]["metrics"]
    for key in ("avg_invested_weight_pct", "min_invested_weight_pct", "max_invested_weight_pct"):
        assert key in metrics, f"缺少仓位统计 {key} → 无法解释收益来源"
    assert metrics["max_invested_weight_pct"] <= 95.0 + 1e-6, \
        "实际仓位超过了显式总仓位上限 95%"


def test_policy_rejects_a_short_risk_window():
    """窗口太短必须直接拒绝：拿两三天数据估波动率比不估更危险。"""
    with pytest.raises(ValueError):
        ConstructionPolicy(scheme="equal_risk", lookback_days=MIN_RETURN_SAMPLES - 1)


# --- 3.5 股票池的上市时间参差不齐（并集日历 + 停牌处理） ----------------------

def _offset_bars(closes: list[float], *, skip_days: set[int]) -> list[PriceBar]:
    """构造缺 K 线的序列：`skip_days` 里的下标直接不出现在数据里（模拟停牌/晚上市）。"""
    return [PriceBar(date=(START + timedelta(days=index)).isoformat(), open=value, close=value,
                     low=value, high=value, turnover=5.0e8)
            for index, value in enumerate(closes) if index not in skip_days]


def test_universe_size_must_not_shrink_the_sample_period():
    """**2026-09-15 实测发现的第二个结构性缺陷的守卫**。

    原先交易日取的是所有标的的**交集**，后果是"股票池越大、可用历史越短"：
    50 只标的里只要有几只 2023 年才上市，整段回测就被压到 2023 年以后
    （实测 top-50 只剩 591 个交易日，年化被算成 75%）。
    现在必须用**并集**：一只有 400 根 K 线的老标的 + 一只只有 120 根 K 线的新标的，
    评价期应当接近 400 根，而不是 120 根。
    """
    long_bars = _bars(_series(400, sigma=0.01, seed=1, drift=0.003))
    short_bars = _offset_bars(_series(400, sigma=0.01, seed=2, drift=0.003),
                              skip_days=set(range(0, 280)))       # 相当于晚 280 天上市
    assert len(short_bars) == 120
    targets = [PortfolioTarget(thscode="600000.SH", name="老票", industry="未分类", bars=long_bars),
               PortfolioTarget(thscode="600001.SH", name="新票", industry="未分类", bars=short_bars)]
    outcome = run_portfolio_backtest(
        targets, strategy_ids=["trend_20_60_v1"], one_way_cost_bps=10, max_positions=2,
        max_single_weight=0.45, max_industry_weight=0.30, max_total_exposure=0.90)
    days = outcome["date_range"]["common_trading_days"]
    assert days > 350, f"可用交易日被最短的那只标的压住了：{days}（应当接近 400）"
    # 新标的上市之前，老标的是唯一的可投对象；组合净值必须连续、没有跳变
    values = [point["net_value"] for point in outcome["portfolio"]["curve"]]
    assert all(value > 0 for value in values)
    jumps = [abs(values[index] / values[index - 1] - 1) for index in range(1, len(values))]
    assert max(jumps) < 0.15, f"净值出现异常跳变（可能是停牌/晚上市被按不存在的价格结算）：{max(jumps)}"


def test_suspended_symbol_is_not_traded_at_a_nonexistent_price():
    """停牌（当天无 K 线）时既不能买也不能卖，持仓市值保持不变。"""
    closes = _series(400, sigma=0.01, seed=5, drift=0.003)
    suspended = set(range(300, 320))
    targets = [PortfolioTarget(thscode="600000.SH", name="正常", industry="未分类",
                               bars=_bars(closes)),
               PortfolioTarget(thscode="600001.SH", name="中途停牌", industry="未分类",
                               bars=_offset_bars(closes, skip_days=suspended))]
    outcome = run_portfolio_backtest(
        targets, strategy_ids=["trend_20_60_v1"], one_way_cost_bps=10, max_positions=2,
        max_single_weight=0.45, max_industry_weight=0.30, max_total_exposure=0.90)
    suspended_days = {(START + timedelta(days=index)).isoformat() for index in suspended}
    traded = {trade["date"] for trade in outcome["portfolio"]["trades"]
              if trade["thscode"] == "600001.SH"}
    assert not (traded & suspended_days), \
        f"停牌日仍然发生了成交：{sorted(traded & suspended_days)[:5]}"


# --- 4. 端到端：ERC 在组合层真的生效，且不传时保持历史行为 -------------------

def test_portfolio_construction_changes_weights_and_reports_audit():
    # 用**单调上行**的行情，保证每只票都在趋势信号内 → 组合里始终有多只持仓，
    # 否则风险贡献只剩一两只，测不出"摊平"这件事。
    targets = [PortfolioTarget(thscode=f"60000{index}.SH", name=f"标的{index}",
                               industry="未分类",
                               bars=_bars(_series(400, sigma=0.006 + 0.008 * index, seed=index,
                                                  drift=0.004)))
               for index in range(6)]
    common = {"strategy_ids": ["trend_20_60_v1"], "one_way_cost_bps": 10, "max_positions": 6,
              "max_single_weight": 0.20, "max_industry_weight": 0.30, "max_total_exposure": 0.95}
    legacy = run_portfolio_backtest(targets, **common)
    assert legacy["construction"] is None, "不传 construction 时不得产生任何构建审计"

    policy = ConstructionPolicy(scheme="equal_risk", max_positions=6, max_single_weight=0.20,
                                max_industry_weight=0.30, max_total_exposure=0.95,
                                lookback_days=120)
    built = run_portfolio_backtest(targets, construction=policy, **common)
    audit = built["construction"]
    assert audit["scheme"] == "equal_risk"
    assert len(audit["final_weights"]) >= 2, "组合里持仓太少，测试本身失效"
    assert audit["final_risk_contributions"], "没有报出实际风险贡献 → 不可审计"
    assert built["portfolio"]["metrics"]["trade_count"] > 0
    # 不传 construction 时不得产生构建审计字段 → 保证新旧对比只有一个变量在动
    assert "final_risk_contributions" not in (legacy["construction"] or {})

    # **诚实的判据**：单票上限 20% 会盖住 ERC 想要的极端权重（低波动标的想拿超过 20%），
    # 所以 ERC 在**有上限**时摊不平风险贡献——这不是 bug，是"上限优先于优化"。
    # 因此这里不比"绝对值小"，而是比"同样的约束下谁更平"：
    equal_policy = ConstructionPolicy(scheme="rank_equal", max_positions=6, max_single_weight=0.20,
                                      max_industry_weight=0.30, max_total_exposure=0.95)
    equal_built = run_portfolio_backtest(targets, construction=equal_policy, **common)

    def spread(outcome: dict) -> float:
        values = outcome["construction"]["final_risk_contributions"].values()
        return max(values) - min(values)

    assert spread(built) <= spread(equal_built) + 1e-9, (
        f"同样约束下 ERC 的风险贡献比等权还集中："
        f"{spread(built):.4f} vs {spread(equal_built):.4f}")


def test_equal_risk_weights_are_long_only_and_sum_to_one():
    """长期多约束自动满足（正根必为正）——但仍要钉住，防止以后改成别的解法。"""
    windows = {"a": _series(200, sigma=0.01, seed=11), "b": _series(200, sigma=0.03, seed=12),
               "c": _series(200, sigma=0.02, seed=13)}
    codes, matrix = shrink_covariance(windows, shrinkage=0.5)
    weights = equal_risk_weights(codes, matrix)
    assert all(value > 0 for value in weights.values())
    assert math.isclose(sum(weights.values()), 1.0, rel_tol=1e-9)
