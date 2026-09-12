"""Registered market-dump link requests; links are never persisted."""
from __future__ import annotations

from .contracts import contract_for
from .transport import ThsTransport

DUMP_KINDS = {"daily_k", "daily_k_10d", "adjustment_factors"}


class DumpClient:
    def __init__(self, transport: ThsTransport) -> None:
        self._transport = transport

    async def download_link(self, kind: str) -> dict:
        if kind not in DUMP_KINDS:
            raise ValueError("不支持的全市场数据集类型。")
        return await self._transport.request(contract_for(f"{kind}_download"), params={})
