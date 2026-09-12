"""Read-only stock trend overview assembled solely from saved evidence."""

from __future__ import annotations

from ..analysis.price_metrics import (
    average_turnover, distance_from_high, moving_average, window_change,
    window_max_drawdown,
)


def local_stock_overview(repository, thscode: str) -> dict:
    history: list[dict] = []
    evidence_ids: list[str] = []
    gaps: list[dict] = []
    source_times: list[str] = []
    fetched_at: list[str] = []
    for meta in repository.list_evidence(thscode=thscode, limit=100):
        if "prices/historical" not in meta.endpoint_path:
            continue
        found = repository.get_evidence(meta.id)
        if not found:
            continue
        _, records, evidence_gaps = found
        history.extend(record for record in records if record.get("basis") == "daily_bar")
        evidence_ids.append(meta.id)
        gaps.extend(gap.model_dump(mode="json") for gap in evidence_gaps)
        if meta.source_time:
            source_times.append(meta.source_time)
        fetched_at.append(meta.fetched_at.isoformat())

    # Duplicate days occur after explicit refreshes. The most recently listed
    # evidence wins, while the row itself remains unchanged and traceable.
    by_date = {record.get("period_key"): record for record in history if record.get("period_key")}
    bars = [by_date[key] for key in sorted(by_date)]
    metrics = [
        moving_average(bars, 20, evidence_ids=evidence_ids),
        moving_average(bars, 60, evidence_ids=evidence_ids),
        moving_average(bars, 120, evidence_ids=evidence_ids),
        average_turnover(bars, 20, evidence_ids=evidence_ids),
        window_change(bars, 20, evidence_ids=evidence_ids),
        window_change(bars, 60, evidence_ids=evidence_ids),
        window_change(bars, 120, evidence_ids=evidence_ids),
        window_max_drawdown(bars, 60, evidence_ids=evidence_ids),
        distance_from_high(bars, 120, evidence_ids=evidence_ids),
    ]
    return {
        "basis": "daily_bar",
        "source_time": max(source_times) if source_times else None,
        "fetched_at": max(fetched_at) if fetched_at else None,
        "evidence_ids": evidence_ids,
        "gaps": gaps,
        "bars": bars,
        "metrics": [metric.model_dump(mode="json") for metric in metrics],
    }
