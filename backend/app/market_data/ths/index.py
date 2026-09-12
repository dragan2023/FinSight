"""Explicit index catalogue, constituent and daily-price requests."""

from __future__ import annotations

from ..instrument_types import INDEX_ASSET_TYPE, validate_instrument_thscode
from .contracts import contract_for, require_batch
from .transport import ThsTransport

INDEX_TAGS = {"cn_concept", "region", "tszs", "industry"}
MAX_INDEX_HISTORY_MS = 10 * 365 * 24 * 60 * 60 * 1000


def _validate_ms_range(start_ms: int, end_ms: int) -> tuple[int, int]:
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or start_ms <= 0 or end_ms < start_ms:
        raise ValueError("指数历史区间必须是有效的毫秒时间戳。")
    if end_ms - start_ms > MAX_INDEX_HISTORY_MS:
        raise ValueError("指数历史区间最长十年。")
    return start_ms, end_ms


class IndexClient:
    def __init__(self, transport: ThsTransport) -> None:
        self._transport = transport

    async def catalog(self, tag: str) -> dict:
        if tag not in INDEX_TAGS:
            raise ValueError("指数目录标签不受支持。")
        return await self._transport.request(contract_for("index_catalog"), params={"tag": tag})

    async def constituents(self, thscode: str) -> dict:
        code = validate_instrument_thscode(INDEX_ASSET_TYPE, thscode)
        return await self._transport.request(contract_for("index_constituents"), params={"thscode": code})

    async def snapshot(self, thscodes: list[str]) -> dict:
        contract = contract_for("index_snapshot")
        require_batch(contract, thscodes)
        codes = [validate_instrument_thscode(INDEX_ASSET_TYPE, code) for code in thscodes]
        return await self._transport.request(contract, params={"thscodes": ",".join(codes)})

    async def history(self, thscode: str, *, start_ms: int, end_ms: int) -> dict:
        code = validate_instrument_thscode(INDEX_ASSET_TYPE, thscode)
        start, end = _validate_ms_range(start_ms, end_ms)
        return await self._transport.request(
            contract_for("index_history"),
            params={"thscode": code, "interval": "1d", "start": start, "end": end})
