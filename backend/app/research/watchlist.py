"""Local-only watchlist; it stores research targets, never positions or alerts."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field

class WatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    security_id: str
    thscode: str
    name: str
    tags: list[str] = Field(default_factory=list, max_length=12)
    created_at: datetime
    updated_at: datetime

class WatchlistStore:
    def __init__(self, repository) -> None: self._repository = repository
    def add(self, *, security_id: str, thscode: str, name: str, tags: list[str] | None = None) -> WatchlistEntry:
        item = WatchlistEntry(id=uuid.uuid4().hex, security_id=security_id, thscode=thscode, name=name, tags=tags or [], created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        return self._repository.upsert_watchlist(item)
    def list(self) -> list[WatchlistEntry]: return self._repository.list_watchlist()
    def update(self, item_id: str, tags: list[str]) -> WatchlistEntry | None: return self._repository.update_watchlist_tags(item_id, tags)
    def remove(self, item_id: str) -> bool: return self._repository.delete_watchlist(item_id)
