"""Normalization contract tests (plan 02 section 3)."""

from __future__ import annotations

from decimal import Decimal

from backend.app.market_data.normalization import (
    BASIS_ANNUAL_REPORT,
    BASIS_DAILY_BAR,
    BASIS_QUARTERLY_UNKNOWN,
    REASON_NOT_PUBLISHED,
    REASON_UNPARSEABLE,
    date_from_ms,
    iso_from_ms,
    normalize_indicators,
    normalize_price_history,
    normalize_snapshot,
    normalize_statement_rows,
    to_decimal,
)


def field_of(record, name):
    return record.fields[name]


def test_percent_becomes_internal_ratio_with_raw_kept():
    result = normalize_indicators(
        [{"ability": "profitability", "indicators": [
            {"index_id": "sale_gross_margin", "value": "89.12000000"},
            {"index_id": "current_ratio", "value": "2.31"},
        ]}], "300033.SZ", "2024-4")
    gross = field_of(result.records[0], "sale_gross_margin")
    current = field_of(result.records[0], "current_ratio")
    assert gross.value == format(Decimal("89.12000000") / 100, "f")
    assert gross.unit == "ratio"
    assert gross.raw_value == "89.12000000"
    assert current.unit == "x"
    assert current.value == "2.31"


def test_null_is_not_zero_and_becomes_not_published_gap():
    result = normalize_statement_rows([{
        "thscode": "600519.SH", "fiscal_year": 2024, "fiscal_period": "FY",
        "report_date_ms": 1735574400000, "period_end_ms": 1735574400000, "currency": "CNY",
        "operating_income": 174144000000, "net_profit": None,
    }], "income")
    record = result.records[0]
    assert field_of(record, "operating_income").value == "174144000000"
    net = field_of(record, "net_profit")
    assert net.value is None
    assert net.raw_value is None
    assert [(gap.field, gap.reason) for gap in result.gaps] == [("net_profit", REASON_NOT_PUBLISHED)]


def test_negative_and_zero_survive_verbatim():
    result = normalize_statement_rows([{
        "thscode": "600519.SH", "fiscal_year": 2024, "fiscal_period": "FY",
        "report_date_ms": 1735574400000, "period_end_ms": 1735574400000, "currency": "CNY",
        "operating_income": 0, "net_profit": -12.5,
    }], "income")
    assert field_of(result.records[0], "operating_income").value == "0"
    assert field_of(result.records[0], "net_profit").value == "-12.5"
    assert result.gaps == []


def test_non_finite_value_becomes_invalid_response_gap():
    result = normalize_indicators(
        [{"ability": "profitability", "indicators": [
            {"index_id": "sale_gross_margin", "value": "NaN"},
        ]}], "300033.SZ", "2024-4")
    assert field_of(result.records[0], "sale_gross_margin").value is None
    assert result.gaps[0].reason == REASON_UNPARSEABLE


def test_duplicate_statement_periods_keep_latest_disclosure():
    older = {"thscode": "600519.SH", "fiscal_year": 2023, "fiscal_period": "FY",
             "report_date_ms": 1703980800000, "period_end_ms": 1703980800000,
             "currency": "CNY", "operating_income": 111}
    newer = dict(older, report_date_ms=1735574400000, operating_income=222)
    result = normalize_statement_rows([older, newer], "income")
    assert result.duplicate_versions == 1
    assert len(result.records) == 1
    assert field_of(result.records[0], "operating_income").value == "222"
    assert result.records[0].disclosed_at == iso_from_ms(1735574400000)


def test_quarterly_basis_stays_unknown_without_derivation():
    row = {"thscode": "600519.SH", "fiscal_year": 2024, "fiscal_period": "Q3",
           "report_date_ms": 1730390400000, "period_end_ms": 1730390400000,
           "currency": "CNY", "operating_income": 100}
    result = normalize_statement_rows([row], "income")
    assert result.records[0].basis == BASIS_QUARTERLY_UNKNOWN
    assert BASIS_ANNUAL_REPORT != BASIS_QUARTERLY_UNKNOWN
    # No derived single-quarter fields exist anywhere in the record.
    assert all("derived" not in name for name in result.records[0].fields)


def test_annual_rows_carry_annual_report_basis():
    row = {"thscode": "600519.SH", "fiscal_year": 2024, "fiscal_period": "FY",
           "report_date_ms": 1735574400000, "period_end_ms": 1735574400000,
           "currency": "CNY", "operating_income": 1}
    result = normalize_statement_rows([row], "income")
    assert result.records[0].basis == BASIS_ANNUAL_REPORT
    assert result.records[0].period_end == "2024-12-31"


def test_top_level_basis_is_derived_from_the_records_not_hardcoded():
    """顶层 basis 必须跟着记录走。

    此前它写死成 `BASIS_ANNUAL_REPORT`：**一次季度请求会把自己标成"年报口径"**。
    `service._unpack` 只取 records/gaps（DataResult 的 basis 由 service 按请求参数给），
    所以暂时没做出错误结论，但任何直接读 `result.basis` 的调用方都会被误导。
    """
    quarterly = {"thscode": "600519.SH", "fiscal_year": 2024, "fiscal_period": "Q3",
                 "report_date_ms": 1730390400000, "period_end_ms": 1730390400000,
                 "currency": "CNY", "operating_income": 100}
    annual = {**quarterly, "fiscal_period": "FY", "fiscal_year": 2023}

    assert normalize_statement_rows([quarterly], "income").basis == BASIS_QUARTERLY_UNKNOWN
    assert normalize_statement_rows([annual], "income").basis == BASIS_ANNUAL_REPORT
    # 混着来（年报 + 季报都在）→ 按"含年报"处理，如实标成年报口径
    assert normalize_statement_rows([quarterly, annual], "income").basis == BASIS_ANNUAL_REPORT


def test_price_history_normalizes_and_keeps_order():
    rows = [
        {"date_ms": 1756742400000, "close_price": 10.5, "volume": 100, "turnover": 1050},
        {"date_ms": 1756656000000, "close_price": 10.1, "volume": 90, "turnover": 909},
    ]
    result = normalize_price_history(rows, "600519.SH")
    assert [record.period_key for record in result.records] == ["2025-09-01", "2025-09-02"]
    assert result.records[0].basis == BASIS_DAILY_BAR
    close = field_of(result.records[0], "close_price")
    assert close.value == "10.1"
    assert close.raw_value == "10.1"
    assert close.unit == "CNY"
    assert field_of(result.records[1], "close_price").value == "10.5"


def test_price_history_missing_close_is_a_gap_not_zero():
    rows = [{"date_ms": 1756656000000, "volume": 100}]
    result = normalize_price_history(rows, "600519.SH")
    assert field_of(result.records[0], "close_price").value is None
    assert any(gap.field == "close_price" and gap.reason == REASON_NOT_PUBLISHED
               for gap in result.gaps)


def test_empty_history_window_yields_no_records_without_error():
    result = normalize_price_history([], "600519.SH")
    assert result.records == []
    assert result.gaps == []


def test_snapshot_null_timestamp_stays_none():
    result = normalize_snapshot([{"thscode": "600519.SH", "last_price": 1299.52}], "prices.snapshot")
    record = result.records[0]
    assert record.period_end is None
    assert record.disclosed_at is None
    assert field_of(record, "last_price").value == "1299.52"


def test_unknown_indicator_index_id_becomes_gap():
    result = normalize_indicators(
        [{"ability": "growth", "indicators": [{"index_id": "mystery_index", "value": "1"}]}],
        "300033.SZ", "2024-4")
    assert result.gaps[0].field == "mystery_index"
    assert result.gaps[0].reason == REASON_UNPARSEABLE


def test_timestamp_helpers_use_shanghai_dates():
    # Vendor date_ms 1756656000000 == 2025-09-01 00:00 +08:00 (Shanghai midnight).
    assert date_from_ms(1756656000000) == "2025-09-01"
    assert iso_from_ms(1756656000000).startswith("2025-09-01T00:00:00+08:00")
    assert date_from_ms(None) is None
    assert date_from_ms(0) is None


def test_to_decimal_rejects_non_finite_and_bools():
    assert to_decimal(None) is None
    assert to_decimal(True) is None
    assert to_decimal("NaN") is None
    assert to_decimal("Infinity") is None
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal("12.34") == Decimal("12.34")


def test_valuation_fields_normalized_as_ratios():
    result = normalize_snapshot([{
        "thscode": "600519.SH", "ticker": "600519", "name": "贵州茅台",
        "pe_ttm": 19.948748, "pb_mrq": 6.465603,
    }], "valuations.snapshot")
    pe = field_of(result.records[0], "pe_ttm")
    assert pe.unit == "x"
    assert pe.value == "19.948748"
    assert pe.raw_value == "19.948748"
