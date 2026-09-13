"""C1 治理层服务：把「规则在评估期内不可改写」从文档变成程序拒绝。

对应方案 02 §5 的 G1–G4：

| # | 机制 | 本模块的落点 |
|---|---|---|
| G1 | 预注册 | `assert_runnable()`：未登记或未冻结的假设不得进入候选生成 |
| G2 | 评估期冻结 | `freeze()` + `set_hypothesis_status()`；**冻结后的定义列由 SQLite 触发器拒绝改写** |
| G3 | 变更即新批次 | `record_change()`：关旧批次 → 开新批次 → 留痕，同一事务完成 |
| G4 | 批次隔离 | `batch_statistics()` / `combined_statistics()`：跨批次合并必须显式确认，且**不得用于判定** |

设计取舍（有意的）：`record_change()` 打开的新批次**默认继承**旧批次的计划与停止条件。
理由：让"改规则"这件事**不可能顺带放松停止条件**——想放松就必须在新批次里显式写出来，
那本身就是一条可审计的记录。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from ..errors import AppError
from ..storage import Storage
from .repository import GovernanceRepository

HYPOTHESIS_KINDS = ("strategy", "factor", "rule", "scan")
ARTIFACT_KINDS = ("advice", "prediction", "experiment", "scan", "hypothesis")

#: 方案 02 §5 的 G8 停止条件——**批次开始前**写死，达标即停，不延长也不降标准。
DEFAULT_STOP_CRITERIA = {
    "day_10": "无任何 trial_buy_signal，或关键工具频繁失败 → 当前版本不可用",
    "settled_30": "方向命中率 95% 置信区间包含 50% → 无方向优势",
    "settled_60_weeks_12": "未优于简单基准，或真实净收益为负 → 正式停用",
    "ai_track_worse": "AI 裁决轨显著劣于策略轨 → 移除 LLM 的方向权限（退化为纯解释）",
    "drift": "走查/实盘与回测偏离超阈值 → 暂停并排查实现差异",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def params_hash(params: dict | None) -> str:
    payload = json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class GovernanceService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage
        self._repo = GovernanceRepository(storage)

    # --- G1 预注册 --------------------------------------------------------
    def register_hypothesis(self, *, kind: str, name: str, reference: str,
                            definition: dict | None = None, direction: str = "",
                            params: dict | None = None, exploratory: bool = False,
                            trial_count: int = 1, notes: str = "",
                            freeze: bool = True) -> dict:
        """登记一条假设。`reference` 必须唯一（策略形如 `trend_20_60_v1@v2`）。

        默认**立刻冻结**：登记即进入评估期，此后定义不可改写（方案 A.1：
        「登记后不得改名或改定义——变更视为新因子」）。
        """
        if kind not in HYPOTHESIS_KINDS:
            raise AppError("governance_invalid", f"不支持的假设类型：{kind}", 422)
        if not reference.strip() or not name.strip():
            raise AppError("governance_invalid", "假设的 reference 与 name 不可为空。", 422)
        if self._repo.get_hypothesis_by_reference(reference) is not None:
            raise AppError("governance_duplicate",
                           f"假设 {reference} 已登记。改定义必须登记为新 reference。", 409)
        now = _now_iso()
        row = {"id": uuid4().hex, "reference": reference, "kind": kind, "name": name,
               "definition_json": json.dumps(definition or {}, ensure_ascii=False, sort_keys=True),
               "direction": direction,
               "params_json": json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
               "params_hash": params_hash(params), "submitted_at": now,
               "frozen_at": now if freeze else None,
               "status": "frozen" if freeze else "draft",
               "exploratory": exploratory, "trial_count": max(1, int(trial_count)), "notes": notes}
        self._repo.insert_hypothesis(row)
        return self.get_hypothesis(row["id"])

    def get_hypothesis(self, hypothesis_id: str) -> dict:
        row = self._repo.get_hypothesis(hypothesis_id)
        if row is None:
            raise AppError("not_found", "假设不存在。", 404)
        return row

    def list_hypotheses(self, *, status: str | None = None) -> list[dict]:
        return self._repo.list_hypotheses(status=status)

    def find_by_reference(self, reference: str) -> dict | None:
        return self._repo.get_hypothesis_by_reference(reference)

    def freeze(self, hypothesis_id: str) -> dict:
        row = self.get_hypothesis(hypothesis_id)
        if row["status"] == "retired":
            raise AppError("governance_invalid", "已退役的假设不可再冻结。", 422)
        if row["status"] != "frozen":
            self._repo.set_hypothesis_status(hypothesis_id, status="frozen", frozen_at=_now_iso())
        return self.get_hypothesis(hypothesis_id)

    def retire(self, hypothesis_id: str) -> dict:
        """退役是**生命周期**变更，不改定义，因此不触发冻结守卫、也不开新批次。"""
        row = self.get_hypothesis(hypothesis_id)
        if row["status"] != "retired":
            self._repo.set_hypothesis_status(hypothesis_id, status="retired")
        return self.get_hypothesis(hypothesis_id)

    def amend_definition(self, hypothesis_id: str, *, definition: dict | None = None,
                         params: dict | None = None) -> dict:
        """改写定义。**只对 draft 合法**。

        冻结后不接受任何形式的"就地修改"——唯一的合法路径是登记一个**新的 reference**
        （方案 A.1：「登记后不得改名或改定义——变更视为新因子」），再用 `record_change()`
        开启新批次。触发器是这条规则的最后一道防线，本方法是给人看的明确拒绝。
        """
        row = self.get_hypothesis(hypothesis_id)
        if row["status"] != "draft":
            raise AppError(
                "governance_rule_frozen",
                f"{row['reference']} 当前状态为 {row['status']}，定义与参数不可改写。"
                "变更必须登记为新的 reference，并用 record_change 开启新批次。", 409)
        definition_json = json.dumps(
            definition if definition is not None else json.loads(row["definition_json"]),
            ensure_ascii=False, sort_keys=True)
        params_json = json.dumps(
            params if params is not None else json.loads(row["params_json"]),
            ensure_ascii=False, sort_keys=True)
        merged_params = params if params is not None else json.loads(row["params_json"])
        self._repo.update_hypothesis_definition(
            hypothesis_id, definition_json=definition_json, params_json=params_json,
            params_hash=params_hash(merged_params))
        return self.get_hypothesis(hypothesis_id)

    # --- G1 门禁 ----------------------------------------------------------
    def is_runnable(self, reference: str) -> bool:
        row = self._repo.get_hypothesis_by_reference(reference)
        return bool(row and row["status"] == "frozen")

    def assert_runnable(self, reference: str) -> dict:
        row = self._repo.get_hypothesis_by_reference(reference)
        if row is None:
            raise AppError(
                "governance_not_registered",
                f"{reference} 尚未预注册，不得进入候选生成。请先在治理层登记（方案 G1）。", 409)
        if row["status"] != "frozen":
            raise AppError(
                "governance_not_frozen",
                f"{reference} 当前状态为 {row['status']}，只有 frozen 的假设可进入候选生成。", 409)
        return row

    # --- 评估批次 ---------------------------------------------------------
    def open_batch(self, *, name: str, plan: dict | None = None,
                   stop_criteria: dict | None = None, exploratory: bool = False) -> dict:
        if self._repo.open_batch() is not None:
            raise AppError("governance_batch_open",
                           "已有进行中的评估批次；请先关闭它，或用 record_change 走变更流程。", 409)
        row = {"id": uuid4().hex, "name": name,
               "plan_json": json.dumps(plan or {}, ensure_ascii=False, sort_keys=True),
               "stop_criteria_json": json.dumps(
                   stop_criteria if stop_criteria is not None else DEFAULT_STOP_CRITERIA,
                   ensure_ascii=False, sort_keys=True),
               "started_at": _now_iso(), "closed_at": None, "status": "open",
               "verdict_json": None, "superseded_by": None, "exploratory": exploratory}
        self._repo.insert_batch(row)
        return self.get_batch(row["id"])

    def get_batch(self, batch_id: str) -> dict:
        row = self._repo.get_batch(batch_id)
        if row is None:
            raise AppError("not_found", "评估批次不存在。", 404)
        return row

    def list_batches(self, *, status: str | None = None) -> list[dict]:
        return self._repo.list_batches(status=status)

    def current_batch_id(self) -> str | None:
        row = self._repo.open_batch()
        return row["id"] if row else None

    def require_current_batch_id(self) -> str:
        """登记建议/实验时必须落在某个批次里；没有进行中的批次就**拒绝登记**（G4）。"""
        batch_id = self.current_batch_id()
        if batch_id is None:
            raise AppError("governance_no_batch",
                           "没有进行中的评估批次，拒绝登记。请先在治理层开启批次（方案 G4）。", 409)
        return batch_id

    def close_batch(self, batch_id: str, *, verdict: dict | None = None) -> dict:
        row = self.get_batch(batch_id)
        if row["status"] == "closed":
            return row
        self._repo.close_batch(batch_id, closed_at=_now_iso(),
                               verdict_json=json.dumps(verdict, ensure_ascii=False) if verdict else None,
                               superseded_by=None)
        return self.get_batch(batch_id)

    def freeze_batch(self, batch_id: str) -> dict:
        """冻结批次：其成员的规则定义随之不可改写（触发器 `batch_frozen_immutable`）。"""
        row = self.get_batch(batch_id)
        if row["status"] == "closed":
            raise AppError("governance_invalid", "已关闭的批次不可再冻结。", 422)
        with self._storage.connect() as db:
            db.execute("UPDATE evaluation_batches SET status='frozen' WHERE id=?", (batch_id,))
        return self.get_batch(batch_id)

    # --- 批次成员（G4）----------------------------------------------------
    def add_member(self, *, batch_id: str, artifact_kind: str, artifact_id: str) -> dict:
        if artifact_kind not in ARTIFACT_KINDS:
            raise AppError("governance_invalid", f"不支持的成员类型：{artifact_kind}", 422)
        self.get_batch(batch_id)
        self._repo.add_member(batch_id=batch_id, artifact_kind=artifact_kind,
                              artifact_id=artifact_id, added_at=_now_iso())
        return {"batch_id": batch_id, "artifact_kind": artifact_kind, "artifact_id": artifact_id}

    def members(self, batch_id: str, *, artifact_kind: str | None = None) -> list[dict]:
        return self._repo.members(batch_id, artifact_kind=artifact_kind)

    # --- G3 变更即新批次 --------------------------------------------------
    def record_change(self, *, field: str, reason: str, old_hash: str = "", new_hash: str = "",
                      hypothesis_id: str | None = None, replacement_id: str | None = None,
                      new_batch_name: str | None = None, plan: dict | None = None,
                      stop_criteria: dict | None = None) -> dict:
        """记录一次规则变更，并**自动关闭旧批次、开启新批次**。

        冻结期的定义变更会被触发器直接拒绝（`AppError governance_rule_frozen`）；
        本方法是唯一合法的变更路径——它保证每次改规则都产生新批次，旧样本不被合并。
        """
        current = self._repo.open_batch()
        if plan is None:
            plan = json.loads(current["plan_json"]) if current else {}
        if stop_criteria is None:
            stop_criteria = json.loads(current["stop_criteria_json"]) if current else {}
        batch_name = new_batch_name or (
            f"{current['name']} → 变更后" if current else f"变更批次（{field}）")
        new_batch = {"id": uuid4().hex, "name": batch_name,
                     "plan_json": json.dumps(plan, ensure_ascii=False, sort_keys=True),
                     "stop_criteria_json": json.dumps(stop_criteria, ensure_ascii=False, sort_keys=True),
                     "started_at": _now_iso(),
                     "exploratory": bool(current and json.loads(current["plan_json"]).get("exploratory"))}
        close = None
        if current is not None:
            close = {"batch_id": current["id"], "closed_at": _now_iso(),
                     "verdict_json": json.dumps(
                         {"closed_reason": "rule_change", "field": field, "reason": reason},
                         ensure_ascii=False)}
        change = {"id": uuid4().hex, "batch_id": current["id"] if current else None,
                  "changed_at": _now_iso(), "field": field, "old_hash": old_hash,
                  "new_hash": new_hash, "reason": reason, "hypothesis_id": hypothesis_id,
                  "replacement_id": replacement_id}
        self._repo.apply_change(change=change, new_batch=new_batch, close=close)
        return {"change": change, "previous_batch": current, "new_batch": self.get_batch(new_batch["id"])}

    def list_changes(self, *, batch_id: str | None = None) -> list[dict]:
        return self._repo.list_changes(batch_id=batch_id)

    # --- 结算统计（G4）----------------------------------------------------
    def batch_statistics(self, batch_id: str) -> dict:
        """**只统计该批次**的覆盖与分级分布。

        这里**不含**方向命中率、Brier 分数等判定指标——那些属批次 5 的双轨独立结算。
        本方法刻意只回答"这个批次里有什么"，避免在治理层提前产生"结论"。
        """
        self.get_batch(batch_id)
        members = self._repo.members(batch_id)
        by_kind: dict[str, list[str]] = {}
        for item in members:
            by_kind.setdefault(item["artifact_kind"], []).append(item["artifact_id"])
        grades: dict[str, int] = {}
        if by_kind.get("experiment"):
            with self._storage.connect() as db:
                rows = db.execute(
                    "SELECT validation_grade, count(*) AS n FROM strategy_experiments "
                    "WHERE id IN (SELECT artifact_id FROM batch_members "
                    "             WHERE batch_id=? AND artifact_kind='experiment') "
                    "GROUP BY validation_grade", (batch_id,)).fetchall()
            grades = {row["validation_grade"]: row["n"] for row in rows}
        return {"batch_id": batch_id, "cross_batch": False, "usable_for_verdict": True,
                "member_counts": {kind: len(ids) for kind, ids in by_kind.items()},
                "experiment_grades": grades, "total_members": len(members)}

    def combined_statistics(self, batch_ids: list[str],
                            *, acknowledge_cross_batch: bool = False) -> dict:
        """跨批次合并统计：**默认拒绝**；显式确认后也只供参考，不得用于判定。"""
        unique = list(dict.fromkeys(batch_ids))
        if len(unique) < 2:
            raise AppError("governance_invalid", "跨批次合并至少需要两个批次。", 422)
        if not acknowledge_cross_batch:
            raise AppError(
                "governance_cross_batch",
                "跨批次合并统计被拒绝（方案 G4：统计只按批次聚合）。如确需参考，"
                "请显式确认 acknowledge_cross_batch=true——其结果不得用于判定。", 409)
        parts = [self.batch_statistics(batch_id) for batch_id in unique]
        merged: dict[str, int] = {}
        for part in parts:
            for kind, count in part["member_counts"].items():
                merged[kind] = merged.get(kind, 0) + count
        grades: dict[str, int] = {}
        for part in parts:
            for grade, count in part["experiment_grades"].items():
                grades[grade] = grades.get(grade, 0) + count
        return {"batch_ids": unique, "cross_batch": True, "usable_for_verdict": False,
                "acknowledged": True, "member_counts": merged, "experiment_grades": grades,
                "total_members": sum(part["total_members"] for part in parts)}
