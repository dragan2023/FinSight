"""Piotroski F-Score 的 A 股可用八项实现。"""
from __future__ import annotations

from typing import Mapping


def _number(values: Mapping[str, object], key: str) -> float | None:
    value = values.get(key)
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _roa(values: Mapping[str, object]) -> float | None:
    """资产回报率：优先用**本地口径** `roa`，回退到厂商指标 `total_assets_net_ratio`。

    两个口径不是同一个东西，因此**不合并成一个键**：

    - `roa`（本地，`market_warehouse/financials.py`）：净利润 ÷ **期末**资产总计，
      只有三表就能算，且带披露日 → 可用于历史 point-in-time；
    - `total_assets_net_ratio`（厂商 `indicators`）：实测与本地口径**相对差 <1%**，
      差值方向一致（厂商看起来用**平均**总资产），但它**不带披露日**，只能用于当前时点。

    优先本地口径的理由：它自证（公式与来源都在我们自己手里），而不是拿一个无法追溯
    披露时点的外部数字。回退厂商口径是为了不破坏顾问链既有调用方式。
    """
    local = _number(values, "roa")
    return local if local is not None else _number(values, "total_assets_net_ratio")


def calculate_f_score(current: Mapping[str, object], previous: Mapping[str, object],
                      *, evidence_id: str = "F1") -> dict:
    """计算九项卡片；增发数据不可得时固定为 N/A，不将缺口误当作零。"""
    is_st = current.get("is_st") is True
    listed_days = _number(current, "listed_days")
    if is_st or (listed_days is not None and listed_days < 365):
        reason = "ST 标的" if is_st else "上市未满一年"
        return {
            "algorithm": "Piotroski F-Score", "rating": "中", "score": None,
            "available_items": 0, "total_items": 9, "items": [], "eligible": False,
            "note": f"{reason}不适用 F-Score 比较，已按研究口径剔除。",
        }
    roa = _roa(current)
    previous_roa = _roa(previous)
    cfo = _number(current, "act_cash_flow_net")
    assets = _number(current, "assets_total")
    cfo_roa = cfo / assets if cfo is not None and assets not in (None, 0) else None

    def comparison(key: str, title: str, predicate) -> dict:
        now, before = _number(current, key), _number(previous, key)
        available = now is not None and before is not None
        return {
            "name": title, "available": available,
            "passed": bool(predicate(now, before)) if available else None,
            "evidence_id": evidence_id,
        }

    items = [
        {"name": "资产回报为正", "available": roa is not None,
         "passed": roa > 0 if roa is not None else None, "evidence_id": evidence_id},
        {"name": "经营现金流为正", "available": cfo is not None,
         "passed": cfo > 0 if cfo is not None else None, "evidence_id": evidence_id},
        {"name": "资产回报改善", "available": roa is not None and previous_roa is not None,
         "passed": roa > previous_roa if roa is not None and previous_roa is not None else None,
         "evidence_id": evidence_id},
        {"name": "现金流覆盖利润质量", "available": cfo_roa is not None and roa is not None,
         "passed": cfo_roa > roa if cfo_roa is not None and roa is not None else None,
         "evidence_id": evidence_id},
        comparison("assets_debt_ratio", "资产负债率下降", lambda now, before: now < before),
        comparison("current_ratio", "流动比率改善", lambda now, before: now > before),
        {"name": "未增发", "available": False, "passed": None, "evidence_id": evidence_id,
         "reason": "数据源未提供可靠的增发信息，按 N/A 不计分。"},
        comparison("sale_gross_margin", "销售毛利率改善", lambda now, before: now > before),
        comparison("total_assets_turnover_ratio", "总资产周转改善", lambda now, before: now > before),
    ]
    available = [item for item in items if item["available"]]
    score = sum(1 for item in available if item["passed"])
    return {
        "algorithm": "Piotroski F-Score", "rating": "中", "score": score,
        "available_items": len(available), "total_items": 9, "items": items, "eligible": True,
        "note": "9 项中最多 8 项可由当前数据计算；未增发项恒为 N/A，不计入得分。",
    }
