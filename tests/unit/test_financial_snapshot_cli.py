"""守卫：财务快照 CLI 必须能在小夹具上端到端跑完。

**为什么单独测一个脚本**（上一轮踩过的教训）：事件研究 CLI 重写时漏了 import，
脚本在**真实长跑**里才抛 `NameError`，而当时测试全绿——因为没有任何测试会去跑它。
这个 CLI 是"用户能自己敲一条命令看 F-Score"的唯一入口，必须有人盯着它能跑起来。

顺带钉住两件事：
1. 数据集版本不对时**拒绝运行**（不许拿旧口径的数据出结论）；
2. 算不出来的项必须显示为"算不出来"，**不得**显示成 0。
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.app.market_warehouse.financials import available_from_ms
from backend.app.market_warehouse.repository import WarehouseRepository

import scripts.financial_snapshot as cli  # noqa: E402

DAY = 86_400_000


def ms(year: int, month: int, day: int) -> int:
    from datetime import datetime, timedelta, timezone

    return int(datetime(year, month, day, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


def _rows(period: str, period_end: int, disclosed: int, values: dict) -> list[dict]:
    gate, basis = available_from_ms(period_end_ms=period_end, api_disclosed_ms=disclosed)
    return [{"thscode": "600519.SH", "statement": "income", "period_key": period,
             "period_end_ms": period_end, "field": field, "value": value,
             "unit": "CNY", "currency": "CNY", "disclosed_at_ms": disclosed,
             "available_from_ms": gate, "available_basis": basis,
             "api_basis": "quarterly_cumulative_unknown", "source": "test", "run_id": "r",
             "fetched_at": "2026-09-15T00:00:00+00:00"} for field, value in values.items()]


def _ledger() -> dict:
    return {"status": "available", "periods": 2, "fields": 8, "gaps": 0,
            "requested_start_ms": ms(2016, 1, 1), "requested_end_ms": ms(2026, 9, 4),
            "api_basis": "quarterly_cumulative_unknown", "error_code": None,
            "error_message": None, "run_id": "r", "fetched_at": "2026-09-15T00:00:00+00:00"}


@pytest.fixture
def cli_repository(tmp_path, monkeypatch):
    repository = WarehouseRepository(tmp_path / "market.duckdb")
    # 两年同季：2025-Q2 与 2026-Q2（后者接口日期可信 → 闸门 = 2026-08-16）
    values_now = {"net_profit": 120.0, "assets_total": 1000.0, "act_cash_flow_net": 150.0,
                  "total_debt": 300.0, "operating_income": 600.0, "operating_costs": 300.0}
    values_before = {"net_profit": 100.0, "assets_total": 900.0, "act_cash_flow_net": 90.0,
                     "total_debt": 320.0, "operating_income": 500.0, "operating_costs": 280.0}
    repository.write_financial_symbol(
        thscode="600519.SH", statement="income",
        rows=(_rows("2025-Q2", ms(2025, 6, 30), ms(2025, 8, 13), values_before)
              + _rows("2026-Q2", ms(2026, 6, 30), ms(2026, 8, 15), values_now)),
        ledger=_ledger())
    repository.write_financial_meta({"version": "financial_pit_v1", "run_id": "r",
                                     "universe": '["600519.SH"]'})
    monkeypatch.setattr(
        cli, "load_settings",
        lambda: SimpleNamespace(market_warehouse_path=repository.path, db_path=tmp_path / "db.sqlite"))
    return repository


def test_cli_prints_the_pit_snapshot_and_a_local_f_score(cli_repository, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["financial_snapshot", "--symbol", "600519.SH",
                                      "--as-of", "2026-09-04"])
    assert cli.main() == 0
    output = capsys.readouterr().out

    assert "2026-Q2" in output                       # 可见报告期
    assert "2025-Q2" in output                       # 上年同季（Δ 类信号的口径对齐）
    assert "api_report_date" in output               # 闸门采信来源必须写出来
    assert "F-Score" in output and "7/9" in output   # 本地可算 7 项
    assert "流动比率改善（本地方案不可算" in output      # 算不出来的项如实标注
    assert "不构成投资建议" in output


def test_cli_respects_the_pit_gate(cli_repository, monkeypatch, capsys):
    """时点早于 2026-Q2 的闸门 → 只能看到 2025-Q2（**不许**用未来的报表）。"""
    monkeypatch.setattr(sys, "argv", ["financial_snapshot", "--symbol", "600519.SH",
                                      "--as-of", "2026-08-01"])
    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "可见报告期：**2025-Q2**" in output
    assert "2026-Q2" not in output.split("可见报告期")[1].split("\n")[0]


def test_cli_refuses_when_the_dataset_version_is_stale(cli_repository, monkeypatch, capsys):
    cli_repository.write_financial_meta({"version": "financial_pit_v0"})
    monkeypatch.setattr(sys, "argv", ["financial_snapshot", "--symbol", "600519.SH"])
    assert cli.main() == 1
    assert "不可用" in capsys.readouterr().out


def test_cli_reports_missing_data_instead_of_faking_it(cli_repository, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["financial_snapshot", "--symbol", "000001.SZ",
                                      "--as-of", "2026-09-04"])
    assert cli.main() == 1
    assert "没有任何**已披露**的财务数据" in capsys.readouterr().out


def test_cli_json_mode_is_machine_readable(cli_repository, monkeypatch, capsys):
    import json

    monkeypatch.setattr(sys, "argv", ["financial_snapshot", "--symbol", "600519.SH",
                                      "--as-of", "2026-09-04", "--json"])
    assert cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current"]["period_key"] == "2026-Q2"
    assert payload["current"]["available_basis"] == "api_report_date"
    assert payload["local_ratios"]["roa"] == pytest.approx(0.12)
    assert payload["f_score"]["available_items"] == 7
