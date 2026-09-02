"""Evidence assembly: sanitize, hash, persist normalized records only.

Default storage policy is reference_only: the raw vendor payload is hashed
(not stored) so re-verification is possible without permanently keeping the
full text under an unclear license. Credential-shaped strings are scrubbed
from anything derived from a raw response.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid

from ..market_data.normalization import NormalizedRecord
from ..research.evidence_models import DataGap, EvidenceMeta, StoragePolicy
from ..research.repository import ResearchRepository

SCHEMA_VERSION = "finsight.evidence.v1"
LICENSE_NOTE_DEFAULT = "同花顺 Financial-API 返回数据；授权未约定保留全文，仅保留哈希与出处。"

_SENSITIVE_KEY = re.compile(r"api[_-]?key|token|secret|authorization|x-api-key", re.IGNORECASE)
_TOKENISH = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def sanitize_payload(payload: object) -> object:
    """Deep-copy with credential-shaped content removed."""
    if isinstance(payload, dict):
        clean: dict = {}
        for key, value in payload.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                clean[key] = "[removed]"
            else:
                clean[key] = sanitize_payload(value)
        return clean
    if isinstance(payload, list):
        return [sanitize_payload(item) for item in payload]
    if isinstance(payload, str):
        return _TOKENISH.sub("[removed]", payload)
    return payload


def canonical_json(payload: object) -> str:
    return json.dumps(sanitize_payload(payload), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def content_hash(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class EvidenceStore:
    def __init__(self, repository: ResearchRepository) -> None:
        self._repository = repository

    def save_fetch(self, *, provider: str, profile_id: str | None, capability: str,
                   endpoint_path: str, params: dict, basis: str, source_time: str | None,
                   raw_payload: object, records: list[NormalizedRecord], gaps: list[DataGap],
                   duplicate_versions: int = 0, security_id: str | None = None,
                   thscode: str | None = None, storage_policy: StoragePolicy = StoragePolicy.REFERENCE_ONLY,
                   license_note: str = LICENSE_NOTE_DEFAULT) -> EvidenceMeta:
        meta = EvidenceMeta(
            id=uuid.uuid4().hex, provider=provider, profile_id=profile_id, capability=capability,
            endpoint_path=endpoint_path, params=sanitize_payload(params), security_id=security_id,
            thscode=thscode, basis=basis, source_time=source_time,
            content_hash=content_hash(raw_payload), record_count=len(records),
            schema_version=SCHEMA_VERSION, storage_policy=storage_policy,
            license_note=license_note)
        self._repository.insert_evidence(meta, records, gaps, duplicate_versions,
                                         raw_payload=sanitize_payload(raw_payload))
        return meta

    def get(self, evidence_id: str):
        return self._repository.get_evidence(evidence_id)

    def list(self, **filters):
        return self._repository.list_evidence(**filters)
