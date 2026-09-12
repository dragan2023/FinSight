"""One user-triggered watchlist observation batch; no polling or alerts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError


class WatchlistObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    watchlist_item_id: str
    thscode: str
    snapshot: dict[str, str | None] = Field(default_factory=dict)
    anomalies: list[dict[str, str | None]] = Field(default_factory=list)
    price_evidence_id: str | None = None
    anomaly_evidence_id: str | None = None
    observed_at: datetime


class WatchlistObservationService:
    def __init__(self, repository, market) -> None:
        self._repository = repository
        self._market = market

    async def observe(self, item_ids: list[str] | None, *, refresh: bool) -> list[WatchlistObservation]:
        entries = self._repository.list_watchlist()
        selected_ids = list(dict.fromkeys(item_ids or [item.id for item in entries]))
        if len(selected_ids) > 20:
            raise AppError("validation", "单次最多观察 20 个关注标的。", 422)
        by_id = {item.id: item for item in entries}
        missing = [item_id for item_id in selected_ids if item_id not in by_id]
        if missing:
            raise AppError("validation", "只能观察当前关注列表中的标的。", 422)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        observations: list[WatchlistObservation] = []
        for item_id in selected_ids:
            item = by_id[item_id]
            try:
                identity = self._market.require_stock_identity(item.thscode)
                price = await self._market.fetch_price_snapshot(
                    [item.thscode], security_id=identity.thscode, refresh=refresh)
                anomaly = await self._market.fetch_anomaly_analysis(
                    item.thscode, date_ms=now_ms, security_id=identity.thscode, refresh=refresh)
                observation = WatchlistObservation(
                    id=uuid.uuid4().hex, watchlist_item_id=item.id, thscode=item.thscode,
                    snapshot=_snapshot_summary(price.records), anomalies=_anomaly_summary(anomaly.records),
                    price_evidence_id=(price.evidence_ids or [None])[0],
                    anomaly_evidence_id=(anomaly.evidence_ids or [None])[0],
                    observed_at=datetime.now(timezone.utc))
            except AppError as error:
                # Keep siblings progressing; the stored result shows a gap rather
                # than silently treating the failing symbol as normal.
                observation = WatchlistObservation(
                    id=uuid.uuid4().hex, watchlist_item_id=item.id, thscode=item.thscode,
                    snapshot={"status": error.category}, anomalies=[], observed_at=datetime.now(timezone.utc))
            self._repository.insert_watchlist_observation(observation)
            observations.append(observation)
        return observations

    def latest(self) -> list[WatchlistObservation]:
        return self._repository.latest_watchlist_observations()


def _snapshot_summary(records: list[dict]) -> dict[str, str | None]:
    fields = (records[0].get("fields") or {}) if records else {}
    return {name: (fields.get(name) or {}).get("value") for name in (
        "last_price", "price_change_ratio_pct", "high_price", "low_price", "turnover")}


def _anomaly_summary(records: list[dict]) -> list[dict[str, str | None]]:
    return [{name: (record.get("fields", {}).get(name) or {}).get("value") for name in ("tag", "reason", "event_time")}
            for record in records]
