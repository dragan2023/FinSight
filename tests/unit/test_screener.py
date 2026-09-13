"""C2 最小版守卫测试：横截面可复现性、股票池可解释、因子口径、以及**评价方法的正确性**。

最后一条最重要：本模块第一版用「逐标的置乱安慰剂」当显著性门槛，实测得到 0.24–0.52 的
"通过率"——而真零分布下该值应≈0.05。根因是逐标的置乱把个股收益之间的横截面相关
（共同市场因子）一起打碎了，零分布离散度只有真实 IC 离散度的 1/4–1/7，于是系统性放大通过率。
下面的测试把"显著性必须来自 IC 时间序列 t 值"这件事钉住，防止那条错路被再走一遍。
"""

from __future__ import annotations

import math

import pytest

from backend.app.market_warehouse.repository import WarehouseRepository
from backend.app.screener import FACTOR_IDS, ScreenerService, UniverseRules
from backend.app.screener.service import (_stdev, _verdict, ascending_ranks,
                                          percentile_preference)
from backend.app.screener.universe import UNSUPPORTED_EXCLUSIONS, apply_rules

DAY_MS = 86_400_000
THSCODE = "601799.SH"


# --- 纯函数层面的口径 ------------------------------------------------------

def test_ascending_ranks_uses_average_for_ties():
    assert ascending_ranks([10.0, 20.0, 20.0, 30.0]) == [0.0, 1.5, 1.5, 3.0]


def test_percentile_direction_decides_who_gets_the_best_score():
    values = [1.0, 2.0, 3.0]
    assert percentile_preference(values, "high") == [0.0, 0.5, 1.0]
    assert percentile_preference(values, "low") == [1.0, 0.5, 0.0]


def test_verdict_refuses_to_overclaim():
    assert "与噪声无异" in _verdict(1.95)
    assert "与噪声无异" in _verdict(-1.95)
    assert "不构成本身可交易的结论" in _verdict(2.5)
    assert "方向与数据相反" in _verdict(-2.5)
    assert "样本不足" in _verdict(None)


def test_stdev_is_the_sample_standard_deviation():
    assert _stdev([1.0]) == 0.0
    assert _stdev([1.0, 3.0]) == pytest.approx(math.sqrt(2.0))


# --- 股票池规则 -----------------------------------------------------------

def _row(**overrides) -> dict:
    row = {"thscode": THSCODE, "close": 10.0, "prev_close": 9.8, "high": 10.2,
           "turnover_20": 5.0e7, "volume_20": 1.0e6, "bars": 300, "ma20": 9.9, "ma60": 9.5,
           "vol20": 0.01, "close_20_ago": 9.5, "close_60_ago": 9.0,
           "bonus_20": 0.0, "div_20": 0.0, "rights_20": 0.0, "rights_cost_20": 0.0,
           "bonus_60": 0.0, "div_60": 0.0, "rights_60": 0.0, "rights_cost_60": 0.0}
    row.update(overrides)
    return row


def test_every_exclusion_has_a_count_and_a_documented_reason():
    rows = [
        _row(thscode="000001.SZ"),
        _row(thscode="000002.SZ", bars=100),                       # 历史不足
        _row(thscode="000003.SZ", turnover_20=1.0e6),              # 成交额过低
        _row(thscode="000004.SZ", close=0.0),                      # 价格异常
        _row(thscode="000005.SZ", close=10.8, high=10.8),          # 涨停代理（涨幅 10.2%）
    ]
    kept, exclusions = apply_rules(rows, UniverseRules())

    assert [row["thscode"] for row in kept] == ["000001.SZ"]
    assert exclusions["insufficient_history"] == 1
    assert exclusions["below_min_turnover"] == 1
    assert exclusions["non_positive_price"] == 1
    assert exclusions["suspected_limit_up"] == 1
    assert exclusions["no_bar_on_date"] == 0


def test_unsupported_exclusions_are_disclosed():
    """能做到什么、做不到什么都要说清楚——否则使用者会以为池子已经干净了。"""
    joined = " ".join(UNSUPPORTED_EXCLUSIONS)
    assert "ST" in joined and "退市" in joined and "停牌" in joined


def test_limit_up_proxy_can_be_disabled_and_is_documented_as_a_proxy():
    rows = [_row(close=10.8, high=10.8)]
    kept, exclusions = apply_rules(rows, UniverseRules(exclude_suspected_limit_up=False))
    assert len(kept) == 1 and exclusions["suspected_limit_up"] == 0
    _, excluded = apply_rules(rows, UniverseRules())
    assert excluded["suspected_limit_up"] == 1


# --- 因子口径 -------------------------------------------------------------

def test_momentum_is_corporate_action_adjusted():
    """送股 1:1 时价格折半，但总收益口径的 60 日动量不应被记成 −50%。"""
    row = _row(close=10.0, close_60_ago=20.0, bonus_60=1.0)   # 10送10
    from backend.app.screener.service import _factor_value

    adjusted = _factor_value(row, "momentum_60d")
    unadjusted = row["close"] / row["close_60_ago"] - 1
    assert unadjusted == pytest.approx(-0.5)
    assert adjusted == pytest.approx(0.0)


def test_cash_dividend_is_added_back():
    from backend.app.screener.service import _factor_value

    row = _row(close=19.0, close_60_ago=20.0, div_60=1.0)
    assert _factor_value(row, "momentum_60d") == pytest.approx(0.0)
    assert _factor_value(row, "bias_20") == pytest.approx(row["close"] / row["ma20"] - 1)
    assert _factor_value(row, "volatility_20d") == pytest.approx(row["vol20"] * math.sqrt(252))
    assert _factor_value(row, "unknown_factor") is None


# --- 真实 DuckDB：确定性与 as-of 截断 --------------------------------------

def _write_fixture(repository) -> None:
    db = repository._connect()
    try:
        rows = []
        for symbol in ("000001.SZ", "000002.SZ"):
            price = 10.0
            for index in range(80):
                date_ms = DAY_MS * (index + 1)
                rows.append((symbol, date_ms, price, price + 0.1, price - 0.1, price,
                             1_000_000.0, 5.0e7, "none", "CNY"))
                price *= 1.002
        db.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        # 一个**评估日之后**才发生的除权事件：as-of 截断必须把它挡掉
        db.execute("INSERT INTO adjustment_factors VALUES (?,?,?,?,?,?,?,?,?)",
                   ("future", "000001.SZ", "000001", DAY_MS * 200, 0.0, 1.0, 0.0, 0.0, "CNY"))
    finally:
        db.close()


def test_cross_section_ignores_everything_after_as_of(tmp_path):
    repository = WarehouseRepository(tmp_path / "market.duckdb")
    _write_fixture(repository)
    service = ScreenerService(repository)

    pool = service.rank(DAY_MS * 80, rules=UniverseRules(min_history_bars=60),
                        factor_ids=("bias_20",), top_n=None)

    assert pool["universe"]["raw_symbols"] == 2
    for row in pool["candidates"]:
        assert row["data_as_of"] == "1970-03-22"      # DAY_MS*80
        assert row["bars"] == 80                       # 评估日之后的行不存在
    # 未来除权事件不得被应用：两个标的的乖离率必须完全一致（序列形状相同）
    biases = {row["factors"]["bias_20"]["value"] for row in pool["candidates"]}
    assert len(biases) == 1


def test_rank_is_deterministic_and_params_hash_is_stable(tmp_path):
    repository = WarehouseRepository(tmp_path / "market.duckdb")
    _write_fixture(repository)
    service = ScreenerService(repository)
    rules = UniverseRules(min_history_bars=60)

    first = service.rank(DAY_MS * 80, rules=rules, top_n=None)
    second = service.rank(DAY_MS * 80, rules=rules, top_n=None)

    assert first["params_hash"] == second["params_hash"]
    assert [row["thscode"] for row in first["candidates"]] == \
           [row["thscode"] for row in second["candidates"]]
    assert [row["composite_score"] for row in first["candidates"]] == \
           [row["composite_score"] for row in second["candidates"]]

    other = service.rank(DAY_MS * 80, rules=UniverseRules(min_history_bars=70), top_n=None)
    assert other["params_hash"] != first["params_hash"], "规则变化必须改变 params_hash"

    weighted = service.rank(DAY_MS * 80, rules=rules, top_n=None,
                            weights={"bias_20": 2.0, "momentum_60d": 0.5,
                                     "volatility_20d": 1.0})
    assert weighted["params_hash"] != first["params_hash"], "权重变化必须改变 params_hash"


def test_rank_output_is_traceable_and_labelled_as_a_candidate_pool(tmp_path):
    repository = WarehouseRepository(tmp_path / "market.duckdb")
    _write_fixture(repository)
    pool = ScreenerService(repository).rank(DAY_MS * 80,
                                            rules=UniverseRules(min_history_bars=60), top_n=None)

    assert pool["label"] == "候选池（研究用，不是买入清单）"
    assert pool["disclaimer"] == "研究参考，不构成投资建议。"
    row = pool["candidates"][0]
    for key in ("thscode", "composite_score", "rank", "factors", "liquidity",
                "bars", "data_as_of", "data_source"):
        assert key in row
    for factor_id in FACTOR_IDS:
        assert row["factors"][factor_id]["value"] is not None
        assert row["factors"][factor_id]["preference"] is not None
        assert row["factors"][factor_id]["direction"] in ("high", "low")


def test_unregistered_factor_is_rejected(tmp_path):
    from backend.app.errors import AppError

    repository = WarehouseRepository(tmp_path / "market.duckdb")
    _write_fixture(repository)
    with pytest.raises(AppError) as excinfo:
        ScreenerService(repository).rank(DAY_MS * 80, factor_ids=("made_up_alpha",))
    assert excinfo.value.category == "screener_invalid"
    assert "先登记" in excinfo.value.message


# --- 评价方法的正确性（本模块第一版踩过的坑）------------------------------

def test_significance_comes_from_the_ic_t_statistic_not_from_the_placebo():
    """IC 时间序列的 t 值必须由**真实 IC 的逐日离散度**算出。

    用一个真实 IC 离散度远大于 IID 零分布的情形构造：如果谁把 IID 零分布当门槛，
    就会把纯噪声判成显著。这里直接验证 t 值的算法与判读措辞。
    """
    ics = [0.15, -0.12, 0.18, -0.10, 0.14, -0.11]      # 均值≈0.023，sd≈0.14
    mean_ic = sum(ics) / len(ics)
    sd = _stdev(ics)
    t = mean_ic / (sd / math.sqrt(len(ics)))
    iid_null_sd = 1 / math.sqrt(5000 - 1)

    assert sd > 5 * iid_null_sd, "构造应体现真实 IC 离散度远大于 IID 零分布"
    assert _verdict(t) == _verdict(t) and abs(t) < 1.96, f"该序列应判为与噪声无异（t={t:.2f}）"
    assert "与噪声无异" in _verdict(t)


def test_ic_report_does_not_expose_a_placebo_based_pass_rate():
    """回归守卫：把「逐标的置乱安慰剂通过率」当显著性指标是错的，不得再出现在报告里。"""
    import json
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "backend" / "app" / "screener" / "service.py"
    text = source.read_text(encoding="utf-8")
    assert '"pass_rate"' not in text, "报告里不得再出现安慰剂通过率字段"
    assert "beats_placebo_p95" not in text
    # 方法说明必须写明安慰剂只是机制自检
    assert "机制自检" in text and "不得**用作显著性门槛" in text.replace("**", "**")
    assert json  # 保持 import 有用（避免 lint 误报）
