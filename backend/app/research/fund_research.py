"""Read-only fund-research index backed by immutable local reports."""
from __future__ import annotations


def local_fund_research(repository, fund_id: str) -> dict:
    reports = []
    for kind in ("fund_overlap", "fund_quality"):
        reports.extend(repository.reports_for_scope_type(kind, target=fund_id))
    reports.sort(key=lambda item: item.created_at, reverse=True)
    return {"fund_id": fund_id, "reports": [item.model_dump(mode="json") for item in reports],
            "local_only": True,
            "notice": "只返回已保存的本地报告；持仓与净值不会在打开页面时自动刷新。"}
