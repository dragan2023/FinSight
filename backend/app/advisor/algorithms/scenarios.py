"""三情景期望值与单变量敏感性表。"""
from __future__ import annotations

from typing import Iterable, Mapping


def calculate_scenario_ev(scenarios: Iterable[Mapping[str, object]], *, current_price: float | None = None) -> dict | None:
    """EV 使用各情景区间中点；敏感性把单一情景概率上下调 10 个百分点后归一化。"""
    items = []
    for scenario in scenarios:
        try:
            low, high = scenario["expected_range_pct"]
            items.append({"name": str(scenario["name"]), "probability": float(scenario["probability_pct"]) / 100,
                          "return": (float(low) + float(high)) / 200})
        except (KeyError, TypeError, ValueError):
            return None
    if len(items) != 3 or abs(sum(item["probability"] for item in items) - 1) > 0.0001:
        return None
    expected_return = sum(item["probability"] * item["return"] for item in items)
    sensitivity = []
    for target in items:
        for delta, label in ((0.10, "上调 10 个百分点"), (-0.10, "下调 10 个百分点")):
            changed = min(1.0, max(0.0, target["probability"] + delta))
            others = [item for item in items if item is not target]
            other_total = sum(item["probability"] for item in others)
            if other_total <= 0:
                continue
            adjusted = [{**item, "probability": changed if item is target else item["probability"] * (1 - changed) / other_total}
                        for item in items]
            ev = sum(item["probability"] * item["return"] for item in adjusted)
            sensitivity.append({"variable": f"{target['name']}情景概率{label}", "expected_return_pct": round(ev * 100, 3)})
    implied_price = current_price * (1 + expected_return) if current_price is not None and current_price > 0 else None
    return {"expected_return_pct": round(expected_return * 100, 3),
            "expected_price": None if implied_price is None else round(implied_price, 4),
            "margin_of_safety_pct": round(expected_return * 100, 3), "sensitivity": sensitivity,
            "note": "研究工具，非个性化投资建议；期望值由情景区间中点计算，不是价格预测。"}
