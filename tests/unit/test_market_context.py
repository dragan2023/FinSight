from backend.app.analysis.market_context import dedupe_dragon_tiger,historical_anomaly_context,limit_ladder
def test_windowed_dragon_tiger_does_not_merge_one_and_three_day():
    rows=[{"thscode":"a","trade_date":"2024-01-01","window":"1d","reason":"x"},{"thscode":"a","trade_date":"2024-01-01","window":"3d","reason":"x"}]
    assert len(dedupe_dragon_tiger(rows))==2
def test_ladder_is_capped_and_historical_anomaly_is_not_reused():
    assert len(limit_ladder([{ "streak":2,"id":x} for x in range(5)])[2])==4
    assert historical_anomaly_context(requested_date="y",supported_date="x",explanation="today")["available"] is False
