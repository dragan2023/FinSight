"""本地 DuckDB 数据源：把仓库的公司行动总收益序列适配成策略实验用的日 K。

与数据源前复权（`adjust="forward"`）在**收益率口径上等价**——两者都让除权除息日
不产生机械跳空，只差一个乘性常数；而策略信号（均线关系、分位、波动闸门）与基准
净值全部只看收益率，因此两种口径给出**相同的信号与收益**。区别只有三点，都是好的：

1. **完全本地**：不触网、不调用付费 API，满足 D6 与「付费 API 不自动调用」红线；
2. **强制 as-of 截断**：`ex_date_ms > end_ms` 的公司行动一律不参与复权，
   不会像"顺手 join 复权因子"那样泄漏未来信息；
3. **口径自证**：返回值带 `basis`，可直接写进策略实验的 `data_snapshot`。

⚠️ 本模块只负责取数与口径，**不做任何门禁判断**；`validation.grade()` 是唯一判据。
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..advisor.algorithms.base_rate import BASIS_CORPORATE_ACTION_TOTAL_RETURN, total_return_closes
from .models import PriceBar


def _day(date_ms: int) -> str:
    return datetime.fromtimestamp(date_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def local_price_bars(repository, thscode: str, *, start_ms: int = 0,
                     end_ms: int) -> tuple[list[PriceBar], dict]:
    """返回 ``(bars, snapshot)``：总收益口径日 K 与其数据口径快照。

    ``end_ms`` 即 as-of 时点：它同时截断行情与公司行动事件。
    ``snapshot`` 用于写入实验的 ``data_snapshot``（口径必须自证，见 D12）。
    """
    history = repository.corporate_action_total_return_history(thscode, start_ms, end_ms)
    converted = total_return_closes(history.get("items") or [], as_of_ms=end_ms)
    bars: list[PriceBar] = []
    for row in converted["closes"]:
        bars.append(PriceBar(date=_day(int(row["date_ms"])), open=row.get("open_price"),
                             close=float(row["close_price"])))
    snapshot = {
        "basis": BASIS_CORPORATE_ACTION_TOTAL_RETURN,
        "adjust": "corporate_action_total_return",
        "source": "local_duckdb:data/market/finsight_market.duckdb",
        "bars": len(bars),
        "missing_open_bars": sum(1 for bar in bars if bar.open is None),
        "applied_event_count": converted["applied_event_count"],
        "dropped_after_as_of_count": converted["dropped_after_as_of_count"],
        "skipped_bar_count": converted["skipped_bar_count"],
        "unmatched_event_count": history.get("unmatched_event_count"),
        "invalid_factor_count": history.get("invalid_factor_count"),
        "start": bars[0].date if bars else None,
        "end": bars[-1].date if bars else None,
    }
    return bars, snapshot
