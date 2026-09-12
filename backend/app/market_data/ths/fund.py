"""Fund requests with explicit upstream fund types and no suffix inference."""

from __future__ import annotations

from ..instrument_types import FUND_ASSET_TYPES, validate_instrument_thscode, validate_provider_fund_type
from .contracts import contract_for
from .transport import ThsTransport

NAV_RANGES = {"week", "month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"}
FUND_DETAIL_KINDS = {
    "holders_detail", "holders_top", "dividends", "diagnostics",
    "financial_indicators", "income_statements", "balance_sheets",
}
MANAGER_KINDS = {"manager_style", "manager_performance", "manager_experience", "manager_detail"}


class FundClient:
    def __init__(self, transport: ThsTransport) -> None:
        self._transport = transport

    @staticmethod
    def _code(asset_type: str, thscode: str) -> str:
        if asset_type not in FUND_ASSET_TYPES:
            raise ValueError("基金请求需要来自已确认基金身份的资产类型。")
        return validate_instrument_thscode(asset_type, thscode)

    async def profile(self, thscode: str, *, asset_type: str, fund_type: str) -> dict:
        code = self._code(asset_type, thscode)
        return await self._transport.request(contract_for("fund_profile"), params={"thscode": code, "fund_type": validate_provider_fund_type(fund_type)})

    async def holdings(self, thscode: str, *, asset_type: str, fund_type: str,
                       report_type: str | None = None, end_date: str | None = None) -> dict:
        code = self._code(asset_type, thscode)
        params: dict[str, str] = {"thscode": code, "fund_type": validate_provider_fund_type(fund_type)}
        if report_type:
            params["report_type"] = report_type
        if end_date:
            params["end_date"] = end_date
        return await self._transport.request(contract_for("fund_holdings"), params=params)

    async def nav(self, thscode: str, *, asset_type: str, fund_type: str, range_name: str,
                  nav_type: str = "unit,adj") -> dict:
        if range_name not in NAV_RANGES:
            raise ValueError("基金净值范围不受支持。")
        if nav_type not in {"unit", "adj", "unit,adj"}:
            raise ValueError("基金净值口径仅支持 unit / adj / unit,adj。")
        code = self._code(asset_type, thscode)
        return await self._transport.request(contract_for("fund_nav"), params={"thscode": code, "fund_type": validate_provider_fund_type(fund_type), "range": range_name, "nav_type": nav_type})

    async def market_snapshot(self, thscode: str, *, asset_type: str) -> dict:
        code = self._code(asset_type, thscode)
        if asset_type != "fund-etf":
            raise ValueError("场内基金行情当前仅支持 ETF。")
        return await self._transport.request(contract_for("fund_market_snapshot"), params={"thscodes": code})

    async def report_dates(self, thscode: str, *, asset_type: str, fund_type: str, holding: str) -> dict:
        if holding not in {"stock", "bond"}: raise ValueError("持仓报告类型仅支持 stock 或 bond。")
        code = self._code(asset_type, thscode)
        return await self._transport.request(contract_for(f"fund_{holding}_report_dates"), params={"thscode": code, "fund_type": validate_provider_fund_type(fund_type)})

    async def portfolio_history(self, thscode: str, *, asset_type: str, fund_type: str,
                                holding: str, report_type: str, end_date: str) -> dict:
        if holding not in {"stock", "bond"}:
            raise ValueError("历史持仓类型仅支持 stock 或 bond。")
        if not report_type or not end_date:
            raise ValueError("历史持仓必须先由报告日期接口提供 report_type 和 end_date。")
        code = self._code(asset_type, thscode)
        params = {"thscode": code, "fund_type": validate_provider_fund_type(fund_type),
                  "report_type": report_type, "end_date": end_date}
        return await self._transport.request(contract_for(f"fund_{holding}_history"), params=params)

    async def offerings(self, *, subscribe: str) -> dict:
        if subscribe not in {"active", "upcoming"}:
            raise ValueError("基金募集状态仅支持 active 或 upcoming。")
        return await self._transport.request(contract_for("fund_offerings"), params={"subscribe": subscribe})

    async def allocation(self, thscode: str, *, asset_type: str, fund_type: str, kind: str) -> dict:
        if kind not in {"asset", "industry"}: raise ValueError("配置类型仅支持 asset 或 industry。")
        code = self._code(asset_type, thscode)
        return await self._transport.request(contract_for(f"fund_{kind}_allocation"), params={"thscode": code, "fund_type": validate_provider_fund_type(fund_type)})

    async def performance_detail(self, thscode: str, *, asset_type: str, fund_type: str, kind: str) -> dict:
        if kind not in {"returns", "indicators_historical", "drawdowns"}: raise ValueError("基金业绩能力不受支持。")
        code = self._code(asset_type, thscode)
        return await self._transport.request(contract_for(f"fund_{kind}"), params={"thscode": code, "fund_type": validate_provider_fund_type(fund_type)})

    async def market_history(self, thscode: str, *, asset_type: str, start_ms: int, end_ms: int) -> dict:
        code = self._code(asset_type, thscode)
        if asset_type != "fund-etf": raise ValueError("场内基金历史行情当前仅支持 ETF。")
        if not all(isinstance(value, int) and value > 0 for value in (start_ms, end_ms)) or end_ms < start_ms: raise ValueError("基金行情日期范围不合法。")
        return await self._transport.request(contract_for("fund_market_history"), params={"thscode": code, "start": start_ms, "end": end_ms})

    async def detail(self, thscode: str, *, asset_type: str, fund_type: str, kind: str,
                     extra: dict[str, str] | None = None) -> dict:
        if kind not in FUND_DETAIL_KINDS:
            raise ValueError("基金详情能力不受支持。")
        code = self._code(asset_type, thscode)
        params: dict[str, str] = {"thscode": code, "fund_type": validate_provider_fund_type(fund_type)}
        if extra:
            params.update({key: value for key, value in extra.items() if value is not None})
        return await self._transport.request(contract_for(f"fund_{kind}"), params=params)

    async def manager(self, manager_id: str, *, kind: str) -> dict:
        if kind not in MANAGER_KINDS:
            raise ValueError("基金经理能力不受支持。")
        manager_id = (manager_id or "").strip()
        if not manager_id:
            raise ValueError("基金经理 ID 不能为空。")
        return await self._transport.request(contract_for(f"fund_{kind}"), params={"manager_id": manager_id})

    async def company(self, company_id: str) -> dict:
        company_id = (company_id or "").strip()
        if not company_id:
            raise ValueError("基金公司 ID 不能为空。")
        return await self._transport.request(contract_for("fund_company_detail"), params={"company_id": company_id})

    async def news(self, thscode: str, *, asset_type: str, fund_type: str,
                   limit: int | None = None, offset: str | None = None) -> dict:
        code = self._code(asset_type, thscode)
        if limit is not None and not 1 <= limit <= 100:
            raise ValueError("基金资讯每页条数必须在 1 到 100 之间。")
        params: dict[str, str | int] = {"thscode": code, "fund_type": validate_provider_fund_type(fund_type)}
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return await self._transport.request(contract_for("fund_news"), params=params)
