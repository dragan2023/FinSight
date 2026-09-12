"""Market data service: identity confirmation, fetch, normalize, evidence.

Fetch pipeline: confirmed identity -> cache lookup (key isolates provider,
profile, credential_version, endpoint, params, schema) -> ThsClient ->
normalization -> evidence ledger -> DataResult. A failing capability becomes
a DataGap inside DataResult; it never raises a 500 and never blocks siblings
in multi-statement fetches.
"""

from __future__ import annotations

import json
import uuid
from typing import Callable

from ..api_config.service import ApiConfigService
from ..errors import AppError
from ..research.evidence_models import DataGap, DataResult, RequestedScope, ResultStatus
from ..research.evidence_store import EvidenceStore
from ..research.repository import ResearchRepository
from ..storage import Storage
from .cache import DataCache, cache_key
from .identity import (
    SecurityIdentity,
    TickerCandidate,
    identity_from_candidate,
)
from .models import STATEMENTS, Capability
from .normalization import (
    BASIS_SNAPSHOT,
    NormalizedRecord,
    NormalizedField,
    iso_from_ms,
    normalize_indicators,
    normalize_price_history,
    normalize_snapshot,
    normalize_statement_rows,
)
from .index_normalization import (
    normalize_index_catalog, normalize_index_constituents, normalize_index_history,
    normalize_index_snapshot,
)
from .fund_normalization import normalize_fund_news, normalize_fund_rows
from .ths_client import ThsClient
from .ths.contracts import contract_for
from .ths.special import DAILY_LISTS

PROVIDER = "hexin"

GAP_IMPACT = {
    "not_configured": "本能力未取得数据，其余能力不受影响。",
    "permission_denied": "本能力未取得数据，其余能力不受影响。",
    "rate_limited": "暂时未取得数据，稍后重试通常可恢复。",
    "timeout": "暂时未取得数据，稍后重试通常可恢复。",
    "not_published": "上游尚未披露该期数据，不构成数据错误。",
    "unsupported": "该标的或能力组合不受上游支持。",
        "invalid_response": "上游响应无法解释，未采用任何无法验证的数值。",
}

GAP_REMEDIES = {
    "not_configured": ["在设置中保存同花顺 API Key 后重试。"],
    "permission_denied": ["在同花顺账号中确认该能力的授权范围。"],
    "rate_limited": ["稍后重试，避免短时间高频刷新。"],
    "timeout": ["稍后重试。"],
    "not_published": ["等待上游披露后重试，或改用已披露的期间。"],
    "unsupported": ["更换标的或改用其他能力。"],
        "invalid_response": ["稍后重试；若持续出现，请通过设置中的能力测试检查服务状态。"],
}


def _gap(reason: str, dimension: str, fields: list[str], period: str | None,
         security_id: str | None) -> DataGap:
    return DataGap(
        id=uuid.uuid4().hex, dimension=dimension, fields=fields, security_id=security_id,
        period=period, reason=reason,
        impact=GAP_IMPACT.get(reason, "本能力未取得数据。"),
        remedies=GAP_REMEDIES.get(reason, ["稍后重试。"]))


def _group_gaps(candidates, security_id: str | None) -> list[DataGap]:
    grouped: dict[tuple, list[str]] = {}
    order: list[tuple] = []
    for candidate in candidates:
        key = (candidate.dimension, candidate.period_key, candidate.reason)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(candidate.field)
    return [_gap(reason, dimension, grouped[key], period, security_id)
            for key in order
            for (dimension, period, reason) in [key]]


class MarketDataService:
    def __init__(self, credentials: ApiConfigService, storage: Storage, http,
                 *, client: ThsClient | None = None) -> None:
        self._credentials = credentials
        self._repository = ResearchRepository(storage)
        self._store = EvidenceStore(self._repository)
        self._cache = DataCache(self._repository)
        self._client = client or ThsClient(http, credential_source=self.credential_source)

    # -- credentials ---------------------------------------------------------
    def profile_for(self, provider: str) -> dict | None:
        # "hexin" 是管线内部提供方名；凭据档案存的是 "hexin_finance"。
        lookup = "hexin_finance" if provider == "hexin" else provider
        for row in self._credentials.repository.list():
            if row.get("provider") == lookup:
                return row
        return None

    def hexin_profile(self) -> dict | None:
        return self.profile_for("hexin_finance")

    def credential_source(self) -> tuple[str | None, bool]:
        return self._credential_for("hexin_finance")

    def _credential_for(self, provider: str) -> tuple[str | None, bool]:
        row = self.profile_for(provider)
        if row is None or not row.get("enabled"):
            return None, False
        key_status = self._credentials.root.encryption.key_status(row.get("encrypted_api_key"))
        if key_status != "configured":
            return None, bool(row.get("enabled"))
        secret = self._credentials.root.encryption.decrypt(row["encrypted_api_key"])
        return secret.get_secret_value(), True

    def source_status(self) -> dict:
        row = self.hexin_profile()
        if row is None:
            return {"configured": False, "enabled": False, "profile_id": None}
        key_status = self._credentials.root.encryption.key_status(row.get("encrypted_api_key"))
        return {"configured": key_status == "configured", "enabled": bool(row.get("enabled")),
                "profile_id": row["id"], "key_status": key_status}

    async def fetch_market_dump_link(self, kind: str) -> dict:
        """Request a one-time market-dump link without persisting it.

        The warehouse owns the subsequent download.  Keeping this narrow
        service boundary avoids exposing the provider client to callers.
        """
        return await self._client.dumps.download_link(kind)

    # -- identity ------------------------------------------------------------
    async def resolve_candidates(self, query: str, *, exchange: str | None = None,
                                 asset_type: str | None = None, limit: int = 10) -> list[TickerCandidate]:
        data = await self._client.search(query, exchange=exchange, asset_type=asset_type, limit=limit)
        return [TickerCandidate.model_validate(item) for item in data.get("item") or []]

    async def ticker_table(self, *, asset_type: str = "a-share", limit: int = 10_000) -> dict:
        """一次取尽代码表（契约 §4：limit 上限 10000，循环分页由调用方处理）。

        仅供用户主动触发的路径使用（如 RSS 实体关联的代码表缓存）；调用方
        负责把结果缓存进 data_cache，自动轮询绝不经由此处付费取数。
        """
        return await self._client.ticker_list(asset_type=asset_type, limit=limit)

    def confirm_identity(self, candidate: TickerCandidate, source: str = "user_selected") -> SecurityIdentity:
        identity = identity_from_candidate(candidate, source)
        self._repository.upsert_identity(identity)
        return identity

    def identities(self) -> list[SecurityIdentity]:
        return self._repository.list_identities()

    def identity(self, thscode: str) -> SecurityIdentity | None:
        return self._repository.get_identity(thscode)

    def require_identity(self, thscode: str) -> SecurityIdentity:
        identity = self.identity(thscode)
        if identity is None:
            raise AppError("not_found", "请先检索并确认标的后，再获取该数据。", 404)
        return identity

    def require_stock_identity(self, thscode: str) -> SecurityIdentity:
        """Guard company-only APIs after broadening the shared identity model."""
        identity = self.require_identity(thscode)
        if identity.asset_type != "a-share":
            raise AppError("unsupported", "公司行情、估值和财务接口仅支持已确认的股票身份。", 422)
        return identity

    def require_index_identity(self, thscode: str) -> SecurityIdentity:
        identity = self.require_identity(thscode)
        if identity.asset_type != "a-share-index":
            raise AppError("unsupported", "该接口仅支持已确认的指数身份。", 422)
        return identity

    def require_fund_identity(self, thscode: str) -> SecurityIdentity:
        identity = self.require_identity(thscode)
        if identity.asset_type not in {"fund-otc", "fund-etf", "fund-lof"}:
            raise AppError("unsupported", "该接口仅支持已确认的基金身份。", 422)
        return identity

    # -- fetch pipeline ------------------------------------------------------
    async def _fetch(self, *, capability: str, endpoint_path: str, params: dict,
                     request: Callable[[], object], normalize: Callable[[dict], tuple[list[NormalizedRecord], list, int]],
                     basis: str, security_id: str | None = None, thscode: str | None = None,
                     refresh: bool = False, provider: str = PROVIDER) -> DataResult:
        scope = RequestedScope(capability=capability, security_id=security_id,
                               thscode=thscode, params=params)
        row = self.profile_for(provider)
        profile_id = row["id"] if row else None
        credential_version = row.get("credential_version", 0) if row else 0
        key = cache_key(provider=PROVIDER, profile_id=profile_id,
                        credential_version=credential_version, endpoint_path=endpoint_path,
                        params=params)
        payload = None if refresh else self._cache.lookup(key)
        from_cache = payload is not None
        if payload is None:
            payload = await request()
            self._cache.store(key, provider=PROVIDER, profile_id=profile_id,
                              credential_version=credential_version,
                              endpoint_path=endpoint_path, payload=payload, capability=capability)
        normalized = normalize(payload)
        records, candidates, duplicates = normalized[:3]
        result_scope = normalized[3] if len(normalized) == 4 else {}
        gaps = _group_gaps(candidates, security_id)
        meta = self._store.save_fetch(
            provider=provider, profile_id=profile_id, capability=capability,
            endpoint_path=endpoint_path, params=params, basis=basis,
            source_time=iso_or_none(payload.get("timestamp")),
            raw_payload=payload, records=records, gaps=gaps,
            duplicate_versions=duplicates, security_id=security_id, thscode=thscode)
        status = ResultStatus.AVAILABLE
        if not records:
            status = ResultStatus.UNAVAILABLE
        elif gaps:
            status = ResultStatus.PARTIAL
        return DataResult(
            status=status, records=[record.model_dump() for record in records],
            evidence_ids=[meta.id], gaps=gaps, requested_scope=scope,
            returned_scope={"count": len(records), "from_cache": from_cache,
                            "duplicate_versions": duplicates, **result_scope},
            source_profile_id=profile_id)

    async def fetch_result(self, capability: str, endpoint_path: str, params: dict,
                           request: Callable[[], object], normalize, basis: str, **kwargs) -> DataResult:
        try:
            return await self._fetch(capability=capability, endpoint_path=endpoint_path,
                                     params=params, request=request, normalize=normalize,
                                     basis=basis, **kwargs)
        except AppError as error:
            if error.category == "validation":
                # Caller mistakes must surface as 422, not become a silent gap.
                raise
            return self._failure(capability, endpoint_path, params, error, kwargs)

    def _failure(self, capability: str, endpoint_path: str, params: dict,
                 error: AppError, kwargs: dict) -> DataResult:
        reason = getattr(error, "gap_reason", None) or "invalid_response"
        if error.category == "not_configured":
            reason = "not_configured"
        elif error.category == "unsupported" and reason == "invalid_response":
            reason = "unsupported"
        security_id = kwargs.get("security_id")
        gap = _gap(reason, capability, [], kwargs.get("period"), security_id)
        return DataResult(
            status=ResultStatus.UNAVAILABLE, records=[], evidence_ids=[], gaps=[gap],
            requested_scope=RequestedScope(capability=capability, security_id=security_id,
                                           thscode=kwargs.get("thscode"), params=params),
            returned_scope={"count": 0}, source_profile_id=None)

    # -- capability wrappers -------------------------------------------------
    async def fetch_price_snapshot(self, thscodes: list[str], *, security_id: str | None = None,
                                   refresh: bool = False) -> DataResult:
        params = {"thscodes": ",".join(thscodes)}
        async def request():
            return await self._client.price_snapshot(thscodes)
        return await self.fetch_result(
            Capability.PRICE_SNAPSHOT, "/api/a-share/prices/snapshot", params, request,
            lambda payload: _unpack(normalize_snapshot(payload.get("item") or [], "prices.snapshot")),
            BASIS_SNAPSHOT, thscode=thscodes[0] if len(thscodes) == 1 else None,
            security_id=security_id, refresh=refresh)

    async def fetch_index_catalog(self, tag: str, *, refresh: bool = False) -> DataResult:
        params = {"tag": tag}
        async def request():
            return await self._client.indices.catalog(tag)
        return await self.fetch_result(
            "index_catalog", "/api/a-share-index/catalog/ths-index-list", params, request,
            lambda payload: (normalize_index_catalog(payload.get("item") or [], tag), [], 0),
            "index_catalog", refresh=refresh)

    async def fetch_index_constituents(self, thscode: str, *, refresh: bool = False) -> DataResult:
        self.require_index_identity(thscode)
        params = {"thscode": thscode}
        async def request():
            return await self._client.indices.constituents(thscode)
        return await self.fetch_result(
            "index_constituents", "/api/a-share-index/constituents/ths-stock-list", params, request,
            lambda payload: (normalize_index_constituents(payload.get("item") or [], thscode), [], 0),
            "current_constituents", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_index_snapshot(self, thscodes: list[str], *, refresh: bool = False) -> DataResult:
        if not thscodes:
            raise AppError("validation", "请至少选择一个指数。", 422)
        for code in thscodes:
            self.require_index_identity(code)
        params = {"thscodes": ",".join(thscodes)}
        async def request():
            return await self._client.indices.snapshot(thscodes)
        return await self.fetch_result(
            "index_snapshot", "/api/a-share-index/prices/snapshot", params, request,
            lambda payload: (normalize_index_snapshot(payload.get("item") or []), [], 0),
            "index_snapshot", thscode=thscodes[0] if len(thscodes) == 1 else None, refresh=refresh)

    async def fetch_index_history(self, thscode: str, *, start_ms: int, end_ms: int,
                                  refresh: bool = False) -> DataResult:
        self.require_index_identity(thscode)
        params = {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms}
        async def request():
            return await self._client.indices.history(thscode, start_ms=start_ms, end_ms=end_ms)
        return await self.fetch_result(
            "index_history", "/api/a-share-index/prices/historical", params, request,
            lambda payload: (normalize_index_history(payload.get("item") or [], thscode), [], 0),
            "index_daily_bar", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_valuations(self, thscodes: list[str], *, security_id: str | None = None,
                               refresh: bool = False) -> DataResult:
        params = {"thscodes": ",".join(thscodes)}
        async def request():
            return await self._client.valuation_snapshot(thscodes)
        return await self.fetch_result(
            Capability.VALUATION_SNAPSHOT, "/api/a-share/valuations/snapshot", params, request,
            lambda payload: _unpack(normalize_snapshot(payload.get("item") or [], "valuations.snapshot")),
            BASIS_SNAPSHOT, thscode=thscodes[0] if len(thscodes) == 1 else None,
            security_id=security_id, refresh=refresh)

    async def fetch_price_history(self, thscode: str, *, start_ms: int, end_ms: int,
                                  adjust: str = "forward", security_id: str | None = None,
                                  refresh: bool = False) -> DataResult:
        params = {"thscode": thscode, "interval": "1d", "start": start_ms, "end": end_ms, "adjust": adjust}
        async def request():
            return await self._client.price_history(thscode, start_ms=start_ms, end_ms=end_ms, adjust=adjust)
        return await self.fetch_result(
            Capability.PRICE_HISTORY, "/api/a-share/prices/historical", params, request,
            lambda payload: _unpack(normalize_price_history(payload.get("item") or [], thscode)),
            "daily_bar", thscode=thscode, security_id=security_id, refresh=refresh)

    async def fetch_statement(self, statement: str, thscode: str, *, period: str = "annual",
                              limit: int | None = None, start_ms: int | None = None,
                              end_ms: int | None = None, security_id: str | None = None,
                              refresh: bool = False) -> DataResult:
        params: dict = {"thscode": thscode, "period": period}
        if start_ms is not None:
            params.update({"start": start_ms, "end": end_ms})
        else:
            params["limit"] = 4 if limit is None else limit
        async def request():
            return await self._client.financial_statement(
                statement, thscode, period=period, limit=limit, start_ms=start_ms, end_ms=end_ms)
        return await self.fetch_result(
            Capability.FINANCIAL_STATEMENTS, f"/api/a-share/financials/{STATEMENTS[statement]}", params, request,
            lambda payload: _unpack(normalize_statement_rows(payload.get("item") or [], statement)),
            "annual_report" if period == "annual" else "quarterly_cumulative_unknown",
            thscode=thscode, security_id=security_id, refresh=refresh)

    async def fetch_all_statements(self, thscode: str, *, period: str = "annual",
                                   limit: int | None = None, security_id: str | None = None,
                                   refresh: bool = False) -> list[DataResult]:
        """One failing statement must not affect the others."""
        results: list[DataResult] = []
        for statement in ("income", "balance", "cashflow"):
            results.append(await self.fetch_statement(
                statement, thscode, period=period, limit=limit,
                security_id=security_id, refresh=refresh))
        return results

    async def fetch_indicators(self, thscode: str, report: str, *,
                               security_id: str | None = None, refresh: bool = False) -> DataResult:
        params = {"thscode": thscode, "report": report}
        async def request():
            return await self._client.financial_indicators(thscode, report)
        return await self.fetch_result(
            Capability.FINANCIAL_INDICATORS, "/api/a-share/financials/indicators", params, request,
            lambda payload: _unpack(normalize_indicators(payload.get("abilities") or [], thscode, report)),
            "unknown", thscode=thscode, security_id=security_id, refresh=refresh)

    async def fetch_trading_days(self, *, start_ms: int, end_ms: int,
                                 refresh: bool = False) -> DataResult:
        params = {"start": start_ms, "end": end_ms}
        async def request():
            return await self._client.trading_days(start_ms=start_ms, end_ms=end_ms)
        return await self.fetch_result(
            Capability.TRADING_DAYS, "/api/a-share/calendar/trading-days", params, request,
            lambda payload: _unpack_calendar(payload),
            "calendar", refresh=refresh)

    async def fetch_anomaly_analysis(self, thscode: str, *, date_ms: int,
                                     security_id: str | None = None, refresh: bool = False) -> DataResult:
        """Today-only anomaly evidence; an empty list is not a claim of no event."""
        params = {"thscode": thscode, "date": date_ms}

        async def request():
            return await self._client.anomaly_analysis_stock(thscode, date_ms=date_ms)

        def normalize(payload: dict):
            records: list[NormalizedRecord] = []
            for position, item in enumerate(payload.get("item") or []):
                if not isinstance(item, dict):
                    continue
                # 字段名以官方手册为准（anomaly-analysis）：analysis_content /
                # tag_name / keyword_list；旧候选名保留为兼容回退。
                reason = (item.get("analysis_content") or item.get("reason")
                          or item.get("reason_desc") or item.get("anomaly_reason"))
                tag = (item.get("tag_name") or item.get("tag")
                       or item.get("label") or item.get("event_type"))
                event_time = item.get("event_time") or item.get("time") or item.get("timestamp")
                keywords = item.get("keyword_list")
                fields = {
                    "tag": NormalizedField(source_field="tag", label="异动标签", kind="text", unit="",
                                           raw_value=None if tag is None else str(tag), value=None if tag is None else str(tag)),
                    "reason": NormalizedField(source_field="reason", label="官方解读", kind="text", unit="",
                                              raw_value=None if reason is None else str(reason), value=None if reason is None else str(reason)),
                    "event_time": NormalizedField(source_field="event_time", label="异动时间", kind="timestamp", unit="",
                                                  raw_value=None if event_time is None else str(event_time), value=None if event_time is None else str(event_time)),
                    "keywords": NormalizedField(source_field="keywords", label="关键词", kind="json", unit="",
                                                raw_value=None if keywords is None else str(keywords),
                                                value=None if keywords is None else str(keywords)),
                }
                records.append(NormalizedRecord(
                    thscode=thscode, period_key=f"{date_ms}:{position}", period_end=None, disclosed_at=None,
                    basis="daily_event", currency=None, fields=fields))
            return records, [], 0

        return await self.fetch_result(
            "special_data.anomaly", "/api/a-share/special-data/anomaly-analysis-stock", params, request,
            normalize, "daily_event", thscode=thscode, security_id=security_id, refresh=refresh)

    async def fetch_market_event(self, kind: str, *, date_ms: int, refresh: bool = False) -> DataResult:
        """One closed-enum market module.  Every module retains independent evidence."""
        params = {"date": date_ms, "module": kind}
        async def request():
            return await self._client.special.daily_list(kind, date_ms=date_ms)
        def normalize(payload: dict):
            records: list[NormalizedRecord] = []
            for position, row in enumerate(payload.get("item") or []):
                if not isinstance(row, dict):
                    continue
                code = str(row.get("thscode") or "")
                fields = {}
                # Keep the complete supplier row in the evidence model.  The
                # local market-review layer needs fields such as heat, seal
                # money and board groups; reducing every event to six generic
                # columns made meaningful local calculations impossible.
                for field, raw in row.items():
                    if field in {"thscode", "ticker"}:
                        continue
                    value = None if raw is None else (json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
                                                       if isinstance(raw, (dict, list)) else str(raw))
                    fields[field] = NormalizedField(
                        source_field=field, label=_market_event_field_label(field),
                        kind="number" if isinstance(raw, (int, float)) else "text", unit=_market_event_field_unit(field),
                        raw_value=value, value=value)
                records.append(NormalizedRecord(thscode=code, period_key=f"{date_ms}:{kind}:{position}",
                                                period_end=None, disclosed_at=None, basis=f"market_{kind}",
                                                currency=None, fields=fields))
            return records, [], 0
        return await self.fetch_result(
            f"special_data.{kind}", contract_for(DAILY_LISTS[kind]).path, params, request, normalize,
            f"market_{kind}", refresh=refresh)

    async def fetch_auction_snapshot(self, thscode: str, *, stage: str = "final", refresh: bool = False) -> DataResult:
        self.require_stock_identity(thscode)
        params = {"thscode": thscode, "stage": stage}
        async def request(): return await self._client.auction.snapshot(thscode, stage=stage)
        return await self.fetch_result("auction.snapshot", "/api/a-share/auction/snapshot", params, request,
            lambda payload: _unpack_event_rows(payload, thscode, "auction_snapshot"), "auction_snapshot", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_hot_rank_trend(self, thscode: str, *, start_ms: int, end_ms: int, refresh: bool = False) -> DataResult:
        if end_ms < start_ms:
            raise AppError("validation", "热榜趋势结束日期不能早于开始日期。", 422)
        self.require_stock_identity(thscode)
        params = {"thscode": thscode, "start": start_ms, "end": end_ms}
        async def request(): return await self._client.special.hot_rank_trend(thscode, start_ms=start_ms, end_ms=end_ms)
        return await self.fetch_result("special_data.hot_rank_trend", "/api/a-share/special-data/hot-stock-rank-trend", params, request,
            lambda payload: _unpack_event_rows(payload, thscode, "hot_rank_trend"), "hot_rank_trend", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_profile(self, thscode: str, *, fund_type: str, refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        params = {"thscode": thscode, "fund_type": fund_type}
        async def request(): return await self._client.funds.profile(thscode, asset_type=identity.asset_type, fund_type=fund_type)
        return await self.fetch_result("fund_profile", "/api/fund/profile/detail", params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, "fund_profile")), "fund_profile", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_report_dates(self, thscode: str, *, fund_type: str, holding: str = "stock", refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        params = {"thscode": thscode, "fund_type": fund_type, "holding": holding}
        async def request(): return await self._client.funds.report_dates(thscode, asset_type=identity.asset_type, fund_type=fund_type, holding=holding)
        return await self.fetch_result(f"fund_portfolio.{holding}_report_dates", f"/api/fund/portfolio/{holding}-report-dates", params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, f"fund_{holding}_report_dates")), f"fund_{holding}_report_dates", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_holdings(self, thscode: str, *, fund_type: str, report_type: str | None = None, end_date: str | None = None, refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        params = {"thscode": thscode, "fund_type": fund_type, "report_type": report_type, "end_date": end_date}
        async def request(): return await self._client.funds.holdings(thscode, asset_type=identity.asset_type, fund_type=fund_type, report_type=report_type, end_date=end_date)
        return await self.fetch_result("fund_portfolio.holdings", "/api/fund/portfolio/holdings", params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, "fund_holdings")), "fund_holdings", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_nav(self, thscode: str, *, fund_type: str, range_name: str = "year", refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        params = {"thscode": thscode, "fund_type": fund_type, "range": range_name}
        async def request(): return await self._client.funds.nav(thscode, asset_type=identity.asset_type, fund_type=fund_type, range_name=range_name)
        return await self.fetch_result("fund_performance.nav", "/api/fund/performance/nav", params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, "fund_nav")), "fund_nav", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_detail(self, thscode: str, *, fund_type: str, kind: str,
                                extra: dict[str, str] | None = None, refresh: bool = False) -> DataResult:
        """Fetch a registered fund endpoint without falling back to stock APIs."""
        identity = self.require_fund_identity(thscode)
        contract = contract_for(f"fund_{kind}")
        params = {"thscode": thscode, "fund_type": fund_type, **(extra or {})}
        async def request():
            return await self._client.funds.detail(thscode, asset_type=identity.asset_type,
                                                   fund_type=fund_type, kind=kind, extra=extra)
        return await self.fetch_result(
            contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, f"fund_{kind}")),
            f"fund_{kind}", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_allocation(self, thscode: str, *, fund_type: str, kind: str,
                                    refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        contract = contract_for(f"fund_{kind}_allocation")
        params = {"thscode": thscode, "fund_type": fund_type}
        async def request(): return await self._client.funds.allocation(thscode, asset_type=identity.asset_type,
                                                                         fund_type=fund_type, kind=kind)
        return await self.fetch_result(contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, f"fund_{kind}_allocation")),
            f"fund_{kind}_allocation", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_portfolio_history(self, thscode: str, *, fund_type: str, holding: str,
                                           report_type: str, end_date: str, refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        contract = contract_for(f"fund_{holding}_history")
        params = {"thscode": thscode, "fund_type": fund_type, "report_type": report_type, "end_date": end_date}
        async def request(): return await self._client.funds.portfolio_history(thscode, asset_type=identity.asset_type,
                                                                               fund_type=fund_type, holding=holding,
                                                                               report_type=report_type, end_date=end_date)
        return await self.fetch_result(contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, f"fund_{holding}_history")),
            f"fund_{holding}_history", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_offerings(self, *, subscribe: str, refresh: bool = False) -> DataResult:
        contract = contract_for("fund_offerings")
        params = {"subscribe": subscribe}
        async def request(): return await self._client.funds.offerings(subscribe=subscribe)
        return await self.fetch_result(contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, "", "fund_offerings")),
            "fund_offerings", refresh=refresh)

    async def fetch_fund_performance_detail(self, thscode: str, *, fund_type: str, kind: str,
                                            refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        contract = contract_for(f"fund_{kind}")
        params = {"thscode": thscode, "fund_type": fund_type}
        async def request(): return await self._client.funds.performance_detail(thscode, asset_type=identity.asset_type,
                                                                                 fund_type=fund_type, kind=kind)
        return await self.fetch_result(contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, f"fund_{kind}")),
            f"fund_{kind}", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_market_snapshot(self, thscode: str, *, refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        params = {"thscodes": thscode}
        async def request(): return await self._client.funds.market_snapshot(thscode, asset_type=identity.asset_type)
        return await self.fetch_result("fund_market", "/api/fund/market/snapshot", params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, thscode, "fund_market_snapshot")),
            "fund_market_snapshot", thscode=thscode, security_id=thscode, refresh=refresh)

    async def fetch_fund_manager(self, manager_id: str, *, kind: str, refresh: bool = False) -> DataResult:
        contract = contract_for(f"fund_manager_{kind}")
        params = {"manager_id": manager_id}
        async def request(): return await self._client.funds.manager(manager_id, kind=f"manager_{kind}")
        return await self.fetch_result(contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, "", f"fund_manager_{kind}")),
            f"fund_manager_{kind}", refresh=refresh)

    async def fetch_fund_company(self, company_id: str, *, refresh: bool = False) -> DataResult:
        contract = contract_for("fund_company_detail")
        params = {"company_id": company_id}
        async def request(): return await self._client.funds.company(company_id)
        return await self.fetch_result(contract.capability, contract.path, params, request,
            lambda payload: _unpack(normalize_fund_rows(payload, "", "fund_company")),
            "fund_company", refresh=refresh)

    async def fetch_fund_news(self, thscode: str, *, fund_type: str, limit: int | None = None,
                              offset: str | None = None, refresh: bool = False) -> DataResult:
        identity = self.require_fund_identity(thscode)
        params = {"thscode": thscode, "fund_type": fund_type, "limit": limit, "offset": offset}
        async def request(): return await self._client.funds.news(thscode, asset_type=identity.asset_type,
                                                                   fund_type=fund_type, limit=limit, offset=offset)
        return await self.fetch_result("fund_extended", "/api/fund/news/article-list", params, request,
            lambda payload: normalize_fund_news(payload, thscode),
            "fund_news", thscode=thscode, security_id=thscode, refresh=refresh)

    # -- settings-card capability tests --------------------------------------
    async def test_capability(self, profile_id: str, capability: str,
                              credential_version: int) -> dict:
        """Minimal real probe for one catalog capability; never fakes results."""
        probes = {
            "prices": [self._probe_search, self._probe_snapshot],
            "valuations": [self._probe_valuations],
            "financials": [self._probe_statements],
            "reference": [self._probe_ticker_list],
            "corporate_actions": [self._probe_adjustment_factors],
        }
        status, message = "ok", ""
        for probe in probes.get(capability, []):
            try:
                await probe()
            except AppError as error:
                if error.category == "permission_denied":
                    status, message = "permission_denied", "凭据对该能力没有权限。"
                elif error.category == "rate_limited":
                    status, message = "quota_limited", "已触发频率或额度限制。"
                elif error.category in ("not_configured", "unsupported"):
                    status, message = "failed", "数据服务未配置或已停用。"
                else:
                    status, message = "failed", "探针未取得可用结果。"
                break
        return {"capability": capability, "status": status, "message": message,
                "credential_version": credential_version}

    async def _probe_search(self) -> None:
        await self._client.search("600519", limit=1)

    async def _probe_snapshot(self) -> None:
        await self._client.price_snapshot(["600519.SH"])

    async def _probe_valuations(self) -> None:
        await self._client.valuation_snapshot(["600519.SH"])

    async def _probe_statements(self) -> None:
        await self._client.financial_statement("income", "600519.SH", period="annual", limit=1)

    async def _probe_ticker_list(self) -> None:
        await self._client.ticker_list(asset_type="a-share", limit=1)

    async def _probe_adjustment_factors(self) -> None:
        await self._client.adjustment_factors("600519.SH")

    # -- evidence reads ------------------------------------------------------
    def evidence(self, evidence_id: str):
        return self._store.get(evidence_id)

    def evidence_list(self, *, thscode: str | None = None, capability: str | None = None,
                      limit: int = 50):
        return self._store.list(thscode=thscode, capability=capability, limit=limit)


def _unpack(result):
    return result.records, result.gaps, result.duplicate_versions


_MARKET_EVENT_LABELS = {
    "name": "股票名称", "rank": "热度排名", "heat": "热度值", "rank_change": "排名变化",
    "rank_trend": "排名趋势", "continue_day_cnt": "连板天数", "continue_day_text": "连板标识",
    "limit_up_reason": "涨停原因", "limit_up_time": "首次涨停时间", "first_limit_time": "首次跌停时间",
    "last_limit_time": "最后跌停时间", "price_change_ratio_pct": "涨跌幅", "last_price": "最新价",
    "seal_money": "封单金额", "max_seal_money": "最大封单金额", "turnover": "成交额",
    "turnover_ratio_pct": "换手率", "open_times": "开板次数", "boards": "连板梯队",
    "date": "交易日期", "board_num": "连板数", "seal_nextday": "次日封板", "reason": "上榜原因",
    "window": "统计窗口", "range_days": "统计窗口", "is_new": "次新股标识", "is_st": "ST 标识",
}


def _market_event_field_label(field: str) -> str:
    """Readable labels for known market fields; unknown fields remain traceable."""
    return _MARKET_EVENT_LABELS.get(field, f"供应商字段：{field}")


def _market_event_field_unit(field: str) -> str:
    if field in {"price_change_ratio_pct", "turnover_ratio_pct"}:
        return "%"
    if field in {"seal_money", "max_seal_money", "turnover", "heat"}:
        return "元" if field != "heat" else "热度"
    return ""


def _unpack_calendar(payload: dict):
    records = [NormalizedRecord(
        thscode="", period_key=str(item.get("date", "")), period_end=None, disclosed_at=None,
        basis="calendar", currency=None, fields={}) for item in payload.get("item") or []]
    return records, [], 0


def _unpack_event_rows(payload: dict, thscode: str, basis: str):
    records = []
    for position, row in enumerate(payload.get("item") or []):
        if not isinstance(row, dict): continue
        fields = {key: NormalizedField(source_field=key, label=key, kind="text", unit="", raw_value=None if value is None else str(value), value=None if value is None else str(value)) for key, value in row.items() if key != "thscode"}
        records.append(NormalizedRecord(thscode=thscode, period_key=str(row.get("date") or row.get("timestamp") or position), period_end=None, disclosed_at=None, basis=basis, currency=None, fields=fields))
    return records, [], 0


def iso_or_none(ms) -> str | None:
    return iso_from_ms(ms)
