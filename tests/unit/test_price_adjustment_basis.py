"""P0-1 复权口径守卫：基率不得建立在未复权收盘价上。

三件事必须同时成立，否则顾问的概率基率与 half-Kelly 区间会被除权除息污染：

1. 除权除息日不产生机械性负收益（10 送 10 会把价格折半）；
2. 仓库里**已公告但尚未发生**的未来除权日不得被应用到 as-of 之前的序列上；
3. 基率必须显式声明自己用的是哪种口径，便于事后判断该结论是否可信。

全部离线：临时 DuckDB 建在 tmp_path 下，不碰正式行情仓库。
"""

from __future__ import annotations

import pytest

from backend.app.advisor.algorithms.base_rate import (
    BASIS_CORPORATE_ACTION_TOTAL_RETURN,
    BASIS_UN_ADJUSTED,
    calculate_base_rate,
    total_return_closes,
)
from backend.app.market_warehouse.repository import WarehouseRepository

DAY_MS = 86_400_000
THSCODE = "601799.SH"


def _item(date_ms: int, close: float, factor: float | None = None, events: int = 0) -> dict:
    return {"date_ms": date_ms, "close_price": close, "total_return_factor": factor,
            "event_count": events}


def _returns(levels: list[float]) -> list[float]:
    return [levels[index] / levels[index - 1] - 1 for index in range(1, len(levels))]


def test_ten_for_ten_bonus_produces_no_mechanical_negative_return():
    """10 送 10：除权日理论价从 20.00 折半到 10.00，总收益口径收益应为 0。"""
    items = [_item(DAY_MS * (index + 1), 20.0, 1.0) for index in range(99)]
    items.append(_item(DAY_MS * 100, 10.0, (10.0 * (1 + 1.0)) / 20.0, events=1))
    items.extend(_item(DAY_MS * (index + 1), 10.0, 1.0) for index in range(100, 160))

    converted = total_return_closes(items, as_of_ms=DAY_MS * 160)

    raw_levels = [row["close_price"] for row in converted["raw_closes"]]
    return_levels = [row["close_price"] for row in converted["closes"]]

    assert _returns(raw_levels)[98] == -0.5  # 未复权口径把送股记成 -50%
    assert _returns(return_levels)[98] == 0.0  # 总收益口径不产生机械负收益
    assert min(_returns(return_levels)) == 0.0
    assert converted["applied_event_count"] == 1


def test_cash_dividend_is_not_counted_as_a_loss():
    """每股派 1 元现金、除息日收 19 元（前收 20 元）同样不该产生负收益。"""
    items = [_item(DAY_MS * (index + 1), 20.0, 1.0) for index in range(50)]
    items.append(_item(DAY_MS * 51, 19.0, (19.0 * 1 + 1.0) / 20.0, events=1))

    converted = total_return_closes(items, as_of_ms=DAY_MS * 51)

    assert _returns([row["close_price"] for row in converted["raw_closes"]])[-1] == pytest.approx(-0.05)
    assert _returns([row["close_price"] for row in converted["closes"]])[-1] == pytest.approx(0.0)


def test_event_after_as_of_is_never_applied():
    """as-of 之后的事件与行情一律丢弃，且不得回溯改变 as-of 之前的指数水平。"""
    as_of = DAY_MS * 120
    base = [_item(DAY_MS * (index + 1), 20.0, 1.0) for index in range(120)]
    # 这是一个"若被应用就会把历史腰斩"的事件：落在 as-of 之后。
    leaky = base + [_item(DAY_MS * 130, 10.0, 0.5, events=1),
                    _item(DAY_MS * 131, 10.0, 1.0)]

    truncated = total_return_closes(leaky, as_of_ms=as_of)
    clean = total_return_closes(base, as_of_ms=as_of)

    assert truncated["closes"] == clean["closes"]
    assert truncated["applied_event_count"] == 0
    assert truncated["dropped_after_as_of_count"] == 2
    assert max(row["date_ms"] for row in truncated["closes"]) <= as_of


def _write_market_fixture(repository: WarehouseRepository) -> None:
    """写入 60 个交易日：第 41 日 10 送 10，第 55 日已公告派息 5 元。"""
    db = repository._connect()
    try:
        rows = []
        for index in range(60):
            date_ms = DAY_MS * (index + 1)
            close = 20.0 if index < 40 else 10.0
            rows.append((THSCODE, date_ms, close, close, close, close, 1000.0, 1_000_000.0,
                         "none", "CNY"))
        db.executemany("INSERT INTO daily_k VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        db.executemany(
            "INSERT INTO adjustment_factors VALUES (?,?,?,?,?,?,?,?,?)",
            [("bonus-1", THSCODE, "601799", DAY_MS * 41, 0.0, 1.0, 0.0, 0.0, "CNY"),
             ("div-1", THSCODE, "601799", DAY_MS * 55, 5.0, 0.0, 0.0, 0.0, "CNY")])
    finally:
        db.close()


def test_repository_query_excludes_bars_and_events_after_as_of(tmp_path):
    """仓库层就必须截断：as-of 之后的行情行与除权事件都不得出现在结果里。"""
    repository = WarehouseRepository(tmp_path / "market.duckdb")
    _write_market_fixture(repository)
    as_of = DAY_MS * 50

    history = repository.corporate_action_total_return_history(THSCODE, 0, as_of)
    later = repository.corporate_action_total_return_history(THSCODE, 0, DAY_MS * 60)

    assert all(item["date_ms"] <= as_of for item in history["items"])
    assert max(item["date_ms"] for item in history["items"]) == as_of

    converted = total_return_closes(history["items"], as_of_ms=as_of)
    converted_later = total_return_closes(later["items"], as_of_ms=DAY_MS * 60)

    assert converted["applied_event_count"] == 1  # 只有第 41 日的 10 送 10
    assert converted_later["applied_event_count"] == 2  # 加上第 55 日的派息
    # 未来事件不得回溯改变 as-of 之前的指数水平。
    assert [row for row in converted_later["closes"] if row["date_ms"] <= as_of] == converted["closes"]
    # 除权日不再产生机械性 -50%。
    assert min(_returns([row["close_price"] for row in converted["closes"]])) > -1e-9
    assert min(_returns([row["close_price"] for row in converted["raw_closes"]])) == -0.5


def test_base_rate_uses_total_return_series_and_declares_its_basis():
    """同一段 10 送 10 行情：未复权口径凭空多出 -50% 的"亏损"，总收益口径没有。"""
    raw = ([{"date_ms": DAY_MS * (index + 1), "close_price": 100.0} for index in range(100)]
           + [{"date_ms": DAY_MS * (index + 1), "close_price": 50.0} for index in range(100, 200)])
    total_return = [{"date_ms": DAY_MS * (index + 1), "close_price": 100.0} for index in range(200)]

    unadjusted = calculate_base_rate(raw)
    adjusted = calculate_base_rate(raw, return_rows=total_return,
                                   price_basis=BASIS_CORPORATE_ACTION_TOTAL_RETURN)

    assert unadjusted["price_basis"] == BASIS_UN_ADJUSTED
    assert adjusted["price_basis"] == BASIS_CORPORATE_ACTION_TOTAL_RETURN
    assert unadjusted["forward_20"]["available"] and adjusted["forward_20"]["available"]
    assert unadjusted["forward_20"]["average_loss_pct"] == -50.0
    assert adjusted["forward_20"]["average_loss_pct"] is None
    assert "未复权" in unadjusted["note"]
    assert "公司行动总收益" in adjusted["note"]


def test_local_price_bars_enforce_as_of_cutoff_and_carry_open_prices(tmp_path):
    """本地实验数据源（`strategy/local_bars.py`）必须带开盘价并强制 as-of 截断。

    这是 C2 批量实验的数据入口（D11/D12）：「次日开盘成交」的回测没有开盘价就
    一笔交易都不会发生；口径又必须自证，否则实验落库会谎报数据来源。
    """
    from backend.app.strategy.local_bars import local_price_bars

    repository = WarehouseRepository(tmp_path / "market.duckdb")
    _write_market_fixture(repository)

    bars, snapshot = local_price_bars(repository, THSCODE, start_ms=0, end_ms=DAY_MS * 50)

    assert len(bars) == 50  # as-of 之后的 10 根不得出现
    assert all(bar.open is not None for bar in bars)
    assert snapshot["basis"] == BASIS_CORPORATE_ACTION_TOTAL_RETURN
    assert snapshot["end"] == "1970-02-20"
    assert snapshot["applied_event_count"] == 1  # 只有第 41 日的 10 送 10
    # 两层截断：仓库 SQL 的 `date_ms <= end_ms` 先挡掉 as-of 之后的行情，
    # 所以转换层的计数器为 0——这正是纵深防护生效的证据，不是漏截断。
    assert snapshot["dropped_after_as_of_count"] == 0
    assert snapshot["missing_open_bars"] == 0
    assert min(_returns([bar.close for bar in bars])) > -1e-9  # 无机械性 −50%


def test_base_rate_declares_downgrade_when_total_return_series_is_too_short():
    """总收益序列不足时如实降标口径，绝不假装仍在用复权口径。"""
    series = [{"date_ms": DAY_MS * (index + 1), "close_price": 100.0} for index in range(200)]

    result = calculate_base_rate(series, return_rows=series,
                                 price_basis=BASIS_CORPORATE_ACTION_TOTAL_RETURN,
                                 as_of_ms=DAY_MS * 80)

    assert result["available"] is False
    assert result["price_basis"] == BASIS_UN_ADJUSTED
