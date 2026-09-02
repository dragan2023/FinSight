import pytest

from backend.app.market_data.fund_data import discover_report_dates, nav_request_params, require_fund_type


def test_fund_type_requires_metadata_not_code_suffix():
    assert require_fund_type({"fund_type": "etf"}) == "etf"
    with pytest.raises(ValueError, match="元数据"):
        require_fund_type({"fund_id": "510300"})


def test_nav_range_is_contract_enum_and_disclosure_dates_are_discovered():
    assert nav_request_params("fund-a", range_name="year")["range"] == "year"
    with pytest.raises(ValueError, match="范围"):
        nav_request_params("fund-a", range_name="2026-01-01")
    assert discover_report_dates([{"report_type": "annual", "end_date": "2025-12-31"}, {"report_type": "annual", "end_date": "2025-12-31"}]) == [{"report_type": "annual", "end_date": "2025-12-31"}]
