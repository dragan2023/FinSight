"""Read-only sector-research index backed by immutable local reports."""
from __future__ import annotations


def local_sector_research(repository, sector_id: str) -> dict:
    reports = repository.reports_for_scope_type("sector_relative", target=sector_id)
    return {"sector_id": sector_id, "reports": [item.model_dump(mode="json") for item in reports],
            "local_only": True,
            "notice": "只返回已保存的本地报告；刷新数据请由用户显式新建研究任务。"}
