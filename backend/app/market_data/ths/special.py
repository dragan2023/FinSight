"""Daily special-data requests; endpoint choice is a closed enum, never a path."""

from __future__ import annotations

from ..instrument_types import STOCK_ASSET_TYPE, validate_instrument_thscode
from .contracts import contract_for
from .transport import ThsTransport

DAILY_LISTS = {
    "limit_up": "limit_up_pool", "limit_down": "limit_down_pool", "limit_break": "limit_break_pool",
    "limit_ladder": "limit_up_ladder", "skyrocket": "skyrocket_list", "hot": "hot_stock_list",
    "anomaly": "anomaly_analysis_list", "dragon_tiger": "dragon_tiger_list",
}


class SpecialDataClient:
    def __init__(self, transport: ThsTransport) -> None:
        self._transport = transport

    async def daily_list(self, kind: str, *, date_ms: int) -> dict:
        if kind not in DAILY_LISTS or not isinstance(date_ms, int) or date_ms <= 0:
            raise ValueError("特色数据类型或日期不合法。")
        return await self._transport.request(contract_for(DAILY_LISTS[kind]), params={"date": date_ms})

    async def stock_anomaly(self, thscode: str, *, date_ms: int | None = None) -> dict:
        code = validate_instrument_thscode(STOCK_ASSET_TYPE, thscode)
        # 当前上游契约是当日接口，仅接受 thscodes；旧版曾传入 date，既无效也会
        # 在非交易日/版本升级后触发 invalid_response。保留 date_ms 形参只是兼容
        # 服务层按观察日缓存的调用者，绝不把它透传给上游。
        return await self._transport.request(contract_for("anomaly_analysis_stock"), params={"thscodes": code})

    async def hot_rank_trend(self, thscode: str, *, start_ms: int, end_ms: int) -> dict:
        code = validate_instrument_thscode(STOCK_ASSET_TYPE, thscode)
        if not all(isinstance(item, int) and item > 0 for item in (start_ms, end_ms)) or end_ms < start_ms:
            raise ValueError("热榜趋势时间范围不合法。")
        return await self._transport.request(contract_for("hot_stock_rank_trend"), params={"thscode": code, "start": start_ms, "end": end_ms})

    async def hot_history(self, *, date_ms: int) -> dict:
        if not isinstance(date_ms, int) or date_ms <= 0:
            raise ValueError("热榜历史日期不合法。")
        return await self._transport.request(contract_for("hot_stock_list_history"), params={"date": date_ms})
