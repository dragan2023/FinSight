"""Offline contract tests for TushareClient (plan 04 section 2.4 / section 4)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app.errors import AppError
from backend.app.market_data.identity import (
    ts_code_from_thscode,
    verify_stock_basic_mapping,
)
from backend.app.market_data.models import (
    InvalidUpstreamResponse,
    PermissionDenied,
    RateLimited,
)
from backend.app.market_data.tushare_client import (
    BudgetExhausted,
    TushareClient,
    TusharePermissionDenied,
    TushareRateLimited,
    normalize_tushare_rows,
    consolidation_period_basis,
)


TOKEN = "ts-token-not-a-real-token"
run = asyncio.run


def envelope(code: int = 0, fields: list | None = None, items: list | None = None,
             msg: str | None = None) -> dict:
    payload = {"code": code, "msg": msg}
    if code == 0:
        payload["data"] = {"fields": fields or [], "items": items or []}
    return payload


class Harness:
    def __init__(self, handler, *, token: str | None = TOKEN, enabled: bool = True,
                 max_retries: int = 2, budget_flags: list[bool] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.spends = 0

        def track(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request, len(self.requests))

        async def fake_sleep(seconds: float) -> None:
            self.slept.append(seconds)

        def spend() -> bool:
            self.spends += 1
            if budget_flags is None:
                return True
            return budget_flags[self.spends - 1] if self.spends <= len(budget_flags) else True

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(track), trust_env=False)
        self.client = TushareClient(
            self.http,
            credential_source=lambda: (token, enabled),
            sleep=fake_sleep,
            max_retries=max_retries,
            budget_spend=spend if budget_flags is not None else None,
        )

    async def aclose(self) -> None:
        await self.http.aclose()


def ok_response(fields: list[str], items: list[list]) -> httpx.Response:
    return httpx.Response(200, json=envelope(0, fields, items))


class TestRequestContract:
    def test_token_in_body_https_and_fields(self):
        seen = {}

        def handler(request, _):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.read())
            seen["headers"] = dict(request.headers)
            return ok_response(
                ["ts_code", "symbol", "name", "area", "industry", "market", "list_date"],
                [["600519.SH", "600519", "贵州茅台", "贵州", "白酒", "主板", "20010827"]])

        harness = Harness(handler)
        try:
            rows = run(harness.client.stock_basic("600519.SH"))
            assert seen["url"] == "https://api.tushare.pro/"
            body = seen["body"]
            assert body["api_name"] == "stock_basic"
            assert body["token"] == TOKEN and TOKEN not in json.dumps(seen["headers"])
            assert body["fields"] == "ts_code,symbol,name,area,industry,market,list_date"
            assert rows[0]["ts_code"] == "600519.SH" and rows[0]["name"] == "贵州茅台"
        finally:
            run(harness.aclose())

    def test_fields_items_misaligned_rejected(self):
        harness = Harness(lambda request, _: ok_response(["a", "b"], [[1, 2, 3]]))
        try:
            with pytest.raises(InvalidUpstreamResponse):
                run(harness.client.call("fina_audit", {}, "a,b"))
            assert len(harness.requests) == 1
        finally:
            run(harness.aclose())

    def test_missing_field_in_response_rejected(self):
        harness = Harness(lambda request, _: ok_response(["a"], [[1]]))
        try:
            with pytest.raises(InvalidUpstreamResponse):
                run(harness.client.call("fina_audit", {}, "ts_code,end_date"))
        finally:
            run(harness.aclose())


class TestErrors:
    def test_permission_message_never_retried(self):
        harness = Harness(lambda request, _: httpx.Response(
            200, json=envelope(-2001, msg="抱歉，您暂时没有该接口的访问权限")))
        try:
            with pytest.raises(TusharePermissionDenied):
                run(harness.client.call("fina_indicator", {}, "ts_code,end_date"))
            assert len(harness.requests) == 1
        finally:
            run(harness.aclose())

    def test_rate_message_retries_then_rate_limited(self):
        harness = Harness(lambda request, _: httpx.Response(
            200, json=envelope(-2001, msg="抱歉，您每分钟最多访问该接口1次")))
        try:
            with pytest.raises(TushareRateLimited):
                run(harness.client.call("fina_indicator", {}, "ts_code,end_date"))
            assert len(harness.requests) == 3 and harness.slept == [0.5, 0.5]
        finally:
            run(harness.aclose())

    def test_http_429_honours_retry_after(self):
        harness = Harness(lambda request, _: httpx.Response(
            429, headers={"Retry-After": "3"}, json={}))
        try:
            with pytest.raises(RateLimited):
                run(harness.client.call("fina_indicator", {}, "ts_code,end_date"))
            assert harness.slept == [3.0, 3.0]
        finally:
            run(harness.aclose())

    def test_persistent_500_invalid_response(self):
        harness = Harness(lambda request, _: httpx.Response(500))
        try:
            with pytest.raises(InvalidUpstreamResponse):
                run(harness.client.call("fina_indicator", {}, "ts_code,end_date"))
            assert len(harness.requests) == 3
        finally:
            run(harness.aclose())

    def test_http_401_permission(self):
        harness = Harness(lambda request, _: httpx.Response(401))
        try:
            with pytest.raises(PermissionDenied):
                run(harness.client.call("fina_indicator", {}, "ts_code,end_date"))
            assert len(harness.requests) == 1
        finally:
            run(harness.aclose())



INCOME_FIELDS = ("ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                 "total_revenue,n_income,n_income_attr_p")
INCOME_ROWS = [
    ["600519.SH", "20240402", "20240402", "20231231", "1", "1", "1",
     150600000000, 74700000000, 74730000000],
    ["600519.SH", "20240420", "20240420", "20231231", "1", "1", "2",
     150610000000, 74750000000, 74780000000],
]


class TestBudgetAndValidation:
    def test_budget_exhausted_zero_requests(self):
        harness = Harness(lambda request, _: httpx.Response(200), budget_flags=[False])
        try:
            with pytest.raises(BudgetExhausted):
                run(harness.client.call("fina_indicator", {}, "ts_code,end_date"))
            assert harness.requests == []
        finally:
            run(harness.aclose())

    def test_budget_spent_per_attempt(self):
        harness = Harness(lambda request, _: httpx.Response(500), budget_flags=[True, False])
        try:
            with pytest.raises(BudgetExhausted):
                run(harness.client.call("daily_basic", {"ts_code": "600519.SH"}, "ts_code"))
            assert len(harness.requests) == 1 and harness.spends == 2
        finally:
            run(harness.aclose())

    @pytest.mark.parametrize("bad", ["600519.SS", "600519", "000001.sz"])
    def test_bad_ts_code_rejected_before_wire(self, bad):
        harness = Harness(lambda request, _: httpx.Response(200))
        try:
            with pytest.raises(AppError) as excinfo:
                run(harness.client.stock_basic(bad))
            assert excinfo.value.category == "validation"
            assert harness.requests == []
        finally:
            run(harness.aclose())

    def test_bad_date_rejected(self):
        harness = Harness(lambda request, _: httpx.Response(200))
        try:
            with pytest.raises(AppError):
                run(harness.client.daily_basic(
                    "600519.SH", start_date="2024-01-01", end_date="20241231"))
            assert harness.requests == []
        finally:
            run(harness.aclose())


class TestNormalize:
    def test_revision_keeps_latest_disclosure(self):
        rows = [dict(zip(INCOME_FIELDS.split(","), row)) for row in INCOME_ROWS]
        records, gaps, duplicates = normalize_tushare_rows(
            "income", rows, "600519.SH", "supplement.income")
        assert duplicates == 1 and len(records) == 1
        record = records[0]
        assert record.period_key == "2023-12-31"
        assert record.disclosed_at == "2024-04-20"
        assert record.basis == "annual_report"
        assert record.fields["report_type"].value == "1"
        assert record.fields["operating_income"].value == "150610000000"
        assert record.currency == "CNY"
        assert gaps == []

    def test_percent_converted_and_null_is_gap(self):
        rows = [{"ts_code": "600519.SH", "ann_date": "20240402", "end_date": "20231231",
                 "grossprofit_margin": "45.67", "current_ratio": None}]
        records, gaps, _ = normalize_tushare_rows(
            "fina_indicator", rows, "600519.SH", "supplement.fina_indicator")
        fields = records[0].fields
        assert fields["sale_gross_margin"].value == "0.4567"
        assert fields["sale_gross_margin"].unit == "ratio"
        assert fields["current_ratio"].value is None
        assert gaps[0].field == "current_ratio" and gaps[0].reason == "not_published"

    def test_disclosure_plan_and_actual_stay_separate(self):
        rows = [{"ts_code": "600519.SH", "end_date": "20240630",
                 "pre_date": "20240820", "actual_date": None, "modify_date": None}]
        records, gaps, _ = normalize_tushare_rows(
            "disclosure_date", rows, "600519.SH", "supplement.disclosure_date")
        fields = records[0].fields
        assert fields["disclosure_planned_date"].value == "2024-08-20"
        assert fields["disclosure_actual_date"].value is None
        assert any(g.field == "disclosure_actual_date" for g in gaps)

    def test_report_type_basis_mapping(self):
        assert consolidation_period_basis("1") == ("consolidated", "cumulative")
        assert consolidation_period_basis("2") == ("consolidated", "single_quarter")
        assert consolidation_period_basis("6") == ("parent", "cumulative")
        assert consolidation_period_basis("") == ("unknown", "unknown")


class TestIdentityMapping:
    def test_ts_code_mapping(self):
        assert ts_code_from_thscode("600519.SH") == "600519.SH"
        with pytest.raises(AppError):
            ts_code_from_thscode("600519.SS")
        with pytest.raises(AppError):
            ts_code_from_thscode("600519")

    def test_verify_mapping(self):
        assert verify_stock_basic_mapping({"ts_code": "600519.SH", "name": "贵州茅台"}, "600519.SH")
        assert not verify_stock_basic_mapping({"ts_code": "000001.SZ", "name": "x"}, "600519.SH")
        assert not verify_stock_basic_mapping({"ts_code": "600519.SH", "name": ""}, "600519.SH")



class TestServicePipeline:
    """Service wiring: credentials -> client -> normalize -> evidence/DataResult."""

    def make_service(self, tmp_path, handler, *, token=TOKEN, enabled=True):
        from pydantic import SecretStr

        from backend.app.market_data.service import MarketDataService
        from backend.app.storage import Storage

        class Encryption:
            def key_status(self, ciphertext):
                return "configured" if ciphertext and token else "empty"

            def decrypt(self, ciphertext):
                return SecretStr(token or "")

        class Repository:
            def list(self):
                return [{"provider": "tushare", "enabled": enabled,
                         "encrypted_api_key": "cipher-ts", "credential_version": 2,
                         "id": "ts-profile-1"}]

        class Credentials:
            repository = Repository()
            root = type("R", (), {"encryption": Encryption()})()

        storage = Storage(tmp_path / "t.db")
        storage.initialize()
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: handler(request, 0)),
            trust_env=False)
        service = MarketDataService(Credentials(), storage, http)
        return service, http

    def test_fetch_saves_evidence_with_tushare_provider(self, tmp_path):
        seen = {}

        def handler(request, _):
            seen["url"] = str(request.url)
            body = json.loads(request.read())
            seen["body"] = body
            fields = body["fields"].split(",")
            samples = {"ts_code": "600519.SH", "ann_date": "20240402", "f_ann_date": "20240402",
                       "end_date": "20231231", "report_type": "1", "comp_type": "1",
                       "update_flag": "1", "total_revenue": 150610000000,
                       "revenue": 150600000000, "operate_cost": 30000000000,
                       "operate_profit": 75000000000, "total_profit": 76000000000,
                       "income_tax": 1300000000, "n_income": 74750000000,
                       "n_income_attr_p": 74780000000, "basic_eps": 59.49}
            return ok_response(fields, [[samples.get(f) for f in fields]])

        service, http = self.make_service(tmp_path, handler)
        try:
            result = run(service.fetch_tushare_supplement(
                "income", "600519.SH", start_date="20240101", end_date="20241231"))
            assert result.status.value == "available"
            assert result.gaps == []
            assert result.evidence_ids
            assert result.source_profile_id == "ts-profile-1"
            assert result.records[0]["period_key"] == "2023-12-31"
            assert seen["body"]["token"] == TOKEN
            assert seen["body"]["api_name"] == "income"
            meta, _records, _gaps = service.evidence(result.evidence_ids[0])
            assert meta.provider == "tushare"
        finally:
            run(http.aclose())

    def test_unconfigured_token_becomes_gap_not_500(self, tmp_path):
        service, http = self.make_service(tmp_path, lambda request, _: httpx.Response(200),
                                          token=None)
        try:
            result = run(service.fetch_tushare_supplement(
                "income", "600519.SH", start_date="20240101", end_date="20241231"))
            assert result.status.value == "unavailable"
            assert result.gaps[0].reason == "not_configured"
            assert result.gaps[0].impact
        finally:
            run(http.aclose())

    def test_permission_denied_becomes_specific_gap(self, tmp_path):
        def handler(request, _):
            return httpx.Response(200, json=envelope(-2001, msg="抱歉，您暂时没有该接口的访问权限"))

        service, http = self.make_service(tmp_path, handler)
        try:
            result = run(service.fetch_tushare_supplement(
                "fina_mainbz", "600519.SH", start_date="20240101", end_date="20241231"))
            assert result.status.value == "unavailable"
            assert result.gaps[0].reason == "permission_denied"
            assert "其余能力不受影响" in result.gaps[0].impact
        finally:
            run(http.aclose())

    def test_capability_probe_routes_by_provider(self, tmp_path):
        def handler(request, _):
            assert json.loads(request.read())["api_name"] == "stock_basic"
            return ok_response(
                ["ts_code", "symbol", "name", "area", "industry", "market", "list_date"],
                [["600519.SH", "600519", "贵州茅台", "贵州", "白酒", "主板", "20010827"]])

        service, http = self.make_service(tmp_path, handler)
        try:
            view = run(service.test_capability("ts-profile-1", "stock_basic", 2,
                                               provider="tushare"))
            assert view["status"] == "ok" and view["capability"] == "stock_basic"
            assert view["credential_version"] == 2
        finally:
            run(http.aclose())
