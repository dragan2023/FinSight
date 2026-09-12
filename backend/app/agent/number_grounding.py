"""可复用的公开文本数字落点校验。

解析不确定时只返回 ``not_checked``，从不把日期、证券代码或证据编号当作
虚构数字。研究报告和顾问建议共用同一套单位换算与容差规则。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

_TOLERANCE = Decimal("0.005")
_FLOOR = Decimal("0.0001")
_PERIOD_WITH_WORD = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*(?:[-/年月.]\s*\d{1,2}){0,2}\s*(?:日|Q[1-4]|季[度报]?|年[度报中]?|月)")
_PERIOD_BARE_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?![\d.万亿%元倍])")
_PERIOD_SHORT = re.compile(r"(?<!\d)\d{1,2}\s*(?:月(?:\s*\d{1,2}\s*日?)?|季[度报]?|日)")
_SECURITY_CODE = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ)\b", re.IGNORECASE)
_EVIDENCE_ID = re.compile(r"\b[A-Z]\d{1,3}\b")
_QUANTITY = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*(%|万亿|亿|万)?")


class QuantityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str
    candidates: list[Decimal]


class GroundedMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    value: str | None
    unit: str


class GroundingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    mention: str
    message: str


def _decimal_of(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _representations(value: Decimal, unit: str) -> list[Decimal]:
    forms = [value]
    if unit == "ratio":
        forms.append(value * 100)
    if unit in ("CNY", "CNY/share"):
        forms.extend([value / 10000, value / 100000000, value / 1000000000000])
    return forms


def _candidates(base: Decimal, suffix: str | None) -> list[Decimal]:
    if suffix is None:
        return [base]
    if suffix == "%":
        return [base / 100]
    return [base * {"万": 10000, "亿": 100000000, "万亿": 1000000000000}[suffix]]


def extract_quantities(text: str) -> list[QuantityMention]:
    stripped = _SECURITY_CODE.sub(" ", text)
    stripped = _EVIDENCE_ID.sub(" ", stripped)
    stripped = _PERIOD_WITH_WORD.sub(" ", stripped)
    stripped = _PERIOD_BARE_YEAR.sub(" ", stripped)
    stripped = _PERIOD_SHORT.sub(" ", stripped)
    result: list[QuantityMention] = []
    for match in _QUANTITY.finditer(stripped):
        base = _decimal_of(match.group(1))
        if base is None or (match.group(2) is None and base == base.to_integral_value()
                            and Decimal("1900") <= base <= Decimal("2099")):
            continue
        result.append(QuantityMention(raw=match.group(0), candidates=_candidates(base, match.group(2))))
    return result


def validate_numbers(text: str, metrics: list[GroundedMetric]) -> list[GroundingIssue]:
    usable = [(value, metric.unit) for metric in metrics if (value := _decimal_of(metric.value)) is not None]
    if not usable:
        return [GroundingIssue(code="not_checked", mention="", message="没有可解析的结构化指标，数字未校验。")]
    issues: list[GroundingIssue] = []
    for mention in extract_quantities(text):
        matched = any(abs(candidate - form) <= max(abs(form) * _TOLERANCE, _FLOOR)
                      for value, unit in usable for form in _representations(value, unit)
                      for candidate in mention.candidates)
        if not matched:
            issues.append(GroundingIssue(code="metric_conflict", mention=mention.raw,
                                         message="正文数字与结构化指标不一致。"))
    return issues
