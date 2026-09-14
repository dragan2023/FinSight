"""市场状态（regime）与**波动率目标**——机构最常用的两件"不预测方向"的事。

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

## 波动率目标（volatility targeting）：机构真正在用的那件事

2026-09-15 实测（`.cache/probe_drawdown_scope.py`）：把仓位从 1.0 降到 0.1，**回撤最长持续
依旧是 1876 个交易日**，只有**深度**从 −50.6% 降到 −5.6%。结论：**降仓位只能买"浅"，
买不到"快"**。因此本模块提供的是**深度控制**（对应用户的"最大回撤 ≤20%"），
不是"回撤时长控制"。

做法（机构的标准做法，不含任何方向预测）：

    realized_vol(d) = std(等权全市场指数 过去 N 个日收益) × sqrt(252)    # 只用截至 d 的数据
    exposure(d)     = clip(target_vol / realized_vol(d), min_exposure, 1.0)

波动升高 → 自动减仓；波动降低 → 自动加仓（上限 1.0，**不加杠杆**）。
用**市场指数**的波动而不是组合自身已实现波动，是为了避免反馈环：按组合自身波动减仓 →
组合波动随之下降 → 又允许加仓，会自激振荡。市场波动是外生的，不受我们仓位影响。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import duckdb

CST = timezone(timedelta(hours=8))
TRADING_DAYS_PER_YEAR = 252


def _ew_index_returns(repository, *, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """等权全市场指数的**日收益**序列（原始查询，供状态线与波动率两条链路共用）。"""
    if not repository.path.exists() or end_ms < start_ms:
        return []
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
        return []
    finally:
        db.close()
    return [(int(date_ms), float(ew_return or 0.0)) for date_ms, ew_return, _count in rows]


def _day(date_ms: int) -> str:
    return datetime.fromtimestamp(date_ms / 1000, CST).strftime("%Y-%m-%d")


def market_index_returns(repository, *, start_ms: int = 0, end_ms: int) -> dict[str, float]:
    """``{交易日: 等权全市场指数当日涨跌幅}``（小数，不是百分数）。"""
    return {_day(date_ms): value for date_ms, value in
            _ew_index_returns(repository, start_ms=start_ms, end_ms=end_ms)}


def market_regime_series(repository, *, start_ms: int = 0, end_ms: int,
                         ma_days: int = 60) -> dict[str, dict]:
    """返回 ``{交易日: {"index": 等权全市场指数, "ma": ma_days 均线, "risk_on": bool}}``。

    只用**截至当日**的数据：均线是「当日及以前 `ma_days` 个交易日的指数均值」，
    而交易发生在此日之后的开盘，因此没有前视。
    """
    rows = _ew_index_returns(repository, start_ms=start_ms, end_ms=end_ms)
    out: dict[str, dict] = {}
    index = 1.0
    history: list[float] = []
    for date_ms, ew_return in rows:
        index *= 1 + ew_return
        history.append(index)
        window = history[-ma_days:]
        ma = sum(window) / len(window) if window else None
        out[_day(date_ms)] = {"index": round(index, 8), "ma": round(ma, 8) if ma else None,
                              "risk_on": bool(ma is not None and index > ma),
                              "samples": len(window)}
    return out


def realized_vol_series(returns: dict[str, float], *, lookback_days: int = 60) -> dict[str, float]:
    """``{交易日: 截至当日的年化已实现波动率}``。

    **样本不足时该日不出现在结果里**（而不是补 0 或补一个猜测值）——调用方据此决定
    是"不知道就保守"还是"干脆不参与"，而不是被动接受一个假数字。
    """
    if lookback_days < 2:
        raise ValueError("波动率回看窗口至少需要 2 个交易日。")
    out: dict[str, float] = {}
    buffer: list[float] = []
    for day in sorted(returns):
        buffer.append(float(returns[day]))
        if len(buffer) < lookback_days:
            continue
        window = buffer[-lookback_days:]
        mean = sum(window) / len(window)
        variance = sum((item - mean) ** 2 for item in window) / (len(window) - 1)
        out[day] = math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return out


def vol_target_exposure(vols: dict[str, float], *, target_vol: float,
                        min_exposure: float = 0.0, max_exposure: float = 1.0) -> dict[str, float]:
    """``{交易日: 目标总仓位比例}`` = clip(目标波动 / 已实现波动, 下限, 上限)。

    - 上限默认 **1.0**：这是长多研究工具，**不加杠杆**；目标波动高于市场波动时也只满仓。
    - 下限由调用方给（`RiskProfile.min_exposure`）：市场上波动率极高的极端时段，
      允许降到下限但**不清仓**——"全进全出"已被实测证明会把回撤期拉长。
    - 波动率未知的交易日**不出现在结果里**，组合层按 1.0（不干预）处理。
    """
    if not 0 < min_exposure <= max_exposure:
        raise ValueError("仓位下限必须满足 0 < 下限 ≤ 上限。")
    if target_vol <= 0:
        raise ValueError("目标波动率必须为正。")
    return {day: min(max_exposure, max(min_exposure, target_vol / vol))
            for day, vol in vols.items() if vol and vol > 0}


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
