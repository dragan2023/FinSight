"""V2-04 Advisor 用户表达层：枚举 → 中文映射（方案 §3.4 规则一）。

模型永远不输出中文标签本身，界面永远不出现英文枚举；
本表与前端 frontend/src/components/advisor/labels.ts 双侧同源，改动须同步。
"""
from __future__ import annotations

DIRECTION_LABELS = {
    "accumulate": "倾向买入",
    "hold": "继续持有",
    "wait": "暂不买入，等待时机",
    "reduce": "建议减仓",
    "avoid": "回避",
    "abstain": "证据不足，暂不表态",
}

CONFIDENCE_LABELS = {
    "very_low": "把握度：很低",
    "low": "把握度：较低",
    "medium": "把握度：中等",
    "high": "把握度：较高",
}

PROBABILITY_SOURCE_LABELS = {
    "base_rate": "该概率来自历史同类案例统计",
    "subjective": "该概率为分析判断（主观估计）",
}

HORIZON_LABELS = {
    "days_5": "约一周内视角",
    "days_20": "一个月内视角",
    "quarter": "一个季度视角",
    "year": "一年视角",
    "custom": "自定义时间窗",
}

SCENARIO_NAME_LABELS = {"bull": "乐观情景", "base": "基准情景", "bear": "悲观情景"}
RISK_APPETITE_LABELS = {"conservative": "稳健", "balanced": "平衡", "aggressive": "进取"}
LEDGER_STATUS_LABELS = {"tracking": "跟踪中", "settled": "已结算", "void": "已作废"}
CHANNEL_LABELS = {"fts": "关键词", "vector": "语义"}


def label(mapping: dict, key: str | None) -> str:
    """映射缺失时返回原值兜底，绝不抛错挡住展示。"""
    if key is None:
        return ""
    return mapping.get(key, key)
