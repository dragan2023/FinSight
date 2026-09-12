"""Altman Z-Score：仅作为困境预警线索，不输出阈值结论。"""
from __future__ import annotations

from typing import Mapping


def _number(values: Mapping[str, object], key: str) -> float | None:
    try:
        value = values.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def calculate_z_score(values: Mapping[str, object], *, evidence_id: str = "F1",
                      peer_scores: list[float] | None = None) -> dict:
    """使用原始五因子公式；任一因子缺失即诚实降级，不作猜测或填零。"""
    assets = _number(values, "assets_total")
    current_assets = _number(values, "total_current_assets")
    liabilities = _number(values, "total_debt")
    retained = _number(values, "retained_earnings")
    ebit = _number(values, "operating_profit")
    market_cap = _number(values, "market_value_equity")
    revenue = _number(values, "operating_income")
    missing = []
    if assets in (None, 0): missing.append("资产总计")
    if current_assets is None or liabilities is None: missing.append("营运资本")
    if retained is None: missing.append("留存收益")
    if ebit is None: missing.append("息税前利润")
    if market_cap is None or liabilities in (None, 0): missing.append("权益市值/负债")
    if revenue is None: missing.append("营业收入")
    score = None
    if not missing:
        score = (1.2 * ((current_assets - liabilities) / assets)
                 + 1.4 * (retained / assets) + 3.3 * (ebit / assets)
                 + 0.6 * (market_cap / liabilities) + 1.0 * (revenue / assets))
    peers = []
    for peer in peer_scores or []:
        try:
            peers.append(float(peer))
        except (TypeError, ValueError):
            continue
    relative = None
    if score is not None and peers:
        relative = {"sample_count": len(peers),
                    "percentile": round(sum(peer <= score for peer in peers) / len(peers) * 100, 1)}
    return {
        "algorithm": "Altman Z-Score", "rating": "弱", "score": None if score is None else round(score, 4),
        "available": score is not None, "missing": missing, "evidence_id": evidence_id,
        "industry_relative": relative,
        "note": "仅作困境预警线索，不给出破产阈值或确定性结论；原始系数在 A 股存在口径迁移风险。",
    }
