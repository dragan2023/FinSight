"""C1 治理层守卫测试（方案 02 §6 的验收标准）。

守的是"规则在评估期内不可改写"这一**程序拒绝**能力。任何一条变红，都应先假设
治理被削弱，而不是测试过时。对应方案 02 §5 的 G1–G4：

- **G2 冻结有效**：对 frozen 假设、或 frozen 批次的成员假设改写定义，**必须失败**；
- **G3 变更即新批次**：变更自动关闭旧批次并开启新批次，且留痕；
- **G4 批次隔离**：跨批次合并统计默认被拒绝，显式确认后也标记为不得用于判定；
- **G1 预注册门禁**：未登记/未冻结的策略跑不了实验；
- **审计完备**：每条建议必须带 `batch_id`，没有进行中的批次就拒绝登记。

全部离线：临时 SQLite 建在 tmp_path 下，不碰正式业务库、不访问网络。
"""

from __future__ import annotations

import sqlite3

import pytest

from backend.app.advisor.ledger import AdvisorLedger
from backend.app.errors import AppError
from backend.app.governance.service import GovernanceService
from backend.app.storage import Storage


@pytest.fixture
def storage(tmp_path):
    storage = Storage(tmp_path / "governance.db")
    storage.initialize()
    storage.initialize()  # 幂等
    return storage


@pytest.fixture
def governance(storage):
    return GovernanceService(storage)


def _register(governance, *, reference="demo_v1@v1", freeze=True, **kwargs):
    return governance.register_hypothesis(
        kind=kwargs.pop("kind", "strategy"), name=kwargs.pop("name", "演示策略"),
        reference=reference, definition={"rule": "close > ma20"}, freeze=freeze, **kwargs)


# --- G2 冻结有效 -----------------------------------------------------------

def test_frozen_hypothesis_definition_cannot_be_updated(storage, governance):
    hypothesis = _register(governance)
    assert hypothesis["status"] == "frozen"

    with pytest.raises(sqlite3.IntegrityError, match="hypothesis_frozen_immutable"):
        with storage.connect() as db:
            db.execute("UPDATE hypothesis_registry SET definition_json=? WHERE id=?",
                       ('{"rule": "close > ma5"}', hypothesis["id"]))

    with pytest.raises(sqlite3.IntegrityError, match="hypothesis_frozen_immutable"):
        with storage.connect() as db:
            db.execute("UPDATE hypothesis_registry SET params_hash=? WHERE id=?",
                       ("deadbeef", hypothesis["id"]))


def test_frozen_hypothesis_definition_is_refused_with_a_clear_service_error(governance):
    """人看的拒绝：服务层给出可操作的原因（去登记新 reference），而不是原始 sqlite 异常。"""
    hypothesis = _register(governance)
    with pytest.raises(AppError) as excinfo:
        governance.amend_definition(hypothesis["id"], definition={"rule": "close > ma5"})
    assert excinfo.value.category == "governance_rule_frozen"
    assert "新的 reference" in excinfo.value.message
    assert governance.get_hypothesis(hypothesis["id"])["definition_json"] == '{"rule": "close > ma20"}'


def test_draft_hypothesis_definition_is_amendable_through_the_service(governance):
    draft = _register(governance, reference="amend_v1@v1", freeze=False)
    amended = governance.amend_definition(draft["id"], definition={"rule": "close > ma5"},
                                          params={"window": 5})
    assert amended["definition_json"] == '{"rule": "close > ma5"}'
    assert amended["params_hash"] != draft["params_hash"]


def test_frozen_hypothesis_cannot_be_deleted(storage, governance):
    hypothesis = _register(governance)
    with pytest.raises(sqlite3.IntegrityError, match="hypothesis_frozen_no_delete"):
        with storage.connect() as db:
            db.execute("DELETE FROM hypothesis_registry WHERE id=?", (hypothesis["id"],))


def test_draft_hypothesis_is_editable_but_frozen_is_not(storage, governance):
    draft = _register(governance, reference="draft_v1@v1", freeze=False)
    with storage.connect() as db:
        db.execute("UPDATE hypothesis_registry SET definition_json=? WHERE id=?",
                   ('{"rule": "close > ma5"}', draft["id"]))
    assert governance.freeze(draft["id"])["status"] == "frozen"
    with pytest.raises(sqlite3.IntegrityError):
        with storage.connect() as db:
            db.execute("UPDATE hypothesis_registry SET definition_json=? WHERE id=?",
                       ('{"rule": "又改了"}', draft["id"]))


def test_lifecycle_change_to_retired_is_allowed_while_frozen(governance):
    hypothesis = _register(governance)
    assert governance.retire(hypothesis["id"])["status"] == "retired"
    assert governance.is_runnable(hypothesis["reference"]) is False


def test_frozen_batch_blocks_member_hypothesis_updates(storage, governance):
    """方案 02 §4 约束 1：frozen 批次的成员假设定义不可更新。"""
    hypothesis = _register(governance, reference="member_v1@v1", freeze=False)
    batch = governance.open_batch(name="冻结批次")
    governance.add_member(batch_id=batch["id"], artifact_kind="hypothesis",
                         artifact_id=hypothesis["id"])
    governance.freeze_batch(batch["id"])

    with pytest.raises(sqlite3.IntegrityError, match="batch_frozen_immutable"):
        with storage.connect() as db:
            db.execute("UPDATE hypothesis_registry SET definition_json=? WHERE id=?",
                       ('{"rule": "改了"}', hypothesis["id"]))


# --- G1 预注册门禁 ---------------------------------------------------------

def test_unregistered_strategy_is_not_runnable(governance):
    assert governance.is_runnable("nope_v1@v1") is False
    with pytest.raises(AppError) as excinfo:
        governance.assert_runnable("nope_v1@v1")
    assert excinfo.value.category == "governance_not_registered"


def test_draft_hypothesis_is_not_runnable(governance):
    _register(governance, reference="draft2_v1@v1", freeze=False)
    with pytest.raises(AppError) as excinfo:
        governance.assert_runnable("draft2_v1@v1")
    assert excinfo.value.category == "governance_not_frozen"


def test_duplicate_reference_is_rejected(governance):
    _register(governance, reference="dup_v1@v1")
    with pytest.raises(AppError) as excinfo:
        _register(governance, reference="dup_v1@v1")
    assert excinfo.value.category == "governance_duplicate"


# --- G3 变更即新批次 -------------------------------------------------------

def test_rule_change_closes_old_batch_and_opens_new_one(governance):
    old = governance.open_batch(name="初始批次", plan={"exploratory": True})
    result = governance.record_change(field="strategy/trend_20_60_v1@v2",
                                      reason="成本口径由全周期改为测试段", old_hash="a" * 16,
                                      new_hash="b" * 16)

    assert result["previous_batch"]["id"] == old["id"]
    assert governance.get_batch(old["id"])["status"] == "closed"
    assert governance.get_batch(old["id"])["superseded_by"] == result["new_batch"]["id"]
    assert governance.get_batch(result["new_batch"]["id"])["status"] == "open"
    assert governance.current_batch_id() == result["new_batch"]["id"]

    changes = governance.list_changes(batch_id=old["id"])
    assert len(changes) == 1
    assert changes[0]["forced_new_batch_id"] == result["new_batch"]["id"]
    assert changes[0]["old_hash"] == "a" * 16


def test_rule_change_inherits_stop_criteria_so_it_cannot_be_quietly_relaxed(governance):
    old = governance.open_batch(name="初始批次")
    result = governance.record_change(field="x", reason="y")
    assert (result["new_batch"]["stop_criteria_json"] == old["stop_criteria_json"])
    assert "settled_30" in result["new_batch"]["stop_criteria_json"]


def test_rule_change_without_open_batch_still_opens_one(governance):
    result = governance.record_change(field="x", reason="首次变更")
    assert result["previous_batch"] is None
    assert result["new_batch"]["status"] == "open"
    assert governance.list_changes()[0]["batch_id"] is None


def test_cannot_open_two_batches_at_once(governance):
    governance.open_batch(name="批次一")
    with pytest.raises(AppError) as excinfo:
        governance.open_batch(name="批次二")
    assert excinfo.value.category == "governance_batch_open"


def test_closed_batch_plan_is_immutable(governance):
    batch = governance.open_batch(name="待关闭", plan={"exploratory": True, "universe": "all"})
    governance.close_batch(batch["id"], verdict={"verdict": "无效"})
    with pytest.raises(sqlite3.IntegrityError, match="batch_closed_immutable"):
        with governance._storage.connect() as db:  # noqa: SLF001 - 直接验证触发器
            db.execute("UPDATE evaluation_batches SET plan_json=? WHERE id=?",
                       ('{"universe": "top200"}', batch["id"]))


# --- G4 批次隔离 -----------------------------------------------------------

def test_cross_batch_statistics_is_refused_without_explicit_acknowledgement(governance):
    first = governance.open_batch(name="批次一")
    governance.add_member(batch_id=first["id"], artifact_kind="scan", artifact_id="scan-1")
    second = governance.record_change(field="x", reason="变更")["new_batch"]

    with pytest.raises(AppError) as excinfo:
        governance.combined_statistics([first["id"], second["id"]])
    assert excinfo.value.category == "governance_cross_batch"

    merged = governance.combined_statistics([first["id"], second["id"]],
                                            acknowledge_cross_batch=True)
    assert merged["cross_batch"] is True
    assert merged["usable_for_verdict"] is False  # 显式标注后仍不得用于判定


def test_batch_statistics_are_per_batch_only(storage, governance):
    first = governance.open_batch(name="批次一")
    with storage.connect() as db:
        db.execute("INSERT INTO strategy_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("exp-1", "demo_v1", "v1", "601799.SH", "{}", "{}", "{}", "{}",
                    "candidate", "hash", "2026-09-13T00:00:00+00:00"))
        db.execute("INSERT INTO strategy_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   ("exp-2", "demo_v1", "v1", "600519.SH", "{}", "{}", "{}", "{}",
                    "insufficient", "hash", "2026-09-13T00:00:00+00:00"))
    governance.add_member(batch_id=first["id"], artifact_kind="experiment", artifact_id="exp-1")
    governance.add_member(batch_id=first["id"], artifact_kind="experiment", artifact_id="exp-2")

    stats = governance.batch_statistics(first["id"])
    assert stats["cross_batch"] is False and stats["usable_for_verdict"] is True
    assert stats["experiment_grades"] == {"candidate": 1, "insufficient": 1}
    assert stats["member_counts"]["experiment"] == 2


# --- G7 审计完备：建议必须落在批次里 ---------------------------------------

def _card() -> dict:
    return {"schema_version": "finsight.suggestion.v2", "thscode": "601799.SH",
            "name": "星宇股份", "direction": "wait", "horizon": "days_20",
            "scenarios": [], "confidence": "very_low", "claims": [], "key_evidence": [],
            "counter_evidence": ["A1"], "strategy_refs": [], "confidence_basis": {},
            "invalidations": ["实验失效即作废。"], "watchpoints": ["收盘价重新站上 MA20。"],
            "missing_for_decision": [], "decision_narrative": "治理测试用卡。",
            "disclaimer_accepted": True}


def test_advice_registration_is_refused_without_an_open_batch(storage):
    ledger = AdvisorLedger(storage)
    with pytest.raises(AppError) as excinfo:
        ledger.register(_card())
    assert excinfo.value.category == "governance_no_batch"


def test_advice_registration_records_batch_id_and_becomes_a_batch_member(storage, governance):
    batch = governance.open_batch(name="建议批次")
    entry = AdvisorLedger(storage).register(_card())

    assert entry["batch_id"] == batch["id"]
    members = governance.members(batch["id"], artifact_kind="advice")
    assert [item["artifact_id"] for item in members] == [entry["id"]]


def test_strategy_experiment_is_refused_before_registration(storage):
    """G1：未预注册的策略在**取数之前**就被拒绝（不给付费/外部调用留口子）。"""
    from backend.app.strategy.service import StrategyService

    class _ExplodingMarket:
        def require_stock_identity(self, thscode):
            raise AssertionError("门禁未生效：未登记的策略竟然走到了取数环节")

    service = StrategyService(storage, _ExplodingMarket())
    with pytest.raises(AppError) as excinfo:
        import asyncio

        from backend.app.strategy.models import StrategyExperimentInput

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            service.run(StrategyExperimentInput(strategy_id="trend_20_60_v1",
                                                thscode="601799.SH",
                                                start_ms=1, end_ms=2)))
    assert excinfo.value.category == "governance_not_registered"
