"""Reader-facing market-review measures derived from saved supplier records.

The supplier payload is evidence, not a UI model.  This module turns the
small, well-defined daily event sets into Chinese labels, comparable measures
and chart-ready rows without creating an investment recommendation.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any


MODULE_NAMES = {
    "limit_up": "涨停",
    "limit_down": "跌停",
    "limit_break": "炸板",
    "limit_ladder": "连板梯队",
    "hot": "热度榜",
    "dragon_tiger": "龙虎榜",
}


def build_market_dashboard(*, results: dict[str, Any], summary: dict[str, dict]) -> dict:
    """Build only measures supported by the returned modules.

    Counts remain module-specific supplier counts.  In particular, the
    ``炸板率`` denominator is ``涨停 + 炸板`` rather than an inferred market
    population, so a partially unavailable module cannot be mistaken for a
    complete market statistic.
    """
    counts = {key: _count(summary.get(key)) for key in ("limit_up", "limit_down", "limit_break")}
    available = {key: _available(summary.get(key)) for key in MODULE_NAMES}
    denominator = counts["limit_up"] + counts["limit_break"]
    break_rate = round(counts["limit_break"] / denominator * 100, 1) if denominator else None
    return {
        "overview": {
            "cards": [
                _card("涨停家数", counts["limit_up"], "当日已获取的涨停股票数量", "positive"),
                _card("跌停家数", counts["limit_down"], "当日已获取的跌停股票数量", "negative"),
                _card("炸板家数", counts["limit_break"], "盘中触及涨停后未能封住的股票数量", "warning"),
                _card("炸板率", break_rate, "炸板 ÷（涨停 + 炸板）；仅在两类数据均可用时计算", "warning", "%"),
            ],
            "structure": [
                {"label": "涨停", "value": counts["limit_up"], "tone": "positive"},
                {"label": "跌停", "value": counts["limit_down"], "tone": "negative"},
                {"label": "炸板", "value": counts["limit_break"], "tone": "warning"},
            ],
            "interpretation": _pulse_interpretation(counts, break_rate, available),
        },
        "ladder": _ladder_panel(_result_records(results.get("limit_ladder"))),
        "hot": _hot_panel(_result_records(results.get("hot"))),
        "dragon_tiger": _dragon_tiger_panel(_result_records(results.get("dragon_tiger"))),
        "coverage": [
            {
                "label": MODULE_NAMES[key],
                "status": _status_label(summary.get(key)),
                "count": _count(summary.get(key)),
                "gaps": int((summary.get(key) or {}).get("gaps") or 0),
            }
            for key in MODULE_NAMES
            if key in summary
        ],
        "notice": "所有图表均由本机基于当次保存的数据计算；它们描述市场结构，不构成交易建议。",
    }


def _card(label: str, value: int | float | None, description: str, tone: str, unit: str = "家") -> dict:
    return {"label": label, "value": value, "unit": unit, "description": description, "tone": tone}


def _count(item: dict | None) -> int:
    return int((item or {}).get("count") or 0)


def _available(item: dict | None) -> bool:
    return (item or {}).get("status") in {"available", "partial"}


def _status_label(item: dict | None) -> str:
    status = (item or {}).get("status")
    return {"available": "已获取", "partial": "部分获取", "unavailable": "暂不可用"}.get(status, "未获取")


def _pulse_interpretation(counts: dict[str, int], break_rate: float | None, available: dict[str, bool]) -> str:
    if not all(available.get(key) for key in ("limit_up", "limit_down", "limit_break")):
        return "部分涨跌停模块不可用，当前仅展示已获取样本，不对市场强弱作完整判断。"
    rate = "--" if break_rate is None else f"{break_rate:.1f}%"
    return f"当日已获取涨停 {counts['limit_up']} 家、跌停 {counts['limit_down']} 家，炸板率 {rate}。请结合成交、板块和历史序列进一步观察。"


def _result_records(result: Any) -> list[dict]:
    if result is None:
        return []
    records = getattr(result, "records", None)
    if records is None and isinstance(result, dict):
        records = result.get("records")
    return [item for item in (records or []) if isinstance(item, dict) or hasattr(item, "fields")]


def _field(record: Any, *names: str) -> Any:
    fields = record.get("fields", {}) if isinstance(record, dict) else getattr(record, "fields", {})
    for name in names:
        value = fields.get(name) if isinstance(fields, dict) else None
        if value is None:
            continue
        if isinstance(value, dict):
            candidate = value.get("value") if value.get("value") is not None else value.get("raw_value")
        else:
            candidate = getattr(value, "value", None)
            if candidate is None:
                candidate = getattr(value, "raw_value", None)
        if candidate not in (None, ""):
            return candidate
    return None


def _record_value(record: Any, *names: str) -> Any:
    if isinstance(record, dict):
        for name in names:
            if record.get(name) not in (None, ""):
                return record[name]
    return _field(record, *names)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _decode_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _ladder_panel(records: list[dict]) -> dict:
    boards = _decode_json(_record_value(records[0], "boards")) if records else None
    flattened: list[dict] = []
    if isinstance(boards, dict):
        for key, items in boards.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                level = _number(item.get("board_num"))
                if level is None:
                    level = _number("".join(char for char in key if char.isdigit()))
                flattened.append({
                    "level": int(level or 0), "name": item.get("name") or item.get("thscode") or "未命名标的",
                    "thscode": item.get("thscode") or "", "next_day_sealed": item.get("seal_nextday"),
                })
    counts = Counter(item["level"] for item in flattened if item["level"] > 0)
    return {
        "levels": [{"label": f"{level} 连板", "value": counts[level]} for level in sorted(counts, reverse=True)],
        "stocks": sorted(flattened, key=lambda item: (-item["level"], item["name"]))[:30],
        "description": "连板梯队按供应商返回的板数分组；“次日封板”仅为已返回字段，不可用时不作推断。",
    }


def _hot_panel(records: list[dict]) -> dict:
    rows = []
    for record in records:
        rank = _number(_record_value(record, "rank"))
        heat = _number(_record_value(record, "heat", "hot_value"))
        if rank is None and heat is None:
            continue
        change = _number(_record_value(record, "rank_change"))
        rows.append({
            "rank": int(rank) if rank is not None else None,
            "name": _record_value(record, "name") or _record_value(record, "thscode") or "未命名标的",
            "thscode": _record_value(record, "thscode") or "",
            "heat": heat,
            "rank_change": int(change) if change is not None else None,
            "trend": _record_value(record, "rank_trend"),
        })
    rows.sort(key=lambda item: (item["rank"] is None, item["rank"] or 10**9))
    movement = Counter(item["trend"] for item in rows if item["trend"])
    return {
        "top": rows[:10],
        "movement": {"up": movement["up"], "down": movement["down"], "flat": movement["flat"]},
        "description": "热度值和排名变化均为供应商当次返回；热度不是成交额，也不等同于资金流。",
    }


def _dragon_tiger_panel(records: list[dict]) -> dict:
    rows = []
    reasons: Counter[str] = Counter()
    for record in records:
        reason = _record_value(record, "reason", "reason_name", "reason_desc")
        if reason:
            reasons[str(reason)] += 1
        rows.append({
            "name": _record_value(record, "name") or _record_value(record, "thscode") or "未命名标的",
            "thscode": _record_value(record, "thscode") or "",
            "reason": reason or "供应商未提供上榜原因",
            "window": _record_value(record, "window", "range_days") or "未提供统计窗口",
        })
    distribution = [{"label": label, "value": value} for label, value in reasons.most_common(8)]
    return {
        "reason_distribution": distribution,
        "stocks": rows[:30],
        "description": "同一股票在不同统计窗口内会分别保留；上榜原因来自供应商字段，不代表行业分类或资金关系。",
    }
