"""「财务质量 × 涨停事件」外部字段机制的守卫测试（`24` 号文档 / D68）。

盯三件事：

1. **注册表不变量**——外部字段必须能被校验与查表（在 `KNOWN_FIELDS` 里），
   但**不得**混进 `ALL_FIELDS`：否则 `--field all` 会在没有 provider 时把它们也跑一遍，
   产出一份"看着有结果"的空报告（踩过同类：`17` §4.3 的族级计数被全局参数算松）；
2. **没 provider 就不给值**——外部字段取不到值时必须**留空**，绝不能拿池内均值或 0 顶替
   （那会把"没有财务数据"变成"低 F-Score"混进低组，直接把结论做出来）；
3. **provider 的 as-of 纪律**——闸门之前取不到、之后才取到，并缓存整份快照。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.app.market_warehouse.repository import WarehouseRepository
from backend.app.screener.events import (
    ALL_FIELDS,
    EXTERNAL_FIELDS,
    FIELD_FAMILIES,
    KNOWN_FIELDS,
    LimitUpEventStudy,
)
from backend.app.screener.financial_ic import FinancialFactorProvider

import scripts.run_financial_interaction as cli  # noqa: E402

DAY = 86_400_000


def ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=timezone(timedelta(hours=8))).timestamp() * 1000)


# --- 1. 注册表不变量 ---------------------------------------------------------

def test_external_fields_are_known_but_never_in_all_fields():
    assert set(EXTERNAL_FIELDS) <= set(KNOWN_FIELDS)
    assert not (set(EXTERNAL_FIELDS) & set(ALL_FIELDS)), \
        "外部字段不能进 ALL_FIELDS，否则 --field all 会产出没有 provider 的空报告"
    for field in EXTERNAL_FIELDS:
        assert field in FIELD_FAMILIES, "外部字段同样必须声明统计族"
        assert EXTERNAL_FIELDS[field]["rule"] is not None


def test_external_field_without_provider_yields_no_values():
    """没有 provider → 一个值都不给（**不是**给 0、也不是给池内均值）。"""
    pool = {"AAA.SZ": {"thscode": "AAA.SZ", "date_ms": ms(2026, 6, 30), "seal_money": 1.0}}
    assert LimitUpEventStudy._field_values("f_score", pool, {}) == {}


def test_external_field_uses_the_provider_and_drops_missing_values():
    pool = {
        "AAA.SZ": {"thscode": "AAA.SZ", "date_ms": ms(2026, 6, 30)},
        "BBB.SZ": {"thscode": "BBB.SZ", "date_ms": ms(2026, 6, 30)},
    }

    def provider(field, symbol, date_ms):
        assert field == "f_score" and date_ms == ms(2026, 6, 30)
        return {"AAA.SZ": 5.0}.get(symbol)          # BBB 没有值

    assert LimitUpEventStudy._field_values("f_score", pool, {}, provider) == {"AAA.SZ": 5.0}


def test_static_field_values_still_work_without_a_provider():
    """既有调用方式（静态、三参数）必须保持不变——既有测试与调用方都依赖它。"""
    pool = {"AAA.SZ": {"seal_ratio": 0.5}, "BBB.SZ": {"seal_ratio": None}}
    assert LimitUpEventStudy._field_values("seal_ratio", pool, {}) == {}
    pool_with_row = {"AAA.SZ": {"seal_money": 3.0}}
    assert LimitUpEventStudy._field_values(
        "seal_ratio", pool_with_row, {"AAA.SZ": {"turnover_today": 6.0}}) == {"AAA.SZ": 0.5}


# --- 2. provider 的 as-of 纪律 ------------------------------------------------

@pytest.fixture(scope="module")
def repository(tmp_path_factory):
    repository = WarehouseRepository(tmp_path_factory.mktemp("interaction") / "market.duckdb")
    facts = {"net_profit": 120.0, "assets_total": 1000.0, "act_cash_flow_net": 150.0,
             "total_debt": 300.0, "operating_income": 600.0, "operating_costs": 300.0}
    earlier = {"net_profit": 100.0, "assets_total": 900.0, "act_cash_flow_net": 90.0,
               "total_debt": 320.0, "operating_income": 500.0, "operating_costs": 280.0}
    rows = []
    for period, end, gate, values in (("2026-Q1", ms(2026, 3, 31), ms(2026, 5, 1), facts),
                                      ("2025-Q1", ms(2025, 3, 31), ms(2025, 5, 1), earlier)):
        rows.extend({"thscode": "AAA.SZ", "statement": "income", "period_key": period,
                     "period_end_ms": end, "field": field, "value": value, "unit": "CNY",
                     "currency": "CNY", "disclosed_at_ms": gate, "available_from_ms": gate,
                     "available_basis": "statutory_deadline",
                     "api_basis": "quarterly_cumulative_unknown", "source": "test",
                     "run_id": "fixture", "fetched_at": "2026-09-15T00:00:00+00:00"}
                    for field, value in values.items())
    repository.write_financial_symbol(
        thscode="AAA.SZ", statement="income", rows=rows,
        ledger={"status": "available", "periods": 2, "fields": len(rows), "gaps": 0,
                "requested_start_ms": ms(2025, 1, 1), "requested_end_ms": ms(2026, 9, 4),
                "api_basis": "quarterly_cumulative_unknown", "error_code": None,
                "error_message": None, "run_id": "fixture",
                "fetched_at": "2026-09-15T00:00:00+00:00"})
    repository.write_financial_meta({"version": "financial_pit_v1", "run_id": "fixture"})
    return repository


def test_provider_respects_the_point_in_time_gate(repository):
    provider = FinancialFactorProvider(repository)

    assert provider("f_score", "AAA.SZ", ms(2026, 4, 30)) is None, \
        "2026-Q1 的闸门是 5 月 1 日，4 月 30 日取不到（不许前视）"
    assert provider("f_score", "AAA.SZ", ms(2026, 9, 4)) == 7.0, "两家同季齐备 → 本地 7 项全过"
    # 未知标的与缺日期都不给值
    assert provider("f_score", "ZZZ.SZ", ms(2026, 9, 4)) is None
    assert provider("f_score", "AAA.SZ", None) is None
    assert provider("roa", "AAA.SZ", ms(2026, 9, 4)) == pytest.approx(0.12), \
        "provider 对任意已声明因子都可用（不止 f_score）"


def test_provider_caches_the_whole_snapshot_per_symbol_and_date(repository):
    provider = FinancialFactorProvider(repository)
    before = provider.cache_size
    provider("f_score", "AAA.SZ", ms(2026, 9, 4))
    after_first = provider.cache_size
    provider("roa", "AAA.SZ", ms(2026, 9, 4))       # 同一 (标的, 日期)
    assert provider.cache_size == after_first == before + 1, \
        "同一 (标的, 日期) 只应算一次快照（按字段缓存会让快照被重算好几遍）"


# --- 3. CLI 接线（轻量冒烟：没有涨停池时必须明确拒绝） -------------------------

def test_cli_refuses_without_a_limit_up_pool(repository, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "load_settings",
        lambda: SimpleNamespace(market_warehouse_path=repository.path,
                                db_path=repository.path.parent / "db.sqlite"))
    monkeypatch.setattr(sys, "argv", ["run_financial_interaction", "--draws", "5"])
    assert cli.main() == 1
    assert "sync_limit_pools" in capsys.readouterr().out
