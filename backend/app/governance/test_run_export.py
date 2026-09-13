"""当日测试数据落盘（方案 00 §5 / 03 号操作手册）。

**触发方式**：只能由用户在界面点击触发。本模块**不注册任何定时任务**，
也不在任何取数路径里被隐式调用——红线要求「付费 API 不自动调用」，
导出同理：它是一次显式的数据变更动作。

产物目录 `data/exports/test-runs/<日期>/`：

| 文件 | 内容 | 给谁 |
|---|---|---|
| `daily.md` | 当日候选、每只的建议卡、证据编号、档位与理由 | 给人看 |
| `daily.jsonl` | 逐条一行 JSON（`record_type` 区分 advice / experiment / track） | 给统计脚本吃 |
| `batch.json` | 批次号、`params_hash`、数据快照 `sha256`、模型配置 ID、治理状态 | 审计用 |

**诚实优先**：样本为空时三份产物都会**明确写出"本次导出没有当日数据"**，
并附上当前批次与治理状态，而不是产出空文件让人以为坏了。同样常驻披露
幸存者偏差与"探索性结果不得用于判定"。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import AppError
from ..storage import Storage
from .service import GovernanceService

CST = timezone(timedelta(hours=8))
SCHEMA_VERSION = "finsight.testrun.v1"

DISCLAIMER = "研究参考，不构成投资建议。所有结论均依赖本地数据与历史样本，不承诺收益。"
STANDING_CAVEATS = [
    "本机行情仓库不含退市证券（5286 只有效标的中 0 只已退市），历史统计存在乐观偏置。",
    "探索性扫描结果（含安慰剂对照）**不得**作为 C3 判定样本，也不得据此调整规则。",
    "任何门禁口径或规则变更都必须走 record_change 并开新批次，否则结论不可审计。",
]


def _today() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _cst_day_bounds(date_str: str) -> tuple[str, str]:
    """返回该 CST 自然日对应的 UTC ISO 起止（用于与 created_at 做字符串比较）。"""
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CST)
    return (start.astimezone(timezone.utc).isoformat(),
            (start + timedelta(days=1)).astimezone(timezone.utc).isoformat())


class DailyExportService:
    def __init__(self, storage: Storage, exports_root: Path) -> None:
        self._storage = storage
        self._root = Path(exports_root) / "test-runs"
        self._governance = GovernanceService(storage)

    # --- 数据装配 ---------------------------------------------------------
    def build(self, *, date: str | None = None, batch_id: str | None = None) -> dict:
        day = date or _today()
        if batch_id and self._governance.get_batch(batch_id) is None:
            raise AppError("not_found", "评估批次不存在。", 404)
        start, end = _cst_day_bounds(day)

        if batch_id:
            with self._storage.connect() as db:
                advice_ids = [row["artifact_id"] for row in db.execute(
                    "SELECT artifact_id FROM batch_members WHERE batch_id=? AND artifact_kind='advice'",
                    (batch_id,)).fetchall()]
                experiment_ids = [row["artifact_id"] for row in db.execute(
                    "SELECT artifact_id FROM batch_members WHERE batch_id=? AND artifact_kind='experiment'",
                    (batch_id,)).fetchall()]
            clause, params = ("id IN (%s)" % ",".join("?" * len(advice_ids)), list(advice_ids)) \
                if advice_ids else ("0", [])
            scope_label = f"批次 {batch_id[:8]}"
        else:
            clause, params = ("created_at >= ? AND created_at < ?", [start, end])
            experiment_ids = None
            scope_label = f"{day}（Asia/Shanghai）"

        with self._storage.connect() as db:
            advice_rows = [dict(row) for row in db.execute(
                f"SELECT * FROM advice_ledger WHERE {clause} ORDER BY created_at", params).fetchall()] \
                if clause != "0" else []
            if experiment_ids is None:
                experiment_rows = [dict(row) for row in db.execute(
                    "SELECT * FROM strategy_experiments WHERE created_at >= ? AND created_at < ? "
                    "ORDER BY created_at", (start, end)).fetchall()]
            elif experiment_ids:
                experiment_rows = [dict(row) for row in db.execute(
                    "SELECT * FROM strategy_experiments WHERE id IN (%s) ORDER BY created_at"
                    % ",".join("?" * len(experiment_ids)), experiment_ids).fetchall()]
            else:
                experiment_rows = []
            track_rows = [dict(row) for row in db.execute(
                "SELECT * FROM track_settlements WHERE advice_id IN (%s) ORDER BY advice_id, track"
                % ",".join("?" * len(advice_rows)),
                [row["id"] for row in advice_rows]).fetchall()] if advice_rows else []
            prediction_rows = [dict(row) for row in db.execute(
                "SELECT id,thscode,name,horizon,dominant_scenario,event,p_up,p_flat,p_down,"
                "probability_source,status,model_profile_id,prompt_version,evidence_hash,created_at "
                "FROM research_predictions WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
                (start, end)).fetchall()]

        return {
            "schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "date": day, "scope_label": scope_label, "batch_id": batch_id,
            "advice": advice_rows, "experiments": experiment_rows,
            "tracks": track_rows, "predictions": prediction_rows,
            "batch": self._governance.get_batch(batch_id) if batch_id
                     else (self._governance.get_batch(self._governance.current_batch_id())
                           if self._governance.current_batch_id() else None),
            "batches": self._governance.list_batches(),
            "changes": self._governance.list_changes(),
            "hypotheses": self._governance.list_hypotheses(),
            "disclaimer": DISCLAIMER, "caveats": STANDING_CAVEATS,
            "sample_is_empty": not (advice_rows or experiment_rows or track_rows or prediction_rows),
        }

    # --- 落盘 -------------------------------------------------------------
    def export(self, *, date: str | None = None, batch_id: str | None = None) -> dict:
        payload = self.build(date=date, batch_id=batch_id)
        directory = self._root / payload["date"]
        directory.mkdir(parents=True, exist_ok=True)
        files = {}
        files["daily.md"] = self._markdown(payload)
        files["daily.jsonl"] = self._jsonl(payload)
        files["batch.json"] = self._batch_json(payload)
        written = []
        for name, content in files.items():
            target = (directory / name).resolve()
            if self._root.resolve() not in target.parents:
                raise AppError("validation", "导出路径无效。", 422)
            # 显式 newline="\n"：Windows 默认会把 \n 翻成 \r\n，那会让**报告的字节数
            # 与磁盘不一致**，也让产物在不同平台上不可逐字节复现。
            target.write_text(content, encoding="utf-8", newline="\n")
            written.append({"name": name, "path": str(target), "bytes": len(content.encode("utf-8"))})
        return {"directory": str(directory), "files": written, "date": payload["date"],
                "scope_label": payload["scope_label"], "sample_is_empty": payload["sample_is_empty"],
                "counts": {"advice": len(payload["advice"]), "experiments": len(payload["experiments"]),
                           "tracks": len(payload["tracks"]), "predictions": len(payload["predictions"])}}

    def list_runs(self) -> list[dict]:
        if not self._root.exists():
            return []
        runs = []
        for directory in sorted(self._root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            brief = {}
            for name in ("daily.md", "daily.jsonl", "batch.json"):
                path = directory / name
                if path.is_file():
                    brief[name] = {"bytes": path.stat().st_size,
                                   "modified_at": datetime.fromtimestamp(
                                       path.stat().st_mtime, CST).isoformat()}
            runs.append({"date": directory.name, "path": str(directory), "files": brief})
        return runs

    # --- 三种格式 ---------------------------------------------------------
    @staticmethod
    def _safe(value: object) -> str:
        """Markdown 里保持惰性：不解析内嵌 HTML。"""
        return str(value if value is not None else "").replace("\x00", "").replace("\r", "") \
            .replace("<", "&lt;").replace(">", "&gt;")

    def _markdown(self, payload: dict) -> str:
        day, batch = payload["date"], payload["batch"]
        lines = [f"# FinSight 当日测试数据 · {day}", "",
                 f"> {DISCLAIMER}", "",
                 "> 本文件由用户在界面点击导出（**非后台定时**），不含 API Key、模型隐藏推理与网页全文。",
                 "", "## 0. 本次导出范围", "",
                 f"- 范围：{self._safe(payload['scope_label'])}",
                 f"- 生成时间（UTC）：{payload['generated_at']}",
                 f"- 当前评估批次：{self._batch_label(batch)}",
                 "- 样本统计："
                 f"建议 **{len(payload['advice'])}** 条 / 实验 **{len(payload['experiments'])}** 条 / "
                 f"双轨记录 **{len(payload['tracks'])}** 条 / 研究预测 **{len(payload['predictions'])}** 条",
                 ""]
        if payload["sample_is_empty"]:
            lines += ["### ⚠️ 本次导出没有当日数据", "",
                      "这**不代表系统故障**。可能的原因：本轮尚未产生通过候选门禁的实验；"
                      "顾问工作台今天还没跑过；或双轨记录尚未结算。",
                      "治理状态与预注册台账仍然照常导出（见 §5），可用于确认链路与规则状态。", ""]

        lines += ["## 1. 当日候选与档位", ""]
        if not payload["advice"]:
            lines += ["（无）", ""]
        else:
            lines += ["| 标的 | 名称 | 方向 | 把握度 | 档位 | 档位理由 | 策略引用 |",
                      "|---|---|---|---|---|---|---|"]
            for row in payload["advice"]:
                tier = json.loads(row.get("tier_snapshot_json") or "{}")
                refs = json.loads(row.get("strategy_ref_json") or "[]")
                lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                    self._safe(row["thscode"]), self._safe(row["name"]), self._safe(row["direction"]),
                    self._safe(row["confidence"]), self._safe(tier.get("tier")),
                    self._safe(tier.get("tier_reason")),
                    self._safe("、".join(refs) if refs else "—")))
            lines.append("")

        lines += ["## 2. 建议卡明细", ""]
        if not payload["advice"]:
            lines += ["（无）", ""]
        for row in payload["advice"]:
            card = json.loads(row.get("card_json") or "{}")
            tier = json.loads(row.get("tier_snapshot_json") or "{}")
            board = json.loads(row.get("card_json") or "{}").get("claims") or []
            tracks = [item for item in payload["tracks"] if item["advice_id"] == row["id"]]
            lines += [f"### {self._safe(row['thscode'])} {self._safe(row['name'])}"
                      f"（{row['created_at']}）", "",
                      f"- 方向 / 把握度：{self._safe(row['direction'])} / {self._safe(row['confidence'])}",
                      f"- 档位：**{self._safe(tier.get('tier'))}** — {self._safe(tier.get('tier_reason'))}",
                      f"- 档位门禁输入：`{json.dumps(tier.get('gates') or {}, ensure_ascii=False)}`",
                      f"- 关键证据：{self._safe('、'.join(card.get('key_evidence') or []) or '—')}",
                      f"- 反证证据：{self._safe('、'.join(card.get('counter_evidence') or []) or '—')}",
                      f"- 参数哈希 / 证据哈希：`{self._safe(row.get('params_hash'))}` / "
                      f"`{self._safe(row.get('evidence_hash'))}`",
                      f"- 模型配置 ID / 提示词版本：`{self._safe(row.get('model_profile_id') or '—')}` / "
                      f"`{self._safe(row.get('prompt_version'))}`",
                      f"- 批次：`{self._safe(row.get('batch_id') or '—')}`"]
            for scenario in card.get("scenarios") or []:
                lines.append(f"  - 情景 {self._safe(scenario.get('name'))}："
                             f"{scenario.get('probability_pct')}%（{self._safe(scenario.get('probability_source'))}）")
            for claim in board:
                lines.append(f"  - 陈述（{self._safe(claim.get('kind'))}）：{self._safe(claim.get('text'))}"
                             f"　证据 {self._safe('、'.join(claim.get('evidence_refs') or []) or '—')}")
            lines.append(f"- 失效条件：{self._safe('；'.join(card.get('invalidations') or []) or '—')}")
            lines.append(f"- 观察哨：{self._safe('；'.join(card.get('watchpoints') or []) or '—')}")
            lines.append(f"- 双轨：{self._track_brief(tracks)}")
            lines.append("")

        lines += ["## 3. 策略实验", ""]
        if not payload["experiments"]:
            lines += ["（无）", ""]
        else:
            lines += ["| 标的 | 策略@版本 | 级别 | 原因 | 参数哈希 | 数据快照 sha256 |",
                      "|---|---|---|---|---|---|"]
            for row in payload["experiments"]:
                result = json.loads(row.get("result_json") or "{}")
                snapshot = result.get("data_snapshot") or json.loads(row.get("data_snapshot_json") or "{}")
                lines.append("| {} | {}@{} | {} | {} | `{}` | `{}` |".format(
                    self._safe(row["thscode"]), self._safe(row["strategy_id"]),
                    self._safe(row["strategy_version"]), self._safe(row["validation_grade"]),
                    self._safe("；".join((result.get("validation_reasons") or [])[:2])),
                    self._safe(row.get("params_hash")),
                    self._safe(str(snapshot.get("sha256"))[:16] + "…" if snapshot.get("sha256") else "—")))
            lines.append("")

        lines += ["## 4. 双轨独立结算（策略轨 vs AI 裁决轨）", ""]
        if not payload["tracks"]:
            lines += ["（无）", ""]
        else:
            lines += ["| 建议 | 轨 | 方向 | 状态 | 命中 | 二元 Brier | 弃权 | 结算区间 |",
                      "|---|---|---|---|---|---|---|---|"]
            for row in payload["tracks"]:
                settlement = json.loads(row.get("settlement_json") or "{}")
                window = (f"{settlement.get('base_date')}→{settlement.get('final_date')}"
                          if settlement else "—")
                lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    self._safe(row["advice_id"][:8]), self._safe(row["track"]),
                    self._safe(row["direction"]), self._safe(row["status"]),
                    self._safe(settlement.get("hit")), self._safe(settlement.get("binary_brier")),
                    self._safe(settlement.get("abstained")), self._safe(window)))
            lines.append("")

        lines += ["## 5. 治理状态", "",
                  f"- 进行中批次：{self._batch_label(batch)}", ""]
        if batch:
            lines += ["  停止条件（预注册，达标即停）：", ""]
            for key, value in (json.loads(batch.get("stop_criteria_json") or "{}")).items():
                lines.append(f"  - `{key}`：{self._safe(value)}")
            lines.append("")
        lines += ["  预注册台账：", "", "| reference | 类型 | 状态 | 探索性 | 试验次数 |",
                  "|---|---|---|---|---|"]
        for row in payload["hypotheses"]:
            lines.append("| `{}` | {} | {} | {} | {} |".format(
                self._safe(row["reference"]), self._safe(row["kind"]), self._safe(row["status"]),
                "是" if row["exploratory"] else "否", row["trial_count"]))
        lines += ["", "  规则变更留痕：", ""]
        if not payload["changes"]:
            lines += ["  （无）", ""]
        else:
            lines += ["| 时间 | 字段 | 旧哈希 | 新哈希 | 触发的新批次 |", "|---|---|---|---|---|"]
            for row in payload["changes"]:
                lines.append("| {} | `{}` | `{}` | `{}` | `{}` |".format(
                    self._safe(row["changed_at"]), self._safe(row["field"]),
                    self._safe(row["old_hash"]), self._safe(row["new_hash"]),
                    self._safe((row.get("forced_new_batch_id") or "—")[:8])))
            lines.append("")

        lines += ["## 6. 常驻披露", ""] + [f"- {item}" for item in STANDING_CAVEATS]
        lines += ["", f"> {DISCLAIMER}", ""]
        return "\n".join(lines)

    @staticmethod
    def _batch_label(batch: dict | None) -> str:
        if not batch:
            return "（无进行中批次）"
        return f"`{batch['id'][:8]}`「{batch['name']}」status={batch['status']}"

    @staticmethod
    def _track_brief(tracks: list[dict]) -> str:
        if not tracks:
            return "（未封存）"
        parts = []
        for row in tracks:
            settlement = json.loads(row.get("settlement_json") or "{}")
            state = row["status"]
            if settlement:
                state += f"（{'弃权' if settlement.get('abstained') else '命中' if settlement.get('hit') else '未命中'}）"
            parts.append(f"{row['track']}={row['direction']}/{state}")
        return "；".join(parts)

    def _jsonl(self, payload: dict) -> str:
        """逐条一行 JSON，`record_type` 供统计脚本过滤。"""
        lines = []
        for row in payload["advice"]:
            lines.append(json.dumps({
                "record_type": "advice", "date": payload["date"], "id": row["id"],
                "thscode": row["thscode"], "name": row["name"], "direction": row["direction"],
                "confidence": row["confidence"], "horizon": row["horizon"],
                "batch_id": row.get("batch_id"), "params_hash": row.get("params_hash"),
                "evidence_hash": row.get("evidence_hash"), "model_profile_id": row.get("model_profile_id"),
                "prompt_version": row.get("prompt_version"), "created_at": row["created_at"],
                "tier": json.loads(row.get("tier_snapshot_json") or "{}"),
                "card": json.loads(row.get("card_json") or "{}"),
                "strategy_refs": json.loads(row.get("strategy_ref_json") or "[]"),
            }, ensure_ascii=False))
        for row in payload["experiments"]:
            result = json.loads(row.get("result_json") or "{}")
            snapshot = result.get("data_snapshot") or json.loads(row.get("data_snapshot_json") or "{}")
            lines.append(json.dumps({
                "record_type": "strategy_experiment", "date": payload["date"], "id": row["id"],
                "thscode": row["thscode"], "strategy_id": row["strategy_id"],
                "strategy_version": row["strategy_version"],
                "validation_grade": row["validation_grade"],
                "validation_reasons": result.get("validation_reasons") or [],
                "metrics": result.get("metrics") or {}, "params_hash": row.get("params_hash"),
                "data_snapshot": {key: snapshot.get(key) for key in
                                  ("basis", "adjust", "bars", "start", "end", "sha256")},
                "created_at": row["created_at"],
            }, ensure_ascii=False))
        for row in payload["tracks"]:
            lines.append(json.dumps({
                "record_type": "track_settlement", "date": payload["date"], "id": row["id"],
                "advice_id": row["advice_id"], "track": row["track"], "direction": row["direction"],
                "horizon_days": row["horizon_days"], "as_of_date": row["as_of_date"],
                "as_of_basis": row["as_of_basis"], "eligible": bool(row["eligible"]),
                "ineligible_reason": row["ineligible_reason"] or "",
                "probability": json.loads(row.get("probability_json") or "{}"),
                "status": row["status"], "settled_at": row.get("settled_at"),
                "settlement": json.loads(row["settlement_json"]) if row.get("settlement_json") else None,
            }, ensure_ascii=False))
        for row in payload["predictions"]:
            lines.append(json.dumps({"record_type": "research_prediction", "date": payload["date"],
                                     **row}, ensure_ascii=False))
        return "\n".join(lines) + ("\n" if lines else "")

    def _batch_json(self, payload: dict) -> str:
        """审计用的汇总：批次号、params_hash 集合、数据快照 sha256 集合、模型配置 ID 集合。"""
        snapshots = []
        for row in payload["experiments"]:
            result = json.loads(row.get("result_json") or "{}")
            snapshot = result.get("data_snapshot") or json.loads(row.get("data_snapshot_json") or "{}")
            snapshots.append({"experiment_id": row["id"], "thscode": row["thscode"],
                              "sha256": snapshot.get("sha256"), "basis": snapshot.get("basis") or snapshot.get("adjust"),
                              "bars": snapshot.get("bars"), "start": snapshot.get("start"),
                              "end": snapshot.get("end"), "params_hash": row.get("params_hash")})
        batch = payload["batch"]
        document = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": payload["generated_at"],
            "generated_by": "user_clicked_export",
            "background_timer": False,
            "date": payload["date"],
            "scope_label": payload["scope_label"],
            "sample_is_empty": payload["sample_is_empty"],
            "counts": {"advice": len(payload["advice"]),
                       "strategy_experiments": len(payload["experiments"]),
                       "track_settlements": len(payload["tracks"]),
                       "research_predictions": len(payload["predictions"])},
            "batch": None if not batch else {
                "id": batch["id"], "name": batch["name"], "status": batch["status"],
                "started_at": batch["started_at"], "closed_at": batch["closed_at"],
                "superseded_by": batch["superseded_by"],
                "plan": json.loads(batch["plan_json"] or "{}"),
                "stop_criteria": json.loads(batch["stop_criteria_json"] or "{}"),
            },
            "audit": {
                "params_hashes": sorted({row.get("params_hash") for row in payload["advice"]
                                         if row.get("params_hash")} |
                                        {row.get("params_hash") for row in payload["experiments"]
                                         if row.get("params_hash")}),
                "evidence_hashes": sorted({row.get("evidence_hash") for row in payload["advice"]
                                           if row.get("evidence_hash")}),
                "model_profile_ids": sorted({row.get("model_profile_id") for row in payload["advice"]
                                             if row.get("model_profile_id")}),
                "prompt_versions": sorted({row.get("prompt_version") for row in payload["advice"]
                                           if row.get("prompt_version")}),
                "data_snapshots": snapshots,
                "rule_changes": [{"changed_at": row["changed_at"], "field": row["field"],
                                  "old_hash": row["old_hash"], "new_hash": row["new_hash"],
                                  "forced_new_batch_id": row["forced_new_batch_id"]}
                                 for row in payload["changes"]],
                "hypotheses": [{"reference": row["reference"], "kind": row["kind"],
                                "status": row["status"], "exploratory": bool(row["exploratory"]),
                                "trial_count": row["trial_count"],
                                "params_hash": row["params_hash"]} for row in payload["hypotheses"]],
                "batches": [{"id": row["id"], "name": row["name"], "status": row["status"],
                             "started_at": row["started_at"], "closed_at": row["closed_at"],
                             "superseded_by": row["superseded_by"]} for row in payload["batches"]],
            },
            "disclaimer": payload["disclaimer"],
            "caveats": payload["caveats"],
        }
        return json.dumps(document, ensure_ascii=False, indent=2) + "\n"
