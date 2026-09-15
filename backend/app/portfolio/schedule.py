"""**决策节奏**：散户打法里最容易被忽略、却最影响能不能真赚到钱的一层。

## 为什么需要它（实测数字，不是感觉）

`26` 号文档实测：现有两条策略在组合层的**年化换手是 860%–11700%**。
按单边 10bps 计，光成本就是 **0.9%–11.7%/年**。对散户来说这有两个致命处：

1. **执行不了**：一年换手 48 倍意味着几乎每个交易日都要动手，散户没有这个时间与纪律。
2. **成本吃掉收益**：换手最高的那几档，成本已经和策略本身的收益同量级。

而且**在权重层打补丁是没用的**——同一个文档实测「换手带（不交易区）」几乎无效，
因为换手来自**信号进出**（仓位 0 ↔ 正），不是来自权重微调。
所以要降换手，只能改**决策节奏**：把"每天都可以重新决定"改成"按固定节奏决定"。

## 本模块的规则（简单到可以用一句话说给非技术用户）

> **每个月最后一天挑一次股；中间如果某只票的信号消失了就卖掉，但绝不加仓、不换股。**

这条规则同时解决三件事：

- **换手**：加仓/换股的机会一个月只有一次，换手自然降到月度水平。
- **可执行**：散户只需要每月花半小时。
- **不违反风控**：止损、熔断这些**风控出场不经过决策点**，随时可以发生
  （它们走的是单标的策略曲线，不由本模块控制）。

## 为什么不是"每 N 个交易日"

用"自然月末"而不是"固定间隔"有两个好处：①与散户的真实习惯一致（发工资、看月报）；
②月末往往是财报与资金面的自然节点，信息密度高。代价是间隔在 19–23 个交易日之间浮动，
这一点在文档里如实说明。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def _month_key(day: str) -> str:
    return day[:7]


def monthly_decision_days(trading_days: list[str]) -> set[str]:
    """每个自然月的**最后一个交易日**作为决策日。

    `trading_days` 必须是升序的交易日字符串（`YYYY-MM-DD`）。
    直接从这个序列推出来，不额外查库——这样决策日与回测用的日历**必然一致**，
    不会出现"决策日不是交易日"这种错位。
    """
    out: set[str] = set()
    if not trading_days:
        return out
    current_month = _month_key(trading_days[0])
    candidate = trading_days[0]
    for day in trading_days:
        month = _month_key(day)
        if month != current_month:
            out.add(candidate)
            current_month = month
        candidate = day
    out.add(candidate)          # 最后一个月的最后一天
    return out


def every_nth_decision_days(trading_days: list[str], *, every: int) -> set[str]:
    """每 `every` 个交易日决策一次（用于做"月度 vs 周度 vs 每日"的对照）。

    `every=1` 等价于"每天都重新决定"（即历史上的行为）。
    """
    if every < 1:
        raise ValueError("决策间隔必须 ≥1 个交易日。")
    return {day for index, day in enumerate(trading_days) if index % every == 0}


def day_to_ms(day: str) -> int:
    """`YYYY-MM-DD` → 当日 **00:00（北京时间）** 的毫秒时间戳。

    ⚠️ **不要拿它当横截面的 as-of 时点**。`cross_section` 要求"T 当天有成交"
    （`last_ms == T`），自己造的时点会让接口**静默返回空**、股票池变成 0 只。
    as-of 时点必须用 `daily_k` 里真实存在的 `date_ms`（见 `repository.trading_days()`）。
    本函数只做纯日期换算（例如把交易日字符串换成时间戳做区间过滤）。
    """
    parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST)
    return int(parsed.timestamp() * 1000)
