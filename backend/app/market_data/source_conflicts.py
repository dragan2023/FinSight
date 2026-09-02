"""Cross-source comparability, supplement merging and honest percentiles.

Plan 04 section 2.4: before comparing the same metric across sources, entity,
period, currency, unit, consolidated-vs-parent basis, cumulative-vs-single-
quarter basis, disclosure version and definition must all match. Fully
comparable and primary missing -> the authorized supplement fills the
normalized field while the substitute source is preserved. Any conflict is
shown side by side - never averaged, never max-picked, never silently
overwritten. The user's source choice affects only the new report version;
historical evidence is never mutated (all functions here return new values).

Historical valuation percentiles always state the indicator, window, valid
trading days, negative-value handling, missing ratio and formula; a short
actual window is labelled as such and can never claim a ten-year percentile.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MergeDecision(StrEnum):
    FILL = "fill"
    CONFLICT = "conflict"
    CONSISTENT = "consistent"
    PARALLEL = "parallel"


class ComparabilityKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str
    period: str
    currency: str | None
    unit: str | None
    consolidation: str
    period_basis: str
    definition: str
    version: str | None = None


class SourceConflictOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: str
    label: str
    decision: MergeDecision
    primary_source: str
    supplement_source: str
    primary_value: str | None
    supplement_value: str | None
    note: str


def comparability_key(*, entity: str, period: str, currency: str | None, unit: str | None,
                      consolidation: str, period_basis: str, definition: str,
                      version: str | None = None) -> ComparabilityKey:
    return ComparabilityKey(
        entity=entity, period=period, currency=currency, unit=unit,
        consolidation=consolidation, period_basis=period_basis,
        definition=definition, version=version)


def _comparable(a: ComparabilityKey, b: ComparabilityKey) -> bool:
    # 口径一致才允许比较；version 不同视为不同披露版本，不强并。
    return (a.entity == b.entity and a.period == b.period and a.currency == b.currency
            and a.unit == b.unit and a.consolidation == b.consolidation
            and a.period_basis == b.period_basis and a.definition == b.definition
            and a.version == b.version)


def _value_of(field: object) -> str | None:
    value = getattr(field, "value", field)
    return None if value is None else str(value)


def compare_supplement(primary: dict[str, object], supplement: dict[str, object], *,
                       primary_key: ComparabilityKey,
                       supplement_key: ComparabilityKey,
                       primary_source: str, supplement_source: str,
                       labels: dict[str, str] | None = None) -> list[SourceConflictOutcome]:
    """逐字段给出跨源决策；两个输入都不修改。"""
    outcomes: list[SourceConflictOutcome] = []
    comparable = _comparable(primary_key, supplement_key)
    for definition, supplement_field in supplement.items():
        if definition not in primary:
            continue  # 主源没有该定义：无可比结构，留给缺口而不是硬填
        primary_field = primary[definition]
        primary_value = _value_of(primary_field)
        supplement_value = _value_of(supplement_field)
        if not comparable:
            decision = MergeDecision.PARALLEL
            note = "两个来源的口径（期间/币种/单位/合并口径/累计口径/披露版本）不一致，并列解释，不合并。"
        elif primary_value is None:
            decision = MergeDecision.FILL
            note = "主源缺失该字段，已用已授权补充源填入；替代来源已单独保留。"
        elif primary_value == supplement_value:
            decision = MergeDecision.CONSISTENT
            note = "两个来源数值一致。"
        else:
            decision = MergeDecision.CONFLICT
            note = "两个来源数值冲突，并列显示；不做平均、不取大小、不静默覆盖。"
        outcomes.append(SourceConflictOutcome(
            definition=definition, label=(labels or {}).get(definition, definition),
            decision=decision, primary_source=primary_source,
            supplement_source=supplement_source,
            primary_value=primary_value, supplement_value=supplement_value,
            note=note))
    return outcomes


def resolve_by_choice(outcomes: list[SourceConflictOutcome],
                      choices: dict[str, str] | None = None) -> dict[str, str | None]:
    """用户选择只影响本次新报告：primary 为默认，选择需显式传入。"""
    resolved: dict[str, str | None] = {}
    for outcome in outcomes:
        choice = (choices or {}).get(outcome.definition, "primary")
        if choice == "supplement" and outcome.decision in (
                MergeDecision.FILL, MergeDecision.CONSISTENT, MergeDecision.CONFLICT):
            resolved[outcome.definition] = outcome.supplement_value
        else:
            resolved[outcome.definition] = outcome.primary_value
    return resolved


def _decimal_ok(value: object) -> bool:
    try:
        Decimal(str(value))
        return True
    except Exception:  # noqa: BLE001 - 无法解释的样本不参与排名
        return False


class PercentileMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: str
    window_label: str
    valid_days: int
    missing_ratio: str
    negative_handling: str
    formula: str
    percentile: str
    note: str


def valuation_percentile(series: list[object], current: object, *, indicator: str,
                         span_days: int) -> PercentileMeta:
    """窗口历史分位 + 强制元数据；标签只描述实际窗口。"""
    valid = [Decimal(str(v)) for v in series if v is not None and _decimal_ok(v)]
    current_value = Decimal(str(current)) if current is not None and _decimal_ok(current) else None
    missing_ratio = (Decimal(len(series) - len(valid)) / Decimal(len(series))) if series else Decimal(1)
    if current_value is None or not valid:
        percentile = None
    else:
        below = sum(1 for v in valid if v <= current_value)
        percentile = Decimal(below) * Decimal(100) / Decimal(len(valid))
    years = Decimal(span_days) / Decimal(365)
    if years >= Decimal(10):
        window_label = "近十年及以上历史分位"
    else:
        window_label = "近" + str(span_days) + "个交易日历史分位"
    if percentile is None:
        return PercentileMeta(
            indicator=indicator, window_label=window_label, valid_days=len(valid),
            missing_ratio=format(missing_ratio, "f"), negative_handling="负值参与排名",
            formula="百分比排名：小于等于当前值的样本数 / 有效样本数 × 100",
            percentile="unknown", note="有效样本不足，无法计算分位。")
    return PercentileMeta(
        indicator=indicator, window_label=window_label, valid_days=len(valid),
        missing_ratio=format(missing_ratio, "f"), negative_handling="负值参与排名",
        formula="百分比排名：小于等于当前值的样本数 / 有效样本数 × 100",
        percentile=format(percentile, "f"),
        note="分位仅描述所选窗口内的相对位置，不构成估值判断。")
