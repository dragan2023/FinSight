"""涨停事件研究（预注册设计）的守卫测试。

这个模块的价值全在"设计不被结果牵着走"，所以测试盯的是**设计纪律**而不是数字好看：

1. 分组规则必须与登记一致（连板 ≥2 / 封单按**当日池内中位数**）；
2. 文本字段 `limit_up_reason` **未定编码方案前不得评价**——它必须报错，不能被塞一个
   默认编码偷偷跑出结果；
3. t 值必须**按评估日聚合**（同一天几十只涨停股高度相关，逐事件当独立样本会放大 |t|）；
4. 对照置换必须保持"同十分位 + 同样本量"；
5. 报告必须常驻披露幸存者偏差、2022 覆盖下限、以及"前向收益不得作为信号"。
"""

from __future__ import annotations

import math

import pytest

from backend.app.market_warehouse.repository import WarehouseRepository
from backend.app.screener.events import FIELD_ENCODINGS, LimitUpEventStudy, _t_stat

DAY_MS = 86_400_000
#: 事件日排在第 70 根 K 线：**事前 40 日动量需要 `close_60_ago`（第 61 根）**，
#: 事件日必须晚于它，否则"事前特征匹配"变体会静默退化成 base（早期夹具就是这样）。
EVENT_DAY = DAY_MS * 70
SYMBOLS = [f"{index:06d}.SZ" for index in range(30)]
#: 涨停的 6 只**故意分散在不同成交额十分位**（对照必须落在同一十分位；若全落在最低那一档，
#: 该档里没有非涨停标的，事件会被如实跳过 → 一天都用不了）。
POOL = [SYMBOLS[index] for index in (1, 6, 11, 16, 21, 26)]
CONTINUE_DAYS = [1, 1, 1, 2, 2, 3]
SEAL_MONEY = [1.0e6, 2.0e6, 3.0e6, 4.0e6, 5.0e6, 6.0e6]
#: 30 只 × 80 根 = 2400 行。**不能再多**：实测本机 DuckDB 参数化插入约 2–7 毫秒/行。
#: 80 根是**事前特征匹配的下限**：`close_60_ago` 需要第 61 根，事件日必须排在它之后。
BARS = 80


def _fixture(repository) -> None:
    db = repository._connect()
    try:
        bars = []
        for position, symbol in enumerate(SYMBOLS):
            # 每个标的走不同的斜率 → 前向收益有横截面差异（否则 t 值退化为 0/0）
            rate = 0.002 * (1 + (position % 5) * 0.5)
            for step in range(1, BARS + 1):
                price = 10.0 * (1 + rate * step)
                bars.append((symbol, DAY_MS * step, price, price * 1.01, price * 0.99, price,
                             1_000_000.0, 1.0e6 * (position + 1), "none", "CNY"))
        # 显式开事务（不开事务时是"每行一次提交"，更慢）
        db.execute("BEGIN")
        db.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?,?,?)", bars)
        db.commit()
    finally:
        db.close()
    repository.write_limit_pool_day(
        date_ms=EVENT_DAY, pool="limit_up",
        rows=[{"thscode": symbol, "name": "演示", "is_st": False, "is_new": False,
               "last_price": 10.0, "price_change_ratio_pct": 10.0,
               "continue_day_cnt": CONTINUE_DAYS[index], "continue_day_text": "演示",
               "seal_money": SEAL_MONEY[index], "limit_up_reason": "演示原因"}
              for index, symbol in enumerate(POOL)],
        status="ok", total=len(POOL), pages=1, page_size=200, error_code=0,
        error_message=None, fetched_at="2026-09-13T00:00:00+00:00")


@pytest.fixture(scope="module")
def repository(tmp_path_factory):
    """**模块级夹具**：本机 DuckDB 参数化插入约 2–7 毫秒/行，每个测试各建一次夹具
    会把 9 个测试拖到 150 秒以上。这些测试只读，共用一份没有副作用。"""
    repository = WarehouseRepository(tmp_path_factory.mktemp("event-study") / "market.duckdb")
    _fixture(repository)
    return repository


# --- 设计纪律 --------------------------------------------------------------

def test_text_field_is_refused_until_an_encoding_is_preregistered(repository):
    """`limit_up_reason` 是文本：编码方案本身是搜索空间，未预注册前**不得**评价。"""
    assert "limit_up_reason" not in FIELD_ENCODINGS
    with pytest.raises(ValueError) as excinfo:
        LimitUpEventStudy(repository).run(field="limit_up_reason", draws=5, horizon_days=5)
    assert "limit_up_reason" in str(excinfo.value)


def test_t_stat_is_computed_over_days_not_over_events():
    """把逐事件超额当独立样本会放大 |t|——这里钉住"按日聚合"的算法。"""
    assert _t_stat([0.01, 0.02, 0.03]) is not None
    assert _t_stat([0.01, 0.01]) is None, "少于 3 天没有 t 值"
    assert _t_stat([0.02, 0.02, 0.02]) is None, "零离散度不产生 t 值"
    assert _t_stat([0.02, float("nan"), 0.04, 0.06]) is not None, "NaN 必须被丢掉而不是当 0"

    series = [0.05, -0.05, 0.05, -0.05, 0.05, -0.05]
    mean = sum(series) / len(series)
    sd = math.sqrt(sum((value - mean) ** 2 for value in series) / (len(series) - 1))
    assert _t_stat(series) == pytest.approx(mean / (sd / math.sqrt(len(series))))


# --- 分组与对照 ------------------------------------------------------------

def test_continue_day_cnt_splits_at_two_boards(repository):
    report = LimitUpEventStudy(repository).run(field="continue_day_cnt", horizon_days=5,
                                               draws=5)

    assert report["dates_with_pool"] == 1 and report["dates_usable"] == 1
    assert report["events_total"] == 6
    assert report["results"]["high"]["events"] == 3      # 2、2、3 连板
    assert report["results"]["low"]["events"] == 3       # 三个首板
    assert "≥2" in report["split"]
    # 只有 1 个可用评估日 → 没有 t 值，但日均超额仍要报出来（不能因为算不出 t 就不输出）
    assert report["results"]["high"]["t_stat"] is None
    assert report["results"]["high"]["mean_excess"] == report["results"]["high"]["mean_excess"]
    assert report["results"]["high_minus_low"]["mean_excess"] is not None


def test_seal_money_splits_at_the_intraday_pool_median_not_an_absolute_amount(repository):
    report = LimitUpEventStudy(repository).run(field="seal_money", horizon_days=5, draws=5)

    # 当日池内封单中位数 = 4e6（6 个值里的第 4 个）→ 高组 2 个、低组 4 个
    assert report["results"]["high"]["events"] == 2
    assert report["results"]["low"]["events"] == 4
    assert "中位数" in report["split"]


def test_control_group_excludes_limit_up_names_themselves(repository):
    """对照必须是**非涨停**标的：把自己当对照会把超额压成 0。"""
    study = LimitUpEventStudy(repository)
    result = study._study_date(EVENT_DAY, {symbol: {"thscode": symbol, "continue_day_cnt": 1}
                                           for symbol in POOL},
                               horizon_days=5, fields=("continue_day_cnt",), draws=3,
                               rng=__import__("random").Random(1), pool_union=set(POOL))
    detail = result["continue_day_cnt"]
    # 30 只标的里 6 只涨停 → 对照最多 24 只（**涨停标的不得出现在自己的对照里**）
    assert detail["control_size"] == 24


def test_permutation_placebo_keeps_sample_size_and_decile_matching(repository):
    study = LimitUpEventStudy(repository)
    detail = study._study_date(EVENT_DAY, {symbol: {"thscode": symbol,
                                                    "continue_day_cnt": CONTINUE_DAYS[index]}
                                           for index, symbol in enumerate(POOL)},
                               horizon_days=5, fields=("continue_day_cnt",), draws=7,
                               rng=__import__("random").Random(2), pool_union=set(POOL))
    assert detail["continue_day_cnt"]["high_events"] == 3
    detail = detail["continue_day_cnt"]
    assert len(detail["permuted"]["high"]) == 7
    assert len(detail["permuted"]["low"]) == 7
    # 置换后的日均超额由"同期对照标的"算出，必然落在对照收益的取值范围内
    for value in detail["permuted"]["high"]:
        assert value == value                       # 不是 NaN
        assert abs(value) < 1.0


def test_permutation_null_is_reported_for_the_group_difference_too(repository):
    """「连板高度没有区分度」这句话必须有自己的零分布，否则它只是没算过。"""
    report = LimitUpEventStudy(repository).run(field="continue_day_cnt", horizon_days=5,
                                               draws=5)
    groups = report["placebo"]["permutation"]["by_group"]
    assert set(groups) == {"high", "low", "high_minus_low"}
    for stat in groups.values():
        assert "real_t" in stat and "t_abs_p95" in stat


def test_date_shift_placebo_uses_the_same_paired_estimator_as_the_main_statistic(repository):
    """回归守卫（第一版的错就在这里）。

    第一版日期错位用**全市场均值**当基准，而主统计量用**同十分位对照均值**——两者不是
    同一个量，实测 |t| 95 分位冲到 10.08，看起来像"主结果不显著"，其实是基准不一致。

    构造一个**差分测试**：让池内标的的收益恰好等于它所在十分位的对照均值（配对超额恒为 0），
    同时让全市场均值与之相差很远且逐日变化。
    - 正确的实现（配对估计量）→ 每日超额恒 0 → 零离散度 → 给不出 t 值 → draws == 0；
    - 错误实现（全市场均值）→ 逐日变化的非零超额 → 能算出 t 值 → draws > 0。
    """
    study = LimitUpEventStudy(repository)
    dates = list(range(1, 41))
    per_date = {}
    for index, date_ms in enumerate(dates):
        value = 0.01 + 0.001 * index
        per_date[date_ms] = {
            "pool_codes": ["A.SZ"],
            "pool_returns": {"A.SZ": value},
            "bucket_by_symbol": {"A.SZ": 0},
            "bucket_mean": {0: value},          # ← 配对超额恒为 0
            "market_mean": value + 500 + index,  # ← 全市场均值刻意相差很远且逐日变化
        }
    shift = study._date_shift_placebo(per_date, dates, draws=10, seed=7)
    assert shift["draws"] == 0, "配对超额恒为 0 时不该算出任何 t 值（否则说明用错了基准）"
    assert shift["t_abs_p95"] is None


def test_date_shift_placebo_does_detect_a_shifted_cohort_that_really_differs(repository):
    """反向对照：平移过来的标的若稳定跑赢同档对照，|t| 必须抓得到。

    没有这一条，"日期错位给不出 t 值"就可能是因为实现整个失效了，而不是因为对照配得准。
    """
    study = LimitUpEventStudy(repository)
    dates = list(range(1, 41))
    per_date = {}
    for index, date_ms in enumerate(dates):
        # 逐日有波动（否则零离散度），但**稳定**跑赢同档对照 3 个百分点
        per_date[date_ms] = {
            "pool_codes": ["A.SZ"],
            "pool_returns": {"A.SZ": 0.05 + (0.01 if index % 2 else -0.01)},
            "bucket_by_symbol": {"A.SZ": 0},
            "bucket_mean": {0: 0.02},
            "market_mean": 0.0,
        }
    shift = study._date_shift_placebo(per_date, dates, draws=10, seed=3)
    assert shift["draws"] > 0
    assert shift["t_abs_p95"] is not None and shift["t_abs_p95"] > 1.96


# --- v2 稳健性变体：池内分层 + 事前特征匹配 + 多重检验门槛 -----------------

def test_strata_restrict_events_to_the_declared_subsample(repository):
    """池内分层必须**真的**只取那一段子样本——否则"稳健性检验"是假的。

    夹具：首板 3 只（封单 1/2/3 百万）、连板 3 只（封单 4/5/6 百万），
    池内封单中位数 = 4 百万。
    """
    study = LimitUpEventStudy(repository)
    report = study.run(field="seal_money", horizon_days=5, draws=3)
    variants = report["variants"]

    # 按连板结构分层：首板全是低封单，连板里 2 高 1 低
    assert variants["base_first_board"]["results"]["high"]["events"] == 0
    assert variants["base_first_board"]["results"]["low"]["events"] == 3
    assert variants["base_multi_board"]["results"]["high"]["events"] == 2
    assert variants["base_multi_board"]["results"]["low"]["events"] == 1
    # 按封单结构分层看连板：高封单那层只有连板，低封单那层 3 首板 + 1 连板
    other = study.run(field="continue_day_cnt", horizon_days=5, draws=3)["variants"]
    assert other["base_high_seal"]["results"]["high"]["events"] == 2
    assert other["base_high_seal"]["results"]["low"]["events"] == 0
    assert other["base_low_seal"]["results"]["high"]["events"] == 1
    assert other["base_low_seal"]["results"]["low"]["events"] == 3
    # 分层后的样本量必须小于 all 层
    assert variants["base_all"]["events"] > variants["base_multi_board"]["events"]


def test_matched_variant_really_uses_a_finer_control_grid(repository):
    """事前特征匹配必须**真的**把对照切得更细，而不是换个名字。

    细网格 = 成交额十分位 × 事前 40 日动量三分位。若事件日排在 `close_60_ago` 之前，
    动量全是空 → 细网格退化成粗网格，这条测试就会失败（这正是它要防的）。
    """
    study = LimitUpEventStudy(repository)
    detail = study._study_date(EVENT_DAY, {symbol: {"thscode": symbol,
                                                    "continue_day_cnt": CONTINUE_DAYS[index],
                                                    "seal_money": SEAL_MONEY[index]}
                                           for index, symbol in enumerate(POOL)},
                               horizon_days=5, fields=("seal_money",), draws=3,
                               rng=__import__("random").Random(5), pool_union=set(POOL))
    detail = detail["seal_money"]
    assert detail["control_buckets"] > 0
    assert detail["control_cells_matched"] > detail["control_buckets"], \
        "事前动量三分位没有生效：细网格必须比粗网格更细"
    assert detail["variants"]["matched_all"]["events_used"] > 0


def test_variant_results_and_nulls_are_reported_for_every_variant(repository):
    report = LimitUpEventStudy(repository).run(field="seal_money", horizon_days=5, draws=3)
    names = sorted(report["variants"])
    assert names == ["base_all", "base_first_board", "base_multi_board", "matched_all"]
    for name, payload in report["variants"].items():
        assert set(payload["results"]) == {"high", "low", "high_minus_low"}
        assert set(payload["placebo"]["by_group"]) == {"high", "low", "high_minus_low"}
        for stat in payload["placebo"]["by_group"].values():
            assert "real_t" in stat and "t_abs_p95" in stat and "t_draws" in stat
    # 兼容：顶层 results / placebo 指向 base_all
    assert report["results"] == report["variants"]["base_all"]["results"]


def test_multiplicity_counts_every_comparison_it_reports(repository):
    """按方案 A.2，计数必须包含**全部看过的**比较；门槛随计数变严。"""
    report = LimitUpEventStudy(repository).run(field="seal_money", horizon_days=5, draws=3)
    multiplicity = report["multiplicity"]
    expected = len(report["variants"]) * 3 + len(report["by_period"])
    assert multiplicity["statistics_tested"] == expected
    assert multiplicity["per_test_abs_t"] == 1.96
    assert multiplicity["bonferroni_primary_abs_t"] > 1.96
    assert multiplicity["bonferroni_all_abs_t"] > multiplicity["bonferroni_primary_abs_t"]
    assert str(multiplicity["bonferroni_all_abs_t"]) in report["significance"]


def test_two_sided_z_thresholds_are_correct():
    from backend.app.screener.events import _z_for_two_sided

    assert _z_for_two_sided(0.05) == pytest.approx(1.96, abs=0.01)
    assert _z_for_two_sided(0.05 / 2) == pytest.approx(2.241, abs=0.01)
    assert _z_for_two_sided(0.05 / 24) == pytest.approx(3.09, abs=0.02)
    assert _z_for_two_sided(0.10) < _z_for_two_sided(0.05)


# --- 报告纪律 --------------------------------------------------------------

def test_report_discloses_the_hard_limitations(repository):
    report = LimitUpEventStudy(repository).run(field="continue_day_cnt", horizon_days=5,
                                               draws=5)
    joined = " ".join(report["limitations"])
    assert "幸存者偏差" in joined and "2022-01-05" in joined
    assert "不得作为信号输入" in joined
    assert "不构成投资建议" in report["disclaimer"]
    assert "limit_up_event_study_design@v1" in report["design"]
    assert "看 t 值" in report["significance"]


def test_placebo_is_reported_but_never_used_as_the_significance_threshold(repository):
    """`13` 号文档的方法学修正：安慰剂只是机制自检，门槛永远是 t 值。"""
    report = LimitUpEventStudy(repository).run(field="continue_day_cnt", horizon_days=5,
                                               draws=5)
    assert "不得**当作显著性门槛" in report["significance"]
    for key in ("permutation", "date_shift"):
        assert key in report["placebo"]
    assert report["placebo"]["date_shift"]["draws"] >= 0


def test_empty_pool_returns_a_reason_instead_of_a_fake_conclusion(tmp_path):
    repository = WarehouseRepository(tmp_path / "empty.duckdb")
    report = LimitUpEventStudy(repository).run(field="continue_day_cnt", draws=5)
    assert report["dates_usable"] == 0
    assert "sync_limit_pools" in report["reason"]
