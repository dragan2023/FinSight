"""Business validation of model-written report sections (plan 03 section 2.3).

What this CAN check deterministically:
- references exist and belong to this task's authorized evidence/metrics/gaps;
- evidence scope (security) matches the report scope; an explicit period in
  the claim text that shares no year with any cited evidence period is
  flagged as a conflict (period mentions are never treated as amounts);
- fact/derived claims have at least one grounding reference — otherwise the
  claim is RECLASSIFIED to inference (never silently trusted, never deleted);
- a number retyped by the model that matches no representable form of the
  cited metrics is flagged as a conflict for that section (facts and derived
  claims only). Matching is unit-aware: %, 万, 亿, 万亿 suffixes are scaled
  before comparison, and ratio/CNY/CNY-share metric representations are
  accounted for. Both error directions stay conservative: an unparseable or
  ambiguous number is skipped, never accused.

What this CANNOT check: the correctness of language reasoning itself. Model
self-assessment is never treated as verification; users audit claims through
evidence and metric blocks. Raw model text never enters these objects.

ValidationContext.evidence_index values are shaped
{"thscode": str | None, "period": str | None}; the research graph fills them
from EvidenceMeta (thscode) and the request/report period (period).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

from .output_models import ChartSpec, Claim, ClaimKind, ResearchReport, SectionStatus
from .number_grounding import GroundedMetric, extract_quantities, validate_numbers

_TOLERANCE = Decimal("0.005")
_FLOOR = Decimal("0.0001")

# Date-like period mentions: 2024, 2024-12-31, 2024/12/31, 2024年12月31日,
# 2024年12月, 2024Q3, 2024年三季报, 12月31日, 第3季度. A bare year followed by
# a quantity suffix (2024万) stays a quantity. Extraction failures only skip a
# check; they never invent data.
_PERIOD_WITH_WORD = re.compile(
    r"(?<!\d)(?:19|20)\d{2}\s*(?:[-/年月.]\s*\d{1,2}){0,2}\s*(?:日|Q[1-4]|季[度报]?|年[度报中]?|月)"
)
_PERIOD_BARE_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?![\d.万亿%元倍])")
_PERIOD_SHORT = re.compile(r"(?<!\d)\d{1,2}\s*(?:月(?:\s*\d{1,2}\s*日?)?|季[度报]?|日)")
_YEAR_MENTION = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_QUANTITY = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*(%|万亿|亿|万)?")


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    code: str
    message: str
    claim_index: int | None = None


class ValidationContext:
    """Task-local lookups: what this task may reference and where it points."""

    def __init__(self, *, evidence_index: dict[str, dict], metric_names: set[str],
                 gap_ids: set[str], scope_thscode: str | None) -> None:
        self.evidence_index = evidence_index
        self.metric_names = metric_names
        self.gap_ids = gap_ids
        self.scope_thscode = scope_thscode

    def authorized_evidence(self) -> set[str]:
        """Evidence ids that exist AND belong to this task's security scope."""
        return {ref for ref in self.evidence_index if _scope_ok(ref, self)}


def _decimal_of(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _representations(metric_value: Decimal, unit: str) -> list[Decimal]:
    forms = [metric_value]
    if unit == "ratio":
        forms.append(metric_value * 100)
    if unit in ("CNY", "CNY/share"):
        forms.extend([metric_value / 10000, metric_value / 100000000, metric_value / 1000000000000])
    return forms


def _candidates(base: Decimal, suffix: str | None) -> list[Decimal]:
    if suffix is None:
        return [base]
    if suffix == "%":
        # A percent sign is a scale claim: 0.15% is never a valid retelling of
        # a 0.15 ratio metric. Percent-unit metrics, if ever introduced, must
        # add their own representations instead of loosening this rule.
        return [base / 100]
    if suffix == "万":
        return [base * 10000]
    if suffix == "亿":
        return [base * Decimal("100000000")]
    if suffix == "万亿":
        return [base * Decimal("1000000000000")]
    return [base]


def _quantities(text: str) -> list[list[Decimal]]:
    """Grouped candidate values per number occurrence in the claim text."""
    return [item.candidates for item in extract_quantities(text)]


def _matches_any(candidates: list[Decimal], cited_values: list[tuple[Decimal, str]]) -> bool:
    for value, unit in cited_values:
        for form in _representations(value, unit):
            for candidate in candidates:
                if abs(candidate - form) <= max(abs(form) * _TOLERANCE, _FLOOR):
                    return True
    return False


def _clean_refs(refs: list[str], known: set[str], code: str, message: str,
                section_id: str, claim_index: int | None, issues: list[ValidationIssue]) -> list[str]:
    kept = []
    for ref in refs:
        if ref in known:
            kept.append(ref)
        else:
            issues.append(ValidationIssue(section_id=section_id, code=code, message=message,
                                          claim_index=claim_index))
    return kept


def _scope_ok(ref: str, context: ValidationContext) -> bool:
    meta = context.evidence_index.get(ref) or {}
    thscode = meta.get("thscode")
    return thscode is None or context.scope_thscode is None or thscode == context.scope_thscode


def _period_conflict(claim: Claim, evidence_refs: list[str],
                     context: ValidationContext) -> bool:
    """True when explicit text years share no year with every cited period."""
    text_years = set(_YEAR_MENTION.findall(claim.text))
    if not text_years or not evidence_refs:
        return False
    evidence_years: set[str] = set()
    for ref in evidence_refs:
        period = (context.evidence_index.get(ref) or {}).get("period")
        if period:
            evidence_years.update(_YEAR_MENTION.findall(str(period)))
    return bool(evidence_years) and not text_years & evidence_years


def _validate_claim(claim: Claim, *, section_id: str, claim_index: int,
                    context: ValidationContext, authorized: set[str],
                    metric_values: dict[str, tuple[Decimal | None, str]],
                    issues: list[ValidationIssue]) -> Claim:
    evidence_refs = _clean_refs(
        claim.evidence_refs, authorized, "evidence_ref_invalid",
        "部分证据引用不存在或不属于本次授权范围，已移除。", section_id, claim_index, issues)
    metric_refs = _clean_refs(
        claim.metric_refs, context.metric_names, "metric_ref_missing",
        "指标引用不存在，已移除。", section_id, claim_index, issues)
    gap_refs = _clean_refs(
        claim.gap_refs, context.gap_ids, "gap_ref_missing",
        "缺口引用不存在，已移除。", section_id, claim_index, issues)
    kind = claim.kind
    if kind in (ClaimKind.FACT, ClaimKind.DERIVED) and not evidence_refs and not metric_refs:
        kind = ClaimKind.INFERENCE
        issues.append(ValidationIssue(
            section_id=section_id, code="ungrounded_reclassified",
            message="缺少证据或指标依据的论断已改标为推断。", claim_index=claim_index))
    if _period_conflict(claim, evidence_refs, context):
        issues.append(ValidationIssue(
            section_id=section_id, code="period_conflict",
            message="论断所述期间与被引证据的期间不一致，已标记冲突。", claim_index=claim_index))
    if kind in (ClaimKind.FACT, ClaimKind.DERIVED) and metric_refs:
        cited = [(metric_values[name][0], metric_values[name][1])
                 for name in metric_refs if name in metric_values and metric_values[name][0] is not None]
        if cited:
            shared_metrics = [GroundedMetric(name=name, value=str(value), unit=unit)
                              for name, (value, unit) in metric_values.items()
                              if name in metric_refs and value is not None]
            if any(item.code == "metric_conflict" for item in validate_numbers(claim.text, shared_metrics)):
                issues.append(ValidationIssue(
                    section_id=section_id, code="metric_conflict",
                    message="正文数字与被引指标不一致，已标记冲突。", claim_index=claim_index))
    return Claim(kind=kind, text=claim.text, evidence_refs=evidence_refs,
                 metric_refs=metric_refs, gap_refs=gap_refs)


def validate_report(report: ResearchReport, context: ValidationContext) -> tuple[ResearchReport, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    metric_values = {metric.metric: (_decimal_of(metric.value), metric.unit) for metric in report.metrics}
    authorized = context.authorized_evidence()
    sections = []
    for section in report.sections:
        claims = [_validate_claim(claim, section_id=section.id, claim_index=index,
                                  context=context, authorized=authorized,
                                  metric_values=metric_values, issues=issues)
                  for index, claim in enumerate(section.claims)]
        status = section.status
        grounded = any(claim.kind in (ClaimKind.FACT, ClaimKind.DERIVED) for claim in claims)
        if status == SectionStatus.SUPPORTED and not grounded and section.id != "followups":
            status = SectionStatus.CONDITIONAL
            issues.append(ValidationIssue(
                section_id=section.id, code="unsupported_section",
                message="没有可支撑的论断，章节状态已降级为 conditional。"))
        counter = []
        for item in section.counter_evidence:
            refs = _clean_refs(item.evidence_refs, authorized, "evidence_ref_invalid",
                               "反证引用不存在或不属于本次授权范围，已移除。", section.id, None, issues)
            counter.append(item.model_copy(update={"evidence_refs": refs}))
        sections.append(section.model_copy(update={"claims": claims, "status": status,
                                                   "counter_evidence": counter}))
    chart_specs: list[ChartSpec] = []
    for chart in report.chart_specs:
        refs = _clean_refs(chart.metric_refs, context.metric_names, "chart_metric_ref_missing",
                           "图表引用了不存在的指标，已拒绝该图表。", "chart", None, issues)
        if refs or not chart.metric_refs:
            chart_specs.append(chart.model_copy(update={"metric_refs": refs}))
    return report.model_copy(update={"sections": sections, "chart_specs": chart_specs}), issues
