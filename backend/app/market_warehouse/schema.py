SCHEMA_VERSION = "market_daily_actions_v2"
DAILY_COLUMNS = {"thscode", "date_ms", "open_price", "high_price", "low_price", "close_price", "volume", "turnover", "adjusted", "currency"}
ADJUSTMENT_FACTOR_COLUMNS = {
    "thscode", "ticker", "ex_date_ms", "dividend_per_share", "per_share_bonus",
    "allotment_ratio", "allotment_price", "currency",
}
PRIMARY_KEY = ("thscode", "date_ms")
