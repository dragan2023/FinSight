"""Price metric tests: deterministic range change and drawdown."""

from __future__ import annotations

from decimal import Decimal

from backend.app.analysis.price_metrics import max_drawdown, range_change, close_series


def test_close_series_sorts_and_skips_unusable():
    records = [
        {"period_key": "2025-09-03", "fields": {"close_price": {"value": "9", "unit": "CNY"}}},
        {"period_key": "2025-09-01", "fields": {"close_price": {"value": "bad", "unit": "CNY"}}},
        {"period_key": "2025-09-02", "fields": {"close_price": {"value": None, "unit": "CNY"}}},
    ]
    assert close_series(records) == [("2025-09-03", Decimal("9"))]
