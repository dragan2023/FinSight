"""财务因子 IC 评价的守卫测试（`23` 号文档 / 预注册设计 `financial_factor_study_design@v1`）。

盯三类东西：

1. **跨期拼数**——同一快照里的各张报表**必须来自同一个报告期**。三表披露节奏不同
   （资产负债表 Q4 的接口日期实测比利润表晚 4 个月），按"全体最大期"取就会造出
   「利润表 Q2 + 资产负债表 Q1」，比率的分子分母跨期了，**数字看着正常、汇总层面看不出来**；
2. **方向与缺值**——方向写错会让 IC 符号全反（登记里写死，不在评价层二次判断）；
   缺值必须是 `None`，不能是 0；
3. **声明的计数**——多重检验的 36 条与族级门槛是**登记时写死**的，不能被代码悄悄改。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.market_warehouse.financials import (
    group_periods,
    prior_year_key,
    select_common_period,
    select_prior_year_facts,
)
from backend.app.market_warehouse.repository import WarehouseRepository
from backend.app.screener.financial_ic import (
    FACTOR_DIRECTIONS,
    FACTORS,
    _declared_comparisons,
    _period_mix,
    _series_stats,
    _verdict,
    factor_values,
)

import scripts.run_financial_ic as cli  # noqa: E402

DAY = 86_400_000


def ms(year: int, month: int, day: int) -> int:
    from datetime import datetime, timedelta, timezone

    return int(datetime(year, month, day, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


def fact(statement: str, period: str, period_end: int, gate: int, field: str,
         value: float) -> dict:
    return {"thscode": "AAA.SZ", "statement": statement, "period_key": period,
            "period_end_ms": period_end, "field": field, "value": value,
            "available_from_ms": gate, "available_basis": "statutory_deadline"}


# --- 1. 共同报告期：禁止跨期拼数 ---------------------------------------------

def test_common_period_never_mixes_statements_from_different_periods():
    """利润表已出到 Q2、资产负债表还停在 Q1 → 只能取 **Q1**（两表齐备的最近期）。

    取 Q2 会得到「净利润(Q2) ÷ 资产总计(Q1)」——分子分母跨期，数字看着正常却是错的。
    """
    q1, q2 = ms(2026, 3, 31), ms(2026, 6, 30)
    rows = [
        fact("income", "2026-Q2", q2, ms(2026, 8, 1), "net_profit", 200.0),
        fact("income", "2026-Q1", q1, ms(2026, 5, 1), "net_profit", 120.0),
        fact("balance", "2026-Q1", q1, ms(2026, 5, 1), "assets_total", 1000.0),
    ]
    period, facts, per_statement = select_common_period(group_periods(rows), ms(2026, 9, 1))

    assert period == q1, "必须取两表齐备的最近期，而不是全体最大期"
    assert facts == {"net_profit": 120.0, "assets_total": 1000.0}
    assert set(per_statement) == {"income", "balance"}
    # 关键性质：快照里的净利润与资产总计来自**同一个**报告期
    assert per_statement["income"]["period_key"] == per_statement["balance"]["period_key"]


def test_common_period_respects_the_gate():
    """闸门之后的那期不算数——共同期只能在**已披露**的期里选。"""
    q1, q2 = ms(2026, 3, 31), ms(2026, 6, 30)
    rows = [
        fact("income", "2026-Q2", q2, ms(2026, 8, 16), "net_profit", 200.0),
        fact("balance", "2026-Q2", q2, ms(2026, 8, 16), "assets_total", 1200.0),
        fact("income", "2026-Q1", q1, ms(2026, 5, 1), "net_profit", 120.0),
        fact("balance", "2026-Q1", q1, ms(2026, 5, 1), "assets_total", 1000.0),
    ]
    grouped = group_periods(rows)

    period, facts, _ = select_common_period(grouped, ms(2026, 8, 15))   # 闸门前 1 天
    assert period == q1 and facts["assets_total"] == 1000.0
    period, facts, _ = select_common_period(grouped, ms(2026, 8, 16))   # 闸门当刻
    assert period == q2 and facts["assets_total"] == 1200.0


def test_prior_year_selection_is_gated_and_optional():
    q1_2025, q1_2026 = ms(2025, 3, 31), ms(2026, 3, 31)
    rows = [
        fact("income", "2026-Q1", q1_2026, ms(2026, 5, 1), "net_profit", 120.0),
        fact("balance", "2026-Q1", q1_2026, ms(2026, 5, 1), "assets_total", 1000.0),
        fact("income", "2025-Q1", q1_2025, ms(2025, 5, 1), "net_profit", 100.0),
        fact("balance", "2025-Q1", q1_2025, ms(2025, 5, 1), "assets_total", 900.0),
    ]
    grouped = group_periods(rows)

    previous = select_prior_year_facts(grouped, "2026-Q1", ms(2026, 9, 1), thscode="AAA.SZ")
    assert previous["period_key"] == "2025-Q1"
    assert previous["facts"] == {"net_profit": 100.0, "assets_total": 900.0}
    # 闸门之前取不到（那时 2025-Q1 还没披露）
    assert select_prior_year_facts(grouped, "2026-Q1", ms(2025, 4, 30)) is None
    # 上年同季不存在时返回 None，而不是拿别的期凑
    assert select_prior_year_facts(grouped, "2024-Q3", ms(2026, 9, 1)) is None
    assert prior_year_key("2026-Q2") == "2025-Q2"
    assert prior_year_key("乱写") is None


# --- 2. 方向与缺值 -----------------------------------------------------------

def test_factor_directions_match_the_preregistration():
    """方向是**登记时写死**的：杠杆越低越好，其余越高越好。写错会让 IC 符号全反。"""
    assert FACTOR_DIRECTIONS["assets_debt_ratio"] == "low"
    assert all(direction == "high" for factor, direction in FACTOR_DIRECTIONS.items()
               if factor != "assets_debt_ratio")
    assert FACTORS == tuple(FACTOR_DIRECTIONS)


def test_factor_values_are_none_not_zero_when_inputs_are_missing():
    current = {"net_profit": 100.0, "assets_total": 1000.0, "total_debt": 400.0,
               "operating_income": 500.0}          # 没有营业成本（银行）
    values = factor_values(current, None)

    assert values["roa"] == pytest.approx(0.1)
    assert values["assets_debt_ratio"] == pytest.approx(0.4)
    assert values["sale_gross_margin"] is None, "缺营业成本 → 算不出来，**不是 0**"
    # 没有上年同季 → F-Score 的两个因子都不给值（Δ 类判定需要对照期）
    assert values["f_score"] is None and values["f_score_ratio"] is None


def test_factor_values_compute_f_score_from_local_statements_only():
    current = {"net_profit": 120.0, "assets_total": 1000.0, "act_cash_flow_net": 150.0,
               "total_debt": 300.0, "operating_income": 600.0, "operating_costs": 300.0}
    previous = {"net_profit": 100.0, "assets_total": 900.0, "act_cash_flow_net": 90.0,
                "total_debt": 320.0, "operating_income": 500.0, "operating_costs": 280.0}
    values = factor_values(current, previous)

    assert values["f_score"] == 7.0, "本地可算的 7 项应全部通过"
    assert values["f_score_ratio"] == pytest.approx(1.0)


# --- 3. 声明的计数与统计量口径 -----------------------------------------------

def test_declared_comparison_count_is_frozen_in_the_code():
    """36 条 = 6 因子 × 2 口径 × 3 区间；这个数字进了登记条目，不能悄悄变。"""
    assert _declared_comparisons() == 36
    assert _declared_comparisons() == len(FACTORS) * 2 * 3


def test_series_stats_needs_at_least_three_dates_and_reports_t():
    sparse = _series_stats([0.1, 0.2], ["a", "b"], [], 200)
    assert sparse["t_stat"] is None and "样本不足" in sparse["verdict"]

    tight = _series_stats([0.1] * 5, list("abcde"), [], 200)
    assert tight["mean_ic"] == pytest.approx(0.1)
    assert tight["t_stat"] is None, "零方差时不给 t（不是给一个无穷大）"

    varied = _series_stats([0.1, -0.05, 0.08, 0.12, -0.02], list("abcde"), [], 200)
    assert varied["t_stat"] is not None and varied["dates"] == 5


def test_verdict_never_claims_significance_below_the_threshold():
    assert "噪声" in _verdict(1.2)
    assert "显著为负" in _verdict(-2.5)
    assert "≥ 1.96" in _verdict(3.5)
    assert "样本不足" in _verdict(None)


def test_period_mix_flags_non_uniform_report_periods():
    """报告期不统一（跨 YTD 窗口）时众数占比会掉下来，评价日因此被剔除。"""
    uniform = {"A": {"period_key": "2026-Q1"}, "B": {"period_key": "2026-Q1"}}
    mixed = {"A": {"period_key": "2026-Q1"}, "B": {"period_key": "2025-Q4"}}
    assert _period_mix(uniform)["modal_share"] == 1.0
    assert _period_mix(mixed)["modal_share"] == 0.5
    assert _period_mix(mixed)["distinct"] == 2


# --- 4. CLI 端到端（脚本必须真能跑起来）--------------------------------------

@pytest.fixture(scope="module")
def market(tmp_path_factory):
    """小夹具：20 只标的 × 90 根 K 线（每 4 天一根，2025 年内）+ 两个报告期的财务事实。

    时间轴必须与财务期**重叠**：否则既没有可用评估日，也测不到整条链路。
    """
    repository = WarehouseRepository(tmp_path_factory.mktemp("fin-ic") / "market.duckdb")
    base = ms(2025, 1, 5)
    db = repository._connect()
    try:
        db.execute("BEGIN")
        rows = []
        for position in range(20):
            symbol = f"{position:06d}.SZ"
            for step in range(1, 91):
                price = 10.0 * (1 + 0.001 * step * (1 + position % 3))
                rows.append((symbol, base + DAY * 4 * step, price, price * 1.01, price * 0.99,
                             price, 1e6, 1e6 * (position + 1), "none", "CNY"))
        db.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
    finally:
        db.close()
    repository.build_event_panel(force=True)
    for position in range(20):
        symbol = f"{position:06d}.SZ"
        for period, end, gate, scale in (("2025-Q1", ms(2025, 3, 31), ms(2025, 5, 1), 1.0),
                                         ("2024-Q1", ms(2024, 3, 31), ms(2024, 5, 1), 0.9)):
            facts = {"net_profit": 100.0 * scale, "assets_total": 1000.0,
                     "act_cash_flow_net": 120.0 * scale, "total_debt": 300.0,
                     "operating_income": 500.0 * scale, "operating_costs": 250.0 * scale}
            repository.write_financial_symbol(
                thscode=symbol, statement="income",
                rows=[{"thscode": symbol, "statement": "income", "period_key": period,
                       "period_end_ms": end, "field": field, "value": value, "unit": "CNY",
                       "currency": "CNY", "disclosed_at_ms": gate, "available_from_ms": gate,
                       "available_basis": "statutory_deadline",
                       "api_basis": "quarterly_cumulative_unknown", "source": "test",
                       "run_id": "fixture", "fetched_at": "2026-09-15T00:00:00+00:00"}
                      for field, value in facts.items()],
                ledger={"status": "available", "periods": 1, "fields": len(facts), "gaps": 0,
                        "requested_start_ms": ms(2024, 1, 1), "requested_end_ms": ms(2026, 9, 4),
                        "api_basis": "quarterly_cumulative_unknown", "error_code": None,
                        "error_message": None, "run_id": "fixture",
                        "fetched_at": "2026-09-15T00:00:00+00:00"})
    repository.write_financial_meta({"version": "financial_pit_v1", "run_id": "fixture",
                                     "universe": "[]"})
    return repository


def test_cli_runs_end_to_end_and_writes_a_report(market, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "load_settings",
        lambda: SimpleNamespace(market_warehouse_path=market.path, db_path=tmp_path / "db.sqlite"))
    out_dir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["run_financial_ic", "--draws", "5",
                                      "--out", str(out_dir)])
    assert cli.main() == 0
    output = capsys.readouterr().out

    import json

    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["design"].endswith("financial_factor_study_design@v1")
    assert set(report["factors"]) == set(FACTORS)
    for basis in ("next_open", "close_t"):
        multiplicity = report["bases"][basis]["multiplicity"]
        assert multiplicity["declared_comparisons"] == 36
        assert multiplicity["bonferroni_family_abs_t"] > 3.0
    assert "样本选择偏置" in output and "不得" in output
    assert "不构成投资建议" in output
