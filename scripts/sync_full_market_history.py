"""User-authorized first sync of the official full-market daily-K dump.

The script only prints local quality metadata.  Credentials stay in the
existing local configuration and are never printed or written to this log.
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
        result = await app.state.market_warehouse.sync("daily_k")
        status = app.state.market_warehouse.status()
        print(json.dumps({
            "status": result["status"],
            "quality": result["quality"],
            "warehouse": {
                "row_count": status["row_count"],
                "symbol_count": status.get("symbol_count"),
                "trading_day_count": status.get("trading_day_count"),
                "min_date_ms": status.get("min_date_ms"),
                "max_date_ms": status.get("max_date_ms"),
            },
        }, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
