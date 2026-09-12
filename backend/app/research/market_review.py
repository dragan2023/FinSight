"""Read-only market-review list; all external refreshes remain explicit tasks."""
from __future__ import annotations


def local_market_reviews(repository) -> dict:
    reports = repository.reports_for_scope_type("market_review")
    return {"reports": [item.model_dump(mode="json") for item in reports],
            "observations": repository.list_market_observations(), "local_only": True,
            "notice": "市场复盘仅显示当时保存的事实和限制，不在读取时联网更新。"}
