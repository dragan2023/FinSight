"""当日测试数据落盘的守卫测试（方案 00 §5）。

守四件事：

1. **三份产物的结构与内容**：`daily.md`（给人看）、`daily.jsonl`（逐条一行、带 `record_type`）、
   `batch.json`（审计三元组：`params_hash` / 数据快照 `sha256` / 模型配置 ID）；
2. **空样本必须如实写出来**，不能产出空文件让人以为坏了；
3. **范围正确**：按批次导出时不能混进别的批次的记录；
4. **绝不后台执行**：本模块与接口只允许被用户点击触发——用静态检查把"以后有人加个定时器"
   这件事挡住（红线：付费 API 不自动调用，导出同理）。

全部离线：临时 SQLite + 临时导出目录。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.app.advisor.ledger import AdvisorLedger
from backend.app.governance.service import GovernanceService
from backend.app.governance.test_run_export import (DISCLAIMER, SCHEMA_VERSION,
                                                    DailyExportService)
from backend.app.storage import Storage

THSCODE = "601799.SH"


@pytest.fixture
def storage(tmp_path):
    storage = Storage(tmp_path / "export.db")
    storage.initialize()
    return storage


@pytest.fixture
def exporter(storage, tmp_path):
    return DailyExportService(storage, tmp_path / "exports")


def _card() -> dict:
    return {"schema_version": "finsight.suggestion.v2", "thscode": THSCODE, "name": "星宇股份",
            "direction": "wait", "horizon": "days_20", "scenarios": [], "confidence": "very_low",
            "claims": [{"kind": "fact", "text": "已取得结构化证据。", "evidence_refs": ["A1"]}],
            "key_evidence": ["A1"], "counter_evidence": ["A1"], "strategy_refs": [],
            "confidence_basis": {}, "invalidations": ["实验失效即作废。"],
            "watchpoints": ["收盘价重新站上 MA20。"], "missing_for_decision": [],
            "decision_narrative": "落盘测试用卡。", "disclaimer_accepted": True}


def _insert_experiment(storage: Storage, entry_id: str, *, created_at: str) -> None:
    payload = {"metrics": {"cumulative_return_pct": -1.0}, "validation_reasons": ["演示原因"],
               "data_snapshot": {"basis": "corporate_action_total_return_v1", "bars": 1868,
                                 "start": "2019-01-02", "end": "2026-09-04", "sha256": "a" * 64}}
    with storage.connect() as db:
        db.execute("INSERT INTO strategy_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (entry_id, "trend_20_60_v1", "v2", THSCODE, "{}", "{}",
                    json.dumps(payload["data_snapshot"]), json.dumps(payload),
                    "insufficient", "hash-" + entry_id, created_at))


# --- 1) 结构与内容 ---------------------------------------------------------

def test_export_writes_three_files_with_the_expected_shapes(storage, exporter):
    GovernanceService(storage).open_batch(name="落盘测试批次")
    AdvisorLedger(storage).register(_card(), raw_verdict={"direction": "accumulate"})
    _insert_experiment(storage, "exp-1", created_at="2026-09-13T01:00:00+00:00")

    result = exporter.export(date="2026-09-13")

    names = {item["name"] for item in result["files"]}
    assert names == {"daily.md", "daily.jsonl", "batch.json"}
    directory = Path(result["directory"])
    assert directory.name == "2026-09-13"
    assert (directory / "daily.md").is_file()

    # 报告的字节数必须等于磁盘实际字节数（防止换行翻译等静默差异）
    for item in result["files"]:
        assert item["bytes"] == Path(item["path"]).stat().st_size, item["name"]

    # daily.md：人看的，必须带免责声明与常驻披露
    markdown = (directory / "daily.md").read_text(encoding="utf-8")
    assert DISCLAIMER in markdown
    assert "## 6. 常驻披露" in markdown
    assert "退市证券" in markdown

    # daily.jsonl：逐条一行，record_type 供统计脚本过滤
    lines = [json.loads(line) for line in
             (directory / "daily.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    kinds = [line["record_type"] for line in lines]
    assert kinds.count("advice") == 1
    assert kinds.count("strategy_experiment") == 1

    # batch.json：审计文档
    audit = json.loads((directory / "batch.json").read_text(encoding="utf-8"))
    assert audit["schema_version"] == SCHEMA_VERSION
    assert audit["generated_by"] == "user_clicked_export"
    assert audit["background_timer"] is False
    assert audit["batch"]["name"] == "落盘测试批次"


def test_batch_json_carries_the_audit_triple(storage, exporter):
    """审计三元组：params_hash、数据快照 sha256、模型配置 ID。"""
    GovernanceService(storage).open_batch(name="审计测试批次")
    AdvisorLedger(storage).register(_card(), model_profile_id="profile-42")
    _insert_experiment(storage, "exp-a", created_at="2026-09-13T01:00:00+00:00")

    exporter.export(date="2026-09-13")
    audit = json.loads((Path(exporter.list_runs()[0]["path"]) / "batch.json").read_text("utf-8"))

    assert audit["audit"]["model_profile_ids"] == ["profile-42"]
    assert any(item["sha256"] == "a" * 64 for item in audit["audit"]["data_snapshots"])
    assert len(audit["audit"]["params_hashes"]) >= 2  # 建议 + 实验
    assert audit["audit"]["data_snapshots"][0]["basis"] == "corporate_action_total_return_v1"


def test_export_records_governance_state(storage, exporter):
    governance = GovernanceService(storage)
    batch = governance.open_batch(name="治理状态批次")
    governance.record_change(field="demo/field", reason="演示变更")

    exporter.export(date="2026-09-13")
    audit = json.loads((Path(exporter.list_runs()[0]["path"]) / "batch.json").read_text("utf-8"))

    assert [item["field"] for item in audit["audit"]["rule_changes"]] == ["demo/field"]
    assert audit["batch"]["id"] != batch["id"]  # record_change 已开新批次
    assert audit["batch"]["stop_criteria"]  # G8 停止条件随之导出
    assert any(item["status"] == "closed" for item in audit["audit"]["batches"])


# --- 2) 空样本如实报告 -----------------------------------------------------

def test_empty_day_is_reported_honestly(storage, exporter):
    GovernanceService(storage).open_batch(name="空样本批次")

    result = exporter.export(date="2020-01-02")

    assert result["sample_is_empty"] is True
    directory = Path(result["directory"])
    markdown = (directory / "daily.md").read_text(encoding="utf-8")
    assert "本次导出没有当日数据" in markdown
    assert "不代表系统故障" in markdown
    # 空样本时治理状态仍然照常导出，便于确认链路与规则状态
    assert "## 5. 治理状态" in markdown
    assert "空样本批次" in markdown
    audit = json.loads((directory / "batch.json").read_text(encoding="utf-8"))
    assert audit["sample_is_empty"] is True
    assert audit["counts"] == {"advice": 0, "strategy_experiments": 0,
                               "track_settlements": 0, "research_predictions": 0}
    assert (directory / "daily.jsonl").read_text(encoding="utf-8") == ""


def test_export_uses_the_shanghai_calendar_day(storage, exporter):
    """created_at 是 UTC，但"当日"按 Asia/Shanghai 判定。"""
    GovernanceService(storage).open_batch(name="时区批次")
    # UTC 2026-09-13T20:00 == CST 2026-09-14 04:00 → 属于 09-14，不属于 09-13
    _insert_experiment(storage, "exp-late", created_at="2026-09-13T20:00:00+00:00")
    _insert_experiment(storage, "exp-early", created_at="2026-09-13T01:00:00+00:00")

    assert exporter.build(date="2026-09-13")["experiments"][0]["id"] == "exp-early"
    assert [row["id"] for row in exporter.build(date="2026-09-14")["experiments"]] == ["exp-late"]


# --- 3) 范围隔离 -----------------------------------------------------------

def test_batch_scoped_export_does_not_leak_other_batches(storage, exporter):
    governance = GovernanceService(storage)
    first = governance.open_batch(name="第一批次")
    advisor = AdvisorLedger(storage)
    advisor.register(_card())
    second = governance.record_change(field="x", reason="换批次")["new_batch"]
    advisor.register(_card())

    scoped_first = exporter.build(batch_id=first["id"])
    scoped_second = exporter.build(batch_id=second["id"])

    assert len(scoped_first["advice"]) == 1
    assert len(scoped_second["advice"]) == 1
    assert scoped_first["advice"][0]["id"] != scoped_second["advice"][0]["id"]
    assert all(row["batch_id"] == first["id"] for row in scoped_first["advice"])
    assert all(row["batch_id"] == second["id"] for row in scoped_second["advice"])


def test_unknown_batch_is_rejected(storage, exporter):
    from backend.app.errors import AppError

    with pytest.raises(AppError) as excinfo:
        exporter.build(batch_id="ffffffffffffffffffffffffffffffff")
    assert excinfo.value.category == "not_found"


# --- 4) 绝不后台执行 -------------------------------------------------------

def test_export_module_has_no_scheduler_or_threading():
    """静态检查：导出模块不得引入任何定时/后台执行手段。"""
    source = Path(__file__).resolve().parents[2] / "backend" / "app" / "governance" / \
        "test_run_export.py"
    text = source.read_text(encoding="utf-8")
    for forbidden in ("threading", "asyncio", "schedule", "Timer", "apscheduler", "cron"):
        assert not re.search(rf"\b{forbidden}\b", text), (
            f"导出模块出现了 {forbidden}：导出只能由用户点击触发（红线）")


def test_export_endpoint_is_user_triggered_only():
    """接口层面：只有 POST 会落盘；GET 只列出既有导出（只读）。"""
    source = Path(__file__).resolve().parents[2] / "backend" / "app" / "api" / "governance.py"
    text = source.read_text(encoding="utf-8")
    assert '@router.post("/test-run-export"' in text
    assert '@router.get("/test-run-exports"' in text
    # 导出服务只在 POST 里被调用一次
    assert text.count("export_service(request).export(") == 1
    for forbidden in ("threading", "asyncio", "BackgroundTasks", "schedule"):
        assert not re.search(rf"\b{forbidden}\b", text), f"治理接口出现了 {forbidden}"
