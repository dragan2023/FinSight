"""Offline contract tests for ThsClient (plan 02 §4)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app.errors import AppError
from backend.app.market_data.models import (
    ConnectionFailed,
    InvalidUpstreamResponse,
    PermissionDenied,
    RateLimited,
    SourceDisabled,
    SourceNotConfigured,
    UpstreamRejected,
    UpstreamTimeout,
)
from backend.app.market_data.ths_client import ThsClient

API_KEY = "sk-test-not-a-real-key"
HISTORY_WINDOW_OK = {"start_ms": 1756656000000, "end_ms": 1756742400000}


def envelope(code: int = 0, data: dict | None = None, message: str = "success") -> dict:
    return {"code": code, "message": message, "request_id": "test", "data": data if data is not None else {"timestamp": 1, "item": []}}


class Harness:
    """Builds a client around a MockTransport and records every request."""

    def __init__(self, handler, *, api_key: str | None = API_KEY, enabled: bool = True,
                 sleep=None, max_retries: int = 2, pend_sleep: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []
        self.pend_sleep = pend_sleep

        def track(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request, len(self.requests))

        async def fake_sleep(seconds: float) -> None:
            self.slept.append(seconds)
            if self.pend_sleep:
                await asyncio.Event().wait()  # pends until the task is cancelled

        transport = httpx.MockTransport(track)
        self.http = httpx.AsyncClient(transport=transport, trust_env=False)
        self.client = ThsClient(
            self.http,
            credential_source=lambda: (api_key, enabled),
            sleep=sleep if sleep is not None else fake_sleep,
            max_retries=max_retries,
        )

    async def aclose(self) -> None:
        await self.http.aclose()


def ok_data(data: dict) -> httpx.Response:
    return httpx.Response(200, json=envelope(0, data))


async def test_sends_auth_header_and_returns_payload_without_logging_key():
    seen: dict = {}

    def handler(request: httpx.Request, _: int) -> httpx.Response:
        seen["auth"] = request.headers.get("X-api-key")
        return ok_data({"timestamp": 1, "item": [{"thscode": "600519.SH"}]})

    harness = Harness(handler)
    data = await harness.client.price_snapshot(["600519.SH"])
    await harness.aclose()
    assert seen["auth"] == API_KEY
    assert data["item"][0]["thscode"] == "600519.SH"
    assert API_KEY not in json.dumps(data)


async def test_missing_key_raises_without_request():
    def handler(request: httpx.Request, _: int) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not request without a key")

    harness = Harness(handler, api_key=None)
    with pytest.raises(SourceNotConfigured):
        await harness.client.search("茅台")
    await harness.aclose()
    assert harness.requests == []


async def test_disabled_source_stops_before_request():
    def handler(request: httpx.Request, _: int) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not request when disabled")

    harness = Harness(handler, enabled=False)
    with pytest.raises(SourceDisabled):
        await harness.client.search("茅台")
    await harness.aclose()
    assert harness.requests == []


async def test_http_200_business_error_is_not_success_and_not_retried():
    def handler(request: httpx.Request, count: int) -> httpx.Response:
        return httpx.Response(200, json=envelope(1001, None, "Missing required parameter: q"))

    harness = Harness(handler)
    with pytest.raises(UpstreamRejected) as caught:
        await harness.client.search("茅台")
    await harness.aclose()
    assert caught.value.code == 1001
    assert caught.value.gap_reason == "invalid_response"
    assert len(harness.requests) == 1
    assert harness.slept == []


async def test_non_json_body_raises_invalid_response():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>", headers={"content-type": "text/html"})

    harness = Harness(handler)
    with pytest.raises(InvalidUpstreamResponse):
        await harness.client.search("茅台")
    await harness.aclose()
    assert len(harness.requests) == 1


async def test_http_401_permission_denied_without_retry():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(401, json=envelope(2001, None, "unauthenticated"))

    harness = Harness(handler)
    with pytest.raises(PermissionDenied):
        await harness.client.search("茅台")
    await harness.aclose()
    assert len(harness.requests) == 1


async def test_business_2003_maps_to_permission_denied():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(200, json=envelope(2003, None, "capability not allowed"))

    harness = Harness(handler)
    with pytest.raises(PermissionDenied):
        await harness.client.price_snapshot(["600519.SH"])
    await harness.aclose()
    assert len(harness.requests) == 1


async def test_http_429_retries_with_retry_after_then_succeeds():
    def handler(request: httpx.Request, count: int) -> httpx.Response:
        if count == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return ok_data({"timestamp": 1, "item": []})

    harness = Harness(handler)
    data = await harness.client.search("茅台")
    await harness.aclose()
    assert data["item"] == []
    assert len(harness.requests) == 2
    assert harness.slept == [2.0]


async def test_http_429_exhausts_retries_and_raises_rate_limited():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.5"})

    harness = Harness(handler)
    with pytest.raises(RateLimited):
        await harness.client.search("茅台")
    await harness.aclose()
    assert len(harness.requests) == 3
    assert all(delay <= 10.0 for delay in harness.slept)


async def test_business_4001_retries_then_raises_rate_limited():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(200, json=envelope(4001, None, "qps exceeded"))

    harness = Harness(handler)
    with pytest.raises(RateLimited):
        await harness.client.search("茅台")
    await harness.aclose()
    assert len(harness.requests) == 3


async def test_transient_5xx_retries_then_succeeds():
    def handler(request: httpx.Request, count: int) -> httpx.Response:
        if count == 1:
            return httpx.Response(503, text="unavailable")
        return ok_data({"timestamp": 1, "item": []})

    harness = Harness(handler)
    data = await harness.client.search("茅台")
    await harness.aclose()
    assert len(harness.requests) == 2
    assert data["item"] == []


async def test_timeouts_retry_then_raise_timeout():
    attempts = {"n": 0}

    def handler(request: httpx.Request, count: int) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectTimeout("timed out")

    harness = Harness(handler)
    with pytest.raises(UpstreamTimeout):
        await harness.client.search("茅台")
    await harness.aclose()
    assert attempts["n"] == 3


async def test_transport_errors_exhaust_retries_then_connection_failed():
    def handler(request: httpx.Request, count: int) -> httpx.Response:
        raise httpx.ConnectError("refused")

    harness = Harness(handler)
    with pytest.raises(ConnectionFailed):
        await harness.client.search("茅台")
    await harness.aclose()
    assert len(harness.requests) == 3


async def test_cancellation_propagates_and_stops_follow_up_requests():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    harness = Harness(handler, pend_sleep=True)
    task = asyncio.ensure_future(harness.client.search("茅台"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await harness.aclose()
    assert len(harness.requests) == 1


async def test_statement_modes_are_mutually_exclusive_before_request():
    def handler(request: httpx.Request, _: int) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not request on invalid params")

    harness = Harness(handler)
    with pytest.raises(AppError) as conflict:
        await harness.client.financial_statement(
            "income", "600519.SH", period="annual", limit=2,
            start_ms=HISTORY_WINDOW_OK["start_ms"], end_ms=HISTORY_WINDOW_OK["end_ms"])
    with pytest.raises(AppError) as half_open:
        await harness.client.financial_statement(
            "income", "600519.SH", period="annual", start_ms=HISTORY_WINDOW_OK["start_ms"])
    await harness.aclose()
    assert conflict.value.status_code == 422
    assert half_open.value.status_code == 422
    assert harness.requests == []


async def test_history_window_ceiling_enforced_client_side():
    def handler(request: httpx.Request, _: int) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not request an over-sized window")

    harness = Harness(handler)
    with pytest.raises(AppError) as caught:
        await harness.client.price_history(
            "600519.SH", start_ms=0, end_ms=11 * 365 * 24 * 60 * 60 * 1000)
    await harness.aclose()
    assert caught.value.status_code == 422
    assert harness.requests == []


async def test_search_and_thscode_validation_before_request():
    def handler(request: httpx.Request, _: int) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not request on invalid params")

    harness = Harness(handler)
    with pytest.raises(AppError):
        await harness.client.search("   ")
    with pytest.raises(AppError):
        await harness.client.price_snapshot(["600519"])
    with pytest.raises(AppError):
        await harness.client.financial_indicators("600519.SH", "2024-9")
    await harness.aclose()
    assert harness.requests == []


async def test_data_not_ready_maps_to_not_published():
    def handler(request: httpx.Request, _: int) -> httpx.Response:
        return httpx.Response(200, json=envelope(3002, None, "not ready"))

    harness = Harness(handler)
    with pytest.raises(UpstreamRejected) as caught:
        await harness.client.price_snapshot(["600519.SH"])
    await harness.aclose()
    assert caught.value.gap_reason == "not_published"
