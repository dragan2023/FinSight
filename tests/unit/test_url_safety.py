"""Offline safety tests for the public-reader URL policy (plan 04 §2.3 / §4)."""

from __future__ import annotations

import asyncio

import httpcore
import pytest

from backend.app.errors import AppError
from backend.app.web_research.url_safety import (
    DEFAULT_OFFICIAL_DOMAINS,
    PinnedNetworkBackend,
    build_public_reader_client,
    normalize_allowlist,
    resolve_and_pin,
    validate_url,
)


def run(coro):
    return asyncio.run(coro)


def v4(ip: str) -> list:
    return [(2, 1, 6, "", (ip, 443))]


def v6(ip: str) -> list:
    return [(23, 1, 6, "", (ip, 443, 0, 0))]


class TestValidateUrl:
    @pytest.mark.parametrize("url", [
        "http://www.cninfo.com.cn/a",
        "https://www.cninfo.com.cn:8443/a",
        "https://user:pass@www.cninfo.com.cn/",
        "https://93.184.216.34/a",
        "https://www.cninfo.com%2Ecn/a",
        "https://www.cninfo.com.cn\evil",
        "https://www.exämple.com/a",
        "https://evil.com/a",
        "https://www.cninfo.com.cn.evil.com/a",
        "https://evil-www.cninfo.com.cn/a",
    ])
    def test_rejects(self, url):
        with pytest.raises(AppError) as excinfo:
            validate_url(url, allowed_domains=DEFAULT_OFFICIAL_DOMAINS)
        assert excinfo.value.category == "unsafe_url"

    @pytest.mark.parametrize("url,host", [
        ("https://www.cninfo.com.cn/a", "www.cninfo.com.cn"),
        ("https://static.cninfo.com.cn/x", "static.cninfo.com.cn"),
        ("https://sub.www.cninfo.com.cn/deep", "sub.www.cninfo.com.cn"),
        ("https://WWW.SSE.COM.CN/", "www.sse.com.cn"),
    ])
    def test_accepts_allowlisted_hosts(self, url, host):
        target = validate_url(url, allowed_domains=DEFAULT_OFFICIAL_DOMAINS)
        assert target.host == host


class TestAllowlistEntries:
    @pytest.mark.parametrize("entry", ["*.cninfo.com.cn", "com", "中文.com", "www.cninfo.com.cn/path"])
    def test_rejects_wildcards_and_tlds(self, entry):
        with pytest.raises(AppError):
            normalize_allowlist([entry])

    def test_keeps_clean_entries(self):
        assert normalize_allowlist(["WWW.Cninfo.com.cn ", "static.cninfo.com.cn"]) == (
            "www.cninfo.com.cn", "static.cninfo.com.cn")


class TestResolveAndPin:
    def test_all_public_records_pinned(self):
        resolver = lambda host, port: v4("93.184.216.34") + v6("2606:2800:220:1:248:1893:25c8:1946")
        pinned = resolve_and_pin("www.cninfo.com.cn", resolver=resolver)
        assert pinned == ("2606:2800:220:1:248:1893:25c8:1946", "93.184.216.34")

    @pytest.mark.parametrize("ip", [
        "10.0.0.1", "172.16.0.5", "192.168.1.1", "127.0.0.1", "169.254.169.254",
        "0.0.0.0", "100.64.1.1", "198.18.0.1", "224.0.0.1",
        "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1",
    ])
    def test_private_answers_fail_closed(self, ip):
        answers = v4(ip) if ":" not in ip else v6(ip)
        resolver = lambda host, port: answers + v4("93.184.216.34")
        with pytest.raises(AppError):
            run(resolve_and_pin("www.cninfo.com.cn", resolver=resolver))

    def test_empty_and_broken_dns_rejected(self):
        with pytest.raises(AppError):
            run(resolve_and_pin("www.cninfo.com.cn", resolver=lambda host, port: []))

        def broken(host, port):
            raise OSError("dns down")

        with pytest.raises(AppError):
            run(resolve_and_pin("www.cninfo.com.cn", resolver=broken))

    def test_scope_id_rejected(self):
        answers = [(23, 1, 6, "", ("fe80::1%eth0", 443, 0, 0))]
        with pytest.raises(AppError):
            run(resolve_and_pin("www.cninfo.com.cn", resolver=lambda host, port: answers))


class SpyBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append((host, port))
        return object()


class TestPinnedNetworkBackend:
    def test_unpinned_host_never_connects(self):
        spy = SpyBackend()
        backend = PinnedNetworkBackend({"www.cninfo.com.cn": ("93.184.216.34",)}, inner=spy)
        with pytest.raises(AppError):
            run(backend.connect_tcp("evil.com", 443))
        assert spy.calls == []

    def test_connect_uses_pinned_ip_not_hostname(self):
        spy = SpyBackend()
        backend = PinnedNetworkBackend({"www.cninfo.com.cn": ("93.184.216.34",)}, inner=spy)
        run(backend.connect_tcp("www.cninfo.com.cn", 443, timeout=5.0))
        assert spy.calls == [("93.184.216.34", 443)]

    def test_host_lookup_case_insensitive(self):
        spy = SpyBackend()
        backend = PinnedNetworkBackend({"WWW.Cninfo.com.cn": ("93.184.216.34",)}, inner=spy)
        run(backend.connect_tcp("www.cninfo.com.cn", 443))
        assert spy.calls == [("93.184.216.34", 443)]


def test_public_reader_client_defaults():
    client = build_public_reader_client({"www.cninfo.com.cn": ("93.184.216.34",)},
                                        resolver=lambda host, port: v4("93.184.216.34"))
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
    finally:
        run(client.aclose())
