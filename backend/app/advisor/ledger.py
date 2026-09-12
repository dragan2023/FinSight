"""V2-04 建议台账：登记 → 结算 → 复盘 → 命中率校准（方案 §4.4）。

结算使用同花顺行情数据核对，仅由用户点击或显式开启的免费刷新触发；
绝不自动后台结算。每次输出落盘参数与结果快照哈希（合规 §4.4.4）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from ..errors import AppError
from ..storage import Storage
from ..strategy.registry import get_spec
from .output_models import tier_gate_snapshot, upgrade_suggestion_view

HORIZON_MS = {
    "days_5": 5 * 86_400_000,
    "days_20": 20 * 86_400_000,
    "quarter": 90 * 86_400_000,
    "year": 365 * 86_400_000,
    "custom": 20 * 86_400_000,
}
PROMPT_VERSION = "advisor-v2.1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _params_hash(card: dict) -> str:
    payload = json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _load_experiment(storage: Storage, ref: str) -> dict | None:
    """解析 strategy_id@version#experiment_id 并读取本地实验；格式不符返回 None。"""
    try:
        strategy_id, rest = ref.split("@", 1)
        version, experiment_id = rest.split("#", 1)
    except ValueError:
        return None
    try:
        if get_spec(strategy_id).version != version:
            return None
    except KeyError:
        return None
    with storage.connect() as db:
        row = db.execute(
            "SELECT thscode, validation_grade, input_json, result_json FROM strategy_experiments "
            "WHERE id=? AND strategy_id=? AND strategy_version=?",
            (experiment_id, strategy_id, version)).fetchone()
    if row is None:
        return None
    return {"grade": row["validation_grade"], "thscode": row["thscode"],
            "input": json.loads(row["input_json"] or "{}"),
            "result": json.loads(row["result_json"] or "{}")}


def _empirical_probability(storage: Storage, refs: list, *, event: str | None,
                           horizon_days: int) -> tuple[float, float, dict] | None:
    """候选策略同版本、同期限的历史经验概率（计划 04 §3.1）。

    概率只来自该候选实验净值曲线的非重叠等宽窗口统计，窗口按交易日近似预测期限；
    独立窗口少于 30 个时返回 None，绝不用模型主观三情景概率替代。
    阈值默认覆盖该实验的往返成本假设（2 × 单边 bps）。
    """
    if event is None or horizon_days <= 0:
        return None
    for ref in refs:
        experiment = _load_experiment(storage, str(ref))
        if experiment is None or experiment["grade"] != "candidate":
            continue
        curve = experiment["result"].get("curve") or []
        # 一个窗口消耗 horizon_days 个步长（h+1 个点）；窗口数按可用步长计算，末点必须存在。
        windows = (len(curve) - 1) // horizon_days if len(curve) else 0
        if windows < 30:
            continue
        threshold = round(2 * float(experiment["input"].get("one_way_cost_bps", 10)) / 100, 4)
        hits = 0
        for index in range(windows):
            start_value = float(curve[index * horizon_days].get("net_value") or 0)
            end_value = float(curve[(index + 1) * horizon_days].get("net_value") or 0)
            if start_value <= 0:
                continue
            change_pct = (end_value / start_value - 1) * 100
            hit = change_pct >= threshold if event == "positive_return" else change_pct <= -threshold
            hits += 1 if hit else 0
        probability = round(hits / windows, 4)
        basis = {"experiment_id": str(ref).split("#", 1)[1], "events": windows,
                 "return_threshold_pct": threshold,
                 "note": "概率来自同版本候选实验净值曲线的非重叠窗口统计；期限按交易日近似。"}
        return probability, threshold, basis
    return None


def _forecast_event(card: dict, storage: Storage) -> dict:
    """预测事件完全由服务器依据方向和候选策略快照生成。"""
    horizon_days = HORIZON_MS.get(card.get("horizon"), HORIZON_MS["custom"]) // 86_400_000
    direction = card.get("direction")
    event = "positive_return" if direction in {"accumulate", "hold"} else (
        "negative_return" if direction in {"reduce", "avoid"} else None)
    refs = list(card.get("strategy_refs") or [])
    empirical = _empirical_probability(storage, refs, event=event, horizon_days=horizon_days)
    if empirical is not None:
        probability, threshold, basis = empirical
        return {"event": event, "horizon_days": horizon_days, "probability": probability,
                "return_threshold_pct": threshold, "source": "strategy_empirical",
                "strategy_refs": refs, "probability_basis": basis}
    # 没有足够独立历史窗口时明确无经验概率，不伪造。
    return {"event": event, "horizon_days": horizon_days, "probability": None,
            "return_threshold_pct": 0.2, "source": "unavailable" if not refs else "strategy_empirical_pending",
            "strategy_refs": refs}


def _strategy_forecasts(storage: Storage, refs: list, *, thscode: str,
                        horizon_days: int, registered_at: str) -> list[dict]:
    """冻结本轮候选策略的确定性原始信号，与 AI 综合方向分轨评估。"""
    forecasts: list[dict] = []
    registered_date = _shanghai_date(registered_at)
    for ref in refs:
        experiment = _load_experiment(storage, str(ref))
        if experiment is None or experiment["grade"] != "candidate" or experiment["thscode"] != thscode:
            continue
        snapshot = experiment["result"].get("signal_snapshot") or {}
        as_of = snapshot.get("as_of")
        age_days = None
        if as_of:
            try:
                age_days = (datetime.fromisoformat(registered_date).date()
                            - datetime.fromisoformat(str(as_of)).date()).days
            except ValueError:
                age_days = None
        fresh = age_days is not None and 0 <= age_days <= 7
        next_position = snapshot.get("next_position")
        event = "positive_return" if next_position == 1 else None
        empirical = (_empirical_probability(storage, [ref], event=event,
                                             horizon_days=horizon_days)
                     if event is not None else None)
        probability = empirical[0] if empirical is not None else None
        threshold = empirical[1] if empirical is not None else round(
            2 * float(experiment["input"].get("one_way_cost_bps", 10)) / 100, 4)
        forecasts.append({
            "track": "strategy_only", "strategy_ref": str(ref),
            "signal": snapshot.get("action", "unavailable"), "event": event,
            "horizon_days": horizon_days, "probability": probability,
            "return_threshold_pct": threshold, "signal_as_of": as_of,
            "signal_age_days": age_days, "fresh": fresh,
            "eligible": bool(fresh and event and probability is not None),
            "reason": (None if fresh else "策略信号不是登记日前 7 个自然日内生成的最新快照。")
                      if snapshot else "旧实验没有确定性信号快照；请重新运行实验。",
        })
    return forecasts


class AdvisorLedger:
    """advice_ledger 的读写与结算统计。"""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def register(self, card: dict, *, session_id: str | None = None,
                 evidence: list[dict] | None = None,
                 model_profile_id: str | None = None,
                 prompt_version: str = PROMPT_VERSION) -> dict:
        from uuid import uuid4
        entry_id = uuid4().hex
        now = _now_iso()
        horizon_days = HORIZON_MS.get(card.get("horizon"), HORIZON_MS["custom"]) // 86_400_000
        strategy_forecasts = _strategy_forecasts(
            self._storage, list(card.get("strategy_refs") or []), thscode=card["thscode"],
            horizon_days=horizon_days, registered_at=now)
        with self._storage.connect() as db:
            forecast = _forecast_event(card, self._storage)
            evidence_json = json.dumps(evidence or [], ensure_ascii=False)
            db.execute(
                "INSERT INTO advice_ledger(id, session_id, thscode, name, direction, confidence, "
                "horizon, card_json, evidence_json, params_hash, strategy_ref_json, forecast_event_json, "
                "confidence_basis_json, evidence_hash, strategy_forecast_json, prompt_version, "
                "model_profile_id, tier_snapshot_json, status, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'tracking',?)",
                (entry_id, session_id, card["thscode"], card["name"], card["direction"],
                 card["confidence"], card["horizon"],
                 json.dumps(card, ensure_ascii=False), evidence_json, _params_hash(card),
                 json.dumps(card.get("strategy_refs") or [], ensure_ascii=False),
                 json.dumps(forecast, ensure_ascii=False),
                 json.dumps(card.get("confidence_basis") or {}, ensure_ascii=False),
                 hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()[:16],
                 json.dumps(strategy_forecasts, ensure_ascii=False), prompt_version,
                 model_profile_id,
                 json.dumps(tier_gate_snapshot(card), ensure_ascii=False), now))
        return self.get(entry_id)

    def get(self, entry_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM advice_ledger WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "台账条目不存在。", 404)
        return self._view(dict(row))

    def list(self, *, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._storage.connect() as db:
            if status:
                rows = db.execute(
                    "SELECT * FROM advice_ledger WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset)).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM advice_ledger ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)).fetchall()
        return [self._view(dict(row)) for row in rows]

    def _view(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "thscode": row["thscode"],
            "name": row["name"],
            "direction": row["direction"],
            "confidence": row["confidence"],
            "horizon": row["horizon"],
            "card": upgrade_suggestion_view(json.loads(row["card_json"] or "{}")),
            "evidence": json.loads(row.get("evidence_json") or "[]"),
            "strategy_refs": json.loads(row.get("strategy_ref_json") or "[]"),
            "forecast_event": json.loads(row.get("forecast_event_json") or "{}"),
            "confidence_basis": json.loads(row.get("confidence_basis_json") or "{}"),
            "strategy_forecasts": json.loads(row.get("strategy_forecast_json") or "[]"),
            "tier_snapshot": json.loads(row.get("tier_snapshot_json") or "{}"),
            "evidence_hash": row.get("evidence_hash") or "",
            "params_hash": row["params_hash"],
            "prompt_version": row.get("prompt_version") or "advisor-v2",
            "model_profile_id": row.get("model_profile_id"),
            "status": row["status"],
            "settled_at": row["settled_at"],
            "settlement": json.loads(row["settlement_json"]) if row["settlement_json"] else None,
            "created_at": row["created_at"],
        }

    async def settle(self, entry_id: str, market, *, refresh: bool = False) -> dict:
        """按实际可用交易 K 线结算；策略原始轨与 AI 综合轨共享同一价格窗口。"""
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM advice_ledger WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "台账条目不存在。", 404)
        entry = self._view(dict(row))
        if entry["status"] != "tracking":
            return entry
        card = entry["card"]
        thscode = entry["thscode"]
        if entry["direction"] == "abstain" and not entry.get("strategy_forecasts"):
            # 没有策略原始轨时，主动拒答无需行情即可作废。
            with self._storage.connect() as db:
                db.execute(
                    "UPDATE advice_ledger SET status='void', settled_at=?, "
                    "settlement_json=? WHERE id=?",
                    (_now_iso(), json.dumps({"kind": "abstain", "declined": True,
                                             "reason": "主动拒答：证据不足以形成可结算方向；计入覆盖率，不计入方向错误率。"},
                                            ensure_ascii=False), entry_id))
            return self.get(entry_id)
        window_days = HORIZON_MS.get(entry["horizon"], HORIZON_MS["custom"]) // 86_400_000
        base_cutoff_date = _base_cutoff_date(entry["created_at"])
        start_date = _shift_date(base_cutoff_date, -14)
        # 交易日窗口用日 K 根数判断；请求区间保守覆盖周末、长假与短暂停牌。
        end_date = _shift_date(base_cutoff_date, window_days * 2 + 14)
        history = await market.fetch_price_history(
            thscode, start_ms=_date_to_ms(start_date), end_ms=_date_to_ms(end_date))
        if history.status is None or not history.records:
            raise AppError("data_unavailable", "暂无本地行情数据，无法结算；请先刷新该标的行情。", 409)
        base_close = None   # (date_str, price) 登记日或之前最后一个收盘
        all_closes: list[tuple[str, float]] = []
        for record in history.records:
            close_field = record.fields.get("close_price") or record.fields.get("close")
            if close_field is None or close_field.value is None:
                continue
            price = float(close_field.value)
            bar_date = str(record.period_key)[:10]
            all_closes.append((bar_date, price))
            if bar_date <= base_cutoff_date and (base_close is None or bar_date > base_close[0]):
                base_close = (bar_date, price)
        if base_close is None:
            raise AppError("data_unavailable", "跟踪窗口内的行情尚未齐备，稍后可再次结算。", 409)
        forward_closes = sorted({date: price for date, price in all_closes
                                 if date > base_close[0]}.items())
        if len(forward_closes) < window_days:
            raise AppError("data_unavailable", "跟踪窗口内的交易日行情尚未齐备，稍后可再次结算。", 409)
        final_close = forward_closes[window_days - 1]
        window_closes = [(date, price) for date, price in all_closes
                         if base_close[0] < date <= final_close[0]]
        change_pct = (final_close[1] - base_close[1]) / base_close[1] * 100.0
        strategy_results = []
        for item in entry.get("strategy_forecasts") or []:
            strategy_event = item.get("event")
            strategy_probability = item.get("probability")
            strategy_hit = None
            if item.get("eligible") and strategy_event == "positive_return":
                strategy_hit = change_pct >= float(item.get("return_threshold_pct") or 0.0)
            strategy_results.append({**item, "hit": strategy_hit,
                "squared_error": round((float(strategy_probability) - float(strategy_hit)) ** 2, 6)
                if strategy_hit is not None and strategy_probability is not None else None})
        if entry["direction"] in {"wait", "abstain"}:
            sentinels = ([_evaluate_watchpoint(text, window_closes)
                          for text in (card.get("watchpoints") or [])]
                         if entry["direction"] == "wait" else [])
            settlement = {"kind": entry["direction"], "declined": entry["direction"] == "abstain",
                          "window_days": window_days, "sentinels": sentinels,
                          "base_date": base_close[0], "final_date": final_close[0],
                          "base_close": base_close[1], "final_close": final_close[1],
                          "change_pct": round(change_pct, 2),
                          "window_basis": "observed_trading_bars",
                          "strategy_results": strategy_results,
                          "reason": "主动拒答：不纳入顾问方向错误率；策略原始轨仍独立结算。"
                                    if entry["direction"] == "abstain" else None,
                          "note": "观察哨不纳入顾问方向统计；策略原始轨仍独立结算。"
                                  if entry["direction"] == "wait" else None}
            with self._storage.connect() as db:
                db.execute(
                    "UPDATE advice_ledger SET status='void', settled_at=?, "
                    "settlement_json=? WHERE id=?",
                    (_now_iso(), json.dumps(settlement, ensure_ascii=False), entry_id))
            return self.get(entry_id)
        forecast = entry["forecast_event"]
        threshold = float(forecast.get("return_threshold_pct") or 0.0)
        event = forecast.get("event")
        hit = change_pct >= threshold if event == "positive_return" else change_pct <= -threshold
        probability = forecast.get("probability")
        settlement = {
            "kind": "price",
            "base_date": base_close[0],
            "final_date": final_close[0],
            "base_close": base_close[1],
            "final_close": final_close[1],
            "change_pct": round(change_pct, 2),
            "hit": hit,
            "event": event,
            "return_threshold_pct": threshold,
            "probability": probability,
            "squared_error": round((float(probability) - float(hit)) ** 2, 6) if probability is not None else None,
            "window_days": window_days,
            "basis": "ths_daily_close",
            "window_basis": "observed_trading_bars",
            "strategy_results": strategy_results,
        }
        with self._storage.connect() as db:
            db.execute(
                "UPDATE advice_ledger SET status='settled', settled_at=?, settlement_json=? WHERE id=?",
                (_now_iso(), json.dumps(settlement, ensure_ascii=False), entry_id))
        return self.get(entry_id)

    async def settle_eligible(self, market, *, confirmed: bool) -> dict:
        if not confirmed:
            raise AppError("confirmation_required", "请确认后再批量结算已到期记录。", 422)
        now_date = _shanghai_date(_now_iso())
        entries = [item for item in self.list(status="tracking", limit=50)
                   if _shift_date(_shanghai_date(item["created_at"]), HORIZON_MS.get(item["horizon"], HORIZON_MS["custom"]) // 86_400_000) <= now_date]
        outcomes = []
        for entry in entries:
            try:
                outcomes.append({"id": entry["id"], "entry": await self.settle(entry["id"], market)})
            except AppError as error:
                outcomes.append({"id": entry["id"], "error": {"code": error.code, "message": error.message}})
        return {"eligible": len(entries), "outcomes": outcomes}

    def calibration(self, *, window: int = 20) -> dict:
        """滚动窗口命中率：high 档实际命中 <60% 时综合者提示词注入警告。"""
        with self._storage.connect() as db:
            all_rows = db.execute(
                "SELECT thscode, horizon, direction, confidence, forecast_event_json, settlement_json, created_at, settled_at, status "
                "FROM advice_ledger WHERE settlement_json IS NOT NULL ORDER BY settled_at DESC LIMIT ?",
                (max(1, min(int(window), 200)),)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM advice_ledger").fetchone()[0]
            abstained = db.execute("SELECT COUNT(*) FROM advice_ledger WHERE direction='abstain'").fetchone()[0]
        rows = [row for row in all_rows if row["status"] == "settled"]
        strategy_samples: list[dict] = []
        for row in all_rows:
            settlement = json.loads(row["settlement_json"] or "{}")
            for strategy_result in settlement.get("strategy_results") or []:
                if (strategy_result.get("eligible") and strategy_result.get("hit") is not None
                        and strategy_result.get("probability") is not None):
                    strategy_samples.append({"thscode": row["thscode"], "confidence": "strategy",
                        "created_at": row["created_at"], "end_date": settlement.get("final_date"),
                        "window_days": int(strategy_result.get("horizon_days") or 20),
                        "probability": float(strategy_result["probability"]),
                        "hit": bool(strategy_result["hit"])})
        strategy_summary = _track_summary(_independent_observations(strategy_samples, horizon_days=20))
        if not rows:
            return {"window": 0, "total_advice": total, "settleable": 0, "abstain_count": abstained,
                    "coverage_rate": round(abstained / total, 4) if total else 0.0,
                    "hit_rate": None, "high_rate": None, "warn_high": False,
                    "brier": None, "brier_skill_score": None, "calibration_gap": None,
                    "sample_sufficient": False, "insufficiency_reason": "尚无已结算的方向预测。",
                    "track_comparison": {"advisor_combined": _track_summary([]),
                                         "strategy_only": strategy_summary}, "groups": []}
        hits = 0
        high_total = 0
        high_hits = 0
        samples: list[dict] = []
        groups: dict[tuple[str, str, str], list[tuple[bool, float | None]]] = {}
        for row in rows:
            settlement = json.loads(row["settlement_json"] or "{}")
            if settlement.get("hit") is True:
                hits += 1
            if row["confidence"] == "high":
                high_total += 1
                if settlement.get("hit") is True:
                    high_hits += 1
            forecast = json.loads(row["forecast_event_json"] or "{}")
            probability = settlement.get("probability", forecast.get("probability"))
            if probability is not None:
                samples.append({"thscode": row["thscode"], "confidence": row["confidence"],
                                "created_at": row["created_at"], "end_date": settlement.get("final_date"),
                                "window_days": HORIZON_MS.get(row["horizon"], HORIZON_MS["custom"]) // 86_400_000,
                                "probability": float(probability),
                                "hit": bool(settlement.get("hit"))})
            key = (str(forecast.get("strategy_refs", ["none"])[0] if forecast.get("strategy_refs") else "none"),
                   row["horizon"], row["confidence"])
            groups.setdefault(key, []).append((bool(settlement.get("hit")), float(probability) if probability is not None else None))
        high_rate = (high_hits / high_total) if high_total else None
        # 校准样本按同标的窗口不重叠去重；展示分组仍统计全部已结算条目。
        deduped = _independent_observations(samples, horizon_days=20)
        empirical = [(item["probability"], item["hit"]) for item in deduped]
        brier = sum((probability - actual) ** 2 for probability, actual in empirical) / len(empirical) if empirical else None
        actual_rate = sum(actual for _, actual in empirical) / len(empirical) if empirical else None
        baseline = (sum((actual_rate - actual) ** 2 for _, actual in empirical) / len(empirical)) if empirical else None
        group_views = [{"strategy_ref": key[0], "horizon": key[1], "confidence": key[2],
                        "count": len(values), "hit_rate": round(sum(hit for hit, _ in values) / len(values), 4),
                        "empirical_count": sum(probability is not None for _, probability in values)}
                       for key, values in groups.items()]
        return {
            "window": len(rows),
            "total_advice": total, "settleable": len(rows), "abstain_count": abstained,
            # 覆盖率=（已结算方向 + 主动拒答）/ 总建议：拒答计入覆盖率，不计入方向错误率。
            "coverage_rate": round((len(rows) + abstained) / total, 4) if total else 0.0,
            "hit_rate": round(hits / len(rows), 2),
            "high_rate": round(high_rate, 2) if high_rate is not None else None,
            "warn_high": high_rate is not None and high_rate < 0.6,
            "brier": round(brier, 6) if brier is not None else None,
            "brier_skill_score": round(1 - brier / baseline, 6) if brier is not None and baseline and baseline > 0 else None,
            "calibration_gap": round(abs(sum(probability for probability, _ in empirical) / len(empirical) - actual_rate), 6) if empirical else None,
            "sample_sufficient": len(empirical) >= 30,
            "independent_samples": len(empirical),
            "insufficiency_reason": None if len(empirical) >= 30 else "具有经验概率的独立前向样本少于 30 条。",
            "track_comparison": {
                "advisor_combined": _track_summary(deduped),
                "strategy_only": strategy_summary,
            },
            "groups": group_views,
        }

    def strategy_calibration(self, strategy_id: str, strategy_version: str, horizon_days: int) -> dict:
        """前向升级门槛：独立样本数、Brier、Skill Score、校准差、近期退化与高置信错误率。"""
        prefix = f"{strategy_id}@{strategy_version}#"
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT thscode, settlement_json, created_at FROM advice_ledger "
                "WHERE settlement_json IS NOT NULL").fetchall()
        matched = []
        for row in rows:
            settlement = json.loads(row["settlement_json"] or "{}")
            for result in settlement.get("strategy_results") or []:
                if (result.get("horizon_days") != horizon_days
                        or not str(result.get("strategy_ref", "")).startswith(prefix)
                        or not result.get("eligible") or result.get("probability") is None
                        or result.get("hit") is None):
                    continue
                matched.append({"thscode": row["thscode"], "confidence": "strategy",
                                "created_at": row["created_at"], "end_date": settlement.get("final_date"),
                                "probability": float(result["probability"]),
                                "hit": bool(result["hit"])})
        observations = _independent_observations(matched, horizon_days)
        count = len(observations)
        if not observations:
            return {"count": 0, "eligible": False, "reason": "没有具有经验概率的独立前向结算样本。"}
        brier = sum((item["probability"] - item["hit"]) ** 2 for item in observations) / count
        actual_rate = sum(item["hit"] for item in observations) / count
        baseline = sum((actual_rate - item["hit"]) ** 2 for item in observations) / count
        gap = abs(sum(item["probability"] for item in observations) / count - actual_rate)
        # 最近 20 条与全样本并列：近期显著退化（>0.05）阻止升级。
        recent = observations[-20:]
        recent_brier = sum((item["probability"] - item["hit"]) ** 2 for item in recent) / len(recent)
        degraded = len(observations) > len(recent) and recent_brier > brier + 0.05
        # 策略原始轨没有语言模型的 confidence 档位；经验概率 >= 0.8 视为高置信预测。
        high_items = [item for item in observations if item["probability"] >= 0.8]
        high_error = (sum(not item["hit"] for item in high_items) / len(high_items)) if high_items else 0.0
        eligible = (count >= 30 and brier <= .25 and baseline > 0
                    and (1 - brier / baseline) > 0 and gap <= .15 and not degraded and high_error <= .2)
        reasons = []
        if count < 30: reasons.append("独立样本少于 30 条")
        if brier > .25: reasons.append("Brier 分数高于 0.25")
        if baseline <= 0 or (1 - brier / baseline) <= 0: reasons.append("无技巧 Brier 基准下无技能")
        if gap > .15: reasons.append("校准差高于 0.15")
        if degraded: reasons.append("最近 20 条 Brier 比全样本恶化超过 0.05")
        if high_error > .2: reasons.append("高置信错误率高于 20%")
        return {"count": count, "brier": round(brier, 6), "brier_skill_score": round(1 - brier / baseline, 6) if baseline else None,
                "calibration_gap": round(gap, 6), "recent_brier": round(recent_brier, 6),
                "high_confidence_error_rate": round(high_error, 4), "eligible": eligible,
                "reason": None if eligible else "未达到前向升级门槛：" + "；".join(reasons) + "。"}


_WATCHPOINT_PATTERN = re.compile(
    r"(跌破|低于|下穿|回落至|回落到|突破|高于|上穿|涨至|涨到|升穿|<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)?)")
_BELOW_KEYWORDS = {"跌破", "低于", "下穿", "回落至", "回落到", "<=", "<"}


def _evaluate_watchpoint(text: str, closes: list[tuple[str, float]]) -> dict:
    """确定性评估文本观察哨（计划 04 §3.2.4）。

    只机读「关键字 + 价格」形态的比较条件，命中即记录触发日期；
    无法机读的观察哨保持 triggered=None，留待人工复盘，不做猜测。
    """
    sentinel: dict = {"watchpoint": text, "level": None, "comparison": None,
                      "triggered": None, "trigger_date": None, "note": None}
    match = _WATCHPOINT_PATTERN.search(text or "")
    if match is None:
        sentinel["note"] = "观察哨不含可机读的价格条件，需人工复盘。"
        return sentinel
    keyword, level = match.group(1), float(match.group(2))
    below = keyword in _BELOW_KEYWORDS
    sentinel["level"] = level
    sentinel["comparison"] = "close_below" if below else "close_above"
    for bar_date, close in closes:
        if (close < level) if below else (close > level):
            sentinel["triggered"] = True
            sentinel["trigger_date"] = bar_date
            return sentinel
    sentinel["triggered"] = False
    return sentinel


def _independent_observations(rows: list[dict], horizon_days: int) -> list[dict]:
    """按「同一标的预测窗口不重叠」抽取独立校准样本（阶段04 §3.3）。

    同一标的多条预测窗口重叠时，只保留登记最早的一条进入校准样本；
    其余条目仍可展示，但不重复计入 Brier / 命中率。
    """
    kept: list[dict] = []
    occupied: dict[str, list[tuple[str, str]]] = {}
    for item in sorted(rows, key=lambda entry: entry["created_at"]):
        start = _shanghai_date(item["created_at"])
        end = str(item.get("end_date") or _shift_date(start, int(item.get("window_days") or horizon_days)))
        if any(start < other_end and other_start < end
               for other_start, other_end in occupied.get(item["thscode"], [])):
            continue
        occupied.setdefault(item["thscode"], []).append((start, end))
        kept.append(item)
    return kept


def _track_summary(rows: list[dict]) -> dict:
    if not rows:
        return {"count": 0, "hit_rate": None, "brier": None}
    count = len(rows)
    return {"count": count,
            "hit_rate": round(sum(bool(item["hit"]) for item in rows) / count, 4),
            "brier": round(sum((float(item["probability"]) - float(bool(item["hit"]))) ** 2
                               for item in rows) / count, 6)}


def _shanghai_date(value: str) -> str:
    """登记时间（UTC ISO）→ 上海日期字符串，与行情 period_key 同口径。"""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is not None:
        from datetime import timedelta
        shanghai = moment.utcoffset()
        moment = moment.replace(tzinfo=None) + (timedelta(hours=8) - shanghai)
    return moment.date().isoformat()


def _base_cutoff_date(value: str) -> str:
    """登记发生在 A 股收盘前时，只允许使用上一日及更早的已完成日 K。"""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    if (moment.hour, moment.minute) < (15, 0):
        moment -= timedelta(days=1)
    return moment.date().isoformat()


def _shift_date(date_str: str, days: int) -> str:
    from datetime import date, timedelta
    base = date.fromisoformat(date_str)
    return (base + timedelta(days=days)).isoformat()


def _date_to_ms(date_str: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc).timestamp() * 1000)
