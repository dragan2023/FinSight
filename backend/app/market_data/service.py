"""Market data service: identity confirmation, fetch, normalize, evidence.

Fetch pipeline: confirmed identity -> cache lookup (key isolates provider,
profile, credential_version, endpoint, params, schema) -> ThsClient ->
normalization -> evidence ledger -> DataResult. A failing capability becomes
a DataGap inside DataResult; it never raises a 500 and never blocks siblings
in multi-statement fetches.
"""

from __future__ import annotations

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
    ts_code_from_thscode,
)
from .models import STATEMENTS, Capability
from .normalization import (
    BASIS_SNAPSHOT,
    NormalizedRecord,
    iso_from_ms,
    normalize_indicators,
    normalize_price_history,
    normalize_snapshot,
    normalize_statement_rows,
)
from .ths_client import ThsClient
from .tushare_client import TushareClient, normalize_tushare_rows

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
                 *, client: ThsClient | None = None,
                 tushare_client: TushareClient | None = None) -> None:
        self._credentials = credentials
        self._repository = ResearchRepository(storage)
        self._store = EvidenceStore(self._repository)
        self._cache = DataCache(self._repository)
        self._client = client or ThsClient(http, credential_source=self.credential_source)
        self._tushare = tushare_client or TushareClient(
            http, credential_source=self.tushare_credential_source)

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

    def tushare_profile(self) -> dict | None:
        return self.profile_for("tushare")

    def credential_source(self) -> tuple[str | None, bool]:
        return self._credential_for("hexin_finance")

    def tushare_credential_source(self) -> tuple[str | None, bool]:
        return self._credential_for("tushare")

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

    # -- identity ------------------------------------------------------------
    async def resolve_candidates(self, query: str, *, exchange: str | None = None,
                                 asset_type: str | None = None, limit: int = 10) -> list[TickerCandidate]:
        data = await self._client.search(query, exchange=exchange, asset_type=asset_type, limit=limit)
        return [TickerCandidate.model_validate(item) for item in data.get("item") or []]

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
        records, candidates, duplicates = normalize(payload)
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
                            "duplicate_versions": duplicates},
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

    # -- Tushare supplements (plan 04 §2.4) -----------------------------------
    async def fetch_tushare_supplement(self, api_name: str, thscode: str, *,
                                       start_date: str, end_date: str,
                                       security_id: str | None = None,
                                       refresh: bool = False) -> DataResult:
        """One authorized supplement API for the confirmed entity only.

        未授权接口按具体缺口处理（permission_denied），不阻止其他来源；
        capability 前缀决定缓存 TTL（financial*/historical*/ticker_search*）。
        """
        ts_code = ts_code_from_thscode(thscode)
        params: dict = {"ts_code": ts_code, "start_date": start_date, "end_date": end_date}
        if api_name == "stock_basic":
            params = {"ts_code": ts_code}
        async def request():
            if api_name == "stock_basic":
                return {"rows": await self._tushare.stock_basic(ts_code)}
            if api_name == "daily_basic":
                return {"rows": await self._tushare.daily_basic(
                    ts_code, start_date=start_date, end_date=end_date)}
            if api_name in ("income", "balancesheet", "cashflow"):
                return {"rows": await self._tushare.statement(
                    api_name, ts_code, start_date=start_date, end_date=end_date)}
            if api_name == "fina_indicator":
                return {"rows": await self._tushare.fina_indicator(
                    ts_code, start_date=start_date, end_date=end_date)}
            if api_name == "fina_audit":
                return {"rows": await self._tushare.fina_audit(
                    ts_code, start_date=start_date, end_date=end_date)}
            if api_name == "fina_mainbz":
                return {"rows": await self._tushare.fina_mainbz(
                    ts_code, start_date=start_date, end_date=end_date)}
            if api_name == "disclosure_date":
                return {"rows": await self._tushare.disclosure_date(
                    ts_code, start_date=start_date, end_date=end_date)}
            raise AppError("validation", "不支持的补充接口。", 422)
        def normalize(payload):
            records, gaps, duplicates = normalize_tushare_rows(
                api_name, payload.get("rows") or [], ts_code, "supplement." + api_name)
            return records, gaps, duplicates
        capability = {"stock_basic": "ticker_search_stock_basic",
                      "daily_basic": "historical_daily_basic"}.get(
                          api_name, "financial_supplement_" + api_name)
        return await self.fetch_result(
            capability, "/tushare/" + api_name, params, request, normalize,
            "supplement." + api_name, thscode=thscode, security_id=security_id,
            refresh=refresh, provider="tushare")

    # -- settings-card capability tests --------------------------------------
    async def test_capability(self, profile_id: str, capability: str,
                              credential_version: int, provider: str = PROVIDER) -> dict:
        """Minimal real probe for one catalog capability; never fakes results."""
        if provider == "tushare":
            probes = {
                "stock_basic": [self._probe_tushare_basic],
                "daily_basic": [self._probe_tushare_daily],
                "financials": [self._probe_tushare_financials],
                "indicators": [self._probe_tushare_indicator],
                "audit": [self._probe_tushare_audit],
                "mainbz": [self._probe_tushare_mainbz],
                "disclosure": [self._probe_tushare_disclosure],
            }
        else:
            probes = {
                "prices": [self._probe_search, self._probe_snapshot],
                "valuations": [self._probe_valuations],
                "financials": [self._probe_statements],
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

    # -- Tushare probes（真实计费调用，上游 confirm_external 闸门已把关） --------
    async def _probe_tushare_basic(self) -> None:
        await self._tushare.stock_basic("600519.SH")

    async def _probe_tushare_daily(self) -> None:
        await self._tushare.daily_basic("600519.SH", start_date="20241220", end_date="20241231")

    async def _probe_tushare_financials(self) -> None:
        await self._tushare.statement("income", "600519.SH", start_date="20240101", end_date="20241231")

    async def _probe_tushare_indicator(self) -> None:
        await self._tushare.fina_indicator("600519.SH", start_date="20240101", end_date="20241231")

    async def _probe_tushare_audit(self) -> None:
        await self._tushare.fina_audit("600519.SH", start_date="20240101", end_date="20241231")

    async def _probe_tushare_mainbz(self) -> None:
        await self._tushare.fina_mainbz("600519.SH", start_date="20240101", end_date="20241231")

    async def _probe_tushare_disclosure(self) -> None:
        await self._tushare.disclosure_date("600519.SH", start_date="20240101", end_date="20241231")
    # -- evidence reads ------------------------------------------------------
    def evidence(self, evidence_id: str):
        return self._store.get(evidence_id)

    def evidence_list(self, *, thscode: str | None = None, capability: str | None = None,
                      limit: int = 50):
        return self._store.list(thscode=thscode, capability=capability, limit=limit)


def _unpack(result):
    return result.records, result.gaps, result.duplicate_versions


def _unpack_calendar(payload: dict):
    records = [NormalizedRecord(
        thscode="", period_key=str(item.get("date", "")), period_end=None, disclosed_at=None,
        basis="calendar", currency=None, fields={}) for item in payload.get("item") or []]
    return records, [], 0


def iso_or_none(ms) -> str | None:
    return iso_from_ms(ms)
