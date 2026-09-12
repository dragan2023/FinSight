"""Safe dump downloads: no forwarded API key, redirects or persistent links."""
from __future__ import annotations
import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse
import httpx
from collections.abc import Callable
from ..errors import AppError

MAX_DUMP_BYTES = 2 * 1024 * 1024 * 1024


async def download_parquet(http: httpx.AsyncClient, url: str, destination: Path,
                           *, max_bytes: int = MAX_DUMP_BYTES, cancelled: Callable[[], bool] | None = None) -> tuple[Path, str]:
    def _validated_https_url(candidate: str) -> str:
        parsed = urlparse(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            raise AppError("validation", "下载链接及其重定向目标必须是 HTTPS 地址。", 422)
        return candidate

    current_url = _validated_https_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
    digest = hashlib.sha256(); total = 0
    try:
        for _ in range(4):
            async with http.stream("GET", current_url, headers={"Accept": "application/octet-stream"}, follow_redirects=False) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise AppError("invalid_response", "下载服务返回了缺少目标地址的重定向。", 502)
                    current_url = _validated_https_url(urljoin(current_url, location))
                    continue
                response.raise_for_status()
                with temp.open("xb") as output:
                    async for block in response.aiter_bytes():
                        if cancelled and cancelled():
                            raise asyncio.CancelledError()
                        total += len(block)
                        if total > max_bytes:
                            raise AppError("validation", "下载文件超过本机安全大小限制。", 422)
                        digest.update(block); output.write(block)
                os.replace(temp, destination)
                return destination, digest.hexdigest()
        raise AppError("invalid_response", "下载链接的重定向次数超过安全上限。", 502)
    except asyncio.CancelledError:
        raise
    except httpx.HTTPError as error:
        raise AppError("invalid_response", "全市场下载未成功完成。", 502) from error
    finally:
        temp.unlink(missing_ok=True)
