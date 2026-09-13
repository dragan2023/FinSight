"""P0-4 / P0-5 收尾的守卫测试。

守四件事：

1. **文档口径不再腐化**：把 `scripts/doc_lint.py` 的三项检查搬进 pytest，
   文档里引用的路径、方法卡版本号、说明书常量只要与代码不符就会红。
   （F10 的教训：11 条路径里 9 条不存在，而人工核对过一次之后又腐化了。）
2. **只读冻结数据集不可被导入**：`financial_statements` 按 D9 冻结，
   任何把它当可更新数据集的调用必须**抛错**，而不是靠读者记得。
3. **as-of 约束是可执行断言**：仓库层一旦返回越过评估时点的行情或除权事件，必须炸掉。
4. **门禁的 20bps 成本口径取测试段**（F15）：`grade(cost20_test=...)` 必须传**测试段**
   的 20bps 指标，而不是全周期指标。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.app.market_warehouse.repository import WarehouseRepository
from backend.app.market_warehouse.schema import FROZEN_READONLY_DATASETS

ROOT = Path(__file__).resolve().parents[2]


# --- 1) 文档口径 -----------------------------------------------------------

def test_documentation_paths_versions_and_constants_are_consistent(monkeypatch):
    """doc_lint 的三项检查必须零违规——这是 P0-5 的回归守卫。"""
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import doc_lint  # noqa: PLC0415

    markdown = sorted((ROOT / "docs").rglob("*.md"))
    files = [str(item.relative_to(ROOT)).replace("\\", "/")
             for item in ROOT.rglob("*") if item.is_file()
             and ".git/" not in str(item) and "node_modules" not in str(item)]

    violations, checked, _ = doc_lint.check_paths(markdown, files)
    versions = doc_lint.check_versions()
    constants = doc_lint.check_timeouts()
    navigation = doc_lint.check_navigation()

    assert checked > 40, "路径检查数量异常偏低，扫描逻辑可能坏了"
    assert violations == [], f"文档引用了不存在的路径：{violations[:5]}"
    assert versions == [], f"方法卡版本与 registry 不一致：{versions}"
    assert constants == [], f"说明书常量与代码不一致：{constants}"
    assert navigation == [], f"说明书导航清单与前端不一致：{navigation}"


# --- 2) 只读冻结数据集 -----------------------------------------------------

def test_frozen_dataset_rejects_import_validation(tmp_path):
    repository = WarehouseRepository(tmp_path / "market.duckdb")
    with pytest.raises(ValueError) as excinfo:
        repository.validate_parquet(tmp_path / "whatever.parquet", kind="financial_statements")
    message = str(excinfo.value)
    assert "只读冻结数据集" in message
    assert FROZEN_READONLY_DATASETS["financial_statements"]["decision"] in message
    assert "禁止增量更新" in message


# --- 3) as-of 断言 ---------------------------------------------------------

class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _StubConnection:
    """第 1 次 execute 返回行情行，第 2 次返回未匹配事件；其余返回空。"""

    def __init__(self, price_rows, unmatched):
        self._queue = [price_rows, unmatched]
        self._index = 0

    def execute(self, sql, params=None):
        if self._index < len(self._queue):
            rows = self._queue[self._index]
        else:
            rows = []
        self._index += 1
        return _StubResult(rows)

    def close(self):
        return None


def test_repository_asserts_no_price_row_beyond_as_of(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    path.write_bytes(b"")  # 只要 exists() 为真即可，连接被替换成桩
    repository = WarehouseRepository(path)
    as_of = 1_000_000
    # 一行合法的 (<= as_of) 与一行越界的。
    price_rows = [(as_of, 1.0, 1.0, 1.0, 0, 0.0, 0.0, 0.0, 0.0),
                  (as_of + 86_400_000, 1.0, 1.0, 1.0, 0, 0.0, 0.0, 0.0, 0.0)]
    monkeypatch.setattr("backend.app.market_warehouse.repository.duckdb.connect",
                        lambda *a, **k: _StubConnection(price_rows, []))

    with pytest.raises(ValueError) as excinfo:
        repository.corporate_action_total_return_history("601799.SH", 0, as_of)
    assert "未来信息泄漏" in str(excinfo.value)


def test_repository_asserts_no_unmatched_event_beyond_as_of(tmp_path, monkeypatch):
    path = tmp_path / "market.duckdb"
    path.write_bytes(b"")
    repository = WarehouseRepository(path)
    as_of = 1_000_000
    monkeypatch.setattr("backend.app.market_warehouse.repository.duckdb.connect",
                        lambda *a, **k: _StubConnection([], [(as_of + 86_400_000,)]))

    with pytest.raises(ValueError) as excinfo:
        repository.corporate_action_total_return_history("601799.SH", 0, as_of)
    assert "未匹配除权事件越过" in str(excinfo.value)


# --- 4) 门禁的 20bps 口径取测试段（F15）------------------------------------

def test_cost20_gate_uses_the_test_segment_not_the_full_period(monkeypatch):
    """F15：`grade(cost20_test=...)` 必须收到**测试段**的 20bps 指标。

    用一个可识别的哨兵值区分两种口径：全周期指标带 `full_period` 标记，
    测试段指标带 `test_segment` 标记。修好之后门禁只能看到后者。
    """
    import asyncio

    from backend.app.storage import Storage
    from backend.app.strategy import service as service_module
    from backend.app.strategy.models import StrategyExperimentInput

    storage = Storage(Path(__file__).parent / "_tmp_cost20.db")
    storage.initialize()
    try:
        captured: dict = {}

        def fake_grade(**kwargs):
            captured.update(kwargs)
            return "experimental", ["stub"]

        def fake_segments(strategy_id, bars, cost_bps, gate=None):
            marker = "test_segment" if cost_bps == 20 else "other"
            segment = {"segment": "test", "cumulative_return_pct": 1.0,
                       "max_drawdown_pct": -1.0, "benchmark_max_drawdown_pct": -2.0,
                       "trade_count": 10, "marker": marker}
            return [dict(segment, segment="development"), dict(segment, segment="validation"), segment]

        monkeypatch.setattr(service_module, "grade", fake_grade)
        monkeypatch.setattr(service_module, "segments", fake_segments)
        monkeypatch.setattr(service_module, "run_backtest",
                            lambda strategy_id, bars, cost_bps, gate=None: {"curve": [], "trades": []})
        monkeypatch.setattr(service_module, "metrics",
                            lambda curve, trades: {"marker": "full_period", "cumulative_return_pct": -99.0})
        monkeypatch.setattr(service_module, "current_signal_snapshot", lambda *a, **k: {})

        class _Market:
            def require_stock_identity(self, thscode):
                return None

            async def fetch_price_history(self, thscode, **kwargs):
                record = {"period_key": "2026-01-05",
                          "fields": {"close_price": {"value": 10.0},
                                     "open_price": {"value": 10.0}}}
                return type("R", (), {"records": [record], "evidence_ids": []})()

        service = service_module.StrategyService(storage, _Market())
        monkeypatch.setattr(service._governance, "assert_runnable",
                            lambda reference: {"status": "frozen"})
        payload = StrategyExperimentInput(strategy_id="trend_20_60_v1", thscode="601799.SH",
                                          start_ms=1, end_ms=2)
        asyncio.new_event_loop().run_until_complete(service.run(payload))

        assert captured["cost20_test"]["marker"] == "test_segment", (
            "门禁的 20bps 口径仍在用全周期指标（F15 未修）")
        assert captured["test"]["segment"] == "test"
    finally:
        storage.path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(storage.path) + suffix).unlink(missing_ok=True)
