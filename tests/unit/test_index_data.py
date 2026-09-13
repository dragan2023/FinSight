import pytest

from backend.app.market_data.index_data import index_history_params, index_prices


def test_index_history_never_sends_equity_adjust_parameter():
    params = index_history_params("000300.SH", start_ms=1, end_ms=2)
    assert params == {"thscode": "000300.SH", "interval": "1d", "start": 1, "end": 2}
    assert "adjust" not in params


def test_index_prices_keep_gaps_and_reject_invalid_price():
    assert index_prices([{"date": "2026-01-02", "close": "10"}, {"date": "2026-01-03", "close": None}]) == {"2026-01-02": "10", "2026-01-03": None}
    with pytest.raises(ValueError, match="必须为正数"):
        index_prices([{"date": "2026-01-02", "close": "0"}])
