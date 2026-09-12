"""Synchronize official full-market corporate-action events into the local warehouse.

Only local quality metadata is printed.  The configured Financial API key and
short-lived signed download URL are never printed or persisted by this script.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.main import create_app  # noqa: E402


async def main() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        result = await app.state.market_warehouse.sync("adjustment_factors")
        status = app.state.market_warehouse.status()
        print(json.dumps({
            "status": result["status"],
            "quality": result["quality"],
            "warehouse": {
                "adjustment_factor_row_count": status.get("adjustment_factor_row_count"),
                "adjustment_factor_symbol_count": status.get("adjustment_factor_symbol_count"),
                "adjustment_factor_min_date_ms": status.get("adjustment_factor_min_date_ms"),
                "adjustment_factor_max_date_ms": status.get("adjustment_factor_max_date_ms"),
            },
        }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
