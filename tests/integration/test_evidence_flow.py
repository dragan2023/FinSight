"""Evidence flow integration tests (plan 02 section 4).

Scripted hexin responses through the full app: resolve/confirm, multi-statement
fetch isolation, gaps instead of 500, evidence read with traversal guard,
token scrubbing, and cache isolation across credential versions.
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings

KEY_V1 = "sk-fuyao-unit-key-one"
KEY_V2 = "sk-fuyao-unit-key-two"
SECRET_TOKEN = "sk-fuyao-LEAKEDTOKEN123456789"


def envelope(data, code=0, message="success"):
    return {"code": code, "message": message, "request_id": "rid", "data": data}


def search_item(thscode="600519.SH", ticker="600519", name="贵州茅台"):
    return {"thscode": thscode, "ticker": ticker, "name": name,
            "exchange": thscode.split(".")[1], "asset_type": "a-share", "currency": "CNY"}


def income_row():
    return {"thscode": "600519.SH", "ticker": "600519", "period": "annual",
            "fiscal_year": 2024, "fiscal_period": "FY", "report_date_ms": 1735574400000,
            "period_end_ms": 1735574400000, "currency": "CNY",
            "operating_income": 174144000000, "net_profit": 93000000000,
            "parent_holder_net_profit": 86000000000}


def bar(date_ms, close):
    return {"date_ms": date_ms, "open_price": close, "high_price": close,
            "low_price": close, "close_price": close, "prev_price": close,
            "price_change": 0, "price_change_ratio_pct": 0.0,
            "volume": 100, "turnover": close * 100}


class Script:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = {key: value for key, value in request.url.params.items()}
        key = request.headers.get("X-api-key", "")
        self.calls.append((path, params, key))
        if path == "/api/meta/tickers/search":
            return httpx.Response(200, json=envelope({"timestamp": 1, "item": [search_item()]}))
        if path == "/api/a-share/prices/snapshot":
            return httpx.Response(200, json=envelope(
                {"timestamp": None, "item": [{"thscode": "600519.SH", "ticker": "600519",
                                              "last_price": 1299.52, "volume": 100}]}))
        if path == "/api/a-share/valuations/snapshot":
            return httpx.Response(200, json=envelope(None, 3001, "ticker not found"))
        if path == "/api/a-share/prices/historical":
            return httpx.Response(200, json=envelope(
                {"timestamp": None, "item": [bar(1756656000000, 100.0), bar(1756742400000, 120.0)],
                 "thscode": "600519.SH", "interval": "1d", "adjust": "forward"}))
        if path == "/api/a-share/financials/income-statements":
            return httpx.Response(200, json=envelope(
                {"timestamp": 1735574400000, "item": [income_row()],
                 "note": SECRET_TOKEN}))
        if path == "/api/a-share/financials/balance-sheets":
            return httpx.Response(200, json=envelope(None, 4001, "qps exceeded"))
        if path == "/api/a-share/financials/cash-flow-statements":
            return httpx.Response(200, json=envelope(None, 3002, "not ready"))
        return httpx.Response(404, json=envelope(None, 3001, "no route"))


@pytest.fixture
def harness(tmp_path):
    script = Script()
    settings = Settings(project_root=tmp_path, encryption_key=Fernet.generate_key().decode())

    def build_client():
        app = create_app(settings, transport=httpx.MockTransport(script))
        client = TestClient(app, base_url="http://127.0.0.1:8000")
        return client

    client = build_client()
    with client:
        token = client.get("/api/system/session", headers={"X-FinSight-Client": "web"}).json()["token"]
        client.headers.update({"X-FinSight-Session": token, "Content-Type": "application/json"})
        response = client.post("/api/integrations/profiles", json={
            "provider": "hexin_finance", "name": "同花顺联调", "api_key": KEY_V1,
            "enabled": True,
        })
        assert response.status_code == 201, response.text
        profile = response.json()
        yield {"client": client, "script": script, "settings": settings,
               "profile": profile, "build_client": build_client}


def confirm(client):
    response = client.post("/api/data/resolve", json={"query": "600519"})
    assert response.status_code == 200, response.text
    assert response.json()["candidates"], "search should return at least one candidate"
    response = client.post("/api/data/resolve", json={"confirm_thscode": "600519.SH"})
    assert response.status_code == 200, response.text
    return response.json()["identity"]


def test_resolve_and_confirm_save_identity(harness):
    client = harness["client"]
    identity = confirm(client)
    assert identity["thscode"] == "600519.SH"
    assert identity["confirmed_source"] == "user_selected"
    listed = client.get("/api/data/identities").json()["identities"]
    assert [item["thscode"] for item in listed] == ["600519.SH"]


def test_fetch_requires_confirmed_identity(harness):
    response = harness["client"].post("/api/data/fetch",
                                      json={"capability": "price_snapshot", "thscode": "600519.SH"})
    assert response.status_code == 404
    assert "确认标的" in response.json()["error"]["message"]


def test_multi_statement_failure_keeps_others(harness):
    client = harness["client"]
    confirm(client)
    response = client.post("/api/data/fetch", json={
        "capability": "financial_statements", "thscode": "600519.SH", "statement": "all"})
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [result["status"] for result in results] == ["available", "unavailable", "unavailable"]
    income, balance, cashflow = results
    assert income["records"][0]["fields"]["operating_income"]["value"] == "174144000000"
    assert balance["gaps"][0]["reason"] == "rate_limited"
    assert cashflow["gaps"][0]["reason"] == "not_published"
    assert income["evidence_ids"] and not balance["evidence_ids"]
    # The failing statements stored no evidence; the working one did.
    evidence = client.get(f"/api/evidence/{income['evidence_ids'][0]}")
    assert evidence.status_code == 200
    body = evidence.json()["evidence"]
    assert body["records"][0]["period_key"] == "2024-FY"
    assert body["basis"] == "annual_report"


def test_missing_data_is_gap_not_500(harness):
    client = harness["client"]
    confirm(client)
    response = client.post("/api/data/fetch", json={
        "capability": "valuations", "thscode": "600519.SH"})
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "unavailable"
    assert result["gaps"][0]["reason"] == "unsupported"
    assert result["records"] == []


def test_tokens_are_stripped_from_stored_artifacts(harness):
    client = harness["client"]
    confirm(client)
    response = client.post("/api/data/fetch", json={
        "capability": "financial_statements", "thscode": "600519.SH", "statement": "income"})
    assert response.status_code == 200
    db = sqlite3.connect(harness["settings"].db_path)
    try:
        blobs = []
        for table, column in (("evidence", "records_json"), ("evidence", "params_json"),
                              ("data_cache", "payload_json")):
            blobs.extend(row[0] for row in db.execute(f"SELECT {column} FROM {table}"))
    finally:
        db.close()
    assert blobs and all(SECRET_TOKEN not in blob for blob in blobs)


def test_evidence_id_guard_blocks_non_ids(harness):
    for bad in ("not-an-id", "../../etc/passwd", "a" * 31, "a" * 33):
        response = harness["client"].get(f"/api/evidence/{bad}")
        assert response.status_code == 404, bad
    matched = harness["client"].get(f"/api/evidence/{'a' * 32}")
    assert matched.status_code == 404
    assert "证据不存在" in matched.json()["error"]["message"]


def test_cache_isolated_by_credential_version(harness):
    client, script = harness["client"], harness["script"]
    confirm(client)
    body = {"capability": "price_history", "thscode": "600519.SH",
            "start": 1756570000000, "end": 1756829000000}
    first = client.post("/api/data/fetch", json=body)
    assert first.status_code == 200
    calls_after_first = len(script.calls)
    second = client.post("/api/data/fetch", json=body)
    assert second.status_code == 200
    assert len(script.calls) == calls_after_first, "second fetch must hit the cache"
    assert second.json()["results"][0]["returned_scope"]["from_cache"] is True

    # Replacing the key bumps credential_version and must bypass the cache.
    profile = harness["profile"]
    response = client.put(f"/api/integrations/profiles/{profile['id']}", json={
        "provider": "hexin_finance", "name": "同花顺联调", "api_key": KEY_V2,
        "enabled": True, "credential_version": profile["credential_version"],
    })
    assert response.status_code == 200, response.text
    third = client.post("/api/data/fetch", json=body)
    assert third.status_code == 200
    assert len(script.calls) > calls_after_first, "new credential must not reuse the old cache"
    assert script.calls[-1][2] == KEY_V2
    assert third.json()["results"][0]["returned_scope"]["from_cache"] is False


def test_price_history_computes_without_derived_fabrication(harness):
    client = harness["client"]
    confirm(client)
    response = client.post("/api/data/fetch", json={
        "capability": "price_history", "thscode": "600519.SH",
        "start": 1756570000000, "end": 1756829000000})
    result = response.json()["results"][0]
    assert result["status"] == "available"
    assert len(result["records"]) == 2
    assert result["records"][0]["basis"] == "daily_bar"
