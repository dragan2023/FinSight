"""Collection-auction endpoints with final-stage defaulting."""

from __future__ import annotations

from ..instrument_types import STOCK_ASSET_TYPE, validate_instrument_thscode
from .contracts import contract_for
from .transport import ThsTransport


class AuctionClient:
    def __init__(self, transport: ThsTransport) -> None:
        self._transport = transport

    async def snapshot(self, thscode: str, *, stage: str = "final") -> dict:
        if stage not in {"live", "final"}:
            raise ValueError("竞价阶段仅支持 live 或 final。")
        code = validate_instrument_thscode(STOCK_ASSET_TYPE, thscode)
        return await self._transport.request(contract_for("auction_snapshot"), params={"thscodes": code, "stage": stage})

    async def short_term_benchmark(self, *, date_ms: int) -> dict:
        if not isinstance(date_ms, int) or date_ms <= 0:
            raise ValueError("竞价基准日期必须为毫秒时间戳。")
        return await self._transport.request(contract_for("auction_benchmark"), params={"date": date_ms})
