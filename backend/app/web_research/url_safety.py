"""SSRF-safe URL policy and pinned-DNS transport for public page reads.

Plan 04 §2.3: reads are restricted to HTTPS:443 on an explicit allowlist of
official domains (user-added issuer domains pass the same rules; no TLD
wildcards). Validation rejects userinfo, non-standard ports, IP-literal
hosts, localhost/private/link-local/reserved/cloud-metadata addresses and
obfuscated hostnames, and requires every resolved A/AAAA record to be publicly
routable (fail closed on mixed answers).

Connections bind to the validated resolution: the network backend never
re-resolves DNS, it connects to the pinned IP while httpcore performs TLS with
the original hostname (SNI and certificate verification preserved). Redirects
are never followed automatically; a new target must pass the full rules again
and never inherits credentials or cookies (none are ever attached).
Reference: OWASP SSRF Prevention Cheat Sheet.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpcore
import httpx

from .models import UrlRejected

# Verified starting directory; new announcement domains are added explicitly.
DEFAULT_OFFICIAL_DOMAINS = (
    "www.cninfo.com.cn",
    "static.cninfo.com.cn",
    "www.sse.com.cn",
    "www.szse.cn",
)
PUBLIC_READER_USER_AGENT = "FinSight-PublicReader/0.1 (local research; contact: local user)"
# Address ranges the stdlib may treat as global on some versions; always deny.
_EXTRA_NON_GLOBAL_V4 = (
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT / shared
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("192.0.0.0/24"),   # device provisioning
)
_REJECTED_V6_PREFIXES = ("2002::",)  # 6to4 embeds a v4 address; deprecated tunneling


@dataclass(frozen=True)
class ParsedTarget:
    url: str
    host: str


def normalize_allowlist(entries) -> tuple[str, ...]:
    """Validate user-configured allowlist entries; no wildcards, no bare TLDs."""
    cleaned: list[str] = []
    for entry in entries:
        domain = str(entry).strip().lower().rstrip(".")
        if not domain or "*" in domain or "/" in domain or "@" in domain or ":" in domain:
            raise UrlRejected("域名白名单仅支持主机名，不允许通配。")
        if any(ord(c) > 126 or ord(c) < 32 for c in domain):
            raise UrlRejected("域名白名单仅支持 ASCII 主机名。")
        if "." not in domain:
            raise UrlRejected("不允许把整个顶级域名加入白名单。")
        if domain not in cleaned:
            cleaned.append(domain)
    return tuple(cleaned)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_publicly_routable(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast             or ip.is_reserved or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        if any(ip in network for network in _EXTRA_NON_GLOBAL_V4):
            return False
        return True
    text = str(ip)
    return not any(text.startswith(prefix) for prefix in _REJECTED_V6_PREFIXES)


def validate_url(url: str, *, allowed_domains) -> ParsedTarget:
    if not isinstance(url, str) or not url.strip():
        raise UrlRejected("请提供要读取的 URL。")
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise UrlRejected("URL 无法解析。") from None
    if parts.scheme != "https":
        raise UrlRejected("公开原文读取仅支持 HTTPS。")
    if parts.port not in (None, 443):
        raise UrlRejected("公开原文读取仅允许 443 端口。")
    if parts.username or parts.password:
        raise UrlRejected("URL 不允许携带用户信息。")
    host = parts.hostname or ""
    if not host:
        raise UrlRejected("URL 缺少主机名。")
    if any(c in host for c in ("%", " ", chr(9), chr(10), chr(92), "@")):
        raise UrlRejected("主机名包含可疑字符。")
    if any(ord(c) > 126 for c in host):
        raise UrlRejected("主机名仅支持 ASCII；请使用已核对的确切域名。")
    if unquote(host) != host:
        raise UrlRejected("主机名不允许百分号编码混淆。")
    if _is_ip_literal(host):
        raise UrlRejected("不允许按 IP 字面量读取，请使用已核对的域名。")
    host = host.lower()
    normalized = normalize_allowlist(allowed_domains)
    if not any(host == entry or host.endswith("." + entry) for entry in normalized):
        raise UrlRejected("该域名不在公开原文读取允许清单中。")
    return ParsedTarget(url=url.strip(), host=host)


def validate_extract_target(url: str) -> str:
    """Light pre-check before handing a URL to the Tavily extract service.

    Extraction runs on the provider's crawler, not on this machine, so no
    allowlist applies — but private/loopback literals and non-HTTPS are still
    refused so the user's paid call is never spent on meaningless targets.
    """
    if not isinstance(url, str) or not url.strip():
        raise UrlRejected("请提供要提取的 URL。")
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise UrlRejected("URL 无法解析。") from None
    if parts.scheme != "https" or not parts.hostname:
        raise UrlRejected("正文提取仅支持 HTTPS URL。")
    if parts.username or parts.password:
        raise UrlRejected("URL 不允许携带用户信息。")
    host = parts.hostname.lower()
    if _is_ip_literal(host):
        ip = ipaddress.ip_address(host)
        if not _is_publicly_routable(ip):
            raise UrlRejected("不允许提取私有/保留地址。")
    return url.strip()


def resolve_and_pin(host: str, *, resolver=socket.getaddrinfo) -> tuple[str, ...]:
    """Resolve every A/AAAA record and require all of them to be public.

    Any private/reserved answer fails the whole host (fail closed), so a
    mixed answer cannot smuggle a private address through round-robin.
    """
    try:
        answers = resolver(host, 443)
    except (socket.gaierror, OSError):
        raise UrlRejected("DNS 解析失败，拒绝读取。") from None
    ips: set[str] = set()
    for answer in answers:
        sockaddr = answer[4]
        ip_text = sockaddr[0]
        if "%" in ip_text:  # scope id on link-local; not globally routable
            raise UrlRejected("DNS 返回了链路本地地址，拒绝读取。")
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            raise UrlRejected("DNS 返回了无法解释的地址。") from None
        if not _is_publicly_routable(ip):
            raise UrlRejected("DNS 解析结果包含非公网地址，拒绝读取。")
        ips.add(str(ip))
    if not ips:
        raise UrlRejected("DNS 未返回任何地址，拒绝读取。")
    return tuple(sorted(ips))


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Never re-resolves DNS: connects only to pre-validated IPs.

    httpcore wraps the returned stream in TLS using the original hostname as
    server_hostname, so SNI and certificate verification stay bound to the
    name the user saw — connecting to the pinned IP does not weaken TLS.
    """

    def __init__(self, pins: dict[str, tuple[str, ...]], *, resolver=socket.getaddrinfo,
                 inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._pins = {host.lower(): ips for host, ips in pins.items()}
        self._resolver = resolver
        self._inner = inner if inner is not None else _default_async_backend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        pinned = self._pins.get(str(host).lower())
        if not pinned:
            raise UrlRejected("该主机的 DNS 解析未经校验，拒绝连接。")
        return await self._inner.connect_tcp(
            pinned[0], port, timeout=timeout, local_address=local_address,
            socket_options=socket_options)


def _default_async_backend() -> httpcore.AsyncNetworkBackend:
    from httpcore._backends.auto import AutoBackend  # httpcore 1.0.x

    return AutoBackend()


def build_public_reader_client(pins: dict[str, tuple[str, ...]], *, resolver=socket.getaddrinfo,
                               timeout: float = 30.0) -> httpx.AsyncClient:
    """Client for public reads: pinned DNS, TLS on, redirects off, no env trust."""
    transport = PinnedTransport(pins, resolver=resolver)
    return httpx.AsyncClient(
        transport=transport,
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        headers={"User-Agent": PUBLIC_READER_USER_AGENT},
    )


class PinnedTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose pool uses the pinned-DNS network backend.

    The stdlib default SSL context keeps certificate verification and hostname
    checking on; trust_env/proxies are deliberately not honoured here.
    """

    def __init__(self, pins: dict[str, tuple[str, ...]], *, resolver=socket.getaddrinfo) -> None:
        # Deliberately no super().__init__: the pool is built directly with the
        # pinned backend; handle_async_request/aclose only use self._pool.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedNetworkBackend(pins, resolver=resolver),
        )
