"""治理层的数据访问：固定语句，不接受调用方传入 SQL 片段。

触发器抛出的 `RAISE(ABORT, ...)` 在这里被翻译成带审计语义的 `AppError`，
让"冻结期改规则"这类操作得到一个**明确拒绝**而不是一个原始 sqlite 异常。
"""
from __future__ import annotations

import sqlite3

from ..errors import AppError
from ..storage import Storage

#: 触发器消息 → 面向调用方的拒绝原因。任何一条命中都不得被吞掉或降级为警告。
TRIGGER_REJECTIONS = {
    "hypothesis_frozen_immutable":
        "该假设已冻结，定义与参数不可改写。变更必须登记为新假设（新 reference）并开新批次。",
    "batch_frozen_immutable":
        "该假设所属评估批次处于冻结期，定义与参数不可改写。变更必须开新批次。",
    "batch_closed_immutable":
        "该批次已关闭，其计划与停止条件不可改写。请开新批次。",
    "hypothesis_frozen_no_delete":
        "已冻结的假设不可删除（删除会抹掉评估证据）。请改为 retired。",
}


def _guard(action):
    def wrapped(*args, **kwargs):
        try:
            return action(*args, **kwargs)
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            for marker, reason in TRIGGER_REJECTIONS.items():
                if marker in message:
                    raise AppError("governance_rule_frozen", reason, 409) from None
            raise
    return wrapped


class GovernanceRepository:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # --- 假设登记 ---------------------------------------------------------
    @_guard
    def insert_hypothesis(self, row: dict) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO hypothesis_registry(id, reference, kind, name, definition_json, "
                "direction, params_json, params_hash, submitted_at, frozen_at, status, "
                "exploratory, trial_count, notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["reference"], row["kind"], row["name"], row["definition_json"],
                 row["direction"], row["params_json"], row["params_hash"], row["submitted_at"],
                 row["frozen_at"], row["status"], int(bool(row["exploratory"])),
                 int(row["trial_count"]), row["notes"]))

    def get_hypothesis(self, hypothesis_id: str) -> dict | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM hypothesis_registry WHERE id=?", (hypothesis_id,)).fetchone()
        return dict(row) if row else None

    def get_hypothesis_by_reference(self, reference: str) -> dict | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM hypothesis_registry WHERE reference=?",
                             (reference,)).fetchone()
        return dict(row) if row else None

    def list_hypotheses(self, *, status: str | None = None) -> list[dict]:
        with self._storage.connect() as db:
            if status:
                rows = db.execute("SELECT * FROM hypothesis_registry WHERE status=? "
                                  "ORDER BY submitted_at DESC", (status,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM hypothesis_registry "
                                  "ORDER BY submitted_at DESC").fetchall()
        return [dict(row) for row in rows]

    @_guard
    def set_hypothesis_status(self, hypothesis_id: str, *, status: str,
                              frozen_at: str | None = None) -> None:
        with self._storage.connect() as db:
            db.execute("UPDATE hypothesis_registry SET status=?, "
                       "frozen_at=COALESCE(?, frozen_at) WHERE id=?",
                       (status, frozen_at, hypothesis_id))

    @_guard
    def update_hypothesis_definition(self, hypothesis_id: str, *, definition_json: str,
                                     params_json: str, params_hash: str) -> None:
        """改写定义列。**只对 draft 合法**——冻结行会被触发器直接拒绝。"""
        with self._storage.connect() as db:
            db.execute("UPDATE hypothesis_registry SET definition_json=?, params_json=?, "
                       "params_hash=? WHERE id=?",
                       (definition_json, params_json, params_hash, hypothesis_id))

    # --- 评估批次 ---------------------------------------------------------
    @_guard
    def insert_batch(self, row: dict) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO evaluation_batches(id, name, plan_json, stop_criteria_json, started_at, "
                "closed_at, status, verdict_json, superseded_by, exploratory) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["name"], row["plan_json"], row["stop_criteria_json"],
                 row["started_at"], row["closed_at"], row["status"], row["verdict_json"],
                 row["superseded_by"], int(bool(row["exploratory"]))))

    def get_batch(self, batch_id: str) -> dict | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM evaluation_batches WHERE id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def list_batches(self, *, status: str | None = None) -> list[dict]:
        with self._storage.connect() as db:
            if status:
                rows = db.execute("SELECT * FROM evaluation_batches WHERE status=? "
                                  "ORDER BY started_at DESC", (status,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM evaluation_batches "
                                  "ORDER BY started_at DESC").fetchall()
        return [dict(row) for row in rows]

    def open_batch(self) -> dict | None:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM evaluation_batches WHERE status IN ('open','frozen') "
                             "ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    @_guard
    def close_batch(self, batch_id: str, *, closed_at: str, verdict_json: str | None,
                    superseded_by: str | None) -> None:
        with self._storage.connect() as db:
            db.execute("UPDATE evaluation_batches SET status='closed', closed_at=?, verdict_json=?, "
                       "superseded_by=? WHERE id=?",
                       (closed_at, verdict_json, superseded_by, batch_id))

    # --- 批次成员 ---------------------------------------------------------
    def add_member(self, *, batch_id: str, artifact_kind: str, artifact_id: str,
                   added_at: str) -> None:
        with self._storage.connect() as db:
            db.execute("INSERT OR IGNORE INTO batch_members(batch_id, artifact_kind, artifact_id, "
                       "added_at) VALUES(?,?,?,?)",
                       (batch_id, artifact_kind, artifact_id, added_at))

    def members(self, batch_id: str, *, artifact_kind: str | None = None) -> list[dict]:
        with self._storage.connect() as db:
            if artifact_kind:
                rows = db.execute("SELECT * FROM batch_members WHERE batch_id=? AND artifact_kind=? "
                                  "ORDER BY added_at", (batch_id, artifact_kind)).fetchall()
            else:
                rows = db.execute("SELECT * FROM batch_members WHERE batch_id=? "
                                  "ORDER BY added_at", (batch_id,)).fetchall()
        return [dict(row) for row in rows]

    # --- 变更留痕 ---------------------------------------------------------
    @_guard
    def insert_change(self, row: dict) -> None:
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO rule_change_log(id, batch_id, changed_at, field, old_hash, new_hash, "
                "reason, hypothesis_id, replacement_id, forced_new_batch_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["batch_id"], row["changed_at"], row["field"], row["old_hash"],
                 row["new_hash"], row["reason"], row.get("hypothesis_id"),
                 row.get("replacement_id"), row["forced_new_batch_id"]))

    def list_changes(self, *, batch_id: str | None = None) -> list[dict]:
        with self._storage.connect() as db:
            if batch_id:
                rows = db.execute("SELECT * FROM rule_change_log WHERE batch_id=? "
                                  "ORDER BY changed_at DESC", (batch_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM rule_change_log "
                                  "ORDER BY changed_at DESC").fetchall()
        return [dict(row) for row in rows]

    # --- 原子变更（G3）：关旧批次 + 开新批次 + 留痕，必须在同一事务内 -------
    def apply_change(self, *, change: dict, new_batch: dict, close: dict | None) -> None:
        try:
            with self._storage.connect() as db:
                if close is not None:
                    db.execute("UPDATE evaluation_batches SET status='closed', closed_at=?, "
                               "verdict_json=?, superseded_by=? WHERE id=?",
                               (close["closed_at"], close["verdict_json"], new_batch["id"],
                                close["batch_id"]))
                db.execute(
                    "INSERT INTO evaluation_batches(id, name, plan_json, stop_criteria_json, "
                    "started_at, closed_at, status, verdict_json, superseded_by, exploratory) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (new_batch["id"], new_batch["name"], new_batch["plan_json"],
                     new_batch["stop_criteria_json"], new_batch["started_at"], None, "open", None,
                     None, int(bool(new_batch["exploratory"]))))
                db.execute(
                    "INSERT INTO rule_change_log(id, batch_id, changed_at, field, old_hash, "
                    "new_hash, reason, hypothesis_id, replacement_id, forced_new_batch_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (change["id"], change["batch_id"], change["changed_at"], change["field"],
                     change["old_hash"], change["new_hash"], change["reason"],
                     change.get("hypothesis_id"), change.get("replacement_id"), new_batch["id"]))
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            for marker, reason in TRIGGER_REJECTIONS.items():
                if marker in message:
                    raise AppError("governance_rule_frozen", reason, 409) from None
            raise
