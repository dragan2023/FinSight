"""Explicit, local-only report deletion with a non-mutating impact preview."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError


class DeletionImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    exists: bool
    attachments: int = 0
    excerpts: int = 0
    notes: int = 0
    exports: int = 0
    blocked_by: list[str] = Field(default_factory=list)


class RetentionService:
    def __init__(self, repository, project_root: Path) -> None:
        self._repository = repository
        self._root = project_root.resolve()

    def preview(self, report_ids: list[str]) -> list[DeletionImpact]:
        return [self._impact(report_id) for report_id in dict.fromkeys(report_ids)]

    def delete(self, report_ids: list[str]) -> list[DeletionImpact]:
        impacts = self.preview(report_ids)
        missing = [item.report_id for item in impacts if not item.exists]
        blocked = [item.report_id for item in impacts if item.blocked_by]
        if missing:
            raise AppError("not_found", f"报告不存在：{', '.join(missing)}", 404)
        if blocked:
            raise AppError("request_conflict", "报告仍被引用，不能删除：" + ", ".join(blocked), 409)
        for impact in impacts:
            for relative in self._repository.report_excerpt_paths(impact.report_id):
                self._remove_excerpt(impact.report_id, relative)
            for filename in self._repository.report_export_paths(impact.report_id):
                self._remove_export(impact.report_id, filename)
        self._repository.delete_reports([item.report_id for item in impacts])
        return impacts

    def retry_failures(self, report_id: str) -> int:
        repaired = 0
        for relative in self._repository.cleanup_failures(report_id):
            self._remove_excerpt(report_id, relative, record_failure=False)
            self._repository.clear_cleanup_failure(report_id, relative)
            repaired += 1
        return repaired

    def _impact(self, report_id: str) -> DeletionImpact:
        counts = self._repository.report_deletion_counts(report_id)
        if counts is None:
            return DeletionImpact(report_id=report_id, exists=False)
        blocked: list[str] = []
        if counts["thesis_observations"]:
            blocked.append(f"{counts['thesis_observations']} 条理由观察仍引用此报告")
        if counts["supplement_references"]:
            blocked.append(f"{counts['supplement_references']} 条补充资料由此版本引用")
        return DeletionImpact(report_id=report_id, exists=True, attachments=counts["attachments"],
                              excerpts=counts["excerpts"], notes=counts["notes"],
                              exports=counts["exports"], blocked_by=blocked)

    def _remove_excerpt(self, report_id: str, relative: str, *, record_failure: bool = True) -> None:
        path = (self._root / relative).resolve()
        allowed = (self._root / "data" / "attachments").resolve()
        if allowed not in path.parents or path.suffix != ".txt":
            raise AppError("validation", "附件摘录路径无效，已拒绝清理。", 422)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            if record_failure:
                self._repository.record_cleanup_failure(
                    report_id, relative, "摘录文件暂时无法删除，请稍后重试。")
            raise AppError("request_conflict", "本地摘录文件暂时无法删除；数据库未作删除。", 409) from None

    def _remove_export(self, report_id: str, filename: str) -> None:
        path = (self._root / "data" / "exports" / filename).resolve()
        allowed = (self._root / "data" / "exports").resolve()
        if allowed not in path.parents or path.name != filename:
            raise AppError("validation", "导出文件路径无效，已拒绝清理。", 422)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            self._repository.record_cleanup_failure(
                report_id, f"data/exports/{filename}", "导出文件暂时无法删除，请稍后重试。")
            raise AppError("request_conflict", "本地导出文件暂时无法删除；数据库未作删除。", 409) from None
