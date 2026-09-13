"""双轨封存数据契约：研究预测轨（交接提示词 §8.2）。

研究预测轨允许大胆的方向概率预测，但与执行建议轨完全分开：
- 每条预测登记即封存（canonical JSON 的 SHA-256），内容事后不可改写、可完整性校验；
- 反证与失效条件是必填字段，缺少反证或失效条件的预测不得封存；
- 主导情景必须唯一：无法区分方向时应在执行建议轨按观察/拒答处理，不进入本轨；
- 结算与统计口径独立于执行建议台账、历史回放、覆盖度回放与人工前向验证，绝不混合。
本轨只用于研究评估与概率校准，不会自动生成任何真实仓位建议。
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..errors import AppError
from ..governance.service import GovernanceService
from ..storage import Storage
from .ledger import HORIZON_MS, _base_cutoff_date, _date_to_ms, _independent_observations, _now_iso, _shift_date

PROMPT_VERSION = "research-track-v1"
_EVENTS = {"up": "positive_return", "flat": "flat_return", "down": "negative_return"}


class ResearchPredictionInput(BaseModel):
    """研究预测封存输入；缺反证或缺失效条件时拒绝，不做默认补齐。"""

    model_config = ConfigDict(extra="forbid")

    thscode: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ|BJ)$")
    name: str = Field(min_length=1, max_length=80)
    as_of_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    horizon: Literal["days_5", "days_20", "quarter", "year", "custom"]
    p_up: float = Field(ge=0, le=100)
    p_flat: float = Field(ge=0, le=100)
    p_down: float = Field(ge=0, le=100)
    probability_source: Literal["model_subjective", "base_rate"]
    rationale: str = Field(min_length=1, max_length=2000)
    counter_evidence: list[str] = Field(min_length=1, max_length=12)
    invalidations: list[str] = Field(min_length=1, max_length=8)
    evidence_snapshot: list[dict] = Field(default_factory=list, max_length=20)
    return_threshold_pct: float = Field(default=0.2, ge=0, le=50)
    model_profile_id: str | None = None
    prompt_version: str = PROMPT_VERSION
    source: Literal["manual", "advisor_research"] = "manual"
    linked_advice_id: str | None = None

    @model_validator(mode="after")
    def _probabilities_normalized(self):
        total = self.p_up + self.p_flat + self.p_down
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"三情景概率之和必须等于 100（当前 {total:.2f}）。")
        return self

    @model_validator(mode="after")
    def _unique_dominant_scenario(self):
        values = {"up": self.p_up, "flat": self.p_flat, "down": self.p_down}
        top = max(values.values())
        leaders = sorted(name for name, value in values.items() if top - value <= 0.01)
        if len(leaders) != 1:
            raise ValueError(
                "研究预测必须给出唯一主导情景（当前并列："
                + "、".join(leaders) + "）；无法区分方向时不应封存研究预测。")
        return self

    @model_validator(mode="after")
    def _flat_needs_positive_threshold(self):
        values = {"up": self.p_up, "flat": self.p_flat, "down": self.p_down}
        top = max(values.values())
        if top - self.p_flat <= 0.01 and self.return_threshold_pct <= 0:
            raise ValueError("主导情景为震荡时，涨跌阈值必须为正，否则所有走势都会被记为命中。")
        return self


def _canonical_hash(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:16]


def _dominant(item: ResearchPredictionInput) -> str:
    values = {"up": item.p_up, "flat": item.p_flat, "down": item.p_down}
    top = max(values.values())
    return sorted(name for name, value in values.items() if top - value <= 0.01)[0]


class ResearchPredictionLedger:
    """research_predictions 表的封存、读取、完整性校验与结算。"""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # -- 封存 ----------------------------------------------------------------
    def seal(self, payload: dict, *, batch_id: str | None = None) -> dict:
        """封存一条研究预测；与研究轨同样受 G4 批次隔离约束（无进行中批次即拒绝）。"""
        item = ResearchPredictionInput.model_validate(payload)
        batch_id = batch_id or GovernanceService(self._storage).require_current_batch_id()
        with self._storage.connect() as db:
            if item.linked_advice_id:
                linked = db.execute("SELECT id FROM advice_ledger WHERE id=?",
                                    (item.linked_advice_id,)).fetchone()
                if linked is None:
                    raise AppError("not_found", "关联的执行建议台账条目不存在，无法建立双轨关联。", 422)
        dominant = _dominant(item)
        entry_id = uuid4().hex
        now = _now_iso()
        evidence_snapshot = list(item.evidence_snapshot)
        evidence_hash = _canonical_hash({"evidence": evidence_snapshot})
        record = {
            "id": entry_id,
            "source": item.source,
            "thscode": item.thscode,
            "name": item.name,
            "as_of_date": item.as_of_date,
            "horizon": item.horizon,
            "p_up": item.p_up,
            "p_flat": item.p_flat,
            "p_down": item.p_down,
            "dominant_scenario": dominant,
            "event": _EVENTS[dominant],
            "return_threshold_pct": item.return_threshold_pct,
            "probability_source": item.probability_source,
            "rationale": item.rationale,
            "counter_evidence": list(item.counter_evidence),
            "invalidations": list(item.invalidations),
            "evidence_snapshot": evidence_snapshot,
            "evidence_hash": evidence_hash,
            "model_profile_id": item.model_profile_id,
            "prompt_version": item.prompt_version,
            "linked_advice_id": item.linked_advice_id,
            "created_at": now,
        }
        record_hash = _canonical_hash(record)
        with self._storage.connect() as db:
            db.execute(
                "INSERT INTO research_predictions(id, source, thscode, name, as_of_date, horizon, "
                "p_up, p_flat, p_down, dominant_scenario, event, return_threshold_pct, "
                "probability_source, rationale, counter_evidence_json, invalidations_json, "
                "evidence_snapshot_json, evidence_hash, record_hash, model_profile_id, "
                "prompt_version, linked_advice_id, status, created_at, batch_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry_id, item.source, item.thscode, item.name, item.as_of_date, item.horizon,
                 item.p_up, item.p_flat, item.p_down, dominant, _EVENTS[dominant],
                 item.return_threshold_pct, item.probability_source, item.rationale,
                 json.dumps(list(item.counter_evidence), ensure_ascii=False),
                 json.dumps(list(item.invalidations), ensure_ascii=False),
                 json.dumps(evidence_snapshot, ensure_ascii=False), evidence_hash,
                 record_hash, item.model_profile_id, item.prompt_version,
                 item.linked_advice_id, "sealed", now, batch_id))
        return self.get(entry_id)

    # -- 读取 ----------------------------------------------------------------
    def get(self, entry_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_predictions WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "研究预测条目不存在。", 404)
        return self._view(dict(row))

    def find_advisor_research(self, advice_id: str) -> dict | None:
        """按执行建议关联找自动封存条目，用于任务恢复时幂等续作。

        人工研究预测仍可引用同一条执行建议；这里只读取 advisor_research，
        不把人工条目误当成自动任务的已完成检查点。
        """
        with self._storage.connect() as db:
            row = db.execute(
                "SELECT * FROM research_predictions WHERE source='advisor_research' "
                "AND linked_advice_id=? ORDER BY created_at LIMIT 1", (advice_id,)).fetchone()
        return self._view(dict(row)) if row is not None else None

    def list(self, *, status: str | None = None, thscode: str | None = None,
             limit: int = 50, offset: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        query = "SELECT * FROM research_predictions"
        conditions: list[str] = []
        params: list[object] = []
        if status:
            conditions.append("status=?")
            params.append(status)
        if thscode:
            conditions.append("thscode=?")
            params.append(thscode)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._storage.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._view(dict(row)) for row in rows]

    def _view(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "source": row["source"],
            "thscode": row["thscode"],
            "name": row["name"],
            "as_of_date": row["as_of_date"],
            "horizon": row["horizon"],
            "probabilities": {"up": row["p_up"], "flat": row["p_flat"], "down": row["p_down"]},
            "dominant_scenario": row["dominant_scenario"],
            "event": row["event"],
            "return_threshold_pct": row["return_threshold_pct"],
            "probability_source": row["probability_source"],
            "rationale": row["rationale"],
            "counter_evidence": json.loads(row["counter_evidence_json"] or "[]"),
            "invalidations": json.loads(row["invalidations_json"] or "[]"),
            "evidence_snapshot": json.loads(row["evidence_snapshot_json"] or "[]"),
            "evidence_hash": row["evidence_hash"],
            "record_hash": row["record_hash"],
            "model_profile_id": row["model_profile_id"],
            "prompt_version": row["prompt_version"],
            "linked_advice_id": row["linked_advice_id"],
            "status": row["status"],
            "settlement": json.loads(row["settlement_json"]) if row["settlement_json"] else None,
            "settled_at": row["settled_at"],
            "created_at": row["created_at"],
        }

    # -- 完整性校验 ------------------------------------------------------------
    def verify(self, entry_id: str) -> dict:
        """重算封存哈希；只覆盖预测内容字段，不覆盖 status 等工作流状态。"""
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_predictions WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "研究预测条目不存在。", 404)
        record = self._sealed_record(dict(row))
        recomputed = _canonical_hash(record)
        stored = row["record_hash"]
        return {"id": entry_id, "record_hash": stored, "recomputed_hash": recomputed,
                "intact": stored == recomputed}

    def verify_all(self) -> dict:
        with self._storage.connect() as db:
            rows = db.execute("SELECT id FROM research_predictions").fetchall()
        checked = 0
        violations: list[dict] = []
        for row in rows:
            result = self.verify(row["id"])
            checked += 1
            if not result["intact"]:
                violations.append({"id": row["id"]})
        return {"checked": checked, "intact": not violations, "violations": violations}

    def _sealed_record(self, row: dict) -> dict:
        """封存哈希的覆盖范围：登记时的预测内容。结算只追加 settlement 字段。"""
        return {
            "id": row["id"],
            "source": row["source"],
            "thscode": row["thscode"],
            "name": row["name"],
            "as_of_date": row["as_of_date"],
            "horizon": row["horizon"],
            "p_up": row["p_up"],
            "p_flat": row["p_flat"],
            "p_down": row["p_down"],
            "dominant_scenario": row["dominant_scenario"],
            "event": row["event"],
            "return_threshold_pct": row["return_threshold_pct"],
            "probability_source": row["probability_source"],
            "rationale": row["rationale"],
            "counter_evidence": json.loads(row["counter_evidence_json"] or "[]"),
            "invalidations": json.loads(row["invalidations_json"] or "[]"),
            "evidence_snapshot": json.loads(row["evidence_snapshot_json"] or "[]"),
            "evidence_hash": row["evidence_hash"],
            "model_profile_id": row["model_profile_id"],
            "prompt_version": row["prompt_version"],
            "linked_advice_id": row["linked_advice_id"],
            "created_at": row["created_at"],
        }

    # -- 结算 ----------------------------------------------------------------
    async def settle(self, entry_id: str, market) -> dict:
        """按实际交易 K 线结算主导情景；时点口径与执行建议台账一致。"""
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM research_predictions WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "研究预测条目不存在。", 404)
        entry = self._view(dict(row))
        if entry["status"] != "sealed":
            return entry
        window_days = HORIZON_MS.get(entry["horizon"], HORIZON_MS["custom"]) // 86_400_000
        base_cutoff_date = _base_cutoff_date(entry["created_at"])
        start_date = _shift_date(base_cutoff_date, -14)
        end_date = _shift_date(base_cutoff_date, window_days * 2 + 14)
        history = await market.fetch_price_history(
            entry["thscode"], start_ms=_date_to_ms(start_date), end_ms=_date_to_ms(end_date))
        if history.status is None or not history.records:
            raise AppError("data_unavailable", "暂无本地行情数据，无法结算；请先刷新该标的行情。", 409)
        base_close = None
        all_closes: list[tuple[str, float]] = []
        for record in history.records:
            close_field = record.fields.get("close_price") or record.fields.get("close")
            if close_field is None or close_field.value is None:
                continue
            bar_date = str(record.period_key)[:10]
            all_closes.append((bar_date, float(close_field.value)))
            if bar_date <= base_cutoff_date and (base_close is None or bar_date > base_close[0]):
                base_close = (bar_date, float(close_field.value))
        if base_close is None:
            raise AppError("data_unavailable", "跟踪窗口内的行情尚未齐备，稍后可再次结算。", 409)
        forward_closes = sorted({date: price for date, price in all_closes
                                 if date > base_close[0]}.items())
        if len(forward_closes) < window_days:
            raise AppError("data_unavailable", "跟踪窗口内的交易日行情尚未齐备，稍后可再次结算。", 409)
        final_date, final_price = forward_closes[window_days - 1]
        change_pct = (final_price - base_close[1]) / base_close[1] * 100.0
        threshold = float(entry["return_threshold_pct"])
        event = entry["event"]
        if event == "positive_return":
            hit = change_pct >= threshold
        elif event == "negative_return":
            hit = change_pct <= -threshold
        else:
            hit = abs(change_pct) <= threshold
        probability = float(entry["probabilities"][entry["dominant_scenario"]]) / 100.0
        settlement = {
            "kind": "research_prediction",
            "event": event,
            "dominant_scenario": entry["dominant_scenario"],
            "probability": probability,
            "return_threshold_pct": threshold,
            "hit": hit,
            "squared_error": round((probability - float(hit)) ** 2, 6),
            "base_date": base_close[0],
            "final_date": final_date,
            "base_close": base_close[1],
            "final_close": final_price,
            "change_pct": round(change_pct, 2),
            "window_days": window_days,
            "basis": "ths_daily_close",
            "window_basis": "observed_trading_bars",
        }
        with self._storage.connect() as db:
            db.execute("UPDATE research_predictions SET status='settled', settled_at=?, settlement_json=? WHERE id=?",
                (_now_iso(), json.dumps(settlement, ensure_ascii=False), entry_id))
        return self.get(entry_id)

    # -- 汇总 ----------------------------------------------------------------
    def summary(self, *, limit: int = 200) -> dict:
        """研究预测轨自己的统计；独立样本去重口径与执行建议台账一致。"""
        with self._storage.connect() as db:
            counts = {row["status"]: row["n"] for row in db.execute(
                "SELECT status, COUNT(*) AS n FROM research_predictions GROUP BY status").fetchall()}
            rows = db.execute(
                "SELECT thscode, horizon, created_at, settlement_json FROM research_predictions "
                "WHERE settlement_json IS NOT NULL ORDER BY created_at LIMIT ?"
                , (max(1, min(int(limit), 500)),)).fetchall()
        samples: list[dict] = []
        for row in rows:
            settlement = json.loads(row["settlement_json"] or "{}")
            if settlement.get("hit") is None or settlement.get("probability") is None:
                continue
            samples.append({
                "thscode": row["thscode"],
                "created_at": row["created_at"],
                "end_date": settlement.get("final_date"),
                "window_days": int(settlement.get("window_days") or 20),
                "probability": float(settlement["probability"]),
                "hit": bool(settlement["hit"]),
            })
        independent = _independent_observations(samples, horizon_days=20)
        hit_rate = (sum(item["hit"] for item in independent) / len(independent)) if independent else None
        brier = (sum((item["probability"] - float(item["hit"])) ** 2
                     for item in independent) / len(independent)) if independent else None
        return {
            "sealed": counts.get("sealed", 0),
            "settled": counts.get("settled", 0),
            "void": counts.get("void", 0),
            "independent_samples": len(independent),
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "brier": round(brier, 6) if brier is not None else None,
            "sample_sufficient": len(independent) >= 30,
        }
