"""Graph state channels and budget helpers (plan 03 section 2.2).

State 只承载本任务后端内存中的公开业务数据：证据、指标、缺口、章节与用量
计数。reasoning_content 由适配器只记字符数（reasoning_chars），正文不进
state、不落盘；ResearchGraph 不配置任何 checkpoint，进程重启后新建分析上下文。
"""

from __future__ import annotations

from typing import TypedDict

MODE_TOOLS = "tools"
MODE_TEMPLATE = "template"
MODE_NONE = "none"

# 计划 §2.2 默认预算：24 次 LLM 请求、24 次结构化取数、8 个工具轮；
# 搜索初版为 0。重试计入对应预算。
DEFAULT_BUDGET: dict = {
    "max_llm_requests": 24,
    "max_fetches": 24,
    "max_tool_rounds": 8,
    "max_searches": 0,
    "max_extractions": 0,
}

USAGE_KEYS = ("llm_requests", "fetches", "tool_rounds", "searches", "extractions")


class ResearchState(TypedDict, total=False):
    # 输入
    task_id: str
    question: str
    scope: dict
    mode: str
    # 身份确认
    identity: dict | None
    # 证据收集
    records: dict[str, list[dict]]
    group_evidence: dict[str, list[str]]
    evidence_index: dict[str, dict]
    gaps: list[dict]
    # 确定性计算
    metrics: list[dict]
    # 分节解释
    sections: list[dict]
    section_states: dict[str, dict]
    raw_sections: list[dict]
    # 预算与用量
    budget: dict
    usage: dict
    # 进度与输出（再分析任务携带父报告与被引用的用户补充）
    notices: list[dict]
    report_id: str | None
    parent_report_id: str | None
    supplement_ids: list[str]
    supplements: list[dict]
    finished_reason: str | None
    task_error: dict | None


def new_usage() -> dict:
    usage = {key: 0 for key in USAGE_KEYS}
    usage["reasoning_chars"] = 0
    return usage


def remaining(budget: dict, usage: dict, kind: str) -> int:
    return max(int(budget.get(f"max_{kind}", 0)) - int(usage.get(kind, 0)), 0)


def can_spend(budget: dict, usage: dict, kind: str, n: int = 1) -> bool:
    return remaining(budget, usage, kind) >= n


def spend(usage: dict, kind: str, n: int = 1) -> dict:
    updated = dict(usage)
    updated[kind] = int(updated.get(kind, 0)) + n
    return updated
