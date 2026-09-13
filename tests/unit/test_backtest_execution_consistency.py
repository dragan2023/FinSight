"""回测执行口径一致性守卫（F23 / 07 §4.3）。

缺陷原状：净值曲线在换手当日沿用 `close_i / close_{i-1}`，而 `trades[].return_pct`
按 `open → open` 计算——**同一笔交易有两个收益率**，且净值的开仓当日相当于按前收成交、
只按开盘价扣成本。

**为什么长期没被发现**：无跳空时 `open_i == close_{i-1}`，两套算法给出的结果**完全相同**。
所以这里的每条测试都必须构造**跳空**，否则测不出任何东西——最后一条测试专门把这件事
pin 住（无跳空时新旧口径一致），避免以后有人以为"没跳空也没差"是 bug。

修复后的不变式：**一笔交易期间的净值比，必须恰好等于该笔交易记录的收益率**。
"""

from __future__ import annotations

import pytest

from backend.app.strategy.backtest import run_backtest
from backend.app.strategy.models import PriceBar

STRATEGY = "trend_20_60_v1"
COST = 10  # bps
COSTS = COST / 10_000


def _bars(closes: list[float], *, gap_at: int | None = None, gap: float = 0.0,
          open_style: str = "prev_close") -> list[PriceBar]:
    """构造日 K。`gap_at` 处开盘价相对前收跳空 `gap`（正数=高开）。"""
    bars: list[PriceBar] = []
    for index, close in enumerate(closes):
        if index == 0:
            opening = close
        elif index == gap_at:
            opening = closes[index - 1] + gap
        else:
            opening = closes[index - 1]
        bars.append(PriceBar(date=f"d{index:03d}", open=opening, close=close))
    return bars


def _rising(count: int = 90, *, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * index for index in range(count)]


def _trade_span(result: dict) -> tuple[int, int]:
    curve = result["curve"]
    trade = result["trades"][0]
    entry = next(index for index, point in enumerate(curve)
                 if point["period"] == trade["entry_date"])
    exit_ = next(index for index, point in enumerate(curve)
                 if point["period"] == trade["exit_date"])
    return entry, exit_


def test_entry_bar_is_marked_at_the_open_price_when_there_is_a_gap():
    """开仓当日必须按**开盘价**盯市：净值 = (1-成本) × close/open，而不是 close/prev_close。"""
    closes = _rising()
    bars = _bars(closes, gap_at=60, gap=5.0)
    result = run_backtest(STRATEGY, bars, COST)

    entry, _ = _trade_span(result)
    assert entry == 60, "构造有问题：本用例假定信号在 bar 60 触发开仓"
    expected = (1 - COSTS) * closes[60] / bars[60].open
    # 曲线里的 net_value 做了 round(...,6)，所以按该精度比较。
    assert result["curve"][60]["net_value"] == pytest.approx(expected, abs=1e-6)
    # 旧口径会得到这个（更大的）值，用来证明本测试确实能区分两套算法。
    stale = (1 - COSTS) * closes[60] / closes[59]
    assert stale > expected


def test_equity_ratio_over_a_trade_equals_the_recorded_trade_return():
    """核心不变式：交易区间内的净值比 == 该笔交易记录的收益率（含两次成本）。"""
    closes = _rising()
    bars = _bars(closes, gap_at=60, gap=5.0)
    result = run_backtest(STRATEGY, bars, COST)

    entry, exit_ = _trade_span(result)
    ratio = result["curve"][exit_]["net_value"] / result["curve"][entry - 1]["net_value"]
    recorded = 1 + result["trades"][0]["return_pct"] / 100
    assert ratio == pytest.approx(recorded, rel=1e-3), (
        "净值曲线与 trades[].return_pct 仍在用两套价格（F23 未修）")


def test_exit_bar_marks_the_position_to_the_exit_open():
    """平仓当日必须把仓位盯市到**开盘成交价**，不能沿用前收→收盘而漏掉隔夜变动。"""
    rising = _rising(80)
    closes = rising + [rising[-1] - 4.0 * index for index in range(1, 30)]
    bars = _bars(closes, gap_at=0, gap=0.0)
    # 把平仓那天做成大幅低开，若沿用 close/prev_close 就会漏掉这段。
    entry, exit_ = None, None
    probe = run_backtest(STRATEGY, bars, COST)
    entry, exit_ = _trade_span(probe)
    bars[exit_].open = closes[exit_ - 1] - 6.0
    result = run_backtest(STRATEGY, bars, COST)
    entry, exit_ = _trade_span(result)

    previous = result["curve"][exit_ - 1]["net_value"]
    expected = previous * (bars[exit_].open / closes[exit_ - 1]) * (1 - COSTS)
    assert result["curve"][exit_]["net_value"] == pytest.approx(expected, abs=1e-6)
    assert result["trades"][0]["exit_price"] == bars[exit_].open


def test_without_gaps_both_conventions_agree_which_is_why_the_bug_hid():
    """无跳空时 `open_i == close_{i-1}`，两套算法结果相同——这就是缺陷长期隐身的原因。"""
    closes = _rising()
    result = run_backtest(STRATEGY, _bars(closes), COST)

    entry, exit_ = _trade_span(result)
    ratio = result["curve"][exit_]["net_value"] / result["curve"][entry - 1]["net_value"]
    recorded = 1 + result["trades"][0]["return_pct"] / 100
    assert ratio == pytest.approx(recorded, rel=1e-3)
    # 开仓当日：开盘价等于前收，于是两种盯市价也相同
    assert _bars(closes)[60].open == closes[59]


def test_signals_and_trade_counts_are_unchanged_by_the_accounting_fix():
    """本次只改**记账**，不改信号：交易日期与笔数必须与执行模型无关。"""
    closes = _rising()
    gapped = run_backtest(STRATEGY, _bars(closes, gap_at=60, gap=5.0), COST)
    flat = run_backtest(STRATEGY, _bars(closes), COST)

    assert [item["entry_date"] for item in gapped["trades"]] == \
           [item["entry_date"] for item in flat["trades"]]
    assert len(gapped["trades"]) == len(flat["trades"])
    # 净值水平不同（跳空被真实计入了），但仓位序列相同
    assert [point["position"] for point in gapped["curve"]] == \
           [point["position"] for point in flat["curve"]]
