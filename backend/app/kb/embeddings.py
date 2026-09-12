"""V2-03 知识库嵌入：可插拔向量提供方与 Qwen（DashScope 兼容模式）实现。

不配置凭据时 resolve 返回 None，检索退回 FTS5 关键词路径——文档内容零外发。
启用后仅在用户主动摄取/检索时，把分块文本发往嵌入服务用于向量化。
"""
from __future__ import annotations

import math
from typing import Callable

import httpx

from ..errors import AppError

DEFAULT_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_EMBEDDING_DIMENSION = 1024
MAX_BATCH_TEXTS = 10
MAX_TEXT_CHARS = 6000
DIMENSION_RANGE = (64, 4096)


def embedding_config_from_options(options: dict) -> dict:
    """api_profiles options_json -> 客户端配置；未知键忽略，非法值报中文校验错。"""
    options = options or {}
    base_url = str(options.get("base_url") or DEFAULT_EMBEDDING_BASE_URL).strip()
    if not base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise AppError("validation", "嵌入服务地址必须是 HTTPS 官方地址。", 422)
    model = str(options.get("model") or DEFAULT_EMBEDDING_MODEL).strip()
    dimension_raw = options.get("dimension", DEFAULT_EMBEDDING_DIMENSION)
    try:
        dimension = int(dimension_raw)
    except (TypeError, ValueError):
        raise AppError("validation", "向量维度必须是整数。", 422) from None
    if not DIMENSION_RANGE[0] <= dimension <= DIMENSION_RANGE[1]:
        raise AppError("validation", f"向量维度需在 {DIMENSION_RANGE[0]}–{DIMENSION_RANGE[1]} 之间。", 422)
    return {"base_url": base_url.rstrip("/"), "model": model, "dimension": dimension}


class QwenEmbeddingClient:
    """OpenAI 兼容 /embeddings 协议；批量上限内逐批请求，校验维度一致。"""

    def __init__(self, http: httpx.AsyncClient, *, api_key: str, base_url: str,
                 model: str, dimension: int, timeout: float = 30.0,
                 sleep: Callable[[float], None] | None = None) -> None:
        self._http = http
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_TEXTS):
            batch = [self._sanitize(text) for text in texts[start:start + MAX_BATCH_TEXTS]]
            vectors.extend(await self._request(batch))
        return vectors

    @staticmethod
    def _sanitize(text: str) -> str:
        # 换行会破坏兼容模式的输入约定；压缩为空格并裁到单条字符上限。
        cleaned = " ".join(str(text).split())
        return cleaned[:MAX_TEXT_CHARS]

    async def _request(self, batch: list[str]) -> list[list[float]]:
        try:
            response = await self._http.post(
                f"{self._base_url}/embeddings",
                json={"model": self._model, "input": batch, "dimensions": self._dimension,
                      "encoding_format": "float"},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout)
        except httpx.TimeoutException:
            raise AppError("upstream_timeout", "嵌入服务响应超时，请稍后重试。", 504) from None
        except httpx.HTTPError:
            raise AppError("network", "无法连接嵌入服务，请检查网络后重试。", 502) from None
        if response.status_code in (401, 403):
            raise AppError("auth_failed", "嵌入服务拒绝了凭据，请检查 API Key。", 401)
        if response.status_code == 429:
            raise AppError("rate_limited", "嵌入服务限流，请稍后重试或降低批量。", 429)
        if response.status_code >= 400:
            raise AppError("upstream", f"嵌入服务返回错误（状态 {response.status_code}）。", 502)
        try:
            payload = response.json()
            rows = list(payload["data"])
        except Exception:
            raise AppError("schema_mismatch", "嵌入服务返回了无法解析的结构。", 502) from None
        rows.sort(key=lambda item: item.get("index", 0))
        if len(rows) != len(batch):
            raise AppError("schema_mismatch", "嵌入服务返回的向量数量与输入不一致。", 502)
        vectors: list[list[float]] = []
        for row in rows:
            vector = row.get("embedding")
            if not isinstance(vector, list) or len(vector) != self._dimension:
                raise AppError("dimension_mismatch",
                               f"嵌入服务返回的向量维度与配置不一致（期望 {self._dimension}）。", 502)
            vectors.append([float(value) for value in vector])
        return vectors


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for left, right in zip(a, b):
        dot += left * right
        norm_a += left * left
        norm_b += right * right
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)
