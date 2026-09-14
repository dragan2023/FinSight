"""财务 point-in-time 数据集的守卫测试（`22` 号文档 / D58–D60）。

这一轮最容易被写错、而且**错了也看不出来**的东西有三类，测试就盯这三类：

1. **闸门**（`available_from_ms`）：它是"能不能用于历史评价"的唯一防线。
   `report_date_ms` 实测不可信（利润表/现金流量表整体晚约一年），所以闸门取
   `min(接口日期, 法定披露截止日) + 1 天`。判据必须逐条钉住——包括**边界**（闸门当刻可见、
   早 1 毫秒不可见）；
2. **缺值不是 0**：`total_assets=0`、银行没有"营业成本"、字段根本没返回——三种情况
   都必须得到 `None`，而不是 0 或近似值；拿 0 兜底会把"算不出来"做成"值为 0"混进因子；
3. **幂等**：同一只标的重复同步不得叠加行数（DuckDB 的 `CREATE/INSERT` 语义与事务边界
   在本仓库已经坑过两次，见 `14` §6.3）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.app.advisor.algorithms.f_score import calculate_f_score
from backend.app.market_warehouse.financials import (
    BASIS_API_REPORT_DATE,
    BASIS_STATUTORY_DEADLINE,
    available_from_ms,
    compute_ratios,
    day_text_ms,
    f_score_inputs,
    f_score_local_coverage,
    iso_text_ms,
    statutory_deadline_date,
)
from backend.app.market_warehouse.repository import WarehouseRepository

CST = timezone(timedelta(hours=8))
RUN_ID = "test-run"


def ms(year: int, month: int, day: int, *, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=CST).timestamp() * 1000)


# --- 1. 法定披露截止日（交易所规则 5.2.2） -----------------------------------

@pytest.mark.parametrize("period_end,expected", [
    ((2025, 3, 31), date(2025, 4, 30)),    # 一季报：季末后 1 个月内
    ((2025, 6, 30), date(2025, 8, 31)),    # 半年报：半年末后 2 个月内
    ((2025, 9, 30), date(2025, 10, 31)),   # 三季报：季末后 1 个月内
    ((2025, 12, 31), date(2026, 4, 30)),   # 年报：年末后 4 个月内 → **次年** 4 月 30 日
])
def test_statutory_deadline_follows_the_exchange_rule(period_end, expected):
    assert statutory_deadline_date(ms(*period_end)) == expected


def test_statutory_deadline_refuses_a_non_standard_fiscal_year_end():
    """非标准财年（期末不在 3/6/9/12 月末）必须报错——截止日不得臆造。"""
    with pytest.raises(ValueError, match="不在法定披露规则表里"):
        statutory_deadline_date(ms(2025, 5, 31))


# --- 2. PIT 闸门：min(接口日期, 法定截止日) + 1 天 ---------------------------

def test_credible_api_date_is_trusted_and_opens_a_bit_earlier():
    """接口日期**早于**截止日 → 它是可信的（实测资产负债表 Q1 滞后仅 25 天），采信它。"""
    gate, basis = available_from_ms(period_end_ms=ms(2025, 3, 31),
                                    api_disclosed_ms=ms(2025, 4, 25))
    assert basis == BASIS_API_REPORT_DATE
    assert gate == ms(2025, 4, 26), "披露日当天不视为已可得，次日 00:00 才开闸"


def test_drifted_api_date_falls_back_to_the_statutory_deadline():
    """实测：利润表/现金流量表的披露日整体晚约一年（中位 386–445 天）。

    若直接采信，历史评价会白等一年；回落到法定截止日既新鲜、也**不会早于**法定截止日
    （披露必定不晚于它），因此不会引入未来信息。
    """
    gate, basis = available_from_ms(period_end_ms=ms(2025, 3, 31),
                                    api_disclosed_ms=ms(2026, 4, 25))
    assert basis == BASIS_STATUTORY_DEADLINE
    assert gate == ms(2025, 5, 1)

    gate, basis = available_from_ms(period_end_ms=ms(2025, 6, 30),
                                   api_disclosed_ms=ms(2026, 8, 15))
    assert basis == BASIS_STATUTORY_DEADLINE
    assert gate == ms(2025, 9, 1)


def test_missing_api_date_falls_back_to_the_deadline():
    gate, basis = available_from_ms(period_end_ms=ms(2025, 9, 30), api_disclosed_ms=None)
    assert (basis, gate) == (BASIS_STATUTORY_DEADLINE, ms(2025, 11, 1))


def test_day_and_iso_text_converters_are_timezone_correct():
    assert day_text_ms("2025-03-31") == ms(2025, 3, 31)
    assert iso_text_ms("2026-08-15T00:00:00+08:00") == ms(2026, 8, 15)
    assert iso_text_ms(None) is None


# --- 3. 本地比率：缺值不得变成 0 ---------------------------------------------

def test_ratios_are_computed_from_the_three_statements():
    facts = {"net_profit": 100.0, "assets_total": 1000.0, "total_debt": 400.0,
             "operating_income": 500.0, "operating_costs": 300.0}
    ratios = compute_ratios(facts)
    assert ratios["roa"] == pytest.approx(0.1)
    assert ratios["assets_debt_ratio"] == pytest.approx(0.4)
    assert ratios["sale_gross_margin"] == pytest.approx(0.4)
    assert ratios["total_assets_turnover_ratio"] == pytest.approx(0.5)


def test_missing_or_zero_inputs_yield_none_never_zero():
    """三种缺法都必须得到 `None`：字段没有、分母为 0、银行没有营业成本。"""
    assert compute_ratios({})["roa"] is None
    assert compute_ratios({"net_profit": 1.0, "assets_total": 0.0})["roa"] is None
    # 银行：income/总资产 有值，但没有 operating_costs → 毛利率算不出来（**不是 0**）
    bank = {"net_profit": 1.0, "assets_total": 10.0, "total_debt": 9.0,
            "operating_income": 5.0, "operating_costs": None}
    assert bank and compute_ratios(bank)["sale_gross_margin"] is None
    assert compute_ratios({"operating_income": 5.0, "assets_total": 10.0})[
        "sale_gross_margin"] is None


def test_local_coverage_is_declared_honestly():
    coverage = f_score_local_coverage()
    assert coverage["total_items"] == 9
    assert coverage["locally_computable"] == 7
    assert coverage["unavailable"] == ["current_ratio", "shares_outstanding"]
    assert "披露日" in coverage["reasons"]["current_ratio"]


def test_f_score_runs_locally_from_statement_facts_only():
    """**从三表原始字段**走通 F-Score——这是这一轮"F-Score 本地可算"的验收点。"""
    current = f_score_inputs({"net_profit": 120.0, "assets_total": 1000.0,
                              "act_cash_flow_net": 150.0, "total_debt": 300.0,
                              "operating_income": 600.0, "operating_costs": 300.0})
    previous = f_score_inputs({"net_profit": 100.0, "assets_total": 900.0,
                               "act_cash_flow_net": 90.0, "total_debt": 320.0,
                               "operating_income": 500.0, "operating_costs": 280.0})
    report = calculate_f_score(current, previous, evidence_id="test")

    assert report["eligible"] is True
    assert report["total_items"] == 9
    # 7 项可算（流动比率与"未增发"本地算不出来），逐项判定照旧
    assert report["available_items"] == 7
    passed = {item["name"]: item["passed"] for item in report["items"]}
    assert passed["资产回报为正"] is True
    assert passed["经营现金流为正"] is True
    assert passed["资产回报改善"] is True
    assert passed["资产负债率下降"] is True
    assert passed["销售毛利率改善"] is True
    assert passed["总资产周转改善"] is True
    # CFO/资产 = 15% > ROA = 12% → 利润质量项通过
    assert passed["现金流覆盖利润质量"] is True
    missing = [item for item in report["items"] if not item["available"]]
    assert {item["name"] for item in missing} == {"流动比率改善", "未增发"}


def test_f_score_accepts_either_roa_dialect():
    """`roa`（本地口径）与 `total_assets_net_ratio`（厂商指标）都要能被识别。

    回退是为了不破坏顾问链既有调用方式；优先本地口径则是因为它自证且带披露日。
    两个口径实测相对差 <1%（厂商疑似用平均总资产），所以**不合并成一个键**。
    """
    base = {"net_profit": 120.0, "assets_total": 1000.0, "act_cash_flow_net": 150.0,
            "total_debt": 300.0, "operating_income": 600.0, "operating_costs": 300.0}
    before = {"net_profit": 100.0, "assets_total": 900.0, "act_cash_flow_net": 90.0,
              "total_debt": 320.0, "operating_income": 500.0, "operating_costs": 280.0}
    local_now, local_before = f_score_inputs(base), f_score_inputs(before)
    vendor_now = {**local_now, "total_assets_net_ratio": 0.12, "roa": None}
    vendor_before = {**local_before, "total_assets_net_ratio": 0.10, "roa": None}

    for now, then in ((local_now, local_before), (vendor_now, vendor_before)):
        report = calculate_f_score(now, then, evidence_id="test")
        assert report["available_items"] == 7, "两种键名都必须让 7 项可算"
        passed = {item["name"]: item["passed"] for item in report["items"]}
        assert passed["资产回报为正"] is True
        assert passed["资产回报改善"] is True


# --- 4. 仓储层：PIT 边界、幂等、台账 -----------------------------------------

def _facts_rows(period: str, period_end_ms: int, api_disclosed_ms: int, values: dict,
                *, basis_override: int | None = None) -> list[dict]:
    gate, basis = available_from_ms(period_end_ms=period_end_ms,
                                    api_disclosed_ms=api_disclosed_ms)
    if basis_override is not None:
        gate = basis_override
    rows = []
    for field, value in values.items():
        rows.append({"thscode": "AAA.SZ", "statement": "income", "period_key": period,
                     "period_end_ms": period_end_ms, "field": field, "value": value,
                     "unit": "CNY", "currency": "CNY", "disclosed_at_ms": api_disclosed_ms,
                     "available_from_ms": gate, "available_basis": basis,
                     "api_basis": "quarterly_cumulative_unknown",
                     "source": "test", "run_id": RUN_ID,
                     "fetched_at": "2026-09-15T00:00:00+00:00"})
    return rows


def _ledger(status: str = "available", periods: int = 1, fields: int = 2) -> dict:
    return {"status": status, "periods": periods, "fields": fields, "gaps": 0,
            "requested_start_ms": ms(2016, 1, 1), "requested_end_ms": ms(2026, 9, 4),
            "api_basis": "quarterly_cumulative_unknown", "error_code": None,
            "error_message": None, "run_id": RUN_ID,
            "fetched_at": "2026-09-15T00:00:00+00:00"}


@pytest.fixture(scope="module")
def repository(tmp_path_factory):
    repository = WarehouseRepository(tmp_path_factory.mktemp("financials") / "market.duckdb")
    # ⚠️ 两个报告期必须**在同一次调用里**写：写入粒度是 `(标的, 报表)`，
    # 一次调用替换该报表的全部报告期（接口一次请求就返回全部期数，粒度因此是对的）。
    # 分两次写会互相抹掉——测试夹具里踩过一次，所以这里显式合并。
    rows = (
        # 2025-Q1：接口日期可疑（漂移）→ 闸门回落到法定截止日 2025-04-30 的次日
        _facts_rows("2025-Q1", ms(2025, 3, 31), ms(2026, 4, 25),
                    {"net_profit": 100.0, "assets_total": 1000.0})
        # 2026-Q1：接口日期可信（+25 天）→ 闸门 = 2026-04-26
        + _facts_rows("2026-Q1", ms(2026, 3, 31), ms(2026, 4, 25),
                      {"net_profit": 200.0, "assets_total": 1200.0}))
    repository.write_financial_symbol(
        thscode="AAA.SZ", statement="income", rows=rows, ledger=_ledger(periods=2, fields=4))
    return repository


def test_pit_gate_boundary_is_exact(repository):
    """闸门当刻可见、早 1 毫秒不可见——PIT 的判据必须精确到毫秒，不能"大概差不多"。"""
    gate = ms(2026, 4, 26)
    just_before = repository.financial_snapshot_asof(thscode="AAA.SZ", as_of_ms=gate - 1)
    at_gate = repository.financial_snapshot_asof(thscode="AAA.SZ", as_of_ms=gate)

    assert just_before["current"]["period_key"] == "2025-Q1", "2026-Q1 还没开闸"
    assert at_gate["current"]["period_key"] == "2026-Q1"
    assert at_gate["current"]["facts"]["net_profit"] == 200.0


def test_no_lookahead_across_the_whole_timeline(repository):
    """在整条时间线上抽样：任何 as_of 下返回的事实，其闸门都必须 ≤ as_of。"""
    for stamp in (ms(2024, 1, 1), ms(2025, 4, 30), ms(2025, 5, 1), ms(2026, 4, 25),
                  ms(2026, 4, 26), ms(2026, 9, 4)):
        snapshot = repository.financial_snapshot_asof(thscode="AAA.SZ", as_of_ms=stamp)
        current = snapshot["current"]
        if current is None:
            continue
        assert current["available_from_ms"] <= stamp
        if snapshot["previous"]:
            assert snapshot["previous"]["available_from_ms"] <= stamp


def test_previous_period_is_the_same_quarter_last_year(repository):
    """Δ 类信号要用**上年同季**：中国季报是累计口径，同季同比才是同一口径的比较。"""
    snapshot = repository.financial_snapshot_asof(thscode="AAA.SZ", as_of_ms=ms(2026, 9, 4))
    assert snapshot["current"]["period_key"] == "2026-Q1"
    assert snapshot["previous"]["period_key"] == "2025-Q1"
    assert snapshot["previous"]["facts"]["net_profit"] == 100.0


def test_before_any_gate_there_is_nothing(repository):
    """早于全部闸门 → 什么都没有。**不能**退化成"用已知的最新值"（那就是未来信息）。"""
    snapshot = repository.financial_snapshot_asof(thscode="AAA.SZ", as_of_ms=ms(2024, 1, 1))
    assert snapshot["current"] is None and snapshot["previous"] is None


def test_writes_are_idempotent_and_ledger_separates_missing_from_unfetched(repository):
    """幂等：同一 (标的, 报表) 重复写是**整体替换**，行数不叠加、也不留旧期。"""
    before = len(repository.financial_facts_by_symbol(thscode="AAA.SZ"))
    assert before == 4, "夹具应写进 2 期 × 2 字段"
    repository.write_financial_symbol(
        thscode="AAA.SZ", statement="income",
        rows=_facts_rows("2026-Q1", ms(2026, 3, 31), ms(2026, 4, 25),
                         {"net_profit": 200.0, "assets_total": 1200.0}),
        ledger=_ledger())
    after = repository.financial_facts_by_symbol(thscode="AAA.SZ")
    assert len(after) == 2, "写入粒度是 (标的, 报表)：只写 1 期就必须只剩 1 期（整体替换）"
    assert {row["period_key"] for row in after} == {"2026-Q1"}

    repository.write_financial_symbol(
        thscode="BBB.SZ", statement="balance", rows=[],
        ledger={**_ledger(status="transport_error"), "error_message": "TimeoutError: 超时"})
    ledger = {(row["thscode"], row["statement"]): row
              for row in repository.financial_sync_ledger()}
    assert ledger[("BBB.SZ", "balance")]["status"] == "transport_error"
    assert "超时" in ledger[("BBB.SZ", "balance")]["error_message"], \
        "台账要记下**消息**而不只是异常类型——否则事后无从查起（实测踩过）"


def test_dataset_status_gates_on_version(repository):
    repository.write_financial_meta({"version": "financial_pit_v0", "run_id": RUN_ID})
    status = repository.financial_dataset_status()
    assert status["ready"] is False and "版本" in status["reason"]

    repository.write_financial_meta({"version": "financial_pit_v1", "run_id": RUN_ID})
    status = repository.financial_dataset_status()
    assert status["ready"] is True
    assert status["counts"]["facts"] > 0
