"""守卫：事件研究 CLI 必须能在小夹具上**端到端跑完**（含三条口径并排）。

**为什么需要它**（实测踩过，2026-09-15）：重写 `run_limit_up_event_study.py` 的输出段时
漏掉了 `from backend.app.settings import load_settings`，脚本在**真实长跑**里才抛
`NameError`——而当时的测试**全绿**：没有任何一条测试会去跑这个脚本。CLI 是本轮交付的
主要入口，"它能不能跑起来"必须有测试盯着，否则只能靠"手工跑一次真实任务"来发现。

同类漏洞一次覆盖：漏 import、参数拼错、输出文件名/汇总表崩掉、口径循环没生效。
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from backend.app.market_warehouse.repository import WarehouseRepository

import scripts.run_limit_up_event_study as runner  # noqa: E402

DAY_MS = 86_400_000
EVENT_DAY = DAY_MS * 10
SYMBOLS = [f"{index:06d}.SZ" for index in range(30)]
#: 涨停的 6 只分散在不同成交额档位——全落在同一档会让当天没有对照，事件被跳过。
POOL = [SYMBOLS[index] for index in (1, 6, 11, 16, 21, 26)]
SEAL_MONEY = [1.0e6, 2.0e6, 3.0e6, 4.0e6, 5.0e6, 6.0e6]
CONTINUE_DAYS = [1, 1, 1, 2, 2, 3]
#: 事件日排在第 10 根；面板只支持 20 日前瞻期，所以至少要有 30 根。
BARS = 30


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    """**模块级**夹具：本机 DuckDB 参数化插入约 2–7 毫秒/行，每个测试各建一次会把
    这个文件拖到一分钟以上。这些数据只读，共用一份没有副作用。"""
    repository = WarehouseRepository(tmp_path_factory.mktemp("cli") / "market.duckdb")
    db = repository._connect()
    try:
        db.execute("BEGIN")
        db.executemany(
            "INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(symbol, DAY_MS * step, price, price * 1.01, price * 0.99, price,
              1_000_000.0, 1.0e6 * (position + 1), "none", "CNY")
             for position, symbol in enumerate(SYMBOLS)
             for step in range(1, BARS + 1)
             for price in (10.0 * (1 + 0.002 * (1 + (position % 5) * 0.5) * step),)])
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
    return repository


@pytest.fixture
def cli_repository(market, monkeypatch):
    # CLI 通过 `load_settings()` 找行情库；测试里把它焊到临时库上。
    # 注意：`monkeypatch.setattr` 默认要求属性**存在**，所以这行同时也守卫"脚本确实
    # import 了 load_settings"——漏掉 import 时这里会直接报 AttributeError。
    monkeypatch.setattr(runner, "load_settings",
                        lambda: SimpleNamespace(market_warehouse_path=market.path))
    return market


def test_cli_runs_all_three_bases_end_to_end(cli_repository, tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "run_limit_up_event_study", "--field", "seal_ratio", "--draws", "3",
        "--return-basis", "all", "--family-prior-statistics", "seal_ratio=63",
        "--out", str(out_dir)])

    assert runner.main() == 0
    output = capsys.readouterr().out

    # 三条口径都要真的跑过，并且并排汇总出现过
    for basis in ("close_t", "next_open", "next_intraday"):
        assert f"结果口径 {basis}" in output, f"口径 {basis} 没有被执行"
    # 落盘名：既有口径保持向后兼容（`<field>.json`），新口径自带后缀
    assert (out_dir / "seal_ratio.json").exists(), "既有口径的落盘名必须保持向后兼容"
    for basis in ("next_open", "next_intraday"):
        assert (out_dir / f"seal_ratio.{basis}.json").exists(), f"{basis} 没有落盘"
    assert "三口径并排" in output
    # 族级计数按字段生效（而不是被当成裸整数套到所有字段）
    assert "族 limit_up_seal_strength 此前 63 条" in output
    report = json.loads((out_dir / "seal_ratio.next_open.json").read_text(encoding="utf-8"))
    multiplicity = report["multiplicity"]
    assert multiplicity["family"] == "limit_up_seal_strength"
    assert multiplicity["family_prior_statistics"] == 63
    assert (multiplicity["family_total_statistics"]
            == multiplicity["statistics_tested"] + 63)
    assert report["outcome_basis"]["id"] == "next_open"


def test_cli_accepts_a_bare_integer_family_prior_with_a_warning(cli_repository, tmp_path,
                                                               monkeypatch, capsys):
    """裸整数仍然可用（向后兼容），但多字段同跑要**明确警告**门槛会被算松。"""
    monkeypatch.setattr(sys, "argv", [
        "run_limit_up_event_study", "--field", "all", "--draws", "3",
        "--family-prior-statistics", "20", "--out", str(tmp_path / "out2")])

    assert runner.main() == 0
    assert "将套用到全部" in capsys.readouterr().out


def test_cli_refuses_an_unknown_basis_without_writing_anything(cli_repository, tmp_path,
                                                               monkeypatch, capsys):
    """拼错口径必须**非零退出且不落盘**——不许悄悄回落到 `close_t`。"""
    out_dir = tmp_path / "out3"
    monkeypatch.setattr(sys, "argv", [
        "run_limit_up_event_study", "--field", "seal_ratio", "--draws", "3",
        "--return-basis", "next-open", "--out", str(out_dir)])

    assert runner.main() == 2
    assert "未知口径" in capsys.readouterr().out
    assert not out_dir.exists()
