"""Reference-data endpoints: authoritative ticker rows and corporate actions."""

from __future__ import annotations

from .contracts import contract_for, require_batch
from .transport import ThsTransport
from ..ths_client import validate_ms_range, validate_thscode

MAX_TICKER_LIST_LIMIT = 10_000


class ReferenceClient:
    def __init__(self, transport: ThsTransport) -> None:
        self._transport = transport

    async def ticker_list(self, *, asset_type: str | None = None, limit: int = 1000,
                          offset: int = 0) -> dict:
        if not 1 <= limit <= MAX_TICKER_LIST_LIMIT:
            raise ValueError(f"标的列表单页范围为 1-{MAX_TICKER_LIST_LIMIT}。")
        if offset < 0:
            raise ValueError("标的列表偏移量不能为负数。")
        params: dict[str, object] = {"limit": limit, "offset": offset}
        if asset_type:
            params["asset_type"] = asset_type
        return await self._transport.request(contract_for("ticker_list"), params=params)

    async def adjustment_factors(self, thscode: str, *, start_ms: int | None = None,
                                 end_ms: int | None = None) -> dict:
        code = validate_thscode(thscode)
        params: dict[str, object] = {"thscode": code}
        if start_ms is not None or end_ms is not None:
            start, end = validate_ms_range(start_ms, end_ms)
            params.update({"start": start, "end": end})
        return await self._transport.request(contract_for("adjustment_factors"), params=params)
