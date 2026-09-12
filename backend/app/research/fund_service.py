"""Module-oriented fund refreshes; each upstream capability remains independent."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from ..research.evidence_models import DataResult

FUND_MODULES = ("profile", "portfolio", "performance", "people", "financials", "diagnostics", "news", "market")
MODULE_BUDGETS = {
    "profile": 1, "portfolio": 6, "performance": 4, "people": 6,
    "financials": 3, "diagnostics": 1, "news": 1, "market": 1,
}


def _field(record: dict, name: str) -> object | None:
    field = (record.get("fields") or {}).get(name) or {}
    value = field.get("value")
    if isinstance(value, str) and field.get("kind") == "json":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


class FundResearchService:
    def __init__(self, market) -> None:
        self._market = market

    async def refresh(self, thscode: str, *, fund_type: str, modules: set[str],
                      range_name: str = "year", report_type: str | None = None,
                      report_end: str | None = None) -> dict:
        unknown = modules - set(FUND_MODULES)
        if unknown:
            raise ValueError("不支持的基金刷新模块。")
        results: dict[str, list[DataResult]] = {}
        profile: DataResult | None = None
        if "profile" in modules or "people" in modules:
            profile = await self._market.fetch_fund_profile(thscode, fund_type=fund_type, refresh=True)
            if "profile" in modules:
                results["profile"] = [profile]
        tasks: dict[str, Callable[[], Awaitable[list[DataResult]]]] = {}
        if "portfolio" in modules:
            tasks["portfolio"] = lambda: self._portfolio(thscode, fund_type, report_type, report_end)
        if "performance" in modules:
            tasks["performance"] = lambda: self._performance(thscode, fund_type, range_name)
        if "people" in modules:
            tasks["people"] = lambda: self._people(thscode, fund_type, profile)
        if "financials" in modules:
            tasks["financials"] = lambda: self._financials(thscode, fund_type)
        if "diagnostics" in modules:
            tasks["diagnostics"] = lambda: self._one(thscode, fund_type, "diagnostics")
        if "news" in modules:
            tasks["news"] = lambda: self._news(thscode, fund_type)
        if "market" in modules:
            tasks["market"] = lambda: self._market_data(thscode)
        if tasks:
            names = list(tasks)
            values = await asyncio.gather(*(tasks[name]() for name in names), return_exceptions=True)
            for name, value in zip(names, values, strict=True):
                # Programmer errors remain visible; ordinary upstream failures are DataResults.
                if isinstance(value, Exception):
                    raise value
                results[name] = value
        return {
            "modules": {name: [item.model_dump(mode="json") for item in values] for name, values in results.items()},
            "budget": {name: MODULE_BUDGETS[name] for name in modules},
            "notice": "每个模块独立保存；资料、净值或持仓的单项失败不会清除其他模块。",
        }

    async def _portfolio(self, thscode: str, fund_type: str, report_type: str | None,
                         report_end: str | None) -> list[DataResult]:
        if bool(report_type) != bool(report_end):
            raise ValueError("报告类型和报告期末日必须同时选择。")
        stock_dates, bond_dates = await asyncio.gather(
            self._market.fetch_fund_report_dates(thscode, fund_type=fund_type, holding="stock", refresh=True),
            self._market.fetch_fund_report_dates(thscode, fund_type=fund_type, holding="bond", refresh=True),
        )
        records = list(stock_dates.records)
        if report_type and not any(
                self._matches_period(record, report_type, report_end)
                for result in (stock_dates, bond_dates) for record in result.records):
            raise ValueError("所选报告期不在本次已发现的披露期中。")
        holdings = await self._market.fetch_fund_holdings(thscode, fund_type=fund_type,
                                                          report_type=report_type, end_date=report_end,
                                                          refresh=True)
        histories: list[Awaitable[DataResult]] = []
        for holding, dates in (("stock", stock_dates), ("bond", bond_dates)):
            selected = next((record for record in dates.records if self._matches_period(record, report_type, report_end)), None)
            if selected:
                fields = selected.get("fields") or {}
                discovered_type = (fields.get("report_type") or {}).get("value")
                discovered_end = (fields.get("end_date") or {}).get("value")
                if discovered_type and discovered_end:
                    histories.append(self._market.fetch_fund_portfolio_history(thscode, fund_type=fund_type, holding=holding, report_type=discovered_type, end_date=discovered_end, refresh=True))
        asset, industry, *history_results = await asyncio.gather(
            self._market.fetch_fund_allocation(thscode, fund_type=fund_type, kind="asset", refresh=True),
            self._market.fetch_fund_allocation(thscode, fund_type=fund_type, kind="industry", refresh=True),
            *histories,
        )
        return [stock_dates, bond_dates, holdings, asset, industry, *history_results]

    @staticmethod
    def _matches_period(record: dict, report_type: str | None, report_end: str | None) -> bool:
        if report_type is None and report_end is None:
            return True
        fields = record.get("fields") or {}
        return ((fields.get("report_type") or {}).get("value") == report_type
                and (fields.get("end_date") or {}).get("value") == report_end)

    async def _performance(self, thscode: str, fund_type: str, range_name: str) -> list[DataResult]:
        return list(await asyncio.gather(
            self._market.fetch_fund_nav(thscode, fund_type=fund_type, range_name=range_name, refresh=True),
            self._market.fetch_fund_performance_detail(thscode, fund_type=fund_type, kind="returns", refresh=True),
            self._market.fetch_fund_performance_detail(thscode, fund_type=fund_type, kind="indicators_historical", refresh=True),
            self._market.fetch_fund_performance_detail(thscode, fund_type=fund_type, kind="drawdowns", refresh=True),
        ))

    async def _people(self, thscode: str, fund_type: str, profile: DataResult | None) -> list[DataResult]:
        tasks: list[Awaitable[DataResult]] = [
            self._market.fetch_fund_detail(thscode, fund_type=fund_type, kind="holders_detail", extra={"merge_scope": "all"}, refresh=True),
            self._market.fetch_fund_detail(thscode, fund_type=fund_type, kind="holders_top", refresh=True),
            self._market.fetch_fund_detail(thscode, fund_type=fund_type, kind="dividends", refresh=True),
        ]
        if profile is None or not profile.records:
            return list(await asyncio.gather(*tasks))
        row = profile.records[0]
        company_id = _field(row, "company_id")
        manager_info = _field(row, "manager_info")
        manager_id = next((item.get("manager_id") for item in manager_info or [] if item.get("manager_id")), None)
        if manager_id:
            tasks.extend(self._market.fetch_fund_manager(str(manager_id), kind=kind, refresh=True)
                         for kind in ("style", "performance", "experience", "detail"))
        if company_id:
            tasks.append(self._market.fetch_fund_company(str(company_id), refresh=True))
        return list(await asyncio.gather(*tasks)) if tasks else []

    async def _financials(self, thscode: str, fund_type: str) -> list[DataResult]:
        return list(await asyncio.gather(*(self._one(thscode, fund_type, kind) for kind in (
            "financial_indicators", "income_statements", "balance_sheets"))))

    async def _one(self, thscode: str, fund_type: str, kind: str) -> list[DataResult]:
        return [await self._market.fetch_fund_detail(thscode, fund_type=fund_type, kind=kind, refresh=True)]

    async def _news(self, thscode: str, fund_type: str) -> list[DataResult]:
        return [await self._market.fetch_fund_news(thscode, fund_type=fund_type, limit=20, refresh=True)]

    async def _market_data(self, thscode: str) -> list[DataResult]:
        return [await self._market.fetch_fund_market_snapshot(thscode, refresh=True)]
