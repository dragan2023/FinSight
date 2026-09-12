"""Explicit, budgeted market-review refreshes with independent module gaps."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from ..errors import AppError
from ..analysis.market_dashboard import build_market_dashboard

DEFAULT_MODULES = ("limit_up", "limit_down", "limit_break", "limit_ladder", "hot", "dragon_tiger")
ALL_MODULES = frozenset(DEFAULT_MODULES) | {"skyrocket", "anomaly"}


class MarketObservationService:
    def __init__(self, repository, market) -> None:
        self.repository, self.market = repository, market

    async def refresh(self, *, date_ms: int, modules: list[str], max_calls: int) -> dict:
        if not isinstance(date_ms, int) or date_ms <= 0:
            raise AppError("validation", "复盘日期必须是有效的毫秒时间戳。", 422)
        selected = modules or list(DEFAULT_MODULES)
        if len(selected) != len(set(selected)) or any(item not in ALL_MODULES for item in selected):
            raise AppError("validation", "市场观察模块不受支持或重复。", 422)
        if not 1 <= max_calls <= 10 or len(selected) > max_calls:
            raise AppError("validation", "最大调用预算必须覆盖所选模块，且不超过 10。", 422)
        observed_date = datetime.fromtimestamp(date_ms / 1000, tz=timezone.utc).date().isoformat()
        # Calendar confirmation is deliberately best-effort: a missing calendar
        # permission becomes an explicit module gap rather than silently moving
        # the request to a nearby trading day.
        calendar_fetch = getattr(self.market, "fetch_trading_days", None)
        if calendar_fetch is not None:
            calendar = await calendar_fetch(start_ms=date_ms, end_ms=date_ms, refresh=False)
            if calendar.status.value == "available" and not calendar.records:
                observation_id = uuid.uuid4().hex
                summary = {"calendar": {"status": "unavailable", "count": 0, "gaps": 0, "notice": "非交易日；未自动回退到最近交易日。"}}
                self.repository.save_market_observation(observation_id=observation_id, observed_date=observed_date,
                    modules=[], summary=summary, evidence_ids=calendar.evidence_ids,
                    created_at=datetime.now(timezone.utc).isoformat(), dashboard={
                        "overview": {"cards": [], "structure": [], "interpretation": "所选日期不是交易日，未请求市场事件数据。"},
                        "ladder": {"levels": [], "stocks": [], "description": "无交易日数据。"},
                        "hot": {"top": [], "movement": {"up": 0, "down": 0, "flat": 0}, "description": "无交易日数据。"},
                        "dragon_tiger": {"reason_distribution": [], "stocks": [], "description": "无交易日数据。"},
                        "coverage": [], "notice": "所选日期不是交易日，未调用任何市场模块。",
                    })
                return {"observation_id": observation_id, "date": observed_date, "modules": [], "summary": summary,
                        "dashboard": {"overview": {"cards": [], "structure": [], "interpretation": "所选日期不是交易日，未请求市场事件数据。"}, "coverage": []},
                        "results": [calendar.model_dump(mode="json")], "notice": "所选日期不是交易日，未调用任何市场模块。"}
        results = await asyncio.gather(*(self.market.fetch_market_event(module, date_ms=date_ms, refresh=True) for module in selected))
        summary = {module: {"status": result.status, "count": len(result.records), "gaps": len(result.gaps)}
                   for module, result in zip(selected, results)}
        by_module = dict(zip(selected, results))
        dashboard = build_market_dashboard(results=by_module, summary=summary)
        evidence_ids = [evidence_id for result in results for evidence_id in result.evidence_ids]
        observation_id = uuid.uuid4().hex
        self.repository.save_market_observation(observation_id=observation_id, observed_date=observed_date,
            modules=selected, summary=summary, evidence_ids=evidence_ids,
            created_at=datetime.now(timezone.utc).isoformat(), dashboard=dashboard)
        return {"observation_id": observation_id, "date": observed_date, "modules": selected,
                "summary": summary, "dashboard": dashboard, "results": [result.model_dump(mode="json") for result in results],
                "notice": "每个模块独立保存证据和缺口；此处是数据观察，不构成交易建议。"}
