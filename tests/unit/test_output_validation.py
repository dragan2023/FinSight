"""Output validation tests (plan 03 section 4, item 2).

Covers: missing/out-of-scope references, wrong security/period/unit,
fabricated numbers, grounding reclassification, section demotion, and the
rule that raw model text never enters validated business objects.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.agent.output_models import (
    Claim,
    ClaimKind,
    Coverage,
    ModelSnapshot,
    OutputIntegrity,
    ReportSection,
    ResearchReport,
    SectionStatus,
)
from backend.app.agent.output_validation import ValidationContext, validate_report
from backend.app.analysis.models import MetricValue
from backend.app.research.evidence_models import DataGap

SCOPE = "600519"


def make_context() -> ValidationContext:
    return ValidationContext(
        evidence_index={
            "ev-fin-2024": {"thscode": SCOPE, "period": "2024-12-31"},
            "ev-price-daily": {"thscode": SCOPE, "period": "2025-06-30"},
            "ev-other-security": {"thscode": "000001", "period": "2024-12-31"},
        },
        metric_names={"revenue", "revenue_yoy", "net_margin"},
        gap_ids={"gap-price"},
        scope_thscode=SCOPE,
    )


def make_report(*claims: Claim, **overrides) -> ResearchReport:
    section_overrides = {
        key: overrides.pop(key)
        for key in ("counter_evidence", "status", "summary")
        if key in overrides
    }
    payload: dict = {
        "id": "report-1",
        "version": 1,
        "task_id": "task-1",
        "question": "贵州茅台2024年经营质量如何？",
        "scope": {"thscode": SCOPE},
        "model_snapshot": ModelSnapshot(
            provider="deepseek", model="deepseek-chat", output_label="64K", mode="tools"
        ),
        "evidence_ids": ["ev-fin-2024", "ev-price-daily"],
        "metrics": [
            MetricValue(
                metric="revenue",
                value="120000000",
                unit="CNY",
                basis="annual_report 2024",
                formula="revenue",
            ),
            MetricValue(
                metric="revenue_yoy",
                value="0.15",
                unit="ratio",
                basis="annual_report 2024",
                formula="(cur - prev) / prev",
            ),
            MetricValue(
                metric="net_margin",
                value="0.15",
                unit="ratio",
                basis="annual_report 2024",
                formula="net / revenue",
            ),
        ],
        "sections": [
            ReportSection(id="financials", title="财务质量", status=section_overrides.pop("status", SectionStatus.SUPPORTED),
                          claims=list(claims), **section_overrides)
        ],
        "data_gaps": [
            DataGap(id="gap-price", dimension="price", fields=["close_price"],
                    reason="not_published", impact="无法判断当前估值")
        ],
        "coverage": Coverage.PARTIAL,
        "output_integrity": OutputIntegrity.VALIDATED,
    }
    payload.update(overrides)
    return ResearchReport(**payload)


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_grounded_claim_with_consistent_period_and_scale_passes():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="截至2024年12月31日，营业收入为1.2亿元。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"]),
        Claim(kind=ClaimKind.FACT, text="2024年营业收入同比增长15%。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue_yoy"]),
    )
    result, issues = validate_report(report, make_context())
    assert issues == []
    assert result.sections[0].status == SectionStatus.SUPPORTED
    kept = result.sections[0].claims
    assert kept[0].kind == ClaimKind.FACT and kept[0].evidence_refs == ["ev-fin-2024"]
    assert kept[1].kind == ClaimKind.FACT


def test_missing_references_are_removed_and_reported():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="公司经营稳健。",
              evidence_refs=["ev-ghost"], metric_refs=["nope"], gap_refs=["gap-x"])
    )
    result, issues = validate_report(report, make_context())
    assert codes(issues) == {"evidence_ref_invalid", "metric_ref_missing", "gap_ref_missing",
                             "ungrounded_reclassified", "unsupported_section"}
    claim = result.sections[0].claims[0]
    assert claim.evidence_refs == [] and claim.metric_refs == [] and claim.gap_refs == []
    # A fact without any grounding left is reclassified, never silently trusted.
    assert claim.kind == ClaimKind.INFERENCE
    assert "ungrounded_reclassified" in codes(issues)


def test_evidence_of_other_security_is_not_authorized():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="2024年增长15%。",
              evidence_refs=["ev-other-security"], metric_refs=["revenue_yoy"])
    )
    result, issues = validate_report(report, make_context())
    assert "evidence_ref_invalid" in codes(issues)
    assert result.sections[0].claims[0].evidence_refs == []


def test_explicit_period_mismatch_is_flagged_but_ref_kept():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="2023年营业收入为1.2亿元。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"])
    )
    result, issues = validate_report(report, make_context())
    assert "period_conflict" in codes(issues)
    assert result.sections[0].claims[0].evidence_refs == ["ev-fin-2024"]


def test_money_unit_confusion_is_flagged():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="营业收入为12亿元。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"])
    )
    _, issues = validate_report(report, make_context())
    assert "metric_conflict" in codes(issues)


def test_percent_unit_confusion_is_flagged():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="净利率为0.15%。",
              evidence_refs=["ev-fin-2024"], metric_refs=["net_margin"])
    )
    _, issues = validate_report(report, make_context())
    assert "metric_conflict" in codes(issues)


def test_fabricated_number_is_flagged():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="2024年营业收入同比增长35%。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue_yoy"])
    )
    _, issues = validate_report(report, make_context())
    assert "metric_conflict" in codes(issues)


def test_dates_and_years_are_not_flagged_as_amount_conflicts():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="2024年12月31日货币资金为12000万元，第3季度回款稳定。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"])
    )
    _, issues = validate_report(report, make_context())
    assert "metric_conflict" not in codes(issues)


def test_ungrounded_fact_is_reclassified_to_inference():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="管理层执行力强。")
    )
    result, issues = validate_report(report, make_context())
    claim = result.sections[0].claims[0]
    assert claim.kind == ClaimKind.INFERENCE
    assert "ungrounded_reclassified" in codes(issues)


def test_supported_section_without_grounded_claims_is_demoted():
    report = make_report(
        Claim(kind=ClaimKind.INFERENCE, text="可能存在周期性波动。")
    )
    result, issues = validate_report(report, make_context())
    assert result.sections[0].status == SectionStatus.CONDITIONAL
    assert "unsupported_section" in codes(issues)


def test_counter_evidence_invalid_ref_removed():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="2024年营业收入为1.2亿元。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"]),
        counter_evidence=[{"text": "增长可能不可持续。", "evidence_refs": ["ev-ghost", "ev-price-daily"]}],
    )
    result, issues = validate_report(report, make_context())
    counter = result.sections[0].counter_evidence[0]
    assert counter.evidence_refs == ["ev-price-daily"]
    assert "evidence_ref_invalid" in codes(issues)


def test_null_metric_value_never_triggers_conflict():
    report = make_report(
        Claim(kind=ClaimKind.FACT, text="营业收入为1.2亿元。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"]),
        metrics=[
            MetricValue(metric="revenue", value=None, unit="CNY",
                        basis="annual_report 2024", formula="revenue")
        ],
    )
    _, issues = validate_report(report, make_context())
    assert "metric_conflict" not in codes(issues)


def test_raw_model_text_never_enters_validated_objects():
    context = make_context()
    raw_section = {
        "id": "facts", "title": "事实概览", "status": "supported", "summary": "ok",
        "claims": [{"kind": "fact", "text": "增长15%", "evidence_refs": ["ev-fin-2024"]}],
        "injected": "<script>alert(1)</script>",
    }
    raw = {
        "id": "report-raw", "version": 1, "task_id": "task-1", "question": "q",
        "coverage": "partial", "output_integrity": "unstructured",
        "sections": [raw_section],
    }
    with pytest.raises(ValidationError):
        ResearchReport.model_validate(raw)

    report = make_report(
        Claim(kind=ClaimKind.FACT, text="2024年营业收入为1.2亿元。",
              evidence_refs=["ev-fin-2024"], metric_refs=["revenue"])
    )
    result, _ = validate_report(report, context)
    assert result is not report  # validated copy, never the raw input object
    claim = result.sections[0].claims[0]
    assert isinstance(claim, Claim)
    with pytest.raises(ValidationError):
        Claim(kind=ClaimKind.FACT, text="增长15%", evidence_refs=["ev-fin-2024"], note="extra")
