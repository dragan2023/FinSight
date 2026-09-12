"""TTL cache over the data_cache table; expiry never touches evidence."""

from __future__ import annotations

import hashlib
import json

from ..research.evidence_store import SCHEMA_VERSION, sanitize_payload
from ..research.repository import ResearchRepository

TTL_SECONDS = {
    "price_snapshot": 60,
    "index_snapshot": 60,
    "fund_market_snapshot": 60,
    "auction": 0,
    "ticker_list": 24 * 3600,
    "ticker_search": 24 * 3600,
    "reference": 24 * 3600,
    "index_catalog": 24 * 3600,
    "index_constituents": 24 * 3600,
    "corporate_actions": 6 * 3600,
    "index_history": 6 * 3600,
    "fund_performance": 6 * 3600,
    "special_data": 6 * 3600,
    "historical": 6 * 3600,
    "financial": 6 * 3600,
    "calendar": 6 * 3600,
    "snapshot": 60,
}


def cache_ttl(capability: str) -> int:
    for prefix, ttl in TTL_SECONDS.items():
        if capability.startswith(prefix):
            return ttl
    return TTL_SECONDS["snapshot"]


def cache_key(*, provider: str, profile_id: str | None, credential_version: int,
              endpoint_path: str, params: dict, schema_version: str = SCHEMA_VERSION) -> str:
    parts = json.dumps({
        "provider": provider,
        "profile_id": profile_id,
        "credential_version": credential_version,
        "endpoint": endpoint_path,
        "params": params,
        "schema_version": schema_version,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


class DataCache:
    def __init__(self, repository: ResearchRepository) -> None:
        self._repository = repository

    def lookup(self, key: str) -> dict | None:
        payload = self._repository.cache_get(key)
        return payload if isinstance(payload, dict) else None

    def store(self, key: str, *, provider: str, profile_id: str | None,
              credential_version: int, endpoint_path: str, payload: object,
              capability: str) -> None:
        self._repository.cache_put(
            key, provider=provider, profile_id=profile_id, credential_version=credential_version,
            endpoint_path=endpoint_path, schema_version=SCHEMA_VERSION,
            payload=sanitize_payload(payload), ttl_seconds=cache_ttl(capability))
