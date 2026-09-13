"""C1 双轨独立结算：让「策略原始信号」与「AI 裁决」各自可结算，差值即 LLM 的边际贡献。

## 为什么需要它

方案 02 §4 指出的缺陷是：LLM 的裁决结果被门禁**压制掉**了，而不是记录为一条独立轨道。
实测确认了这一点——`advisor/graph.py:295` 的 `card = self._enrich_card(...)` 会把
"候选为空 → 强制 wait"、"存在冲突 → 降级 wait" 写回卡片，于是**模型原本说了什么**
在登记时已经不存在了。

结果是：无法回答"如果把 LLM 换成抛硬币，结果会更好还是更差"。而这是判断要不要继续让
LLM 参与决策的**唯一诚实依据**。

## 两条轨的定义（必须显式，且都是"原始"的）

| 轨 | 来源 | 为什么是这个 |
|---|---|---|
| `strategy` | 候选策略实验的 `signal_snapshot`（`next_position`）+ 由历史样本估计的经验概率 | 确定性层只能提名，不能决定最终档位；它自己的那次提名必须单独留档 |
| `ai` | **门禁改写之前**的 LLM 裁决（direction / confidence / research_prediction） | 用改写后的卡片做"AI 轨"等于拿被治理层修剪过的结果去评价治理层，结论必然失真 |

**两轨共享同一个 `as_of_date` 与到期窗口**——否则差值比较的不是同一个市场区间，毫无意义。
`as_of_basis` 记录该日期的来源（候选信号快照 / 登记日），便于事后核验。

## 结算口径

只用**本地 DuckDB 的公司行动总收益序列**（P0-1），不联网、可确定性复现（D6）。
结算由用户显式触发，不做后台定时。

命中与分数：

- 方向命中率：`accumulate` → 命中 = 到期收益 ≥ +阈值；`avoid`/`reduce` → 命中 = ≤ −阈值；
  **`wait`/`hold`/`abstain` 是弃权，不计入命中率**（它们计入"等待/拒答比例"）。
- 二元 Brier：事件固定为「到期收益 ≥ +阈值」，两轨都用它，因此**可直接比较**。
  AI 轨的二值概率取 `research_prediction.p_up`；策略轨取历史经验概率。
- 三分类 Brier：仅 AI 轨有完整的 p_up/p_flat/p_down 时给出，作为附加信息。

**样本不足时不下结论**：方案 G8 要求 ≥30 条已结算才看方向命中率，本模块把它做成
`sufficient` 标志与 `stop_criteria` 判定，而不是让调用方自己记得。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..errors import AppError
from ..storage import Storage

#: 方向 → 该方向预期的事件。弃权方向（wait/hold/abstain）不在表内。
BULLISH = {"accumulate"}
BEARISH = {"avoid", "reduce"}
ABSTAINING = {"wait", "hold", "abstain"}

#: 方案 G8：方向命中率至少要这么多条已结算样本才允许看。
MIN_SETTLED_FOR_VERDICT = 30

CST = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shanghai_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone(CST).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)[:10]


def _day_shift(date_str: str, days: int) -> str:
    return (datetime.fromisoformat(date_str).date() + timedelta(days=days)).isoformat()


def _threshold(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.2
    return number if number > 0 else 0.2


def _binary_probability(probability: dict) -> float | None:
    """把概率字典折算成"到期收益 ≥ +阈值"的单值概率（两轨可比的那一个）。"""
    value = probability.get("p_up")
    if value is None:
        value = probability.get("probability")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1:  # 允许 0-100 的写法
        number /= 100.0
    return min(max(number, 0.0), 1.0)


def wilson_interval(hits: int, total: int, *, z: float = 1.96) -> tuple[float, float] | None:
    """二项比例的 Wilson 95% 置信区间（小样本下比正态近似可靠）。"""
    if total <= 0:
        return None
    phat = hits / total
    denominator = 1 + z * z / total
    center = (phat + z * z / (2 * total)) / denominator
    margin = (z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


class DualTrackLedger:
    """`track_settlements` 的封存、独立结算与边际贡献报告。"""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    # --- 封存 -------------------------------------------------------------
    def seal(self, *, advice_id: str, batch_id: str | None, thscode: str, horizon_days: int,
             as_of_date: str, as_of_basis: str, strategy_forecasts: list[dict],
             raw_verdict: dict | None, created_at: str | None = None) -> list[dict]:
        """为一条建议封存两条轨。`raw_verdict` 是**门禁改写之前**的 LLM 裁决。"""
        now = created_at or _now_iso()
        threshold = _threshold((raw_verdict or {}).get("return_threshold_pct"))
        rows = [
            self._strategy_row(advice_id=advice_id, batch_id=batch_id, thscode=thscode,
                               horizon_days=horizon_days, as_of_date=as_of_date,
                               as_of_basis=as_of_basis, forecasts=strategy_forecasts, now=now),
            self._ai_row(advice_id=advice_id, batch_id=batch_id, thscode=thscode,
                         horizon_days=horizon_days, as_of_date=as_of_date,
                         as_of_basis=as_of_basis, verdict=raw_verdict, threshold=threshold, now=now),
        ]
        with self._storage.connect() as db:
            for row in rows:
                db.execute(
                    "INSERT OR IGNORE INTO track_settlements(id, advice_id, batch_id, track, "
                    "thscode, horizon_days, as_of_date, as_of_basis, due_date, direction, "
                    "confidence, probability_json, return_threshold_pct, eligible, "
                    "ineligible_reason, status, settled_at, settlement_json, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'sealed',NULL,NULL,?)",
                    (row["id"], row["advice_id"], row["batch_id"], row["track"], row["thscode"],
                     row["horizon_days"], row["as_of_date"], row["as_of_basis"],
                     _day_shift(row["as_of_date"], max(1, row["horizon_days"]) * 2 + 14),
                     row["direction"], row["confidence"],
                     json.dumps(row["probability"], ensure_ascii=False),
                     row["return_threshold_pct"], int(row["eligible"]),
                     row["ineligible_reason"], now))
        return self.for_advice(advice_id)

    def _strategy_row(self, *, advice_id, batch_id, thscode, horizon_days, as_of_date,
                      as_of_basis, forecasts, now) -> dict:
        eligible = [item for item in forecasts if item.get("eligible")
                    and item.get("event") == "positive_return"]
        if eligible:
            item = eligible[0]
            return {"id": uuid4().hex, "advice_id": advice_id, "batch_id": batch_id,
                    "track": "strategy", "thscode": thscode, "horizon_days": horizon_days,
                    "as_of_date": as_of_date, "as_of_basis": as_of_basis,
                    "direction": "accumulate", "confidence": "strategy",
                    "probability": {"probability": item.get("probability")},
                    "return_threshold_pct": _threshold(item.get("return_threshold_pct")),
                    "eligible": True, "ineligible_reason": ""}
        fresh_but_no_edge = [item for item in forecasts if item.get("event") == "positive_return"]
        reason = (fresh_but_no_edge[0].get("reason") or "策略信号缺少可用的历史概率估计。"
                  if fresh_but_no_edge else "本轮没有通过候选门禁且期限匹配的策略信号。")
        return {"id": uuid4().hex, "advice_id": advice_id, "batch_id": batch_id,
                "track": "strategy", "thscode": thscode, "horizon_days": horizon_days,
                "as_of_date": as_of_date, "as_of_basis": as_of_basis,
                "direction": "abstain", "confidence": "strategy", "probability": {},
                "return_threshold_pct": 0.2, "eligible": False, "ineligible_reason": reason}

    def _ai_row(self, *, advice_id, batch_id, thscode, horizon_days, as_of_date, as_of_basis,
                verdict, threshold, now) -> dict:
        verdict = verdict or {}
        prediction = verdict.get("research_prediction") or {}
        probability = {}
        if prediction:
            for key in ("p_up", "p_flat", "p_down"):
                if prediction.get(key) is not None:
                    probability[key] = float(prediction[key])
        direction = str(verdict.get("direction") or "abstain")
        reason = "" if verdict else "本轮没有留下可供独立结算的原始 LLM 裁决（历史数据）。"
        if not verdict:
            direction = "abstain"
        return {"id": uuid4().hex, "advice_id": advice_id, "batch_id": batch_id, "track": "ai",
                "thscode": thscode, "horizon_days": horizon_days, "as_of_date": as_of_date,
                "as_of_basis": as_of_basis, "direction": direction,
                "confidence": str(verdict.get("confidence") or ""), "probability": probability,
                "return_threshold_pct": _threshold(prediction.get("return_threshold_pct")
                                                   if prediction else threshold),
                "eligible": bool(verdict), "ineligible_reason": reason}

    # --- 读取 -------------------------------------------------------------
    def for_advice(self, advice_id: str) -> list[dict]:
        with self._storage.connect() as db:
            rows = db.execute("SELECT * FROM track_settlements WHERE advice_id=? ORDER BY track",
                              (advice_id,)).fetchall()
        return [self._view(dict(row)) for row in rows]

    def list(self, *, track: str | None = None, status: str | None = None,
             batch_id: str | None = None, limit: int = 200) -> list[dict]:
        clauses, params = [], []
        if track:
            clauses.append("track=?")
            params.append(track)
        if status:
            clauses.append("status=?")
            params.append(status)
        if batch_id:
            clauses.append("batch_id=?")
            params.append(batch_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 2000)))
        with self._storage.connect() as db:
            rows = db.execute(f"SELECT * FROM track_settlements {where} "  # noqa: S608 - 仅拼接固定子句
                              "ORDER BY as_of_date DESC, track LIMIT ?", params).fetchall()
        return [self._view(dict(row)) for row in rows]

    def get(self, settlement_id: str) -> dict:
        with self._storage.connect() as db:
            row = db.execute("SELECT * FROM track_settlements WHERE id=?",
                             (settlement_id,)).fetchone()
        if row is None:
            raise AppError("not_found", "双轨结算记录不存在。", 404)
        return self._view(dict(row))

    @staticmethod
    def _view(row: dict) -> dict:
        return {**{key: row[key] for key in
                   ("id", "advice_id", "batch_id", "track", "thscode", "horizon_days",
                    "as_of_date", "as_of_basis", "due_date", "direction", "confidence",
                    "return_threshold_pct", "ineligible_reason", "status", "settled_at",
                    "created_at")},
                "eligible": bool(row["eligible"]),
                "probability": json.loads(row["probability_json"] or "{}"),
                "settlement": json.loads(row["settlement_json"]) if row["settlement_json"] else None}

    def due(self, *, as_of: str | None = None) -> list[dict]:
        """已到期且未结算的轨道（供用户显式触发结算）。"""
        today = as_of or datetime.now(CST).strftime("%Y-%m-%d")
        with self._storage.connect() as db:
            rows = db.execute(
                "SELECT * FROM track_settlements WHERE status='sealed' AND due_date<=? "
                "ORDER BY due_date, track", (today,)).fetchall()
        return [self._view(dict(row)) for row in rows]

    def _series(self, repository, thscode: str, end_date: str) -> list[tuple[str, float]]:
        """本地总收益口径的收盘序列（不联网、可确定性复现）。"""
        from .algorithms.base_rate import total_return_closes

        end_ms = int(datetime.fromisoformat(end_date).replace(tzinfo=CST).timestamp() * 1000)
        history = repository.corporate_action_total_return_history(thscode, 0, end_ms)
        converted = total_return_closes(history.get("items") or [], as_of_ms=end_ms)
        return [(datetime.fromtimestamp(row["date_ms"] / 1000, CST).strftime("%Y-%m-%d"),
                 float(row["close_price"])) for row in converted["closes"]]

    def settle(self, settlement_id: str, repository) -> dict:
        """结算**一条**轨道。两轨各自调用，互不依赖。"""
        current = self.get(settlement_id)
        if current["status"] == "settled":
            return current
        if not current["eligible"]:
            with self._storage.connect() as db:
                db.execute("UPDATE track_settlements SET status='void', settled_at=?, "
                           "settlement_json=? WHERE id=?",
                           (_now_iso(), json.dumps({"kind": "ineligible", "declined": True,
                                                    "reason": current["ineligible_reason"],
                                                    "note": "无有效信号：计入等待/拒答比例，"
                                                            "不计入方向命中率。"},
                                                   ensure_ascii=False), settlement_id))
            return self.get(settlement_id)

        horizon_days = int(current["horizon_days"])
        # 一次取到足够远，再按信号日切成"当时可见"与"到期窗口"两段：
        # 两段必须来自同一条序列，否则会混入不同口径。
        series = self._series(repository, current["thscode"],
                              _day_shift(current["as_of_date"], horizon_days * 2 + 40))
        past = [item for item in series if item[0] <= current["as_of_date"]]
        if not past:
            raise AppError("data_unavailable", "本地行情尚无该标的在信号日及之前的数据，无法结算。", 409)
        base_date, base_close = past[-1]
        future = [item for item in series if item[0] > base_date]
        if len(future) < horizon_days:
            raise AppError("data_unavailable",
                           f"跟踪窗口尚未齐备（已观测 {len(future)}/{horizon_days} 个交易日），稍后再结算。", 409)
        final_date, final_close = future[horizon_days - 1]
        change_pct = (final_close / base_close - 1) * 100.0
        threshold = _threshold(current["return_threshold_pct"])
        outcome = "up" if change_pct >= threshold else ("down" if change_pct <= -threshold else "flat")

        direction = current["direction"]
        hit = None
        if direction in BULLISH:
            hit = outcome == "up"
        elif direction in BEARISH:
            hit = outcome == "down"

        probability = _binary_probability(current["probability"])
        realized = 1.0 if outcome == "up" else 0.0
        binary_brier = round((probability - realized) ** 2, 6) if probability is not None else None
        three_class = None
        if all(key in current["probability"] for key in ("p_up", "p_flat", "p_down")):
            total = 0.0
            for key in ("p_up", "p_flat", "p_down"):
                raw = float(current["probability"][key])
                value = raw / 100.0 if raw > 1 else raw
                total += (value - (1.0 if outcome == key[2:] else 0.0)) ** 2
            three_class = round(total, 6)

        settlement = {"kind": "dual_track", "track": current["track"], "abstained": hit is None,
                      "base_date": base_date, "final_date": final_date, "base_close": base_close,
                      "final_close": final_close, "change_pct": round(change_pct, 2),
                      "return_threshold_pct": threshold, "outcome": outcome,
                      "direction": direction, "hit": hit, "probability": probability,
                      "binary_brier": binary_brier, "three_class_brier": three_class,
                      "horizon_days": horizon_days, "basis": "local_total_return_close",
                      "note": "弃权方向（wait/hold/abstain）不计入方向命中率，只计入等待/拒答比例。"
                              if hit is None else None}
        with self._storage.connect() as db:
            db.execute("UPDATE track_settlements SET status='settled', settled_at=?, "
                       "settlement_json=? WHERE id=?",
                       (_now_iso(), json.dumps(settlement, ensure_ascii=False), settlement_id))
        return self.get(settlement_id)

    def settle_due(self, repository, *, confirmed: bool) -> dict:
        """批量结算已到期轨道。**必须显式确认**——结算是一次数据变更，不是后台动作。"""
        if not confirmed:
            raise AppError("confirmation_required", "请确认后再批量结算已到期的双轨记录。", 422)
        outcomes = []
        for item in self.due():
            try:
                settled = self.settle(item["id"], repository)
                outcomes.append({"id": item["id"], "track": item["track"],
                                 "status": settled["status"],
                                 "settlement": settled["settlement"]})
            except AppError as error:
                outcomes.append({"id": item["id"], "track": item["track"],
                                 "error": {"code": error.code, "message": error.message}})
        return {"eligible": len(outcomes), "outcomes": outcomes}

    # --- 边际贡献报告 -----------------------------------------------------
    def margin_report(self, *, batch_id: str | None = None) -> dict:
        """两轨对比表 + **LLM 的边际贡献 = 两轨差值**。

        样本不足时给出 `sufficient=False`，并明确指出**不得据此下结论**——
        这是方案 G8「≥30 条已结算才看命中率」的程序化落实。
        """
        rows = self.list(status="settled", batch_id=batch_id, limit=2000)
        tracks = {name: self._track_statistics([row for row in rows if row["track"] == name])
                  for name in ("strategy", "ai")}
        strategy, ai = tracks["strategy"], tracks["ai"]
        deltas = {}
        for key in ("hit_rate", "binary_brier", "abstention_rate"):
            left, right = strategy.get(key), ai.get(key)
            deltas[key] = (None if left is None or right is None else round(right - left, 6))
        sufficient = (strategy["settled"] >= MIN_SETTLED_FOR_VERDICT
                      or ai["settled"] >= MIN_SETTLED_FOR_VERDICT)
        return {"batch_id": batch_id, "tracks": tracks,
                "marginal_contribution": {"note": "正数表示 AI 轨在该指标上高于策略轨", **deltas},
                "settled_records": len(rows), "sufficient": sufficient,
                "verdict_note": (None if sufficient else
                                 f"已结算样本不足 {MIN_SETTLED_FOR_VERDICT} 条："
                                 "**不得据此判断 LLM 的边际贡献**，也不得触发停用条件。")}

    @staticmethod
    def _track_statistics(rows: list[dict]) -> dict:
        settled = [row for row in rows if row["status"] == "settled"]
        directional = [row for row in settled if (row["settlement"] or {}).get("hit") is not None]
        hits = sum(1 for row in settled if (row["settlement"] or {}).get("hit") is True)
        briers = [row["settlement"]["binary_brier"] for row in settled
                  if (row["settlement"] or {}).get("binary_brier") is not None]
        abstained = sum(1 for row in settled if (row["settlement"] or {}).get("abstained"))
        interval = wilson_interval(hits, len(directional))
        return {"settled": len(settled), "directional_samples": len(directional),
                "hits": hits,
                "hit_rate": (round(hits / len(directional), 4) if directional else None),
                "hit_rate_ci95": ([round(interval[0], 4), round(interval[1], 4)] if interval else None),
                "binary_brier": (round(sum(briers) / len(briers), 6) if briers else None),
                "abstained": abstained,
                "abstention_rate": (round(abstained / len(settled), 4) if settled else None)}

    def evaluate_stop_criteria(self, batch_id: str | None = None) -> dict:
        """按方案 G8 的预注册停止条件判定（**只看程序化可判的那两条**）。

        - `settled_30`：任一轨 ≥30 条已结算且方向命中率 95% 置信区间**包含 50%** → 无方向优势；
        - `ai_track_worse`：AI 轨命中率显著低于策略轨（区间不重叠）→ 移除 LLM 的方向权限。

        其余条件（走势偏离、工具失败）不是纯统计量，留给人判定——不在这里假装能自动判。
        """
        report = self.margin_report(batch_id=batch_id)
        triggered: list[dict] = []
        for name, stats in report["tracks"].items():
            interval = stats.get("hit_rate_ci95")
            if stats["directional_samples"] >= MIN_SETTLED_FOR_VERDICT and interval:
                if interval[0] <= 0.5 <= interval[1]:
                    triggered.append({"criterion": "settled_30", "track": name,
                                      "detail": f"命中率 {stats['hit_rate']} 的 95% 区间 "
                                                f"{interval} 包含 50% → 无方向优势"})
        strategy, ai = report["tracks"]["strategy"], report["tracks"]["ai"]
        if (strategy.get("hit_rate_ci95") and ai.get("hit_rate_ci95")
                and ai["directional_samples"] >= MIN_SETTLED_FOR_VERDICT
                and strategy["directional_samples"] >= MIN_SETTLED_FOR_VERDICT
                and ai["hit_rate_ci95"][1] < strategy["hit_rate_ci95"][0]):
            triggered.append({"criterion": "ai_track_worse", "track": "ai",
                              "detail": f"AI 轨命中率 95% 区间 {ai['hit_rate_ci95']} 整体低于"
                                        f"策略轨 {strategy['hit_rate_ci95']} → 移除 LLM 方向权限"})
        return {**report, "triggered_stop_criteria": triggered,
                "not_evaluated": ["day_10", "settled_60_weeks_12", "drift"]}
