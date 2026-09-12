"""SuggestionCard v2 的确定性发布门禁。"""
from __future__ import annotations

import re

from ..agent.number_grounding import GroundedMetric, extract_quantities, validate_numbers
from ..errors import AppError
from .evidence import AdvisorEvidenceItem, AdvisorMetric
from .output_models import SuggestionCard

FORBIDDEN_TERMS = (
    "肯定", "必然", "稳赚", "必涨", "必跌", "抄底", "包赚", "稳赢", "稳了",
    "保本", "零风险", "无风险", "闭眼买", "满仓干", "梭哈", "一定涨", "一定跌",
    "百分百", "翻倍", "目标价就是", "稳稳的", "板上钉钉",
)
_ENUM_LEAK = re.compile(r"\b(accumulate|hold|wait|reduce|avoid|abstain|very_low|medium|high|base_rate|subjective|days_5|days_20|quarter|year|bull|base|bear)\b")
_CODE_LEAK = re.compile(r"\b(JSON|SuggestionCard|thscode|scenario|narrative)\b", re.IGNORECASE)
NARRATIVE_MIN_CHARS = 100


def _text_of(card: SuggestionCard) -> str:
    parts = [card.decision_narrative, *card.invalidations, *card.watchpoints]
    parts.extend(item.trigger_condition for item in card.scenarios)
    if card.position_thinking:
        parts.append(card.position_thinking)
    if card.ev_summary is not None:
        parts.append(card.ev_summary.note)
    return " ".join(parts)


def forbidden_hits(text: str) -> list[str]:
    return [term for term in FORBIDDEN_TERMS if term in text]


def _metric_models(metrics: dict[str, AdvisorMetric]) -> list[GroundedMetric]:
    return [GroundedMetric(name=name, value=item.value, unit=item.unit) for name, item in metrics.items()]


def validate_suggestion(card: SuggestionCard, *, evidence_index: dict[str, AdvisorEvidenceItem] | None = None,
                        metrics: dict[str, AdvisorMetric] | None = None,
                        strategy_refs: set[str] | None = None) -> list[str]:
    """返回安全、可用于唯一一次修复的错误摘要；空列表代表可发布。"""
    issues: list[str] = []
    text = _text_of(card)
    hits = forbidden_hits(text)
    if hits:
        issues.append(f"输出含承诺性或绝对化措辞（{'、'.join(hits)}），请改用概率语言。")
    if len(card.decision_narrative.strip()) < NARRATIVE_MIN_CHARS:
        issues.append(f"决策理由不足 {NARRATIVE_MIN_CHARS} 字，请按「证据→排除→结论→反转条件」四段式补充。")
    if _ENUM_LEAK.search(card.decision_narrative) or _CODE_LEAK.search(card.decision_narrative):
        issues.append("决策理由出现英文枚举或代码词，请改用通俗中文表述。")
    if card.direction == "wait" and not any(re.search(r"\d", item) for item in card.watchpoints):
        issues.append("等待时机方向必须给出可量化触发条件（含数字阈值或明确日期）。")

    evidence_index = evidence_index or {}
    metrics = metrics or {}
    if not evidence_index:
        # 单元测试/离线调用可只验证 schema 与文案；实际 graph 一律传入本轮索引。
        return issues
    public_refs = set(evidence_index)
    known_strategy_refs = strategy_refs or set()
    unknown_card_refs = set(card.strategy_refs) - known_strategy_refs
    if unknown_card_refs:
        issues.append(f"strategy_refs 引用了不存在或非候选实验（{'、'.join(sorted(unknown_card_refs)[:4])}）。")
    referenced = [*card.key_evidence, *card.counter_evidence]
    grounded_metric_refs: set[str] = set()
    for claim in card.claims:
        referenced.extend(claim.evidence_refs)
        for metric_ref in claim.metric_refs:
            if metric_ref not in metrics:
                issues.append(f"claims.metric_refs：指标编号不存在（{metric_ref}）。")
        if claim.kind in {"fact", "derived"} and not (claim.evidence_refs or claim.metric_refs):
            issues.append("claims：事实或计算陈述必须引用公开证据或结构化指标。")
        if claim.kind == "inference" and extract_quantities(claim.text):
            issues.append("claims：推断陈述不得携带新的精确数字。")
        if claim.kind == "forecast" and not claim.strategy_refs:
            issues.append("claims：预测陈述必须回指已登记策略或结构化情景。")
        unknown_claim_refs = set(claim.strategy_refs) - known_strategy_refs
        if unknown_claim_refs:
            issues.append(f"claims.strategy_refs 引用了不存在或非候选实验（{'、'.join(sorted(unknown_claim_refs)[:4])}）。")
        grounded_metric_refs.update(claim.metric_refs)
        if claim.kind in {"fact", "derived"} and claim.metric_refs:
            selected = [metrics[ref] for ref in claim.metric_refs if ref in metrics]
            if any(issue.code == "metric_conflict" for issue in validate_numbers(claim.text, [
                GroundedMetric(name=item.name, value=item.value, unit=item.unit) for item in selected
            ])):
                issues.append("claims：事实数字与被引结构化指标不一致。")
    missing = sorted({ref for ref in referenced if ref not in public_refs})
    if missing:
        issues.append(f"证据编号不存在、不可公开或不属于本标的（{'、'.join(missing[:6])}）。")
    if not card.counter_evidence:
        issues.append("必须列出至少一项公开反方事实证据。")
    if extract_quantities(card.decision_narrative):
        allowed_metrics = _metric_models({name: item for name, item in metrics.items()
                                          if name in grounded_metric_refs})
        allowed_metrics.extend(
            GroundedMetric(name=f"scenario_{index}", value=str(value), unit="ratio")
            for index, scenario in enumerate(card.scenarios)
            for value in ([scenario.probability_pct / 100, *scenario.expected_range_pct]
                          if scenario.expected_range_pct else [scenario.probability_pct / 100])
        )
        if any(issue.code == "metric_conflict" for issue in validate_numbers(card.decision_narrative, allowed_metrics)):
            issues.append("决策理由中的数字未落在已通过的陈述、结构化情景或服务器指标中。")
    return issues


def require_valid(card: SuggestionCard, *, evidence_index: dict[str, AdvisorEvidenceItem] | None = None,
                  metrics: dict[str, AdvisorMetric] | None = None) -> None:
    issues = validate_suggestion(card, evidence_index=evidence_index, metrics=metrics)
    if issues:
        raise AppError("suggestion_invalid", "；".join(issues), 422)
