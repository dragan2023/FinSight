"""市场状态（regime）：**下跌市不交易**——把长多策略的"水下时间"压下来的那一层。

为什么需要它：策略级验收里唯一卡住的是**回撤最长持续**（实测 586–792 天 vs 用户要求 183 天）。
长多策略的回撤期之所以长，是因为它在下跌市里照样满仓挨打、还不断被均线反复打脸。
**只在大盘走强时开新仓**是最常规、最可解释的应对（"常规股市交易逻辑"），它不需要新的预测能力。

## 市场状态怎么算（不引入任何外部数据、也没有前视）

本机没有指数行情表，但 `daily_k` 有**全市场日线**。所以就地构造一个**等权全市场指数**：

    ew_return(d)  = 当日所有「有前收且收正」标的的收盘涨跌幅的平均（等权）
    ew_index(d)   = ∏(1 + ew_return) 累积

再对它取 `ma_days` 日均线，**只看当日及以前**：

    risk_on(d) = ew_index(d) > ma_days 均线(d)

均线用**截至当日**的收盘累积值计算（`d` 当天收盘即可知，次日开盘才交易），因此没有前视。

⚠️ 这是**市场代理**，不是真实指数：等权 → 小票权重被放大的程度高于市值加权指数。
所以它只用来做"是否开新仓"的粗判断，**不得**当成指数行情用于别的用途。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import duckdb

CST = timezone(timedelta(hours=8))


def market_regime_series(repository, *, start_ms: int = 0, end_ms: int,
                         ma_days: int = 60) -> dict[str, dict]:
    """返回 ``{交易日: {"index": 等权全市场指数, "ma": ma_days 均线, "risk_on": bool}}``。

    只用**截至当日**的数据：均线是「当日及以前 `ma_days` 个交易日的指数均值」，
    而交易发生在此日之后的开盘，因此没有前视。
    """
    if not repository.path.exists() or end_ms < start_ms:
        return {}
    db = duckdb.connect(str(repository.path), read_only=True)
    try:
        rows = db.execute("""
            WITH bars AS (
                SELECT thscode, date_ms, close_price,
                       lag(close_price) OVER (PARTITION BY thscode ORDER BY date_ms) AS prev
                FROM daily_k WHERE close_price > 0
            )
            SELECT date_ms, avg(close_price / prev - 1) AS ew_return, count(*) AS n
            FROM bars
            WHERE prev IS NOT NULL AND prev > 0 AND date_ms >= ? AND date_ms <= ?
            GROUP BY date_ms ORDER BY date_ms
        """, [start_ms, end_ms]).fetchall()
    except duckdb.CatalogException:
        return {}
    finally:
        db.close()

    out: dict[str, dict] = {}
    index = 1.0
    history: list[float] = []
    for date_ms, ew_return, _count in rows:
        index *= 1 + float(ew_return or 0.0)
        history.append(index)
        window = history[-ma_days:]
        ma = sum(window) / len(window) if window else None
        day = datetime.fromtimestamp(date_ms / 1000, CST).strftime("%Y-%m-%d")
        out[day] = {"index": round(index, 8), "ma": round(ma, 8) if ma else None,
                    "risk_on": bool(ma is not None and index > ma),
                    "samples": len(window)}
    return out


def regime_gate(series: dict[str, dict], *, warmup_days: int = 60) -> dict[str, bool]:
    """把状态序列压成 ``{交易日: 是否允许开新仓}``。

    **均线未成形之前（前 `warmup_days` 个交易日）一律视为不可开仓**——
    宁可晚入场，也不要在没有状态判断依据时假装"大盘没问题"。
    """
    out: dict[str, bool] = {}
    for day in sorted(series):
        item = series[day]
        out[day] = bool(item.get("risk_on")) and item.get("samples", 0) >= warmup_days
    return out
