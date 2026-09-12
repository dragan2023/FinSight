"""Evidence-backed index research.  Reads never call external providers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from ..analysis.relative_performance import RelativeSeries, breadth, relative_performance
from ..errors import AppError


def _field(record: dict, name: str) -> str | None:
    value = (record.get("fields") or {}).get(name) or {}
    return value.get("value")


def _records(repo, *, capability: str, thscode: str | None = None) -> tuple[list[dict], list[str]]:
    """Read the newest evidence for each relevant local request only."""
    selected: list[dict] = []
    evidence_ids: list[str] = []
    seen: set[tuple[str | None, str]] = set()
    for meta in repo.list_evidence(capability=capability, thscode=thscode, limit=100):
        key = (meta.thscode, str(meta.params))
        if key in seen:
            continue
        seen.add(key)
        item = repo.get_evidence(meta.id)
        if item:
            selected.extend(item[1])
            evidence_ids.append(meta.id)
    return selected, evidence_ids


def _history_series(repo, thscode: str) -> tuple[RelativeSeries | None, list[str]]:
    rows, evidence_ids = _records(repo, capability="index_history", thscode=thscode)
    prices = {row.get("period_key"): _field(row, "close_price") for row in rows if row.get("period_key")}
    turnovers = {row.get("period_key"): _field(row, "turnover") for row in rows if row.get("period_key")}
    identity = repo.get_identity(thscode)
    if len(prices) < 2:
        return None, evidence_ids
    return RelativeSeries(id=thscode, label=identity.name if identity else thscode,
                          prices=prices, turnovers=turnovers, price_basis="index_price"), evidence_ids


def local_catalog(repo, tag: str) -> dict:
    records, evidence_ids = _records(repo, capability="index_catalog")
    items = []
    seen: set[str] = set()
    for row in records:
        code = row.get("thscode")
        if not code or code in seen:
            continue
        if _field(row, "tag") not in (tag, None, ""):
            continue
        seen.add(code)
        items.append({"thscode": code, "name": _field(row, "name") or code,
                      "tag": _field(row, "tag") or tag})
    return {"tag": tag, "items": items, "evidence_ids": evidence_ids,
            "local_only": True, "notice": "仅显示已保存的本地目录；请显式刷新以获取上游最新数据。"}


def local_research(repo, thscode: str) -> dict:
    history, history_evidence = _history_series(repo, thscode)
    members, member_evidence = _records(repo, capability="index_constituents", thscode=thscode)
    snapshots, snapshot_evidence = _records(repo, capability="price_snapshot")
    current_members = [
        {"thscode": _field(row, "member_thscode"), "name": _field(row, "member_name")}
        for row in members if _field(row, "member_thscode")
    ]
    member_codes = {item["thscode"] for item in current_members}
    changes = []
    for row in snapshots:
        code = row.get("thscode")
        if code in member_codes:
            changes.append({"thscode": code, "change": _field(row, "change_rate")})
    return {
        "thscode": thscode,
        "identity": repo.get_identity(thscode).model_dump(mode="json") if repo.get_identity(thscode) else None,
        "history": history.model_dump(mode="json") if history else None,
        "current_constituents": current_members,
        "breadth": breadth(changes, current_constituents_only=True, total_constituents=len(member_codes)),
        "evidence_ids": list(dict.fromkeys(history_evidence + member_evidence + snapshot_evidence)),
        "local_only": True,
        "limitation": "当前成分回看，存在存活者偏差；没有历史成分与权重，不能据此生成行业贡献或无偏回测结论。",
    }


def local_compare(repo, thscodes: list[str], reference_id: str) -> dict:
    series: list[RelativeSeries] = []
    evidence_ids: list[str] = []
    for code in thscodes:
        item, ids = _history_series(repo, code)
        evidence_ids.extend(ids)
        if item:
            series.append(item)
    if len(series) < 2:
        raise AppError("validation", "所选指数中至少两个拥有本地可比日线，不能用缺失交易日填充。", 422)
    result = relative_performance(series, reference_id)
    return {"comparison": result.model_dump(mode="json"), "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "ranking_scope": "所选行业内排名", "limitation": "排名仅在本次所选且共同交易日可比的指数中计算；收益差不是 alpha。"}


@dataclass
class IndexResearchService:
    market: object
    repository: object

    async def refresh(self, thscode: str, *, history: bool, constituents: bool,
                      member_snapshots: bool, start_ms: int | None, end_ms: int | None) -> dict:
        identity = self.market.identity(thscode)
        if identity is None or identity.asset_type != "a-share-index":
            raise AppError("not_found", "请先从指数目录选择并确认该指数身份。", 404)
        if not any((history, constituents, member_snapshots)):
            raise AppError("validation", "至少选择一项刷新范围。", 422)
        now = datetime.now(timezone.utc)
        end = end_ms or int(now.timestamp() * 1000)
        start = start_ms or int((now - timedelta(days=365)).timestamp() * 1000)
        if end < start or end - start > 10 * 365 * 24 * 60 * 60 * 1000:
            raise AppError("validation", "指数历史日期范围必须有效且不超过十年。", 422)
        results = []
        if history:
            results.append(await self.market.fetch_index_history(thscode, start_ms=start, end_ms=end, refresh=True))
        member_rows: list[dict] = []
        if constituents or member_snapshots:
            members = await self.market.fetch_index_constituents(thscode, refresh=True)
            results.append(members)
            member_rows = members.records
        if member_snapshots:
            codes = [_field(row, "member_thscode") for row in member_rows]
            codes = [code for code in codes if code]
            semaphore = asyncio.Semaphore(4)
            async def fetch_batch(batch: list[str]):
                async with semaphore:
                    return await self.market.fetch_price_snapshot(batch, refresh=True)
            batch_results = await asyncio.gather(*(fetch_batch(codes[i:i + 50]) for i in range(0, len(codes), 50)))
            results.extend(batch_results)
        return {"results": [result.model_dump(mode="json") for result in results],
                "requested_member_count": len({_field(row, "member_thscode") for row in member_rows if _field(row, "member_thscode")}),
                "notice": "成员快照按最多 50 个一批、固定并发不超过 4 获取；部分权限失败会保留已成功证据。"}
