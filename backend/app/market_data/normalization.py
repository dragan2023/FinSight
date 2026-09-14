"""Deterministic normalization of vendor rows into research-grade records.

Contract (plan 02 section 3):
- every value keeps raw_value, normalized value, unit, kind, source field;
- vendor percent notation converts to an internal ratio (89.12 -> 0.8912);
- amounts/prices become Decimal and serialize as JSON strings;
- null != 0 != negative: a null field becomes a gap candidate, never zero;
- quarterly cumulative-vs-single-quarter basis is unknown upstream, so no
  derived period-to-period differences are produced from quarterly rows;
- duplicate rows for the same period keep the latest disclosed version and
  record how many versions were folded away.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from .field_dictionary import (
    PRICE_FIELDS,
    STATEMENT_FIELDS,
    VALUATION_FIELDS,
    FieldKind,
    FieldSpec,
    indicator_spec,
)

# Requires the tzdata package on Windows (declared in requirements.txt):
# Windows CPython ships no Olson tz database of its own.
TZ_SHANGHAI = ZoneInfo('Asia/Shanghai')

BASIS_ANNUAL_REPORT = "annual_report"
BASIS_QUARTERLY_UNKNOWN = "quarterly_cumulative_unknown"
BASIS_DAILY_BAR = "daily_bar"
BASIS_SNAPSHOT = "point_in_time_snapshot"
BASIS_UNKNOWN = "unknown"

REASON_NOT_PUBLISHED = "not_published"
REASON_UNPARSEABLE = "invalid_response"


def date_from_ms(ms: int | None) -> str | None:
    """Report-period / bar date in Asia/Shanghai (vendor contract)."""
    if not isinstance(ms, int) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=TZ_SHANGHAI).date().isoformat()


def iso_from_ms(ms: int | None) -> str | None:
    if not isinstance(ms, int) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=TZ_SHANGHAI).isoformat()


def to_decimal(raw: object) -> Decimal | None:
    """Decimal from int/float/str; None for null; never fabricates zeros."""
    if raw is None:
        return None
    try:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, str)):
            value = Decimal(raw)
        elif isinstance(raw, float):
            value = Decimal(repr(raw))
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


class NormalizedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_field: str
    label: str
    kind: str
    unit: str
    raw_value: str | None
    value: str | None
    currency: str | None = None
    gap_reason: str | None = None


class GapCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    dimension: str
    period_key: str | None
    reason: str


class NormalizedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thscode: str
    period_key: str
    period_end: str | None
    disclosed_at: str | None
    basis: str
    currency: str | None
    fields: dict[str, NormalizedField]


class NormalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    basis: str
    records: list[NormalizedRecord]
    gaps: list[GapCandidate]
    duplicate_versions: int = 0


def _unit_for(kind: FieldKind) -> str:
    return {
        FieldKind.AMOUNT: "CNY",
        FieldKind.PRICE: "CNY",
        FieldKind.TURNOVER: "CNY",
        FieldKind.PER_SHARE: "CNY/share",
        FieldKind.VOLUME: "share",
        FieldKind.PERCENT: "ratio",
        FieldKind.RATIO: "x",
        FieldKind.MULTIPLE: "x",
        FieldKind.TURNS: "times",
    }.get(kind, "")


def unit_for(kind: FieldKind) -> str:
    """Public unit table; one owner for hexin and supplement normalization."""
    return _unit_for(kind)


def _normalize_with_spec(raw: object, spec: FieldSpec, currency: str | None) -> NormalizedField:
    value = to_decimal(raw)
    if value is None and raw is not None:
        return NormalizedField(
            source_field=spec.upstream, label=spec.label, kind=spec.kind, unit=_unit_for(spec.kind),
            raw_value=None if raw is None else str(raw), value=None,
            currency=currency, gap_reason=REASON_UNPARSEABLE)
    if spec.kind is FieldKind.PERCENT and value is not None:
        value = value / Decimal(100)
    return NormalizedField(
        source_field=spec.upstream, label=spec.label, kind=spec.kind, unit=_unit_for(spec.kind),
        raw_value=None if raw is None else str(raw),
        value=None if value is None else format(value, "f"),
        currency=currency)


def _field_or_gap(raw: object, spec: FieldSpec, currency: str | None,
                  dimension: str, period_key: str, gaps: list[GapCandidate]) -> NormalizedField:
    field = _normalize_with_spec(raw, spec, currency)
    if field.value is None:
        gaps.append(GapCandidate(
            field=spec.upstream, dimension=dimension, period_key=period_key,
            reason=REASON_UNPARSEABLE if field.gap_reason else REASON_NOT_PUBLISHED))
    return field


def normalize_statement_rows(rows: list[dict], statement: str) -> NormalizationResult:
    """Merged statements: metadata + dictionary fields, latest disclosure wins."""
    dimension = f"financials.{statement}"
    by_period: dict[str, tuple[dict, int]] = {}
    duplicates = 0
    for row in rows or []:
        period_key = f"{row.get('fiscal_year')}-{row.get('fiscal_period')}"
        report_ms = row.get("report_date_ms") or 0
        if period_key in by_period:
            duplicates += 1
            if report_ms < by_period[period_key][1]:
                continue
        by_period[period_key] = (row, report_ms)
    records: list[NormalizedRecord] = []
    gaps: list[GapCandidate] = []
    for period_key, (row, _) in by_period.items():
        currency = row.get("currency")
        annual = row.get("fiscal_period") == "FY"
        fields: dict[str, NormalizedField] = {}
        for name, raw in row.items():
            spec = STATEMENT_FIELDS.get(name)
            if spec is None:
                continue
            fields[name] = _field_or_gap(raw, spec, currency, dimension, period_key, gaps)
        records.append(NormalizedRecord(
            thscode=row.get("thscode", ""), period_key=period_key,
            period_end=date_from_ms(row.get("period_end_ms")),
            disclosed_at=iso_from_ms(row.get("report_date_ms")),
            basis=BASIS_ANNUAL_REPORT if annual else BASIS_QUARTERLY_UNKNOWN,
            currency=currency, fields=fields))
    records.sort(key=lambda record: record.period_key, reverse=True)
    # 顶层 basis 必须**由记录推出来**，不能写死。写死成 `BASIS_ANNUAL_REPORT` 会让一次
    # **季度**请求把自己标成"年报口径"——目前 `service._unpack` 只取 records/gaps、
    # 顶层 basis 被丢弃（DataResult 的 basis 由 service 按请求参数给），所以它暂时没造成
    # 错误结论，但任何直接读它的调用方都会被误导。这里按"有没有 FY 行"如实推导。
    basis = (BASIS_ANNUAL_REPORT
             if any(record.basis == BASIS_ANNUAL_REPORT for record in records)
             else BASIS_QUARTERLY_UNKNOWN)
    return NormalizationResult(domain=dimension, basis=basis, records=records,
                               gaps=gaps, duplicate_versions=duplicates)


def normalize_price_history(rows: list[dict], thscode: str) -> NormalizationResult:
    records: list[NormalizedRecord] = []
    gaps: list[GapCandidate] = []
    seen: set[str] = set()
    duplicates = 0
    # History bars carry OHLCV only; snapshot-only fields do not apply here.
    history_specs = {name: spec for name, spec in PRICE_FIELDS.items()
                     if name in ("open_price", "high_price", "low_price", "close_price",
                                 "volume", "turnover")}
    for row in rows or []:
        date = date_from_ms(row.get("date_ms"))
        if date is None:
            gaps.append(GapCandidate(field="date_ms", dimension="prices.history",
                                     period_key=None, reason=REASON_UNPARSEABLE))
            continue
        if date in seen:
            duplicates += 1
            continue
        seen.add(date)
        fields = {
            name: _field_or_gap(row.get(name), spec, "CNY", "prices.history", date, gaps)
            for name, spec in history_specs.items()
        }
        records.append(NormalizedRecord(
            thscode=thscode, period_key=date, period_end=date, disclosed_at=None,
            basis=BASIS_DAILY_BAR, currency="CNY", fields=fields))
    records.sort(key=lambda record: record.period_key)
    return NormalizationResult(domain="prices.history", basis=BASIS_DAILY_BAR,
                               records=records, gaps=gaps, duplicate_versions=duplicates)


def normalize_snapshot(rows: list[dict], domain: str) -> NormalizationResult:
    """Price or valuation snapshot rows; timestamp null stays null, never now()."""
    records: list[NormalizedRecord] = []
    gaps: list[GapCandidate] = []
    # Snapshots carry last_price, not close_price (a history-only field).
    specs = ({name: spec for name, spec in PRICE_FIELDS.items() if name != "close_price"}
             if domain == "prices.snapshot" else VALUATION_FIELDS)
    for row in rows or []:
        thscode = row.get("thscode", "")
        period_key = f"snapshot:{thscode}"
        fields = {
            name: _field_or_gap(row.get(name), spec, "CNY", domain, period_key, gaps)
            for name, spec in specs.items()
        }
        records.append(NormalizedRecord(
            thscode=thscode, period_key=period_key, period_end=None, disclosed_at=None,
            basis=BASIS_SNAPSHOT, currency="CNY", fields=fields))
    return NormalizationResult(domain=domain, basis=BASIS_SNAPSHOT,
                               records=records, gaps=gaps, duplicate_versions=0)


def normalize_indicators(abilities: list[dict], thscode: str, report: str) -> NormalizationResult:
    records: list[NormalizedRecord] = []
    gaps: list[GapCandidate] = []
    fields: dict[str, NormalizedField] = {}
    for ability in abilities or []:
        for item in ability.get("indicators") or []:
            index_id = item.get("index_id", "")
            spec = indicator_spec(index_id)
            if spec is None:
                gaps.append(GapCandidate(field=index_id, dimension="financials.indicators",
                                         period_key=report, reason=REASON_UNPARSEABLE))
                continue
            raw = item.get("value")
            field = _normalize_with_spec(raw, spec, "CNY")
            if field.value is None:
                gaps.append(GapCandidate(
                    field=index_id, dimension="financials.indicators", period_key=report,
                    reason=REASON_UNPARSEABLE if raw not in (None, "") else REASON_NOT_PUBLISHED))
            fields[index_id] = field
    records.append(NormalizedRecord(
        thscode=thscode, period_key=report, period_end=None, disclosed_at=None,
        basis=BASIS_UNKNOWN, currency="CNY", fields=fields))
    return NormalizationResult(domain="financials.indicators", basis=BASIS_UNKNOWN,
                               records=records, gaps=gaps, duplicate_versions=0)
