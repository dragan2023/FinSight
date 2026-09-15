"""**时点股票池**：把"事后挑出来的股票池"换成"每个决策日当天真正可选的那批标的"。

## 为什么这是散户打法里必须先修的一条

现在的验收用**截至 2026-09-04 的流动性前 20 名**去跑 2017 年以来的行情。那批标的是
"今天最大最活跃的 20 只"——它们是**这十年涨出来的结果**。实测后果：
等权买入持有在样本里是**年化 36.2%**，这个数字不可能真实，它就是幸存者偏差本身。

只要股票池是事后挑的，任何"策略跑赢买入持有 / 策略赚钱"的结论都**不能采信**：
策略和基准**共用同一个被美化过的池子**，差异里有多少来自策略、多少来自池子的挑选，
分不开。

## 做法（与决策节奏天然配套）

月度决策日（`schedule.monthly_decision_days`）当天，用**只含截至当日信息**的横截面
（`repository.cross_section`，它自带 as-of 截断与公司行动修正）按 20 日成交额排序取前 N。
每个决策日各算一次，**不允许用后面的排名回头改前面的池子**。

一个必须讲清的口径细节：`cross_section(date_ms=T)` 的截断是 `date_ms <= T`，
也就是**包含 T 当天**。这里的 `T` 取**决策日当天的收盘时点**（当日 23:59），
因为决策发生在当日收盘后、交易发生在次日开盘——这与全项目"前一交易日收盘决策、
次日开盘执行"的口径一致。若取当日 00:00，就会漏掉决策日当天的行情，池子会偏旧。

## 已知局限（如实登记，不外推）

- **仍然只覆盖"当天有成交"的标的**：停牌股自然被排除（这是 `cross_section` 的设计，
  也是正确的——停牌买不进）。
- **退市证券池不存在**（`07` 号文档 F22：5286 只里退市 0 只）→ 样本期内的退市标的
  根本不在库里，这一层偏差**无法在本数据集内消除**，只能常驻披露。
- **候选规模受 `cross_section` 的 `history_days` 限制**：默认 400 个自然日，
  因此新上市不足一年的标的会被排除。这与策略的 252 根预热要求一致，不是额外损失。

## ⚠️ as-of 时点必须用**当日那个交易日的原始 `date_ms`**

`cross_section` 有两件事同时成立：①截断 `date_ms <= T`；②**只有 `last_ms == T` 的标的
才进入结果**（即"T 当天有成交"）。第二条意味着 `T` 必须是 `daily_k` 里**真实存在的时间戳**，
不能自己造一个"当日 23:59:59"——那样没有任何一行的 `date_ms` 等于 T，接口会**静默返回空**，
然后股票池变成 0 只（2026-09-15 实测正是这么踩到的：106 个决策日全部返回 0 只）。

所以本模块的 `decision_days` 参数要的是 ``{交易日字符串: 该日的原始 date_ms}``，
由 `repository.trading_days()` 提供，而不是由日期字符串换算。
"""
from __future__ import annotations

from .schedule import day_to_ms      # noqa: F401  （保留供调用方做纯日期换算，不用于 as-of）


def liquidity_universe(repository, *, decision_days: dict[str, int], top: int,
                       history_days: int = 400) -> dict[str, list[str]]:
    """``{决策日: 当日按 20 日成交额排名的前 top 只}``（**只用当日及以前的数据**）。

    `decision_days` 是 ``{交易日: 该日的原始 date_ms}``（见模块 docstring 的 as-of 说明）。
    每个决策日单独算一次，不做任何跨日平滑——跨日平滑会让"今天入选"依赖"昨天入选"，
    那就不再是时点事实，而是一个需要另外验证的规则。
    """
    if top < 1:
        raise ValueError("股票池规模必须为正。")
    out: dict[str, list[str]] = {}
    for day in sorted(decision_days):
        rows = repository.cross_section(date_ms=decision_days[day], history_days=history_days)
        ranked = sorted((row for row in rows if (row.get("turnover_20") or 0) > 0),
                        key=lambda row: (-row["turnover_20"], str(row["thscode"])))
        out[day] = [str(row["thscode"]) for row in ranked[:top]]
    return out


def union_symbols(universe: dict[str, list[str]]) -> list[str]:
    """所有决策日入选过的标的的并集——这是需要加载 K 线的标的集合。"""
    codes: set[str] = set()
    for members in universe.values():
        codes.update(members)
    return sorted(codes)


def eligibility_series(universe: dict[str, list[str]], trading_days: list[str]) -> dict[str, set[str]]:
    """把"决策日的池子"铺成"每个交易日可选哪些标的"。

    铺法：**决策日定下的池子沿用到下一个决策日之前**。这是唯一诚实的做法——
    如果每个交易日都用当天的排名，那就变成了"每天都换池子"，与月度决策节奏矛盾，
    而且会引入大量与策略无关的换手。
    """
    if not universe:
        return {}
    ordered = sorted(universe)
    out: dict[str, set[str]] = {}
    index = -1
    current: set[str] = set()
    for day in trading_days:
        while index + 1 < len(ordered) and ordered[index + 1] <= day:
            index += 1
            current = set(universe[ordered[index]])
        if index >= 0:
            out[day] = current
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def describe(universe: dict[str, list[str]]) -> dict:
    """池子的可复现描述：每个决策日的规模、并集大小、换手程度。"""
    sizes = [len(members) for members in universe.values()]
    codes = union_symbols(universe)
    # 相邻决策日的重合率：衡量"池子有多稳定"。太低说明排名噪声大，换手会被池子推高。
    overlaps: list[float] = []
    ordered = sorted(universe)
    for previous, current in zip(ordered, ordered[1:]):
        before, after = set(universe[previous]), set(universe[current])
        overlaps.append(len(before & after) / max(1, len(before)))
    return {
        "decision_days": len(ordered),
        "per_day_size": {"min": min(sizes) if sizes else 0, "max": max(sizes) if sizes else 0,
                         "median": _median([float(value) for value in sizes])},
        "union_size": len(codes),
        "adjacent_overlap_median": _median(overlaps),
        "adjacent_overlap_min": min(overlaps) if overlaps else None,
        "first_day": ordered[0] if ordered else None,
        "last_day": ordered[-1] if ordered else None,
    }
