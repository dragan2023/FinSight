"""SQLite-backed local report discovery and immutable-version reads."""
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class LibraryItem(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id:str; task_id:str; version:int; parent_report_id:str|None; question:str; coverage:str; output_integrity:str; created_at:datetime; thscode:str|None=None; labels:list[str]=Field(default_factory=list)
class ReportLibrary:
    def __init__(self,repository): self._repository=repository
    def search(self,query:str|None=None,limit:int=100)->list[LibraryItem]: return self._repository.search_reports(query,limit)
    def versions(self,report_id:str)->list[LibraryItem]: return self._repository.report_versions(report_id)
    def diff(self, older_id: str, newer_id: str) -> dict:
        older = self._repository.get_report(older_id)
        newer = self._repository.get_report(newer_id)
        if older is None or newer is None:
            raise KeyError("report")
        before = older.report.model_dump(mode="json")
        after = newer.report.model_dump(mode="json")
        def changes(left: set[str], right: set[str]) -> dict:
            return {"added": sorted(right - left), "removed": sorted(left - right)}
        old_metrics = {str(item.get("metric")) for item in before.get("metrics", [])}
        new_metrics = {str(item.get("metric")) for item in after.get("metrics", [])}
        old_gaps = {str(item.get("id") or item.get("reason")) for item in before.get("data_gaps", [])}
        new_gaps = {str(item.get("id") or item.get("reason")) for item in after.get("data_gaps", [])}
        return {"older_id": older_id, "newer_id": newer_id,
                "evidence": changes(set(before.get("evidence_ids", [])), set(after.get("evidence_ids", [])),),
                "metrics": changes(old_metrics, new_metrics), "gaps": changes(old_gaps, new_gaps),
                "coverage_changed": before.get("coverage") != after.get("coverage"),
                "integrity_changed": before.get("output_integrity") != after.get("output_integrity"),
                "notice": "仅比较结构化证据、指标与缺口；章节文字措辞变化不作为实质研究变化。"}
